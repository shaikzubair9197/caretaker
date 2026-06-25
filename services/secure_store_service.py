"""
SecureStoreService — the single, credential-type-agnostic secure store for the
Follow-up Center (plan Phase 1).

Every confidential value discovered by any source (Teams, Email, manual additions,
future integrations) is synchronized here, encrypted with AES-256-GCM (via
VaultService) and versioned with rotation history. Draft generation, reveal and
send-time resolution resolve ONLY against this store — they never re-read Teams or
Email.

Security invariants:
- Plaintext NEVER lives in any column except CredentialVersion.ciphertext.
- "Same value vs. rotated" is decided by a keyed, non-reversible fingerprint
  (VaultService.fingerprint) — ingestion never decrypts.
- Producing a plaintext value happens only in decrypt_credential(), behind the
  approved-AgentAction + justification gate inherited from VaultService, and is
  always the LATEST ACTIVE version.

Module-function style, matching knowledge_evolution_service / knowledge_retrieval_service.
"""

import re
from typing import Optional

from sqlalchemy.orm import Session

from database.models import AuditEvent, CredentialVersion, SecureCredential
from services.vault_service import VaultService
from utils.logger import get_logger
from utils.time_utils import utcnow

logger = get_logger("services.secure_store")

# Confidence above which resolution auto-resolves without asking the user.
# (The caller reads SECURE_RESOLUTION_THRESHOLD; this is just a documented default.)
DEFAULT_RESOLUTION_THRESHOLD = 0.8


def _norm(value) -> str:
    return str(value).strip().lower() if value else ""


# ── Descriptor parsing → SecureReference (Phase 4) ───────────────────────────
#
# SINGLE SOURCE OF TRUTH for the non-secret descriptor vocabulary. Phase 2's
# continuous sync (api/graph_sync) and Phase 4's draft-time resolution MUST infer
# system_name / environment / credential_type the same way, or a credential stored
# under one set of dimensions can never be matched at draft time. Keeping the vocab
# here (the service that owns SecureReference semantics) and having graph_sync
# delegate guarantees they never drift. Operates ONLY on masked descriptor words —
# never the secret value, never an LLM call.

# Best-effort normalization vocab (descriptor words, never secret values).
_SYSTEM_KEYWORDS = {
    "openai": "openai", "azure": "azure", "anthropic": "anthropic", "aws": "aws",
    "github": "github", "gitlab": "gitlab", "stripe": "stripe",
    "postgresql": "postgres", "postgres": "postgres", "mysql": "mysql",
    "mongodb": "mongodb", "redis": "redis", "snowflake": "snowflake",
    "datadog": "datadog", "twilio": "twilio", "sendgrid": "sendgrid",
}
_ENV_KEYWORDS = {
    "production": "production", "prod": "production", "staging": "staging",
    "stage": "staging", "development": "development", "dev": "development",
    "sandbox": "sandbox", "qa": "qa", "uat": "uat",
}

# Any of these words means the text REFERS to a confidential value (so a draft
# should embed a {{SECURE_REF:n}} rather than prose). Drives the credential gate.
_CREDENTIAL_DESCRIPTOR_RE = re.compile(
    r"\b(api[\s_-]?keys?|access[\s_-]?keys?|secret[\s_-]?keys?|client[\s_-]?secrets?|"
    r"private[\s_-]?keys?|connection[\s_-]?strings?|database[\s_-]?url|db[\s_-]?url|"
    r"jdbc|dsn|passwords?|passwd|pwd|credentials?|secrets?|tokens?|bearer|keys?)\b",
    re.IGNORECASE,
)
# Narrow descriptors that map CONFIDENTLY onto the store's credential_type taxonomy
# (currently {api_key, connection_string} — what Phase 2 sync produces). Anything
# generic ("credential", "secret", "token") stays None so the credential_type hard
# filter in resolve_secure_reference does not eliminate an otherwise-valid match;
# system_name + context dimensions then carry the resolution.
_CONNECTION_RE = re.compile(
    r"\b(connection[\s_-]?strings?|database[\s_-]?url|db[\s_-]?url|jdbc|dsn)\b", re.IGNORECASE
)
_APIKEY_RE = re.compile(
    r"\b(api[\s_-]?keys?|access[\s_-]?keys?|secret[\s_-]?keys?|client[\s_-]?secrets?|"
    r"private[\s_-]?keys?)\b",
    re.IGNORECASE,
)


