"""Проверка настроенного LLM-провайдера.

Запуск: python tools_check_llm.py

Отвечает на два вопроса: доходит ли запрос до модели и умеет ли она отдавать
строгий JSON — без этого агенты работать не смогут.
"""
from __future__ import annotations

import asyncio
import sys
import time

sys.path.insert(0, ".")

from app.config import settings
from app.llm.base import LLMError, LLMMessage
from app.llm.factory import build_provider


async def main() -> int:
    provider = build_provider()
    print(f"Провайдер : {provider.name}")
    print(f"Модель    : {provider.model}")

    if provider.name == "mock":
        print("\nЭто заглушка: ключ не задан или провайдер не настроен.")
        print("Впишите ключ в backend/.env и укажите LLM_PROVIDER.")
        return 1

    print("\n1. Простой запрос...")
    started = time.monotonic()
    try:
        answer = await provider.complete(
            [LLMMessage("user", "Ответь одним словом: работает?")], max_tokens=32
        )
    except LLMError as exc:
        print(f"   ОШИБКА: {exc}")
        await provider.aclose()
        return 1
    print(f"   ответ за {time.monotonic() - started:.1f} с: {answer.text.strip()[:80]}")
    if answer.usage:
        print(f"   токены: {answer.usage}")

    print("\n2. Строгий JSON (на нём держатся все агенты)...")
    started = time.monotonic()
    try:
        data = await provider.complete_json(
            "Ты аналитик. Верни JSON вида {\"trl\": число 1-9, \"comment\": строка}.",
            "Оцени зрелость технологии твердотельных аккумуляторов.",
        )
    except LLMError as exc:
        print(f"   ОШИБКА: {exc}")
        await provider.aclose()
        return 1
    print(f"   разобрано за {time.monotonic() - started:.1f} с: {data}")

    await provider.aclose()
    print("\nПровайдер готов к работе.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
