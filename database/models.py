from sqlalchemy import DateTime, JSON, ForeignKey, BigInteger, Boolean, LargeBinary, Numeric
from sqlalchemy import UniqueConstraint, Index, text
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import Column, Integer, String, Text

from utils.time_utils import utcnow


class Base(DeclarativeBase):
    pass


class SourceItem(Base):
    """Raw source archive — every ingested piece of text lives here first."""

    __tablename__ = "source_items"

    id = Column(Integer, primary_key=True, index=True)

    source_type = Column(String, nullable=False)
    # panic_dump | outlook_email | teams_chat | calendar | voice_note | local_note

    raw_text = Column(Text, nullable=False)

    metadata_ = Column("metadata", JSON, nullable=True)
    # arbitrary source-specific fields (sender, subject, meeting_id, etc.)

    sensitivity_label = Column(String, default="PUBLIC")
    # PUBLIC | INTERNAL | CONFIDENTIAL | RESTRICTED

    noise_removed = Column(Text, nullable=True)
    # cleaned version after noise filtering (preserved for audit)

    masked_text = Column(Text, nullable=True)
    # version with secrets replaced by [REDACTED:TYPE]

    created_at = Column(DateTime, default=utcnow)

    user_id = Column(Integer, default=1)


class Task(Base):

    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True)

    description = Column(String, nullable=False)

    priority = Column(String, default="medium")

    status = Column(String, default="pending")

    source_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)

    user_id = Column(Integer, default=1)

    __table_args__ = (
        # Scope uniqueness per user — a global unique on description alone would
        # collide across users and forbid legitimate recurring tasks.
        UniqueConstraint("user_id", "description", name="uq_task_description"),
    )


class ActiveWindow(Base):

    __tablename__ = "active_windows"

    id = Column(Integer, primary_key=True, index=True)

    window_title = Column(Text, nullable=False)

    started_at = Column(DateTime, nullable=False)

    ended_at = Column(DateTime, nullable=True)

    duration_seconds = Column(Integer, nullable=True)

    created_at = Column(DateTime)

    user_id = Column(Integer, default=1)


class Memory(Base):

    __tablename__ = "memories"

    id = Column(Integer, primary_key=True, index=True)

    text = Column(Text, nullable=False)

    type = Column(String, default="general")

    importance = Column(Integer, default=1)

    embedding = Column(JSON, nullable=True)
    # float list (384 dims for all-MiniLM-L6-v2) stored as JSON array
    # similarity search done in Python — no pgvector extension required

    source_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)

    user_id = Column(Integer, default=1)

    __table_args__ = (
        # Scope uniqueness per user — a global unique on text alone would
        # collide across users (see uq_task_description).
        UniqueConstraint("user_id", "text", name="uq_memory_text"),
    )


class Commitment(Base):

    __tablename__ = "commitments"

    id = Column(Integer, primary_key=True, index=True)

    raw_text = Column(Text, nullable=False)

    person = Column(String, nullable=True)

    action = Column(Text, nullable=False)

    commitment_type = Column(String, default="task")
    # task | email | reminder | follow_up

    status = Column(String, default="pending")
    # pending | drafted | done | dismissed

    due_date = Column(DateTime, nullable=True)

    # ── Reminder auto-scheduling (Phase 6) ────────────────────────────────────
    # Self-contained reminder window snapshotted onto the Commitment at creation:
    # remind_at = due_date - remind_offset_minutes. Once set, the reminder daemon
    # works EXCLUSIVELY from these columns and never reads back the originating
    # KnowledgeItem (the execution layer is independent of the extraction layer).
    remind_at = Column(DateTime, nullable=True)
    remind_offset_minutes = Column(Integer, nullable=True)
    reminded_at = Column(DateTime, nullable=True)   # fire-once stamp; NULL = not yet fired

    source_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)

    user_id = Column(Integer, default=1)

    created_at = Column(DateTime, default=utcnow)


