"""
Categorization agent — orchestrates the MCP client + LLM pipeline.

Connects to the MCP server via stdio, reads uncategorized articles,
sends batches to the LLM for categorization, and writes results back.
"""

from __future__ import annotations

import json
import logging
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ..config import settings
from .llm_providers import LLMProvider, create_provider

log = logging.getLogger("categorizer.agent")

# Batch size for LLM calls (articles per prompt)
LLM_BATCH_SIZE = 10


def _get_server_params() -> StdioServerParameters:
    """Build MCP server subprocess parameters."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.app.mcp_server.server"],
    )


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> object:
    """Call an MCP tool and parse the JSON response."""
    result = await session.call_tool(name, arguments)
    if not result.content:
        return None
    text = result.content[0].text
    data = json.loads(text)
    # Check for error responses from MCP server
    if isinstance(data, dict) and "error" in data:
        log.error("MCP tool %s returned error: %s", name, data["error"])
        return None
    return data


async def run_categorization_cycle(
    session: ClientSession, provider: LLMProvider
) -> dict:
    """
    Run one full categorization cycle:
    1. Fetch all active situations
    2. Fetch uncategorized articles
    3. Batch-send to LLM
    4. Write results back via MCP tools
    """
    stats = {"articles_processed": 0, "links_created": 0, "errors": 0, "skipped": 0}

    # 1. Get situations
    situations = await _call_tool(session, "get_all_active_situations", {})
    if not situations:
        log.info("No active situations — skipping categorization")
        return stats

    log.info("Found %d active situations", len(situations))

    # 2. Get uncategorized articles
    articles = await _call_tool(
        session,
        "get_uncategorized_articles",
        {"limit": settings.categorizer_batch_size, "since_hours": 24},
    )
    if not articles or not isinstance(articles, list):
        log.info("No uncategorized articles — skipping")
        return stats

    log.info("Found %d uncategorized articles to process", len(articles))
    threshold = settings.categorizer_relevance_threshold

    # 3. Process in batches
    for i in range(0, len(articles), LLM_BATCH_SIZE):
        batch = articles[i : i + LLM_BATCH_SIZE]
        log.info(
            "Processing batch %d/%d (%d articles)",
            i // LLM_BATCH_SIZE + 1,
            (len(articles) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE,
            len(batch),
        )

        try:
            batch_result = await provider.categorize_batch(batch, situations)
        except Exception:
            log.exception("LLM call failed for batch starting at index %d", i)
            stats["errors"] += len(batch)
            continue

        # 4. Write results back
        for article_result in batch_result.results:
            stats["articles_processed"] += 1
            # Filter by threshold
            valid_matches = [
                {
                    "situation_id": m.situation_id,
                    "relevance_score": m.relevance_score,
                    "reason": m.reason,
                }
                for m in article_result.matches
                if m.relevance_score >= threshold
            ]

            try:
                if valid_matches:
                    result = await _call_tool(
                        session,
                        "categorize_article",
                        {
                            "feed_article_id": article_result.article_id,
                            "situation_matches": valid_matches,
                            "llm_model": provider.model_name,
                        },
                    )
                    if result and result.get("success"):
                        stats["links_created"] += result.get("links_created", 0)
                    else:
                        log.warning(
                            "categorize_article failed for %s: %s",
                            article_result.article_id,
                            result,
                        )
                        stats["errors"] += 1
                else:
                    await _call_tool(
                        session,
                        "mark_article_uncategorizable",
                        {
                            "feed_article_id": article_result.article_id,
                            "reason": "No situations matched above threshold",
                        },
                    )
                    stats["skipped"] += 1
            except Exception:
                log.exception(
                    "Failed to write result for article %s",
                    article_result.article_id,
                )
                stats["errors"] += 1

    return stats


async def run_agent() -> dict:
    """
    Entry point: spawn MCP server, connect, run one categorization cycle.
    Returns stats dict.
    """
    if not settings.llm_api_key:
        log.error("LLM_API_KEY not configured — cannot run categorization")
        return {"error": "LLM_API_KEY not configured"}

    provider = create_provider(
        settings.llm_provider,
        settings.llm_api_key,
        settings.llm_model,
    )
    log.info("Using LLM provider: %s (model: %s)", settings.llm_provider, provider.model_name)

    server_params = _get_server_params()

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            log.info("MCP session initialized")
            stats = await run_categorization_cycle(session, provider)

    log.info(
        "Cycle complete: %d processed, %d links, %d skipped, %d errors",
        stats.get("articles_processed", 0),
        stats.get("links_created", 0),
        stats.get("skipped", 0),
        stats.get("errors", 0),
    )
    return stats
