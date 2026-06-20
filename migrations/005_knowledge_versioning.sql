-- Migration 005: Knowledge retrieval, versioning & intelligence (Plan 2)
-- Adds versioning/supersession columns + retrieval indexes to knowledge_items,
-- backfills existing Plan 1 rows so the partial unique index is safe to create,
-- and registers the credential_reference knowledge_type in the column comment.
-- All new columns are nullable/defaulted so existing rows keep working.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/005_knowledge_versioning.sql
--
-- After this runs, optionally enrich rows (embeddings + deterministic structured
-- keys) with:
--   python scripts/backfill_knowledge_versioning.py

BEGIN;

-- ── knowledge_items: versioning & supersession columns ────────────────────────
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS knowledge_key     VARCHAR(128);
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS version           INTEGER DEFAULT 1;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS is_active         BOOLEAN DEFAULT TRUE;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS superseded_by_id  INTEGER REFERENCES knowledge_items(id);
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS valid_from        TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS valid_to          TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE knowledge_items ADD COLUMN IF NOT EXISTS resolution_method VARCHAR(30);

-- ── Backfill existing Plan 1 rows (Plan 2 §9) ────────────────────────────────
-- Each legacy row becomes its own single-row active chain: a per-row unique
-- "legacy:<id>" key (we cannot retroactively know which were the same fact),
-- version 1, active, validity opening at creation. This MUST run before the
-- partial unique index so no false "no active version" gap is created.
UPDATE knowledge_items
   SET version           = COALESCE(version, 1),
       is_active         = COALESCE(is_active, TRUE),
       valid_from        = COALESCE(valid_from, created_at),
       knowledge_key     = 'legacy:' || id,
       resolution_method = COALESCE(resolution_method, 'backfill')
 WHERE knowledge_key IS NULL;

-- ── Retrieval indexes ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_knowledge_items_key_active  ON knowledge_items(knowledge_key, is_active);
CREATE INDEX IF NOT EXISTS ix_knowledge_items_type_owner  ON knowledge_items(knowledge_type, owner_token);
CREATE INDEX IF NOT EXISTS ix_knowledge_items_knowledge_key ON knowledge_items(knowledge_key);
CREATE INDEX IF NOT EXISTS ix_knowledge_items_is_active   ON knowledge_items(is_active);

-- At most one active version per knowledge_key, enforced at the DB layer
-- (belt-and-suspenders for the evolution service's transactional supersession).
CREATE UNIQUE INDEX IF NOT EXISTS ux_knowledge_items_active_key
    ON knowledge_items(knowledge_key)
    WHERE is_active = TRUE;

COMMENT ON COLUMN knowledge_items.knowledge_type IS
    'task|commitment|decision|risk|escalation|reminder|approval|alert|'
    'question|information_request|mentioned_document|mentioned_link|'
    'system_reference|github_pr|jira_ticket|credential_reference';

COMMENT ON COLUMN knowledge_items.knowledge_key IS
    'Groups all versions of the same fact. Deterministic for structured types, '
    'semantic/UUID-generated for free-text. See knowledge_evolution_service.';

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'knowledge_items'
ORDER BY ordinal_position;