class AgentAction(Base):

    __tablename__ = "agent_actions"

    id = Column(Integer, primary_key=True)

    action_type = Column(String)
    # email_draft | reminder | task_create | commitment_followup

    payload = Column(JSON, nullable=True)
    # structured dict — replaces old Text JSON string

    status = Column(String, default="pending")
    # pending | approved | executed | dismissed

    approved_at = Column(DateTime, nullable=True)

    user_id = Column(Integer, default=1)

    created_at = Column(DateTime, default=utcnow)


class LLMCallLog(Base):
    """Audit trail for every call made to the local LLM."""

    __tablename__ = "llm_call_logs"

    id = Column(Integer, primary_key=True)

    model = Column(String, nullable=False)
    call_type = Column(String, nullable=False)
    # "panic_extract" | "intent_reason" | "meeting_intelligence_extract"

    prompt_sha256 = Column(String, nullable=False)
    # SHA-256 of the full prompt — hash only, never the prompt text

    prompt_version = Column(String, nullable=True)
    # version tag of the prompt template used, e.g. "meeting_intelligence_extract.v1"

    extraction_version = Column(String, nullable=True)
    # version tag of the extraction logic/schema, for future reprocessing/auditing

    source_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    # links this call back to the SourceItem it was extracted from (e.g. a transcript)

    entity_types = Column(JSON, nullable=True)
    # PII categories present: ["EMAIL_ADDRESS", "PERSON"]

    injection_warnings = Column(JSON, nullable=True)
    # injection patterns neutralised: ["instruction_override"]

    duration_ms = Column(Integer, nullable=True)

    status = Column(String, nullable=False)
    # LLMStatus.*: SUCCESS | CONNECTION_REFUSED | DNS_FAILURE | TIMEOUT |
    # MODEL_NOT_FOUND | HTTP_ERROR | JSON_PARSE_ERROR |
    # SCHEMA_VALIDATION_FAILURE | UNKNOWN_ERROR

    fallback_reason = Column(String, nullable=True)

    # ── Layer 6: exception capture ──────────────────────────────────────
    exception_type    = Column(String, nullable=True)
    # e.g. "httpx.ConnectError", "json.JSONDecodeError"

    exception_message = Column(Text, nullable=True)
    # truncated to 800 chars — never contains raw PII

    # ── Layer 4: request audit ──────────────────────────────────────────
    prompt_preview    = Column(String(300), nullable=True)
    # first 250 chars of the masked user text — for debugging, no raw PII

    prompt_size_chars = Column(Integer, nullable=True)
    # total character count of the cleaned+masked prompt sent to the LLM

    preflight_data    = Column(JSON, nullable=True)
    # populated only when a ConnectError fires:
    # {"ollama_reachable": bool, "model_available": bool, "installed_models": [...]}

    user_id = Column(Integer, default=1)

    created_at = Column(DateTime, default=utcnow)


# ── Phase 2A: Graph source tables ─────────────────────────────────────────────

class Email(Base):
    """Normalized Outlook email — linked to SourceItem for raw archive."""

    __tablename__ = "emails"

    id                  = Column(Integer, primary_key=True)
    source_id           = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id         = Column(String(512), nullable=False, unique=True)
    internet_message_id = Column(String(512), nullable=True)
    subject_masked      = Column(Text, nullable=True)
    from_token          = Column(String(64), nullable=True)
    from_name_token     = Column(String(64), nullable=True)
    to_tokens           = Column(JSON, default=list)
    cc_tokens           = Column(JSON, default=list)
    body_size_chars     = Column(Integer, nullable=True)
    has_attachments     = Column(Boolean, default=False)
    importance          = Column(String(20), default="normal")
    received_at         = Column(DateTime, nullable=False)
    thread_id           = Column(String(512), nullable=True)
    folder              = Column(String(100), default="inbox")
    sensitivity_label   = Column(String(20), default="INTERNAL")
    threat_score        = Column(Numeric(4, 3), nullable=True)
    is_quarantined      = Column(Boolean, default=False)
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


