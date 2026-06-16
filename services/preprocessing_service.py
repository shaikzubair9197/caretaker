import re
import html as _html
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.preprocessing")

# ---------------------------------------------------------------------------
# Presidio setup (lazy-init so import doesn't block startup if model missing)
# ---------------------------------------------------------------------------

_analyzer = None
_anonymizer = None


def _get_presidio():
    global _analyzer, _anonymizer
    if _analyzer is not None:
        return _analyzer, _anonymizer
    try:
        from presidio_analyzer import AnalyzerEngine, PatternRecognizer, Pattern
        from presidio_analyzer.nlp_engine import NlpEngineProvider
        from presidio_anonymizer import AnonymizerEngine

        # Use en_core_web_lg for high-quality NER
        provider = NlpEngineProvider(nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        })
        nlp_engine = provider.create_engine()

        _analyzer = AnalyzerEngine(nlp_engine=nlp_engine, supported_languages=["en"])

        # Custom recognizer: generic API keys / bearer tokens (alphanum 20+ chars)
        api_key_recognizer = PatternRecognizer(
            supported_entity="API_KEY",
            patterns=[
                Pattern("API_KEY_LONG", r"\b[A-Za-z0-9_\-]{16,}\b", 0.5),
                Pattern(
                    "BEARER_HEADER",
                    r"\b(bearer|token|apikey|api_key|secret|password|passwd|pwd)\s*[=:]\s*\S+",
                    0.9,
                ),
                Pattern("AWS_KEY", r"\bAKIA[0-9A-Z]{16}\b", 0.95),
            ],
        )

        # Custom recognizer: database / service connection strings
        conn_str_recognizer = PatternRecognizer(
            supported_entity="CONNECTION_STRING",
            patterns=[
                Pattern(
                    "DB_URL",
                    r"(postgres|postgresql|mysql|mongodb|redis|mssql|sqlite)://[^\s\"']+",
                    0.95,
                )
            ],
        )

        _analyzer.registry.add_recognizer(api_key_recognizer)
        _analyzer.registry.add_recognizer(conn_str_recognizer)
        _anonymizer = AnonymizerEngine()

        logger.info("Presidio engine initialised with en_core_web_lg")
    except Exception as e:
        logger.warning(f"Presidio unavailable ({e}) — falling back to regex masking")
        _analyzer = None
        _anonymizer = None

    return _analyzer, _anonymizer


# ---------------------------------------------------------------------------
# Regex fallback patterns (used when Presidio is unavailable)
# ---------------------------------------------------------------------------

_FALLBACK_PATTERNS = [
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS_KEY"),
    (re.compile(r"(postgres|mysql|mongodb|redis|mssql)://[^\s\"']+", re.IGNORECASE), "CONNECTION_STRING"),
    (re.compile(r"\b(bearer|token|apikey|api_key|secret|password|passwd|pwd)\s*[=:]\s*\S+", re.IGNORECASE), "API_KEY"),
    (re.compile(r"\b(?:\d[ -]?){13,16}\b"), "CREDIT_CARD"),
    (re.compile(r"\b(192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)\d+\.\d+\b"), "PRIVATE_IP"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "EMAIL_ADDRESS"),
    (re.compile(r"\b(\+?\d[\d\s\-().]{7,}\d)\b"), "PHONE_NUMBER"),
]


def _mask_with_regex(text: str) -> tuple[str, list]:
    result = text
    redactions = []
    for pattern, label in _FALLBACK_PATTERNS:
        def replacer(m, lbl=label):
            redactions.append({"type": lbl, "score": 1.0})
            return f"<{lbl}>"
        result = pattern.sub(replacer, result)
    return result, redactions


# ---------------------------------------------------------------------------
# Noise patterns
# ---------------------------------------------------------------------------

_SIGNATURE_PATTERNS = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in [
        r"[-_]{2,}\s*\n.*",
        r"sent from my (iphone|ipad|android|phone)",
        r"get outlook for (ios|android)",
        r"this (email|message) (and any attachments )?may (be |contain )?confidential",
        r"if you (are not the intended|received this in error)",
        r"please consider the environment before printing",
        r"(kind|best|warm|many thanks|regards|sincerely|cheers|thanks),?\s*\n\s*\w+",
        r"caution:?\s*(this email|external email)",
        r"disclaimer:.*",
        r"(unsubscribe|manage (your )?preferences|view in browser)",
    ]
]

_AUTOREPLY_PHRASES = [
    "out of office", "automatic reply", "auto-reply", "i am currently away",
    "i'm on vacation", "i will be back", "i am on leave",
    "do not reply to this email", "this is an automated", "do not reply",
    "noreply", "no-reply",
]

# ---------------------------------------------------------------------------
# Sensitivity classification keywords
# ---------------------------------------------------------------------------

