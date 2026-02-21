-- News Situation Dashboard schema (PostgreSQL)
-- Apply with: psql "$DATABASE_URL" -f db/schema.sql

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS app_user (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS situation (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    query TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS source (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    base_url TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL CHECK (source_type IN ('news_site', 'social', 'rss', 'api', 'other')),
    credibility_score NUMERIC(3,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, base_url)
);

CREATE TABLE IF NOT EXISTS article (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id UUID REFERENCES source(id) ON DELETE SET NULL,
    external_id TEXT,
    url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    published_at TIMESTAMPTZ,
    summary TEXT,
    content TEXT,
    sentiment_score NUMERIC(4,3),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS situation_article (
    situation_id UUID NOT NULL REFERENCES situation(id) ON DELETE CASCADE,
    article_id UUID NOT NULL REFERENCES article(id) ON DELETE CASCADE,
    relevance_score NUMERIC(4,3),
    reason TEXT,
    tagged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (situation_id, article_id)
);

CREATE TABLE IF NOT EXISTS dashboard_snapshot (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    situation_id UUID NOT NULL REFERENCES situation(id) ON DELETE CASCADE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    article_count INTEGER NOT NULL DEFAULT 0,
    source_count INTEGER NOT NULL DEFAULT 0,
    top_headlines JSONB NOT NULL DEFAULT '[]'::jsonb,
    trend_notes TEXT
);

CREATE TABLE IF NOT EXISTS feed_source (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    rss_url TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL DEFAULT 'general',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_fetched_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS feed_article (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    feed_source_id UUID NOT NULL REFERENCES feed_source(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    original_url TEXT NOT NULL UNIQUE,
    snippet TEXT,
    author TEXT,
    published_date TIMESTAMPTZ,
    thumbnail_url TEXT,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_feed_article_source_id ON feed_article(feed_source_id);
CREATE INDEX IF NOT EXISTS idx_feed_article_published ON feed_article(published_date DESC);

CREATE INDEX IF NOT EXISTS idx_situation_user_id ON situation(user_id);
CREATE INDEX IF NOT EXISTS idx_article_source_id ON article(source_id);
CREATE INDEX IF NOT EXISTS idx_article_published_at ON article(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_situation_article_article_id ON situation_article(article_id);
CREATE INDEX IF NOT EXISTS idx_dashboard_snapshot_situation_time
    ON dashboard_snapshot(situation_id, generated_at DESC);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_app_user_set_updated_at ON app_user;
CREATE TRIGGER trg_app_user_set_updated_at
BEFORE UPDATE ON app_user
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_situation_set_updated_at ON situation;
CREATE TRIGGER trg_situation_set_updated_at
BEFORE UPDATE ON situation
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
