"""Расчёт уровня уверенности (ТЗ 5.5, агент W11).

Уверенность собирается из пяти наблюдаемых факторов, а не назначается моделью:
LLM склонна выдавать «0.85» вне зависимости от того, что нашлось.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# Веса подобраны так, чтобы качество и независимость источников
# перевешивали объём: десять слабых ссылок хуже двух авторитетных.
WEIGHTS = {
    "source_quality": 0.30,
    "source_count": 0.15,
    "independence": 0.20,
    "agreement": 0.20,
    "freshness": 0.15,
}


@dataclass
class ConfidenceReport:
    value: float
    factors: dict[str, float] = field(default_factory=dict)
    explanation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "value": round(self.value, 3),
            "percent": round(self.value * 100, 1),
            "factors": {k: round(v, 3) for k, v in self.factors.items()},
            "explanation": self.explanation,
        }


def compute(
    sources: list[dict],
    *,
    agent_confidences: list[float] | None = None,
    critic_issues: int = 0,
    red_team_strength: float = 0.0,
    conflicts: int = 0,
) -> ConfidenceReport:
    factors: dict[str, float] = {}
    notes: list[str] = []

    reliabilities = [float(s.get("reliability", s.get("authority", 0.5))) for s in sources]
    factors["source_quality"] = sum(reliabilities) / len(reliabilities) if reliabilities else 0.0
    factors["source_count"] = min(1.0, len(sources) / 15.0)

    domains = {s.get("domain") or "" for s in sources if s.get("domain")}
    factors["independence"] = min(1.0, len(domains) / 8.0) if domains else 0.0

    if agent_confidences:
        mean = sum(agent_confidences) / len(agent_confidences)
        spread = max(agent_confidences) - min(agent_confidences)
        factors["agreement"] = max(0.0, mean * (1.0 - spread / 2.0))
    else:
        factors["agreement"] = 0.4

    factors["freshness"] = _freshness(sources)

    base = sum(WEIGHTS[name] * value for name, value in factors.items())

    # Штрафы: критика и контраргументы обязаны снижать уверенность (ТЗ 6.9)
    penalty = min(0.35, 0.03 * critic_issues + 0.15 * red_team_strength + 0.05 * conflicts)
    value = max(0.02, min(0.97, base - penalty))

    notes.append(f"Источников: {len(sources)}, независимых доменов: {len(domains)}.")
    notes.append(f"Средняя надёжность источников: {factors['source_quality']:.2f}.")
    if critic_issues:
        notes.append(f"Критик выявил замечаний: {critic_issues}.")
    if red_team_strength:
        notes.append(f"Сила контраргументов Red Team: {red_team_strength:.2f}.")
    if conflicts:
        notes.append(f"Неразрешённых расхождений между агентами: {conflicts}.")
    if penalty:
        notes.append(f"Суммарный штраф за критику и противоречия: -{penalty:.2f}.")
    if len(sources) < 5:
        notes.append("Источников меньше пяти - уверенность ограничена сверху объёмом данных.")

    return ConfidenceReport(value=value, factors=factors, explanation=notes)


def _freshness(sources: list[dict]) -> float:
    """Доля источников не старше пяти лет; без дат фактор нейтрален."""
    current_year = datetime.now(timezone.utc).year
    years: list[int] = []
    for source in sources:
        raw = str(source.get("published_at") or "")[:4]
        if raw.isdigit():
            year = int(raw)
            if 1900 < year <= current_year + 1:
                years.append(year)
    if not years:
        return 0.5
    fresh = sum(1 for y in years if current_year - y <= 5)
    return fresh / len(years)


def label(value: float) -> str:
    if value >= 0.8:
        return "высокая"
    if value >= 0.6:
        return "умеренная"
    if value >= 0.4:
        return "ограниченная"
    return "низкая"