def infer_system_and_context(descriptor_text: str) -> tuple:
    """Infer (system_name, context) from masked descriptor words. First system
    keyword wins (Phase 2 sync stores one system per message); environment is the
    first env keyword found. Behaviour-preserving home for api/graph_sync's prior
    _infer_system_and_context — never reads the secret value, never calls an LLM."""
    low = (descriptor_text or "").lower()
    system = next((v for k, v in _SYSTEM_KEYWORDS.items() if k in low), None)
    context: dict = {}
    for k, v in _ENV_KEYWORDS.items():
        if re.search(rf"\b{re.escape(k)}\b", low):
            context["environment"] = v
            break
    return system, context


def _all_systems(descriptor_text: str) -> list:
    """Every distinct system keyword present, in vocab order — so one item that
    names several systems ("OpenAI and Azure keys") yields one ref per system."""
    low = (descriptor_text or "").lower()
    out: list = []
    for k, v in _SYSTEM_KEYWORDS.items():
        if k in low and v not in out:
            out.append(v)
    return out


def _infer_credential_type(descriptor_text: str) -> Optional[str]:
    """Map a descriptor onto the store taxonomy, or None when too generic to
    constrain on (see _CONNECTION_RE / _APIKEY_RE rationale above)."""
    if _CONNECTION_RE.search(descriptor_text or ""):
        return "connection_string"
    if _APIKEY_RE.search(descriptor_text or ""):
        return "api_key"
    return None


def build_secure_references(masked_text: str, credential_type_hint: Optional[str] = None) -> list:
    """
    Build structured SecureReference(s) from MASKED descriptor text (Phase 4
    "Descriptor Extraction"). Returns [] when no credential descriptor is present,
    so the caller can use a non-empty result as the credential gate.

    One ref per named system (shared environment + credential_type); a generic ref
    (system_name=None) when a credential is referenced but no system is named. The
    value is never read or required — only the surrounding masked words.
    """
    text = masked_text or ""
    if not _CREDENTIAL_DESCRIPTOR_RE.search(text):
        return []
    ctype = credential_type_hint or _infer_credential_type(text)
    _, context = infer_system_and_context(text)
    systems = _all_systems(text)
    if systems:
        return [
            {"credential_type": ctype, "system_name": s, "context": dict(context)}
            for s in systems
        ]
    return [{"credential_type": ctype, "system_name": None, "context": dict(context)}]


# ── Identity / key derivation ────────────────────────────────────────────────

def derive_credential_key(
    credential_type: str,
    system_name: Optional[str] = None,
    context: Optional[dict] = None,
    owner_token: Optional[str] = None,
) -> str:
    """
    Deterministic identity that groups every version of "the same credential"
    (mirrors knowledge_evolution_service.derive_knowledge_key). Built from whichever
    distinguishing dimensions are present. When no system is named, scope by
    owner/application/project so two unrelated unnamed secrets never merge.

    INVARIANT: identity is derived PURELY from metadata (credential_type /
    system_name / context / owner) and NEVER from the credential value. Rotating a
    value therefore keeps the same credential_key — a new CredentialVersion is
    created under the existing chain rather than forking a new identity. The value
    only ever influences `value_fingerprint`, which is used solely for
    duplicate/rotation detection (see upsert_credential), never for identity.
    """
    context = context or {}
    ctype = _norm(credential_type) or "secret"
    system = _norm(system_name)
    env = _norm(context.get("environment"))
    if system:
        parts = [system, env, ctype]
    else:
        disc = _norm(context.get("application") or context.get("project") or owner_token)
        parts = [ctype, env, disc] if disc else [ctype, env]
    return ":".join(p for p in parts if p)


# ── Version helpers ──────────────────────────────────────────────────────────

def _active_version(db: Session, cred: SecureCredential) -> Optional[CredentialVersion]:
    if cred.active_version_id:
        v = db.query(CredentialVersion).filter(CredentialVersion.id == cred.active_version_id).first()
        if v is not None:
            return v
    return (
        db.query(CredentialVersion)
        .filter(CredentialVersion.credential_id == cred.id, CredentialVersion.is_active.is_(True))
        .order_by(CredentialVersion.version.desc())
        .first()
    )


def _insert_version(db: Session, cred: SecureCredential, value: str, md: dict, version: int, now) -> CredentialVersion:
    ciphertext, key_version = VaultService.encrypt_value(value)
    ver = CredentialVersion(
        credential_id=cred.id,
        ciphertext=ciphertext,
        key_version=key_version,
        value_fingerprint=VaultService.fingerprint(value),
        version=version,
        is_active=True,
        valid_from=now,
        valid_to=None,
        source_type=md.get("source_type"),
        source_id=md.get("source_id"),
        source_metadata=md.get("source_metadata") or {},
        created_at=now,
        last_seen_at=now,
    )
    db.add(ver)
    db.flush()
    return ver


