"""Тесты API: авторизация, RBAC, жизненный цикл миссии."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app as fastapi_app


@pytest.fixture
async def client(tmp_path, monkeypatch):
    # Отдельная БД на прогон: тесты не должны трогать рабочую sleuth.db
    monkeypatch.setattr("app.config.settings.database_url", f"sqlite+aiosqlite:///{tmp_path}/t.db")
    import app.db as db_module
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/t.db")
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", async_sessionmaker(engine, expire_on_commit=False))
    # Тесты не должны ходить в интернет: запуск миссии подменяется заглушкой,
    # сам конвейер проверяется отдельно в e2e_check.py
    async def _no_launch(mission_id: str) -> None:
        return None

    monkeypatch.setattr("app.core.orchestrator.launch", _no_launch)
    monkeypatch.setattr("app.api.missions.orchestrator.launch", _no_launch)

    import importlib

    importlib.import_module("app.models")  # регистрация таблиц в метаданных
    async with engine.begin() as conn:
        await conn.run_sync(db_module.Base.metadata.create_all)

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


async def test_health_reports_configuration(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["agents"] == 13


async def test_agents_endpoint_lists_all_thirteen(client):
    response = await client.get("/api/v1/agents")
    assert response.status_code == 200

    agents = response.json()
    assert [a["id"] for a in agents] == [f"W{i:02d}" for i in range(1, 14)]
    assert all(a["name"] and a["description"] for a in agents)


async def test_missions_require_authentication(client):
    response = await client.post("/api/v1/missions", json={"query": "тестовый запрос"})
    assert response.status_code == 401


async def test_first_user_becomes_admin_and_can_create_mission(client):
    registered = await client.post(
        "/api/v1/auth/register", json={"username": "analyst1", "password": "secret-pass-123"}
    )
    assert registered.status_code == 201
    assert registered.json()["role"] == "admin"  # первый пользователь

    token = registered.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    created = await client.post(
        "/api/v1/missions",
        json={"query": "Оцени перспективы водородной авиации", "mode": "quick"},
        headers=headers,
    )
    assert created.status_code == 202
    mission_id = created.json()["mission_id"]

    fetched = await client.get(f"/api/v1/missions/{mission_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["query"].startswith("Оцени")

    history = await client.get("/api/v1/history", headers=headers)
    assert history.status_code == 200
    assert any(m["id"] == mission_id for m in history.json())


async def test_guest_cannot_create_mission(client):
    await client.post("/api/v1/auth/register", json={"username": "owner", "password": "secret-pass-123"})
    guest = await client.post(
        "/api/v1/auth/register",
        json={"username": "guest1", "password": "secret-pass-123", "role": "guest"},
    )
    headers = {"Authorization": f"Bearer {guest.json()['access_token']}"}

    response = await client.post("/api/v1/missions", json={"query": "запрос гостя"}, headers=headers)
    assert response.status_code == 403


async def test_duplicate_username_rejected(client):
    payload = {"username": "dup", "password": "secret-pass-123"}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 409


async def test_wrong_password_rejected(client):
    await client.post("/api/v1/auth/register", json={"username": "u1", "password": "secret-pass-123"})
    response = await client.post(
        "/api/v1/auth/login", json={"username": "u1", "password": "wrong-password"}
    )
    assert response.status_code == 401


def test_planner_suggestion_cannot_shrink_user_choice():
    """Режим comprehensive выбран пользователем и означает «все тринадцать».
    Предложение планировщика вправе только дополнить набор."""
    from app.agents.registry import REGISTRY, resolve_agents

    full = resolve_agents("comprehensive", None, ["W01", "W02"])
    assert len(full) == len(REGISTRY)

    # В сокращённом режиме предложение добавляет агента, но не ломает состав
    quick = resolve_agents("quick", None, ["W06"])
    assert "W06" in quick
    assert len(quick) < len(REGISTRY)

    # Явный список из запроса замещает режим, обязательные агенты остаются
    explicit = resolve_agents("comprehensive", ["W01", "W03"], None)
    assert set(explicit) >= {"W01", "W03", "W08", "W11", "W12", "W13"}
    assert "W05" not in explicit


async def test_memory_links_get_ids(client):
    """Связь графа памяти нельзя построить до вставки записи: идентификатор
    присваивается только при ней, и рёбра уходили в базу с NULL."""
    import app.db as db_module
    from app.memory.store import link, remember

    async with db_module.SessionLocal() as session:
        first = await remember(session, mission_id=None, entry_type="fact",
                               content="Твердотельные аккумуляторы достигли TRL 6")
        second = await remember(session, mission_id=None, entry_type="inference",
                                content="Коммерциализация ожидается после 2027 года")
        assert first.id and second.id

        edge = await link(session, first, second, "supports")
        assert edge is not None
        assert edge.source_entry_id == first.id
        assert edge.target_entry_id == second.id

        # Связь с несуществующей или самой собой не создаётся
        assert await link(session, first, None, "supports") is None
        assert await link(session, first, first, "supports") is None
        await session.commit()
