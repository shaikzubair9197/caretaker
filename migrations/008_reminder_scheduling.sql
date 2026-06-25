-- Migration 008: Reminder auto-scheduling (plan — Phase 6)
-- Adds a self-contained reminder window to commitments so a deadline-bearing
-- reminder fires once as a desktop notification without a per-task approval popup.
-- The due date is snapshotted onto the Commitment at creation; from then on the
-- reminder daemon works EXCLUSIVELY from these columns and never reads back the
-- originating KnowledgeItem (execution layer stays independent of extraction).
-- All additive/nullable.
-- Run once against the caretaker database:
--   psql caretaker -f migrations/008_reminder_scheduling.sql

BEGIN;

ALTER TABLE commitments ADD COLUMN IF NOT EXISTS remind_at TIMESTAMP;
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS remind_offset_minutes INTEGER;
ALTER TABLE commitments ADD COLUMN IF NOT EXISTS reminded_at TIMESTAMP;

COMMENT ON COLUMN commitments.remind_at IS
    'When to fire this reminder = due_date - remind_offset_minutes, snapshotted at '
    'commitment creation. NULL = no reminder scheduled.';
COMMENT ON COLUMN commitments.remind_offset_minutes IS
    'Lead time in minutes used to derive remind_at from due_date (default from '
    'REMINDER_DEFAULT_OFFSET_MINUTES). NULL when no reminder is scheduled.';
COMMENT ON COLUMN commitments.reminded_at IS
    'Fire-once stamp: set when the reminder notification has been fired. NULL = '
    'not yet fired. PATCH /reminders/{id} clears it to re-arm a rescheduled reminder.';

-- Speeds up the daemon''s due-reminder scan (remind_at <= now AND reminded_at IS NULL).
CREATE INDEX IF NOT EXISTS ix_commitments_remind_at ON commitments(remind_at);

COMMIT;

-- Verify
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'commitments'
  AND column_name IN ('remind_at', 'remind_offset_minutes', 'reminded_at')
ORDER BY column_name;
