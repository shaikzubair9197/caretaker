-- Migration 004: Transcript ingestion + meeting intelligence (Plan 1)
-- Adds: meeting_transcripts.content_hash, llm_call_logs extraction-metadata
-- columns, the transcript_segments table, and knowledge_items extraction
-- linkage. All new columns are nullable so existing rows are unaffected.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/004_transcript_meeting_intelligence.sql

BEGIN;

-- ── meeting_transcripts: idempotency support ──────────────────────────────────
-- SHA-256 of the raw archived payload — lets TranscriptIngestionService detect
-- that identical transcript content was already extracted, even under a
-- different external_id (e.g. re-fetched transcript resource).
ALTER TABLE meeting_transcripts
    ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_transcripts_content_hash ON meeting_transcripts(content_hash);

-- ── llm_call_logs: extraction metadata ────────────────────────────────────────
-- Reuses the existing LLM audit table for extraction-run metadata instead of
-- introducing a parallel "ExtractionRun" table.
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS prompt_version VARCHAR;

ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS extraction_version VARCHAR;

ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS source_id INTEGER REFERENCES source_items(id);

CREATE INDEX IF NOT EXISTS idx_llm_call_logs_source ON llm_call_logs(source_id);

COMMENT ON COLUMN llm_call_logs.call_type IS
    '"panic_extract" | "intent_reason" | "meeting_intelligence_extract"';

-- ── Transcript segments — per-utterance breakdown ─────────────────────────────
CREATE TABLE IF NOT EXISTS transcript_segments (
    id              SERIAL PRIMARY KEY,
    transcript_id   INTEGER NOT NULL REFERENCES meeting_transcripts(id) ON DELETE CASCADE,
    speaker_token   VARCHAR(64),
    start_ms        INTEGER,
    end_ms          INTEGER,
    text_masked     TEXT,
    sequence_index  INTEGER NOT NULL,
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_transcript ON transcript_segments(transcript_id);
CREATE INDEX IF NOT EXISTS idx_transcript_segments_speaker    ON transcript_segments(speaker_token);

-- ── knowledge_items: extraction linkage + type-specific data ─────────────────
ALTER TABLE knowledge_items
    ADD COLUMN IF NOT EXISTS extra_data JSONB;

ALTER TABLE knowledge_items
    ADD COLUMN IF NOT EXISTS llm_call_log_id INTEGER REFERENCES llm_call_logs(id);

CREATE INDEX IF NOT EXISTS idx_ki_llm_call_log ON knowledge_items(llm_call_log_id);

COMMENT ON COLUMN knowledge_items.knowledge_type IS
    'task|commitment|decision|risk|escalation|reminder|approval|alert|'
    'question|information_request|mentioned_document|mentioned_link|'
    'system_reference|github_pr|jira_ticket';

COMMIT;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name IN ('meeting_transcripts', 'llm_call_logs', 'transcript_segments', 'knowledge_items')
ORDER BY table_name, ordinal_position;
