"""
Categorization agent - orchestrates the MCP client + LLM pipeline.

Connects to the MCP server via stdio and runs three phases each cycle:
1) Discover broad situations from compact article titles.
2) Drain uncategorized articles using LLM + deterministic keyword matches.
3) Progress resumable keyword backfill for existing situations.
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
from .keyword_matcher import match_article_to_query
from .llm_providers import LLMProvider
from .llm_providers import create_provider

log = logging.getLogger("categorizer.agent")

LLM_BATCH_SIZE = 10
MIN_ARTICLES_FOR_NEW_SITUATION = 3


def _normalize_situation_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _get_server_params() -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "backend.app.mcp_server.server"],
        env=dict(os.environ),
    )


async def _call_tool(session: ClientSession, name: str, arguments: dict) -> object:
    result = await session.call_tool(name, arguments)
    if not result.content:
        return None
    text = result.content[0].text
    data = json.loads(text)
    if isinstance(data, dict) and "error" in data:
        log.error("MCP tool %s returned error: %s", name, data["error"])
        return None
    return data


def _build_keyword_matches_for_article(
    article: dict,
    situations: list[dict],
    threshold: float,
) -> list[dict]:
    """Deterministic keyword matches against all known situations."""
    matches: list[dict] = []
    title = article.get("title")
    snippet = article.get("snippet")
    for situation in situations:
        sit_id = str(situation.get("id") or "").strip()
        if not sit_id:
            continue
        query = situation.get("query") or situation.get("title") or ""
        match = match_article_to_query(title, snippet, query)
        if match is None or match.relevance_score < threshold:
            continue
        matches.append(
            {
                "situation_id": sit_id,
                "relevance_score": match.relevance_score,
                "reason": match.reason,
            }
        )
    return matches


def _merge_matches(
    deterministic_matches: list[dict],
    llm_matches: list,
    threshold: float,
    valid_situation_ids: set[str],
) -> list[dict]:
    """Merge deterministic and LLM matches, preferring the higher score per situation."""
    merged: dict[str, dict] = {}

    for match in deterministic_matches:
        sit_id = match["situation_id"]
        if sit_id not in valid_situation_ids:
            continue
        if match["relevance_score"] < threshold:
            continue
        merged[sit_id] = {
            "situation_id": sit_id,
            "relevance_score": float(match["relevance_score"]),
            "reason": str(match.get("reason") or ""),
        }

    for match in llm_matches:
        sit_id = str(getattr(match, "situation_id", "") or "").strip()
        score = float(getattr(match, "relevance_score", 0.0))
        reason = str(getattr(match, "reason", "") or "")
        if not sit_id or sit_id not in valid_situation_ids or score < threshold:
            continue

        existing = merged.get(sit_id)
        if existing is None or score > existing["relevance_score"]:
            merged[sit_id] = {
                "situation_id": sit_id,
                "relevance_score": score,
                "reason": reason,
            }

    return sorted(merged.values(), key=lambda m: m["relevance_score"], reverse=True)


async def _discover_and_create_situations(
    session: ClientSession,
    provider: LLMProvider,
    situations: list[dict],
    stats: dict,
) -> None:
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
            "Phase 1 discovery failed; continuing with categorization/backfill using existing situations"
        )
        stats["errors"] += 1
        return

    stats["discovery_proposed"] = len(proposed)
    if not proposed:
        log.info("Phase 1: LLM proposed no new situations")
        return

    valid_input_ids = {str(a.get("id")) for a in article_titles if a.get("id") is not None}
    existing_title_keys = {
        _normalize_situation_title(str(s.get("title") or ""))
        for s in situations
        if s.get("title")
    }

    aggregated: dict[str, dict] = {}
    for ns in proposed:
        key = _normalize_situation_title(ns.title)
        if not key or key in existing_title_keys:
            stats["situations_filtered"] += 1
            continue

        supporting_ids = {
            article_id.strip()
            for article_id in ns.supporting_article_ids
            if article_id and article_id.strip() in valid_input_ids
        }

        if key not in aggregated:
            aggregated[key] = {"info": ns, "article_ids": set()}
        aggregated[key]["article_ids"].update(supporting_ids)

    for key, data in sorted(
        aggregated.items(),
        key=lambda item: len(item[1]["article_ids"]),
        reverse=True,
    ):
        article_count = len(data["article_ids"])
        ns = data["info"]

        if article_count < MIN_ARTICLES_FOR_NEW_SITUATION:
            stats["situations_filtered"] += 1
            log.info(
                "Phase 1: Filtering '%s' - %d validated supporting articles (need %d)",
                ns.title,
                article_count,
                MIN_ARTICLES_FOR_NEW_SITUATION,
            )
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
                stats["errors"] += 1
                log.warning("Failed to create discovered situation %s: %s", ns.title, result)
        except Exception:
            stats["errors"] += 1
            log.exception("Failed to create discovered situation: %s", ns.title)


async def _process_uncategorized_page(
    session: ClientSession,
    provider: LLMProvider,
    situations: list[dict],
    page_articles: list[dict],
    stats: dict,
) -> None:
    """Process one fetched page of uncategorized rows in LLM sub-batches."""
    threshold = settings.categorizer_relevance_threshold
    valid_situation_ids = {str(s.get("id")) for s in situations if s.get("id") is not None}

    if not valid_situation_ids:
        log.warning("Phase 2: No situations available; leaving page uncategorized for retry")
        return

    for i in range(0, len(page_articles), LLM_BATCH_SIZE):
        batch = page_articles[i : i + LLM_BATCH_SIZE]
        stats["uncategorized_llm_batches"] += 1

        # Deterministic matches are always available even if LLM call fails.
        deterministic_matches_by_article = {
            str(article["id"]): _build_keyword_matches_for_article(article, situations, threshold)
            for article in batch
        }

        batch_result = None
        try:
            batch_result = await provider.categorize_batch(batch, situations)
        except Exception:
            stats["errors"] += 1
            log.exception(
                "Phase 2: LLM categorization failed for batch at page offset %d; continuing with deterministic matches",
                i,
            )

        result_by_article_id = {}
        if batch_result is not None:
            if batch_result.new_situations:
                log.warning(
                    "Phase 2: LLM returned %d unexpected new_situations; ignoring them",
                    len(batch_result.new_situations),
                )
            result_by_article_id = {result.article_id: result for result in batch_result.results}

        for article in batch:
            article_id = str(article["id"])
            deterministic_matches = deterministic_matches_by_article.get(article_id, [])
            llm_result = result_by_article_id.get(article_id)
            llm_matches = llm_result.matches if llm_result is not None else []

            if llm_result is None and batch_result is not None:
                log.warning(
                    "Phase 2: LLM omitted article %s; applying deterministic-only matching",
                    article_id,
                )

            valid_matches = _merge_matches(
                deterministic_matches=deterministic_matches,
                llm_matches=llm_matches,
                threshold=threshold,
                valid_situation_ids=valid_situation_ids,
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
                        stats["links_created"] += int(result.get("links_created", 0))
                        stats["articles_processed"] += 1
                    else:
                        stats["errors"] += 1
                        log.warning("categorize_article failed for %s: %s", article_id, result)
                else:
                    result = await _call_tool(
                        session,
                        "mark_article_uncategorizable",
                        {
                            "feed_article_id": article_id,
                            "reason": "No deterministic/LLM matches above threshold",
                        },
                    )
                    if result and result.get("success"):
                        stats["articles_processed"] += 1
                        stats["skipped"] += 1
                    else:
                        stats["errors"] += 1
                        log.warning("mark_article_uncategorizable failed for %s: %s", article_id, result)
            except Exception:
                stats["errors"] += 1
                log.exception("Failed to persist categorization result for article %s", article_id)


async def _run_backfill_progression(session: ClientSession, stats: dict) -> None:
    if not settings.categorizer_backfill_enabled:
        return

    candidates = await _call_tool(
        session,
        "list_backfill_candidates",
        {"limit": settings.categorizer_backfill_max_situations_per_cycle},
    )
    if not candidates or not isinstance(candidates, list):
        return

    for candidate in candidates:
        situation_id = candidate.get("situation_id")
        if not situation_id:
            continue

        result = await _call_tool(
            session,
            "run_situation_backfill_chunk",
            {
                "situation_id": situation_id,
                "chunk_size": settings.categorizer_backfill_chunk_size,
                "write_batch_size": settings.categorizer_backfill_write_batch_size,
            },
        )

        if not result or not result.get("success"):
            stats["errors"] += 1
            log.warning("Backfill chunk failed for %s: %s", situation_id, result)
            continue

        stats["backfill_situations_processed"] += 1
        stats["backfill_articles_scanned"] += int(result.get("scanned", 0))
        stats["backfill_links_created"] += int(result.get("linked", 0))
        if result.get("done"):
            stats["backfill_situations_completed"] += 1


async def run_categorization_cycle(session: ClientSession, provider: LLMProvider) -> dict:
    stats = {
        "articles_processed": 0,
        "links_created": 0,
        "errors": 0,
        "skipped": 0,
        "situations_created": 0,
        "situations_filtered": 0,
        "discovery_articles_scanned": 0,
        "discovery_proposed": 0,
        "uncategorized_fetches": 0,
        "uncategorized_llm_batches": 0,
        "backfill_situations_processed": 0,
        "backfill_situations_completed": 0,
        "backfill_articles_scanned": 0,
        "backfill_links_created": 0,
    }

    situations = await _call_tool(session, "get_all_active_situations", {})
    if not situations or not isinstance(situations, list):
        situations = []
        log.info("No existing situations found")
    else:
        log.info("Found %d existing situations", len(situations))

    await _discover_and_create_situations(session, provider, situations, stats)

    # Drain uncategorized rows page by page so one cycle can clear backlog.
    seen_page_fingerprints: set[tuple[int, str, str]] = set()
    while True:
        page_articles = await _call_tool(
            session,
            "get_uncategorized_articles",
            {
                "limit": settings.categorizer_batch_size,
                "since_hours": settings.categorizer_uncategorized_since_hours,
            },
        )

        if not page_articles or not isinstance(page_articles, list):
            break

        if len(page_articles) == 0:
            break

        first_id = str(page_articles[0].get("id", ""))
        last_id = str(page_articles[-1].get("id", ""))
        fingerprint = (len(page_articles), first_id, last_id)
        if fingerprint in seen_page_fingerprints:
            log.warning(
                "Phase 2: Repeated uncategorized page detected; stopping drain to avoid infinite loop"
            )
            break
        seen_page_fingerprints.add(fingerprint)

        stats["uncategorized_fetches"] += 1
        log.info(
            "Phase 2: Processing uncategorized page %d with %d rows",
            stats["uncategorized_fetches"],
            len(page_articles),
        )
        await _process_uncategorized_page(
            session=session,
            provider=provider,
            situations=situations,
            page_articles=page_articles,
            stats=stats,
        )

    await _run_backfill_progression(session, stats)

    return stats


async def run_agent() -> dict:
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
        "Cycle complete: %d processed, %d links, %d skipped, %d errors, "
        "%d new situations, %d filtered, %d discovery titles, %d proposed, "
        "%d uncategorized pages, %d llm batches, %d backfill situations, %d backfill done, "
        "%d backfill scanned, %d backfill links",
        stats.get("articles_processed", 0),
        stats.get("links_created", 0),
        stats.get("skipped", 0),
        stats.get("errors", 0),
        stats.get("situations_created", 0),
        stats.get("situations_filtered", 0),
        stats.get("discovery_articles_scanned", 0),
        stats.get("discovery_proposed", 0),
        stats.get("uncategorized_fetches", 0),
        stats.get("uncategorized_llm_batches", 0),
        stats.get("backfill_situations_processed", 0),
        stats.get("backfill_situations_completed", 0),
        stats.get("backfill_articles_scanned", 0),
        stats.get("backfill_links_created", 0),
    )
    return stats
