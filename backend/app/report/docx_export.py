"""Экспорт отчёта в Word (.docx) через python-docx — ТЗ 6.12, этап 9."""
from __future__ import annotations

import base64
import io
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

from ..analytics.confidence import label as confidence_label
from . import charts

ACCENT = RGBColor(0x1F, 0x4E, 0x79)


def build_docx(report: dict, path: Path) -> Path:
    doc = Document()
    _title(doc, "Аналитический отчёт «Ищейка»")

    meta = doc.add_paragraph()
    meta.add_run(
        f"Миссия: {report.get('mission_id')} | Режим: {report.get('mode')} | "
        f"Агентов: {len(report.get('agents_used', []))}"
    ).font.size = Pt(8)

    _heading(doc, "Запрос")
    doc.add_paragraph(report.get("query", ""))

    _heading(doc, "1. Резюме")
    doc.add_paragraph(report.get("summary", ""))

    findings = report.get("key_findings") or []
    if findings:
        _heading(doc, "Ключевые выводы", level=2)
        for item in findings:
            doc.add_paragraph(str(item), style="List Bullet")

    _heading(doc, "2. Детальный анализ")
    technology = report.get("technology", {})
    _kv_table(
        doc,
        [
            ("Объект", technology.get("name")),
            ("TRL (1-9)", technology.get("trl") or "не определён"),
            ("Обоснование TRL", technology.get("trl_rationale")),
            ("Вывод", technology.get("verdict")),
        ],
    )

    characteristics = technology.get("characteristics") or []
    if characteristics:
        _table(
            doc,
            ["Характеристика", "Значение", "Сравнение"],
            [[c.get("name"), c.get("value"), c.get("comparison")] for c in characteristics],
        )

    detailed = report.get("analysis", {}).get("detailed")
    if detailed:
        doc.add_paragraph(str(detailed))

    comparison = report.get("analysis", {}).get("comparison") or []
    criteria = report.get("analysis", {}).get("comparison_criteria") or []
    if comparison and criteria:
        _heading(doc, "Сравнительный бенчмаркинг", level=2)
        _table(
            doc,
            ["Решение", *criteria, "Итого"],
            [
                [alt.get("name"), *[alt.get("scores", {}).get(c, "-") for c in criteria], alt.get("total")]
                for alt in comparison
            ],
        )

    _heading(doc, "3. Прогноз")
    _embed_chart(doc, charts.forecast_chart(report.get("forecast", {})))
    scenarios = report.get("forecast", {}).get("scenarios") or []
    if scenarios:
        _table(
            doc,
            ["Горизонт", "Год", "Сценарий", "Значение", "Вероятность"],
            [
                [f"{s.get('horizon')} лет", s.get("year"), s.get("scenario"),
                 s.get("value"), s.get("probability")]
                for s in scenarios
            ],
        )
    else:
        doc.add_paragraph("Количественный прогноз не построен: недостаточно данных временного ряда.")

    _heading(doc, "4. Риски")
    _embed_chart(doc, charts.risk_matrix_chart(report.get("risk_matrix", [])))
    risks = report.get("risks") or []
    if risks:
        _table(
            doc,
            ["Тип", "Риск", "Вер.", "Влияние", "Экспозиция", "Меры снижения"],
            [
                [r.get("type_ru") or r.get("type"), r.get("title"), r.get("probability"),
                 r.get("impact"), r.get("exposure"), r.get("mitigation") or "-"]
                for r in risks
            ],
        )

    recommendations = report.get("recommendations") or []
    if recommendations:
        _heading(doc, "5. Рекомендации")
        _table(
            doc,
            ["Приоритет", "Рекомендация", "Обоснование"],
            [[r.get("priority"), r.get("text"), r.get("rationale") or "-"] for r in recommendations],
        )

    confidence = float(report.get("confidence", 0.5))
    _heading(doc, "6. Достоверность")
    doc.add_paragraph(
        f"Итоговая уверенность: {confidence * 100:.1f}% ({confidence_label(confidence)})"
    )
    for note in report.get("confidence_details", {}).get("explanation", []):
        doc.add_paragraph(str(note), style="List Bullet")

    unresolved = report.get("unresolved_issues") or []
    if unresolved:
        _heading(doc, "Нерешённые вопросы", level=2)
        for item in unresolved:
            doc.add_paragraph(str(item), style="List Bullet")

    sources = report.get("sources") or []
    _heading(doc, f"7. Источники ({len(sources)})")
    _table(
        doc,
        ["№", "Источник", "Дата", "Надёжность"],
        [
            [
                index,
                f"{s.get('title') or 'без заголовка'}\n{s.get('url')}",
                s.get("published_at") or "-",
                round(float(s.get("reliability", s.get("authority", 0.5))), 2),
            ]
            for index, s in enumerate(sources, start=1)
        ],
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


def _title(doc: Document, text: str) -> None:
    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(18)
    run.font.color.rgb = ACCENT


def _heading(doc: Document, text: str, level: int = 1) -> None:
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(14 if level == 1 else 11)
    run.font.color.rgb = ACCENT


def _kv_table(doc: Document, rows: list[tuple]) -> None:
    _table(doc, None, [[key, value if value not in (None, "") else "-"] for key, value in rows])


def _table(doc: Document, header: list | None, rows: list[list]) -> None:
    if not rows:
        return
    columns = len(header) if header else len(rows[0])
    table = doc.add_table(rows=0, cols=columns)
    table.style = "Table Grid"

    if header:
        cells = table.add_row().cells
        for index, title in enumerate(header):
            cells[index].text = str(title)
            for paragraph in cells[index].paragraphs:
                for run in paragraph.runs:
                    run.bold = True

    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row[:columns]):
            cells[index].text = "" if value is None else str(value)
    doc.add_paragraph()


def _embed_chart(doc: Document, data_uri: str | None) -> None:
    if not data_uri or not data_uri.startswith("data:image"):
        return
    payload = base64.b64decode(data_uri.split(",", 1)[1])
    doc.add_picture(io.BytesIO(payload), width=doc.sections[0].page_width - Pt(120))
