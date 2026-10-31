"""Съёмка экранов API: состав агентов, состояние системы, контракт запроса."""
from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8123"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


async def main() -> int:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1300, "height": 900},
                                            device_scale_factor=2, locale="ru-RU")
        page = await context.new_page()

        # Реестр агентов W01-W13 с зависимостями
        await page.goto(f"{BASE}/api/v1/agents", wait_until="networkidle")
        await page.wait_for_timeout(800)
        await page.screenshot(path=str(SHOTS / "07_api_agents.png"))
        print("снято: 07_api_agents")

        # Состояние системы: провайдер LLM, движки поиска, число агентов
        await page.goto(f"{BASE}/api/v1/health", wait_until="networkidle")
        await page.wait_for_timeout(600)
        await page.screenshot(path=str(SHOTS / "08_api_health.png"), clip={"x": 0, "y": 0, "width": 1300, "height": 260})
        print("снято: 08_api_health")

        # Контракт POST /missions в Swagger
        await page.goto(f"{BASE}/docs", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        # В Swagger заголовки операций - это кнопки; раскрываем POST /api/v1/missions
        await page.locator("#operations-missions-create_mission_api_v1_missions_post").click()
        await page.wait_for_timeout(1500)
        await page.locator("#operations-missions-create_mission_api_v1_missions_post").scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        await page.screenshot(path=str(SHOTS / "09_swagger_post_mission.png"))
        print("снято: 09_swagger_post_mission")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
