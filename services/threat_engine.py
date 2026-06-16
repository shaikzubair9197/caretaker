"""
ThreatEngine — per-source-type threat scoring for Phase 2A.

Scoring model (see blueprint §8):
  Each signal produces a sub-score 0.0–1.0.
  Aggregate = min(1.0, weighted_mean of applicable signals).
  Thresholds: CLEAN < 0.40 | FLAGGED 0.40–0.79 | QUARANTINE >= 0.80.

Reuses:
  - LLMGuard.sanitize_input() for injection signal detection.
  - PreprocessingService.mask_pii() to detect credential entity types.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from utils.logger import get_logger

logger = get_logger("services.threat_engine")

# ── Thresholds ─────────────────────────────────────────────────────────────────

_QUARANTINE = 0.80
_FLAGGED    = 0.40

# ── Signal patterns ────────────────────────────────────────────────────────────

_URGENCY_WORDS = re.compile(
    r"\b(urgent|urgently|immediately|asap|critical|within\s+\d+\s+(hour|minute|day)|"
    r"account\s+suspended|action\s+required|act\s+now|deadline|expire[sd]?)\b",
    re.IGNORECASE,
)

_CREDENTIAL_REQUEST = re.compile(
    r"\b(verify\s+your\s+(password|credentials?|account)|reply\s+with\s+your\s+"
    r"(username|password|login)|enter\s+your\s+(password|credentials?)|"
    r"confirm\s+your\s+identity|provide\s+your\s+(api\s*key|secret))\b",
    re.IGNORECASE,
)

_LOGIN_LURE = re.compile(
    r"\b(login\s+immediately|log\s*in\s+now|sign\s*in\s+to\s+(verify|confirm)|"
    r"account\s+(will\s+)?(expire|be\s+(suspended|locked|deleted))|"
    r"update\s+your\s+(password|account|details)|confirm\s+your\s+(identity|account)|"
    r"click\s+(here\s+)?to\s+(verify|login|sign\s*in|confirm))\b",
    re.IGNORECASE,
)

_BEC_PATTERNS = re.compile(
    r"\b(wire\s+transfer|bank\s+transfer|send\s+(money|funds|payment)|"
    r"process\s+(this\s+)?payment|purchase\s+(gift\s+)?card|invoice\s+attached|"
    r"ceo|chief\s+executive|president|cfo|chief\s+financial)\b",
    re.IGNORECASE,
)

_PLAINTEXT_PASSWORD = re.compile(
    r"\b(password|passwd|pwd|pass)\s*[=:]\s*\S+",
    re.IGNORECASE,
)

_SUSPICIOUS_TLDS = re.compile(
    r"https?://[^\s\"']*\.(xyz|tk|ml|ga|cf|pw|top|click|download|loan|win|party)\b",
    re.IGNORECASE,
)

_URL_WITH_TOKEN = re.compile(
    r"https?://[^\s\"']*[?&](token|key|secret|api_key|access_token)=[^\s\"'&]+",
    re.IGNORECASE,
)

_SOCIAL_ENG = re.compile(
    r"\b(don'?t\s+tell\s+anyone|keep\s+this\s+between\s+us|ignore\s+security|"
    r"bypass\s+(security|verification)|delete\s+this\s+(message|email)\s+after|"
    r"don'?t\s+forward|confidentially)\b",
    re.IGNORECASE,
)

# Only DIGIT/character-SUBSTITUTED brand spellings — must NOT match the genuine
# brand names (the previous [o0] classes matched real "Microsoft"/"google" and
# false-flagged every legitimate Microsoft email as a typosquat).
_TYPOSQUAT = re.compile(
    r"\b(micr0soft|micros0ft|rnicrosoft|micr0s0ft|"
    r"g00gle|g0ogle|go0gle|"
    r"amaz0n|arnazon|"
    r"paypa1|payqal|"
    r"app1e|0utlook|"
    r"faceb00k|faceb0ok|facebo0k)\b",
    re.IGNORECASE,
)

# Entity types that indicate credential exposure
_CREDENTIAL_ENTITY_TYPES = {"API_KEY", "CONNECTION_STRING", "AWS_KEY", "BEARER_HEADER"}


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class ThreatScore:
    aggregate:          float
    category:           str                     # CLEAN | FLAGGED | QUARANTINE
    phishing_score:     float = 0.0
    bec_score:          float = 0.0
    credential_risk:    float = 0.0
    urgency_score:      float = 0.0
    injection_score:    float = 0.0
    social_eng_score:   float = 0.0
    link_risk_score:    float = 0.0
    flags:              list  = field(default_factory=list)
    recommended_action: str   = ""
    notes:              str   = ""


def _categorise(score: float) -> str:
    if score >= _QUARANTINE:
        return "QUARANTINE"
    if score >= _FLAGGED:
        return "FLAGGED"
    return "CLEAN"


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def _body_text(payload: dict) -> str:
    """Extract plain-text body from any Graph source payload."""
    body = payload.get("body", {})
    if isinstance(body, dict):
        return body.get("content", "") or ""
    return str(body or "")


def _detect_credential_entities(text: str) -> tuple[float, list[str]]:
    """Run Presidio on text to detect credential-type entities."""
    try:
        from services.preprocessing_service import PreprocessingService
        # Credential-scan the HTML-CLEANED text. Raw Graph bodies are HTML, and
        # the generic API_KEY recognizer (16+ alphanumerics) false-flags long
        # hyphenated CSS class names (e.g. "mobile-bottom-padding-15") and
        # href tracking tokens. clean_html drops tags/attributes (removing CSS
        # and href URLs); we also strip any visible URL text. Inline keys
        # (sk-…, AKIA…) and DB connection strings survive both steps.
        scrubbed = PreprocessingService.clean_html(text)
        scrubbed = re.sub(r"https?://\S+", " ", scrubbed)
        _, redactions = PreprocessingService.mask_pii(scrubbed)
        detected = {r["type"] for r in redactions}
        hit_types = detected & _CREDENTIAL_ENTITY_TYPES
        if hit_types:
            flags = [f"{t}_DETECTED" for t in sorted(hit_types)]
            score = 0.95 if "AWS_KEY" in hit_types or "CONNECTION_STRING" in hit_types else 0.75
            return score, flags
    except Exception:
        pass
    # Regex fallback for plaintext credentials
    if _PLAINTEXT_PASSWORD.search(text):
        return 0.80, ["PLAINTEXT_PASSWORD_DETECTED"]
    return 0.0, []


def _detect_injection(text: str) -> float:
    """Reuse LLMGuard's injection pattern check as a threat signal."""
    try:
        from services.llm_guard import sanitize_input
        _, warnings = sanitize_input(text)
        return min(1.0, len(warnings) * 0.2)
    except Exception:
        return 0.0


