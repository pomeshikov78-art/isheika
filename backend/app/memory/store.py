"""Долговременная память системы (ТЗ 6.2, 6.12, таблицы 11.11-11.13).

Перед декомпозицией планировщик ищет здесь похожие завершённые миссии,
после финализации W13 сохраняет извлечённые факты и выводы.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import MemoryEntry, MemoryLink, MemorySearchLog
from .embeddings import cosine, get_embedder

log = logging.getLogger(__name__)


async def remember(
    session: AsyncSession,
    *,
    mission_id: str | None,
    entry_type: str,
    content: str,
    confidence: float = 0.5,
    source_urls: list[str] | None = None,
) -> MemoryEntry | None:
    content = (content or "").strip()
    if len(content) < 12:
        return None  # обрывки не имеют смысла в памяти и портят поиск

    embedder = get_embedder()
    entry = MemoryEntry(
        mission_id=mission_id,
        entry_type=entry_type,
        content=content[:4000],
        embedding=await embedder.embed(content),
        confidence=float(confidence),
        source_urls=source_urls or [],
    )
    session.add(entry)
    # Идентификатор присваивается только при вставке, а вызывающий код сразу
    # строит по нему связи графа - без flush они уходят в базу с NULL
    await session.flush()
    return entry


async def forget_claim(session: AsyncSession, mission_id: str, text: str) -> int:
    """Удаляет из памяти записи, порождённые отклонённым утверждением.

    Смысл в том, чтобы система не тиражировала ошибку: без этого отклонённый
    вывод всплывёт в следующей миссии как «результат прошлого исследования»
    и получит вес наравне с проверенными фактами. Вместе с записью удаляются
    её связи - иначе граф останется с висячими рёбрами.
    """
    text = (text or "").strip()
    if not text:
        return 0

    entries = (
        await session.execute(
            select(MemoryEntry).where(MemoryEntry.mission_id == mission_id)
        )
    ).scalars().all()

    doomed = [e for e in entries if text[:120].lower() in (e.content or "").lower()]
    for entry in doomed:
        links = (
            await session.execute(
                select(MemoryLink).where(
                    (MemoryLink.source_entry_id == entry.id)
                    | (MemoryLink.target_entry_id == entry.id)
                )
            )
        ).scalars().all()
        for edge in links:
            await session.delete(edge)
        await session.delete(entry)

    if doomed:
        log.info("Из памяти удалено записей по отклонённому выводу: %s", len(doomed))
    return len(doomed)


async def link(
    session: AsyncSession,
    source: MemoryEntry | None,
    target: MemoryEntry | None,
    relation: str,
) -> MemoryLink | None:
    """Связь между записями памяти (ТЗ 11.12).

    Без связей память остаётся плоским списком фактов: нельзя ни отследить,
    из какого вывода получен факт, ни увидеть, что критик этот вывод оспорил.
    Отношения: supports, contradicts, derived_from, related_to.
    """
    if source is None or target is None or source is target:
        return None

    edge = MemoryLink(
        source_entry_id=source.id,
        target_entry_id=target.id,
        relation_type=relation,
    )
    session.add(edge)
    await session.flush()
    return edge


async def graph(session: AsyncSession, entry_id: str) -> dict:
    """Ближайшее окружение записи: чем подкреплена и что ей противоречит."""
    outgoing = (
        await session.execute(select(MemoryLink).where(MemoryLink.source_entry_id == entry_id))
    ).scalars().all()
    incoming = (
        await session.execute(select(MemoryLink).where(MemoryLink.target_entry_id == entry_id))
    ).scalars().all()

    return {
        "entry_id": entry_id,
        "outgoing": [{"to": e.target_entry_id, "relation": e.relation_type} for e in outgoing],
        "incoming": [{"from": e.source_entry_id, "relation": e.relation_type} for e in incoming],
    }


async def recall(
    session: AsyncSession,
    query: str,
    *,
    mission_id: str | None = None,
    top_k: int | None = None,
    min_similarity: float | None = None,
    exclude_mission_id: str | None = None,
) -> list[dict]:
    """Семантический поиск по памяти.

    Косинус считается в Python: на SQLite альтернативы нет, а объёмы MVP
    (тысячи записей) это выдерживают. На PostgreSQL этот запрос заменяется
    оператором pgvector без изменения вызывающего кода.
    """
    top_k = top_k or settings.memory_top_k
    threshold = settings.memory_min_similarity if min_similarity is None else min_similarity

    stmt = select(MemoryEntry)
    if exclude_mission_id:
        stmt = stmt.where(MemoryEntry.mission_id != exclude_mission_id)
    entries = (await session.execute(stmt)).scalars().all()
    if not entries:
        return []

    embedder = get_embedder()
    query_vector = await embedder.embed(query)

    scored = []
    for entry in entries:
        score = cosine(query_vector, entry.embedding or [])
        if score >= threshold:
            scored.append((score, entry))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    top = scored[:top_k]

    if mission_id:
        session.add(
            MemorySearchLog(
                mission_id=mission_id,
                query=query[:2000],
                matched_entry_ids=[e.id for _, e in top],
                relevance_scores={e.id: round(s, 4) for s, e in top},
            )
        )

    return [
        {
            "id": entry.id,
            "type": entry.entry_type,
            "content": entry.content,
            "confidence": entry.confidence,
            "similarity": round(score, 4),
            "mission_id": entry.mission_id,
            "sources": entry.source_urls,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
        }
        for score, entry in top
    ]


def as_context(entries: list[dict], limit: int = 5) -> str:
    """Форматирование памяти для подстановки в промпт агента."""
    if not entries:
        return ""
    lines = ["Релевантные результаты прошлых миссий (используй с учётом даты и уверенности):"]
    for entry in entries[:limit]:
        lines.append(
            f"- [{entry['type']}, уверенность {entry['confidence']:.2f}, "
            f"похожесть {entry['similarity']:.2f}] {entry['content']}"
        )
    return "\n".join(lines)
