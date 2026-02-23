from datetime import datetime, timezone
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import desc, distinct, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import (
    create_access_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from .config import settings
from .database import get_db
from .mcp_server.db_tools import enqueue_situation_backfill
from .models import AppUser, Article, DashboardSnapshot, FeedArticle, FeedSource, Situation, SituationArticle, Source
from .worker import fetch_single_feed

from .schemas import (
    ArticleIngest,
    ArticleRead,
    DashboardRead,
    FeedArticleRead,
    FeedSourceCreate,
    FeedSourceRead,
    LoginRequest,
    CreateFromSuggestion,
    SituationSuggestion,
    SituationArticleRead,
    SituationCreate,
    SituationRead,
    SituationUpdate,
    TokenResponse,
    UserCreate,
    UserRead,
    UserRegister,
)

app = FastAPI(title=settings.app_name, version="0.1.0")

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def serialize_article(article: Article) -> ArticleRead:
    return ArticleRead(
        id=article.id,
        source_id=article.source_id,
        external_id=article.external_id,
        url=article.url,
        title=article.title,
        author=article.author,
        published_at=article.published_at,
        summary=article.summary,
        content=article.content,
        sentiment_score=float(article.sentiment_score) if article.sentiment_score is not None else None,
        metadata=article.extra_metadata or {},
        ingested_at=article.ingested_at,
    )


def require_user(db: Session, user_id: UUID) -> AppUser:
    user = db.scalar(select(AppUser).where(AppUser.id == user_id))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def require_situation(db: Session, situation_id: UUID) -> Situation:
    situation = db.scalar(select(Situation).where(Situation.id == situation_id))
    if not situation:
        raise HTTPException(status_code=404, detail="Situation not found")
    return situation


def require_situations(db: Session, situation_ids: list[UUID]) -> list[Situation]:
    if not situation_ids:
        return []

    unique_ids = list(dict.fromkeys(situation_ids))
    rows = db.scalars(select(Situation).where(Situation.id.in_(unique_ids))).all()
    by_id = {row.id: row for row in rows}
    missing = [str(sid) for sid in unique_ids if sid not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail=f"Situation(s) not found: {', '.join(missing)}")
    return [by_id[sid] for sid in unique_ids]


def require_situation_access(
    db: Session, situation_id: UUID, current_user: AppUser
) -> Situation:
    situation = require_situation(db, situation_id)
    if not current_user.is_admin and situation.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return situation


def get_or_create_source(db: Session, payload: ArticleIngest) -> Source:
    base_url = payload.base_url or ""
    source = db.scalar(
        select(Source).where(Source.name == payload.source_name, Source.base_url == base_url)
    )
    if source:
        return source

    source = Source(name=payload.source_name, base_url=base_url, source_type=payload.source_type)
    db.add(source)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        source = db.scalar(
            select(Source).where(Source.name == payload.source_name, Source.base_url == base_url)
        )
        if source is None:
            raise HTTPException(status_code=500, detail="Could not create or find source")
    return source


# ── Auth Endpoints ──────────────────────────────────────────────


@app.post("/auth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserRegister, db: Session = Depends(get_db)) -> dict:
    existing = db.scalar(select(AppUser).where(AppUser.email == payload.email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already exists")

    user_count = db.scalar(select(func.count()).select_from(AppUser))
    make_admin = bool(
        (user_count == 0)
        or (settings.admin_email and payload.email.lower() == settings.admin_email.lower())
    )

    user = AppUser(
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
        is_admin=make_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(user.id, user.is_admin)
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.post("/auth/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(AppUser).where(AppUser.email == payload.email))
    if not user or not user.password_hash or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = create_access_token(user.id, user.is_admin)
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/auth/me", response_model=UserRead)
def get_me(current_user: AppUser = Depends(get_current_user)) -> AppUser:
    return current_user


# ── Health ──────────────────────────────────────────────────────


@app.get("/health")
def healthcheck(db: Session = Depends(get_db)) -> dict[str, str]:
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database unavailable")
    return {"status": "ok", "service": settings.app_name}


# ── Users (Admin only) ─────────────────────────────────────────


@app.post("/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> AppUser:
    existing = db.scalar(select(AppUser).where(AppUser.email == payload.email))
    if existing:
        raise HTTPException(status_code=409, detail="Email already exists")

    user = AppUser(
        email=payload.email,
        display_name=payload.display_name,
        password_hash=hash_password(payload.password) if payload.password else None,
        is_admin=payload.is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.get("/users", response_model=list[UserRead])
def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> list[AppUser]:
    return db.scalars(select(AppUser).order_by(AppUser.created_at.desc()).limit(limit).offset(offset)).all()


# ── Situations (Authenticated, with ownership) ─────────────────


@app.post("/situations", response_model=SituationRead, status_code=status.HTTP_201_CREATED)
def create_situation(
    payload: SituationCreate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> Situation:
    if not current_user.is_admin and payload.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Cannot create situations for other users")
    require_user(db, payload.user_id)

    situation = Situation(
        user_id=payload.user_id,
        title=payload.title,
        description=payload.description,
        query=payload.query,
        is_active=payload.is_active,
    )
    db.add(situation)
    db.commit()
    db.refresh(situation)
    enqueue_situation_backfill(db, str(situation.id), reset=False)
    return situation


@app.post("/situations/from-suggestion", response_model=SituationRead, status_code=status.HTTP_201_CREATED)
def create_situation_from_suggestion(
    payload: CreateFromSuggestion,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> Situation:
    """Create a situation and auto-ingest its discovered articles."""
    situation = Situation(
        user_id=current_user.id,
        title=payload.topic,
        description=payload.description,
        query=payload.query,
        is_active=True,
    )
    db.add(situation)
    db.flush()

    for art in payload.articles:
        # Get or create the source
        source = db.scalar(
            select(Source).where(Source.name == art.source_name, Source.base_url == "")
        )
        if not source:
            source = Source(name=art.source_name, base_url="", source_type="news_site")
            db.add(source)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                source = db.scalar(
                    select(Source).where(Source.name == art.source_name, Source.base_url == "")
                )
                if not source:
                    continue

        # Get or create the article
        existing = db.scalar(select(Article).where(Article.url == art.url))
        if existing:
            article = existing
        else:
            article = Article(
                source_id=source.id,
                url=art.url,
                title=art.title,
                extra_metadata={},
            )
            db.add(article)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                article = db.scalar(select(Article).where(Article.url == art.url))
                if not article:
                    continue

        # Link article to situation
        existing_link = db.scalar(
            select(SituationArticle).where(
                SituationArticle.situation_id == situation.id,
                SituationArticle.article_id == article.id,
            )
        )
        if not existing_link:
            db.add(SituationArticle(
                situation_id=situation.id,
                article_id=article.id,
                reason="Tracked from LLM-categorized suggestion",
            ))

    db.commit()
    db.refresh(situation)
    enqueue_situation_backfill(db, str(situation.id), reset=False)
    return situation


@app.get("/situations", response_model=list[SituationRead])
def list_situations(
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[Situation]:
    stmt = select(Situation)
    # Always show only the current user's own situations
    stmt = stmt.where(Situation.user_id == current_user.id)
    if is_active is not None:
        stmt = stmt.where(Situation.is_active == is_active)
    stmt = stmt.order_by(desc(Situation.updated_at)).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@app.get("/situations/{situation_id}", response_model=SituationRead)
def get_situation(
    situation_id: UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> Situation:
    return require_situation_access(db, situation_id, current_user)


@app.patch("/situations/{situation_id}", response_model=SituationRead)
def update_situation(
    situation_id: UUID,
    payload: SituationUpdate,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> Situation:
    situation = require_situation_access(db, situation_id, current_user)

    changes = payload.model_dump(exclude_unset=True)
    reset_backfill = any(field in changes for field in ("title", "query"))
    for field, value in changes.items():
        setattr(situation, field, value)
    situation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(situation)
    if reset_backfill:
        enqueue_situation_backfill(db, str(situation.id), reset=True)
    return situation


@app.post("/situations/refresh")
def refresh_situations(
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> dict:
    """Re-scan feed articles for all active situations and link new matches."""
    stmt = select(Situation).where(
        Situation.user_id == current_user.id,
        Situation.is_active.is_(True),
    )
    situations = db.scalars(stmt).all()
    if not situations:
        return {"refreshed": 0, "new_articles": 0}

    total_new = 0
    for situation in situations:
        search_words = situation.query.strip().lower().split()
        if not search_words:
            continue

        # Find matching feed articles
        fa_stmt = (
            select(FeedArticle, FeedSource)
            .join(FeedSource, FeedSource.id == FeedArticle.feed_source_id)
            .where(FeedSource.is_active.is_(True))
        )
        for word in search_words:
            fa_stmt = fa_stmt.where(FeedArticle.title.ilike(f"%{word}%"))
        fa_stmt = fa_stmt.order_by(
            FeedArticle.published_date.desc().nullslast(),
            FeedArticle.ingested_at.desc(),
        ).limit(50)

        rows = db.execute(fa_stmt).all()
        for feed_article, feed_source in rows:
            # Check if article URL already linked
            existing_article = db.scalar(
                select(Article).where(Article.url == feed_article.original_url)
            )
            if existing_article:
                # Check if already linked to this situation
                existing_link = db.scalar(
                    select(SituationArticle).where(
                        SituationArticle.situation_id == situation.id,
                        SituationArticle.article_id == existing_article.id,
                    )
                )
                if existing_link:
                    continue
                # Link existing article
                db.add(SituationArticle(
                    situation_id=situation.id,
                    article_id=existing_article.id,
                    reason="Auto-refreshed",
                ))
                total_new += 1
            else:
                # Create source, article, and link
                source = db.scalar(
                    select(Source).where(Source.name == feed_source.name, Source.base_url == "")
                )
                if not source:
                    source = Source(name=feed_source.name, base_url="", source_type="news_site")
                    db.add(source)
                    try:
                        db.flush()
                    except IntegrityError:
                        db.rollback()
                        source = db.scalar(
                            select(Source).where(Source.name == feed_source.name, Source.base_url == "")
                        )
                        if not source:
                            continue

                article = Article(
                    source_id=source.id,
                    url=feed_article.original_url,
                    title=feed_article.title,
                    author=feed_article.author,
                    published_at=feed_article.published_date,
                    summary=feed_article.snippet,
                    extra_metadata={},
                )
                db.add(article)
                try:
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    article = db.scalar(select(Article).where(Article.url == feed_article.original_url))
                    if not article:
                        continue

                db.add(SituationArticle(
                    situation_id=situation.id,
                    article_id=article.id,
                    reason="Auto-refreshed",
                ))
                total_new += 1

    if total_new > 0:
        db.commit()

    return {"refreshed": len(situations), "new_articles": total_new}


@app.delete("/situations/{situation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_situation(
    situation_id: UUID,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> Response:
    situation = require_situation_access(db, situation_id, current_user)
    db.delete(situation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Articles (Admin for ingest, Authenticated for read) ────────


@app.post("/articles/ingest", response_model=ArticleRead)
def ingest_article(
    payload: ArticleIngest,
    response: Response,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> ArticleRead:
    require_situations(db, payload.situation_ids)

    source = get_or_create_source(db, payload)
    existing_article = db.scalar(select(Article).where(Article.url == str(payload.url)))
    created = False

    if existing_article:
        article = existing_article
        article.source_id = source.id
        article.external_id = payload.external_id
        article.title = payload.title
        article.author = payload.author
        article.published_at = payload.published_at
        article.summary = payload.summary
        article.content = payload.content
        article.sentiment_score = payload.sentiment_score
        article.extra_metadata = payload.metadata
    else:
        article = Article(
            source_id=source.id,
            external_id=payload.external_id,
            url=str(payload.url),
            title=payload.title,
            author=payload.author,
            published_at=payload.published_at,
            summary=payload.summary,
            content=payload.content,
            sentiment_score=payload.sentiment_score,
            extra_metadata=payload.metadata,
        )
        db.add(article)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            source = get_or_create_source(db, payload)
            article = db.scalar(select(Article).where(Article.url == str(payload.url)))
            if article is None:
                raise HTTPException(status_code=500, detail="Could not create or find article")
            article.source_id = source.id
            article.external_id = payload.external_id
            article.title = payload.title
            article.author = payload.author
            article.published_at = payload.published_at
            article.summary = payload.summary
            article.content = payload.content
            article.sentiment_score = payload.sentiment_score
            article.extra_metadata = payload.metadata
        else:
            created = True

    for situation_id in payload.situation_ids:
        link = db.scalar(
            select(SituationArticle).where(
                SituationArticle.situation_id == situation_id,
                SituationArticle.article_id == article.id,
            )
        )
        if link:
            link.relevance_score = payload.relevance_score
            link.reason = payload.reason
        else:
            db.add(
                SituationArticle(
                    situation_id=situation_id,
                    article_id=article.id,
                    relevance_score=payload.relevance_score,
                    reason=payload.reason,
                )
            )

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Could not ingest article") from exc

    db.refresh(article)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return serialize_article(article)


@app.get("/situations/{situation_id}/articles", response_model=list[SituationArticleRead])
def list_articles_for_situation(
    situation_id: UUID,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[SituationArticleRead]:
    require_situation_access(db, situation_id, current_user)

    stmt = (
        select(SituationArticle, Article)
        .join(Article, Article.id == SituationArticle.article_id)
        .where(SituationArticle.situation_id == situation_id)
        .order_by(Article.published_at.desc().nullslast(), Article.ingested_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = db.execute(stmt).all()
    return [
        SituationArticleRead(
            article=serialize_article(article),
            relevance_score=float(link.relevance_score) if link.relevance_score is not None else None,
            reason=link.reason,
            tagged_at=link.tagged_at,
        )
        for link, article in rows
    ]


# ── Dashboard ──────────────────────────────────────────────────


@app.get("/situations/{situation_id}/dashboard", response_model=DashboardRead)
def get_dashboard(
    situation_id: UUID,
    persist_snapshot: bool = False,
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> DashboardRead:
    require_situation_access(db, situation_id, current_user)

    article_count = db.scalar(
        select(func.count()).select_from(SituationArticle).where(SituationArticle.situation_id == situation_id)
    )
    source_count = db.scalar(
        select(func.count(distinct(Article.source_id)))
        .select_from(SituationArticle)
        .join(Article, Article.id == SituationArticle.article_id)
        .where(SituationArticle.situation_id == situation_id)
    )
    headline_rows = db.execute(
        select(Article.title, Article.url)
        .select_from(SituationArticle)
        .join(Article, Article.id == SituationArticle.article_id)
        .where(SituationArticle.situation_id == situation_id)
        .order_by(Article.published_at.desc().nullslast(), Article.ingested_at.desc())
        .limit(5)
    ).all()
    headlines = [{"title": title, "url": url} for title, url in headline_rows]

    generated_at = datetime.now(timezone.utc)
    if persist_snapshot:
        snapshot = DashboardSnapshot(
            situation_id=situation_id,
            article_count=article_count or 0,
            source_count=source_count or 0,
            top_headlines=[h["title"] for h in headlines],
            trend_notes=None,
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        generated_at = snapshot.generated_at

    return DashboardRead(
        situation_id=situation_id,
        generated_at=generated_at,
        article_count=article_count or 0,
        source_count=source_count or 0,
        top_headlines=headlines,
        trend_notes=None,
    )


# ── Feed Sources (Authenticated) ──────────────────────────────


@app.post("/feed-sources", response_model=FeedSourceRead, status_code=status.HTTP_201_CREATED)
def create_feed_source(
    payload: FeedSourceCreate,
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> FeedSource:
    existing = db.scalar(select(FeedSource).where(FeedSource.rss_url == payload.rss_url))
    if existing:
        raise HTTPException(status_code=409, detail="Feed URL already exists")

    source = FeedSource(name=payload.name, rss_url=payload.rss_url, category=payload.category)
    db.add(source)
    db.commit()
    db.refresh(source)

    # Immediately fetch the feed so articles are available right away
    try:
        fetch_single_feed(source)
        db.refresh(source)  # pick up updated last_fetched_at
    except Exception:
        pass  # feed fetch failure shouldn't block creation

    return source


@app.get("/feed-sources", response_model=list[FeedSourceRead])
def list_feed_sources(
    category: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[FeedSource]:
    stmt = select(FeedSource)
    if category is not None:
        stmt = stmt.where(FeedSource.category == category)
    stmt = stmt.order_by(FeedSource.created_at.desc()).limit(limit).offset(offset)
    return db.scalars(stmt).all()


@app.delete("/feed-sources/{feed_source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_feed_source(
    feed_source_id: UUID,
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> Response:
    source = db.scalar(select(FeedSource).where(FeedSource.id == feed_source_id))
    if not source:
        raise HTTPException(status_code=404, detail="Feed source not found")
    db.delete(source)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Feed Articles (Authenticated, read-only) ──────────────────


@app.get("/feed-articles", response_model=list[FeedArticleRead])
def list_feed_articles(
    feed_source_id: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[FeedArticle]:
    stmt = select(FeedArticle)
    if feed_source_id is not None:
        stmt = stmt.where(FeedArticle.feed_source_id == feed_source_id)
    stmt = stmt.order_by(FeedArticle.published_date.desc().nullslast(), FeedArticle.ingested_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    return db.scalars(stmt).all()


# ── Situation Suggestions (Authenticated) ─────────────────────────


@app.get("/trending-topics", response_model=list[str])
def get_trending_topics(
    limit: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[str]:
    """Return situation titles ranked by number of LLM-categorized articles."""
    stmt = (
        select(Situation.title, func.count(SituationArticle.article_id).label("cnt"))
        .join(SituationArticle, SituationArticle.situation_id == Situation.id)
        .where(
            Situation.is_active.is_(True),
            SituationArticle.llm_model.isnot(None),
        )
        .group_by(Situation.id, Situation.title)
        .order_by(desc("cnt"))
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    return [row.title for row in rows]


@app.get("/news-suggestions", response_model=list[SituationSuggestion])
def get_news_suggestions(
    q: str = Query(default="", min_length=1, max_length=200),
    db: Session = Depends(get_db),
    _user: AppUser = Depends(get_current_user),
) -> list[dict]:
    """Search existing LLM-categorized situations by title/description."""
    normalized = q.strip()
    if not normalized:
        return []

    # Find situations matching the query that have LLM-categorized articles
    search_words = normalized.lower().split()
    sit_stmt = (
        select(
            Situation.id,
            Situation.title,
            Situation.description,
            Situation.query,
            func.count(distinct(SituationArticle.article_id)).label("article_count"),
        )
        .join(SituationArticle, SituationArticle.situation_id == Situation.id)
        .where(
            Situation.is_active.is_(True),
            SituationArticle.llm_model.isnot(None),
        )
    )
    # Match each word against situation title or description
    for word in search_words:
        pattern = f"%{word}%"
        sit_stmt = sit_stmt.where(
            Situation.title.ilike(pattern) | Situation.description.ilike(pattern)
        )
    sit_stmt = (
        sit_stmt.group_by(Situation.id, Situation.title, Situation.description, Situation.query)
        .order_by(desc("article_count"))
        .limit(10)
    )
    situation_rows = db.execute(sit_stmt).all()
    if not situation_rows:
        return []

    results = []
    for row in situation_rows:
        # Get sample articles and sources for this situation
        art_stmt = (
            select(Article.title, Article.url, Source.name.label("source_name"))
            .join(SituationArticle, SituationArticle.article_id == Article.id)
            .outerjoin(Source, Source.id == Article.source_id)
            .where(
                SituationArticle.situation_id == row.id,
                SituationArticle.llm_model.isnot(None),
            )
            .order_by(SituationArticle.tagged_at.desc())
            .limit(10)
        )
        art_rows = db.execute(art_stmt).all()

        headlines = [a.title for a in art_rows]
        source_names = sorted({a.source_name for a in art_rows if a.source_name})
        articles_data = [
            {
                "url": a.url,
                "title": a.title,
                "source_name": a.source_name or "Unknown",
                "published": None,
            }
            for a in art_rows
        ]

        description = row.description or (
            f"Situation with {row.article_count} articles from "
            f"{len(source_names)} source{'s' if len(source_names) != 1 else ''}."
        )

        results.append({
            "topic": row.title,
            "query": row.query or row.title,
            "description": description,
            "article_count": row.article_count,
            "sources": source_names[:6],
            "sample_headlines": headlines[:4],
            "articles": articles_data,
        })

    return results
