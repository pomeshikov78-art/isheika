"""Документы, источники, справка по агентам, health, WebSocket (ТЗ 10.1-10.2)."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.registry import REGISTRY, describe
from ..cache import llm_cache, search_cache
from ..config import settings
from ..core import orchestrator
from ..core.events import bus
from ..db import get_session
from ..llm.factory import get_provider
from ..models import Document, Source, User
from ..schemas import DocumentOut, HealthResponse, SourceOut
from ..search.gateway import get_gateway
from ..security import audit, current_user, require_role

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["misc"])

MAX_UPLOAD_BYTES = 30 * 1024 * 1024


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    provider = get_provider()
    return HealthResponse(
        status="ok",
        app=settings.app_name,
        llm_provider=provider.name,
        llm_model=provider.model,
        search_engines=[e.name for e in get_gateway().engines],
        agents=len(REGISTRY),
        active_missions=sum(1 for _ in orchestrator._running),
        cache={"search": search_cache.stats(), "llm": llm_cache.stats()},
    )


@router.get("/agents")
async def agents() -> list[dict]:
    return describe()


@router.get("/domains")
async def domains() -> list[dict]:
    """Предметные области заказчика с готовыми формулировками задач.

    Интерфейс подставляет их в поле запроса: аналитику проще начать
    с образца, чем с чистого листа, а планировщик по области подбирает
    профильные англоязычные термины.
    """
    from ..domains import DOMAINS

    return [
        {
            "key": d.key,
            "title": d.title,
            "criteria": d.criteria,
            "examples": d.examples,
            "keywords": d.keywords[:8],
        }
        for d in DOMAINS
    ]


@router.post("/documents/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role("analyst")),
) -> DocumentOut:
    payload = await file.read()
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Файл больше 30 МБ")

    directory = settings.storage_path / "documents"
    directory.mkdir(parents=True, exist_ok=True)

    document = Document(
        user_id=user.id,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        path="",
    )
    path = directory / f"{document.id}_{Path(document.filename).name}"
    path.write_bytes(payload)
    document.path = str(path)

    text, pages = await asyncio.to_thread(_extract_text, path, document.content_type)
    document.text = text
    document.pages = pages

    session.add(document)
    await audit(session, request, "document.upload", user, filename=document.filename, pages=pages)
    await session.commit()

    return DocumentOut(
        id=document.id, filename=document.filename, pages=pages, chars=len(text or "")
    )


def _extract_text(path: Path, content_type: str) -> tuple[str, int]:
    """PDF - через PyMuPDF, DOCX - через python-docx, остальное - как текст.
    Пустой результат не ошибка: скан без OCR даст ноль символов."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf" or "pdf" in content_type:
            import fitz

            with fitz.open(path) as pdf:
                return "\n".join(page.get_text() for page in pdf), pdf.page_count

        if suffix == ".docx" or "wordprocessingml" in content_type:
            from docx import Document as DocxDocument

            doc = DocxDocument(str(path))
            return "\n".join(p.text for p in doc.paragraphs), 0

        return path.read_text(encoding="utf-8", errors="ignore"), 0
    except Exception as exc:
        log.warning("Не удалось извлечь текст из %s: %s", path.name, exc)
        return "", 0


@router.get("/sources/{source_id}", response_model=SourceOut)
async def get_source(
    source_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> SourceOut:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Источник не найден")
    return SourceOut(
        id=source.id, url=source.url, domain=source.domain,
        reliability_rating=source.reliability_rating, last_checked=source.last_checked,
    )


@router.get("/sources")
async def list_sources(
    domain: str | None = None,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[SourceOut]:
    stmt = select(Source).order_by(Source.reliability_rating.desc()).limit(min(limit, 500))
    if domain:
        stmt = stmt.where(Source.domain == domain)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        SourceOut(
            id=s.id, url=s.url, domain=s.domain,
            reliability_rating=s.reliability_rating, last_checked=s.last_checked,
        )
        for s in rows
    ]


ws_router = APIRouter(tags=["websocket"])


@ws_router.websocket("/ws/missions/{mission_id}")
async def mission_events(websocket: WebSocket, mission_id: str) -> None:
    """Поток событий миссии (ТЗ 10.2).

    Аутентификация здесь не требуется: канал только на чтение прогресса по
    заранее известному UUID миссии. При выносе наружу периметра сюда
    добавляется проверка токена из query-параметра.
    """
    await websocket.accept()
    queue = bus.subscribe(mission_id)
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
            if event["type"] in ("FINISHED", "ERROR"):
                break
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.debug("WebSocket миссии %s закрыт: %s", mission_id, exc)
    finally:
        bus.unsubscribe(mission_id, queue)
