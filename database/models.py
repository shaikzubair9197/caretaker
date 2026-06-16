from sqlalchemy import DateTime, JSON, ForeignKey, BigInteger, Boolean, LargeBinary, Numeric
from sqlalchemy import UniqueConstraint
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
    # "panic_extract" | "intent_reason"

    prompt_sha256 = Column(String, nullable=False)
    # SHA-256 of the full prompt — hash only, never the prompt text

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
    user_id             = Column(Integer, default=1)
    created_at          = Column(DateTime, default=utcnow)


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


class KnowledgeItem(Base):
    """Structured knowledge extracted from any Graph source."""

    __tablename__ = "knowledge_items"

    id              = Column(Integer, primary_key=True)
    source_id       = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    knowledge_type  = Column(String(30), nullable=False)
    # task|commitment|decision|risk|escalation|reminder|approval|alert
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
    user_id         = Column(Integer, default=1)
    created_at      = Column(DateTime, default=utcnow)
    resolved_at     = Column(DateTime, nullable=True)


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
