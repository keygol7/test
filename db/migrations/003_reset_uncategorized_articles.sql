-- Reset articles that were marked as categorized but have no situation links.
-- These were previously skipped because only existing situations were checked.
-- After this reset, the LLM will re-process them and discover new situations.

UPDATE feed_article
SET categorized_at = NULL
WHERE categorized_at IS NOT NULL
  AND id NOT IN (
    SELECT DISTINCT fa.id
    FROM feed_article fa
    JOIN article a ON a.url = fa.original_url
    JOIN situation_article sa ON sa.article_id = a.id
  );
