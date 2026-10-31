"""Выявление противоречий между агентами (ТЗ 6.7).

Ищем расхождения там, где их можно поймать формально: расходящиеся числовые
оценки, конфликт TRL с выводом о зрелости, прогноз роста при высоких рисках.
"""
from __future__ import annotations


def detect(results: dict[str, dict]) -> list[dict]:
    conflicts: list[dict] = []

    conflicts.extend(_cagr_vs_forecast(results))
    conflicts.extend(_trl_vs_risk(results))
    conflicts.extend(_confidence_spread(results))

    return conflicts


def _cagr_vs_forecast(results: dict[str, dict]) -> list[dict]:
    """W04 даёт CAGR, W06 - собственный темп по S-кривой. Расхождение
    более чем вдвое - сигнал, что один из агентов опирается на другие данные."""
    market = results.get("W04", {}).get("metrics", {})
    forecast = results.get("W06", {}).get("calculations", {})
    scenarios = forecast.get("scenarios") or []

    cagr = _to_float(market.get("cagr"))
    if cagr is None or not scenarios:
        return []

    base = [s for s in scenarios if s.get("scenario") == "base"]
    if len(base) < 2:
        return []

    first, last = base[0], base[-1]
    years = max(1, last.get("horizon", 1) - first.get("horizon", 0))
    start = _to_float(first.get("value")) or 0.0
    end = _to_float(last.get("value")) or 0.0
    if start <= 0 or end <= 0:
        return []

    implied = (end / start) ** (1 / years) - 1
    if cagr > 0 and (implied > cagr * 2 or implied < cagr / 2):
        return [{
            "description": (
                f"Расхождение темпов роста: W04 (рынок) даёт CAGR {cagr * 100:.1f}%, "
                f"а базовый сценарий W06 подразумевает {implied * 100:.1f}% в год."
            ),
            "agents": ["W04", "W06"],
        }]
    return []


def _trl_vs_risk(results: dict[str, dict]) -> list[dict]:
    """Высокий TRL (8-9) плохо сочетается с доминирующим техническим риском."""
    trl = results.get("W03", {}).get("metrics", {}).get("trl")
    risks = results.get("W05", {}).get("calculations", {}).get("risks", [])
    if not trl or not risks:
        return []

    technical = [r for r in risks if r.get("type") == "technical"]
    if not technical:
        return []

    worst = max(technical, key=lambda r: r.get("exposure", 0))
    if int(trl) >= 8 and worst.get("exposure", 0) >= 0.4:
        return [{
            "description": (
                f"Противоречие зрелости: W03 оценивает TRL={trl} (близко к серийному "
                f"внедрению), при этом W05 фиксирует технический риск «{worst.get('title')}» "
                f"с экспозицией {worst.get('exposure'):.2f}."
            ),
            "agents": ["W03", "W05"],
        }]
    return []


def _confidence_spread(results: dict[str, dict]) -> list[dict]:
    """Сильный разброс уверенности аналитиков на одних и тех же источниках."""
    values = {
        agent_id: float(result.get("confidence", 0.5))
        for agent_id, result in results.items()
        if agent_id in ("W03", "W04", "W05", "W06", "W07")
        and result.get("status") == "succeeded"
    }
    if len(values) < 3:
        return []

    lowest = min(values, key=values.get)
    highest = max(values, key=values.get)
    if values[highest] - values[lowest] >= 0.45:
        return [{
            "description": (
                f"Разброс уверенности агентов на одной доказательной базе: "
                f"{highest}={values[highest]:.2f} против {lowest}={values[lowest]:.2f}."
            ),
            "agents": [highest, lowest],
        }]
    return []


def _to_float(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number
