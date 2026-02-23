"""
MCP Server for News Situation Dashboard.

Exposes database read/write operations as MCP tools so an LLM agent
can discover articles, read situations, and write categorization results.

Run standalone: python -m backend.app.mcp_server.server
"""

from __future__ import annotations

import asyncio
import json
import logging

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..database import SessionLocal
from . import db_tools

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("mcp-server")

app = Server("news-dashboard-categorizer")


TOOLS = [
    Tool(
        name="get_uncategorized_articles",
        description=(
            "Fetch feed articles that have not been categorized yet. "
            "Returns articles from active feeds ingested within the given time window."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max articles to return (default 50)",
                    "default": 50,
                },
                "since_hours": {
                    "type": "integer",
                    "description": "Only articles ingested within this many hours (0 means no cutoff)",
                    "default": 0,
                },
            },
        },
    ),
    Tool(
        name="get_all_articles_titles",
        description=(
            "Fetch compact feed article IDs + titles from active feeds for "
            "situation discovery over a bounded time window."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max article titles to return (default 1000)",
                    "default": 1000,
                },
                "since_hours": {
                    "type": "integer",
                    "description": "Only titles ingested within this many hours (default 336)",
                    "default": 336,
                },
            },
        },
    ),
    Tool(
        name="get_all_active_situations",
        description=(
            "Return all active situations (topics/queries) across all users. "
            "Each situation has an id, title, description, query, and user_id."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="get_article_detail",
        description="Get full details for a single feed article by its ID.",
        inputSchema={
            "type": "object",
            "properties": {
                "feed_article_id": {
                    "type": "string",
                    "description": "UUID of the feed article",
                },
            },
            "required": ["feed_article_id"],
        },
    ),
    Tool(
        name="categorize_article",
        description=(
            "Link a feed article to one or more situations with relevance scores. "
            "Creates the article and source records if they don't exist, "
            "then creates situation_article join rows."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "feed_article_id": {
                    "type": "string",
                    "description": "UUID of the feed article to categorize",
                },
                "situation_matches": {
                    "type": "array",
                    "description": "List of situation matches",
                    "items": {
                        "type": "object",
                        "properties": {
                            "situation_id": {"type": "string"},
                            "relevance_score": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                            },
                            "reason": {"type": "string"},
                        },
                        "required": ["situation_id", "relevance_score", "reason"],
                    },
                },
                "llm_model": {
                    "type": "string",
                    "description": "Identifier of the LLM model that performed categorization",
                },
            },
            "required": ["feed_article_id", "situation_matches", "llm_model"],
        },
    ),
    Tool(
        name="mark_article_uncategorizable",
        description=(
            "Mark a feed article as processed but not matching any situation. "
            "Sets categorized_at so it won't be re-processed."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "feed_article_id": {
                    "type": "string",
                    "description": "UUID of the feed article",
                },
                "reason": {
                    "type": "string",
                    "description": "Why no situations matched",
                },
            },
            "required": ["feed_article_id", "reason"],
        },
    ),
    Tool(
        name="enqueue_situation_backfill",
        description=(
            "Queue or reset keyword backfill processing for a situation. "
            "Used internally after situation create/update."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "situation_id": {
                    "type": "string",
                    "description": "UUID of the situation",
                },
                "reset": {
                    "type": "boolean",
                    "description": "If true, reset cursor/counters before reprocessing",
                    "default": False,
                },
            },
            "required": ["situation_id"],
        },
    ),
    Tool(
        name="list_backfill_candidates",
        description=(
            "List pending/running backfill jobs ordered by oldest update first."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Max jobs to return (default 20)",
                    "default": 20,
                },
            },
        },
    ),
    Tool(
        name="run_situation_backfill_chunk",
        description=(
            "Process one chunk of feed history for one situation using "
            "deterministic keyword matching."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "situation_id": {
                    "type": "string",
                    "description": "UUID of the situation",
                },
                "chunk_size": {
                    "type": "integer",
                    "description": "Max feed rows to scan this run (default 500)",
                    "default": 500,
                },
                "write_batch_size": {
                    "type": "integer",
                    "description": "Commit interval while processing rows (default 50)",
                    "default": 50,
                },
            },
            "required": ["situation_id"],
        },
    ),
    Tool(
        name="enqueue_all_active_situation_backfills",
        description=(
            "Queue (or reset) backfill state for all active situations. "
            "Useful for one-time bootstrap after deployment."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "reset": {
                    "type": "boolean",
                    "description": "If true, reset existing cursors/counters",
                    "default": False,
                },
            },
        },
    ),
    Tool(
        name="create_situation",
        description=(
            "Create a new situation (topic) discovered by the LLM from article analysis. "
            "Owned by the admin user. Returns the new situation's UUID. "
            "If a situation with the same title already exists, returns its ID instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Concise topic title (e.g. 'US-China Trade War')",
                },
                "description": {
                    "type": "string",
                    "description": "1-2 sentence description of this news situation",
                },
                "query": {
                    "type": "string",
                    "description": "Search keywords for this topic",
                },
            },
            "required": ["title", "description", "query"],
        },
    ),
]


@app.list_tools()
async def list_tools() -> list[Tool]:
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    db = SessionLocal()
    try:
        result = _dispatch(db, name, arguments)
        return [TextContent(type="text", text=json.dumps(result))]
    except Exception as e:
        log.exception("Tool %s failed", name)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
    finally:
        db.close()


def _dispatch(db, name: str, arguments: dict) -> object:
    if name == "get_uncategorized_articles":
        return db_tools.get_uncategorized_articles(
            db,
            limit=arguments.get("limit", 50),
            since_hours=arguments.get("since_hours", 0),
        )
    elif name == "get_all_articles_titles":
        return db_tools.get_all_articles_titles(
            db,
            limit=arguments.get("limit", 1000),
            since_hours=arguments.get("since_hours", 336),
        )
    elif name == "get_all_active_situations":
        return db_tools.get_all_active_situations(db)
    elif name == "get_article_detail":
        return db_tools.get_article_detail(db, arguments["feed_article_id"])
    elif name == "categorize_article":
        return db_tools.categorize_article(
            db,
            feed_article_id=arguments["feed_article_id"],
            situation_matches=arguments["situation_matches"],
            llm_model=arguments["llm_model"],
        )
    elif name == "mark_article_uncategorizable":
        return db_tools.mark_article_uncategorizable(
            db,
            feed_article_id=arguments["feed_article_id"],
            reason=arguments["reason"],
        )
    elif name == "enqueue_situation_backfill":
        return db_tools.enqueue_situation_backfill(
            db,
            situation_id=arguments["situation_id"],
            reset=arguments.get("reset", False),
        )
    elif name == "list_backfill_candidates":
        return db_tools.list_backfill_candidates(
            db,
            limit=arguments.get("limit", 20),
        )
    elif name == "run_situation_backfill_chunk":
        return db_tools.run_situation_backfill_chunk(
            db,
            situation_id=arguments["situation_id"],
            chunk_size=arguments.get("chunk_size", 500),
            write_batch_size=arguments.get("write_batch_size", 50),
        )
    elif name == "enqueue_all_active_situation_backfills":
        return db_tools.enqueue_all_active_situation_backfills(
            db,
            reset=arguments.get("reset", False),
        )
    elif name == "create_situation":
        return db_tools.create_situation(
            db,
            title=arguments["title"],
            description=arguments["description"],
            query=arguments["query"],
        )
    else:
        raise ValueError(f"Unknown tool: {name}")


async def main():
    log.info("Starting MCP server (stdio transport)")
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
