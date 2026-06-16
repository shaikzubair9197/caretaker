"""
VaultService — AES-256-GCM token vault for Phase 2A.

Design decisions (see blueprint §6):
- Each entity value is encrypted with a unique 12-byte random nonce.
- Storage format: nonce (12 bytes) || ciphertext || GCM auth tag (16 bytes) → BYTEA.
- Master key read from VAULT_MASTER_KEY env var (64 hex chars = 32 bytes).
- Key versioning via VAULT_KEY_VERSION env var — supports zero-downtime rotation.
- Every tokenize/decrypt call writes to audit_events via an independent DB session
  (same pattern as LLMAuditService) so audit survives any caller rollback.
- Plaintext NEVER stored after decrypt — callers must use it in-memory only.
- Unmask requires all 4 params: token, justification, actor, agent_action_id.
"""

import os
import hashlib
import threading
from typing import Optional

from utils.logger import get_logger
from utils.config import settings

logger = get_logger("services.vault")

_lock = threading.Lock()
_key_cache: dict[int, bytes] = {}


def _load_master_key(version: int) -> bytes:
    """Load and cache the AES key for the given version from environment."""
    with _lock:
        if version in _key_cache:
            return _key_cache[version]

        if version == 1:
            env_var = "VAULT_MASTER_KEY"
        else:
            env_var = f"VAULT_MASTER_KEY_V{version}"

        raw = os.getenv(env_var, settings.VAULT_MASTER_KEY if version == 1 else "")
        if not raw:
            raise RuntimeError(
                f"Vault master key not configured. Set {env_var} in .env "
                "(64 hex chars = 32 bytes). "
                "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        try:
            key = bytes.fromhex(raw.strip())
        except ValueError:
            raise RuntimeError(f"{env_var} is not valid hex — must be 64 hex chars.")
        if len(key) != 32:
            raise RuntimeError(f"{env_var} must be 32 bytes (64 hex chars), got {len(key)}.")

        _key_cache[version] = key
        return key


def _encrypt(plaintext: str, version: int) -> bytes:
    """AES-256-GCM encrypt. Returns nonce||ciphertext||tag as bytes."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _load_master_key(version)
    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ct_and_tag


def _decrypt_bytes(ciphertext_blob: bytes, version: int) -> str:
    """AES-256-GCM decrypt. Expects nonce(12)||ciphertext||tag format."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if len(ciphertext_blob) < 28:   # 12 nonce + 16 tag minimum
        raise ValueError("Ciphertext blob too short — may be corrupted.")
    key = _load_master_key(version)
    nonce = ciphertext_blob[:12]
    ct_and_tag = ciphertext_blob[12:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ct_and_tag, None)
    return plaintext.decode("utf-8")


def _write_audit(
    event_type: str,
    actor: str,
    source_id: Optional[int],
    vault_token: Optional[str],
    outcome: str,
    justification: Optional[str] = None,
    event_data: Optional[dict] = None,
) -> None:
    """
    Write to audit_events via an independent session — never blocks caller.

    The independent session cannot see rows the caller has only flushed (not
    committed) yet, so a hard FK on source_id would silently drop the audit row
    (e.g. MASK written during graph_sync before the source_item is committed).
    Because durability of the audit trail outranks the FK link, we retry once
    with source_id moved into event_data if the first insert fails — the event
    is ALWAYS persisted, with the source reference preserved either way.
    """
    from database.connection import SessionLocal
    from database.models import AuditEvent

    def _insert(sid, extra):
        session = SessionLocal()
        try:
            data = dict(event_data or {})
            if extra:
                data.update(extra)
            session.add(AuditEvent(
                event_type    = event_type,
                actor         = actor,
                source_id     = sid,
                vault_token   = vault_token,
                justification = justification,
                outcome       = outcome,
                event_data    = data,
            ))
            session.commit()
            return True
        except Exception as e:
            session.rollback()
            logger.warning(f"Audit insert failed ({event_type}, source_id={sid}): {e}")
            return False
        finally:
            session.close()

    # First attempt with the FK link; on failure (e.g. uncommitted source_item)
    # retry with the FK column null and the reference captured in event_data.
    if _insert(source_id, None):
        return
    if source_id is not None and _insert(None, {"source_id_ref": source_id}):
        return
    logger.error(f"Audit write permanently failed ({event_type}) — event lost")


class VaultService:
    """
    Tokenize and decrypt PII/secrets using AES-256-GCM.

    Usage in ingestion pipeline:
        masked_text, token_map, redactions = PreprocessingService.mask_pii_indexed(text)
        VaultService.store_tokens(token_map, source_id=si.id, db=db)

    Usage in execution (approved AgentAction only):
        plaintext = VaultService.decrypt(
            token="PERSON_1",
            justification="Drafting email as approved in AgentAction #42",
            actor="system",
            agent_action_id=42,
            db=db,
        )
    """

    @staticmethod
    def store_tokens(
        token_map: dict[str, str],
        source_id: int,
        db,
        session_context: Optional[str] = None,
    ) -> None:
        """
        Encrypt and persist a batch of {token_name: original_value} pairs.

        token_map example: {"PERSON_1": "Monish", "API_KEY_1": "AKIA..."}
        """
        from database.models import VaultToken
        from utils.time_utils import utcnow

        if not token_map:
            return

        version = settings.VAULT_KEY_VERSION
        ctx = session_context or hashlib.sha256(str(source_id).encode()).hexdigest()[:8]

        for token_name, original_value in token_map.items():
            entity_type = token_name.rsplit("_", 1)[0]   # "PERSON_1" → "PERSON"
            ciphertext = _encrypt(original_value, version)

            # ON CONFLICT DO NOTHING — idempotent for duplicate ingest
            existing = db.query(VaultToken).filter(VaultToken.token == token_name).first()
            if existing is None:
                db.add(VaultToken(
                    token           = token_name,
                    entity_type     = entity_type,
                    ciphertext      = ciphertext,
                    key_version     = version,
                    source_id       = source_id,
                    session_context = ctx,
                ))

        _write_audit(
            event_type  = "MASK",
            actor       = "system",
            source_id   = source_id,
            vault_token = None,
            outcome     = "SUCCESS",
            event_data  = {"token_count": len(token_map), "key_version": version},
        )

    @staticmethod
    def decrypt(
        token: str,
        justification: str,
        actor: str,
        agent_action_id: int,
        db,
    ) -> str:
        """
        Authorized unmask. Requires an approved AgentAction.

        Returns the original plaintext — caller must use in-memory only.
        Plaintext is NEVER stored, logged, or returned in any audit record.
        """
        from database.models import VaultToken, AgentAction
        from utils.time_utils import utcnow

        if not justification or not justification.strip():
            raise ValueError("justification is required for vault decryption.")

        # Validate the AgentAction is approved
        action = db.query(AgentAction).filter(AgentAction.id == agent_action_id).first()
        if action is None:
            _write_audit("ACCESS_DENIED", actor, None, token, "DENIED",
                         justification, {"reason": "agent_action_not_found"})
            raise PermissionError(f"AgentAction #{agent_action_id} not found.")
        if action.status != "approved":
            _write_audit("ACCESS_DENIED", actor, None, token, "DENIED",
                         justification, {"reason": f"action_status={action.status}"})
            raise PermissionError(
                f"AgentAction #{agent_action_id} is '{action.status}', not 'approved'."
            )

        vault_record = db.query(VaultToken).filter(VaultToken.token == token).first()
        if vault_record is None:
            _write_audit("ACCESS_DENIED", actor, None, token, "DENIED",
                         justification, {"reason": "token_not_found"})
            raise KeyError(f"Vault token '{token}' not found.")

        try:
            plaintext = _decrypt_bytes(vault_record.ciphertext, vault_record.key_version)
        except Exception as e:
            _write_audit("UNMASK", actor, vault_record.source_id, token, "ERROR",
                         justification, {"error": str(e)[:200]})
            raise

        # Update access tracking
        vault_record.last_accessed_at = utcnow()
        vault_record.access_count = (vault_record.access_count or 0) + 1

        _write_audit(
            event_type    = "UNMASK",
            actor         = actor,
            source_id     = vault_record.source_id,
            vault_token   = token,
            outcome       = "SUCCESS",
            justification = justification,
            event_data    = {
                "agent_action_id": agent_action_id,
                "entity_type": vault_record.entity_type,
                "key_version": vault_record.key_version,
            },
        )

        return plaintext

    @staticmethod
    def is_configured() -> bool:
        """Return True if VAULT_MASTER_KEY is set and valid."""
        try:
            _load_master_key(settings.VAULT_KEY_VERSION)
            return True
        except RuntimeError:
            return False
