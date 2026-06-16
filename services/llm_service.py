"""
LLM Service — Ollama integration with full observability.

Every failure has a specific status code so no engineer ever sees
the ambiguous "unavailable" message again.

Status codes produced by _call_ollama():
  SUCCESS                — valid response, schema matched
  CONNECTION_REFUSED     — OS refused TCP connection (Ollama not running)
  DNS_FAILURE            — hostname not resolvable (bad OLLAMA_HOST)
  TIMEOUT                — request exceeded OLLAMA_TIMEOUT seconds
  MODEL_NOT_FOUND        — Ollama running but model not installed (HTTP 404)
  HTTP_ERROR             — non-404 HTTP error from Ollama
  JSON_PARSE_ERROR       — response not valid JSON or missing expected keys
  SCHEMA_VALIDATION_FAILURE — JSON parsed but required output keys missing
  UNKNOWN_ERROR          — unclassified exception

Pre-flight check (GET /api/tags) runs only when the main call fails with
ConnectError, to distinguish CONNECTION_REFUSED from MODEL_NOT_FOUND.
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

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


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

# Log effective configuration once at startup — engineers can grep this to
# confirm which host/model/timeout are actually in use.
logger.info(
    f"LLM service config — "
    f"OLLAMA_HOST={OLLAMA_HOST} "
    f"MODEL={OLLAMA_MODEL} "
    f"TIMEOUT={OLLAMA_TIMEOUT}s"
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
    LLMStatus.TIMEOUT: {
        "reason": f"Ollama did not respond within {OLLAMA_TIMEOUT}s. Model may be loading or system is under load.",
        "fix":    f"Increase OLLAMA_TIMEOUT (current: {OLLAMA_TIMEOUT}s) or wait for the model to finish loading.",
    },
    LLMStatus.MODEL_NOT_FOUND: {
        "reason": f"Model '{OLLAMA_MODEL}' is not installed in Ollama (HTTP 404).",
        "fix":    f"Run:  ollama pull {OLLAMA_MODEL}",
    },
    LLMStatus.HTTP_ERROR: {
        "reason": "Ollama returned an unexpected HTTP error.",
        "fix":    "Check Ollama server logs for details.",
    },
    LLMStatus.JSON_PARSE_ERROR: {
        "reason": "Ollama returned a response that could not be parsed as JSON.",
        "fix":    f"Ensure {OLLAMA_MODEL} supports structured JSON output. Try:  ollama pull tinyllama:latest",
    },
    LLMStatus.SCHEMA_VALIDATION_FAILURE: {
        "reason": "Ollama returned valid JSON but it was missing required fields.",
        "fix":    "The model output did not match the expected schema. Check the system prompt in prompts/.",
    },
    LLMStatus.UNKNOWN_ERROR: {
        "reason": "An unexpected error occurred during the LLM call.",
        "fix":    "Check caretaker server logs for the full traceback.",
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


# ── Core call ───────────────────────────────────────────────────────────────

def _call_ollama(
    call_type: str,
    system_prompt: str,
    context_text: str,
    required_output_keys: list[str],
    entity_types: Optional[list] = None,
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

    Returns LLMCallResult — never None.
    """
    from services.llm_audit_service import LLMAuditService

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
        "model":   OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": hardened_system},
            {"role": "user",   "content": clean_text},
        ],
        "format": "json",
        "stream": False,
    }

    # ── Layer 4: Request audit log ────────────────────────────────────────
    logger.info(
        f"LLM request — call_type={call_type} model={OLLAMA_MODEL} "
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
            f"host={OLLAMA_HOST} model={OLLAMA_MODEL}: {exc_msg}"
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
            exc_msg         = f"Model '{OLLAMA_MODEL}' not found in Ollama (HTTP 404)"
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

    call_result = LLMCallResult(
        data              = result_data,
        status            = status,
        fallback_reason   = fallback_reason,
        exception_type    = exc_type,
        exception_message = exc_msg,
        duration_ms       = duration_ms,
        preflight         = preflight_data,
    )

    # ── 5. Audit log ──────────────────────────────────────────────────────
    LLMAuditService.log(
        model              = OLLAMA_MODEL,
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
    )

    return call_result


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
        result = _call_ollama(
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
        return _call_ollama(
            call_type="intent_reason",
            system_prompt=system_prompt,
            context_text=context_text,
            required_output_keys=["action_type", "message", "urgency"],
        )

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
