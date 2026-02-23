-- Add llm_created column to situation table to track LLM-auto-created situations.
-- Then delete all existing LLM-created situations and their linked data,
-- and reset categorized_at on all feed articles so the LLM re-processes
-- everything with the new broader-situation logic.

-- 1. Add the column
ALTER TABLE situation ADD COLUMN IF NOT EXISTS llm_created BOOLEAN NOT NULL DEFAULT FALSE;

-- 2. Mark any existing situations that were created by the LLM
--    (situations linked to articles with llm_model set are LLM-created)
UPDATE situation SET llm_created = TRUE
WHERE id IN (
    SELECT DISTINCT sa.situation_id
    FROM situation_article sa
    WHERE sa.llm_model IS NOT NULL
);

-- 3. Delete LLM-created situations (cascades to situation_article rows)
DELETE FROM situation WHERE llm_created = TRUE;

-- 4. Clean up orphaned articles (articles no longer linked to any situation)
DELETE FROM article WHERE id NOT IN (
    SELECT DISTINCT article_id FROM situation_article
);

-- 5. Reset all feed articles so they get re-categorized
UPDATE feed_article SET categorized_at = NULL;
