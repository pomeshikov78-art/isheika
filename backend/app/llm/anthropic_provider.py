"""Провайдер Claude через официальный SDK anthropic.

Отличия от OpenAI-совместимых API, которые здесь и учтены:
  * системный промпт передаётся отдельным параметром system, а не ролью в messages;
  * на моделях 4.6+ включается adaptive thinking, budget_tokens удалён (400);
  * ответ - список блоков контента, текст надо собирать из блоков type == "text".
"""
from __future__ import annotations

from .base import LLMError, LLMMessage, LLMProvider, LLMResponse

# Модели с adaptive thinking и параметром output_config.effort
_ADAPTIVE_THINKING_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-fable-5",
)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-opus-5",
        timeout: int = 120,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        effort: str = "medium",
    ) -> None:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - зависит от окружения
            raise LLMError(
                "Провайдер anthropic выбран, но пакет не установлен: pip install anthropic"
            ) from exc

        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.effort = effort
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None, timeout=float(timeout))

    @property
    def _adaptive(self) -> bool:
        return self.model in _ADAPTIVE_THINKING_MODELS

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        system_parts = [m.content for m in messages if m.role == "system"]
        dialog = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        if not dialog:
            raise LLMError("Anthropic: нужен хотя бы один user-message")

        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "messages": dialog,
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        if self._adaptive:
            # На этих моделях sampling-параметры удалены, зато есть effort.
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": self.effort}
        else:
            kwargs["temperature"] = self.temperature if temperature is None else temperature

        try:
            message = await self._client.messages.create(**kwargs)
        except Exception as exc:  # SDK поднимает свои типы; наверх отдаём один
            raise LLMError(f"Anthropic: {exc}") from exc

        if getattr(message, "stop_reason", None) == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMError(f"Anthropic отклонил запрос: {details}")

        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        usage = {}
        if getattr(message, "usage", None):
            usage = {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
            }
        return LLMResponse(text=text, model=self.model, usage=usage)

    async def aclose(self) -> None:
        await self._client.close()
