"""
LLM security guard.

Two responsibilities:
1. sanitize_input()  — detect and neutralise prompt injection patterns
2. validate_output() — verify LLM response has expected keys before use

Neither function raises an exception. Both return safe values so callers can
always proceed — injection findings are logged and the cleaned text is used;
output validation failures cause the caller to fall back to deterministic logic.
"""

import re
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.llm_guard")

MAX_INPUT_CHARS = 10_000

# ---------------------------------------------------------------------------
# Known prompt injection patterns
# ---------------------------------------------------------------------------

_INJECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Classic instruction override
    (re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)", re.IGNORECASE),
     "instruction_override"),

    # Role reassignment
    (re.compile(r"you\s+are\s+now\s+(a|an)\s+\w+", re.IGNORECASE),
     "role_reassignment"),

    # "Act as / pretend to be"
    (re.compile(r"\b(act\s+as|pretend\s+(to\s+be|you\s+are)|roleplay\s+as)\b", re.IGNORECASE),
     "role_reassignment"),

    # ChatML boundary markers
    (re.compile(r"<\|(im_start|im_end|system|user|assistant)\|>"),
     "chatML_boundary"),

    # Mistral instruct markers
    (re.compile(r"\[INST\]|\[/INST\]"),
     "mistral_boundary"),

    # Alpaca / vicuna format
    (re.compile(r"###\s*(Human|Assistant|System)\s*:", re.IGNORECASE),
     "alpaca_boundary"),

    # System prompt exfiltration
    (re.compile(
        r"\b(print|output|repeat|echo|reveal|show|display)\s+(all\s+)?(your\s+)?"
        r"(instructions?|system\s*prompt|context|rules?|guidelines?)\b",
        re.IGNORECASE,
    ),
     "exfiltration_attempt"),

    # Jailbreak DAN / override phrases
    (re.compile(r"\bDAN\b|\bdo\s+anything\s+now\b", re.IGNORECASE),
     "jailbreak_phrase"),

    # Prompt delimiter injection
    (re.compile(r"[-]{3,}\s*(END|BEGIN)\s*(SYSTEM|PROMPT|INSTRUCTIONS?)\s*[-]{3,}", re.IGNORECASE),
     "delimiter_injection"),
]


def sanitize_input(text: str) -> tuple[str, list[str]]:
    """
    Scan text for injection patterns and replace matches with [FILTERED].
    Returns (cleaned_text, list_of_warning_labels).

    Never rejects input entirely — the user may have copied an email that
    legitimately contains these phrases as data, not as instructions.
    """
    if len(text) > MAX_INPUT_CHARS:
        text = text[:MAX_INPUT_CHARS]
        logger.warning(f"Input truncated to {MAX_INPUT_CHARS} chars")

    cleaned = text
    warnings: list[str] = []

    for pattern, label in _INJECTION_PATTERNS:
        if pattern.search(cleaned):
            cleaned = pattern.sub("[FILTERED]", cleaned)
            warnings.append(label)
            logger.warning(f"Injection pattern detected and filtered: {label}")

    if warnings:
        logger.warning(f"Total injection patterns filtered: {len(warnings)} → {warnings}")

    return cleaned, warnings


def validate_output(raw: Optional[dict], required_keys: list[str]) -> bool:
    """
    Returns True if raw is a dict containing all required_keys.
    Returns False (and logs) if validation fails.
    """
    if not isinstance(raw, dict):
        logger.warning(f"LLM output is not a dict: {type(raw)}")
        return False

    missing = [k for k in required_keys if k not in raw]
    if missing:
        logger.warning(f"LLM output missing required keys: {missing}")
        return False

    return True
