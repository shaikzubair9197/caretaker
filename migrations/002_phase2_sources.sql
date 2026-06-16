-- Phase 2A — Microsoft Graph ingestion tables
-- Apply with: psql -U postgres -h localhost -d caretaker -f migrations/002_phase2_sources.sql

-- ── Source tables ─────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS emails (
    id                  SERIAL PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id         VARCHAR(512) NOT NULL UNIQUE,
    internet_message_id VARCHAR(512),
    subject_masked      TEXT,
    from_token          VARCHAR(64),
    from_name_token     VARCHAR(64),
    to_tokens           JSONB NOT NULL DEFAULT '[]',
    cc_tokens           JSONB NOT NULL DEFAULT '[]',
    body_size_chars     INTEGER,
    has_attachments     BOOLEAN NOT NULL DEFAULT false,
    importance          VARCHAR(20) NOT NULL DEFAULT 'normal',
    received_at         TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    thread_id           VARCHAR(512),
    folder              VARCHAR(100) NOT NULL DEFAULT 'inbox',
    sensitivity_label   VARCHAR(20) NOT NULL DEFAULT 'INTERNAL',
    threat_score        NUMERIC(4,3),
    is_quarantined      BOOLEAN NOT NULL DEFAULT false,
    user_id             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_emails_received_at   ON emails(received_at DESC);
CREATE INDEX IF NOT EXISTS idx_emails_thread        ON emails(thread_id);
CREATE INDEX IF NOT EXISTS idx_emails_quarantine    ON emails(is_quarantined) WHERE is_quarantined = true;
CREATE INDEX IF NOT EXISTS idx_emails_threat        ON emails(threat_score)   WHERE threat_score > 0.4;

-- ── Teams messages ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS teams_messages (
    id                  SERIAL PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id         VARCHAR(512) NOT NULL UNIQUE,
    chat_id             VARCHAR(512),
    channel_id          VARCHAR(512),
    team_id             VARCHAR(512),
    message_type        VARCHAR(50) NOT NULL DEFAULT 'message',
    from_token          VARCHAR(64),
    from_name_token     VARCHAR(64),
    body_masked         TEXT,
    mentions_tokens     JSONB NOT NULL DEFAULT '[]',
    importance          VARCHAR(20) NOT NULL DEFAULT 'normal',
    is_channel_message  BOOLEAN NOT NULL DEFAULT false,
    thread_id           VARCHAR(512),
    reply_to_id         VARCHAR(512),
    has_attachments     BOOLEAN NOT NULL DEFAULT false,
    sensitivity_label   VARCHAR(20),
    threat_score        NUMERIC(4,3),
    is_quarantined      BOOLEAN NOT NULL DEFAULT false,
    sent_at             TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    user_id             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_teams_messages_sent        ON teams_messages(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_teams_messages_chat        ON teams_messages(chat_id);
CREATE INDEX IF NOT EXISTS idx_teams_messages_quarantine  ON teams_messages(is_quarantined) WHERE is_quarantined = true;

-- ── Meeting transcripts ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS meeting_transcripts (
    id                  SERIAL PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id         VARCHAR(512) NOT NULL UNIQUE,
    meeting_id          VARCHAR(512) NOT NULL,
    subject_masked      TEXT,
    participant_tokens  JSONB NOT NULL DEFAULT '[]',
    duration_seconds    INTEGER,
    segment_count       INTEGER,
    word_count          INTEGER,
    masked_content      TEXT,
    sensitivity_label   VARCHAR(20),
    threat_score        NUMERIC(4,3),
    meeting_start       TIMESTAMP WITHOUT TIME ZONE,
    meeting_end         TIMESTAMP WITHOUT TIME ZONE,
    organizer_token     VARCHAR(64),
    user_id             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transcripts_meeting ON meeting_transcripts(meeting_id);
CREATE INDEX IF NOT EXISTS idx_transcripts_start   ON meeting_transcripts(meeting_start DESC);

-- ── Calendar events ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS calendar_events (
    id                  SERIAL PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id         VARCHAR(512) NOT NULL UNIQUE,
    subject_masked      TEXT,
    organizer_token     VARCHAR(64),
    attendee_tokens     JSONB NOT NULL DEFAULT '[]',
    start_at            TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    end_at              TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    is_online_meeting   BOOLEAN NOT NULL DEFAULT false,
    meeting_url         TEXT,
    body_masked         TEXT,
    importance          VARCHAR(20) NOT NULL DEFAULT 'normal',
    sensitivity_label   VARCHAR(20),
    recurrence_pattern  VARCHAR(50),
    is_cancelled        BOOLEAN NOT NULL DEFAULT false,
    user_id             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_calendar_start    ON calendar_events(start_at);
CREATE INDEX IF NOT EXISTS idx_calendar_start_asc ON calendar_events(start_at ASC);  -- use for upcoming queries

-- ── To Do tasks ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS todo_tasks (
    id                  SERIAL PRIMARY KEY,
    source_id           INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id         VARCHAR(512) NOT NULL UNIQUE,
    list_id             VARCHAR(512),
    title_masked        TEXT,
    body_masked         TEXT,
    status              VARCHAR(30) NOT NULL DEFAULT 'notStarted',
    importance          VARCHAR(20) NOT NULL DEFAULT 'normal',
    due_at              TIMESTAMP WITHOUT TIME ZONE,
    completed_at        TIMESTAMP WITHOUT TIME ZONE,
    sensitivity_label   VARCHAR(20),
    user_id             INTEGER NOT NULL DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

-- ── OneDrive / SharePoint document metadata ───────────────────────────────────

CREATE TABLE IF NOT EXISTS onedrive_documents (
    id                   SERIAL PRIMARY KEY,
    source_id            INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    external_id          VARCHAR(512) NOT NULL UNIQUE,
    name_masked          TEXT,
    file_extension       VARCHAR(20),
    size_bytes           BIGINT,
    owner_token          VARCHAR(64),
    modifier_token       VARCHAR(64),
    parent_path          TEXT,
    sensitivity_label    VARCHAR(20),
    ms_sensitivity_label VARCHAR(100),
    is_shared            BOOLEAN NOT NULL DEFAULT false,
    share_scope          VARCHAR(50),
    last_modified_at     TIMESTAMP WITHOUT TIME ZONE,
    user_id              INTEGER NOT NULL DEFAULT 1,
    created_at           TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

-- ── Vault tokens ──────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS vault_tokens (
    id               SERIAL PRIMARY KEY,
    token            VARCHAR(64) NOT NULL UNIQUE,
    entity_type      VARCHAR(50) NOT NULL,
    ciphertext       BYTEA NOT NULL,
    key_version      INTEGER NOT NULL DEFAULT 1,
    source_id        INTEGER REFERENCES source_items(id) ON DELETE CASCADE,
    session_context  VARCHAR(64),
    last_accessed_at TIMESTAMP WITHOUT TIME ZONE,
    access_count     INTEGER NOT NULL DEFAULT 0,
    created_at       TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vault_source      ON vault_tokens(source_id);
CREATE INDEX IF NOT EXISTS idx_vault_type        ON vault_tokens(entity_type);
CREATE INDEX IF NOT EXISTS idx_vault_key_version ON vault_tokens(key_version);

-- ── Knowledge items ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS knowledge_items (
    id                SERIAL PRIMARY KEY,
    source_id         INTEGER REFERENCES source_items(id),
    knowledge_type    VARCHAR(30) NOT NULL,
    title_masked      TEXT NOT NULL,
    detail_masked     TEXT,
    owner_token       VARCHAR(64),
    due_at            TIMESTAMP WITHOUT TIME ZONE,
    priority          VARCHAR(20) NOT NULL DEFAULT 'medium',
    confidence        NUMERIC(4,3),
    status            VARCHAR(30) NOT NULL DEFAULT 'open',
    sensitivity_label VARCHAR(20),
    embedding         JSONB,
    source_type       VARCHAR(50),
    vault_refs        JSONB NOT NULL DEFAULT '[]',
    user_id           INTEGER NOT NULL DEFAULT 1,
    created_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    resolved_at       TIMESTAMP WITHOUT TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_ki_type     ON knowledge_items(knowledge_type);
CREATE INDEX IF NOT EXISTS idx_ki_status   ON knowledge_items(status) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_ki_due      ON knowledge_items(due_at) WHERE due_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_ki_source   ON knowledge_items(source_id);
CREATE INDEX IF NOT EXISTS idx_ki_priority ON knowledge_items(priority, due_at);

-- ── Threat assessments ────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS threat_assessments (
    id                 SERIAL PRIMARY KEY,
    source_id          INTEGER NOT NULL REFERENCES source_items(id) ON DELETE CASCADE,
    threat_score       NUMERIC(4,3) NOT NULL,
    category           VARCHAR(20) NOT NULL,
    phishing_score     NUMERIC(4,3) NOT NULL DEFAULT 0,
    bec_score          NUMERIC(4,3) NOT NULL DEFAULT 0,
    credential_risk    NUMERIC(4,3) NOT NULL DEFAULT 0,
    urgency_score      NUMERIC(4,3) NOT NULL DEFAULT 0,
    injection_score    NUMERIC(4,3) NOT NULL DEFAULT 0,
    social_eng_score   NUMERIC(4,3) NOT NULL DEFAULT 0,
    link_risk_score    NUMERIC(4,3) NOT NULL DEFAULT 0,
    flags              JSONB NOT NULL DEFAULT '[]',
    recommended_action VARCHAR(100),
    notes              TEXT,
    assessed_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    reviewed_by        INTEGER
);

CREATE INDEX IF NOT EXISTS idx_threat_category ON threat_assessments(category);
CREATE INDEX IF NOT EXISTS idx_threat_score    ON threat_assessments(threat_score DESC);
CREATE INDEX IF NOT EXISTS idx_threat_source   ON threat_assessments(source_id);

-- ── Graph sync state ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS graph_sync_state (
    id             SERIAL PRIMARY KEY,
    source_type    VARCHAR(50) NOT NULL,
    user_upn       VARCHAR(255) NOT NULL,
    delta_token    TEXT,
    last_synced_at TIMESTAMP WITHOUT TIME ZONE,
    items_synced   INTEGER NOT NULL DEFAULT 0,
    sync_errors    INTEGER NOT NULL DEFAULT 0,
    is_active      BOOLEAN NOT NULL DEFAULT true,
    UNIQUE(source_type, user_upn)
);

-- ── Audit events (7-year legal hold — never auto-delete) ─────────────────────

CREATE TABLE IF NOT EXISTS audit_events (
    id            BIGSERIAL PRIMARY KEY,
    event_type    VARCHAR(50) NOT NULL,
    actor         VARCHAR(100),
    source_id     INTEGER REFERENCES source_items(id),
    vault_token   VARCHAR(64),
    resource_type VARCHAR(50),
    resource_id   INTEGER,
    justification TEXT,
    ip_address    VARCHAR(45),
    request_id    VARCHAR(64),
    outcome       VARCHAR(20) NOT NULL DEFAULT 'SUCCESS',
    event_data    JSONB,
    created_at    TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_type   ON audit_events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_events(actor, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_vault  ON audit_events(vault_token) WHERE vault_token IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_audit_source ON audit_events(source_id);

-- Confirm all tables created
DO $$
DECLARE
    tbl TEXT;
    missing TEXT[] := '{}';
    expected TEXT[] := ARRAY[
        'emails','teams_messages','meeting_transcripts','calendar_events',
        'todo_tasks','onedrive_documents','vault_tokens','knowledge_items',
        'threat_assessments','graph_sync_state','audit_events'
    ];
BEGIN
    FOREACH tbl IN ARRAY expected LOOP
        IF NOT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = tbl) THEN
            missing := missing || tbl;
        END IF;
    END LOOP;
    IF array_length(missing, 1) IS NOT NULL THEN
        RAISE EXCEPTION 'Missing tables: %', missing;
    ELSE
        RAISE NOTICE 'Phase 2A migration complete — all 11 tables present';
    END IF;
END;
$$;
