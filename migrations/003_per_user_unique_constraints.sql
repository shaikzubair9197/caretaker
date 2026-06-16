-- Scope task/memory uniqueness per user
-- Apply with: psql -U postgres -h localhost -d caretaker -f migrations/003_per_user_unique_constraints.sql
--
-- A global UNIQUE on tasks.description / memories.text collides across users
-- and forbids legitimate recurring tasks. Re-key both to (user_id, <col>).

ALTER TABLE tasks DROP CONSTRAINT IF EXISTS uq_task_description;
ALTER TABLE tasks ADD CONSTRAINT uq_task_description UNIQUE (user_id, description);

ALTER TABLE memories DROP CONSTRAINT IF EXISTS uq_memory_text;
ALTER TABLE memories ADD CONSTRAINT uq_memory_text UNIQUE (user_id, text);
