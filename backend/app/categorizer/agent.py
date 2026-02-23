"""
Categorization agent - orchestrates the MCP client + LLM pipeline.

Connects to the MCP server via stdio, runs two phases each cycle:
1) Discover broad situations from compact article titles across the feed.
2) Categorize uncategorized articles against existing + discovered situations.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from ..config import settings
from .llm_providers import LLMProvider
from .llm_providers import create_provider

log = logging.getLogger("categorizer.agent")

# Batch size for LLM categorization calls (articles per prompt)
LLM_BATCH_SIZE = 10

# Minimum validated supporting articles required to create a new situation
MIN_ARTICLES_FOR_NEW_SITUATION = 3


def _normalize_situation_title(title: str) -> str:
    """Normalize titles for punctuation/case/whitespace-insensitive dedupe."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _get_server_params() -> StdioServerParameters:
    """Build MCP server subprocess parameters."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.app.mcp_server.server"],
        env=dict(os.environ),
    )


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> object:
    """Call an MCP tool and parse the JSON response."""
    result = await session.call_tool(name, arguments)
    if not result.content:
        return None
    text = result.content[0].text
    data = json.loads(text)
    if isinstance(data, dict) and "error" in data:
        log.error("MCP tool %s returned error: %s", name, data["error"])
        return None
    return data


async def _discover_and_create_situations(
    session: ClientSession,
    provider: LLMProvider,
    situations: list[dict],
    stats: dict,
) -> None:
    """Phase 1: discover broad situations from compact full-feed titles."""
    article_titles = await _call_tool(
        session,
        "get_all_articles_titles",
        {
            "limit": settings.categorizer_discovery_limit,
            "since_hours": settings.categorizer_discovery_since_hours,
        },
    )

    if not article_titles or not isinstance(article_titles, list):
        log.info("Phase 1: No article titles available for discovery")
        return

    stats["discovery_articles_scanned"] = len(article_titles)
    log.info("Phase 1: Discovering situations from %d articles", len(article_titles))

    try:
        proposed = await provider.discover_situations(article_titles, situations)
    except Exception:
        log.exception(
            "Phase 1 discovery failed; continuing with Phase 2 using existing situations"
        )
        stats["errors"] += 1
        return

    stats["discovery_proposed"] = len(proposed)
    if not proposed:
        log.info("Phase 1: LLM proposed no new situations")
        return

    valid_input_ids = {
        str(a.get("id"))
        for a in article_titles
        if a.get("id") is not None
    }
    existing_title_keys = {
        _normalize_situation_title(str(s.get("title") or ""))
        for s in situations
        if s.get("title")
    }

    # Aggregate by normalized title so equivalent titles merge support IDs.
    aggregated: dict[str, dict] = {}
    for ns in proposed:
        key = _normalize_situation_title(ns.title)
        if not key:
            stats["situations_filtered"] += 1
            continue

        if key in existing_title_keys:
            stats["situations_filtered"] += 1
            continue

        supporting_ids = {
            article_id.strip()
            for article_id in ns.supporting_article_ids
            if article_id and article_id.strip() in valid_input_ids
        }

        if key not in aggregated:
            aggregated[key] = {
                "info": ns,
                "article_ids": set(),
            }

        aggregated[key]["article_ids"].update(supporting_ids)

    for key, data in sorted(
        aggregated.items(),
        key=lambda item: len(item[1]["article_ids"]),
        reverse=True,
    ):
        article_count = len(data["article_ids"])
        ns = data["info"]

        if article_count < MIN_ARTICLES_FOR_NEW_SITUATION:
            log.info(
                "Phase 1: Filtering discovered situation '%s' - %d validated supporting articles (need %d)",
                ns.title,
                article_count,
                MIN_ARTICLES_FOR_NEW_SITUATION,
            )
            stats["situations_filtered"] += 1
            continue

        try:
            result = await _call_tool(
                session,
                "create_situation",
                {
                    "title": ns.title,
                    "description": ns.description,
                    "query": ns.query,
                },
            )
            if result and result.get("success"):
                situation_id = str(result["situation_id"])
                if not result.get("already_existed"):
                    stats["situations_created"] += 1
                    log.info(
                        "Phase 1: Created situation '%s' (id=%s, %d supporting articles)",
                        ns.title,
                        situation_id,
                        article_count,
                    )
                else:
                    log.info(
                        "Phase 1: Reused existing situation '%s' (id=%s)",
                        ns.title,
                        situation_id,
                    )

                if not any(str(s.get("id")) == situation_id for s in situations):
                    situations.append(
                        {
                            "id": situation_id,
                            "title": ns.title,
                            "description": ns.description,
                            "query": ns.query,
                        }
                    )
                existing_title_keys.add(key)
            else:
                log.warning("Failed to create discovered situation %s: %s", ns.title, result)
                stats["errors"] += 1
        except Exception:
            log.exception("Failed to create discovered situation: %s", ns.title)
            stats["errors"] += 1


async def run_categorization_cycle(
    session: ClientSession,
    provider: LLMProvider,
) -> dict:
    """Run one full two-phase categorization cycle."""
    stats = {
        "articles_processed": 0,
        "links_created": 0,
        "errors": 0,
        "skipped": 0,
        "situations_created": 0,
        "situations_filtered": 0,
        "discovery_articles_scanned": 0,
        "discovery_proposed": 0,
    }

    # Load all currently active situations first.
    situations = await _call_tool(session, "get_all_active_situations", {})
    if not situations or not isinstance(situations, list):
        situations = []
        log.info("No existing situations found")
    else:
        log.info("Found %d existing situations", len(situations))

    # Phase 1: discover new situations from the broader feed context.
    await _discover_and_create_situations(session, provider, situations, stats)

    # Phase 2: categorize only uncategorized articles against all situations.
    articles = await _call_tool(
        session,
        "get_uncategorized_articles",
        {
            "limit": settings.categorizer_batch_size,
            "since_hours": 168,
        },
    )
    if not articles or not isinstance(articles, list):
        log.info("Phase 2: No uncategorized articles - skipping")
        return stats

    log.info("Phase 2: Found %d uncategorized articles to process", len(articles))
    threshold = settings.categorizer_relevance_threshold
    valid_situation_ids = {
        str(s.get("id"))
        for s in situations
        if s.get("id") is not None
    }
    if not valid_situation_ids:
        log.warning(
            "Phase 2: No situations available for matching; leaving uncategorized articles for retry"
        )
        return stats

    for i in range(0, len(articles), LLM_BATCH_SIZE):
        batch = articles[i:i + LLM_BATCH_SIZE]
        batch_index = i // LLM_BATCH_SIZE + 1
        total_batches = (len(articles) + LLM_BATCH_SIZE - 1) // LLM_BATCH_SIZE
        log.info("Phase 2: Processing batch %d/%d (%d articles)", batch_index, total_batches, len(batch))

        try:
            batch_result = await provider.categorize_batch(batch, situations)
        except Exception:
            log.exception("LLM categorization failed for batch starting at index %d", i)
            stats["errors"] += len(batch)
            continue

        if batch_result.new_situations:
            log.warning(
                "Phase 2: LLM returned %d unexpected new_situations; ignoring them",
                len(batch_result.new_situations),
            )

        result_by_article_id = {
            result.article_id: result
            for result in batch_result.results
        }

        for article in batch:
            article_id = str(article["id"])
            article_result = result_by_article_id.get(article_id)
            if article_result is None:
                log.warning(
                    "Phase 2: LLM omitted article %s; leaving it uncategorized for retry",
                    article_id,
                )
                stats["errors"] += 1
                continue

            stats["articles_processed"] += 1
            valid_matches = []
            for match in article_result.matches:
                if match.relevance_score < threshold:
                    continue
                if match.situation_id not in valid_situation_ids:
                    continue
                valid_matches.append(
                    {
                        "situation_id": match.situation_id,
                        "relevance_score": match.relevance_score,
                        "reason": match.reason,
                    }
                )

            try:
                if valid_matches:
                    result = await _call_tool(
                        session,
                        "categorize_article",
                        {
                            "feed_article_id": article_id,
                            "situation_matches": valid_matches,
                            "llm_model": provider.model_name,
                        },
                    )
                    if result and result.get("success"):
                        stats["links_created"] += result.get("links_created", 0)
                    else:
                        log.warning("categorize_article failed for %s: %s", article_id, result)
                        stats["errors"] += 1
                else:
                    await _call_tool(
                        session,
                        "mark_article_uncategorizable",
                        {
                            "feed_article_id": article_id,
                            "reason": "No existing situations matched above threshold",
                        },
                    )
                    stats["skipped"] += 1
            except Exception:
                log.exception("Failed to write result for article %s", article_id)
                stats["errors"] += 1

    return stats


async def run_agent() -> dict:
    """Entry point: spawn MCP server, connect, run one categorization cycle."""
    if not settings.llm_api_key:
        log.error("LLM_API_KEY not configured - cannot run categorization")
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
        "Cycle complete: %d processed, %d links, %d skipped, %d errors, %d new situations, %d filtered, %d discovery titles, %d proposed",
        stats.get("articles_processed", 0),
        stats.get("links_created", 0),
        stats.get("skipped", 0),
        stats.get("errors", 0),
        stats.get("situations_created", 0),
        stats.get("situations_filtered", 0),
        stats.get("discovery_articles_scanned", 0),
        stats.get("discovery_proposed", 0),
    )
    return stats
