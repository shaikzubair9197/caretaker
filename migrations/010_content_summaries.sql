-- Migration 010: Permanent document summaries (Document Retrieval follow-on)
-- A content-addressed, permanent store of LLM document summaries. Keyed by
-- content_hash so identical document content reuses the same summary across
-- meetings and restarts — generated once, never re-billed. Regenerated only when
-- the content changes (new hash) or the summary prompt_version changes. Stores
-- the UNMASKED summary (same trust boundary as the extracted-text cache); the LLM
-- itself only ever saw masked text. Additive; no existing tables are altered.
-- Run once:
--   psql caretaker -f migrations/010_content_summaries.sql

BEGIN;

CREATE TABLE IF NOT EXISTS content_summaries (
    id              SERIAL PRIMARY KEY,
    content_hash    VARCHAR(64) NOT NULL UNIQUE,
    summary         TEXT        NOT NULL,
    redaction_count INTEGER DEFAULT 0,
    token_count     INTEGER DEFAULT 0,
    truncated       BOOLEAN DEFAULT FALSE,
    summary_model   VARCHAR(100),
    prompt_version  VARCHAR(20),
    user_id         INTEGER DEFAULT 1,
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_content_summaries_content_hash
    ON content_summaries(content_hash);

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'content_summaries'
ORDER BY ordinal_position;
