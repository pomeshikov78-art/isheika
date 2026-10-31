"""Провайдер для OpenAI-совместимых API: DeepSeek, Qwen (DashScope), OpenAI, Ollama.

Все четыре говорят на одном протоколе POST /chat/completions, поэтому смысла
в четырёх клиентах нет: различаются только base_url, ключ и имя модели.
"""
from __future__ import annotations

import asyncio
import logging
import re

import httpx

from ..cache import llm_cache, make_key
from .base import LLMError, LLMMessage, LLMProvider, LLMResponse
from .ratelimit import TokenBudget, estimate_tokens

log = logging.getLogger(__name__)

RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

# Модели, тратящие часть бюджета на скрытое рассуждение. У них max_tokens -
# это общий котёл на размышление и ответ, поэтому обычного лимита не хватает:
# модель додумывает до конца и обрывается, не начав отвечать.
_REASONING_MARKERS = ("gpt-oss", "o1", "o3", "o4", "-r1", "reasoner", "thinking", "qwq")
REASONING_ALLOWANCE = 900

# «Please try again in 7.5s» - Groq и другие пишут точное время в тексте ошибки
_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)\s*s", re.I)
MAX_RETRY_WAIT = 90.0


class OpenAICompatProvider(LLMProvider):
    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        supports_json_mode: bool = True,
        max_retries: int = 2,
        rate_limit_retries: int = 4,
        max_concurrency: int = 2,
        tokens_per_minute: int = 0,
        fallback_models: list[str] | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.supports_json_mode = supports_json_mode
        self.max_retries = max_retries
        self.rate_limit_retries = rate_limit_retries
        # Ограничение одновременных запросов к модели. Оркестратор запускает
        # уровень DAG целиком, и восемь агентов разом выбирали минутный лимит
        # токенов бесплатного тарифа: половина задач падала с 429, включая судью.
        self._gate = asyncio.Semaphore(max(1, max_concurrency))
        # Лимит бесплатных тарифов считается в токенах в минуту, а не в запросах
        self.budget = TokenBudget(tokens_per_minute)
        # Суточная квота у провайдера своя на каждую модель. Когда основная
        # исчерпана, разумнее продолжить на запасной, чем останавливать миссию:
        # за отладочный день мы дважды упирались в этот лимит.
        self.fallback_models = [m.strip() for m in (fallback_models or []) if m.strip()]
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0),
            headers=self._headers(),
        )

    @property
    def is_reasoning_model(self) -> bool:
        name = (self.model or "").lower()
        return any(marker in name for marker in _REASONING_MARKERS)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        requested = self.max_tokens if max_tokens is None else max_tokens
        payload: dict = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": requested + (REASONING_ALLOWANCE if self.is_reasoning_model else 0),
            "stream": False,
        }
        if json_mode and self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        # Кэш по полному телу запроса. При низкой температуре ответы модели
        # почти детерминированы, а повторный прогон той же миссии - обычное
        # дело при отладке и демонстрации: на свободном тарифе это прямая
        # экономия суточной квоты.
        cache_key = make_key("llm", self.base_url, payload)
        data = llm_cache.get(cache_key)
        if data is None:
            data = await self._post_with_retries("/chat/completions", payload)
            llm_cache.set(cache_key, data)

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"Неожиданный формат ответа {self.name}: {data}") from exc

        # Модели с рассуждением (gpt-oss, o-серия, R1) тратят часть бюджета
        # на скрытые токены. При тесном max_tokens ответ обрывается ещё
        # в рассуждении, и content приходит пустым - без этой проверки
        # наверх ушло бы невнятное «Пустой ответ модели».
        if not (text or "").strip():
            usage = data.get("usage") or {}
            reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0)
            if choice.get("finish_reason") == "length" or reasoning:
                raise LLMError(
                    f"{self.name}: модель израсходовала лимит на внутреннее рассуждение "
                    f"({reasoning} токенов) и не выдала ответ. Увеличьте LLM_MAX_TOKENS "
                    f"(сейчас {payload['max_tokens']})."
                )
            raise LLMError(f"{self.name}: пустой ответ модели")

        return LLMResponse(text=text, model=data.get("model", self.model),
                           usage=data.get("usage") or {})

    @staticmethod
    def _json_mode_rejected(response: httpx.Response, payload: dict) -> bool:
        """Отказ именно из-за строгого JSON-режима, а не из-за самого запроса."""
        if "response_format" not in payload:
            return False
        body = (response.text or "").lower()
        return "json_validate_failed" in body or "failed to validate json" in body

    def _next_model(self) -> str | None:
        """Следующая непочатая модель из списка запасных."""
        while self.fallback_models:
            candidate = self.fallback_models.pop(0)
            if candidate != self.model:
                return candidate
        return None

    @staticmethod
    def _is_daily_limit(response: httpx.Response) -> bool:
        if response.headers.get("x-should-retry", "").lower() == "false":
            return True
        body = (response.text or "").lower()
        return "per day" in body or "tpd" in body

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        """Сколько провайдер просит подождать: сначала заголовок, потом текст."""
        header = response.headers.get("retry-after")
        if header:
            try:
                return min(MAX_RETRY_WAIT, float(header))
            except ValueError:
                pass
        found = _RETRY_AFTER_RE.search(response.text or "")
        if found:
            try:
                return min(MAX_RETRY_WAIT, float(found.group(1)))
            except ValueError:
                pass
        return None

    async def _post_with_retries(self, path: str, payload: dict) -> dict:
        url = f"{self.base_url}{path}"
        delay = 1.0
        last_error: Exception | None = None
        # Лимит частоты - не сбой, а нормальное состояние бесплатного тарифа,
        # поэтому таких попыток даём больше, чем на обычные ошибки
        attempts_left = self.max_retries
        rate_limit_left = self.rate_limit_retries
        attempt = 0

        while True:
            attempt += 1
            wait: float | None = None
            try:
                reservation = await self.budget.reserve(estimate_tokens(payload))
                async with self._gate:
                    response = await self._client.post(url, json=payload)
                self.budget.calibrate(response.headers)

                if response.status_code == 429:
                    # Провайдер отличает минутный лимит от суточного. Суточный
                    # ожиданием не лечится: Groq помечает такой отказ заголовком
                    # x-should-retry: false и просит вернуться через часы.
                    # Повторять здесь - значит выдавать «таймаут агента» вместо
                    # понятного «дневной лимит модели исчерпан».
                    if self._is_daily_limit(response):
                        replacement = self._next_model()
                        if replacement is None:
                            raise LLMError(
                                f"{self.name}: исчерпан суточный лимит токенов модели "
                                f"{self.model!r}, запасных моделей не осталось. "
                                f"Дождитесь сброса квоты или поднимите тариф. "
                                f"Ответ провайдера: {response.text[:200]}"
                            )
                        log.warning(
                            "%s: суточный лимит модели %s исчерпан, переключаюсь на %s",
                            self.name, self.model, replacement,
                        )
                        self.model = replacement
                        payload["model"] = replacement
                        continue
                    wait = self._retry_after(response) or delay
                    last_error = LLMError(
                        f"{self.name}: лимит частоты запросов, провайдер просит "
                        f"подождать {wait:.1f} с"
                    )
                    if rate_limit_left <= 0:
                        break
                    rate_limit_left -= 1
                elif response.status_code in RETRYABLE_STATUS:
                    last_error = LLMError(
                        f"{self.name} HTTP {response.status_code}: {response.text[:200]}"
                    )
                elif response.status_code == 400 and self._json_mode_rejected(response, payload):
                    # Строгий JSON-режим провайдера не выдержал ответа модели.
                    # У моделей с рассуждением (gpt-oss, R1) часть бюджета уходит
                    # на размышление, и объект не успевает закрыться - провайдер
                    # возвращает json_validate_failed с пустой генерацией.
                    # Свой разборщик у нас устойчивее: он достаёт объект из текста
                    # и из markdown-ограждения. Поэтому просто снимаем строгий режим.
                    log.info(
                        "%s: строгий JSON-режим отклонён провайдером, повтор без него",
                        self.name,
                    )
                    payload.pop("response_format", None)
                    continue
                elif response.status_code >= 400:
                    # 401/402/403/404 повторять бессмысленно: это ошибка
                    # запроса, ключа или оплаты. Подсказываем причину явно,
                    # иначе в логе агента видно только «HTTP 402».
                    raise LLMError(self._explain(response.status_code, response.text))
                else:
                    data = response.json()
                    usage = data.get("usage") or {}
                    await self.budget.settle(reservation, int(usage.get("total_tokens", 0)))
                    return data
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc

            if wait is None:
                if attempts_left <= 0:
                    break
                attempts_left -= 1
                wait = delay
                delay *= 2

            log.warning("%s: попытка %s не удалась (%s), повтор через %.1f с",
                        self.name, attempt, last_error, wait)
            await asyncio.sleep(wait)

        raise LLMError(f"{self.name}: запрос не удался после повторов: {last_error}")

    def _explain(self, status: int, body: str) -> str:
        hints = {
            401: "неверный или отозванный ключ",
            402: "нулевой баланс: тариф провайдера требует пополнения",
            403: "ключ не имеет доступа к этой модели",
            404: f"модель {self.model!r} не найдена у провайдера",
            413: "запрос длиннее лимита модели",
        }
        hint = hints.get(status)
        message = f"{self.name} HTTP {status}"
        if hint:
            message += f" - {hint}"
        return f"{message}: {body[:250]}"

    async def aclose(self) -> None:
        await self._client.aclose()
