"""
Database operations backing the MCP server tools.

Pure SQLAlchemy - no MCP dependency here, making these functions
portable across MCP and REST paths.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..categorizer.keyword_matcher import match_article_to_query
from ..config import settings
from ..models import (
    AppUser,
    Article,
    FeedArticle,
    FeedSource,
    Situation,
    SituationArticle,
    SituationBackfillState,
    Source,
)

KEYWORD_BACKFILL_MODEL = "keyword-backfill-v1"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def normalize_situation_title(title: str) -> str:
    """Normalize a title for dedupe checks (case/punctuation/whitespace-insensitive)."""
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


def _get_or_create_source(db: Session, source_name: str) -> Source:
    source = db.scalar(select(Source).where(Source.name == source_name, Source.base_url == ""))
    if source is not None:
        return source

    source = Source(name=source_name, base_url="", source_type="rss")
    try:
        with db.begin_nested():
            db.add(source)
            db.flush()
    except IntegrityError:
        # Handle concurrent insert race on UNIQUE(name, base_url).
        source = db.scalar(select(Source).where(Source.name == source_name, Source.base_url == ""))
        if source is None:
            raise
    return source


def _get_or_create_article_from_feed(db: Session, fa: FeedArticle, source: Source) -> Article:
    article = db.scalar(select(Article).where(Article.url == fa.original_url))
    if article is not None:
        return article

    article = Article(
        source_id=source.id,
        url=fa.original_url,
        title=fa.title,
        author=fa.author,
        published_at=fa.published_date,
        summary=fa.snippet,
    )
    try:
        with db.begin_nested():
            db.add(article)
            db.flush()
    except IntegrityError:
        # Handle concurrent insert race on UNIQUE(article.url).
        article = db.scalar(select(Article).where(Article.url == fa.original_url))
        if article is None:
            raise
    return article


def get_uncategorized_articles(
    db: Session,
    *,
    limit: int = 50,
    since_hours: int = 0,
) -> list[dict]:
    """Fetch feed articles that have not been categorized yet."""
    stmt = (
        select(
            FeedArticle.id,
            FeedArticle.title,
            FeedArticle.snippet,
            FeedArticle.original_url,
            FeedArticle.author,
            FeedArticle.published_date,
            FeedSource.name.label("feed_source_name"),
        )
        .join(FeedSource, FeedArticle.feed_source_id == FeedSource.id)
        .where(
            FeedArticle.categorized_at.is_(None),
            FeedSource.is_active.is_(True),
        )
    )

    if since_hours > 0:
        cutoff = _now_utc() - timedelta(hours=since_hours)
        stmt = stmt.where(FeedArticle.ingested_at >= cutoff)

    stmt = stmt.order_by(FeedArticle.ingested_at.desc(), FeedArticle.id.desc()).limit(limit)

    rows = db.execute(stmt).all()
    return [
        {
            "id": str(row.id),
            "title": row.title,
            "snippet": row.snippet,
            "url": row.original_url,
            "author": row.author,
            "published_date": row.published_date.isoformat() if row.published_date else None,
            "feed_source_name": row.feed_source_name,
        }
        for row in rows
    ]


def get_all_articles_titles(
    db: Session,
    *,
    limit: int = 1000,
    since_hours: int = 336,
) -> list[dict]:
    """Fetch compact article IDs + titles from active feeds for situation discovery."""
    stmt = (
        select(FeedArticle.id, FeedArticle.title)
        .join(FeedSource, FeedArticle.feed_source_id == FeedSource.id)
        .where(FeedSource.is_active.is_(True))
    )
    if since_hours > 0:
        cutoff = _now_utc() - timedelta(hours=since_hours)
        stmt = stmt.where(FeedArticle.ingested_at >= cutoff)

    stmt = stmt.order_by(FeedArticle.ingested_at.desc(), FeedArticle.id.desc()).limit(limit)
    rows = db.execute(stmt).all()
    return [{"id": str(row.id), "title": row.title} for row in rows]


def get_all_active_situations(db: Session) -> list[dict]:
    """Return all active situations across all users."""
    stmt = select(Situation).where(Situation.is_active.is_(True))
    situations = db.scalars(stmt).all()
    return [
        {
            "id": str(s.id),
            "title": s.title,
            "description": s.description,
            "query": s.query,
            "user_id": str(s.user_id),
        }
        for s in situations
    ]


def get_article_detail(db: Session, feed_article_id: str) -> dict | None:
    """Get full details for a single feed article."""
    fa = db.get(FeedArticle, uuid.UUID(feed_article_id))
    if fa is None:
        return None
    fs = db.get(FeedSource, fa.feed_source_id)
    return {
        "id": str(fa.id),
        "title": fa.title,
        "snippet": fa.snippet,
        "url": fa.original_url,
        "author": fa.author,
        "published_date": fa.published_date.isoformat() if fa.published_date else None,
        "thumbnail_url": fa.thumbnail_url,
        "feed_source_name": fs.name if fs else None,
        "feed_source_category": fs.category if fs else None,
    }


def categorize_article(
    db: Session,
    feed_article_id: str,
    situation_matches: list[dict],
    llm_model: str,
) -> dict:
    """
    Create article + source records and link to situations.

    Each item in situation_matches: {situation_id, relevance_score, reason}
    Returns {success, article_id, links_created}.
    """
    fa = db.get(FeedArticle, uuid.UUID(feed_article_id))
    if fa is None:
        return {"success": False, "error": "Feed article not found"}

    fs = db.get(FeedSource, fa.feed_source_id)
    source_name = fs.name if fs else "Unknown"
    source = _get_or_create_source(db, source_name)
    article = _get_or_create_article_from_feed(db, fa, source)

    links_created = 0
    skipped_invalid_situations = 0
    for match in situation_matches:
        try:
            sit_id = uuid.UUID(match["situation_id"])
        except (ValueError, TypeError, KeyError):
            skipped_invalid_situations += 1
            continue

        situation = db.get(Situation, sit_id)
        if situation is None:
            skipped_invalid_situations += 1
            continue

        existing = db.get(SituationArticle, (sit_id, article.id))
        if existing is None:
            db.add(
                SituationArticle(
                    situation_id=sit_id,
                    article_id=article.id,
                    relevance_score=match["relevance_score"],
                    reason=match["reason"],
                    llm_model=llm_model,
                )
            )
            links_created += 1

    fa.categorized_at = _now_utc()
    db.commit()

    return {
        "success": True,
        "article_id": str(article.id),
        "links_created": links_created,
        "skipped_invalid_situations": skipped_invalid_situations,
    }


def mark_article_uncategorizable(db: Session, feed_article_id: str, reason: str) -> dict:
    """Mark a feed article as processed but not matching any situation."""
    fa = db.get(FeedArticle, uuid.UUID(feed_article_id))
    if fa is None:
        return {"success": False, "error": "Feed article not found"}

    fa.categorized_at = _now_utc()
    db.commit()
    return {"success": True}


def enqueue_situation_backfill(
    db: Session,
    situation_id: str,
    reset: bool = False,
) -> dict:
    """Create/update backfill state so a situation is processed incrementally."""
    try:
        sit_uuid = uuid.UUID(situation_id)
    except ValueError:
        return {"success": False, "error": "Invalid situation_id"}

    situation = db.get(Situation, sit_uuid)
    if situation is None:
        return {"success": False, "error": "Situation not found"}

    state = db.get(SituationBackfillState, sit_uuid)
    now = _now_utc()

    if state is None:
        state = SituationBackfillState(
            situation_id=sit_uuid,
            status="pending",
            updated_at=now,
            completed_at=None,
            last_error=None,
            processed_count=0,
            linked_count=0,
        )
        db.add(state)
    elif reset:
        state.status = "pending"
        state.cursor_ingested_at = None
        state.cursor_feed_article_id = None
        state.processed_count = 0
        state.linked_count = 0
        state.last_error = None
        state.completed_at = None
        state.updated_at = now
    else:
        if state.status == "failed":
            state.status = "pending"
        state.last_error = None
        state.updated_at = now

    db.commit()
    return {
        "success": True,
        "situation_id": str(sit_uuid),
        "status": state.status,
        "reset": reset,
    }


def list_backfill_candidates(db: Session, *, limit: int = 20) -> list[dict]:
    """List pending/running backfill jobs ordered by oldest update first."""
    stmt = (
        select(SituationBackfillState, Situation)
        .join(Situation, Situation.id == SituationBackfillState.situation_id)
        .where(
            Situation.is_active.is_(True),
            SituationBackfillState.status.in_(("pending", "running")),
        )
        .order_by(SituationBackfillState.updated_at.asc())
        .limit(limit)
    )

    rows = db.execute(stmt).all()
    return [
        {
            "situation_id": str(state.situation_id),
            "title": situation.title,
            "query": situation.query,
            "status": state.status,
            "cursor_ingested_at": state.cursor_ingested_at.isoformat()
            if state.cursor_ingested_at
            else None,
            "cursor_feed_article_id": str(state.cursor_feed_article_id)
            if state.cursor_feed_article_id
            else None,
            "processed_count": state.processed_count,
            "linked_count": state.linked_count,
        }
        for state, situation in rows
    ]


def enqueue_all_active_situation_backfills(db: Session, *, reset: bool = False) -> dict:
    """Queue backfill work for all active situations (bootstrap helper)."""
    situation_ids = db.scalars(
        select(Situation.id).where(Situation.is_active.is_(True))
    ).all()

    queued = 0
    failed = 0
    for sit_id in situation_ids:
        result = enqueue_situation_backfill(db, str(sit_id), reset=reset)
        if result.get("success"):
            queued += 1
        else:
            failed += 1

    status_rows = db.scalars(
        select(SituationBackfillState.status)
        .join(Situation, Situation.id == SituationBackfillState.situation_id)
        .where(Situation.is_active.is_(True))
    ).all()
    status_counts = {
        "pending": sum(1 for s in status_rows if s == "pending"),
        "running": sum(1 for s in status_rows if s == "running"),
        "done": sum(1 for s in status_rows if s == "done"),
        "failed": sum(1 for s in status_rows if s == "failed"),
    }

    return {
        "success": failed == 0,
        "queued": queued,
        "failed": failed,
        "total_active": len(situation_ids),
        "status_counts": status_counts,
        "reset": reset,
    }


def run_situation_backfill_chunk(
    db: Session,
    situation_id: str,
    *,
    chunk_size: int = 500,
    write_batch_size: int = 50,
) -> dict:
    """Scan one chunk of feed history and link deterministic keyword matches."""
    scanned = 0
    linked = 0

    try:
        sit_uuid = uuid.UUID(situation_id)
    except ValueError:
        return {"success": False, "error": "Invalid situation_id"}

    situation = db.get(Situation, sit_uuid)
    if situation is None:
        return {"success": False, "error": "Situation not found"}

    state = db.get(SituationBackfillState, sit_uuid)
    if state is None:
        state = SituationBackfillState(
            situation_id=sit_uuid,
            status="pending",
            processed_count=0,
            linked_count=0,
            updated_at=_now_utc(),
        )
        db.add(state)
        db.commit()

    chunk_size = max(1, int(chunk_size))
    write_batch_size = max(1, int(write_batch_size))

    try:
        state.status = "running"
        state.completed_at = None
        state.last_error = None
        state.updated_at = _now_utc()
        db.commit()

        stmt = (
            select(FeedArticle, FeedSource.name.label("feed_source_name"))
            .join(FeedSource, FeedArticle.feed_source_id == FeedSource.id)
            .where(FeedSource.is_active.is_(True))
        )

        if state.cursor_ingested_at is not None and state.cursor_feed_article_id is not None:
            stmt = stmt.where(
                or_(
                    FeedArticle.ingested_at < state.cursor_ingested_at,
                    and_(
                        FeedArticle.ingested_at == state.cursor_ingested_at,
                        FeedArticle.id < state.cursor_feed_article_id,
                    ),
                )
            )
        elif state.cursor_ingested_at is not None:
            stmt = stmt.where(FeedArticle.ingested_at < state.cursor_ingested_at)

        stmt = stmt.order_by(FeedArticle.ingested_at.desc(), FeedArticle.id.desc()).limit(chunk_size)
        rows = db.execute(stmt).all()

        if not rows:
            state.status = "done"
            state.completed_at = _now_utc()
            state.updated_at = _now_utc()
            db.commit()
            return {
                "success": True,
                "situation_id": str(sit_uuid),
                "done": True,
                "scanned": 0,
                "linked": 0,
                "processed_count": state.processed_count,
                "linked_count": state.linked_count,
                "next_cursor_ingested_at": None,
                "next_cursor_feed_article_id": None,
            }

        pending_scanned = 0
        pending_linked = 0
        last_cursor_ingested_at: datetime | None = None
        last_cursor_feed_article_id: uuid.UUID | None = None

        for fa, feed_source_name in rows:
            scanned += 1
            pending_scanned += 1
            last_cursor_ingested_at = fa.ingested_at
            last_cursor_feed_article_id = fa.id

            match = match_article_to_query(fa.title, fa.snippet, situation.query or situation.title)
            if match is not None:
                source = _get_or_create_source(db, feed_source_name or "Unknown")
                article = _get_or_create_article_from_feed(db, fa, source)
                existing_link = db.get(SituationArticle, (sit_uuid, article.id))
                if existing_link is None:
                    db.add(
                        SituationArticle(
                            situation_id=sit_uuid,
                            article_id=article.id,
                            relevance_score=match.relevance_score,
                            reason=match.reason,
                            llm_model=KEYWORD_BACKFILL_MODEL,
                        )
                    )
                    linked += 1
                    pending_linked += 1

            if pending_scanned >= write_batch_size:
                state.cursor_ingested_at = last_cursor_ingested_at
                state.cursor_feed_article_id = last_cursor_feed_article_id
                state.processed_count += pending_scanned
                state.linked_count += pending_linked
                state.updated_at = _now_utc()
                db.commit()
                pending_scanned = 0
                pending_linked = 0

        if last_cursor_ingested_at is not None and last_cursor_feed_article_id is not None:
            state.cursor_ingested_at = last_cursor_ingested_at
            state.cursor_feed_article_id = last_cursor_feed_article_id

        if pending_scanned > 0:
            state.processed_count += pending_scanned
            state.linked_count += pending_linked

        state.status = "pending"
        state.updated_at = _now_utc()
        db.commit()

        return {
            "success": True,
            "situation_id": str(sit_uuid),
            "done": False,
            "scanned": scanned,
            "linked": linked,
            "processed_count": state.processed_count,
            "linked_count": state.linked_count,
            "next_cursor_ingested_at": state.cursor_ingested_at.isoformat()
            if state.cursor_ingested_at
            else None,
            "next_cursor_feed_article_id": str(state.cursor_feed_article_id)
            if state.cursor_feed_article_id
            else None,
        }
    except Exception as exc:
        db.rollback()
        state = db.get(SituationBackfillState, sit_uuid)
        if state is None:
            state = SituationBackfillState(situation_id=sit_uuid, processed_count=0, linked_count=0)
            db.add(state)
        state.status = "failed"
        state.last_error = str(exc)[:2000]
        state.updated_at = _now_utc()
        db.commit()
        return {
            "success": False,
            "error": str(exc),
            "situation_id": str(sit_uuid),
            "done": False,
            "scanned": scanned,
            "linked": linked,
        }


def create_situation(
    db: Session,
    title: str,
    description: str,
    query: str,
) -> dict:
    """Create a new situation owned by the admin user (for LLM-discovered topics)."""
    clean_title = title.strip()
    if not clean_title:
        return {"success": False, "error": "Title cannot be empty"}

    normalized_title = normalize_situation_title(clean_title)
    if not normalized_title:
        return {"success": False, "error": "Title cannot be empty"}

    admin_user = None
    if settings.admin_email:
        admin_user = db.scalar(select(AppUser).where(AppUser.email == settings.admin_email))
    if admin_user is None:
        admin_user = db.scalar(select(AppUser).order_by(AppUser.created_at.asc()))
    if admin_user is None:
        return {"success": False, "error": "No users in database"}

    existing = db.scalar(
        select(Situation).where(
            Situation.is_active.is_(True),
            func.regexp_replace(
                func.lower(Situation.title),
                r"[^a-z0-9]+",
                "",
                "g",
            )
            == normalized_title,
        )
    )
    if existing:
        enqueue_situation_backfill(db, str(existing.id), reset=False)
        return {
            "success": True,
            "situation_id": str(existing.id),
            "already_existed": True,
        }

    situation = Situation(
        user_id=admin_user.id,
        title=clean_title,
        description=description,
        query=query,
        is_active=True,
        llm_created=True,
    )
    db.add(situation)
    db.commit()
    enqueue_situation_backfill(db, str(situation.id), reset=True)
    return {
        "success": True,
        "situation_id": str(situation.id),
        "already_existed": False,
    }
