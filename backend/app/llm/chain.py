"""Цепочка LLM-провайдеров: работа продолжается, когда квота одного исчерпана.

Свободные тарифы ограничены не только в минуту, но и в сутки, причём у каждого
провайдера квота своя. Один ключ Groq даёт около шести полных миссий в день;
три бесплатных ключа - примерно втрое больше, и без единого рубля затрат.

Переключение автоматическое и одностороннее: вернуться на исчерпанного
провайдера в рамках процесса нельзя, потому что квота восстанавливается
часами, а не минутами.
"""
from __future__ import annotations

import logging

from .base import LLMError, LLMMessage, LLMProvider, LLMResponse

log = logging.getLogger(__name__)

# Признаки того, что дело в исчерпанной квоте, а не в самом запросе:
# на такой ошибке имеет смысл сменить провайдера, на прочих - нет
_QUOTA_MARKERS = (
    "суточный лимит",
    "quota",
    "insufficient balance",
    "rate_limit",
    "rate limit",
    "лимит частоты",
    "402",
    "429",
)


class ChainProvider(LLMProvider):
    """Несколько провайдеров, используемых по очереди."""

    name = "chain"

    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("Цепочка провайдеров пуста")
        self.providers = providers
        self.index = 0

    @property
    def active(self) -> LLMProvider:
        return self.providers[self.index]

    @property
    def model(self) -> str:  # type: ignore[override]
        return self.active.model

    @staticmethod
    def _is_quota_error(error: Exception) -> bool:
        text = str(error).lower()
        return any(marker in text for marker in _QUOTA_MARKERS)

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        last_error: Exception | None = None

        while self.index < len(self.providers):
            provider = self.active
            try:
                return await provider.complete(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    json_mode=json_mode,
                )
            except LLMError as error:
                last_error = error
                if not self._is_quota_error(error) or self.index + 1 >= len(self.providers):
                    raise
                nxt = self.providers[self.index + 1]
                log.warning(
                    "Квота провайдера %s исчерпана (%s). Переключаюсь на %s/%s",
                    provider.name, str(error)[:120], nxt.name, nxt.model,
                )
                self.index += 1

        raise LLMError(f"Все провайдеры исчерпаны: {last_error}")

    def describe(self) -> dict:
        return {
            "active": f"{self.active.name}/{self.active.model}",
            "chain": [f"{p.name}/{p.model}" for p in self.providers],
            "switched": self.index,
        }

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()
