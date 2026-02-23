-- Add non-partial ingested_at index for discovery queries that scan all articles.
-- Apply with: psql "$DATABASE_URL" -f db/migrations/005_add_feed_article_ingested_index.sql

CREATE INDEX IF NOT EXISTS idx_feed_article_ingested_at
    ON feed_article(ingested_at DESC);
