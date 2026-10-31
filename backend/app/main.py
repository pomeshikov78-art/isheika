"""Точка входа: API Gateway «Ищейки» (ТЗ 4.1).

Запуск: uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import auth, misc, missions
from .config import settings
from .cache import configure as configure_cache, llm_cache, search_cache
from .db import init_db
from .llm.factory import close_provider, get_provider
from .memory.embeddings import close_embedder
from .search.gateway import close_gateway, get_gateway
from .security import limiter

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
# Библиотеки в режиме DEBUG заливают журнал своей телеметрией: каждая
# SQL-операция aiosqlite и каждый заголовок httpx. Читать журнал миссии
# в таком виде невозможно.
for noisy in ("aiosqlite", "httpx", "httpcore", "asyncio", "multipart", "matplotlib"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

log = logging.getLogger("sleuth")

WEB_DIR = Path(__file__).parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    configure_cache(settings.cache_ttl_seconds)
    provider = get_provider()
    gateway = get_gateway()
    log.info(
        "«%s» запущена. LLM: %s/%s. Движки поиска: %s",
        settings.app_name, provider.name, provider.model,
        ", ".join(e.name for e in gateway.engines) or "нет",
    )
    if provider.name == "mock":
        log.warning(
            "LLM работает в режиме заглушки: отчёты будут демонстрационными. "
            "Задайте ключ в .env (например DEEPSEEK_API_KEY) и LLM_PROVIDER."
        )
    yield
    await close_provider()
    await close_gateway()
    await close_embedder()


app = FastAPI(
    title="Ищейка (Sleuth)",
    description=(
        "Мультиагентная система поиска, анализа, прогнозирования и риск-оценки "
        "технологий, изделий и НИР. 13 агентов W01-W13, поиск по открытым "
        "источникам, форсайт на S-кривых, оценка достоверности каждого факта."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit(request: Request, call_next):
    """ТЗ 8: не более 100 запросов в минуту на клиента."""
    if request.url.path.startswith("/api/"):
        key = request.client.host if request.client else "unknown"
        if not limiter.check(key):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Превышен лимит запросов (100/мин)"},
            )
    return await call_next(request)


app.include_router(auth.router)
app.include_router(missions.router)
app.include_router(misc.router)
app.include_router(misc.ws_router)

# Готовые отчёты и веб-интерфейс
reports_dir = settings.storage_path / "reports"
reports_dir.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")

if WEB_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index():
    page = WEB_DIR / "index.html"
    if page.exists():
        return FileResponse(page)
    return JSONResponse({"app": settings.app_name, "docs": "/docs"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=settings.debug)
