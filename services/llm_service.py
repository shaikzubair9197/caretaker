"""
LLM Service — switchable Ollama / Azure OpenAI backend, full observability.

LLM_PROVIDER selects the backend ("ollama" default, or "azure_openai").
_call_ollama() and _call_azure_openai() share the exact same LLMCallResult
contract and status codes, so callers never need to know which backend
served a given call.

Every failure has a specific status code so no engineer ever sees
the ambiguous "unavailable" message again.

Status codes produced by _call_ollama() / _call_azure_openai():
  SUCCESS                — valid response, schema matched
  CONNECTION_REFUSED     — OS refused TCP connection
  DNS_FAILURE            — hostname not resolvable
  TIMEOUT                — request exceeded the configured timeout
  MODEL_NOT_FOUND        — model/deployment not found (HTTP 404)
  AUTH_ERROR             — Azure OpenAI only: bad/missing API key (HTTP 401/403)
  RATE_LIMITED           — Azure OpenAI only: quota/rate limit exceeded (HTTP 429)
  HTTP_ERROR             — other HTTP error from the backend
  JSON_PARSE_ERROR       — response not valid JSON or missing expected keys
  SCHEMA_VALIDATION_FAILURE — JSON parsed but required output keys missing
  UNKNOWN_ERROR          — unclassified exception

Pre-flight check (GET /api/tags) runs only for the Ollama path, only when the
main call fails with ConnectError, to distinguish CONNECTION_REFUSED from
MODEL_NOT_FOUND. Azure OpenAI has no equivalent local preflight probe (a
remote probe would cost a real authenticated request for no real benefit).
In the success path there is no extra network round-trip.
"""

import json
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from services.llm_guard import sanitize_input, validate_output
from utils.logger import get_logger

logger = get_logger("services.llm")

OLLAMA_HOST    = os.getenv("OLLAMA_HOST",    "http://localhost:11434")
OLLAMA_MODEL   = os.getenv("OLLAMA_MODEL",   "llama3.2")
OLLAMA_TIMEOUT = float(os.getenv("OLLAMA_TIMEOUT", "30"))

# Switchable backend — "ollama" (default, unchanged behavior) or "azure_openai".
LLM_PROVIDER         = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_DEPLOYMENT     = os.getenv("AZURE_DEPLOYMENT", "")      # deployment name = the "model" for Azure's API
OPENAI_API_VERSION   = os.getenv("OPENAI_API_VERSION", "")
OPENAI_ENDPOINT      = os.getenv("OPENAI_ENDPOINT", "")
AZURE_OPENAI_TIMEOUT = float(os.getenv("AZURE_OPENAI_TIMEOUT", "60"))
# Reasoning-capable deployments (confirmed with gpt-5-nano) can burn an entire
# max_completion_tokens budget on hidden reasoning and emit zero visible
# output (finish_reason="length", accepted_prediction_tokens=0) unless this is
# capped. Empirically measured on the real meeting_intelligence_extract
# prompt: default effort -> 8000/8000 reasoning tokens, 0 output; "low" -> 2240
# reasoning tokens, succeeds but slow (~16.5s); "minimal" -> 0 reasoning
# tokens, succeeds fast (~5s) with correct extraction. Defaulting to "minimal".
AZURE_REASONING_EFFORT = os.getenv("AZURE_REASONING_EFFORT", "minimal")

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

# Version tags recorded on LLMCallLog for meeting-intelligence extraction —
# bump these when the prompt or the expected output schema changes, so past
# extractions can be told apart from future re-extractions.
_MEETING_INTELLIGENCE_PROMPT_VERSION    = "meeting_intelligence_extract.v1"
_MEETING_INTELLIGENCE_EXTRACTION_VERSION = "v1"

# Max output tokens per call type (Ollama `options.num_predict`). Measured on
# this CPU-only Ollama instance: ~12.5 tokens/sec generation. Budgets below
# keep each call type's worst case within OLLAMA_TIMEOUT rather than letting
# an open-ended multi-category JSON schema run indefinitely. Re-measure if the
# prompt schema changes or this moves to GPU-backed inference.
_NUM_PREDICT_BUDGET: dict[str, int] = {
    "meeting_intelligence_extract":      800,   # multi-category structured extraction
    "panic_extract":                     600,
    "classify_knowledge_query":          200,   # 5 small fields
    "adjudicate_knowledge_relationship": 150,   # relationship + confidence only
    "intent_reason":                     300,
    "generate_draft":                    800,   # one short message/email + citations
}

# Separate, larger budget for Azure OpenAI's max_completion_tokens. Reasoning-
# capable model families (confirmed here with gpt-5-nano) consume part of this
# budget on hidden internal reasoning tokens BEFORE emitting any visible
# content. Empirically measured against the real meeting_intelligence_extract
# prompt+transcript shape: 200 and 4000 both produced an EMPTY completion
# (JSON parse error — reasoning alone exhausted the budget), 8000 succeeded
# with real visible JSON content. These values are deliberately generous
# multiples of _NUM_PREDICT_BUDGET (which is tuned for Ollama's generation
# *speed*, an unrelated constraint) since reasoning-token consumption is
# unpredictable per request, not simply proportional to visible-output size.
# Re-measure against production-shaped prompts and weigh against per-call
# cost/latency before scaling beyond demo use.
_AZURE_MAX_COMPLETION_TOKENS_BUDGET: dict[str, int] = {
    "meeting_intelligence_extract":      8000,
    "panic_extract":                     6000,
    "classify_knowledge_query":          3000,
    "adjudicate_knowledge_relationship": 3000,
    "intent_reason":                     3000,
    # gpt-5-nano spends part of the budget on hidden reasoning before emitting
    # visible JSON (see note above: 4000 produced empty completions for a
    # comparable prompt, 8000 succeeded). Draft output is short, but reasoning
    # consumption is unpredictable — keep generous headroom. Tunable downward
    # once measured against real draft prompts in the target deployment.
    "generate_draft":                    8000,
}


