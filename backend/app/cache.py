"""Кэш результатов внешних запросов (ТЗ 9).

ТЗ предписывает кэшировать обращения к внешним сервисам на час. Смысл двойной:
ускорение повторных миссий и экономия квот. Второе на свободных тарифах важнее:
суточный лимит модели выедается отладочными прогонами по одной и той же теме,
а поисковые API отвечают 429 на частые повторы.

Хранилище - в памяти процесса. ТЗ называет Redis; на нём же держится и очередь
задач, поэтому переход на общий кэш для нескольких реплик делается вместе
с переходом на Celery и затрагивает только этот модуль.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class TTLCache:
    """Кэш с временем жизни и ограничением размера."""

    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 2000) -> None:
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        record = self._data.get(key)
        if record is None:
            self.misses += 1
            return None

        stored_at, value = record
        if time.monotonic() - stored_at > self.ttl:
            self._data.pop(key, None)
            self.misses += 1
            return None

        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        if len(self._data) >= self.max_entries:
            self._evict()
        self._data[key] = (time.monotonic(), value)

    def _evict(self) -> None:
        """Сначала просроченные, затем самые старые - четверть хранилища."""
        now = time.monotonic()
        expired = [k for k, (stamp, _) in self._data.items() if now - stamp > self.ttl]
        for key in expired:
            self._data.pop(key, None)

        if len(self._data) >= self.max_entries:
            oldest = sorted(self._data.items(), key=lambda item: item[1][0])
            for key, _ in oldest[: max(1, self.max_entries // 4)]:
                self._data.pop(key, None)

    def stats(self) -> dict:
        total = self.hits + self.misses
        return {
            "entries": len(self._data),
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 3) if total else 0.0,
            "ttl_seconds": self.ttl,
        }

    def clear(self) -> None:
        self._data.clear()


def make_key(*parts: Any) -> str:
    """Устойчивый ключ по произвольной структуре запроса."""
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


search_cache = TTLCache()
llm_cache = TTLCache()


def configure(ttl_seconds: int) -> None:
    search_cache.ttl = ttl_seconds
    llm_cache.ttl = ttl_seconds
    log.info("Кэш внешних запросов: время жизни %s с", ttl_seconds)
