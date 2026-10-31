"""W01 Web Searcher и W02 Patent Analyst - сбор данных (ТЗ 5)."""
from __future__ import annotations

import logging
import re

from ..search.authority import domain_authority
from .base import Agent, AgentContext, AgentResult, Claim

log = logging.getLogger(__name__)


class WebSearcher(Agent):
    """W01: параллельный поиск по всем подключённым движкам, дедупликация,
    обогащение метаданными (ТЗ 5, W01)."""

    id = "W01"
    name = "Web Searcher"
    description = "Поиск по всем подключённым поисковым системам с агрегацией и дедупликацией"

    async def execute(self, context: AgentContext) -> AgentResult:
        if context.search is None:
            return self.result(status="failed", error="Поисковый шлюз недоступен", confidence=0.0)

        queries = context.params.get("search_queries") or [context.query]
        hits = await context.search.search(
            queries,
            max_results=context.max_sources,
            relevance_query=" ".join([context.query, *context.entities]),
        )
        sources = context.search.annotate(hits)

        if not sources:
            return self.result(
                status="succeeded",
                summary="Поиск не дал результатов.",
                confidence=0.05,
                unresolved=["Поисковые движки не вернули ни одного источника."],
            )

        engines_used = sorted({e for s in sources for e in s.get("engines", [])})
        confirmed = [s for s in sources if len(s.get("engines", [])) > 1]

        return self.result(
            summary=(
                f"Найдено источников: {len(sources)} по {len(queries)} запросам "
                f"(движки: {', '.join(engines_used) or 'нет'}). "
                f"Подтверждено несколькими движками: {len(confirmed)}."
            ),
            sources=sources,
            metrics={
                "sources_found": len(sources),
                "engines": engines_used,
                "cross_confirmed": len(confirmed),
                "queries": queries,
            },
            confidence=min(0.9, 0.3 + 0.05 * len(sources)),
            unresolved=[] if len(sources) >= 5 else ["Собрано менее 5 источников."],
        )


_PATENT_NUMBER_RE = re.compile(r"\b([A-Z]{2}\d{6,}(?:[A-Z]\d?)?)\b")


class PatentAnalyst(Agent):
    """W02: патентный ландшафт, оценка уникальности (ТЗ 5, W02)."""

    id = "W02"
    name = "Patent Analyst"
    description = "Поиск патентов, анализ ландшафта, оценка патентоспособности"

    SYSTEM = (
        "Ты патентный аналитик. По списку найденных патентных документов оцени "
        "патентный ландшафт технологии: активность, ключевых правообладателей, "
        "плотность патентования и уникальность решения. "
        "Верни JSON: {\"landscape_summary\": str, \"key_holders\": [str], "
        "\"activity_trend\": \"rising|stable|declining\", \"uniqueness\": 0..1, "
        "\"patentability_notes\": str, \"claims\": [{\"text\": str, \"confidence\": 0..1, "
        "\"evidence_urls\": [str]}], \"unresolved\": [str], \"confidence\": 0..1}. "
        "Если патентов мало, честно снижай confidence."
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        if context.search is None:
            return self.result(status="failed", error="Поисковый шлюз недоступен", confidence=0.0)

        entity = context.entities[0] if context.entities else context.query
        hits = await context.search.search(
            [f"{entity} patent", f"{entity} патент"],
            max_results=max(10, context.max_sources // 2),
            engines=["patents", "duckduckgo"],
            relevance_query=entity,
        )
        sources = [
            s for s in context.search.annotate(hits)
            if "patent" in s.get("url", "").lower() or s.get("source_type") == "patent"
        ]

        numbers = sorted({
            number
            for source in sources
            for number in _PATENT_NUMBER_RE.findall(
                f"{source.get('title', '')} {source.get('url', '')}"
            )
        })

        if not sources:
            return self.result(
                summary="Патентные документы не найдены.",
                confidence=0.1,
                metrics={"patents_found": 0},
                unresolved=["Патентный поиск не дал результатов - нужен ключ USPTO/Espacenet."],
            )

        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Технология: {entity}\n\nНайденные патентные документы:\n"
            f"{self.sources_digest(sources)}",
        )

        for source in sources:
            source["source_type"] = "patent"

        return self.result(
            summary=str(data.get("landscape_summary", ""))[:2000],
            claims=self.parse_claims(data.get("claims")),
            sources=sources,
            metrics={
                "patents_found": len(sources),
                "patent_numbers": numbers[:50],
                "key_holders": data.get("key_holders", []),
                "activity_trend": data.get("activity_trend"),
                "uniqueness": data.get("uniqueness"),
            },
            calculations={"patentability_notes": data.get("patentability_notes")},
            confidence=_confidence(data, len(sources)),
            unresolved=list(data.get("unresolved") or []),
        )


def _confidence(data: dict, source_count: int) -> float:
    try:
        stated = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        stated = 0.5
    # Уверенность не может превышать то, что позволяет объём найденного
    ceiling = min(0.9, 0.2 + 0.08 * source_count)
    return round(max(0.05, min(stated, ceiling)), 3)
