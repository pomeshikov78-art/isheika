"""Поисковый шлюз: параллельный опрос движков, агрегация, дедупликация.

ТЗ 4.1: «параллельный опрос нескольких поисковых систем, агрегация и
дедупликация результатов, преобразование в единый формат».
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from urllib.parse import urlparse, urlunparse

import httpx

from ..cache import make_key, search_cache
from ..config import Settings, settings as default_settings
from . import relevance
from .authority import domain_authority, domain_of
from .engines import (
    ArxivEngine,
    BingEngine,
    CrossrefEngine,
    DuckDuckGoEngine,
    Engine,
    GoogleCSEEngine,
    GooglePatentsEngine,
    OpenAlexEngine,
    PubMedEngine,
    SearchHit,
    SearxngEngine,
    SemanticScholarEngine,
    SerpApiEngine,
    openalex_publication_trend,
)

log = logging.getLogger(__name__)

_TRACKING_PARAMS = re.compile(r"^(utm_|fbclid|gclid|yclid|_ga|ref$|source$)")


class SearchGateway:
    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.cfg.search_timeout, connect=10.0),
            headers={"User-Agent": self.cfg.search_user_agent},
            follow_redirects=True,
        )
        self.engines = self._build_engines()
        # Троттлинг на движок: параллельно опрашиваем разные движки,
        # но к одному и тому же не бьём чаще, чем он терпит
        self._throttle: dict[str, asyncio.Lock] = {e.name: asyncio.Lock() for e in self.engines}
        self._last_call: dict[str, float] = {}
        log.info("Поисковый шлюз: %s", ", ".join(e.name for e in self.engines) or "нет движков")

    def _build_engines(self) -> list[Engine]:
        wanted = set(self.cfg.engines)
        engines: list[Engine] = []

        if "duckduckgo" in wanted:
            engines.append(DuckDuckGoEngine())
        if "arxiv" in wanted:
            engines.append(ArxivEngine())
        if "crossref" in wanted:
            engines.append(CrossrefEngine(self.cfg.openalex_mailto))
        if "pubmed" in wanted:
            engines.append(PubMedEngine())
        if "openalex" in wanted:
            engines.append(OpenAlexEngine(self.cfg.openalex_mailto))
        if "semanticscholar" in wanted:
            engines.append(SemanticScholarEngine())
        if "searxng" in wanted:
            instances = [i.strip() for i in (self.cfg.searxng_instances or "").split(",") if i.strip()]
            engines.append(SearxngEngine(instances or None))
        if "patents" in wanted:
            engines.append(GooglePatentsEngine())

        if "google_cse" in wanted and self.cfg.google_cse_key and self.cfg.google_cse_cx:
            engines.append(GoogleCSEEngine(self.cfg.google_cse_key, self.cfg.google_cse_cx))
        if "serpapi" in wanted and self.cfg.serpapi_key:
            engines.append(SerpApiEngine(self.cfg.serpapi_key))
        if "bing" in wanted and self.cfg.bing_api_key:
            engines.append(BingEngine(self.cfg.bing_api_key))

        missing = wanted - {e.name for e in engines}
        if missing:
            log.warning("Движки не активированы (нет ключа или неизвестное имя): %s",
                        ", ".join(sorted(missing)))
        return engines

    async def search(
        self,
        queries: list[str] | str,
        *,
        limit_per_engine: int | None = None,
        max_results: int = 30,
        engines: list[str] | None = None,
        relevance_query: str | None = None,
    ) -> list[SearchHit]:
        """Веерный поиск: каждый запрос уходит в каждый разрешённый движок.

        relevance_query - исходная формулировка миссии. По ней результаты
        ранжируются по смысловому соответствию: без этого в топ выходили
        цитируемые работы не по теме, найденные по общим запросам плана.
        """
        if isinstance(queries, str):
            queries = [queries]
        queries = [q.strip() for q in queries if q and q.strip()]
        if not queries:
            return []

        limit = limit_per_engine or self.cfg.search_max_results_per_engine
        active = [e for e in self.engines if engines is None or e.name in engines]
        if not active:
            log.warning("Нет активных движков для запроса %r", queries[0])
            return []

        jobs = [
            self._safe_search(engine, query, limit) for engine in active for query in queries
        ]
        batches = await asyncio.gather(*jobs)

        merged: list[SearchHit] = []
        for batch in batches:
            merged.extend(batch)

        unique = self.deduplicate(merged)
        # Сравниваем с каждой формулировкой отдельно: запросы плана двуязычные
        term_sets = relevance.build_term_sets(relevance_query or "", *queries)
        relevance.annotate_hits(unique, term_sets)

        # Релевантность - первый критерий, авторитетность и подтверждения - следующие
        unique.sort(
            key=lambda h: (
                h.extra.get("relevance", 0.0),
                len(h.extra.get("engines", [])),
                domain_authority(h.url),
            ),
            reverse=True,
        )

        # Совсем посторонние результаты отбрасываем, но только если есть чем
        # заполнить выдачу: пустой список хуже слабо релевантного источника
        relevant = [h for h in unique if h.extra.get("relevance", 0.0) > 0]
        if len(relevant) >= min(5, max_results):
            unique = relevant

        return unique[:max_results]

    async def _wait_turn(self, engine: Engine) -> None:
        """Выдерживает min_interval между обращениями к одному движку."""
        if engine.min_interval <= 0:
            return
        lock = self._throttle.setdefault(engine.name, asyncio.Lock())
        async with lock:
            previous = self._last_call.get(engine.name)
            now = time.monotonic()
            if previous is not None:
                pause = engine.min_interval - (now - previous)
                if pause > 0:
                    await asyncio.sleep(pause)
            self._last_call[engine.name] = time.monotonic()

    async def _safe_search(self, engine: Engine, query: str, limit: int) -> list[SearchHit]:
        """Ошибка отдельного движка гасится: шлюз обязан пережить падение канала."""
        cache_key = make_key("search", engine.name, query, limit)
        cached = search_cache.get(cache_key)
        if cached is not None:
            log.debug("%s: %s результатов из кэша по %r", engine.name, len(cached), query)
            return list(cached)

        try:
            await self._wait_turn(engine)
            hits = await engine.search(self._client, query, limit)
            log.debug("%s: %s результатов по %r", engine.name, len(hits), query)
            # Пустую выдачу тоже кэшируем: повторять заведомо безрезультатный
            # запрос к движку с лимитами - худшее, что можно сделать
            search_cache.set(cache_key, list(hits))
            return hits
        except Exception as exc:
            log.warning("Движок %s не отработал по запросу %r: %s", engine.name, query, exc)
            return []

    @staticmethod
    def normalize_url(url: str) -> str:
        """Ключ дедупликации: без варианта схемы, www, tracking-параметров и якоря."""
        try:
            parsed = urlparse(url.strip())
        except ValueError:
            return url.strip().lower()

        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = parsed.path.rstrip("/") or "/"
        query = "&".join(
            part
            for part in parsed.query.split("&")
            if part and not _TRACKING_PARAMS.match(part.split("=")[0])
        )
        return urlunparse(("https", host, path, "", query, ""))

    @classmethod
    def deduplicate(cls, hits: list[SearchHit]) -> list[SearchHit]:
        """Схлопывает дубликаты по нормализованному URL.

        Побеждает вариант с наиболее полными метаданными, а список движков,
        нашедших источник, сохраняется: подтверждение из нескольких независимых
        каналов - это сигнал для W11 (Confidence Analyst).
        """
        best: dict[str, SearchHit] = {}
        for hit in hits:
            if not hit.url or not hit.url.startswith("http"):
                continue
            key = cls.normalize_url(hit.url)
            existing = best.get(key)
            if existing is None:
                hit.extra.setdefault("engines", [])
                if hit.engine and hit.engine not in hit.extra["engines"]:
                    hit.extra["engines"].append(hit.engine)
                best[key] = hit
                continue

            engines = existing.extra.setdefault("engines", [])
            if hit.engine and hit.engine not in engines:
                engines.append(hit.engine)
            if len(hit.snippet or "") > len(existing.snippet or ""):
                existing.snippet = hit.snippet
            if not existing.published_at and hit.published_at:
                existing.published_at = hit.published_at
            if not existing.title and hit.title:
                existing.title = hit.title
            if existing.source_type == "web" and hit.source_type != "web":
                existing.source_type = hit.source_type

        return sorted(
            best.values(),
            key=lambda h: (
                len(h.extra.get("engines", [])),
                domain_authority(h.url),
                len(h.snippet or ""),
            ),
            reverse=True,
        )

    @staticmethod
    def annotate(hits: list[SearchHit]) -> list[dict]:
        """Единый формат для агентов: с доменом и предварительной авторитетностью."""
        return [
            {
                "url": h.url,
                "title": h.title,
                "snippet": h.snippet,
                "published_at": h.published_at,
                "domain": domain_of(h.url),
                "source_type": h.source_type,
                "engines": h.extra.get("engines", [h.engine] if h.engine else []),
                "authority": domain_authority(h.url),
                "relevance": h.extra.get("relevance"),
                "extra": {k: v for k, v in h.extra.items() if k != "engines"},
            }
            for h in hits
        ]

    async def publication_trend(self, query: str) -> list[dict]:
        """Фактическая динамика публикаций по годам (для S-кривой агента W06)."""
        try:
            await self._wait_turn(OpenAlexEngine())
            return await openalex_publication_trend(
                self._client, query, mailto=self.cfg.openalex_mailto
            )
        except Exception as exc:
            log.warning("Динамика публикаций не получена: %s", exc)
            return []

    async def aclose(self) -> None:
        await self._client.aclose()


_gateway: SearchGateway | None = None


def get_gateway() -> SearchGateway:
    global _gateway
    if _gateway is None:
        _gateway = SearchGateway()
    return _gateway


async def close_gateway() -> None:
    global _gateway
    if _gateway is not None:
        await _gateway.aclose()
        _gateway = None
