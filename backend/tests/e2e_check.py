"""Сквозной прогон миссии без сети: заглушка LLM + подменённый поисковый шлюз."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core import orchestrator
from app.db import SessionLocal, init_db
from app.models import Claim, Evidence, Forecast, MemoryEntry, Mission, Risk
from app.search.engines import SearchHit
from app.search.gateway import SearchGateway


class FakeGateway(SearchGateway):
    """Реальная логика дедупликации и аннотирования, но без похода в сеть."""

    def __init__(self):
        self.cfg = __import__("app.config", fromlist=["settings"]).settings
        self._client = None
        self.engines = []

    async def search(self, queries, *, limit_per_engine=None, max_results=30, engines=None):
        hits = [
            SearchHit(url="https://www.nature.com/articles/s41560-024-01234", engine="crossref",
                      title="Solid-state battery scale-up", snippet="Обзор технологии.",
                      published_at="2024-05-01", source_type="publication"),
            SearchHit(url="https://arxiv.org/abs/2403.00123", engine="arxiv",
                      title="Sulfide electrolytes review", snippet="Электролиты.",
                      published_at="2024-03-02", source_type="publication"),
            SearchHit(url="https://www.reuters.com/tech/qs-2025", engine="duckduckgo",
                      title="QuantumScape milestone", snippet="Новости рынка.", published_at="2025-01-20"),
            SearchHit(url="https://patents.google.com/patent/US11876543B2", engine="patents",
                      title="US11876543B2 Solid electrolyte", snippet="Патент.",
                      published_at="2024-01-16", source_type="patent"),
            SearchHit(url="https://medium.com/@blogger/batteries", engine="duckduckgo",
                      title="Мой взгляд на батареи", snippet="Блог."),
        ]
        return self.deduplicate(hits)[:max_results]

    async def aclose(self):
        return None


async def main() -> int:
    await init_db()
    orchestrator.get_gateway = FakeGateway  # подмена шлюза на время прогона

    async with SessionLocal() as session:
        mission = Mission(
            query="Оцени перспективы твердотельных аккумуляторов до 2035 года",
            mode="comprehensive",
            horizons=[1, 3, 5, 10],
            params={"max_sources": 20},
        )
        session.add(mission)
        await session.commit()
        mission_id = mission.id

    await orchestrator._run(mission_id)

    async with SessionLocal() as session:
        mission = await session.get(Mission, mission_id)
        report = mission.result or {}
        counts = {}
        for model in (Evidence, Claim, Risk, Forecast, MemoryEntry):
            rows = (await session.execute(__import__("sqlalchemy").select(model))).scalars().all()
            counts[model.__name__] = len(rows)

    print("status          :", mission.status)
    print("confidence      :", mission.confidence)
    print("agents used     :", report.get("agents_used"))
    print("sources         :", len(report.get("sources", [])))
    print("risks           :", len(report.get("risks", [])))
    print("scenarios       :", len(report.get("forecast", {}).get("scenarios", [])))
    print("unresolved      :", len(report.get("unresolved_issues", [])))
    print("files           :", report.get("files"))
    print("db rows         :", counts)
    print("summary         :", (report.get("summary") or "")[:160])

    ok = mission.status == "completed" and report.get("files", {}).get("html")
    print("\nRESULT:", "OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
