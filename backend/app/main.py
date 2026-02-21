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
from .models import AppUser, Article, DashboardSnapshot, Situation, SituationArticle, Source
from .schemas import (
    ArticleIngest,
    ArticleRead,
    DashboardRead,
    LoginRequest,
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
    make_admin = (user_count == 0) or (
        settings.admin_email
        and payload.email.lower() == settings.admin_email.lower()
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
