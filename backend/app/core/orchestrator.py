"""Mission Core: оркестратор выполнения миссии (ТЗ 4.1, 6.1-6.13).

Реализация MVP - внутрипроцессный asyncio вместо Celery. Контракт задачи
(agent_id, context, timeout, retry_count) совпадает с ТЗ 14.1, поэтому замена
исполнителя на Celery/Redis сводится к подмене _run_task и не затрагивает
ни агентов, ни планировщик.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agents.base import AgentContext, AgentResult
from ..agents.registry import get_agent
from ..config import settings
from ..db import SessionLocal
from ..llm.factory import get_provider
from ..memory import store as memory_store
from ..models import Claim, Conflict, Document, Evidence, Forecast, Mission, Risk, Source, Task
from ..search.gateway import get_gateway
from . import conflicts as conflict_detector
from .events import bus
from .planner import plan, topological_levels

log = logging.getLogger(__name__)

_running: dict[str, asyncio.Task] = {}
_mission_slots = asyncio.Semaphore(settings.max_concurrent_missions)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def launch(mission_id: str) -> None:
    """Ставит миссию на выполнение в фоне (ТЗ 6.12: ответ асинхронный)."""
    if mission_id in _running:
        return
    task = asyncio.create_task(_guarded_run(mission_id), name=f"mission-{mission_id}")
    _running[mission_id] = task
    task.add_done_callback(lambda _: _running.pop(mission_id, None))


async def cancel(mission_id: str) -> bool:
    task = _running.get(mission_id)
    if task is None:
        return False
    task.cancel()
    return True


def is_running(mission_id: str) -> bool:
    return mission_id in _running


async def _guarded_run(mission_id: str) -> None:
    try:
        async with _mission_slots:
            await asyncio.wait_for(_run(mission_id), timeout=settings.mission_timeout)
    except asyncio.CancelledError:
        await _fail(mission_id, "Миссия отменена пользователем", status="cancelled")
        raise
    except asyncio.TimeoutError:
        await _fail(mission_id, f"Превышен таймаут миссии ({settings.mission_timeout} с)")
    except Exception as exc:
        log.exception("Миссия %s завершилась ошибкой", mission_id)
        await _fail(mission_id, str(exc))


async def _fail(mission_id: str, error: str, status: str = "failed") -> None:
    async with SessionLocal() as session:
        mission = await session.get(Mission, mission_id)
        if mission is None:
            return
        mission.status = status
        mission.error = error[:2000]
        mission.completed_at = utcnow()
        await session.commit()
    bus.publish(mission_id, "ERROR", {"error": error, "status": status})


async def _run(mission_id: str) -> None:
    llm = get_provider()
    gateway = get_gateway()

    async with SessionLocal() as session:
        mission = await session.get(Mission, mission_id)
        if mission is None:
            raise RuntimeError(f"Миссия {mission_id} не найдена")

        mission.status = "planning"
        mission.started_at = utcnow()
        await session.commit()
        bus.publish(mission_id, "PROGRESS", {"stage": "planning", "percent": 5})

        # [2] Поиск в памяти до декомпозиции (ТЗ 6.2)
        memory_hits = await memory_store.recall(
            session, mission.query, mission_id=mission_id, exclude_mission_id=mission_id
        )
        await session.commit()
        if memory_hits:
            bus.publish(mission_id, "MEMORY_HIT", {"count": len(memory_hits)})

        # [3-4] Декомпозиция и построение DAG
        mission_plan = await plan(
            llm,
            mission.query,
            mode=mission.mode,
            memory_context=memory_store.as_context(memory_hits),
            # Поле agents из запроса принималось API, но до планировщика
            # не доходило - явный выбор пользователя игнорировался
            requested_agents=(mission.params or {}).get("agents"),
        )
        mission.plan = mission_plan
        mission.status = "running"

        documents = await _load_documents(session, mission)
        tasks = await _create_tasks(session, mission, mission_plan)
        await session.commit()

        query = mission.query
        horizons = list(mission.horizons or [1, 3, 5, 10])
        max_sources = int((mission.params or {}).get("max_sources", 20))

    bus.publish(mission_id, "PLAN_READY", {
        "agents": mission_plan["agents"],
        "task_type": mission_plan["task_type"],
        "entities": mission_plan["entities"],
        "search_queries": mission_plan["search_queries"],
    })

    # [5] Выполнение уровней DAG: внутри уровня агенты идут параллельно
    levels = topological_levels(mission_plan["dag"])
    results: dict[str, dict] = {}
    shared_params: dict = {
        "search_queries": mission_plan["search_queries"],
        "task_type": mission_plan["task_type"],
        "conflicts": [],
    }
    slots = asyncio.Semaphore(settings.max_concurrent_agents)
    total = sum(len(level) for level in levels) or 1
    done = 0

    for level in levels:
        coros = [
            _run_task(
                slots=slots,
                mission_id=mission_id,
                task_id=tasks[agent_id],
                agent_id=agent_id,
                query=query,
                entities=mission_plan["entities"],
                horizons=horizons,
                params=shared_params,
                max_sources=max_sources,
                memory=memory_hits,
                documents=documents,
                upstream=results,
                llm=llm,
                gateway=gateway,
            )
            for agent_id in level
        ]
        for agent_id, result in zip(level, await asyncio.gather(*coros)):
            results[agent_id] = result.to_dict()
            done += 1

        bus.publish(mission_id, "PROGRESS", {
            "stage": "agents",
            "percent": 5 + int(80 * done / total),
            "completed": done,
            "total": total,
        })

        # [7] Противоречия ищем по мере накопления результатов, чтобы W11/W12
        # получили их уже в контексте
        found = conflict_detector.detect(results)
        if found and found != shared_params["conflicts"]:
            shared_params["conflicts"] = found
            for conflict in found:
                bus.publish(mission_id, "CONFLICT_FOUND", conflict)

    await _persist(mission_id, results, shared_params["conflicts"])
    await _finish(mission_id, results)


async def _run_task(
    *,
    slots: asyncio.Semaphore,
    mission_id: str,
    task_id: str,
    agent_id: str,
    query: str,
    entities: list[str],
    horizons: list[int],
    params: dict,
    max_sources: int,
    memory: list[dict],
    documents: list[dict],
    upstream: dict[str, dict],
    llm,
    gateway,
) -> AgentResult:
    """Выполнение одной задачи с таймаутом и повторами (ТЗ 6.5: до 3 попыток
    с экспоненциальной задержкой)."""
    agent = get_agent(agent_id)
    context = AgentContext(
        mission_id=mission_id,
        task_id=task_id,
        query=query,
        entities=entities,
        horizons=horizons,
        params=params,
        max_sources=max_sources,
        timeout=settings.agent_timeout,
        memory=memory,
        documents=documents,
        upstream=upstream,
        llm=llm,
        search=gateway,
    )

    await _update_task(task_id, status="running", started_at=utcnow())
    bus.publish(mission_id, "TASK_STARTED", {"agent_id": agent_id, "name": agent.name})

    delay = 2.0
    last_error = ""

    async with slots:
        for attempt in range(settings.agent_max_retries):
            try:
                result = await asyncio.wait_for(
                    agent.execute(context), timeout=settings.agent_timeout
                )
                await _update_task(
                    task_id, status="succeeded", output=result.to_dict(),
                    finished_at=utcnow(), retries=attempt,
                )
                bus.publish(mission_id, "TASK_COMPLETED", {
                    "agent_id": agent_id,
                    "confidence": round(result.confidence, 3),
                    "summary": result.summary[:400],
                })
                bus.publish(mission_id, "AGENT_OUTPUT", {
                    "agent_id": agent_id,
                    "metrics": result.metrics,
                })
                return result

            except asyncio.TimeoutError:
                last_error = f"Таймаут {settings.agent_timeout} с"
            except asyncio.CancelledError:
                await _update_task(task_id, status="failed", error="Отменено", finished_at=utcnow())
                raise
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                log.warning("Агент %s, попытка %s: %s", agent_id, attempt + 1, last_error)

            if attempt < settings.agent_max_retries - 1:
                await _update_task(task_id, status="retry", retries=attempt + 1)
                await asyncio.sleep(delay)
                delay *= 2

    await _update_task(task_id, status="failed", error=last_error, finished_at=utcnow())
    bus.publish(mission_id, "TASK_FAILED", {"agent_id": agent_id, "error": last_error})

    # Падение одного агента не должно ронять миссию: остальные продолжают,
    # а факт отказа попадает в отчёт как нерешённый вопрос.
    return AgentResult(
        agent_id=agent_id,
        status="failed",
        error=last_error,
        confidence=0.0,
        unresolved=[f"Агент {agent_id} ({agent.name}) не выполнен: {last_error}"],
    )


async def _update_task(task_id: str, **fields) -> None:
    async with SessionLocal() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return
        for key, value in fields.items():
            setattr(task, key, value)
        await session.commit()


async def _create_tasks(session: AsyncSession, mission: Mission, mission_plan: dict) -> dict[str, str]:
    ids: dict[str, str] = {}
    for agent_id in mission_plan["agents"]:
        task = Task(
            mission_id=mission.id,
            agent_id=agent_id,
            depends_on=mission_plan["dag"].get(agent_id, []),
            input={"query": mission.query, "entities": mission_plan["entities"]},
        )
        session.add(task)
        await session.flush()
        ids[agent_id] = task.id
    return ids


async def _load_documents(session: AsyncSession, mission: Mission) -> list[dict]:
    doc_ids = (mission.params or {}).get("user_document_ids") or []
    if not doc_ids:
        return []
    rows = (
        await session.execute(select(Document).where(Document.id.in_(doc_ids)))
    ).scalars().all()
    return [
        {"id": d.id, "filename": d.filename, "text": (d.text or "")[:20000], "pages": d.pages}
        for d in rows
    ]


async def _persist(mission_id: str, results: dict[str, dict], conflicts: list[dict]) -> None:
    """Раскладка результатов агентов по таблицам ТЗ 11.4-11.9."""
    async with SessionLocal() as session:
        # Источники и доказательства
        audited = results.get("W08", {}).get("sources") or []
        raw_sources = audited or [
            s for agent_id in ("W01", "W02") for s in results.get(agent_id, {}).get("sources", [])
        ]
        seen_urls: set[str] = set()
        for source in raw_sources:
            url = source.get("url")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            reliability = float(source.get("reliability", source.get("authority", 0.5)))

            session.add(Evidence(
                mission_id=mission_id,
                source_type=source.get("source_type", "web"),
                source_url=url,
                title=(source.get("title") or "")[:1000],
                snippet=(source.get("snippet") or "")[:4000],
                published_at=source.get("published_at"),
                reliability=reliability,
            ))
            await _upsert_source(session, url, source.get("domain", ""), reliability)

        # Утверждения агентов
        for agent_id, result in results.items():
            for claim in result.get("claims", []):
                session.add(Claim(
                    mission_id=mission_id,
                    agent_id=agent_id,
                    claim_text=claim.get("text", "")[:4000],
                    claim_type=claim.get("type", "fact"),
                    confidence=float(claim.get("confidence", 0.5)),
                    evidence_ids=claim.get("evidence_urls", []),
                ))

        # Риски (W05)
        for risk in results.get("W05", {}).get("calculations", {}).get("risks", []):
            session.add(Risk(
                mission_id=mission_id,
                risk_type=risk.get("type", "technical"),
                title=risk.get("title", "")[:1000],
                probability=float(risk.get("probability", 0.5)),
                impact=float(risk.get("impact", 0.5)),
                exposure=float(risk.get("exposure", 0.25)),
                mitigation=risk.get("mitigation"),
                status=risk.get("status", "identified"),
            ))

        # Прогнозы (W06)
        forecast_calc = results.get("W06", {}).get("calculations", {})
        metric = results.get("W06", {}).get("metrics", {}).get("metric") or "adoption"
        for scenario in forecast_calc.get("scenarios", []):
            session.add(Forecast(
                mission_id=mission_id,
                metric=str(metric)[:128],
                horizon=int(scenario.get("horizon", 0)),
                scenario=scenario.get("scenario", "base"),
                value=float(scenario.get("value", 0.0)),
                probability=scenario.get("probability"),
                confidence_interval=scenario.get("confidence_interval", {}),
            ))

        # Противоречия и их разрешение судьёй (W12)
        resolutions = {
            str(r.get("conflict", "")): r.get("resolution")
            for r in results.get("W12", {}).get("calculations", {}).get("conflicts_resolution", [])
        }
        for conflict in conflicts:
            description = conflict.get("description", "")
            resolution = resolutions.get(description) or _best_match(resolutions, description)
            session.add(Conflict(
                mission_id=mission_id,
                description=description[:4000],
                agents=conflict.get("agents", []),
                resolution=resolution,
                resolved_at=utcnow() if resolution else None,
            ))

        await session.commit()


async def _upsert_source(session: AsyncSession, url: str, domain: str, reliability: float) -> None:
    existing = (
        await session.execute(select(Source).where(Source.url == url))
    ).scalar_one_or_none()
    if existing is None:
        session.add(Source(url=url, domain=domain or "", reliability_rating=reliability))
    else:
        existing.reliability_rating = reliability
        existing.last_checked = utcnow()


def _best_match(resolutions: dict[str, str], description: str) -> str | None:
    """Судья пересказывает конфликт своими словами - ищем по пересечению слов."""
    if not resolutions or not description:
        return None
    words = set(description.lower().split())
    best_key, best_score = None, 0.0
    for key in resolutions:
        overlap = len(words & set(key.lower().split()))
        score = overlap / max(1, len(words))
        if score > best_score:
            best_key, best_score = key, score
    return resolutions[best_key] if best_key and best_score >= 0.3 else None


async def _finish(mission_id: str, results: dict[str, dict]) -> None:
    """[13-16] Отчёт, экспорт, память, завершение миссии."""
    from ..report.builder import render_all

    report = results.get("W13", {}).get("calculations", {}).get("report")
    if not report:
        await _fail(mission_id, "Finalizer (W13) не сформировал отчёт")
        return

    bus.publish(mission_id, "PROGRESS", {"stage": "report", "percent": 90})

    async with SessionLocal() as session:
        mission = await session.get(Mission, mission_id)
        if mission is None:
            return

        report["mission_id"] = mission_id
        report["mode"] = mission.mode
        report["created_at"] = mission.created_at.isoformat() if mission.created_at else None

        try:
            files = await asyncio.to_thread(render_all, report)
        except Exception as exc:
            # Отчёт в JSON уже есть: провал рендеринга не обесценивает миссию
            log.exception("Рендеринг отчёта миссии %s не удался", mission_id)
            files = {}
            report.setdefault("unresolved_issues", []).append(
                f"Экспорт файлов отчёта не выполнен: {exc}"
            )

        report["files"] = files
        mission.result = report
        mission.confidence = report.get("confidence")
        mission.status = "completed"
        mission.completed_at = utcnow()
        mission.report_url = files.get("html")

        await _save_memory(session, mission_id, report)
        await session.commit()

    bus.publish(mission_id, "PROGRESS", {"stage": "done", "percent": 100})
    bus.publish(mission_id, "FINISHED", {
        "confidence": round(float(report.get("confidence") or 0.0), 3),
        "files": report.get("files", {}),
        "summary": report.get("summary", "")[:500],
    })


async def _save_memory(session: AsyncSession, mission_id: str, report: dict) -> None:
    """[15] Извлечение фактов и выводов в память со связями между ними (ТЗ 6.12, 11.12).

    Плоский список фактов почти бесполезен: по нему не видно, из какого вывода
    факт получен и что этому выводу возражали. Поэтому вместе с записями
    сохраняется и граф связей.
    """
    confidence = float(report.get("confidence", 0.5))
    source_urls = [s.get("url") for s in report.get("sources", [])[:10] if s.get("url")]

    # Корневая запись миссии: к ней привязывается всё остальное
    conclusion = await memory_store.remember(
        session, mission_id=mission_id, entry_type="inference",
        content=f"{report.get('query')} -> {report.get('summary')}",
        confidence=confidence, source_urls=source_urls,
    )

    for finding in (report.get("key_findings") or [])[:10]:
        entry = await memory_store.remember(
            session, mission_id=mission_id, entry_type="fact",
            content=str(finding), confidence=confidence, source_urls=source_urls,
        )
        await memory_store.link(session, entry, conclusion, "supports")

    for risk in (report.get("risks") or [])[:6]:
        entry = await memory_store.remember(
            session, mission_id=mission_id, entry_type="risk",
            content=f"{risk.get('type_ru', risk.get('type'))}: {risk.get('title')} "
                    f"(экспозиция {risk.get('exposure')})",
            confidence=confidence,
        )
        await memory_store.link(session, entry, conclusion, "related_to")

    for scenario in (report.get("forecast", {}).get("scenarios") or []):
        if scenario.get("scenario") != "base":
            continue
        entry = await memory_store.remember(
            session, mission_id=mission_id, entry_type="forecast",
            content=f"{report.get('query')}: базовый сценарий на {scenario.get('horizon')} лет "
                    f"({scenario.get('year')}) = {scenario.get('value')}",
            confidence=confidence,
        )
        await memory_store.link(session, entry, conclusion, "derived_from")

    for recommendation in (report.get("recommendations") or [])[:5]:
        entry = await memory_store.remember(
            session, mission_id=mission_id, entry_type="recommendation",
            content=str(recommendation.get("text", "")), confidence=confidence,
        )
        await memory_store.link(session, entry, conclusion, "derived_from")

    # Возражения критика и red team - тоже знание о выводе, и притом самое
    # ценное при повторном обращении к теме: они помечаются как противоречащие
    criticism = report.get("criticism", {})
    objections = [str(i.get("text", "")) for i in (criticism.get("issues") or [])[:4]]
    objections += [str(c.get("text", "")) for c in (criticism.get("counterarguments") or [])[:4]]
    for objection in objections:
        entry = await memory_store.remember(
            session, mission_id=mission_id, entry_type="inference",
            content=f"Возражение: {objection}", confidence=confidence,
        )
        await memory_store.link(session, entry, conclusion, "contradicts")