# ── Startup security guard ──────────────────────────────────────────────────

def _validate_ollama_host(host: str) -> None:
    parsed = urllib.parse.urlparse(host)
    allowed = {"localhost", "127.0.0.1", "::1"}
    if parsed.hostname not in allowed:
        msg = (
            f"SECURITY: OLLAMA_HOST is '{host}' — hostname '{parsed.hostname}' "
            f"is not localhost. Set OLLAMA_HOST=http://127.0.0.1:11434."
        )
        logger.critical(msg)
        raise RuntimeError(msg)


_validate_ollama_host(OLLAMA_HOST)


def _validate_llm_provider_config() -> None:
    """Fail fast on a bad LLM_PROVIDER value or missing Azure config — never
    logs the actual secret/endpoint values, only which names are missing."""
    if LLM_PROVIDER not in ("ollama", "azure_openai"):
        raise RuntimeError(f"LLM_PROVIDER must be 'ollama' or 'azure_openai', got '{LLM_PROVIDER}'")
    if LLM_PROVIDER == "azure_openai":
        missing = [
            name for name, val in [
                ("AZURE_OPENAI_API_KEY", AZURE_OPENAI_API_KEY),
                ("AZURE_DEPLOYMENT", AZURE_DEPLOYMENT),
                ("OPENAI_API_VERSION", OPENAI_API_VERSION),
                ("OPENAI_ENDPOINT", OPENAI_ENDPOINT),
            ] if not val
        ]
        if missing:
            msg = f"LLM_PROVIDER=azure_openai but missing env vars: {missing}"
            logger.critical(msg)
            raise RuntimeError(msg)


_validate_llm_provider_config()

# Log effective configuration once at startup — engineers can grep this to
# confirm which provider/host/model/timeout are actually in use. Never logs
# AZURE_OPENAI_API_KEY or OPENAI_ENDPOINT (treated as sensitive).
logger.info(
    f"LLM service config — PROVIDER={LLM_PROVIDER} "
    f"OLLAMA_HOST={OLLAMA_HOST} MODEL={OLLAMA_MODEL} TIMEOUT={OLLAMA_TIMEOUT}s "
    f"AZURE_DEPLOYMENT={AZURE_DEPLOYMENT if LLM_PROVIDER == 'azure_openai' else '(unused)'} "
    f"AZURE_OPENAI_TIMEOUT={AZURE_OPENAI_TIMEOUT}s"
)


# ── Status codes ────────────────────────────────────────────────────────────

class LLMStatus:
    SUCCESS                    = "SUCCESS"
    CONNECTION_REFUSED         = "CONNECTION_REFUSED"
    DNS_FAILURE                = "DNS_FAILURE"
    TIMEOUT                    = "TIMEOUT"
    MODEL_NOT_FOUND            = "MODEL_NOT_FOUND"
    HTTP_ERROR                 = "HTTP_ERROR"
    JSON_PARSE_ERROR           = "JSON_PARSE_ERROR"
    SCHEMA_VALIDATION_FAILURE  = "SCHEMA_VALIDATION_FAILURE"
    UNKNOWN_ERROR              = "UNKNOWN_ERROR"
    AUTH_ERROR                 = "AUTH_ERROR"     # Azure OpenAI: bad/missing API key (HTTP 401/403)
    RATE_LIMITED               = "RATE_LIMITED"    # Azure OpenAI: quota/rate limit exceeded (HTTP 429)


# Human-readable reason + suggested fix for each status code.
# Used by API responses and the dashboard.
LLM_STATUS_DETAIL: dict[str, dict[str, str]] = {
    LLMStatus.CONNECTION_REFUSED: {
        "reason": f"Could not connect to Ollama at {OLLAMA_HOST}. The service is not running.",
        "fix":    "Run:  ollama serve",
    },
    LLMStatus.DNS_FAILURE: {
        "reason": f"Hostname '{OLLAMA_HOST}' could not be resolved.",
        "fix":    "Check OLLAMA_HOST environment variable (should be http://127.0.0.1:11434).",
    },
    LLMStatus.TIMEOUT: (
        {
            "reason": f"Azure OpenAI did not respond within {AZURE_OPENAI_TIMEOUT}s.",
            "fix":    f"Increase AZURE_OPENAI_TIMEOUT (current: {AZURE_OPENAI_TIMEOUT}s) or check Azure service status.",
        } if LLM_PROVIDER == "azure_openai" else {
            "reason": f"Ollama did not respond within {OLLAMA_TIMEOUT}s. Model may be loading or system is under load.",
            "fix":    f"Increase OLLAMA_TIMEOUT (current: {OLLAMA_TIMEOUT}s) or wait for the model to finish loading.",
        }
    ),
    LLMStatus.MODEL_NOT_FOUND: (
        {
            "reason": f"Deployment '{AZURE_DEPLOYMENT}' not found (HTTP 404).",
            "fix":    "Check AZURE_DEPLOYMENT and OPENAI_ENDPOINT — the deployment name may be wrong or removed.",
        } if LLM_PROVIDER == "azure_openai" else {
            "reason": f"Model '{OLLAMA_MODEL}' is not installed in Ollama (HTTP 404).",
            "fix":    f"Run:  ollama pull {OLLAMA_MODEL}",
        }
    ),
    LLMStatus.HTTP_ERROR: (
        {
            "reason": "Azure OpenAI returned an unexpected HTTP error.",
            "fix":    "Check the Azure OpenAI resource's diagnostics/logs for details.",
        } if LLM_PROVIDER == "azure_openai" else {
            "reason": "Ollama returned an unexpected HTTP error.",
            "fix":    "Check Ollama server logs for details.",
        }
    ),
    LLMStatus.JSON_PARSE_ERROR: (
        {
            "reason": "Azure OpenAI returned a response that could not be parsed as JSON (often an empty completion if reasoning tokens exhausted the budget).",
            "fix":    "Increase _AZURE_MAX_COMPLETION_TOKENS_BUDGET for this call_type, or check the deployment's reasoning-effort settings.",
        } if LLM_PROVIDER == "azure_openai" else {
            "reason": "Ollama returned a response that could not be parsed as JSON.",
            "fix":    f"Ensure {OLLAMA_MODEL} supports structured JSON output. Try:  ollama pull tinyllama:latest",
        }
    ),
    LLMStatus.SCHEMA_VALIDATION_FAILURE: (
        {
            "reason": "Azure OpenAI returned valid JSON but it was missing required fields.",
            "fix":    "The model output did not match the expected schema. Check the system prompt in prompts/.",
        } if LLM_PROVIDER == "azure_openai" else {
            "reason": "Ollama returned valid JSON but it was missing required fields.",
            "fix":    "The model output did not match the expected schema. Check the system prompt in prompts/.",
        }
    ),
    LLMStatus.UNKNOWN_ERROR: {
        "reason": "An unexpected error occurred during the LLM call.",
        "fix":    "Check caretaker server logs for the full traceback.",
    },
    LLMStatus.AUTH_ERROR: {
        "reason": "Azure OpenAI rejected the request — invalid, missing, or unauthorized API key/endpoint.",
        "fix":    "Check AZURE_OPENAI_API_KEY, OPENAI_ENDPOINT, and AZURE_DEPLOYMENT in your environment.",
    },
    LLMStatus.RATE_LIMITED: {
        "reason": "Azure OpenAI rate limit or quota exceeded.",
        "fix":    "Wait and retry, reduce request volume, or check your Azure OpenAI deployment quota.",
    },
}


