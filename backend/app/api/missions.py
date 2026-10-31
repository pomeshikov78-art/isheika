"""Миссии: создание, статус, отчёты, история, отмена (ТЗ 10.1)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core import orchestrator
from ..memory.store import forget_claim
from ..models import utcnow
from ..db import get_session
from ..models import Claim, Mission, User
from ..schemas import (
    ClaimOut,
    ClaimReview,
    MissionCreate,
    MissionCreated,
    MissionListItem,
    MissionOut,
    TaskOut,
)
from ..security import audit, current_user, require_role

router = APIRouter(prefix="/api/v1", tags=["missions"])

MEDIA_TYPES = {
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


@router.post("/missions", response_model=MissionCreated, status_code=status.HTTP_202_ACCEPTED)
async def create_mission(
    payload: MissionCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role("analyst")),
) -> MissionCreated:
    mission = Mission(
        user_id=user.id,
        query=payload.query,
        mode=payload.mode,
        horizons=payload.horizons,
        params={
            "max_sources": payload.max_sources,
            "user_document_ids": payload.user_document_ids,
            "agents": payload.agents,
            "filters": payload.filters.model_dump(),
        },
        status="created",
    )
    session.add(mission)
    await audit(session, request, "mission.create", user, query=payload.query, mode=payload.mode)
    await session.commit()

    await orchestrator.launch(mission.id)
    return MissionCreated(mission_id=mission.id, status=mission.status)


@router.get("/missions/{mission_id}", response_model=MissionOut)
async def get_mission(
    mission_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> MissionOut:
    mission = await _load(session, mission_id, user)
    return MissionOut(
        id=mission.id,
        query=mission.query,
        mode=mission.mode,
        status=mission.status,
        confidence=mission.confidence,
        horizons=mission.horizons or [],
        error=mission.error,
        created_at=mission.created_at,
        started_at=mission.started_at,
        completed_at=mission.completed_at,
        report_url=mission.report_url,
        plan=mission.plan or {},
        result=mission.result or {},
        tasks=[
            TaskOut(
                id=t.id, agent_id=t.agent_id, status=t.status, retries=t.retries,
                error=t.error, started_at=t.started_at, finished_at=t.finished_at,
            )
            for t in sorted(mission.tasks, key=lambda t: t.agent_id)
        ],
    )


@router.get("/missions/{mission_id}/report")
async def get_report(
    mission_id: str,
    fmt: str = Query("html", pattern="^(html|docx|pdf)$"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    mission = await _load(session, mission_id, user)
    if mission.status != "completed":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Отчёт не готов: статус «{mission.status}»")

    path = Path(settings.storage_path) / "reports" / f"{mission_id}.{fmt}"
    if not path.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Формат {fmt} не сформирован. Доступно: "
            f"{', '.join((mission.result or {}).get('files', {}).keys()) or 'нет файлов'}",
        )

    return FileResponse(
        path,
        media_type=MEDIA_TYPES[fmt],
        filename=f"sleuth-{mission_id}.{fmt}",
    )


@router.get("/missions/{mission_id}/report.docx")
async def get_report_docx(
    mission_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    """ТЗ 10.1 задаёт отдельные пути для форматов, помимо параметра fmt."""
    return await get_report(mission_id, "docx", session, user)


@router.get("/missions/{mission_id}/report.pdf")
async def get_report_pdf(
    mission_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> FileResponse:
    return await get_report(mission_id, "pdf", session, user)


@router.post("/missions/{mission_id}/rerun", response_model=MissionCreated,
             status_code=status.HTTP_202_ACCEPTED)
async def rerun_mission(
    mission_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role("analyst")),
) -> MissionCreated:
    """Повторный запуск миссии (ТЗ 7.8).

    Создаётся новая миссия с параметрами исходной: переспрашивать их
    у пользователя незачем, он уже их задал. Исходная остаётся в истории
    нетронутой - сравнение прогонов между собой само по себе полезно.
    """
    origin = await _load(session, mission_id, user)

    mission = Mission(
        user_id=user.id,
        query=origin.query,
        mode=origin.mode,
        horizons=list(origin.horizons or []),
        params={**(origin.params or {}), "rerun_of": origin.id},
        status="created",
    )
    session.add(mission)
    await audit(session, request, "mission.rerun", user, origin=origin.id)
    await session.commit()

    await orchestrator.launch(mission.id)
    return MissionCreated(mission_id=mission.id, status=mission.status)


@router.post("/missions/{mission_id}/cancel")
async def cancel_mission(
    mission_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role("analyst")),
) -> dict:
    mission = await _load(session, mission_id, user)
    if mission.status in ("completed", "failed", "cancelled"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Миссия уже завершена: {mission.status}")

    cancelled = await orchestrator.cancel(mission_id)
    await audit(session, request, "mission.cancel", user, mission_id=mission_id)
    await session.commit()
    return {"mission_id": mission_id, "cancelled": cancelled}


@router.get("/missions/{mission_id}/claims", response_model=list[ClaimOut])
async def list_claims(
    mission_id: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[ClaimOut]:
    """Утверждения миссии для проверки аналитиком (ТЗ 3.6)."""
    await _load(session, mission_id, user)
    rows = (
        await session.execute(
            select(Claim).where(Claim.mission_id == mission_id)
            .order_by(Claim.agent_id, Claim.confidence.desc())
        )
    ).scalars().all()
    return [ClaimOut.model_validate(row, from_attributes=True) for row in rows]


@router.post("/missions/{mission_id}/claims/{claim_id}/review", response_model=ClaimOut)
async def review_claim(
    mission_id: str,
    claim_id: str,
    payload: ClaimReview,
    request: Request,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(require_role("analyst")),
) -> ClaimOut:
    """Подтверждение или отклонение вывода (ТЗ 3.6: human-in-the-loop).

    Отклонённое утверждение вычищается из памяти: иначе система растиражирует
    ошибку в последующих миссиях, ссылаясь на собственный прошлый вывод.
    """
    await _load(session, mission_id, user)

    claim = await session.get(Claim, claim_id)
    if claim is None or claim.mission_id != mission_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Утверждение не найдено")

    claim.review_status = payload.status
    claim.review_note = payload.note
    claim.reviewed_by = user.id
    claim.reviewed_at = utcnow()

    if payload.status == "rejected":
        removed = await forget_claim(session, mission_id, claim.claim_text)
        await audit(session, request, "claim.reject", user,
                    claim_id=claim_id, memory_entries_removed=removed)
    else:
        await audit(session, request, "claim.review", user,
                    claim_id=claim_id, status=payload.status)

    await session.commit()
    return ClaimOut.model_validate(claim, from_attributes=True)


@router.get("/history", response_model=list[MissionListItem])
async def history(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(None, alias="status"),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(current_user),
) -> list[MissionListItem]:
    stmt = select(Mission).order_by(Mission.created_at.desc()).limit(limit).offset(offset)
    if user.role != "admin":
        stmt = stmt.where(Mission.user_id == user.id)
    if status_filter:
        stmt = stmt.where(Mission.status == status_filter)

    missions = (await session.execute(stmt)).scalars().all()
    return [
        MissionListItem(
            id=m.id, query=m.query, mode=m.mode, status=m.status,
            confidence=m.confidence, created_at=m.created_at, completed_at=m.completed_at,
        )
        for m in missions
    ]


async def _load(session: AsyncSession, mission_id: str, user: User) -> Mission:
    mission = (
        await session.execute(
            select(Mission).where(Mission.id == mission_id).options(selectinload(Mission.tasks))
        )
    ).scalar_one_or_none()

    if mission is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Миссия не найдена")
    if user.role != "admin" and mission.user_id != user.id:
        # 404 вместо 403: существование чужой миссии - тоже информация
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Миссия не найдена")
    return mission
