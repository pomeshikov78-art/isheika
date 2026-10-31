"""Форсайт: S-кривые и сценарии (ТЗ 5.3, агент W06).

Логистическая кривая y = K / (1 + exp(-r (t - t0))) подгоняется по временному
ряду (патентная активность, число публикаций, продажи). Подгонка сделана без
scipy: перебор потолка K, для каждого K логит-преобразование сводит задачу к
обычной линейной регрессии, побеждает K с минимальной суммой квадратов.
Так модуль остаётся на одном numpy и не тянет тяжёлых зависимостей.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class SCurveFit:
    K: float           # потолок насыщения
    r: float           # скорость роста
    t0: float          # точка перегиба (год)
    r2: float          # качество подгонки
    method: str        # logistic | linear | flat

    def predict(self, t: float) -> float:
        if self.method == "logistic":
            exponent = -self.r * (t - self.t0)
            # защита от переполнения exp при далёких горизонтах
            if exponent > 700:
                return 0.0
            return self.K / (1.0 + math.exp(exponent))
        if self.method == "linear":
            return self.K + self.r * (t - self.t0)
        return self.K

    def to_dict(self) -> dict:
        return {
            "K": round(self.K, 4),
            "r": round(self.r, 4),
            "t0": round(self.t0, 2),
            "r2": round(self.r2, 4),
            "method": self.method,
            "inflection_year": round(self.t0, 1) if self.method == "logistic" else None,
        }


def fit_scurve(years: list[float], values: list[float]) -> SCurveFit:
    """Подгонка логистической кривой. При нехватке данных деградирует
    до линейного тренда, а при полном отсутствии динамики - до константы."""
    t = np.asarray(years, dtype=float)
    y = np.asarray(values, dtype=float)

    if t.size == 0:
        return SCurveFit(K=0.0, r=0.0, t0=0.0, r2=0.0, method="flat")
    if t.size < 4 or float(np.max(y)) <= 0:
        return _fit_linear(t, y)

    y_max = float(np.max(y))
    best: SCurveFit | None = None

    # Потолок ищем от «чуть выше достигнутого» до десятикратного запаса
    for k_mult in np.linspace(1.05, 10.0, 120):
        K = y_max * float(k_mult)
        ratio = np.clip(y / K, 1e-6, 1 - 1e-6)
        logit = np.log(ratio / (1.0 - ratio))
        slope, intercept = np.polyfit(t, logit, 1)
        if slope <= 0:
            continue  # убывающая логистика для форсайта роста бессмысленна
        t0 = -intercept / slope
        candidate = SCurveFit(K=K, r=float(slope), t0=float(t0), r2=0.0, method="logistic")
        candidate.r2 = _r_squared(t, y, candidate)
        if best is None or candidate.r2 > best.r2:
            best = candidate

    if best is None or best.r2 < 0.5:
        linear = _fit_linear(t, y)
        if best is None or linear.r2 > best.r2:
            return linear
    return best


def _fit_linear(t: np.ndarray, y: np.ndarray) -> SCurveFit:
    if t.size < 2:
        value = float(y[0]) if y.size else 0.0
        return SCurveFit(K=value, r=0.0, t0=float(t[0]) if t.size else 0.0, r2=0.0, method="flat")
    slope, intercept = np.polyfit(t, y, 1)
    t0 = float(t[-1])
    fit = SCurveFit(K=float(slope * t0 + intercept), r=float(slope), t0=t0, r2=0.0, method="linear")
    fit.r2 = _r_squared(t, y, fit)
    return fit


def _r_squared(t: np.ndarray, y: np.ndarray, fit: SCurveFit) -> float:
    predicted = np.array([fit.predict(float(x)) for x in t])
    ss_res = float(np.sum((y - predicted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    if ss_tot == 0:
        return 1.0 if ss_res == 0 else 0.0
    return max(0.0, 1.0 - ss_res / ss_tot)


# Разброс сценариев расширяется с горизонтом: на 20 лет неопределённость
# принципиально выше, чем на год, и прогноз обязан это показывать.
SCENARIO_SPREAD = {"optimistic": 1.0, "base": 0.0, "pessimistic": -1.0}


def build_scenarios(
    fit: SCurveFit,
    base_year: int,
    horizons: list[int],
    *,
    volatility: float = 0.18,
) -> list[dict]:
    """Три сценария (ТЗ 5.3) на каждый горизонт, с интервалом и вероятностью."""
    out: list[dict] = []
    quality = max(0.1, min(1.0, fit.r2))

    for horizon in sorted(set(horizons)):
        year = base_year + horizon
        central = fit.predict(float(year))
        # неопределённость растёт как корень из горизонта и падает с качеством подгонки
        spread = volatility * math.sqrt(horizon) * (2.0 - quality)

        for scenario, direction in SCENARIO_SPREAD.items():
            value = central * (1.0 + direction * spread)
            out.append(
                {
                    "horizon": horizon,
                    "year": year,
                    "scenario": scenario,
                    "value": round(max(0.0, value), 4),
                    "probability": _scenario_probability(scenario, quality),
                    "confidence_interval": {
                        "lower": round(max(0.0, central * (1.0 - spread)), 4),
                        "upper": round(max(0.0, central * (1.0 + spread)), 4),
                    },
                }
            )
    return out


def _scenario_probability(scenario: str, quality: float) -> float:
    """Чем хуже подгонка, тем ближе сценарии по вероятности к равновероятным."""
    if scenario == "base":
        return round(0.4 + 0.2 * quality, 2)
    return round((1.0 - (0.4 + 0.2 * quality)) / 2.0, 2)
