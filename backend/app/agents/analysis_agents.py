"""W03 Technical, W04 Market, W07 Comparative - аналитический контур (ТЗ 5)."""
from __future__ import annotations

from .base import Agent, AgentContext, AgentResult

_CONF_CEILING_PER_SOURCE = 0.06



def _documents_block(context: AgentContext) -> str:
    """Документы пользователя идут в промпт отдельным блоком: они принесены
    намеренно и весомее случайной находки поиска, поэтому и места им
    отводится больше, и ссылаться на них агент должен явно."""
    digest = Agent.documents_digest(context.documents)
    if not digest:
        return ""
    header = "Документы, загруженные пользователем (ссылайся как [Д1], [Д2]):"
    return header + chr(10) + digest + chr(10) + chr(10)

def _bounded_confidence(data: dict, source_count: int, floor: float = 0.05) -> float:
    try:
        stated = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        stated = 0.5
    if stated > 1.0:
        stated = stated / 100.0
    ceiling = min(0.92, 0.2 + _CONF_CEILING_PER_SOURCE * source_count)
    return round(max(floor, min(stated, ceiling)), 3)


class TechnicalAnalyst(Agent):
    """W03: TRL 1-9 и сравнение ТТХ (ТЗ 5.2)."""

    id = "W03"
    name = "Technical Analyst"
    description = "Оценка TRL и технических характеристик"
    max_output_tokens = 1600

    SYSTEM = (
        "Ты технический аналитик. По собранным источникам оцени уровень готовности "
        "технологии по шкале TRL 1-9 и сопоставь ключевые характеристики с мировыми "
        "аналогами (производительность, стоимость, надёжность, энергоэффективность). "
        "Опирайся ТОЛЬКО на приведённые источники; чего в них нет - в unresolved. "
        "JSON: {\"trl\": 1..9, \"trl_rationale\": str, \"characteristics\": "
        "[{\"name\": str, \"value\": str, \"comparison\": str}], \"verdict\": str, "
        "\"claims\": [{\"text\": str, \"confidence\": 0..1, \"evidence_urls\": [str]}], "
        "\"unresolved\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()
        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос: {context.query}\nОбъекты анализа: {', '.join(context.entities) or 'не выделены'}\n\n"
            f"{_documents_block(context)}"
            f"Источники:\n{self.sources_digest(sources)}\n\n"
            f"{_memory_block(context)}",
        )

        trl = data.get("trl")
        try:
            trl = int(trl)
            trl = trl if 1 <= trl <= 9 else None
        except (TypeError, ValueError):
            trl = None

        return self.result(
            summary=str(data.get("verdict", ""))[:2000],
            claims=self.parse_claims(data.get("claims")),
            metrics={"trl": trl, "trl_rationale": data.get("trl_rationale")},
            calculations={"characteristics": data.get("characteristics", [])},
            confidence=_bounded_confidence(data, len(sources)),
            unresolved=list(data.get("unresolved") or []),
        )


class MarketAnalyst(Agent):
    """W04: объём рынка, CAGR, игроки, цены (ТЗ 5, W04)."""

    id = "W04"
    name = "Market Analyst"
    description = "Рыночные тренды, конкуренты, ценовые сегменты"

    SYSTEM = (
        "Ты рыночный аналитик. По источникам оцени рынок: объём (USD), CAGR, "
        "ключевых игроков, ценовой диапазон, региональные особенности. "
        "Числа приводи только те, что есть в источниках, каждое - с ссылкой в claims. "
        "JSON: {\"market_size_usd\": number|null, \"market_size_year\": int|null, "
        "\"cagr\": 0..1|null, \"key_players\": [str], \"price_range\": str, "
        "\"regions\": [str], \"claims\": [{\"text\": str, \"confidence\": 0..1, "
        "\"evidence_urls\": [str]}], \"unresolved\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()
        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос: {context.query}\n\nИсточники:\n{self.sources_digest(sources)}\n\n"
            f"{_memory_block(context)}",
        )

        return self.result(
            summary=_market_summary(data),
            claims=self.parse_claims(data.get("claims")),
            metrics={
                "market_size_usd": data.get("market_size_usd"),
                "market_size_year": data.get("market_size_year"),
                "cagr": data.get("cagr"),
                "key_players": data.get("key_players", []),
                "price_range": data.get("price_range"),
                "regions": data.get("regions", []),
            },
            confidence=_bounded_confidence(data, len(sources)),
            unresolved=list(data.get("unresolved") or []),
        )


def _market_summary(data: dict) -> str:
    parts = []
    if data.get("market_size_usd"):
        year = data.get("market_size_year")
        parts.append(f"Объём рынка: {data['market_size_usd']}" + (f" ({year})" if year else ""))
    if data.get("cagr") is not None:
        try:
            parts.append(f"CAGR: {float(data['cagr']) * 100:.1f}%")
        except (TypeError, ValueError):
            pass
    if data.get("key_players"):
        parts.append("Игроки: " + ", ".join(map(str, data["key_players"][:6])))
    return ". ".join(parts) or "Рыночные данные в источниках не найдены."


class ComparativeAnalyst(Agent):
    """W07: бенчмаркинг с альтернативами (ТЗ 5, W07)."""

    id = "W07"
    name = "Comparative Analyst"
    description = "Бенчмаркинг с альтернативными решениями"
    max_output_tokens = 1600

    SYSTEM = (
        "Ты аналитик по бенчмаркингу. Сравни объект запроса с реальными "
        "альтернативами из источников по 4-6 критериям. Оценки по шкале 1-5. "
        "JSON: {\"criteria\": [str], \"alternatives\": [{\"name\": str, "
        "\"scores\": {критерий: 1..5}, \"strengths\": [str], \"weaknesses\": [str]}], "
        "\"verdict\": str, \"unresolved\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()
        technical = context.upstream_result("W03")

        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос: {context.query}\n"
            f"Технические данные: {technical.get('summary', 'нет')}\n\n"
            f"{_documents_block(context)}"
            f"Источники:\n{self.sources_digest(sources)}",
        )

        alternatives = data.get("alternatives") or []
        ranking = sorted(
            (
                {
                    "name": str(alt.get("name", "без названия")),
                    "total": sum(_num(v) for v in (alt.get("scores") or {}).values()),
                    "scores": alt.get("scores") or {},
                    "strengths": alt.get("strengths", []),
                    "weaknesses": alt.get("weaknesses", []),
                }
                for alt in alternatives
                if isinstance(alt, dict)
            ),
            key=lambda a: a["total"],
            reverse=True,
        )

        return self.result(
            summary=str(data.get("verdict", ""))[:2000],
            metrics={"criteria": data.get("criteria", []), "leader": ranking[0]["name"] if ranking else None},
            calculations={"comparison": ranking},
            confidence=_bounded_confidence(data, len(sources)),
            unresolved=list(data.get("unresolved") or []),
        )


def _num(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _memory_block(context: AgentContext) -> str:
    if not context.memory:
        return ""
    from ..memory.store import as_context

    return as_context(context.memory)