def _audit(db: Session, event_type: str, cred: SecureCredential, ver: CredentialVersion, md: dict) -> None:
    """Inline audit write (committed by the caller — same pattern as
    draft_generation_service._audit). Never records the plaintext value."""
    db.add(AuditEvent(
        event_type=event_type,
        actor=md.get("actor") or "system",
        source_id=md.get("source_id"),
        resource_type="SecureCredential",
        resource_id=cred.id,
        outcome="SUCCESS",
        event_data={
            "credential_key": cred.credential_key,
            "credential_type": cred.credential_type,
            "version": ver.version,
            "key_version": ver.key_version,
        },
    ))
    logger.info(
        "[CREDTRACE] Audit -> %s written for credential_id=%s key=%s v%s "
        "(flushed with caller's commit)",
        event_type, cred.id, cred.credential_key, ver.version,
    )


# ── Write path: sync / rotation ──────────────────────────────────────────────

def upsert_credential(value: str, metadata: dict, db: Session) -> CredentialVersion:
    """
    Encrypt + version + rotate a discovered credential. Returns the active
    CredentialVersion. Rotation logic mirrors knowledge_evolution supersede/duplicate:

      - same credential_key + same value → no-op (bump last_seen_at).
      - same credential_key + new value  → deactivate prior, insert version+1 active.
      - new credential_key               → new SecureCredential + version 1.

    metadata keys: credential_type (required), system_name, context (dict),
    owner_token, source_type, source_id, source_metadata (dict), optional
    credential_key (override) and actor.
    """
    if not value:
        raise ValueError("upsert_credential: value must be non-empty")

    md = metadata or {}
    credential_type = md.get("credential_type") or "secret"
    system_name = md.get("system_name")
    context = md.get("context") or {}
    owner_token = md.get("owner_token")
    # Identity is metadata-only (never the value) — so a rotation reuses this key.
    key = md.get("credential_key") or derive_credential_key(credential_type, system_name, context, owner_token)
    now = utcnow()
    logger.info(
        "[CREDTRACE] Upsert -> ENTERED credential_key=%s credential_type=%s system_name=%s",
        key, credential_type, system_name,
    )

    cred = db.query(SecureCredential).filter(SecureCredential.credential_key == key).first()

    # ── new credential chain ────────────────────────────────────────────────
    if cred is None:
        cred = SecureCredential(
            credential_key=key,
            credential_type=credential_type,
            system_name=system_name,
            context=context,
            owner_token=owner_token,
            created_at=now,
            last_seen_at=now,
        )
        db.add(cred)
        db.flush()
        logger.info("[CREDTRACE] DB -> SecureCredential INSERTED id=%s key=%s", cred.id, key)
        ver = _insert_version(db, cred, value, md, version=1, now=now)
        logger.info(
            "[CREDTRACE] DB -> CredentialVersion INSERTED id=%s version=%s credential_id=%s",
            ver.id, ver.version, cred.id,
        )
        cred.active_version_id = ver.id
        db.flush()
        logger.info("[CREDTRACE] DB -> active_version_id UPDATED to %s (cred id=%s)", ver.id, cred.id)
        _audit(db, "CREDENTIAL_STORED", cred, ver, md)
        logger.info("[CREDTRACE] Upsert -> NEW credential chain key=%s v1 id=%s (flushed, not yet committed)", key, cred.id)
        return ver

    active = _active_version(db, cred)

    # ── unchanged value → duplicate confirmation (no new row) ───────────────
    # value_fingerprint is used ONLY here, to tell "same value" from "rotated" —
    # it plays no part in identity (the key was already derived above).
    if active is not None and active.value_fingerprint == VaultService.fingerprint(value):
        cred.last_seen_at = now
        active.last_seen_at = now
        # merge any newly-learned provenance without touching the ciphertext
        if md.get("source_metadata"):
            merged = dict(active.source_metadata or {})
            merged.update(md["source_metadata"])
            active.source_metadata = merged
        db.flush()
        logger.info(
            "[CREDTRACE] Upsert -> DUPLICATE (unchanged value) key=%s v%s (last_seen bumped, "
            "no new row)", key, active.version,
        )
        return active

    # ── rotation → deactivate prior, insert version+1 active ────────────────
    prev_version = active.version if active else 0
    if active is not None:
        active.is_active = False
        active.valid_to = now
        db.flush()
    ver = _insert_version(db, cred, value, md, version=prev_version + 1, now=now)
    logger.info(
        "[CREDTRACE] DB -> CredentialVersion INSERTED (rotation) id=%s version=%s credential_id=%s",
        ver.id, ver.version, cred.id,
    )
    cred.active_version_id = ver.id
    cred.last_seen_at = now
    if system_name and not cred.system_name:
        cred.system_name = system_name
    db.flush()
    logger.info("[CREDTRACE] DB -> active_version_id UPDATED to %s (cred id=%s)", ver.id, cred.id)
    _audit(db, "CREDENTIAL_ROTATED", cred, ver, md)
    logger.info("[CREDTRACE] Upsert -> ROTATION key=%s v%s->v%s (flushed, not yet committed)", key, prev_version, ver.version)
    return ver


