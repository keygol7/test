"""
Database operations backing the MCP server tools.

Pure SQLAlchemy — no MCP dependency here, making these functions
testable independently of the MCP transport layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..models import (
    AppUser,
    Article,
    FeedArticle,
    FeedSource,
    Situation,
    SituationArticle,
    Source,
)
from ..config import settings


def get_uncategorized_articles(
    db: Session, *, limit: int = 50, since_hours: int = 24
) -> list[dict]:
    """Fetch feed articles that haven't been categorized yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

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
            FeedArticle.ingested_at >= cutoff,
            FeedSource.is_active.is_(True),
        )
        .order_by(FeedArticle.ingested_at.desc())
        .limit(limit)
    )

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

    # Get or create Source
    source_name = fs.name if fs else "Unknown"
    source = db.scalar(
        select(Source).where(Source.name == source_name, Source.base_url == "")
    )
    if source is None:
        source = Source(name=source_name, base_url="", source_type="rss")
        db.add(source)
        db.flush()

    # Get or create Article
    article = db.scalar(select(Article).where(Article.url == fa.original_url))
    if article is None:
        article = Article(
            source_id=source.id,
            url=fa.original_url,
            title=fa.title,
            author=fa.author,
            published_at=fa.published_date,
            summary=fa.snippet,
        )
        db.add(article)
        db.flush()

    # Create situation_article links
    links_created = 0
    for match in situation_matches:
        sit_id = uuid.UUID(match["situation_id"])
        existing = db.get(SituationArticle, (sit_id, article.id))
        if existing is None:
            sa = SituationArticle(
                situation_id=sit_id,
                article_id=article.id,
                relevance_score=match["relevance_score"],
                reason=match["reason"],
                llm_model=llm_model,
            )
            db.add(sa)
            links_created += 1

    # Mark feed article as categorized
    fa.categorized_at = datetime.now(timezone.utc)
    db.commit()

    return {
        "success": True,
        "article_id": str(article.id),
        "links_created": links_created,
    }


def mark_article_uncategorizable(
    db: Session, feed_article_id: str, reason: str
) -> dict:
    """Mark a feed article as processed but not matching any situation."""
    fa = db.get(FeedArticle, uuid.UUID(feed_article_id))
    if fa is None:
        return {"success": False, "error": "Feed article not found"}

    fa.categorized_at = datetime.now(timezone.utc)
    db.commit()
    return {"success": True}


def create_situation(
    db: Session,
    title: str,
    description: str,
    query: str,
) -> dict:
    """Create a new situation owned by the admin user (for LLM-discovered topics)."""
    # Find admin user by email, or fall back to first user
    admin_user = None
    if settings.admin_email:
        admin_user = db.scalar(
            select(AppUser).where(AppUser.email == settings.admin_email)
        )
    if admin_user is None:
        admin_user = db.scalar(select(AppUser).order_by(AppUser.created_at.asc()))
    if admin_user is None:
        return {"success": False, "error": "No users in database"}

    # Check for existing situation with the same title (avoid duplicates)
    existing = db.scalar(
        select(Situation).where(
            func.lower(Situation.title) == title.lower(),
            Situation.is_active.is_(True),
        )
    )
    if existing:
        return {
            "success": True,
            "situation_id": str(existing.id),
            "already_existed": True,
        }

    situation = Situation(
        user_id=admin_user.id,
        title=title,
        description=description,
        query=query,
        is_active=True,
        llm_created=True,
    )
    db.add(situation)
    db.commit()
    return {
        "success": True,
        "situation_id": str(situation.id),
        "already_existed": False,
    }
