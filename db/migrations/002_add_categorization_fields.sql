-- Migration: Add LLM categorization tracking fields
-- Apply with: psql "$DATABASE_URL" -f db/migrations/002_add_categorization_fields.sql

-- Track whether a feed article has been processed by the categorizer
ALTER TABLE feed_article ADD COLUMN IF NOT EXISTS categorized_at TIMESTAMPTZ;

-- Track which LLM model performed the categorization
ALTER TABLE situation_article ADD COLUMN IF NOT EXISTS llm_model TEXT;

-- Index to efficiently find uncategorized articles
CREATE INDEX IF NOT EXISTS idx_feed_article_uncategorized
    ON feed_article(ingested_at DESC) WHERE categorized_at IS NULL;