# ── Read path: structured resolution ─────────────────────────────────────────

def _score(cred: SecureCredential, ctype: str, system: str, ref_ctx: dict) -> float:
    """Heuristic match score of an active credential against a SecureReference.
    System name dominates; context dimensions and type refine."""
    score = 0.0
    cred_system = _norm(cred.system_name)
    if system:
        if cred_system == system:
            score += 0.6
        elif cred_system and (system in cred_system or cred_system in system):
            score += 0.4
        else:
            return 0.0  # a system was requested and this credential is a different one
    cred_ctx = {k: _norm(v) for k, v in (cred.context or {}).items() if v}
    if ref_ctx:
        matched = sum(1 for k, v in ref_ctx.items() if cred_ctx.get(k) == v)
        score += 0.4 * (matched / len(ref_ctx))
    if ctype and _norm(cred.credential_type) == ctype:
        score += 0.2
    return score


def _confidence(scored: list) -> float:
    """0.0 when nothing matches. A single strong match → high confidence. A
    near-tie between the top two → low confidence (ambiguous → clarify)."""
    if not scored:
        return 0.0
    top = min(scored[0][0], 1.0)
    if len(scored) == 1:
        return round(top, 3)
    gap = scored[0][0] - scored[1][0]
    return round(min(top, 0.5 + gap), 3)


def _match_dto(cred: SecureCredential) -> dict:
    """Masked-safe candidate descriptor — labels/metadata only, never a value."""
    return {
        "credential_key": cred.credential_key,
        "credential_type": cred.credential_type,
        "system_name": cred.system_name,
        "context": cred.context or {},
        "active_version_id": cred.active_version_id,
    }


def resolve_secure_reference(secure_reference: dict, db: Session) -> dict:
    """
    Generalized structured resolver (no exact-label match required). Accepts a
    SecureReference {credential_type, system_name, context{...}} and scores the
    active credential set by matching dimensions. Returns:

        {"matches": [<masked descriptor>, ...], "confidence": float}

    The caller decides (per SECURE_RESOLUTION_THRESHOLD): high confidence → auto
    resolve the latest active version; low/multiple → clarification; zero → notice.
    NEVER returns a decrypted value.
    """
    ref = secure_reference or {}
    ctype = _norm(ref.get("credential_type"))
    system = _norm(ref.get("system_name"))
    ref_ctx = {k: _norm(v) for k, v in (ref.get("context") or {}).items() if v}

    q = db.query(SecureCredential)
    if ctype:
        q = q.filter(SecureCredential.credential_type == ctype)

    scored = []
    for cred in q.all():
        s = _score(cred, ctype, system, ref_ctx)
        if s > 0:
            scored.append((s, cred))
    scored.sort(key=lambda t: t[0], reverse=True)

    return {"matches": [_match_dto(c) for _, c in scored], "confidence": _confidence(scored)}


def get_active(credential_key: str, db: Session) -> Optional[CredentialVersion]:
    """The latest active CredentialVersion for a key, or None."""
    cred = db.query(SecureCredential).filter(SecureCredential.credential_key == credential_key).first()
    return _active_version(db, cred) if cred is not None else None


def decrypt_credential(credential_key: str, justification: str, actor: str, agent_action_id: int, db: Session) -> str:
    """
    Authorized unmask of a credential's LATEST ACTIVE value (rotation-safe — never
    a historical version). Delegates to VaultService.decrypt_ciphertext, which
    enforces the approved-AgentAction + justification gate and writes the
    CREDENTIAL_REVEAL audit. Plaintext is for in-memory use only.
    """
    cred = db.query(SecureCredential).filter(SecureCredential.credential_key == credential_key).first()
    if cred is None:
        raise KeyError(f"SecureCredential '{credential_key}' not found")
    ver = _active_version(db, cred)
    if ver is None:
        raise KeyError(f"No active version for credential '{credential_key}'")
    return VaultService.decrypt_ciphertext(
        ver.ciphertext,
        ver.key_version,
        justification,
        actor,
        agent_action_id,
        db,
        audit_event="CREDENTIAL_REVEAL",
        source_id=ver.source_id,
        label=credential_key,
    )
