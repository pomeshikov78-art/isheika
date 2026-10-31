"""Запасной рендеринг PDF напрямую через ReportLab.

Используется, когда в системе нет ни WeasyPrint, ни браузера. Вёрстка проще
HTML-версии, но кириллица гарантирована: TTF-шрифт регистрируется явно.
"""
from __future__ import annotations

import base64
import io
import logging
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.fonts import addMapping
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..analytics.confidence import label as confidence_label
from . import charts

log = logging.getLogger(__name__)

ACCENT = colors.HexColor("#1f4e79")
FONT = "ReportSans"
_fonts_ready = False


def _font_files() -> tuple[Path, Path] | None:
    """DejaVu идёт вместе с matplotlib, Arial есть в любой Windows."""
    try:
        import matplotlib

        ttf = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        regular, bold = ttf / "DejaVuSans.ttf", ttf / "DejaVuSans-Bold.ttf"
        if regular.exists() and bold.exists():
            return regular, bold
    except Exception:
        pass

    windows = Path("C:/Windows/Fonts")
    if (windows / "arial.ttf").exists() and (windows / "arialbd.ttf").exists():
        return windows / "arial.ttf", windows / "arialbd.ttf"
    return None


def _register_fonts() -> bool:
    global _fonts_ready
    if _fonts_ready:
        return True

    files = _font_files()
    if files is None:
        log.warning("Не найден TTF с кириллицей - PDF через ReportLab невозможен")
        return False

    regular, bold = files
    pdfmetrics.registerFont(TTFont(FONT, str(regular)))
    pdfmetrics.registerFont(TTFont(FONT + "-Bold", str(bold)))
    addMapping(FONT, 0, 0, FONT)
    addMapping(FONT, 1, 0, FONT + "-Bold")
    _fonts_ready = True
    return True


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=FONT + "-Bold",
                                fontSize=18, textColor=ACCENT, alignment=TA_CENTER),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName=FONT + "-Bold",
                             fontSize=13, textColor=ACCENT, spaceBefore=12),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=FONT + "-Bold",
                             fontSize=11, textColor=ACCENT, spaceBefore=8),
        "body": ParagraphStyle("b", parent=base["BodyText"], fontName=FONT,
                               fontSize=9.5, leading=13),
        "small": ParagraphStyle("s", parent=base["BodyText"], fontName=FONT,
                                fontSize=8, leading=10, textColor=colors.HexColor("#555")),
        "cell": ParagraphStyle("c", parent=base["BodyText"], fontName=FONT,
                               fontSize=8, leading=10),
    }


