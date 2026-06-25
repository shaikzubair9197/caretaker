-- Migration 007: Follow-up Center assignee-based classification (plan — Phase 3)
-- Adds meeting_transcripts.self_token so the Follow-up Center can classify
-- Action Items (assigned to the caretaker self) vs Commitments (work involving an
-- external recipient/entity outside the meeting), computed server-side.
-- The `counterparty` signal lives inside knowledge_items.extra_data (JSON) and
-- needs no schema change. All additive/nullable.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/007_followup_classification.sql

BEGIN;

ALTER TABLE meeting_transcripts ADD COLUMN IF NOT EXISTS self_token VARCHAR(64);

COMMENT ON COLUMN meeting_transcripts.self_token IS
    'Scoped speaker token of the caretaker user (participant whose email matches '
    'SENDER_IDENTITY), or NULL if the user was not in the meeting. Drives the '
    'Action Item (assigned to self) vs Commitment (external party) split.';

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'meeting_transcripts' AND column_name = 'self_token';
