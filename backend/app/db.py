"""Подключение к БД и фабрика сессий (SQLite для MVP, PostgreSQL для production)."""
from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: сессия на запрос."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создание схемы. В production вместо этого используются миграции Alembic."""
    from . import models  # noqa: F401  - регистрация моделей в метаданных

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
