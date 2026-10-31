"""Графики отчёта: S-кривая с прогнозом и матрица рисков (ТЗ 6.12).

Matplotlib в headless-режиме, PNG отдаётся data-URI - так одна HTML-страница
остаётся самодостаточной и не тянет внешних файлов при экспорте в PDF/DOCX.
"""
from __future__ import annotations

import base64
import io
import logging

log = logging.getLogger(__name__)


def _figure_to_data_uri(fig) -> str:
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=110, bbox_inches="tight")
    buffer.seek(0)
    encoded = base64.b64encode(buffer.read()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def forecast_chart(forecast: dict) -> str | None:
    """Исторический ряд + три сценария по горизонтам."""
    series = forecast.get("series") or []
    scenarios = forecast.get("scenarios") or []
    if len(series) < 2 and not scenarios:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        fig, ax = plt.subplots(figsize=(8, 4.2))

        if series:
            ax.plot(
                [p["year"] for p in series],
                [p["value"] for p in series],
                marker="o", linewidth=2, color="#1f4e79", label="Исторические данные",
            )

        styles = {
            "optimistic": ("#2e7d32", "--", "Оптимистичный"),
            "base": ("#1565c0", "-", "Базовый"),
            "pessimistic": ("#c62828", ":", "Пессимистичный"),
        }
        for name, (color, style, label) in styles.items():
            points = sorted(
                (s for s in scenarios if s.get("scenario") == name),
                key=lambda s: s.get("year", 0),
            )
            if not points:
                continue
            ax.plot(
                [p["year"] for p in points],
                [p["value"] for p in points],
                linestyle=style, marker="s", color=color, label=label, alpha=0.9,
            )

        base_points = sorted(
            (s for s in scenarios if s.get("scenario") == "base"), key=lambda s: s.get("year", 0)
        )
        if base_points:
            ax.fill_between(
                [p["year"] for p in base_points],
                [p.get("confidence_interval", {}).get("lower", p["value"]) for p in base_points],
                [p.get("confidence_interval", {}).get("upper", p["value"]) for p in base_points],
                color="#1565c0", alpha=0.12, label="Доверительный интервал",
            )

        ax.set_xlabel("Год")
        ax.set_ylabel(forecast.get("unit") or forecast.get("metric") or "Значение")
        ax.set_title("Форсайт: S-кривая и сценарии")
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)

        uri = _figure_to_data_uri(fig)
        plt.close(fig)
        return uri
    except Exception as exc:
        log.warning("График прогноза не построен: %s", exc)
        return None


def risk_matrix_chart(matrix: list[list[int]]) -> str | None:
    """Тепловая карта 5x5: вероятность по X, влияние по Y."""
    if not matrix:
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np

        data = np.array(matrix, dtype=float)
        # Фон ячейки - экспозиция (произведение уровней), поверх - число рисков
        exposure = np.array([[(p + 1) * (i + 1) for p in range(5)] for i in range(5)], dtype=float)

        fig, ax = plt.subplots(figsize=(5.6, 4.8))
        ax.imshow(exposure, cmap="RdYlGn_r", origin="lower", alpha=0.75)

        for i in range(5):
            for p in range(5):
                count = int(data[i][p]) if i < data.shape[0] and p < data.shape[1] else 0
                if count:
                    ax.text(p, i, str(count), ha="center", va="center",
                            fontsize=13, fontweight="bold", color="#111")

        ax.set_xticks(range(5), ["1", "2", "3", "4", "5"])
        ax.set_yticks(range(5), ["1", "2", "3", "4", "5"])
        ax.set_xlabel("Вероятность")
        ax.set_ylabel("Влияние")
        ax.set_title("Матрица рисков 5x5 (число рисков в ячейке)")

        uri = _figure_to_data_uri(fig)
        plt.close(fig)
        return uri
    except Exception as exc:
        log.warning("Матрица рисков не построена: %s", exc)
        return None
