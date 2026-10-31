"""Контракты API (ТЗ 10.3, 10.4)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Mode = Literal["quick", "comprehensive", "full", "forecast", "risk"]


class MissionFilters(BaseModel):
    date_from: str | None = None
    date_to: str | None = None
    regions: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class MissionCreate(BaseModel):
    query: str = Field(min_length=3, max_length=4000)
    mode: Mode = "comprehensive"
    horizons: list[int] = Field(default_factory=lambda: [1, 3, 5, 10])
    max_sources: int = Field(default=20, ge=3, le=100)
    user_document_ids: list[str] = Field(default_factory=list)
    agents: list[str] | None = None
    filters: MissionFilters = Field(default_factory=MissionFilters)


class MissionCreated(BaseModel):
    mission_id: str
    status: str


class TaskOut(BaseModel):
    id: str
    agent_id: str
    status: str
    retries: int
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class MissionOut(BaseModel):
    id: str
    query: str
    mode: str
    status: str
    confidence: float | None = None
    horizons: list[int] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    report_url: str | None = None
    plan: dict[str, Any] = Field(default_factory=dict)
    tasks: list[TaskOut] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class MissionListItem(BaseModel):
    id: str
    query: str
    mode: str
    status: str
    confidence: float | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ClaimOut(BaseModel):
    id: str
    agent_id: str | None = None
    claim_text: str
    claim_type: str
    confidence: float
    evidence_ids: list[str] = Field(default_factory=list)
    review_status: str
    review_note: str | None = None
    reviewed_at: datetime | None = None


class ClaimReview(BaseModel):
    """Решение аналитика по конкретному выводу (ТЗ 3.6)."""

    status: Literal["accepted", "rejected", "pending"]
    note: str | None = Field(default=None, max_length=2000)


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: Literal["admin", "analyst", "guest"] = "analyst"


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    username: str


class DocumentOut(BaseModel):
    id: str
    filename: str
    pages: int
    chars: int


class SourceOut(BaseModel):
    id: str
    url: str
    domain: str
    reliability_rating: float
    last_checked: datetime | None = None


class HealthResponse(BaseModel):
    status: str
    app: str
    llm_provider: str
    llm_model: str
    search_engines: list[str]
    agents: int
    active_missions: int
    cache: dict[str, Any] = Field(default_factory=dict)
