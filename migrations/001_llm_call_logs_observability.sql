-- Migration 001: LLM Call Log Observability
-- Adds five new columns to llm_call_logs to support granular failure diagnosis.
-- All columns are nullable so existing rows are unaffected.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/001_llm_call_logs_observability.sql

BEGIN;

-- Layer 6: exact exception class name (e.g. "httpx.ConnectError")
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS exception_type VARCHAR;

-- Layer 6: exception message, truncated to 800 chars, never contains raw PII
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS exception_message TEXT;

-- Layer 4: first 250 chars of the masked prompt — for debugging, no raw PII
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS prompt_preview VARCHAR(300);

-- Layer 4: total character count of the masked prompt sent to the LLM
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS prompt_size_chars INTEGER;

-- Layer 2/3: pre-flight check result (JSON), populated only on ConnectError
-- Schema: {"ollama_reachable": bool, "model_available": bool, "installed_models": [...], "error": str|null}
ALTER TABLE llm_call_logs
    ADD COLUMN IF NOT EXISTS preflight_data JSONB;

-- Update status column comment to reflect new granular enum values
COMMENT ON COLUMN llm_call_logs.status IS
    'LLMStatus enum: SUCCESS | CONNECTION_REFUSED | DNS_FAILURE | TIMEOUT | '
    'MODEL_NOT_FOUND | HTTP_ERROR | JSON_PARSE_ERROR | '
    'SCHEMA_VALIDATION_FAILURE | UNKNOWN_ERROR';

COMMIT;

-- Verify
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'llm_call_logs'
ORDER BY ordinal_position;
