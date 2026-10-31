"""Съёмка разделов готового HTML-отчёта и страницы PDF."""
from __future__ import annotations

import asyncio
import glob
import sys
from pathlib import Path

from playwright.async_api import async_playwright

BASE = "http://127.0.0.1:8123"
SHOTS = Path(__file__).resolve().parent.parent / "docs" / "screenshots"

# Разделы отчёта: имя файла -> текст заголовка, по которому ищем блок
SECTIONS = [
    ("10_report_top", "Аналитический отчёт «Ищейка»"),
    ("11_report_tech", "2. Детальный анализ"),
    ("12_report_forecast", "3. Прогноз"),
    ("13_report_risks", "4. Риски"),
    ("14_report_confidence", "6. Достоверность"),
    ("15_report_sources", "7. Источники"),
    ("17_report_criticism", "Замечания критика"),
]


async def main() -> int:
    mission_id = sys.argv[1]
    url = f"{BASE}/reports/{mission_id}.html"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(viewport={"width": 1200, "height": 900},
                                            device_scale_factor=2, locale="ru-RU")
        page = await context.new_page()
        await page.goto(url, wait_until="networkidle")

        for name, heading in SECTIONS:
            # Заголовок раздела ставим в верх кадра, иначе снимок показывает
            # предыдущий раздел, а нужный оказывается у нижней кромки
            await page.evaluate(
                """(text) => {
                    const node = [...document.querySelectorAll('h1,h2,h3')]
                        .find(el => el.textContent.trim().startsWith(text));
                    if (node) node.scrollIntoView({block: 'start'});
                    window.scrollBy(0, -24);
                }""",
                heading,
            )
            await page.wait_for_timeout(500)
            await page.screenshot(path=str(SHOTS / f"{name}.png"))
            print("снято:", name)

        # Страница PDF-версии отчёта: доказательство, что экспорт читаем
        pdf = Path(f"storage/reports/{mission_id}.pdf").resolve()
        if pdf.exists():
            import fitz

            doc = fitz.open(pdf)
            doc[0].get_pixmap(dpi=150).save(str(SHOTS / "16_report_pdf_page.png"))
            print("снято: 16_report_pdf_page (страниц в PDF:", doc.page_count, ")")

        await browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
