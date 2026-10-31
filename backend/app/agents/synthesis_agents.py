"""W12 Judge и W13 Finalizer - синтез и финализация (ТЗ 6.9, 6.12)."""
from __future__ import annotations

import logging

from .base import Agent, AgentContext, AgentResult

log = logging.getLogger(__name__)


class Judge(Agent):
    """W12: взвешенный синтез с учётом критики и разрешением конфликтов.

    Веса агентов задаются кодом, а не моделью: судья должен опираться на
    измеримое качество источников, а не на убедительность формулировок.
    """

    id = "W12"
    name = "Judge"
    description = "Синтез результатов, разрешение конфликтов, итоговое заключение"
    max_output_tokens = 3000

    SYSTEM = (
        "Ты председатель аналитической комиссии. Тебе даны выводы специализированных "
        "агентов, замечания критика и контраргументы red team. Синтезируй итоговое "
        "заключение: учти критику явно, разреши противоречия, отдели факты от гипотез. "
        "Не завышай определённость там, где данных мало. "
        "JSON: {\"summary\": str (2-3 предложения), \"key_findings\": [str], "
        "\"detailed_analysis\": str, \"recommendations\": [{\"text\": str, "
        "\"priority\": \"high|medium|low\", \"rationale\": str}], "
        "\"conflicts_resolution\": [{\"conflict\": str, \"resolution\": str}], "
        "\"accepted_criticism\": [str], \"unresolved_issues\": [str]}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        data = await self.ask_json(context, self.SYSTEM, self._brief(context))

        recommendations = [
            r for r in (data.get("recommendations") or []) if isinstance(r, dict) and r.get("text")
        ]
        priority_order = {"high": 0, "medium": 1, "low": 2}
        recommendations.sort(key=lambda r: priority_order.get(str(r.get("priority", "medium")).lower(), 1))

        confidence = float(
            context.upstream_result("W11").get("metrics", {}).get("confidence", 0.5)
        )

        return self.result(
            summary=str(data.get("summary", ""))[:2000],
            claims=self.parse_claims(
                [{"text": f, "type": "inference", "confidence": confidence}
                 for f in (data.get("key_findings") or [])]
            ),
            metrics={"recommendation_count": len(recommendations)},
            calculations={
                "key_findings": data.get("key_findings", []),
                "detailed_analysis": data.get("detailed_analysis", ""),
                "recommendations": recommendations,
                "conflicts_resolution": data.get("conflicts_resolution", []),
                "accepted_criticism": data.get("accepted_criticism", []),
            },
            confidence=confidence,
            unresolved=list(data.get("unresolved_issues") or []),
        )

    @staticmethod
    def _brief(context: AgentContext) -> str:
        parts = [f"Запрос миссии: {context.query}", ""]

        for agent_id in ("W01", "W02", "W03", "W04", "W05", "W06", "W07"):
            result = context.upstream_result(agent_id)
            if not result:
                continue
            if result.get("status") != "succeeded":
                parts.append(f"[{agent_id}] НЕ ВЫПОЛНЕН: {result.get('error')}")
                continue
            parts.append(f"[{agent_id}] (уверенность {result.get('confidence')}) {result.get('summary')}")
            for claim in (result.get("claims") or [])[:8]:
                parts.append(f"    - {claim.get('text')}")

        critic = context.upstream_result("W09").get("calculations", {})
        if critic.get("issues"):
            parts.append("\nЗамечания критика (W09):")
            parts.extend(
                f"    [{i.get('severity')}] {i.get('text')}" for i in critic["issues"][:10]
            )

        red = context.upstream_result("W10").get("calculations", {})
        if red.get("counterarguments"):
            parts.append("\nКонтраргументы Red Team (W10):")
            parts.extend(
                f"    (сила {c.get('strength')}) {c.get('text')}"
                for c in red["counterarguments"][:8]
            )

        conflicts = context.params.get("conflicts") or []
        if conflicts:
            parts.append("\nВыявленные расхождения между агентами:")
            parts.extend(f"    - {c.get('description')}" for c in conflicts)

        audit = context.upstream_result("W08").get("metrics", {})
        if audit:
            parts.append(
                f"\nКачество источников: средняя надёжность {audit.get('mean_reliability')}, "
                f"недостоверных {audit.get('unreliable_count')}."
            )
        return "\n".join(parts)


class Finalizer(Agent):
    """W13: сборка итогового JSON-отчёта (ТЗ 9, 10.4).

    Сам рендеринг HTML/DOCX/PDF вынесен в app.report - здесь собирается
    структура данных, из которой рендерятся все форматы.
    """

    id = "W13"
    name = "Finalizer"
    description = "Формирование итогового отчёта и прогнозной карты"

    async def execute(self, context: AgentContext) -> AgentResult:
        judge = context.upstream_result("W12")
        judge_calc = judge.get("calculations", {})
        technical = context.upstream_result("W03")
        market = context.upstream_result("W04")
        patents = context.upstream_result("W02")
        comparative = context.upstream_result("W07")
        forecast = context.upstream_result("W06")
        risk = context.upstream_result("W05")
        confidence_result = context.upstream_result("W11")
        audit = context.upstream_result("W08")

        sources = audit.get("sources") or context.collected_sources()
        confidence = float(confidence_result.get("metrics", {}).get("confidence", 0.5))

        unresolved: list[str] = []
        for result in context.upstream.values():
            unresolved.extend(result.get("unresolved") or [])

        report = {
            "query": context.query,
            # Флаг честности: заглушка LLM не должна выдавать себя за анализ
            "demo_mode": getattr(context.llm, "name", "") == "mock",
            "entities": context.entities,
            # 1. Резюме
            "summary": judge.get("summary") or "Итоговое заключение не сформировано.",
            "key_findings": judge_calc.get("key_findings", []),
            # 2. Детальный анализ
            "technology": {
                "name": context.entities[0] if context.entities else context.query,
                "trl": technical.get("metrics", {}).get("trl"),
                "trl_rationale": technical.get("metrics", {}).get("trl_rationale"),
                "characteristics": technical.get("calculations", {}).get("characteristics", []),
                "verdict": technical.get("summary"),
            },
            "analysis": {
                "detailed": judge_calc.get("detailed_analysis", ""),
                "market": market.get("metrics", {}),
                "patents": patents.get("metrics", {}),
                "comparison": comparative.get("calculations", {}).get("comparison", []),
                "comparison_criteria": comparative.get("metrics", {}).get("criteria", []),
            },
            # 3. Прогноз
            "forecast": {
                "metric": forecast.get("metrics", {}).get("metric"),
                "unit": forecast.get("metrics", {}).get("unit"),
                # Откуда взят ряд - существенно для доверия к прогнозу:
                # фактические счётчики публикаций и оценка модели - разные вещи
                "series_source": forecast.get("metrics", {}).get("series_source"),
                "fit": forecast.get("metrics", {}).get("fit", {}),
                "series": forecast.get("calculations", {}).get("series", []),
                "scenarios": forecast.get("calculations", {}).get("scenarios", []),
                "drivers": forecast.get("calculations", {}).get("drivers", []),
                "barriers": forecast.get("calculations", {}).get("barriers", []),
            },
            "risks": risk.get("calculations", {}).get("risks", []),
            "risk_matrix": risk.get("calculations", {}).get("matrix", []),
            "recommendations": judge_calc.get("recommendations", []),
            # 4. Достоверность
            "confidence": round(confidence, 3),
            "confidence_details": confidence_result.get("calculations", {}),
            "criticism": {
                "issues": context.upstream_result("W09").get("calculations", {}).get("issues", []),
                "counterarguments": context.upstream_result("W10")
                .get("calculations", {})
                .get("counterarguments", []),
                "accepted": judge_calc.get("accepted_criticism", []),
            },
            "conflicts_resolution": judge_calc.get("conflicts_resolution", []),
            "sources": sources,
            "unresolved_issues": _dedup(unresolved + list(judge.get("unresolved") or [])),
            "agents_used": sorted(set(context.upstream) | {self.id}),
        }

        return self.result(
            summary="Итоговый отчёт сформирован.",
            metrics={
                "sources_count": len(sources),
                "risks_count": len(report["risks"]),
                "scenarios_count": len(report["forecast"]["scenarios"]),
                "unresolved_count": len(report["unresolved_issues"]),
            },
            calculations={"report": report},
            confidence=confidence,
        )


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        text = (item or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
