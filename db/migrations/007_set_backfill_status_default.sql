-- Ensure existing databases have a default backfill status.
-- Apply with: psql "$DATABASE_URL" -f db/migrations/007_set_backfill_status_default.sql

ALTER TABLE situation_backfill_state
    ALTER COLUMN status SET DEFAULT 'pending';
