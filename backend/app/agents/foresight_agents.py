"""W05 Risk Analyst и W06 Forecast Analyst - риски и форсайт (ТЗ 5.3, 5.4)."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from ..analytics import risks as risk_utils
from ..analytics.scurve import build_scenarios, fit_scurve
from .base import Agent, AgentContext, AgentResult


class RiskAnalyst(Agent):
    """W05: пять типов рисков, количественная оценка, матрица 5x5."""

    id = "W05"
    name = "Risk Analyst"
    description = "Идентификация и количественная оценка рисков"
    max_output_tokens = 2000

    SYSTEM = (
        "Ты риск-аналитик. Выяви риски по пяти типам: technical, market, "
        "regulatory, financial, operational - не менее одного на каждый тип, "
        "всего 6-12. Для каждого укажи вероятность и влияние долями 0..1 "
        "и конкретную меру снижения. "
        "JSON: {\"risks\": [{\"type\": str, \"title\": str, \"probability\": 0..1, "
        "\"impact\": 0..1, \"mitigation\": str, \"evidence_urls\": [str]}], "
        "\"unresolved\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()
        technical = context.upstream_result("W03")
        market = context.upstream_result("W04")

        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос: {context.query}\n"
            f"Технический анализ: {technical.get('summary', 'нет данных')}\n"
            f"Рыночный анализ: {market.get('summary', 'нет данных')}\n\n"
            f"{_documents_block(context)}"
            f"Источники:\n{self.sources_digest(sources)}",
        )

        normalized = [risk_utils.normalize_risk(r) for r in (data.get("risks") or []) if isinstance(r, dict)]
        normalized.sort(key=lambda r: r["exposure"], reverse=True)
        gaps = risk_utils.coverage_gaps(normalized)

        unresolved = list(data.get("unresolved") or [])
        if gaps:
            unresolved.append("Не покрыты типы рисков: " + ", ".join(gaps))

        by_type = Counter(r["type"] for r in normalized)
        top = normalized[0] if normalized else None

        return self.result(
            summary=(
                f"Выявлено рисков: {len(normalized)} по {len(by_type)} типам. "
                + (f"Наибольшая экспозиция - «{top['title']}» ({top['exposure']:.2f}, {top['severity']})."
                   if top else "Риски не выявлены.")
            ),
            metrics={
                "risk_count": len(normalized),
                "by_type": dict(by_type),
                "max_exposure": top["exposure"] if top else 0.0,
                "coverage_gaps": gaps,
            },
            calculations={
                "risks": normalized,
                "matrix": risk_utils.build_matrix(normalized),
            },
            confidence=_bounded(data, len(sources)),
            unresolved=unresolved,
        )


class ForecastAnalyst(Agent):
    """W06: S-кривые и три сценария на горизонты 1-20 лет.

    Разделение труда: LLM извлекает временной ряд из источников, а подгонка
    кривой и построение сценариев считаются кодом. Доверять модели численную
    экстраполяцию нельзя - она даёт правдоподобные, но невоспроизводимые числа.
    """

    id = "W06"
    name = "Forecast Analyst"
    description = "Форсайт на 1-20 лет: S-кривые, сценарии, точки перегиба"
    max_output_tokens = 1500

    SYSTEM = (
        "Ты аналитик-форсайтер. Из источников извлеки временной ряд, отражающий "
        "динамику технологии (число патентов, публикаций, объём рынка, доля "
        "внедрения) - только реальные числа с годами, ничего не выдумывай. "
        "JSON: {\"metric\": str, \"unit\": str, \"series\": [{\"year\": int, "
        "\"value\": number, \"source_url\": str}], \"drivers\": [str], "
        "\"barriers\": [str], \"unresolved\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()

        # Фактическая динамика публикаций из OpenAlex - основной источник ряда.
        # Просить временной ряд у модели ненадёжно: в статьях таких таблиц нет,
        # и ряд приходил пустым, из-за чего прогноз не строился вовсе.
        trend = await self._publication_trend(context)
        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос: {context.query}\nГоризонты прогноза (лет): {context.horizons}\n\n"
            f"{_documents_block(context)}"
            f"Источники:\n{self.sources_digest(sources, limit=15)}",
        )

        series = _clean_series(data.get("series"))
        source_note = "данные из источников"
        if len(trend) > len(series):
            series = trend
            data["metric"] = "Число научных публикаций по теме (OpenAlex)"
            data["unit"] = "публикаций в год"
            source_note = "фактическая динамика публикаций OpenAlex"
        base_year = datetime.now(timezone.utc).year
        unresolved = list(data.get("unresolved") or [])

        if len(series) < 3:
            unresolved.append(
                "Временной ряд слишком короткий для S-кривой "
                f"(точек: {len(series)}) - прогноз построен не будет."
            )
            return self.result(
                summary="Данных для количественного форсайта недостаточно.",
                metrics={"series_points": len(series), "metric": data.get("metric")},
                calculations={"series": series, "drivers": data.get("drivers", []),
                              "barriers": data.get("barriers", [])},
                confidence=0.15,
                unresolved=unresolved,
            )

        years = [p["year"] for p in series]
        values = [p["value"] for p in series]
        fit = fit_scurve(years, values)
        scenarios = build_scenarios(fit, base_year, context.horizons or [1, 3, 5, 10])

        inflection = fit.to_dict().get("inflection_year")
        summary = (
            f"Подгонка {fit.method} по {len(series)} точкам (R²={fit.r2:.2f}), "
            f"метрика: {data.get('metric') or 'не указана'}."
        )
        if inflection:
            summary += f" Точка перегиба S-кривой: {inflection:.0f} г."

        summary += f" Источник ряда: {source_note}."

        return self.result(
            summary=summary,
            metrics={
                "metric": data.get("metric"),
                "unit": data.get("unit"),
                "series_points": len(series),
                "series_source": source_note,
                "fit": fit.to_dict(),
                "base_year": base_year,
            },
            calculations={
                "series": series,
                "scenarios": scenarios,
                "drivers": data.get("drivers", []),
                "barriers": data.get("barriers", []),
            },
            confidence=round(min(_bounded(data, len(sources)), 0.3 + 0.5 * fit.r2), 3),
            unresolved=unresolved,
        )


    @staticmethod
    async def _publication_trend(context: AgentContext) -> list[dict]:
        """Динамика публикаций по годам.

        Формулировки перебираются по очереди: OpenAlex индексирует
        преимущественно англоязычные работы, и по русскому названию
        технологии ряд получается пустым или слишком коротким.
        """
        if context.search is None:
            return []

        candidates = [*context.entities, context.query]
        # Латиница вперёд: по ней выборка на порядок полнее
        candidates.sort(key=lambda text: any("а" <= ch <= "я" for ch in text.lower()))

        best: list[dict] = []
        for candidate in candidates[:3]:
            series = await context.search.publication_trend(candidate)
            if len(series) > len(best):
                best = series
            if len(best) >= 8:
                break
        return best



def _documents_block(context: AgentContext) -> str:
    """Документы пользователя идут в промпт отдельным блоком: они принесены
    намеренно и весомее случайной находки поиска, поэтому и места им
    отводится больше, и ссылаться на них агент должен явно."""
    digest = Agent.documents_digest(context.documents)
    if not digest:
        return ""
    header = "Документы, загруженные пользователем (ссылайся как [Д1], [Д2]):"
    return header + chr(10) + digest + chr(10) + chr(10)

def _clean_series(raw) -> list[dict]:
    """Отбрасывает мусорные точки: без года, с нечисловым значением, вне 1900-2100."""
    cleaned: dict[int, dict] = {}
    for point in raw or []:
        if not isinstance(point, dict):
            continue
        try:
            year = int(point.get("year"))
            value = float(point.get("value"))
        except (TypeError, ValueError):
            continue
        if not (1900 <= year <= 2100):
            continue
        cleaned[year] = {"year": year, "value": value, "source_url": point.get("source_url")}
    return [cleaned[year] for year in sorted(cleaned)]


def _bounded(data: dict, source_count: int) -> float:
    try:
        stated = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        stated = 0.5
    if stated > 1.0:
        stated /= 100.0
    return round(max(0.05, min(stated, min(0.9, 0.2 + 0.06 * source_count))), 3)
