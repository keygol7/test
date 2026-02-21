import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("'false'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    situations: Mapped[list["Situation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Situation(Base):
    __tablename__ = "situation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped["AppUser"] = relationship(back_populates="situations")
    situation_articles: Mapped[list["SituationArticle"]] = relationship(
        back_populates="situation", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["DashboardSnapshot"]] = relationship(
        back_populates="situation", cascade="all, delete-orphan"
    )


class Source(Base):
    __tablename__ = "source"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('news_site', 'social', 'rss', 'api', 'other')",
            name="source_source_type_check",
        ),
        UniqueConstraint("name", "base_url", name="source_name_base_url_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    credibility_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class Article(Base):
    __tablename__ = "article"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source.id", ondelete="SET NULL")
    )
    external_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    sentiment_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    extra_metadata: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    source: Mapped["Source | None"] = relationship(back_populates="articles")
    situation_articles: Mapped[list["SituationArticle"]] = relationship(
        back_populates="article", cascade="all, delete-orphan"
    )


class SituationArticle(Base):
    __tablename__ = "situation_article"

    situation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("situation.id", ondelete="CASCADE"),
        primary_key=True,
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("article.id", ondelete="CASCADE"), primary_key=True
    )
    relevance_score: Mapped[float | None] = mapped_column(Numeric(4, 3))
    reason: Mapped[str | None] = mapped_column(Text)
    tagged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    situation: Mapped["Situation"] = relationship(back_populates="situation_articles")
    article: Mapped["Article"] = relationship(back_populates="situation_articles")


class DashboardSnapshot(Base):
    __tablename__ = "dashboard_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    situation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("situation.id", ondelete="CASCADE"), nullable=False
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    article_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    top_headlines: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    trend_notes: Mapped[str | None] = mapped_column(Text)

    situation: Mapped["Situation"] = relationship(back_populates="snapshots")
