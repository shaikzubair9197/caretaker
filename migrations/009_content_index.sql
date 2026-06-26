-- Migration 009: Content Retrieval catalog (Document Retrieval plan — Phase 1)
-- Adds the generic, multi-source content catalog that powers ranked document
-- retrieval (Meeting Prep is the first consumer via snapshot["related_content"]).
-- The extracted plain text is NEVER stored in Postgres — only on the on-disk
-- cache (text_cache_path); the DB keeps metadata, keywords, entities, embedding
-- and content hashes (design rule #1). Work state (index_status/needs_reindex)
-- is orthogonal to serving state (is_active AND index_status='INDEXED').
-- Versioning/supersession mirror knowledge_items. All additive; no existing
-- tables are altered.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/009_content_index.sql

BEGIN;

-- ── indexed_content: one row per content version ──────────────────────────────
CREATE TABLE IF NOT EXISTS indexed_content (
    id                  SERIAL PRIMARY KEY,

    -- Source identity (provider-agnostic)
    source_type         VARCHAR(50)   NOT NULL DEFAULT 'LOCAL',
    source_identifier   VARCHAR(1024) NOT NULL,
    source_metadata     JSON,

    -- File / display metadata
    root_label          VARCHAR(255),
    filename            VARCHAR(512)  NOT NULL,
    folder              VARCHAR(512),
    folder_path         TEXT,
    extension           VARCHAR(32),
    size_bytes          BIGINT,
    modified_at         TIMESTAMP WITHOUT TIME ZONE,
    content_hash        VARCHAR(64),

    -- Independent lifecycle versions (design rule #5)
    parser_version      VARCHAR(20),
    pipeline_version    VARCHAR(20),
    text_cache_path     TEXT,
    keywords            JSON,
    entities            JSON,
    embedding           JSON,
    embedding_model     VARCHAR(100),
    embedding_dimension INTEGER,
    text_extracted_at   TIMESTAMP WITHOUT TIME ZONE,
    embedded_at         TIMESTAMP WITHOUT TIME ZONE,
    indexed_at          TIMESTAMP WITHOUT TIME ZONE,

    -- Versioning / supersession (mirrors knowledge_items)
    version             INTEGER DEFAULT 1,
    is_active           BOOLEAN DEFAULT TRUE,
    valid_from          TIMESTAMP WITHOUT TIME ZONE,
    valid_to            TIMESTAMP WITHOUT TIME ZONE,
    superseded_by_id    INTEGER REFERENCES indexed_content(id),

    -- Work-queue state (orthogonal to serving — design rule #3)
    index_status        VARCHAR(20) NOT NULL DEFAULT 'PENDING',  -- PENDING|INDEXING|INDEXED|ERROR
    needs_reindex       BOOLEAN DEFAULT FALSE,
    index_error         TEXT,
    retry_count         INTEGER DEFAULT 0,
    next_retry_at       TIMESTAMP WITHOUT TIME ZONE,

    user_id             INTEGER DEFAULT 1,
    created_at          TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- ── Queue / serve hot-path indexes ────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_indexed_content_status         ON indexed_content(index_status);
CREATE INDEX IF NOT EXISTS ix_indexed_content_needs_reindex  ON indexed_content(needs_reindex);
CREATE INDEX IF NOT EXISTS ix_indexed_content_is_active      ON indexed_content(is_active);
CREATE INDEX IF NOT EXISTS ix_indexed_content_folder         ON indexed_content(folder);
CREATE INDEX IF NOT EXISTS ix_indexed_content_parser_version ON indexed_content(parser_version);

-- At most one ACTIVE row per (source_type, source_identifier). The worker
-- deactivates the prior version before activating version+1, so this partial
-- unique index is never violated (belt-and-suspenders for the supersession
-- transaction — same pattern as ux_knowledge_items_active_key).
CREATE UNIQUE INDEX IF NOT EXISTS ux_indexed_content_active_source
    ON indexed_content(source_type, source_identifier)
    WHERE is_active = TRUE;

COMMENT ON COLUMN indexed_content.index_status IS
    'Work state: PENDING|INDEXING|INDEXED|ERROR (orthogonal to serving — '
    'served = is_active AND index_status=''INDEXED''). See content_catalog.';

COMMENT ON COLUMN indexed_content.text_cache_path IS
    'Relative path under CONTENT_TEXT_CACHE_DIR holding the extracted plain text. '
    'Extracted text is never stored in Postgres (design rule #1).';

-- ── content_index_state: single-row catalog generation counter (rule #14) ─────
CREATE TABLE IF NOT EXISTS content_index_state (
    id         SERIAL PRIMARY KEY,
    generation BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

-- Seed the singleton row (id=1). Idempotent — re-running the migration is a no-op.
INSERT INTO content_index_state (id, generation, updated_at)
VALUES (1, 0, NOW())
ON CONFLICT (id) DO NOTHING;

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'indexed_content'
ORDER BY ordinal_position;