def _esc(text) -> str:
    return str(text if text is not None else "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _table(rows: list[list], st: dict, widths: list[float] | None = None) -> Table:
    data = [[Paragraph(_esc(cell), st["cell"]) for cell in row] for row in rows]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c8d4e0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f6f9fc")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ]))
    return table


def _add_chart(story: list, data_uri: str | None) -> None:
    if not data_uri or not data_uri.startswith("data:image"):
        return
    payload = base64.b64decode(data_uri.split(",", 1)[1])
    image = Image(io.BytesIO(payload))
    scale = min(1.0, (170 * mm) / image.drawWidth)
    image.drawWidth *= scale
    image.drawHeight *= scale
    story.append(image)
    story.append(Spacer(1, 3 * mm))


def build_pdf_reportlab(report: dict, path: Path) -> Path:
    if not _register_fonts():
        raise RuntimeError("Нет шрифта с поддержкой кириллицы")

    st = _styles()
    doc = SimpleDocTemplate(
        str(path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"Отчёт «Ищейка» - {report.get('query', '')[:60]}",
    )
    story: list = [Paragraph("Аналитический отчёт «Ищейка»", st["title"]), Spacer(1, 4 * mm)]
    story.append(Paragraph(
        f"Миссия: {report.get('mission_id')} | Режим: {report.get('mode')} | "
        f"Агентов: {len(report.get('agents_used', []))}", st["small"]))

    story.append(Paragraph("Запрос", st["h1"]))
    story.append(Paragraph(_esc(report.get("query")), st["body"]))

    story.append(Paragraph("1. Резюме", st["h1"]))
    story.append(Paragraph(_esc(report.get("summary")), st["body"]))

    for item in report.get("key_findings") or []:
        story.append(Paragraph("• " + _esc(item), st["body"]))

    story.append(Paragraph("2. Технологическая оценка", st["h1"]))
    technology = report.get("technology", {})
    story.append(_table(
        [["Показатель", "Значение"],
         ["Объект", technology.get("name")],
         ["TRL (1-9)", technology.get("trl") or "не определён"],
         ["Обоснование", technology.get("trl_rationale")],
         ["Вывод", technology.get("verdict")]],
        st, widths=[45 * mm, 122 * mm]))

    story.append(Paragraph("3. Прогноз", st["h1"]))
    _add_chart(story, charts.forecast_chart(report.get("forecast", {})))
    scenarios = report.get("forecast", {}).get("scenarios") or []
    if scenarios:
        story.append(_table(
            [["Горизонт", "Год", "Сценарий", "Значение", "Вероятн."]]
            + [[f"{s.get('horizon')} лет", s.get("year"), s.get("scenario"),
                s.get("value"), s.get("probability")] for s in scenarios], st))
    else:
        story.append(Paragraph(
            "Количественный прогноз не построен: недостаточно данных временного ряда.",
            st["body"]))

    story.append(PageBreak())
    story.append(Paragraph("4. Риски", st["h1"]))
    _add_chart(story, charts.risk_matrix_chart(report.get("risk_matrix", [])))
    risks = report.get("risks") or []
    if risks:
        story.append(_table(
            [["Тип", "Риск", "Вер.", "Влиян.", "Эксп.", "Меры снижения"]]
            + [[r.get("type_ru") or r.get("type"), r.get("title"), r.get("probability"),
                r.get("impact"), r.get("exposure"), r.get("mitigation") or "-"] for r in risks],
            st, widths=[22 * mm, 42 * mm, 13 * mm, 13 * mm, 13 * mm, 64 * mm]))

    recommendations = report.get("recommendations") or []
    if recommendations:
        story.append(Paragraph("5. Рекомендации", st["h1"]))
        story.append(_table(
            [["Приоритет", "Рекомендация", "Обоснование"]]
            + [[r.get("priority"), r.get("text"), r.get("rationale") or "-"]
               for r in recommendations],
            st, widths=[22 * mm, 70 * mm, 75 * mm]))

    confidence = float(report.get("confidence", 0.5))
    story.append(Paragraph("6. Достоверность", st["h1"]))
    story.append(Paragraph(
        f"Итоговая уверенность: <b>{confidence * 100:.1f}%</b> ({confidence_label(confidence)})",
        st["body"]))
    for note in report.get("confidence_details", {}).get("explanation", []):
        story.append(Paragraph("• " + _esc(note), st["body"]))

    unresolved = report.get("unresolved_issues") or []
    if unresolved:
        story.append(Paragraph("Нерешённые вопросы", st["h2"]))
        for item in unresolved:
            story.append(Paragraph("• " + _esc(item), st["body"]))

    sources = report.get("sources") or []
    story.append(PageBreak())
    story.append(Paragraph(f"7. Источники ({len(sources)})", st["h1"]))
    story.append(_table(
        [["№", "Источник", "Дата", "Надёжн."]]
        + [[index, f"{s.get('title') or 'без заголовка'}\n{s.get('url')}",
            s.get("published_at") or "-",
            round(float(s.get("reliability", s.get("authority", 0.5))), 2)]
           for index, s in enumerate(sources, start=1)],
        st, widths=[10 * mm, 118 * mm, 22 * mm, 17 * mm]))

    doc.build(story)
    return path
