"""Модель данных «Ищейки» — ТЗ 4.0, раздел 11.

Таблицы 11.1-11.10 (ядро) и 11.11-11.13 (система памяти).
Векторы эмбеддингов хранятся как JSON: на SQLite это единственный вариант,
на PostgreSQL столбец заменяется на pgvector без изменения кода уровня выше.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class User(Base, TimestampMixin):
    """11.1 users"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16), default="analyst")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    missions: Mapped[list["Mission"]] = relationship(back_populates="user")


class Mission(Base, TimestampMixin):
    """11.2 missions"""

    __tablename__ = "missions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(32), default="comprehensive")
    status: Mapped[str] = mapped_column(String(16), default="created", index=True)
    horizons: Mapped[list] = mapped_column(JSON, default=list)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
    report_url: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped["User | None"] = relationship(back_populates="missions")
    tasks: Mapped[list["Task"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan"
    )


class Task(Base, TimestampMixin):
    """11.3 tasks"""

    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    depends_on: Mapped[list] = mapped_column(JSON, default=list)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    output: Mapped[dict] = mapped_column(JSON, default=dict)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    mission: Mapped["Mission"] = relationship(back_populates="tasks")


class Evidence(Base):
    """11.4 evidence - доказательная база, каждый факт привязан сюда"""

    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    source_type: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    content: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[str | None] = mapped_column(String(32))
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Claim(Base):
    """11.5 claims - утверждения со ссылками на evidence.

    Поля review_* обслуживают human-in-the-loop (ТЗ 3.6): аналитик
    подтверждает или отклоняет конкретный вывод, и отклонённый не попадает
    в память - иначе система будет тиражировать ошибку в следующих миссиях.
    """

    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(8))
    claim_text: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(16), default="fact")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    review_status: Mapped[str] = mapped_column(String(12), default="pending")
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(36))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Conflict(Base):
    """11.6 conflicts - расхождения между агентами"""

    __tablename__ = "conflicts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    description: Mapped[str] = mapped_column(Text)
    agents: Mapped[list] = mapped_column(JSON, default=list)
    resolution: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Forecast(Base):
    """11.7 forecasts - сценарии по горизонтам"""

    __tablename__ = "forecasts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    metric: Mapped[str] = mapped_column(String(128), default="adoption")
    horizon: Mapped[int] = mapped_column(Integer)
    scenario: Mapped[str] = mapped_column(String(16))
    value: Mapped[float] = mapped_column(Float)
    probability: Mapped[float | None] = mapped_column(Float)
    confidence_interval: Mapped[dict] = mapped_column(JSON, default=dict)


class Risk(Base):
    """11.8 risks - матрица вероятность x влияние"""

    __tablename__ = "risks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str] = mapped_column(ForeignKey("missions.id"), index=True)
    risk_type: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    probability: Mapped[float] = mapped_column(Float)
    impact: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    mitigation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="identified")


class Source(Base):
    """11.9 sources - реестр источников с рейтингом авторитетности"""

    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    url: Mapped[str] = mapped_column(Text, unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(255), index=True)
    reliability_rating: Mapped[float] = mapped_column(Float, default=0.5)
    notes: Mapped[str | None] = mapped_column(Text)
    last_checked: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    """11.10 audit_log - хранение не менее 90 дней (ТЗ 8)"""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[str | None] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MemoryEntry(Base):
    """11.11 memory_entries - долговременная память системы"""

    __tablename__ = "memory_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"), index=True)
    entry_type: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    validity_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryLink(Base):
    """11.12 memory_links - граф связей между записями памяти"""

    __tablename__ = "memory_links"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_entry_id: Mapped[str] = mapped_column(ForeignKey("memory_entries.id"))
    target_entry_id: Mapped[str] = mapped_column(ForeignKey("memory_entries.id"))
    relation_type: Mapped[str] = mapped_column(String(24))


class MemorySearchLog(Base):
    """11.13 memory_search_log"""

    __tablename__ = "memory_search_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    mission_id: Mapped[str | None] = mapped_column(ForeignKey("missions.id"))
    query: Mapped[str] = mapped_column(Text)
    matched_entry_ids: Mapped[list] = mapped_column(JSON, default=list)
    relevance_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Document(Base):
    """Пользовательские документы (ТЗ 5.1): PDF/DOCX/изображения"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    filename: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(128))
    path: Mapped[str] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
