-- Add persistent backfill progress state for each situation.
-- Apply with: psql "$DATABASE_URL" -f db/migrations/006_add_situation_backfill_state.sql

CREATE TABLE IF NOT EXISTS situation_backfill_state (
    situation_id UUID PRIMARY KEY REFERENCES situation(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'done', 'failed')),
    cursor_ingested_at TIMESTAMPTZ,
    cursor_feed_article_id UUID,
    processed_count INTEGER NOT NULL DEFAULT 0,
    linked_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_situation_backfill_status_updated
    ON situation_backfill_state(status, updated_at);
