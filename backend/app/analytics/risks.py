"""Матрица рисков 5x5 (ТЗ 5.4, агент W05)."""
from __future__ import annotations

RISK_TYPES = ("technical", "market", "regulatory", "financial", "operational")

RISK_TYPE_RU = {
    "technical": "Технический",
    "market": "Рыночный",
    "regulatory": "Регуляторный",
    "financial": "Финансовый",
    "operational": "Операционный",
}

LEVELS = ("низкий", "умеренный", "существенный", "высокий", "критический")


def to_band(value: float) -> int:
    """0..1 -> ячейка матрицы 1..5."""
    value = max(0.0, min(1.0, float(value)))
    return min(5, int(value * 5) + 1)


def exposure(probability: float, impact: float) -> float:
    return round(max(0.0, min(1.0, probability)) * max(0.0, min(1.0, impact)), 4)


def severity(probability: float, impact: float) -> str:
    score = exposure(probability, impact)
    if score < 0.08:
        return LEVELS[0]
    if score < 0.20:
        return LEVELS[1]
    if score < 0.36:
        return LEVELS[2]
    if score < 0.56:
        return LEVELS[3]
    return LEVELS[4]


def normalize_risk(raw: dict) -> dict:
    """Приводит риск от LLM к контракту БД: типы, границы 0..1, exposure."""
    risk_type = str(raw.get("type") or raw.get("risk_type") or "technical").lower()
    if risk_type not in RISK_TYPES:
        risk_type = "technical"

    probability = _clamp(raw.get("probability"), 0.5)
    impact = _clamp(raw.get("impact"), 0.5)

    return {
        "type": risk_type,
        "type_ru": RISK_TYPE_RU[risk_type],
        "title": str(raw.get("title") or raw.get("text") or "Риск без описания").strip(),
        "probability": probability,
        "impact": impact,
        "exposure": exposure(probability, impact),
        "severity": severity(probability, impact),
        "probability_band": to_band(probability),
        "impact_band": to_band(impact),
        "mitigation": str(raw.get("mitigation") or "").strip() or None,
        "status": raw.get("status", "identified"),
    }


def _clamp(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    # LLM нередко отдаёт вероятность в процентах вместо доли
    if number > 1.0:
        number = number / 100.0 if number <= 100.0 else 1.0
    return round(max(0.0, min(1.0, number)), 3)


def build_matrix(risks: list[dict]) -> list[list[int]]:
    """Матрица 5x5: matrix[impact-1][probability-1] = число рисков в ячейке."""
    matrix = [[0] * 5 for _ in range(5)]
    for risk in risks:
        p = risk.get("probability_band") or to_band(risk.get("probability", 0.5))
        i = risk.get("impact_band") or to_band(risk.get("impact", 0.5))
        matrix[i - 1][p - 1] += 1
    return matrix


def coverage_gaps(risks: list[dict]) -> list[str]:
    """ТЗ 5.4 требует не менее 5 типов рисков - показываем, каких не хватает."""
    present = {r["type"] for r in risks}
    return [RISK_TYPE_RU[t] for t in RISK_TYPES if t not in present]
