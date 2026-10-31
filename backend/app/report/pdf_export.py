"""Экспорт HTML-отчёта в PDF.

Цепочка движков, от лучшего к запасному:

  1. WeasyPrint      - если установлен (на Linux ставится из pip, на Windows требует GTK);
  2. Headless Chrome - печатает ровно тот же HTML, что видит пользователь, включая
                       графики и кириллицу; ищется среди установленных браузеров;
  3. ReportLab       - запасной путь без внешних зависимостей: верстка проще,
                       но текст и таблицы гарантированно читаемы.

xhtml2pdf из цепочки исключён намеренно: на Windows он не может передать TTF в
reportlab и молча печатает кириллицу пустыми плашками - формально файл создаётся,
фактически он нечитаем. Такой «успех» хуже честного отказа.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def build_pdf(html: str, path: Path, report: dict | None = None) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)

    if _try_weasyprint(html, path):
        return True
    if _try_chrome(html, path):
        return True
    if report is not None and _try_reportlab(report, path):
        return True

    log.warning(
        "PDF не сформирован: нет ни WeasyPrint, ни браузера Chrome/Edge, "
        "ни данных для запасного рендеринга."
    )
    return False


def _try_weasyprint(html: str, path: Path) -> bool:
    try:
        from weasyprint import HTML
    except Exception:
        return False
    try:
        HTML(string=html).write_pdf(str(path))
        return _keep_if_nonempty(path)
    except Exception as exc:
        log.warning("WeasyPrint не справился: %s", exc)
        return False


def find_browser() -> str | None:
    for name in ("chrome", "google-chrome", "chromium", "msedge"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None


def _try_chrome(html: str, path: Path) -> bool:
    browser = find_browser()
    if browser is None:
        return False

    # Chrome печатает только по URL, поэтому HTML кладётся во временный файл
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", encoding="utf-8", delete=False
    ) as handle:
        handle.write(html)
        source = Path(handle.name)

    try:
        result = subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-sandbox",
                "--no-pdf-header-footer",
                "--print-to-pdf-no-header",
                f"--print-to-pdf={path}",
                source.as_uri(),
            ],
            capture_output=True,
            timeout=120,
        )
        if not _keep_if_nonempty(path):
            log.warning(
                "Chrome не создал PDF: %s", (result.stderr or b"").decode(errors="ignore")[:300]
            )
            return False
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        log.warning("Запуск браузера для печати PDF не удался: %s", exc)
        return False
    finally:
        source.unlink(missing_ok=True)


def _try_reportlab(report: dict, path: Path) -> bool:
    """Запасной рендеринг напрямую из структуры отчёта."""
    try:
        from .reportlab_export import build_pdf_reportlab

        build_pdf_reportlab(report, path)
        return _keep_if_nonempty(path)
    except Exception as exc:
        log.warning("Запасной рендеринг PDF (ReportLab) не удался: %s", exc)
        return False


def _keep_if_nonempty(path: Path) -> bool:
    """Пустышка от сорвавшегося рендеринга хуже отсутствия файла:
    API отдал бы 0 байт вместо честного «формат не сформирован»."""
    if path.exists() and path.stat().st_size > 0:
        return True
    path.unlink(missing_ok=True)
    return False
