from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, HttpUrl

SourceType = Literal["news_site", "social", "rss", "api", "other"]


class UserRegister(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=128)
    is_admin: bool = False


class UserRead(BaseModel):
    id: UUID
    email: EmailStr
    display_name: str
    is_admin: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead


class SituationCreate(BaseModel):
    user_id: UUID
    title: str = Field(min_length=1, max_length=300)
    description: str | None = None
    query: str = Field(min_length=1, max_length=1500)
    is_active: bool = True


class SituationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    query: str | None = Field(default=None, min_length=1, max_length=1500)
    is_active: bool | None = None


class SituationRead(BaseModel):
    id: UUID
    user_id: UUID
    title: str
    description: str | None
    query: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArticleIngest(BaseModel):
    url: HttpUrl
    title: str = Field(min_length=1, max_length=500)
    source_name: str = Field(min_length=1, max_length=300)
    source_type: SourceType = "news_site"
    base_url: str | None = None
    external_id: str | None = None
    author: str | None = None
    published_at: datetime | None = None
    summary: str | None = None
    content: str | None = None
    sentiment_score: float | None = Field(default=None, ge=-1.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    situation_ids: list[UUID] = Field(default_factory=list)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    reason: str | None = None


class ArticleRead(BaseModel):
    id: UUID
    source_id: UUID | None
    external_id: str | None
    url: str
    title: str
    author: str | None
    published_at: datetime | None
    summary: str | None
    content: str | None
    sentiment_score: float | None
    metadata: dict[str, Any]
    ingested_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SituationArticleRead(BaseModel):
    article: ArticleRead
    relevance_score: float | None
    reason: str | None
    tagged_at: datetime


class DashboardRead(BaseModel):
    situation_id: UUID
    generated_at: datetime
    article_count: int
    source_count: int
    top_headlines: list[str]
    trend_notes: str | None = None