class TeamsMessage(Base):
    """Teams chat and channel message archive."""

    __tablename__ = "teams_messages"

    id                  = Column(Integer, primary_key=True)
    source_id           = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id         = Column(String(512), nullable=False, unique=True)
    chat_id             = Column(String(512), nullable=True)
    channel_id          = Column(String(512), nullable=True)
    team_id             = Column(String(512), nullable=True)
    message_type        = Column(String(50), default="message")
    from_token          = Column(String(64), nullable=True)
    from_name_token     = Column(String(64), nullable=True)
    body_masked         = Column(Text, nullable=True)
    mentions_tokens     = Column(JSON, default=list)
    importance          = Column(String(20), default="normal")
    is_channel_message  = Column(Boolean, default=False)
    thread_id           = Column(String(512), nullable=True)
    reply_to_id         = Column(String(512), nullable=True)
    has_attachments     = Column(Boolean, default=False)
    sensitivity_label   = Column(String(20), nullable=True)
    threat_score        = Column(Numeric(4, 3), nullable=True)
    is_quarantined      = Column(Boolean, default=False)
    sent_at             = Column(DateTime, nullable=False)
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


class MeetingTranscript(Base):
    """Teams meeting transcripts with masked content."""

    __tablename__ = "meeting_transcripts"

    id                  = Column(Integer, primary_key=True)
    source_id           = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id         = Column(String(512), nullable=False, unique=True)
    meeting_id          = Column(String(512), nullable=False)
    subject_masked      = Column(Text, nullable=True)
    participant_tokens  = Column(JSON, default=list)
    duration_seconds    = Column(Integer, nullable=True)
    segment_count       = Column(Integer, nullable=True)
    word_count          = Column(Integer, nullable=True)
    masked_content      = Column(Text, nullable=True)
    sensitivity_label   = Column(String(20), nullable=True)
    threat_score        = Column(Numeric(4, 3), nullable=True)
    meeting_start       = Column(DateTime, nullable=True)
    meeting_end         = Column(DateTime, nullable=True)
    organizer_token     = Column(String(64), nullable=True)
    self_token          = Column(String(64), nullable=True)
    # scoped speaker token of the caretaker user (participant whose email matches
    # settings.SENDER_IDENTITY), or NULL if the user was not in the meeting. Used by
    # the Follow-up Center to classify Action Items (assigned to self) vs Commitments.
    content_hash        = Column(String(64), nullable=True)
    # SHA-256 of the raw archived payload — used for idempotent re-ingestion checks
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


class TranscriptSegment(Base):
    """Per-utterance breakdown of a meeting transcript, for speaker/timestamp attribution."""

    __tablename__ = "transcript_segments"

    id              = Column(Integer, primary_key=True)
    transcript_id   = Column(Integer, ForeignKey("meeting_transcripts.id", ondelete="CASCADE"), nullable=False)
    speaker_token   = Column(String(64), nullable=True)
    start_ms        = Column(Integer, nullable=True)
    end_ms          = Column(Integer, nullable=True)
    text_masked     = Column(Text, nullable=True)
    sequence_index  = Column(Integer, nullable=False)
    created_at      = Column(DateTime, default=utcnow)


class CalendarEvent(Base):
    """Outlook Calendar events."""

    __tablename__ = "calendar_events"

    id                  = Column(Integer, primary_key=True)
    source_id           = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id         = Column(String(512), nullable=False, unique=True)
    subject_masked      = Column(Text, nullable=True)
    organizer_token     = Column(String(64), nullable=True)
    attendee_tokens     = Column(JSON, default=list)
    start_at            = Column(DateTime, nullable=False)
    end_at              = Column(DateTime, nullable=False)
    is_online_meeting   = Column(Boolean, default=False)
    meeting_url         = Column(Text, nullable=True)
    body_masked         = Column(Text, nullable=True)
    importance          = Column(String(20), default="normal")
    sensitivity_label   = Column(String(20), nullable=True)
    recurrence_pattern  = Column(String(50), nullable=True)
    is_cancelled        = Column(Boolean, default=False)
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


class TodoTask(Base):
    """Microsoft To Do tasks."""

    __tablename__ = "todo_tasks"

    id                  = Column(Integer, primary_key=True)
    source_id           = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id         = Column(String(512), nullable=False, unique=True)
    list_id             = Column(String(512), nullable=True)
    title_masked        = Column(Text, nullable=True)
    body_masked         = Column(Text, nullable=True)
    status              = Column(String(30), default="notStarted")
    importance          = Column(String(20), default="normal")
    due_at              = Column(DateTime, nullable=True)
    completed_at        = Column(DateTime, nullable=True)
    sensitivity_label   = Column(String(20), nullable=True)
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


