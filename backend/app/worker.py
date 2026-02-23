"""
RSS Feed Worker — fetches feeds every 30 minutes, stores metadata only.
Uses ON CONFLICT DO UPDATE on original_url so changed feed entries are refreshed.
"""

import html
import logging
import re
import signal
import sys
from datetime import datetime, timezone
from time import struct_time

import feedparser
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .database import SessionLocal, engine
from .models import FeedArticle, FeedSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rss-worker")

MAX_SNIPPET_LEN = 300


def struct_to_datetime(t: struct_time | None) -> datetime | None:
    if t is None:
        return None
    try:
        return datetime(*t[:6], tzinfo=timezone.utc)
    except Exception:
        return None


def strip_html(raw: str | None) -> str | None:
    if not raw:
        return None
    text = re.sub(r"<[^>]+>", "", raw)
    text = html.unescape(text).strip()
    return text[:MAX_SNIPPET_LEN] if text else None


def extract_thumbnail(entry) -> str | None:
    # Try media:thumbnail
    thumbs = getattr(entry, "media_thumbnail", None)
    if thumbs and isinstance(thumbs, list) and thumbs[0].get("url"):
        return thumbs[0]["url"]

    # Try media:content with image type
    media = getattr(entry, "media_content", None)
    if media and isinstance(media, list):
        for m in media:
            if "image" in m.get("type", "") or m.get("medium") == "image":
                return m.get("url")

    # Try enclosures with image type
    enclosures = getattr(entry, "enclosures", None)
    if enclosures and isinstance(enclosures, list):
        for enc in enclosures:
            if "image" in enc.get("type", ""):
                return enc.get("href") or enc.get("url")

    return None


def fetch_single_feed(feed_source: FeedSource) -> int:
    """Fetch one RSS feed and insert articles. Returns count of new articles."""
    log.info("Fetching: %s (%s)", feed_source.name, feed_source.rss_url)

    parsed = feedparser.parse(feed_source.rss_url)

    if parsed.bozo and not parsed.entries:
        log.warning(
            "Feed %s returned an error: %s",
            feed_source.name,
            getattr(parsed, "bozo_exception", "unknown"),
        )
        return 0

    rows = []
    for entry in parsed.entries:
        url = getattr(entry, "link", None)
        title = getattr(entry, "title", None)
        if not url or not title:
            continue

        rows.append(
            {
                "feed_source_id": feed_source.id,
                "title": title[:500],
                "original_url": url,
                "snippet": strip_html(getattr(entry, "summary", None)),
                "author": getattr(entry, "author", None),
                "published_date": struct_to_datetime(
                    getattr(entry, "published_parsed", None)
                ),
                "thumbnail_url": extract_thumbnail(entry),
            }
        )

    if not rows:
        log.info("  No entries found for %s", feed_source.name)
        return 0

    db = SessionLocal()
    try:
        insert_stmt = pg_insert(FeedArticle).values(rows)
        excluded = insert_stmt.excluded
        change_predicate = or_(
            FeedArticle.feed_source_id.is_distinct_from(excluded.feed_source_id),
            FeedArticle.title.is_distinct_from(excluded.title),
            FeedArticle.snippet.is_distinct_from(excluded.snippet),
            FeedArticle.author.is_distinct_from(excluded.author),
            FeedArticle.published_date.is_distinct_from(excluded.published_date),
            FeedArticle.thumbnail_url.is_distinct_from(excluded.thumbnail_url),
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["original_url"],
            set_={
                "feed_source_id": excluded.feed_source_id,
                "title": excluded.title,
                "snippet": excluded.snippet,
                "author": excluded.author,
                "published_date": excluded.published_date,
                "thumbnail_url": excluded.thumbnail_url,
                # Treat changed rows as newly ingested so they surface in recent views.
                "ingested_at": func.now(),
                # Re-categorize rows whose content/metadata changed.
                "categorized_at": None,
            },
            where=change_predicate,
        )
        result = db.execute(stmt)
        changed_count = int(result.rowcount or 0)

        # Update last_fetched_at
        feed_source_row = db.scalar(
            select(FeedSource).where(FeedSource.id == feed_source.id)
        )
        if feed_source_row:
            feed_source_row.last_fetched_at = datetime.now(timezone.utc)

        db.commit()
        log.info(
            "  %s: %d inserted/updated articles (of %d entries)",
            feed_source.name,
            changed_count,
            len(rows),
        )
        return changed_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def fetch_all_feeds():
    """Main job: loop through all active feed sources and fetch each one."""
    log.info("=== Starting RSS fetch cycle ===")
    db = SessionLocal()
    try:
        sources = db.scalars(
            select(FeedSource).where(FeedSource.is_active == True)  # noqa: E712
        ).all()
    finally:
        db.close()

    if not sources:
        log.info("No active feed sources configured. Skipping.")
        return

    total_new = 0
    errors = 0
    for source in sources:
        try:
            total_new += fetch_single_feed(source)
        except Exception:
            errors += 1
            log.exception("Error fetching feed: %s (%s)", source.name, source.rss_url)

    log.info(
        "=== Fetch cycle complete: %d sources, %d new articles, %d errors ===",
        len(sources),
        total_new,
        errors,
    )


def main():
    # Verify database connectivity
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        log.info("Database connection OK")
    except Exception:
        log.exception("Cannot connect to database, exiting")
        sys.exit(1)

    # Run immediately on startup
    fetch_all_feeds()

    # Schedule every 30 minutes
    scheduler = BlockingScheduler()
    scheduler.add_job(fetch_all_feeds, "interval", minutes=30)
    log.info("Scheduler started — fetching every 30 minutes")

    def shutdown(signum, frame):
        log.info("Shutting down scheduler...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    scheduler.start()


if __name__ == "__main__":
    main()
