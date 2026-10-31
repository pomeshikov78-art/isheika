"""Генерация отчёта: HTML -> DOCX -> PDF (ТЗ 6.12, 10.1).

Функция render_all синхронная и вызывается из оркестратора через
asyncio.to_thread: matplotlib, python-docx и xhtml2pdf блокирующие, и держать
на них event loop нельзя.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from ..analytics.confidence import label as confidence_label
from ..config import settings
from . import charts

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

MARKET_LABELS = {
    "market_size_usd": "Объём рынка, USD",
    "market_size_year": "Год оценки",
    "cagr": "CAGR",
    "key_players": "Ключевые игроки",
    "price_range": "Ценовой диапазон",
    "regions": "Регионы",
}
SCENARIO_LABELS = {
    "optimistic": "Оптимистичный",
    "base": "Базовый",
    "pessimistic": "Пессимистичный",
}
PRIORITY_LABELS = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
# Классы CSS только латиницей: парсер xhtml2pdf не понимает кириллицу
# в именах селекторов и роняет весь экспорт в PDF.
SEVERITY_CLASSES = {
    "низкий": "low",
    "умеренный": "moderate",
    "существенный": "significant",
    "высокий": "high",
    "критический": "critical",
}
FACTOR_LABELS = {
    "source_quality": "Качество источников",
    "source_count": "Количество источников",
    "independence": "Независимость источников",
    "agreement": "Согласованность агентов",
    "freshness": "Актуальность данных",
}

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_html(report: dict) -> str:
    confidence = float(report.get("confidence", 0.5))
    template = _env.get_template("report.html")
    return template.render(
        report=report,
        generated_at=datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"),
        forecast_chart=charts.forecast_chart(report.get("forecast", {})),
        risk_chart=charts.risk_matrix_chart(report.get("risk_matrix", [])),
        conf_label=confidence_label(confidence),
        conf_class="high" if confidence >= 0.7 else ("medium" if confidence >= 0.45 else "low"),
        market_labels=MARKET_LABELS,
        scenario_labels=SCENARIO_LABELS,
        priority_labels=PRIORITY_LABELS,
        factor_labels=FACTOR_LABELS,
        severity_classes=SEVERITY_CLASSES,
    )


def render_all(report: dict) -> dict[str, str]:
    """Возвращает карту формат -> относительный URL. Форматы независимы:
    падение DOCX не должно лишить пользователя HTML."""
    mission_id = report.get("mission_id", "report")
    directory = settings.storage_path / "reports"
    directory.mkdir(parents=True, exist_ok=True)

    files: dict[str, str] = {}

    html = render_html(report)
    html_path = directory / f"{mission_id}.html"
    html_path.write_text(html, encoding="utf-8")
    files["html"] = f"/reports/{mission_id}.html"

    try:
        from .docx_export import build_docx

        build_docx(report, directory / f"{mission_id}.docx")
        files["docx"] = f"/reports/{mission_id}.docx"
    except Exception as exc:
        log.warning("Экспорт DOCX не выполнен: %s", exc)

    try:
        from .pdf_export import build_pdf

        if build_pdf(html, directory / f"{mission_id}.pdf", report=report):
            files["pdf"] = f"/reports/{mission_id}.pdf"
    except Exception as exc:
        log.warning("Экспорт PDF не выполнен: %s", exc)

    return files
