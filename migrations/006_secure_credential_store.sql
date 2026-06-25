-- Migration 006: Secure Credential Store (Follow-up Center plan — Phase 1)
-- Adds the single, credential-type-agnostic secure store that Teams/Email/manual
-- sources synchronize into. The plaintext value never lives here — only the
-- AES-256-GCM ciphertext on credential_versions. Rotation deactivates the prior
-- active version and inserts version+1 (mirrors knowledge_items supersession).
-- All additive; no existing tables are altered.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/006_secure_credential_store.sql

BEGIN;

-- ── secure_credentials: one row per distinct credential identity ──────────────
CREATE TABLE IF NOT EXISTS secure_credentials (
    id                SERIAL PRIMARY KEY,
    credential_key    VARCHAR(256) NOT NULL UNIQUE,
    credential_type   VARCHAR(40)  NOT NULL,
    system_name       VARCHAR(128),
    context           JSON,
    owner_token       VARCHAR(64),
    active_version_id INTEGER,                 -- app-maintained; no circular FK
    created_at        TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    last_seen_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_secure_credentials_credential_key
    ON secure_credentials(credential_key);
CREATE INDEX IF NOT EXISTS ix_secure_credentials_type_system
    ON secure_credentials(credential_type, system_name);

-- ── credential_versions: encrypted, versioned values + provenance ─────────────
CREATE TABLE IF NOT EXISTS credential_versions (
    id              SERIAL PRIMARY KEY,
    credential_id   INTEGER NOT NULL REFERENCES secure_credentials(id) ON DELETE CASCADE,
    ciphertext      BYTEA   NOT NULL,          -- nonce(12)||ciphertext||tag(16)
    key_version     INTEGER NOT NULL DEFAULT 1,
    value_fingerprint VARCHAR(64),             -- keyed HMAC; rotation detection w/o decrypt
    version         INTEGER NOT NULL DEFAULT 1,
    is_active       BOOLEAN DEFAULT TRUE,
    valid_from      TIMESTAMP WITHOUT TIME ZONE,
    valid_to        TIMESTAMP WITHOUT TIME ZONE,
    source_type     VARCHAR(50),
    source_id       INTEGER REFERENCES source_items(id) ON DELETE SET NULL,
    source_metadata JSON,
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW(),
    last_seen_at    TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_credential_versions_credential_id
    ON credential_versions(credential_id);
CREATE INDEX IF NOT EXISTS ix_credential_versions_is_active
    ON credential_versions(is_active);
CREATE INDEX IF NOT EXISTS ix_credential_versions_value_fingerprint
    ON credential_versions(value_fingerprint);

-- At most one active version per credential — DB-level backstop for the
-- secure_store_service's transactional rotation.
CREATE UNIQUE INDEX IF NOT EXISTS ux_credential_versions_active
    ON credential_versions(credential_id)
    WHERE is_active = TRUE;

COMMENT ON COLUMN secure_credentials.credential_type IS
    'api_key|password|certificate|ssh_key|jwt|oauth_token|client_secret|'
    'db_credential|connection_string|license_key|... (open taxonomy)';
COMMENT ON COLUMN secure_credentials.context IS
    'Flexible dimension map: environment/project/application/region/customer/...';
COMMENT ON COLUMN credential_versions.source_metadata IS
    'Provenance: conversation_id/chat_id/team_id/channel_id/sender/source_url/message_id/...';

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name IN ('secure_credentials', 'credential_versions')
ORDER BY table_name, ordinal_position;