# ── Result type ─────────────────────────────────────────────────────────────

@dataclass
class LLMCallResult:
    """Returned by every _call_ollama() invocation. Never None — always carries a status."""
    data:              Optional[dict]   # parsed response dict, or None on failure
    status:            str              # one of LLMStatus.*
    fallback_reason:   Optional[str] = None
    exception_type:    Optional[str] = None
    exception_message: Optional[str] = None
    duration_ms:       int             = 0
    preflight:         dict            = field(default_factory=dict)
    call_log_id:       Optional[int]   = None   # LLMCallLog.id for this call, if the audit write succeeded

    @property
    def succeeded(self) -> bool:
        return self.status == LLMStatus.SUCCESS

    @property
    def reason(self) -> str:
        return LLM_STATUS_DETAIL.get(self.status, {}).get("reason", "")

    @property
    def fix(self) -> str:
        return LLM_STATUS_DETAIL.get(self.status, {}).get("fix", "")


# ── Prompt loading ──────────────────────────────────────────────────────────

def _load_prompt(filename: str) -> str:
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


_SECURITY_PREAMBLE: str = _load_prompt("security_preamble.txt")


# ── Pre-flight check ────────────────────────────────────────────────────────

def preflight_check() -> dict:
    """
    GET /api/tags — verify Ollama is reachable and the configured model is installed.

    Called by:
      - GET /health/detailed   (always — to populate the diagnostics panel)
      - _diagnose_connect_error (only when the main call fails with ConnectError,
        to produce MODEL_NOT_FOUND vs CONNECTION_REFUSED)

    Returns a dict safe to store as JSON in the audit log.
    """
    result: dict = {
        "ollama_reachable":  False,
        "model_available":   False,
        "installed_models":  [],
        "configured_model":  OLLAMA_MODEL,
        "error":             None,
        "duration_ms":       0,
    }
    t0 = time.monotonic()
    try:
        r = httpx.get(f"{OLLAMA_HOST}/api/tags", timeout=3.0)
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        if r.status_code == 200:
            result["ollama_reachable"] = True
            models = [m["name"] for m in r.json().get("models", [])]
            result["installed_models"] = models
            # Match exact name or with tag suffix: "llama3.2" matches "llama3.2:latest"
            result["model_available"] = _model_matches_any(OLLAMA_MODEL, models)
        else:
            result["error"] = f"HTTP {r.status_code}"
    except httpx.ConnectError as e:
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["error"] = "CONNECTION_REFUSED"
    except httpx.TimeoutException:
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["error"] = "TIMEOUT"
    except Exception as e:
        result["duration_ms"] = int((time.monotonic() - t0) * 1000)
        result["error"] = str(e)
    return result


def _model_matches_any(requested: str, installed: list[str]) -> bool:
    """Check if `requested` matches any installed model name (with or without tag)."""
    req_base = requested.split(":")[0].lower()
    for m in installed:
        if m.lower() == requested.lower():
            return True
        if m.lower().startswith(req_base + ":"):
            return True
    return False


def _diagnose_connect_error(exc: httpx.ConnectError) -> tuple[str, str]:
    """
    Given a ConnectError, run GET /api/tags to distinguish:
      - Ollama not running           → CONNECTION_REFUSED
      - Ollama running, model absent → MODEL_NOT_FOUND
      - DNS unresolvable             → DNS_FAILURE
    Returns (status_code, preflight_json_safe_str).
    """
    exc_str = str(exc).lower()
    if "name or service not known" in exc_str or "getaddrinfo" in exc_str or "nodename" in exc_str:
        return LLMStatus.DNS_FAILURE, "{}"

    pf = preflight_check()
    if pf["ollama_reachable"] and not pf["model_available"]:
        return LLMStatus.MODEL_NOT_FOUND, json.dumps(pf)
    return LLMStatus.CONNECTION_REFUSED, json.dumps(pf)


