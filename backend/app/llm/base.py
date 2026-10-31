"""Единый контракт LLM-провайдера.

Провайдер должен уметь две вещи: свободный текст и строгий JSON.
Агенты пользуются только этим интерфейсом и не знают, кто под ним.
"""
from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMMessage:
    role: str  # system | user | assistant
    content: str


@dataclass
class LLMResponse:
    text: str
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


class LLMError(RuntimeError):
    pass


class LLMProvider(abc.ABC):
    """Базовый класс провайдера. Реализации: openai_compat, anthropic_provider, mock."""

    name: str = "base"
    model: str = ""

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        ...

    async def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict[str, Any]:
        """Запрос, ответ на который обязан быть JSON-объектом.

        JSON-режим у провайдеров реализован по-разному (а у локальных моделей
        его может не быть вовсе), поэтому парсинг всегда идёт через
        устойчивый extract_json, а не через доверие к формату ответа.
        """
        messages = [
            LLMMessage("system", system + "\n\nОтвечай ТОЛЬКО валидным JSON-объектом, без пояснений и markdown-обёрток."),
            LLMMessage("user", user),
        ]
        response = await self.complete(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )
        return extract_json(response.text)

    async def aclose(self) -> None:
        return None


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Достаёт JSON-объект из ответа модели.

    Порядок: как есть -> из markdown-ограждения -> первый сбалансированный
    объект в тексте. Если ничего не вышло, поднимается LLMError: молча
    возвращать пустой словарь нельзя, иначе агент отрапортует пустой результат
    как успешный.
    """
    text = (text or "").strip()
    if not text:
        raise LLMError("Пустой ответ модели")

    for candidate in _candidates(text):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    raise LLMError(f"Не удалось разобрать JSON из ответа модели: {text[:300]}")


def _candidates(text: str) -> list[str]:
    out = [text]
    fenced = _FENCE_RE.search(text)
    if fenced:
        out.append(fenced.group(1).strip())
    balanced = _first_balanced_object(text)
    if balanced:
        out.append(balanced)
    repaired = _repair_truncated(text)
    if repaired:
        out.append(repaired)
    return out


def _repair_truncated(text: str) -> str | None:
    """Достраивает объект, оборванный лимитом токенов.

    Модель с исчерпанным max_tokens прекращает генерацию на середине - объект
    не закрыт, и обычный разбор бессилен. При этом всё, что она успела выдать,
    осмысленно: терять целый ответ агента из-за незакрытой скобки расточительно.
    Обрезаем по последнему завершённому значению и дозакрываем скобки.
    """
    start = text.find("{")
    if start == -1:
        return None

    stack: list[str] = []
    in_string = False
    escaped = False
    safe_cut: int | None = None
    safe_stack: list[str] = []

    for index in range(start, len(text)):
        char = text[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append("}" if char == "{" else "]")
        elif char in "}]":
            if not stack:
                return None
            stack.pop()
            safe_cut, safe_stack = index + 1, list(stack)
        elif char == ",":
            # Запятая означает, что предыдущее значение записано целиком
            safe_cut, safe_stack = index, list(stack)

    if safe_cut is None or not safe_stack:
        return None

    return text[start:safe_cut].rstrip().rstrip(",") + "".join(reversed(safe_stack))


def _first_balanced_object(text: str) -> str | None:
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
