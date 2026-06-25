"""
Display (presentation-layer) un-masking for the desktop UIs.

There are TWO independent views of the same masked data:

  • LLM view — fully masked. Unchanged and NEVER touched here. Drafts/extraction keep
    reading the masked KnowledgeItem columns; this module is not on that path.

  • Human view (Follow-up Center / Meeting Prep popups) — non-secret identity &
    context entities (people, organizations, speakers, emails, locations, dates,
    meeting titles) resolved to their original values so the popup reads like the
    real meeting.

HARD SECURITY RULES (do not relax):
  • SECRETS ARE NEVER UN-MASKED. Resolution is gated by a strict ALLOWLIST of
    non-secret entity types (DISPLAY_SAFE_TYPES). Anything not on the list — API
    keys, connection strings, credentials, financial/health PII, or any unknown
    type — stays masked. The allowlist is keyed off the entity type encoded in the
    token name, so a new/unknown type fails closed (stays masked).
  • {{SECURE_REF:n}} placeholders are never matched or altered — credentials remain
    behind the Phase 4 Reveal flow / Secure Store.
  • Read-only. Reuses VaultService's existing decrypt primitive and the vault_tokens
    table; it changes nothing about masking, ingestion, storage, threat scoring, or
    the vault itself. No audit (UNMASK) event is written — this is non-secret display
    resolution, not an authorized secret reveal.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from database.models import VaultToken
# Reuse the existing AES-GCM primitive — we do NOT modify the vault. The gated
# VaultService.decrypt() is for secrets (approved AgentAction + audit); display of
# non-secret identity tokens uses the same crypto primitive without that gate.
from services.vault_service import _decrypt_bytes
from utils.logger import get_logger

logger = get_logger("services.display")

# Non-secret identity / context entity types that are safe to show a human. This is
# an ALLOWLIST — every entity type NOT listed here stays masked, so secrets and any
# future/unknown type fail closed.
DISPLAY_SAFE_TYPES: frozenset = frozenset({
    "PERSON", "SPEAKER", "ORG", "ORGANIZATION", "NORP", "NRP",
    "EMAIL_ADDRESS", "LOCATION", "GPE", "FAC",
    "DATE_TIME", "DATE", "TIME", "URL", "PHONE_NUMBER",
    "EVENT", "PRODUCT", "LANGUAGE", "WORK_OF_ART", "TITLE",
})

# A masked placeholder inside text: <S123_PERSON_1>, <ORGANIZATION_2>, … Deliberately
# does NOT match {{SECURE_REF:n}} (different delimiters) so credentials are untouched.
_PLACEHOLDER_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*_\d+)>")
_SCOPE_PREFIX_RE = re.compile(r"^S\d+_")
_INDEX_SUFFIX_RE = re.compile(r"_\d+$")


def _logical_type(token_name: str) -> str:
    """Entity type encoded in a (possibly source-scoped) token name.
    'S518_ORGANIZATION_1' → 'ORGANIZATION'; 'API_KEY_1' → 'API_KEY'."""
    name = _SCOPE_PREFIX_RE.sub("", token_name or "")
    name = _INDEX_SUFFIX_RE.sub("", name)
    return name.upper()


def _is_display_safe(token_name: str) -> bool:
    return _logical_type(token_name) in DISPLAY_SAFE_TYPES


def _resolve_values(token_names: set, db: Session) -> dict:
    """{token_name: plaintext} for the SAFE, decryptable subset only. Secrets/unknown
    types are never queried or decrypted; decrypt failures leave the token masked."""
    safe = {t for t in token_names if _is_display_safe(t)}
    if not safe:
        return {}
    out: dict = {}
    for row in db.query(VaultToken).filter(VaultToken.token.in_(safe)).all():
        # Defence in depth: re-check the stored token name against the allowlist.
        if not _is_display_safe(row.token):
            continue
        try:
            out[row.token] = _decrypt_bytes(row.ciphertext, row.key_version)
        except Exception:
            # Master key missing / corrupt ciphertext → leave it masked, never crash.
            logger.warning("display: could not decrypt a non-secret token; left masked")
    return out


def unmask_for_display(text: Optional[str], db: Session) -> Optional[str]:
    """Return `text` with non-secret <PLACEHOLDER> tokens replaced by their original
    values. Secrets, unknown types, {{SECURE_REF:n}}, and unresolvable tokens are
    left exactly as-is. Safe to call on any UI string."""
    if not text or "<" not in text:
        return text
    tokens = {m.group(1) for m in _PLACEHOLDER_RE.finditer(text)}
    resolved = _resolve_values(tokens, db)
    if not resolved:
        return text
    return _PLACEHOLDER_RE.sub(lambda m: resolved.get(m.group(1), m.group(0)), text)


def display_tokens(tokens: list, db: Session) -> list:
    """Resolve a list of BARE tokens (no angle brackets, e.g. participant_tokens) to
    human-readable values. Secret/unknown/unresolvable tokens are returned unchanged."""
    if not tokens:
        return tokens
    bare = {str(t).strip("<>") for t in tokens if t}
    resolved = _resolve_values(bare, db)
    return [resolved.get(str(t).strip("<>"), t) for t in tokens]
