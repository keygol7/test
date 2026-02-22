import re
import time
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
from .models import AppUser, Article, DashboardSnapshot, FeedArticle, FeedSource, Situation, SituationArticle, Source
import feedparser as _feedparser

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

# ── News Suggestions Cache ───────────────────────────────────────
_suggestions_cache: dict[str, tuple[float, list[dict]]] = {}
_SUGGESTIONS_TTL_SECONDS = 300  # 5 minutes

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
                reason="Auto-ingested from news suggestion",
            ))

    db.commit()
    db.refresh(situation)
    return situation


@app.get("/situations", response_model=list[SituationRead])
def list_situations(
    user_id: UUID | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: AppUser = Depends(get_current_user),
) -> list[Situation]:
    stmt = select(Situation)
    if not current_user.is_admin:
        stmt = stmt.where(Situation.user_id == current_user.id)
    elif user_id is not None:
        stmt = stmt.where(Situation.user_id == user_id)
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
    for field, value in changes.items():
        setattr(situation, field, value)
    situation.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(situation)
    return situation


@app.delete("/situations/{situation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_situation(
    situation_id: UUID,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> Response:
    situation = require_situation(db, situation_id)
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
    headlines = db.scalars(
        select(Article.title)
        .select_from(SituationArticle)
        .join(Article, Article.id == SituationArticle.article_id)
        .where(SituationArticle.situation_id == situation_id)
        .order_by(Article.published_at.desc().nullslast(), Article.ingested_at.desc())
        .limit(5)
    ).all()

    generated_at = datetime.now(timezone.utc)
    if persist_snapshot:
        snapshot = DashboardSnapshot(
            situation_id=situation_id,
            article_count=article_count or 0,
            source_count=source_count or 0,
            top_headlines=headlines,
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


# ── Feed Sources (Admin only) ─────────────────────────────────


@app.post("/feed-sources", response_model=FeedSourceRead, status_code=status.HTTP_201_CREATED)
def create_feed_source(
    payload: FeedSourceCreate,
    db: Session = Depends(get_db),
    _admin: AppUser = Depends(require_admin),
) -> FeedSource:
    existing = db.scalar(select(FeedSource).where(FeedSource.rss_url == payload.rss_url))
    if existing:
        raise HTTPException(status_code=409, detail="Feed URL already exists")

    source = FeedSource(name=payload.name, rss_url=payload.rss_url, category=payload.category)
    db.add(source)
    db.commit()
    db.refresh(source)
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
    _admin: AppUser = Depends(require_admin),
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

_STOP_WORDS = frozenset(
    "a an the and or but in on at to for of is it by with from as be was were "
    "are been has have had do does did will would could should may might can "
    "not no this that these those its his her he she they we you i my your our "
    "their who what when where how why all each every any some new says said "
    "after over about up into out also just than more very so if than being us "
    "vs get gets got make makes report reports could would should first last "
    "back may might like amid amid during still another here there way according "
    "want wants need needs take takes look looks come comes week day year "
    "monday tuesday wednesday thursday friday saturday sunday today".split()
)

# Theme labels that map entity-keyword patterns to broad situation names.
# These are checked first; if none match, we auto-generate from entities.
_THEME_PATTERNS: list[tuple[set[str], str]] = [
    ({"election", "vote", "ballot", "poll", "polls", "voting", "campaign"}, "Election"),
    ({"protest", "protests", "protesters", "rally", "rallies", "demonstration"}, "Protests"),
    ({"war", "military", "troops", "strike", "strikes", "attack", "invasion"}, "Military Conflict"),
    ({"trade", "tariff", "tariffs", "sanctions", "embargo", "economy"}, "Trade & Sanctions"),
    ({"climate", "warming", "emissions", "carbon", "environment"}, "Climate & Environment"),
    ({"earthquake", "hurricane", "flood", "wildfire", "disaster"}, "Natural Disaster"),
    ({"trial", "court", "lawsuit", "verdict", "judge", "indictment"}, "Legal & Courts"),
    ({"deal", "merger", "acquisition", "ipo", "stock", "market"}, "Business & Markets"),
    ({"championship", "tournament", "playoff", "finals", "season", "league"}, "Sports Season"),
    ({"transfer", "signing", "roster", "draft", "trade"}, "Sports Transfers"),
    ({"covid", "pandemic", "vaccine", "outbreak", "virus", "health"}, "Public Health"),
    ({"summit", "talks", "diplomacy", "treaty", "negotiations"}, "Diplomacy & Talks"),
    ({"reform", "bill", "legislation", "policy", "law"}, "Policy & Legislation"),
    ({"tech", "ai", "artificial", "intelligence", "software", "startup"}, "Technology"),
    ({"revolution", "uprising", "regime", "coup", "overthrow"}, "Revolution & Unrest"),
]


def _extract_source_name(entry) -> str:
    src = getattr(entry, "source", None)
    if src and isinstance(src, dict) and src.get("title"):
        return src["title"]
    author = getattr(entry, "author", None)
    if author:
        return author
    return "Unknown"


def _clean_title(title: str, source: str) -> str:
    """Strip trailing ' - Source Name' suffix from Google News titles."""
    if source and source != "Unknown":
        suffix = f" - {source}"
        if title.endswith(suffix):
            return title[: -len(suffix)].strip()
    cleaned = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
    return cleaned or title


def _extract_entities(text: str) -> set[str]:
    """
    Extract named entities (proper nouns, multi-word capitalized phrases).
    These are the primary clustering signal for broad themes.
    """
    # Find capitalized words/phrases (2+ chars), excluding sentence starters
    # by looking at words that are capitalized mid-sentence or are all-caps
    entities: set[str] = set()

    # Multi-word capitalized phrases: "United States", "Premier League"
    for match in re.finditer(r"(?<!\. )(?<!\.\n)([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text):
        entities.add(match.group(0).lower())

    # All-caps acronyms: US, NATO, NFL, AI
    for match in re.finditer(r"\b([A-Z]{2,6})\b", text):
        entities.add(match.group(0).lower())

    # Single capitalized words (likely proper nouns), skip very short ones
    for match in re.finditer(r"\b([A-Z][a-z]{2,})\b", text):
        word = match.group(0).lower()
        if word not in _STOP_WORDS:
            entities.add(word)

    return entities


def _extract_keywords(text: str) -> set[str]:
    """Extract all meaningful lowercase keywords from a headline."""
    words = re.findall(r"[A-Za-z'\u2019]{3,}", text)
    return {w.lower() for w in words if w.lower() not in _STOP_WORDS}


def _find_theme_label(all_keywords: set[str]) -> str | None:
    """Check if a set of keywords matches a known broad theme pattern."""
    for pattern_words, label in _THEME_PATTERNS:
        if all_keywords & pattern_words:
            return label
    return None


def _generate_topic_name(cluster: dict) -> str:
    """
    Generate a broad, readable topic name from a cluster's entities.
    e.g. "Iran — Military Conflict" or "Lakers — Sports Season"
    """
    # Count entity frequency across all headlines
    entity_counts: dict[str, int] = {}
    for entities in cluster["entity_lists"]:
        for e in entities:
            entity_counts[e] = entity_counts.get(e, 0) + 1

    # Sort by frequency, take top entities
    top_entities = sorted(entity_counts.items(), key=lambda x: -x[1])

    # Find a theme label from the combined keywords
    theme = _find_theme_label(cluster["all_keywords"])

    # Pick the most common entity as the subject (title-case it)
    subject_parts = []
    for entity, _count in top_entities[:3]:
        # Skip if it's a generic theme word already captured
        if theme and entity in theme.lower():
            continue
        subject_parts.append(entity.title())
        if len(subject_parts) >= 2:
            break

    subject = ", ".join(subject_parts) if subject_parts else "Developing Story"

    if theme:
        return f"{subject} — {theme}"
    return subject


def _cluster_articles(entries: list[dict]) -> list[dict]:
    """
    Group articles into broad situation clusters.
    Uses shared entities (proper nouns) as the primary signal.
    Any shared entity merges articles into the same cluster.
    """
    clusters: list[dict] = []

    for entry in entries:
        title = entry["title"]
        entities = entry["entities"]
        keywords = entry["keywords"]
        source = entry["source"]
        article_data = {
            "url": entry["url"],
            "title": title,
            "source_name": source,
            "published": entry.get("published"),
        }

        # Find the cluster with the most entity overlap
        best_cluster = None
        best_overlap = 0
        for cluster in clusters:
            overlap = len(entities & cluster["core_entities"])
            if overlap >= 1 and overlap > best_overlap:
                best_cluster = cluster
                best_overlap = overlap

        if best_cluster:
            best_cluster["headlines"].append(title)
            best_cluster["sources"].add(source)
            best_cluster["core_entities"] |= entities
            best_cluster["all_keywords"] |= keywords
            best_cluster["entity_lists"].append(entities)
            best_cluster["articles"].append(article_data)
        else:
            clusters.append({
                "headlines": [title],
                "sources": {source},
                "core_entities": set(entities),
                "all_keywords": set(keywords),
                "entity_lists": [entities],
                "articles": [article_data],
            })

    # Second pass: merge clusters that share entities (iterative)
    merged = True
    while merged:
        merged = False
        i = 0
        while i < len(clusters):
            j = i + 1
            while j < len(clusters):
                if clusters[i]["core_entities"] & clusters[j]["core_entities"]:
                    # Merge j into i
                    clusters[i]["headlines"].extend(clusters[j]["headlines"])
                    clusters[i]["sources"] |= clusters[j]["sources"]
                    clusters[i]["core_entities"] |= clusters[j]["core_entities"]
                    clusters[i]["all_keywords"] |= clusters[j]["all_keywords"]
                    clusters[i]["entity_lists"].extend(clusters[j]["entity_lists"])
                    clusters[i]["articles"].extend(clusters[j]["articles"])
                    clusters.pop(j)
                    merged = True
                else:
                    j += 1
            i += 1

    return clusters


@app.get("/news-suggestions", response_model=list[SituationSuggestion])
def get_news_suggestions(
    q: str = Query(default="", min_length=1, max_length=200),
    _user: AppUser = Depends(get_current_user),
) -> list[dict]:
    normalized = q.strip().lower()
    if not normalized:
        return []

    now = time.time()
    cached = _suggestions_cache.get(normalized)
    if cached and cached[0] > now:
        return cached[1]

    rss_url = (
        f"https://news.google.com/rss/search"
        f"?q={q.strip()}&hl=en-US&gl=US&ceid=US:en"
    )
    try:
        parsed = _feedparser.parse(rss_url)
    except Exception:
        raise HTTPException(status_code=502, detail="Failed to reach Google News")

    entries = []
    for entry in parsed.entries[:50]:
        title_raw = getattr(entry, "title", None)
        link = getattr(entry, "link", None)
        if not title_raw or not link:
            continue
        source = _extract_source_name(entry)
        title = _clean_title(title_raw, source)
        entities = _extract_entities(title)
        keywords = _extract_keywords(title)
        if not entities and not keywords:
            continue

        pub_struct = getattr(entry, "published_parsed", None)
        published = None
        if pub_struct:
            try:
                dt = datetime(*pub_struct[:6], tzinfo=timezone.utc)
                published = dt.strftime("%b %d, %Y")
            except Exception:
                published = None

        entries.append({
            "title": title,
            "source": source,
            "url": link,
            "published": published,
            "entities": entities,
            "keywords": keywords,
        })

    if not entries:
        _suggestions_cache[normalized] = (now + _SUGGESTIONS_TTL_SECONDS, [])
        return []

    clusters = _cluster_articles(entries)

    # Filter out tiny clusters (single-article) unless there are few results
    big_clusters = [c for c in clusters if len(c["headlines"]) >= 2]
    if len(big_clusters) < 2:
        big_clusters = clusters

    big_clusters.sort(key=lambda c: len(c["headlines"]), reverse=True)

    results = []
    for cluster in big_clusters[:6]:
        headlines = cluster["headlines"]
        sources = sorted(cluster["sources"] - {"Unknown"})
        topic = _generate_topic_name(cluster)

        # Build a query using the search term + top entities for tracking
        top_entity_words = []
        entity_counts: dict[str, int] = {}
        for elist in cluster["entity_lists"]:
            for e in elist:
                entity_counts[e] = entity_counts.get(e, 0) + 1
        for entity, _ in sorted(entity_counts.items(), key=lambda x: -x[1])[:3]:
            top_entity_words.append(entity)
        query = " ".join(top_entity_words) if top_entity_words else q.strip()

        description = (
            f"Ongoing situation with {len(headlines)} articles from "
            f"{len(sources)} source{'s' if len(sources) != 1 else ''}. "
            f"Key coverage: {headlines[0]}"
        )

        results.append({
            "topic": topic,
            "query": query,
            "description": description,
            "article_count": len(headlines),
            "sources": sources[:6],
            "sample_headlines": headlines[:4],
            "articles": cluster["articles"],
        })

    if len(_suggestions_cache) > 500:
        _suggestions_cache.clear()
    _suggestions_cache[normalized] = (now + _SUGGESTIONS_TTL_SECONDS, results)
    return results