class OneDriveDocument(Base):
    """OneDrive / SharePoint file metadata (no content download)."""

    __tablename__ = "onedrive_documents"

    id                   = Column(Integer, primary_key=True)
    source_id            = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    external_id          = Column(String(512), nullable=False, unique=True)
    name_masked          = Column(Text, nullable=True)
    file_extension       = Column(String(20), nullable=True)
    size_bytes           = Column(BigInteger, nullable=True)
    owner_token          = Column(String(64), nullable=True)
    modifier_token       = Column(String(64), nullable=True)
    parent_path          = Column(Text, nullable=True)
    sensitivity_label    = Column(String(20), nullable=True)
    ms_sensitivity_label = Column(String(100), nullable=True)
    is_shared            = Column(Boolean, default=False)
    share_scope          = Column(String(50), nullable=True)
    last_modified_at     = Column(DateTime, nullable=True)
    user_id              = Column(Integer, default=1)
    created_at           = Column(DateTime, default=utcnow)


# ── Phase 2A: Processing / vault tables ───────────────────────────────────────

class VaultToken(Base):
    """Bidirectional mapping: stable indexed token ↔ AES-256-GCM ciphertext."""

    __tablename__ = "vault_tokens"

    id               = Column(Integer, primary_key=True)
    token            = Column(String(64), nullable=False, unique=True)
    entity_type      = Column(String(50), nullable=False)
    ciphertext       = Column(LargeBinary, nullable=False)   # nonce(12)||ciphertext||tag(16)
    key_version      = Column(Integer, nullable=False, default=1)
    source_id        = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=True)
    session_context  = Column(String(64), nullable=True)
    last_accessed_at = Column(DateTime, nullable=True)
    access_count     = Column(Integer, default=0)
    created_at       = Column(DateTime, default=utcnow)


class SecureCredential(Base):
    """A discovered confidential value of ANY credential type, versioned with
    rotation history. The single source of truth that Teams/Email/manual sources
    sync into; draft generation, reveal and send resolve only against this store
    (Follow-up Center plan — Phase 1). The plaintext value never lives here — only
    on CredentialVersion.ciphertext (AES-256-GCM)."""

    __tablename__ = "secure_credentials"

    id                = Column(Integer, primary_key=True)
    credential_key    = Column(String(256), nullable=False, unique=True, index=True)
    # deterministic identity grouping every version of "the same credential" —
    # see secure_store_service.derive_credential_key.
    credential_type   = Column(String(40), nullable=False)
    # open taxonomy: api_key|password|certificate|ssh_key|jwt|oauth_token|
    # client_secret|db_credential|connection_string|license_key|... (extensible)
    system_name       = Column(String(128), nullable=True)
    context           = Column(JSON, default=dict)
    # flexible dimension map: environment/project/application/region/customer/...
    owner_token       = Column(String(64), nullable=True)
    active_version_id = Column(Integer, nullable=True)
    # points at the current active CredentialVersion (app-maintained; no DB FK to
    # avoid a circular constraint with credential_versions.credential_id).
    created_at        = Column(DateTime, default=utcnow)
    last_seen_at      = Column(DateTime, default=utcnow)

    __table_args__ = (
        Index("ix_secure_credentials_type_system", "credential_type", "system_name"),
    )