# ── Per-source scoring ─────────────────────────────────────────────────────────

class ThreatEngine:

    @staticmethod
    def score_email(payload: dict) -> ThreatScore:
        """Score an Outlook email payload."""
        body = _body_text(payload)
        subject = payload.get("subject", "") or ""
        from_addr: str = (
            (payload.get("from", {}) or {})
            .get("emailAddress", {})
            .get("address", "")
        ) or ""
        from_name: str = (
            (payload.get("from", {}) or {})
            .get("emailAddress", {})
            .get("name", "")
        ) or ""
        full_text = f"{subject}\n{body}"
        flags: list[str] = []

        # 1. Credential exposure in body (weight 0.40)
        cred_score, cred_flags = _detect_credential_entities(full_text)
        flags.extend(cred_flags)

        # 2. Typosquatted brand — check sender AND body/links (a digit-substituted
        #    brand like "micr0soft" anywhere is near-certain phishing).
        typo_score = 0.0
        if _TYPOSQUAT.search(from_addr) or _TYPOSQUAT.search(from_name):
            typo_score = 0.90
            flags.append("TYPOSQUATTED_SENDER")
        elif _TYPOSQUAT.search(full_text):
            typo_score = 0.90
            flags.append("TYPOSQUAT_IN_BODY")

        # 3. Sender domain mismatch: display name says "Microsoft" but domain differs
        domain_mismatch_score = 0.0
        if from_name and from_addr:
            name_lower = from_name.lower()
            trusted_brands = ["microsoft", "google", "amazon", "paypal", "apple"]
            for brand in trusted_brands:
                if brand in name_lower and brand not in from_addr.lower():
                    domain_mismatch_score = 0.85
                    flags.append(f"DOMAIN_MISMATCH_{brand.upper()}")
                    break

        # 4. Credential request / login lure phrases
        cred_req_score = 0.0
        if _CREDENTIAL_REQUEST.search(full_text) or _LOGIN_LURE.search(full_text):
            cred_req_score = 0.85
            flags.append("CREDENTIAL_REQUEST_DETECTED")

        # 5. BEC patterns
        bec_score = 0.60 if _BEC_PATTERNS.search(full_text) else 0.0
        if bec_score > 0:
            flags.append("BEC_PATTERN_DETECTED")

        # 6. Urgency manipulation
        urgency_score = 0.40 if _URGENCY_WORDS.search(full_text) else 0.0

        # 7. Suspicious links
        link_risk = 0.0
        if _SUSPICIOUS_TLDS.search(full_text):
            link_risk = 0.85
            flags.append("SUSPICIOUS_TLD_LINK")
        if _URL_WITH_TOKEN.search(full_text):
            link_risk = max(link_risk, 0.70)
            flags.append("URL_WITH_AUTH_TOKEN")

        # Phishing score = strongest single signal (a lone high-confidence signal
        # such as a typosquat domain or a credential lure should flag on its own;
        # the previous weighted-mean diluted single strong signals below threshold).
        phishing_signals = [0.0, typo_score, domain_mismatch_score, link_risk]
        if cred_req_score and urgency_score:
            phishing_signals.append(0.85)      # lure + urgency = classic phish
        elif cred_req_score:
            phishing_signals.append(0.55)
        if urgency_score and link_risk:
            phishing_signals.append(0.75)
        phishing_score = _clamp(max(phishing_signals))

        aggregate = _clamp(max(
            phishing_score,
            cred_score,
            cred_score * 0.40 + bec_score * 0.20 + urgency_score * 0.15,
        ))
        category = _categorise(aggregate)

        recommended = ""
        if category == "QUARANTINE":
            recommended = "QUARANTINE + ALERT_SECURITY_TEAM"
        elif category == "FLAGGED":
            recommended = "PROCESS_WITH_ELEVATED_AUDIT"

        return ThreatScore(
            aggregate          = round(aggregate, 3),
            category           = category,
            phishing_score     = round(phishing_score, 3),
            bec_score          = round(bec_score, 3),
            credential_risk    = round(cred_score, 3),
            urgency_score      = round(urgency_score, 3),
            link_risk_score    = round(link_risk, 3),
            flags              = flags,
            recommended_action = recommended,
            notes              = f"from={from_addr[:60]}",
        )

    @staticmethod
    def score_teams_message(payload: dict) -> ThreatScore:
        """Score a Teams chat or channel message payload."""
        body = _body_text(payload)
        flags: list[str] = []

        # 1. Credential exposure (weight 0.50)
        cred_score, cred_flags = _detect_credential_entities(body)
        flags.extend(cred_flags)

        # 2. Prompt injection (weight 0.40) — reuse LLMGuard
        injection_score = _detect_injection(body)
        if injection_score > 0:
            flags.append("INJECTION_PATTERN_IN_CHAT")

        # 3. Social engineering language (weight 0.20)
        social_eng = 0.60 if _SOCIAL_ENG.search(body) else 0.0
        if social_eng > 0:
            flags.append("SOCIAL_ENGINEERING_LANGUAGE")

        # 4. Plaintext password (weight 0.45)
        pwd_score = 0.85 if _PLAINTEXT_PASSWORD.search(body) else 0.0
        if pwd_score > 0:
            flags.append("PLAINTEXT_PASSWORD_IN_CHAT")

        # 5. External link with token (weight 0.15)
        link_risk = 0.60 if _URL_WITH_TOKEN.search(body) else 0.0

        aggregate = _clamp(max(
            cred_score,
            pwd_score,
            injection_score * 0.40 + social_eng * 0.20 + link_risk * 0.15,
        ))
        category = _categorise(aggregate)

        recommended = ""
        if category == "QUARANTINE":
            recommended = "QUARANTINE + ALERT_SECURITY_TEAM + CREATE_ROTATION_TASKS"

        return ThreatScore(
            aggregate          = round(aggregate, 3),
            category           = category,
            credential_risk    = round(cred_score, 3),
            injection_score    = round(injection_score, 3),
            social_eng_score   = round(social_eng, 3),
            link_risk_score    = round(link_risk, 3),
            flags              = flags,
            recommended_action = recommended,
        )

    @staticmethod
    def score_transcript(payload: dict) -> ThreatScore:
        """Score a meeting transcript payload."""
        content = payload.get("content", "") or ""
        participants = payload.get("participants", []) or []
        flags: list[str] = []

        # 1. Credential exposure in transcript body
        cred_score, cred_flags = _detect_credential_entities(content)
        flags.extend(cred_flags)

        # 2. Injection in participant display names
        participant_text = " ".join(str(p) for p in participants)
        injection_score = _detect_injection(participant_text)
        if injection_score > 0:
            flags.append("INJECTION_IN_PARTICIPANT_NAME")

        aggregate = _clamp(max(cred_score, injection_score * 0.40))
        category = _categorise(aggregate)

        return ThreatScore(
            aggregate       = round(aggregate, 3),
            category        = category,
            credential_risk = round(cred_score, 3),
            injection_score = round(injection_score, 3),
            flags           = flags,
        )

    @staticmethod
    def score_calendar(payload: dict) -> ThreatScore:
        """Score a calendar event payload — generally low risk."""
        body = _body_text(payload)
        subject = payload.get("subject", "") or ""
        full_text = f"{subject}\n{body}"
        flags: list[str] = []

        cred_score, cred_flags = _detect_credential_entities(full_text)
        flags.extend(cred_flags)

        injection_score = _detect_injection(full_text)
        if injection_score > 0:
            flags.append("INJECTION_IN_CALENDAR_BODY")

        aggregate = _clamp(max(cred_score, injection_score * 0.30))
        return ThreatScore(
            aggregate       = round(aggregate, 3),
            category        = _categorise(aggregate),
            credential_risk = round(cred_score, 3),
            injection_score = round(injection_score, 3),
            flags           = flags,
        )

    @staticmethod
    def score(source_type: str, payload: dict) -> ThreatScore:
        """Dispatch to the right scorer by source_type."""
        dispatch = {
            "outlook_email":  ThreatEngine.score_email,
            "teams_chat":     ThreatEngine.score_teams_message,
            "teams_channel":  ThreatEngine.score_teams_message,
            "transcript":     ThreatEngine.score_transcript,
            "calendar":       ThreatEngine.score_calendar,
        }
        scorer = dispatch.get(source_type, ThreatEngine.score_email)
        try:
            result = scorer(payload)
            logger.debug(
                f"ThreatEngine.score source_type={source_type} "
                f"aggregate={result.aggregate} category={result.category} "
                f"flags={result.flags}"
            )
            return result
        except Exception as e:
            logger.error(f"ThreatEngine.score failed ({source_type}): {e}")
            return ThreatScore(aggregate=0.0, category="CLEAN", notes=f"scoring_error:{e}")
