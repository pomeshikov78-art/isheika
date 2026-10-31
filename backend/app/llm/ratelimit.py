"""Учёт токенного бюджета провайдера.

Бесплатные тарифы ограничивают не число запросов, а токены в минуту: у Groq
это 8000 TPM. Миссия из 13 агентов тратит около 39 000 токенов, поэтому без
учёта бюджета половина агентов падает с 429 — в прошлом прогоне так потерялся
судья W12, и отчёт остался без синтеза.

Ограничение параллелизма этой задачи не решает: два агента подряд с большими
промптами выбирают минутный лимит так же быстро, как восемь одновременно.
Считать нужно именно токены.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

log = logging.getLogger(__name__)

WINDOW = 60.0  # окно лимита, секунды


class TokenBudget:
    """Скользящее окно расхода токенов.

    Перед запросом резервируется оценка стоимости; после ответа резерв
    заменяется фактическим расходом из usage. Оценка намеренно завышена:
    занизить её опаснее, чем подождать лишнюю секунду.
    """

    def __init__(self, tokens_per_minute: int = 0) -> None:
        self.limit = tokens_per_minute
        self._spent: deque[tuple[float, int]] = deque()
        self._lock = asyncio.Lock()

    def calibrate(self, headers) -> None:
        """Провайдер сам сообщает свой лимит в заголовках ответа."""
        raw = headers.get("x-ratelimit-limit-tokens")
        if not raw:
            return
        try:
            reported = int(float(raw))
        except (TypeError, ValueError):
            return
        if reported > 0 and reported != self.limit:
            log.info("Токенный лимит провайдера: %s в минуту", reported)
            self.limit = reported

    def _prune(self, now: float) -> None:
        while self._spent and now - self._spent[0][0] > WINDOW:
            self._spent.popleft()

    def _used(self, now: float) -> int:
        self._prune(now)
        return sum(tokens for _, tokens in self._spent)

    async def reserve(self, estimate: int) -> int:
        """Ждёт, пока в окне освободится место, и резервирует оценку.

        Возвращает идентификатор резерва - момент времени, по которому
        расход потом уточняется фактическим значением.
        """
        if self.limit <= 0:
            return 0

        while True:
            async with self._lock:
                now = time.monotonic()
                used = self._used(now)
                if used + estimate <= self.limit or not self._spent:
                    self._spent.append((now, estimate))
                    return int(now * 1000)
                # Ждём, пока из окна выпадет самая старая запись
                oldest = self._spent[0][0]
                pause = max(0.2, WINDOW - (now - oldest) + 0.2)

            log.info(
                "Токенный бюджет исчерпан (%s/%s за минуту), пауза %.1f с",
                used, self.limit, pause,
            )
            await asyncio.sleep(pause)

    async def settle(self, reservation: int, actual: int) -> None:
        """Заменяет резерв фактическим расходом."""
        if self.limit <= 0 or not reservation or actual <= 0:
            return
        async with self._lock:
            target = reservation / 1000.0
            for index, (stamp, tokens) in enumerate(self._spent):
                if abs(stamp - target) < 0.002:
                    self._spent[index] = (stamp, actual)
                    return


def estimate_tokens(payload: dict) -> int:
    """Стоимость запроса в понимании провайдера.

    Ключевая деталь: провайдеры резервируют из минутного лимита ПОЛНЫЙ
    max_tokens, а не фактический размер ответа. Проверено на Groq: запрос
    с max_tokens=2048 и ответом в 300 токенов уменьшает остаток ровно
    на 2048. Попытка оценивать ответ «по типичному размеру» приводила
    к систематическому перебору лимита и веренице 429.

    Отсюда практический вывод: экономить нужно не оценку, а сам max_tokens.
    Агенты укладываются в 1024 токена, и это удваивает пропускную способность
    на том же тарифе.
    """
    characters = sum(len(str(m.get("content", ""))) for m in payload.get("messages", []))
    prompt_tokens = characters // 3 + 64
    return prompt_tokens + int(payload.get("max_tokens", 1024))