class CredentialVersion(Base):
    """One encrypted value for a SecureCredential. Exactly one row per credential
    is active at a time; rotation deactivates the prior and inserts version+1
    (mirrors knowledge_evolution_service supersession). Plaintext exists only in
    memory during an audited decrypt — never anywhere but `ciphertext`."""

    __tablename__ = "credential_versions"

    id              = Column(Integer, primary_key=True)
    credential_id   = Column(Integer, ForeignKey("secure_credentials.id", ondelete="CASCADE"), nullable=False, index=True)
    ciphertext      = Column(LargeBinary, nullable=False)   # nonce(12)||ciphertext||tag(16)
    key_version     = Column(Integer, nullable=False, default=1)
    value_fingerprint = Column(String(64), nullable=True, index=True)
    # keyed HMAC-SHA256 of the value (VaultService.fingerprint) — lets rotation
    # detection compare "same value vs new value" WITHOUT decrypting. Non-reversible.
    version         = Column(Integer, nullable=False, default=1)
    is_active       = Column(Boolean, default=True, index=True)
    valid_from      = Column(DateTime, nullable=True)
    valid_to        = Column(DateTime, nullable=True)
    source_type     = Column(String(50), nullable=True)
    source_id       = Column(Integer, ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True)
    source_metadata = Column(JSON, default=dict)
    # provenance: conversation_id/chat_id/team_id/channel_id/sender/source_url/message_id/...
    created_at      = Column(DateTime, default=utcnow)
    last_seen_at    = Column(DateTime, default=utcnow)

    __table_args__ = (
        # At most one active version per credential — DB-level backstop for the
        # service's transactional rotation (mirrors ux_knowledge_items_active_key).
        Index(
            "ux_credential_versions_active",
            "credential_id",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )


class KnowledgeItem(Base):
    """Structured knowledge extracted from any Graph source."""

    __tablename__ = "knowledge_items"

    id              = Column(Integer, primary_key=True)
    source_id       = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    knowledge_type  = Column(String(30), nullable=False)
    # task|commitment|decision|risk|escalation|reminder|approval|alert|
    # question|information_request|mentioned_document|mentioned_link|
    # system_reference|github_pr|jira_ticket|credential_reference
    title_masked    = Column(Text, nullable=False)
    detail_masked   = Column(Text, nullable=True)
    owner_token     = Column(String(64), nullable=True)
    due_at          = Column(DateTime, nullable=True)
    priority        = Column(String(20), default="medium")
    confidence      = Column(Numeric(4, 3), nullable=True)
    status          = Column(String(30), default="open")
    sensitivity_label = Column(String(20), nullable=True)
    embedding       = Column(JSON, nullable=True)
    source_type     = Column(String(50), nullable=True)
    vault_refs      = Column(JSON, default=list)
    extra_data      = Column(JSON, nullable=True)
    # type-specific fields, shape determined by knowledge_type — see
    # services/knowledge_persistence_service.py for the per-type schema
    llm_call_log_id = Column(Integer, ForeignKey("llm_call_logs.id"), nullable=True)
    # links this item back to the single extraction call that produced it

    # ── Plan 2: knowledge retrieval, versioning & supersession ───────────────
    # All additive/nullable so existing Plan 1 rows are unaffected until the
    # backfill (migrations/005 + scripts/backfill_knowledge_versioning.py) runs.
    knowledge_key     = Column(String(128), nullable=True, index=True)
    # groups every version of "the same fact" — deterministic for structured
    # types, semantically/UUID-generated for free-text. See
    # services/knowledge_evolution_service.derive_knowledge_key.
    version           = Column(Integer, default=1)
    is_active         = Column(Boolean, default=True, index=True)
    # exactly one active row per knowledge_key — enforced by the partial unique
    # index ux_knowledge_items_active_key below + the evolution service's
    # transactional, row-locked supersession.
    superseded_by_id  = Column(Integer, ForeignKey("knowledge_items.id"), nullable=True)
    valid_from        = Column(DateTime, nullable=True)
    valid_to          = Column(DateTime, nullable=True)
    resolution_method = Column(String(30), nullable=True)
    # new_chain | supersedes | duplicate_confirmation | semantic | llm_adjudicated

    user_id         = Column(Integer, default=1)
    created_at      = Column(DateTime, default=utcnow)
    resolved_at     = Column(DateTime, nullable=True)

    __table_args__ = (
        # Retrieval hot paths: active-version lookup by key, and the structured
        # "same owner + same type" candidate scan used during identity resolution.
        Index("ix_knowledge_items_key_active", "knowledge_key", "is_active"),
        Index("ix_knowledge_items_type_owner", "knowledge_type", "owner_token"),
        # At most one active version per knowledge_key, enforced at the DB layer
        # independent of application logic (belt-and-suspenders for the
        # evolution service's transactional guarantee). NULL knowledge_keys are
        # distinct under a unique index, so un-backfilled/unkeyed rows coexist.
        Index(
            "ux_knowledge_items_active_key",
            "knowledge_key",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )


class ThreatAssessment(Base):
    """Per-source-item threat scoring result."""

    __tablename__ = "threat_assessments"

    id               = Column(Integer, primary_key=True)
    source_id        = Column(Integer, ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    threat_score     = Column(Numeric(4, 3), nullable=False)
    category         = Column(String(20), nullable=False)   # CLEAN|FLAGGED|QUARANTINE
    phishing_score   = Column(Numeric(4, 3), default=0)
    bec_score        = Column(Numeric(4, 3), default=0)
    credential_risk  = Column(Numeric(4, 3), default=0)
    urgency_score    = Column(Numeric(4, 3), default=0)
    injection_score  = Column(Numeric(4, 3), default=0)
    social_eng_score = Column(Numeric(4, 3), default=0)
    link_risk_score  = Column(Numeric(4, 3), default=0)
    flags            = Column(JSON, default=list)
    recommended_action = Column(String(100), nullable=True)
    notes            = Column(Text, nullable=True)
    assessed_at      = Column(DateTime, default=utcnow)
    reviewed_by      = Column(Integer, nullable=True)


class GraphSyncState(Base):
    """Delta query cursors for incremental Graph sync per source+user."""

    __tablename__ = "graph_sync_state"

    id             = Column(Integer, primary_key=True)
    source_type    = Column(String(50), nullable=False)
    user_upn       = Column(String(255), nullable=False)
    delta_token    = Column(Text, nullable=True)
    last_synced_at = Column(DateTime, nullable=True)
    items_synced   = Column(Integer, default=0)
    sync_errors    = Column(Integer, default=0)
    is_active      = Column(Boolean, default=True)

    __table_args__ = (
        UniqueConstraint("source_type", "user_upn", name="uq_sync_state_source_upn"),
    )


class AuditEvent(Base):
    """Immutable audit trail — 7-year legal hold. Never auto-deleted."""

    __tablename__ = "audit_events"

    id            = Column(BigInteger, primary_key=True, autoincrement=True)
    event_type    = Column(String(50), nullable=False)
    # INGEST|MASK|UNMASK|LLM_CALL|RETRIEVAL|EXPORT|ACCESS_DENIED
    actor         = Column(String(100), nullable=True)
    source_id     = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    vault_token   = Column(String(64), nullable=True)
    resource_type = Column(String(50), nullable=True)
    resource_id   = Column(Integer, nullable=True)
    justification = Column(Text, nullable=True)
    ip_address    = Column(String(45), nullable=True)
    request_id    = Column(String(64), nullable=True)
    outcome       = Column(String(20), default="SUCCESS")
    event_data    = Column(JSON, nullable=True)
    created_at    = Column(DateTime, default=utcnow)


# ── Content Retrieval layer (Document Retrieval plan — Phase 1) ───────────────

class IndexedContent(Base):
    """Catalog entry for one piece of content from any source (LOCAL folder first,
    cloud later). The extracted plain text is NEVER stored here — only on the
    on-disk cache (text_cache_path); the DB carries metadata, keywords, entities,
    embedding and content hashes (Document Retrieval plan, design rule #1).

    Two orthogonal lifecycles share this row (design rule #3):
      • work state  — index_status PENDING|INDEXING|INDEXED|ERROR (+ needs_reindex)
      • serve state — is_active AND index_status='INDEXED'
    A served (is_active, INDEXED) row may still carry needs_reindex=True; the
    worker writes version+1 and only supersedes the old row once the new one is
    INDEXED, so retrieval always has a row to serve (versioning mirrors
    KnowledgeItem supersession)."""

    __tablename__ = "indexed_content"

    id                = Column(Integer, primary_key=True)

    # ── Source identity (provider-agnostic) ──────────────────────────────────
    source_type       = Column(String(50), nullable=False, default="LOCAL")
    # LOCAL | SHAREPOINT | TEAMS | ... (open taxonomy; LOCAL is the only provider in Phase 1)
    source_identifier = Column(String(1024), nullable=False)
    # stable identity within a source — normalised absolute path (LOCAL) or item id (cloud)
    source_metadata   = Column(JSON, default=dict)
    # LOCAL {root, relative_path}; cloud {site_id, drive_id, item_id} (design rule #6)

    # ── File / display metadata ───────────────────────────────────────────────
    root_label        = Column(String(255), nullable=True)   # human label of the originating root
    filename          = Column(String(512), nullable=False)
    folder            = Column(String(512), nullable=True)   # immediate parent folder name (clustering signal)
    folder_path       = Column(Text, nullable=True)          # relative folder path from root (server-side only)
    extension         = Column(String(32), nullable=True)
    size_bytes        = Column(BigInteger, nullable=True)
    modified_at       = Column(DateTime, nullable=True)
    content_hash      = Column(String(64), nullable=True)    # SHA-256 of the raw bytes

    # ── Independent lifecycle versions (design rule #5) ───────────────────────
    parser_version    = Column(String(20), nullable=True)    # re-extract trigger
    pipeline_version  = Column(String(20), nullable=True)    # re-run keyword/entity/normalisation trigger
    text_cache_path   = Column(Text, nullable=True)          # relative path under CONTENT_TEXT_CACHE_DIR (server-side)
    keywords          = Column(JSON, default=list)
    entities          = Column(JSON, default=list)
    embedding         = Column(JSON, nullable=True)          # float list (e.g. 384-dim) — cosine in Python, no pgvector
    embedding_model   = Column(String(100), nullable=True)   # re-embed trigger
    embedding_dimension = Column(Integer, nullable=True)
    text_extracted_at = Column(DateTime, nullable=True)
    embedded_at       = Column(DateTime, nullable=True)
    indexed_at        = Column(DateTime, nullable=True)

    # ── Versioning / supersession (mirrors KnowledgeItem) ─────────────────────
    version           = Column(Integer, default=1)
    is_active         = Column(Boolean, default=True, index=True)
    valid_from        = Column(DateTime, nullable=True)
    valid_to          = Column(DateTime, nullable=True)
    superseded_by_id  = Column(Integer, ForeignKey("indexed_content.id"), nullable=True)

    # ── Work-queue state (orthogonal to serving — design rule #3) ─────────────
    index_status      = Column(String(20), nullable=False, default="PENDING")
    # PENDING | INDEXING | INDEXED | ERROR
    needs_reindex     = Column(Boolean, default=False)
    index_error       = Column(Text, nullable=True)
    retry_count       = Column(Integer, default=0)
    next_retry_at     = Column(DateTime, nullable=True)

    user_id           = Column(Integer, default=1)
    created_at        = Column(DateTime, default=utcnow)

    __table_args__ = (
        # Queue / serve hot paths. (is_active is indexed via Column(index=True).)
        Index("ix_indexed_content_status", "index_status"),
        Index("ix_indexed_content_needs_reindex", "needs_reindex"),
        Index("ix_indexed_content_folder", "folder"),
        Index("ix_indexed_content_parser_version", "parser_version"),
        # At most one ACTIVE row per (source_type, source_identifier). The worker
        # deactivates the prior version before activating version+1, so this is
        # never violated (belt-and-suspenders for the supersession transaction).
        # NULL is_active rows are excluded by the partial predicate.
        Index(
            "ux_indexed_content_active_source",
            "source_type",
            "source_identifier",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )


class ContentIndexState(Base):
    """Single-row catalog generation counter (design rule #14). The worker bumps
    `generation` on ANY catalog mutation; each API process caches the value and
    refreshes it every CONTENT_GENERATION_REFRESH_SECONDS so the retrieval cache
    key (query_hash + generation) self-invalidates. There is no state.json."""

    __tablename__ = "content_index_state"

    id         = Column(Integer, primary_key=True)
    generation = Column(BigInteger, nullable=False, default=0)
    updated_at = Column(DateTime, default=utcnow)