def _diagnose_azure_connect_error(exc: httpx.ConnectError) -> str:
    """
    Classify a ConnectError to Azure OpenAI by message text only — unlike
    Ollama's local /api/tags preflight, a remote preflight probe to Azure
    would cost a real authenticated request/quota for no real benefit.
    """
    exc_str = str(exc).lower()
    if "name or service not known" in exc_str or "getaddrinfo" in exc_str or "nodename" in exc_str:
        return LLMStatus.DNS_FAILURE
    return LLMStatus.CONNECTION_REFUSED


# ── Core call ───────────────────────────────────────────────────────────────

def _call_ollama(
    call_type: str,
    system_prompt: str,
    context_text: str,
    required_output_keys: list[str],
    entity_types: Optional[list] = None,
    prompt_version: Optional[str] = None,
    extraction_version: Optional[str] = None,
    source_id: Optional[int] = None,
    model: Optional[str] = None,
) -> LLMCallResult:
    """
    Secure Ollama call pipeline:
      1. Sanitise input (injection filter)
      2. Prepend security preamble
      3. POST to Ollama /api/chat
      4. Classify every exception category with a specific LLMStatus code
      5. Run targeted pre-flight only when ConnectError fires (no extra latency on success path)
      6. Validate output schema
      7. Write full audit log entry (independent DB session)

    `model` overrides the configured OLLAMA_MODEL for this one call — used by
    _call_with_fallback() to try an alternate model without touching the
    global default. Returns LLMCallResult — never None.
    """
    from services.llm_audit_service import LLMAuditService

    effective_model = model or OLLAMA_MODEL

    # ── 1. Input guard ────────────────────────────────────────────────────
    clean_text, injection_warnings = sanitize_input(context_text)

    # ── 2. Build hardened system prompt ──────────────────────────────────
    hardened_system = _SECURITY_PREAMBLE + system_prompt
    full_prompt = hardened_system + "\n\n" + clean_text     # for SHA256 only

    # Prompt preview: first 250 chars of the cleaned user text — no raw PII
    # (clean_text has already been injection-sanitised but is NOT PII-masked;
    #  context_text passed in from LLMService is always preprocessed.masked_text)
    prompt_preview = clean_text[:250]
    prompt_size_chars = len(clean_text)

    payload = {
        "model":   effective_model,
        "messages": [
            {"role": "system", "content": hardened_system},
            {"role": "user",   "content": clean_text},
        ],
        "format": "json",
        "stream": False,
        # Bound generation length — on CPU-only Ollama (no GPU, confirmed via
        # `ollama ps` size_vram=0 in this environment) an uncapped structured
        # extraction can take minutes to finish generating. Measured directly:
        # the real meeting_intelligence_extract prompt does ~26s prompt-eval +
        # ~12.5 tokens/sec generation. Without num_predict the model keeps
        # generating until *it* decides to stop, which on a long multi-category
        # JSON schema can exceed any reasonable OLLAMA_TIMEOUT. Capping forces
        # early, predictable termination (truncated output -> JSON_PARSE_ERROR /
        # SCHEMA_VALIDATION_FAILURE, which the existing fallback chain and
        # never-raise contract already handle) instead of an indefinite hang.
        "options": {"num_predict": _NUM_PREDICT_BUDGET.get(call_type, 512)},
    }

    # ── Layer 4: Request audit log ────────────────────────────────────────
    logger.info(
        f"LLM request — call_type={call_type} model={effective_model} "
        f"prompt_chars={prompt_size_chars} timeout={OLLAMA_TIMEOUT}s"
    )

    # ── 3. Call Ollama ────────────────────────────────────────────────────
    status           = LLMStatus.UNKNOWN_ERROR
    fallback_reason  = None
    exc_type         = None
    exc_msg          = None
    preflight_data   = {}
    result_data      = None
    t0               = time.monotonic()

    try:
        response = httpx.post(
            f"{OLLAMA_HOST}/api/chat",
            json=payload,
            timeout=OLLAMA_TIMEOUT,
        )
        response.raise_for_status()

        raw = json.loads(response.json()["message"]["content"])

        # ── 4. Output schema validation ───────────────────────────────────
        if validate_output(raw, required_output_keys):
            status      = LLMStatus.SUCCESS
            result_data = raw
            logger.info(f"LLM call succeeded — call_type={call_type} status=SUCCESS")
        else:
            status         = LLMStatus.SCHEMA_VALIDATION_FAILURE
            fallback_reason = f"required keys {required_output_keys} absent in response"
            logger.warning(
                f"LLM schema validation failed — call_type={call_type} "
                f"required={required_output_keys} got_keys={list(raw.keys())}"
            )

    except httpx.ConnectError as e:
        # Diagnose: is Ollama down or is the model missing?
        exc_type, _ = type(e).__module__ + "." + type(e).__name__, None
        diag_status, pf_json = _diagnose_connect_error(e)
        try:
            preflight_data = json.loads(pf_json)
        except Exception:
            preflight_data = {}
        status          = diag_status
        exc_type        = "httpx.ConnectError"
        exc_msg         = str(e)[:400]
        fallback_reason = diag_status
        logger.warning(
            f"LLM ConnectError → diagnosed as {diag_status} — "
            f"host={OLLAMA_HOST} model={effective_model}: {exc_msg}"
        )

    except httpx.TimeoutException as e:
        status          = LLMStatus.TIMEOUT
        exc_type        = "httpx.TimeoutException"
        exc_msg         = f"Request timed out after {OLLAMA_TIMEOUT}s"
        fallback_reason = LLMStatus.TIMEOUT
        logger.warning(f"LLM timeout after {OLLAMA_TIMEOUT}s — call_type={call_type}")

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            status          = LLMStatus.MODEL_NOT_FOUND
            exc_msg         = f"Model '{effective_model}' not found in Ollama (HTTP 404)"
            fallback_reason = LLMStatus.MODEL_NOT_FOUND
        else:
            status          = LLMStatus.HTTP_ERROR
            exc_msg         = f"HTTP {e.response.status_code}: {e.response.text[:300]}"
            fallback_reason = LLMStatus.HTTP_ERROR
        exc_type = "httpx.HTTPStatusError"
        logger.error(f"LLM HTTP error — call_type={call_type} status={status}: {exc_msg}")

    except json.JSONDecodeError as e:
        status          = LLMStatus.JSON_PARSE_ERROR
        exc_type        = "json.JSONDecodeError"
        exc_msg         = f"Position {e.pos}: {e.msg}"
        fallback_reason = LLMStatus.JSON_PARSE_ERROR
        logger.error(f"LLM JSON parse error — call_type={call_type}: {exc_msg}")

    except KeyError as e:
        # Ollama responded but the response structure was unexpected
        status          = LLMStatus.JSON_PARSE_ERROR
        exc_type        = "KeyError"
        exc_msg         = f"Missing key in Ollama response structure: {e}"
        fallback_reason = LLMStatus.JSON_PARSE_ERROR
        logger.error(f"LLM response structure error — call_type={call_type}: {exc_msg}")

    except Exception as e:
        status          = LLMStatus.UNKNOWN_ERROR
        exc_type        = type(e).__qualname__
        exc_msg         = str(e)[:400]
        fallback_reason = LLMStatus.UNKNOWN_ERROR
        logger.exception(f"LLM unexpected error — call_type={call_type}: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)

    # ── 5. Audit log ──────────────────────────────────────────────────────
    call_log_id = LLMAuditService.log(
        model              = effective_model,
        call_type          = call_type,
        prompt             = full_prompt,
        entity_types       = entity_types or [],
        injection_warnings = injection_warnings,
        duration_ms        = duration_ms,
        status             = status,
        fallback_reason    = fallback_reason,
        exception_type     = exc_type,
        exception_message  = exc_msg,
        preflight_data     = preflight_data if preflight_data else None,
        prompt_preview     = prompt_preview,
        prompt_size_chars  = prompt_size_chars,
        prompt_version     = prompt_version,
        extraction_version = extraction_version,
        source_id          = source_id,
    )

    return LLMCallResult(
        data              = result_data,
        status            = status,
        fallback_reason   = fallback_reason,
        exception_type    = exc_type,
        exception_message = exc_msg,
        duration_ms       = duration_ms,
        preflight         = preflight_data,
        call_log_id       = call_log_id,
    )


def _call_azure_openai(
    call_type: str,
    system_prompt: str,
    context_text: str,
    required_output_keys: list[str],
    entity_types: Optional[list] = None,
    prompt_version: Optional[str] = None,
    extraction_version: Optional[str] = None,
    source_id: Optional[int] = None,
    model: Optional[str] = None,
) -> LLMCallResult:
    """
    Secure Azure OpenAI call pipeline — same contract and steps as
    _call_ollama (never raises, always returns LLMCallResult), pointed at the
    user's Azure OpenAI deployment instead of local Ollama:
      1. Sanitise input (injection filter)            — same as _call_ollama
      2. Prepend security preamble                    — same as _call_ollama
      3. POST to {OPENAI_ENDPOINT}/openai/deployments/{deployment}/chat/completions
      4. Classify every exception category with a specific LLMStatus code
      5. Validate output schema                        — same as _call_ollama
      6. Write full audit log entry (independent DB session)

    `model` overrides the configured AZURE_DEPLOYMENT for this one call.
    Never reads, logs, or returns AZURE_OPENAI_API_KEY / OPENAI_ENDPOINT values.
    """
    from services.llm_audit_service import LLMAuditService

    effective_model = model or AZURE_DEPLOYMENT

    # ── 1. Input guard ────────────────────────────────────────────────────
    clean_text, injection_warnings = sanitize_input(context_text)

    # ── 2. Build hardened system prompt ──────────────────────────────────
    hardened_system = _SECURITY_PREAMBLE + system_prompt
    full_prompt = hardened_system + "\n\n" + clean_text     # for SHA256 only

    prompt_preview = clean_text[:250]
    prompt_size_chars = len(clean_text)

    url = f"{OPENAI_ENDPOINT.rstrip('/')}/openai/deployments/{effective_model}/chat/completions?api-version={OPENAI_API_VERSION}"
    headers = {"api-key": AZURE_OPENAI_API_KEY, "Content-Type": "application/json"}
    payload = {
        "messages": [
            {"role": "system", "content": hardened_system},
            {"role": "user",   "content": clean_text},
        ],
        "response_format": {"type": "json_object"},
        # Newer model families (e.g. the gpt-5/o-series reasoning models) reject
        # the older "max_tokens" chat-completions field — confirmed via a live
        # 400 from this deployment: "Unsupported parameter: 'max_tokens' ...
        # Use 'max_completion_tokens' instead." Budget is Azure-specific (see
        # _AZURE_MAX_COMPLETION_TOKENS_BUDGET) since reasoning-model token
        # consumption is a different constraint than Ollama's generation speed.
        "max_completion_tokens": _AZURE_MAX_COMPLETION_TOKENS_BUDGET.get(call_type, 2000),
        # See AZURE_REASONING_EFFORT definition — without this, reasoning
        # models can consume the entire token budget on hidden reasoning and
        # emit zero visible output.
        "reasoning_effort": AZURE_REASONING_EFFORT,
    }

    logger.info(
        f"LLM request — call_type={call_type} provider=azure_openai model={effective_model} "
        f"prompt_chars={prompt_size_chars} timeout={AZURE_OPENAI_TIMEOUT}s"
    )

    status           = LLMStatus.UNKNOWN_ERROR
    fallback_reason  = None
    exc_type         = None
    exc_msg          = None
    preflight_data: dict = {}
    result_data      = None
    t0               = time.monotonic()

    try:
        response = httpx.post(url, json=payload, headers=headers, timeout=AZURE_OPENAI_TIMEOUT)
        response.raise_for_status()

        raw = json.loads(response.json()["choices"][0]["message"]["content"])

        if validate_output(raw, required_output_keys):
            status      = LLMStatus.SUCCESS
            result_data = raw
            logger.info(f"LLM call succeeded — call_type={call_type} status=SUCCESS")
        else:
            status         = LLMStatus.SCHEMA_VALIDATION_FAILURE
            fallback_reason = f"required keys {required_output_keys} absent in response"
            logger.warning(
                f"LLM schema validation failed — call_type={call_type} "
                f"required={required_output_keys} got_keys={list(raw.keys())}"
            )

    except httpx.ConnectError as e:
        diag_status     = _diagnose_azure_connect_error(e)
        status          = diag_status
        exc_type        = "httpx.ConnectError"
        exc_msg         = str(e)[:400]
        fallback_reason = diag_status
        logger.warning(
            f"LLM ConnectError → diagnosed as {diag_status} — "
            f"provider=azure_openai model={effective_model}: {exc_msg}"
        )

    except httpx.TimeoutException as e:
        status          = LLMStatus.TIMEOUT
        exc_type        = "httpx.TimeoutException"
        exc_msg         = f"Request timed out after {AZURE_OPENAI_TIMEOUT}s"
        fallback_reason = LLMStatus.TIMEOUT
        logger.warning(f"LLM timeout after {AZURE_OPENAI_TIMEOUT}s — call_type={call_type}")

    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            status          = LLMStatus.AUTH_ERROR
            exc_msg         = f"Azure OpenAI auth error (HTTP {code}): {e.response.text[:300]}"
            fallback_reason = LLMStatus.AUTH_ERROR
        elif code == 429:
            status          = LLMStatus.RATE_LIMITED
            exc_msg         = f"Azure OpenAI rate limited (HTTP 429): {e.response.text[:300]}"
            fallback_reason = LLMStatus.RATE_LIMITED
        elif code == 404:
            status          = LLMStatus.MODEL_NOT_FOUND
            exc_msg         = f"Deployment '{effective_model}' not found (HTTP 404) — check AZURE_DEPLOYMENT/OPENAI_ENDPOINT"
            fallback_reason = LLMStatus.MODEL_NOT_FOUND
        else:
            status          = LLMStatus.HTTP_ERROR
            exc_msg         = f"HTTP {code}: {e.response.text[:300]}"
            fallback_reason = LLMStatus.HTTP_ERROR
        exc_type = "httpx.HTTPStatusError"
        logger.error(f"LLM HTTP error — call_type={call_type} status={status}: {exc_msg}")

    except json.JSONDecodeError as e:
        status          = LLMStatus.JSON_PARSE_ERROR
        exc_type        = "json.JSONDecodeError"
        exc_msg         = f"Position {e.pos}: {e.msg}"
        fallback_reason = LLMStatus.JSON_PARSE_ERROR
        logger.error(f"LLM JSON parse error — call_type={call_type}: {exc_msg}")

    except KeyError as e:
        status          = LLMStatus.JSON_PARSE_ERROR
        exc_type        = "KeyError"
        exc_msg         = f"Missing key in Azure OpenAI response structure: {e}"
        fallback_reason = LLMStatus.JSON_PARSE_ERROR
        logger.error(f"LLM response structure error — call_type={call_type}: {exc_msg}")

    except Exception as e:
        status          = LLMStatus.UNKNOWN_ERROR
        exc_type        = type(e).__qualname__
        exc_msg         = str(e)[:400]
        fallback_reason = LLMStatus.UNKNOWN_ERROR
        logger.exception(f"LLM unexpected error — call_type={call_type}: {e}")

    duration_ms = int((time.monotonic() - t0) * 1000)

    call_log_id = LLMAuditService.log(
        model              = effective_model,
        call_type          = call_type,
        prompt             = full_prompt,
        entity_types       = entity_types or [],
        injection_warnings = injection_warnings,
        duration_ms        = duration_ms,
        status             = status,
        fallback_reason    = fallback_reason,
        exception_type     = exc_type,
        exception_message  = exc_msg,
        preflight_data     = preflight_data if preflight_data else None,
        prompt_preview     = prompt_preview,
        prompt_size_chars  = prompt_size_chars,
        prompt_version     = prompt_version,
        extraction_version = extraction_version,
        source_id          = source_id,
    )

    return LLMCallResult(
        data              = result_data,
        status            = status,
        fallback_reason   = fallback_reason,
        exception_type    = exc_type,
        exception_message = exc_msg,
        duration_ms       = duration_ms,
        preflight         = preflight_data,
        call_log_id       = call_log_id,
    )


# ── Per-call-type model fallback chain ──────────────────────────────────────
#
# Ordering is evidence-based, taken from LLMCallLog history in this dev
# environment (CPU-only Ollama — no GPU, size_vram=0 per `ollama ps`):
#   panic_extract:                llama3.2 584/595 SUCCESS  vs  tinyllama 3/4 SUCCESS
#   meeting_intelligence_extract: tinyllama 2/8 SUCCESS     vs  llama3.2 0/11 SUCCESS
#                                 (llama3.2 is the better model but its 3.2B
#                                  size never finishes this call on this CPU;
#                                  tinyllama's 1B size at least sometimes does)
#   classify_knowledge_query / adjudicate_knowledge_relationship: short output,
#                                 try the faster model first.
# Re-derive this ordering (`SELECT model, call_type, status, count(*) ...`)
# if the call moves to GPU-backed Ollama or a different model is pulled.
_MODEL_FALLBACK_CHAIN: dict[str, list[str]] = {
    "panic_extract":                     ["llama3.2", "tinyllama"],
    "meeting_intelligence_extract":      ["tinyllama", "llama3.2"],
    "classify_knowledge_query":          ["tinyllama", "llama3.2"],
    "adjudicate_knowledge_relationship": ["tinyllama", "llama3.2"],
}

# Failures where a different model could plausibly do better — worth
# retrying down the chain. CONNECTION_REFUSED / DNS_FAILURE / UNKNOWN_ERROR
# mean Ollama itself (or our request) is broken, not the model — retrying
# with a different model on the same broken connection wastes the timeout
# twice for no gain, so those stop the chain immediately.
_FALLBACK_RETRYABLE_STATUSES = {
    LLMStatus.TIMEOUT,
    LLMStatus.MODEL_NOT_FOUND,
    LLMStatus.HTTP_ERROR,
    LLMStatus.JSON_PARSE_ERROR,
    LLMStatus.SCHEMA_VALIDATION_FAILURE,
    LLMStatus.RATE_LIMITED,   # Azure OpenAI transient rate limit — worth retrying
}


def _call_with_fallback(
    call_type: str,
    system_prompt: str,
    context_text: str,
    required_output_keys: list[str],
    **kwargs,
) -> LLMCallResult:
    """
    Same contract as _call_ollama (never raises, always returns LLMCallResult)
    but walks _MODEL_FALLBACK_CHAIN[call_type] in order, stopping at the first
    SUCCESS. Every attempt is independently audited via _call_ollama's own
    LLMAuditService.log() call, so the audit trail shows exactly which models
    were tried and which one (if any) succeeded.

    When LLM_PROVIDER=azure_openai, dispatches to _call_azure_openai instead
    and returns immediately — Azure uses a single configured deployment, not
    a multi-model fallback chain, so none of the Ollama chain logic below runs.
    """
    if LLM_PROVIDER == "azure_openai":
        return _call_azure_openai(
            call_type=call_type,
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=required_output_keys,
            **kwargs,
        )

    chain = _MODEL_FALLBACK_CHAIN.get(call_type) or [OLLAMA_MODEL]
    last_result: Optional[LLMCallResult] = None

    for i, model_name in enumerate(chain):
        result = _call_ollama(
            call_type=call_type,
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=required_output_keys,
            model=model_name,
            **kwargs,
        )
        last_result = result

        if result.succeeded:
            if i > 0:
                logger.info(
                    f"LLM fallback succeeded — call_type={call_type} "
                    f"model={model_name} (attempt {i + 1}/{len(chain)})"
                )
            return result

        if result.status not in _FALLBACK_RETRYABLE_STATUSES:
            return result  # infra-level failure — another model won't help

        if i + 1 < len(chain):
            logger.warning(
                f"LLM attempt failed — call_type={call_type} model={model_name} "
                f"status={result.status} — trying next model in chain"
            )
        else:
            logger.warning(
                f"LLM fallback chain exhausted — call_type={call_type} "
                f"tried={chain} final_status={result.status}"
            )

    return last_result


# ── Public service interface ─────────────────────────────────────────────────

class LLMService:

    @staticmethod
    def extract_panic_items(
        text: str,
        entity_types: Optional[list] = None,
    ) -> LLMCallResult:
        """
        Parse a panic dump with LLM.
        Always returns LLMCallResult — check .succeeded and .data.
        """
        system_prompt = _load_prompt("panic_extract.txt")
        result = _call_with_fallback(
            call_type="panic_extract",
            system_prompt=system_prompt,
            context_text=text,
            required_output_keys=["items"],
            entity_types=entity_types,
        )
        if result.succeeded:
            logger.info(
                f"LLM extracted {len(result.data.get('items', []))} items, "
                f"overload={result.data.get('overload_detected', False)}"
            )
        else:
            logger.warning(
                f"LLM extraction failed — status={result.status} "
                f"reason={result.reason}"
            )
        return result

    @staticmethod
    def reason_intent(context: dict) -> LLMCallResult:
        """
        Ask LLM for the best next action given current brain context.
        Always returns LLMCallResult — check .succeeded and .data.
        """
        system_prompt = _load_prompt("intent_reason.txt")
        context_text  = json.dumps(context, indent=2)
        if LLM_PROVIDER == "azure_openai":
            return _call_azure_openai(
                call_type="intent_reason",
                system_prompt=system_prompt,
                context_text=context_text,
                required_output_keys=["action_type", "message", "urgency"],
            )
        return _call_ollama(
            call_type="intent_reason",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=["action_type", "message", "urgency"],
        )

    @staticmethod
    def extract_meeting_intelligence(
        masked_transcript: str,
        participant_tokens: list,
        source_id: Optional[int] = None,
    ) -> LLMCallResult:
        """
        Extract structured meeting intelligence (action items, commitments,
        deadlines, questions, risks, decisions, mentioned PRs/tickets/links, ...)
        from an already-masked transcript.

        Always returns LLMCallResult — check .succeeded and .data. The raw
        .data is not a database shape; callers (TranscriptIngestionService)
        parse it into the in-memory MeetingIntelligence model before any
        persistence happens.
        """
        system_prompt = _load_prompt("meeting_intelligence_extract.txt")
        context_text = json.dumps(
            {"participant_tokens": participant_tokens, "transcript": masked_transcript},
            indent=2,
        )
        result = _call_with_fallback(
            call_type="meeting_intelligence_extract",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=["items"],
            prompt_version=_MEETING_INTELLIGENCE_PROMPT_VERSION,
            extraction_version=_MEETING_INTELLIGENCE_EXTRACTION_VERSION,
            source_id=source_id,
        )
        if result.succeeded:
            logger.info(f"LLM extracted {len(result.data.get('items', []))} meeting intelligence items")
        else:
            logger.warning(f"Meeting intelligence extraction failed — status={result.status} reason={result.reason}")
        return result

    @staticmethod
    def classify_knowledge_query(
        masked_query: str,
        entity_types: Optional[list] = None,
    ) -> LLMCallResult:
        """
        Classify a knowledge-retrieval query (Plan 2 §1) into how it should be
        looked up — structured vs semantic vs secret, likely knowledge_types,
        search terms, and an optional as-of hint.

        Same never-raise / status-code / masked-input / audited contract as every
        other call here. `masked_query` MUST already be PII-masked by the caller
        (knowledge_intent_service) — no raw PII ever reaches the model.

        Always returns LLMCallResult — check .succeeded and .data.
        """
        system_prompt = _load_prompt("classify_knowledge_query.txt")
        context_text = json.dumps({"query": masked_query})
        result = _call_with_fallback(
            call_type="classify_knowledge_query",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=["query_type", "is_secret_request"],
            entity_types=entity_types,
        )
        if not result.succeeded:
            logger.warning(
                f"Knowledge query classification failed — status={result.status} "
                f"reason={result.reason}"
            )
        return result

    @staticmethod
    def adjudicate_knowledge_relationship(
        knowledge_type: str,
        existing: dict,
        incoming: dict,
        entity_types: Optional[list] = None,
    ) -> LLMCallResult:
        """
        Decide the relationship (SUPERSEDES | DUPLICATE | CONFLICT | UNRELATED)
        between two masked knowledge items whose embedding similarity fell in the
        ambiguous band (Plan 2 §8). Inputs carry only masked title/detail —
        never raw PII, never decrypted secrets.

        Always returns LLMCallResult — check .succeeded and .data.
        """
        system_prompt = _load_prompt("adjudicate_knowledge_relationship.txt")
        context_text = json.dumps(
            {"knowledge_type": knowledge_type, "existing": existing, "incoming": incoming},
            default=str,
        )
        result = _call_with_fallback(
            call_type="adjudicate_knowledge_relationship",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=["relationship"],
            entity_types=entity_types,
        )
        if not result.succeeded:
            logger.warning(
                f"Knowledge relationship adjudication failed — status={result.status} "
                f"reason={result.reason}"
            )
        return result

    @staticmethod
    def generate_draft(
        draft_type: str,
        masked_context: dict,
        retrieval_results: list,
        source_id: Optional[int] = None,
    ) -> LLMCallResult:
        """
        Generate a single follow-up draft (teams_message | email | reminder |
        calendar_reminder | followup_suggestion) from ALREADY-MASKED meeting
        context and Plan 2's confidence/conflict-gated Validated Knowledge
        Results. One method, one prompt file per draft_type — same precedent as
        extract_meeting_intelligence handling many types through one call site.

        The model produces only human-language content + a `citations` array
        echoing which retrieval_results back each claim; the masked routing
        tokens (recipient/person/attendee) and timing are attached
        deterministically by DraftGenerationService from the KnowledgeItem, not
        invented here. Routes through _call_with_fallback, so LLM_PROVIDER
        (ollama | azure_openai, e.g. gpt-5-nano) is honoured transparently.

        Always returns LLMCallResult — check .succeeded and .data. Confidence/
        conflict gating happens in DraftGenerationService BEFORE this is called.
        """
        prompt_files = {
            "teams_message":       "draft_teams_message.txt",
            "email":               "draft_email.txt",
            "reminder":            "draft_reminder.txt",
            "calendar_reminder":   "draft_calendar_reminder.txt",
            "followup_suggestion": "draft_followup_suggestion.txt",
        }
        required_keys = {
            "teams_message":       ["body", "citations"],
            "email":               ["subject", "body", "citations"],
            "reminder":            ["title", "citations"],
            "calendar_reminder":   ["title", "citations"],
            "followup_suggestion": ["suggestion_text", "citations"],
        }
        prompt_file = prompt_files.get(draft_type)
        if prompt_file is None:
            logger.error(f"generate_draft: unknown draft_type={draft_type!r}")
            return LLMCallResult(
                data=None,
                status=LLMStatus.SCHEMA_VALIDATION_FAILURE,
                fallback_reason=f"unknown draft_type {draft_type!r}",
            )

        system_prompt = _load_prompt(prompt_file)
        context_text = json.dumps(
            {
                "draft_type": draft_type,
                "context": masked_context,
                "retrieval_results": retrieval_results,
            },
            default=str,
        )
        result = _call_with_fallback(
            call_type="generate_draft",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=required_keys.get(draft_type, ["citations"]),
            source_id=source_id,
        )
        if result.succeeded:
            logger.info(f"generate_draft succeeded — type={draft_type}")
        else:
            logger.warning(f"generate_draft failed — type={draft_type} status={result.status} reason={result.reason}")
        return result

    @staticmethod
    def reason_brain(payload: dict) -> dict:
        """Deterministic fallback reasoning — used when Ollama is unavailable."""
        tasks   = payload.get("tasks", [])
        insights = payload.get("insights", {})

        recommendations = []
        if insights.get("work_state") == "low_focus":
            recommendations.append("Low focus detected. Start with smallest task to build momentum.")
        if any(t.get("priority") == "high" for t in tasks):
            recommendations.append("High priority tasks pending — focus on them first.")
        if insights.get("distraction_level") == "high":
            recommendations.append("High distraction detected. Reduce application switching.")

        return {
            "summary": {
                "top_task":   tasks[0]["description"] if tasks else None,
                "task_count": len(tasks),
                "focus_state": insights.get("work_state", "unknown"),
            },
            "recommendations": recommendations,
        }