_CONFIDENTIAL_KEYWORDS = [
    "password", "passwd", "pwd", "secret", "token", "api key", "api_key",
    "credential", "private key", "certificate", "ssn", "social security",
    "credit card", "bank account", "routing number",
]

_INTERNAL_KEYWORDS = [
    "internal", "confidential", "not for distribution", "proprietary",
    "do not share", "restricted", "company only", "team only",
]

# Entity types that always mean CONFIDENTIAL
_CONFIDENTIAL_ENTITY_TYPES = {
    "API_KEY", "CONNECTION_STRING", "AWS_KEY",
    "CREDIT_CARD", "US_SSN", "IN_PAN", "IBAN_CODE",
    "US_BANK_NUMBER", "MEDICAL_LICENSE",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class PreprocessedResult:
    original_text: str
    cleaned_text: str           # noise stripped
    masked_text: str            # PII/secrets replaced with <ENTITY_TYPE> placeholders
    sensitivity_label: str      # PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED
    is_autoreply: bool
    redactions: list = field(default_factory=list)
    # each entry: {"type": entity_type, "score": float}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class PreprocessingService:

    @staticmethod
    def preprocess(text: str) -> PreprocessedResult:
        from services.llm_guard import sanitize_input

        is_autoreply = PreprocessingService._is_autoreply(text)
        cleaned = PreprocessingService.strip_noise(text)
        # Strip injection patterns from cleaned text so they never reach
        # the memory store or task descriptions.
        cleaned, injection_warnings = sanitize_input(cleaned)
        if injection_warnings:
            logger.warning(
                f"Injection patterns stripped from cleaned_text: {injection_warnings}"
            )
        masked, redactions = PreprocessingService.mask_pii(cleaned)
        label = PreprocessingService.classify_sensitivity(text, redactions)

        logger.info(
            f"Preprocessed: label={label}, autoreply={is_autoreply}, "
            f"redactions={len(redactions)}"
        )

        return PreprocessedResult(
            original_text=text,
            cleaned_text=cleaned,
            masked_text=masked,
            sensitivity_label=label,
            is_autoreply=is_autoreply,
            redactions=redactions,
        )

    @staticmethod
    def clean_html(text: str) -> str:
        """
        HTML Cleanup stage — required for Microsoft Graph bodies (Teams messages
        and Outlook emails arrive as contentType=html).

        Strips tags but PRESERVES inner text (so secrets inside <code>/<pre>
        blocks remain visible to the masking layer and get vaulted), unescapes
        HTML entities, and normalises whitespace. Block-level tags become
        newlines so structure is not silently concatenated.
        """
        if not text:
            return text
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
        t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
        t = re.sub(r"</(p|div|li|tr|h[1-6]|ul|ol|blockquote)>", "\n", t, flags=re.I)
        t = re.sub(r"<[^>]+>", "", t)          # remove all remaining tags
        t = _html.unescape(t)                  # &amp; -> &, &lt; -> <, &nbsp; -> space
        t = t.replace("\xa0", " ")
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r"\n{3,}", "\n\n", t)
        return t.strip()

    @staticmethod
    def strip_noise(text: str) -> str:
        result = text
        for pattern in _SIGNATURE_PATTERNS:
            result = pattern.sub("", result)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    @staticmethod
    def mask_pii(text: str) -> tuple[str, list]:
        """
        Mask PII and secrets using Presidio (spaCy NER + custom recognizers).
        Falls back to regex patterns if Presidio is unavailable.
        Placeholders use <ENTITY_TYPE> format for semantic LLM reasoning.
        """
        analyzer, anonymizer = _get_presidio()

        if analyzer is None or anonymizer is None:
            logger.debug("Using regex fallback for PII masking")
            return _mask_with_regex(text)

        try:
            results = analyzer.analyze(text=text, language="en")
            if not results:
                return text, []

            from presidio_anonymizer.entities import OperatorConfig
            operators = {
                entity_type: OperatorConfig("replace", {"new_value": f"<{entity_type}>"})
                for entity_type in {r.entity_type for r in results}
            }

            anonymized = anonymizer.anonymize(
                text=text,
                analyzer_results=results,
                operators=operators,
            )
            redactions = [
                {"type": r.entity_type, "score": round(r.score, 3)}
                for r in results
            ]
            return anonymized.text, redactions

        except Exception as e:
            logger.error(f"Presidio analysis failed: {e} — using regex fallback")
            return _mask_with_regex(text)

    # Keep old name as alias so existing call sites don't break
    @staticmethod
    def mask_secrets(text: str) -> tuple[str, list]:
        return PreprocessingService.mask_pii(text)

    @staticmethod
    def mask_pii_indexed(text: str) -> tuple[str, dict[str, str], list[dict]]:
        """
        Produce INDEXED tokens: <PERSON_1>, <API_KEY_1>, etc.

        Unlike mask_pii() which emits generic <ENTITY_TYPE> placeholders,
        this method assigns a sequential per-type index so each unique entity
        value gets a stable, reversible token within the document.

        Returns:
            masked_text  — text with <TOKEN_N> placeholders
            token_map    — {token_name: original_value} for vault storage
            redactions   — [{type, score, token}] list

        Algorithm (right-to-left replacement preserves original offsets):
            1. Run Presidio (or regex fallback) to get entity spans.
            2. Resolve OVERLAPPING spans — Presidio can return overlapping
               entities (e.g. EMAIL_ADDRESS "john.doe@company.com" overlapping
               URL "company.com"). Greedily keep the highest-score (then longest)
               span and drop any span overlapping an accepted one, otherwise the
               replacements collide and corrupt the output (<URL_2>ADDRESS_1>).
            3. Sort the kept spans by start position DESCENDING.
            4. For each span, assign per-type counter → token name.
               Same (type, value) reuses the same token (stable within doc).
            5. Replace span in text right-to-left so earlier offsets stay valid.
        """
        analyzer, _ = _get_presidio()

        if analyzer is None:
            return PreprocessingService._mask_indexed_regex(text)

        try:
            results = analyzer.analyze(text=text, language="en")
            if not results:
                return text, {}, []

            # ── Step 2: resolve overlaps (highest score, then longest span wins) ──
            accepted: list = []
            for r in sorted(results, key=lambda r: (r.score, r.end - r.start), reverse=True):
                if any(r.start < a.end and a.start < r.end for a in accepted):
                    continue  # overlaps an already-accepted span — drop it
                accepted.append(r)

            # Sort descending by start so right-to-left replacement keeps offsets correct
            sorted_results = sorted(accepted, key=lambda r: r.start, reverse=True)

            type_counters: dict[str, int] = {}
            value_to_token: dict[tuple, str] = {}
            token_map: dict[str, str] = {}
            redactions: list[dict] = []
            masked = text

            for result in sorted_results:
                original_value = text[result.start:result.end]
                entity_type = result.entity_type
                key = (entity_type, original_value)

                if key in value_to_token:
                    token_name = value_to_token[key]
                else:
                    type_counters[entity_type] = type_counters.get(entity_type, 0) + 1
                    token_name = f"{entity_type}_{type_counters[entity_type]}"
                    value_to_token[key] = token_name
                    token_map[token_name] = original_value

                masked = masked[:result.start] + f"<{token_name}>" + masked[result.end:]
                redactions.append({
                    "type":  entity_type,
                    "score": round(result.score, 3),
                    "token": token_name,
                })

            return masked, token_map, redactions

        except Exception as e:
            logger.error(f"mask_pii_indexed Presidio failed: {e} — using regex fallback")
            return PreprocessingService._mask_indexed_regex(text)

    @staticmethod
    def _mask_indexed_regex(text: str) -> tuple[str, dict[str, str], list[dict]]:
        """Regex-based fallback for mask_pii_indexed when Presidio is unavailable."""
        result = text
        type_counters: dict[str, int] = {}
        value_to_token: dict[tuple, str] = {}
        token_map: dict[str, str] = {}
        redactions: list[dict] = []

        for pattern, label in _FALLBACK_PATTERNS:
            def replacer(m, lbl=label):
                original = m.group(0)
                key = (lbl, original)
                if key not in value_to_token:
                    type_counters[lbl] = type_counters.get(lbl, 0) + 1
                    token_name = f"{lbl}_{type_counters[lbl]}"
                    value_to_token[key] = token_name
                    token_map[token_name] = original
                else:
                    token_name = value_to_token[key]
                redactions.append({"type": lbl, "score": 1.0, "token": token_name})
                return f"<{token_name}>"
            result = pattern.sub(replacer, result)

        return result, token_map, redactions

    @staticmethod
    def classify_sensitivity(text: str, redactions: Optional[list] = None) -> str:
        # Check confidential signals first (entity types and keywords)
        if redactions:
            detected_types = {r["type"] for r in redactions}
            if detected_types & _CONFIDENTIAL_ENTITY_TYPES:
                return "CONFIDENTIAL"

        lower = text.lower()
        if any(kw in lower for kw in _CONFIDENTIAL_KEYWORDS):
            return "CONFIDENTIAL"

        # Then INTERNAL signals
        if redactions:
            detected_types = {r["type"] for r in redactions}
            if detected_types & {"PERSON", "LOCATION", "ORG", "ORGANIZATION", "EMAIL_ADDRESS", "PHONE_NUMBER"}:
                return "INTERNAL"

        if any(kw in lower for kw in _INTERNAL_KEYWORDS):
            return "INTERNAL"

        return "PUBLIC"

    @staticmethod
    def _is_autoreply(text: str) -> bool:
        lower = text.lower()
        return any(phrase in lower for phrase in _AUTOREPLY_PHRASES)
