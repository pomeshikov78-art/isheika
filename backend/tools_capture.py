"""Съёмка экранов работающей системы для презентации.

Одна миссия обслуживает весь набор снимков: браузер запускает её через
интерфейс, снимает живой журнал выполнения, дожидается завершения и снимает
разделы готового отчёта. Прежняя версия прогоняла две миссии - на свободном
тарифе LLM это вдвое дольше и вдвое дороже по суточной квоте.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8123"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
SHOTS.mkdir(parents=True, exist_ok=True)

USER = {"username": "analyst", "password": "analyst-pass-2026"}
QUERY = "Оцени перспективы твердотельных аккумуляторов для электромобилей до 2035 года"

# Разделы отчёта: имя снимка -> заголовок, по которому ищем блок
DOCUMENT = Path("storage/test_document.pdf")

SECTIONS = [
    ("10_report_top", "Аналитический отчёт «Ищейка»"),
    ("11_report_tech", "2. Детальный анализ"),
    ("12_report_forecast", "3. Прогноз"),
    ("13_report_risks", "4. Риски"),
    ("14_report_confidence", "6. Достоверность"),
    ("17_report_criticism", "Замечания критика"),
    ("15_report_sources", "7. Источники"),
]


async def shot(page, name: str) -> None:
    await page.wait_for_timeout(600)
    await page.screenshot(path=str(SHOTS / f"{name}.png"))
    print("снято:", name, flush=True)


async def mission_status(mission_id: str, token: str) -> str:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as api:
        r = await api.get(f"/api/v1/missions/{mission_id}",
                          headers={"Authorization": f"Bearer {token}"})
        return r.json().get("status", "unknown")


async def main() -> int:
    async with httpx.AsyncClient(base_url=BASE, timeout=60) as api:
        r = await api.post("/api/v1/auth/register", json=USER)
        if r.status_code == 409:
            r = await api.post("/api/v1/auth/login", json=USER)
        token = r.json()["access_token"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1440, "height": 900},
                                            device_scale_factor=2, locale="ru-RU")
        page = await context.new_page()

        # 1. Экран входа
        await page.goto(BASE, wait_until="networkidle")
        await shot(page, "01_login")

        # 2. Форма новой миссии
        await page.fill("#username", USER["username"])
        await page.fill("#password", USER["password"])
        await page.click("text=Войти")
        await page.wait_for_selector("#mission-card:not(.hidden)")
        await page.fill("#query", QUERY)
        await page.select_option("#mode", "comprehensive")
        # Прикладываем документ: ТЗ 5.1 и сценарий «Анализ научной публикации»
        if DOCUMENT.exists():
            await page.set_input_files("#docs", str(DOCUMENT.resolve()))
        await shot(page, "02_mission_form")

        # 3. Живой журнал: запускаем и снимаем в процессе
        await page.click("#runBtn")
        await page.wait_for_timeout(75000)
        await shot(page, "03_progress_live")

        # 4. Дожидаемся завершения, снимаем итог
        mission_id = await page.evaluate("() => window.__mission || null") or ""
        for _ in range(120):
            await page.wait_for_timeout(10000)
            done = await page.evaluate(
                "() => document.getElementById('result').textContent.length > 0")
            if done:
                break
        await shot(page, "04_progress_done")

        # 5. Проверка выводов аналитиком (ТЗ 3.6)
        await page.evaluate(
            "() => document.getElementById('claims-card').scrollIntoView({block:'start'})")
        await page.wait_for_timeout(1200)
        await shot(page, "18_claims_review")

        # 6. История миссий
        await page.click("text=Обновить")
        await page.wait_for_timeout(1500)
        await page.evaluate("document.getElementById('history-card').scrollIntoView()")
        await shot(page, "05_history")

        # 7. Разделы готового отчёта
        link = await page.evaluate(
            "() => { const a = document.querySelector('#result a'); return a ? a.getAttribute('href') : null; }")
        if link:
            await page.goto(BASE + link, wait_until="networkidle")
            await page.set_viewport_size({"width": 1200, "height": 900})
            for name, heading in SECTIONS:
                await page.evaluate(
                    """(text) => {
                        const node = [...document.querySelectorAll('h1,h2,h3')]
                            .find(el => el.textContent.trim().startsWith(text));
                        if (node) node.scrollIntoView({block: 'start'});
                        window.scrollBy(0, -24);
                    }""",
                    heading,
                )
                await shot(page, name)
            report_id = link.split("/")[-1].replace(".html", "")
            pdf = Path(f"storage/reports/{report_id}.pdf").resolve()
            if pdf.exists():
                import fitz

                fitz.open(pdf)[0].get_pixmap(dpi=150).save(str(SHOTS / "16_report_pdf_page.png"))
                print("снято: 16_report_pdf_page", flush=True)
        else:
            print("ВНИМАНИЕ: ссылка на отчёт не появилась, разделы не сняты", flush=True)

        # 8. Swagger
        await page.set_viewport_size({"width": 1300, "height": 900})
        await page.goto(f"{BASE}/docs", wait_until="networkidle")
        await page.wait_for_timeout(2500)
        await shot(page, "06_swagger")

        # 9. Состояние системы: провайдер, движки, статистика кэша
        await page.set_viewport_size({"width": 1300, "height": 320})
        await page.goto(f"{BASE}/api/v1/health", wait_until="networkidle")
        await page.wait_for_timeout(800)
        await shot(page, "08_api_health")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
