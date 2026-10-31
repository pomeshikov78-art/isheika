"""W08 Evidence Auditor, W09 Critic, W10 Red Team, W11 Confidence Analyst.

Контур взаимной проверки (ТЗ 3.3, 6.7-6.9): именно он отличает «Ищейку»
от обычной обёртки над LLM.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from ..analytics import confidence as confidence_utils
from ..search.authority import domain_authority, effective_authority, is_preprint
from .base import Agent, AgentContext, AgentResult


class EvidenceAuditor(Agent):
    """W08: надёжность источников 0..1 по домену, дате, типу и подтверждениям."""

    id = "W08"
    name = "Evidence Auditor"
    description = "Проверка достоверности источников"

    async def execute(self, context: AgentContext) -> AgentResult:
        sources = context.collected_sources()
        if not sources:
            return self.result(
                summary="Нет источников для проверки.",
                confidence=0.0,
                unresolved=["Аудит достоверности не выполнен: источники отсутствуют."],
            )

        current_year = datetime.now(timezone.utc).year
        audited: list[dict] = []
        unreliable: list[str] = []

        for source in sources:
            score, notes = self._score(source, current_year)
            entry = dict(source)
            entry["reliability"] = score
            entry["reliability_notes"] = notes
            audited.append(entry)
            if score < 0.4:
                unreliable.append(source.get("url", ""))

        audited.sort(key=lambda s: s["reliability"], reverse=True)
        mean = sum(s["reliability"] for s in audited) / len(audited)

        return self.result(
            summary=(
                f"Проверено источников: {len(audited)}, средняя надёжность {mean:.2f}, "
                f"ниже порога 0.4: {len(unreliable)}."
            ),
            sources=audited,
            metrics={
                "mean_reliability": round(mean, 3),
                "unreliable_count": len(unreliable),
                "unreliable_urls": unreliable[:20],
                "high_quality_count": sum(1 for s in audited if s["reliability"] >= 0.75),
            },
            confidence=round(min(0.95, 0.5 + 0.02 * len(audited)), 3),
        )

    # Надбавки работают в остатке до единицы, а не складываются с базой.
    # Аддитивная схема с общим срезом упиралась в потолок раньше, чем
    # цитируемость успевала на что-то повлиять: у крупного издателя база
    # уже 0.90, +0.10 метаданных - и любое число ссылок меняло ноль.
    MAX_SCORE = 0.98
    META_WEIGHT = 0.4        # доля остатка, отдаваемая метаданным
    CITATION_WEIGHT = 0.6    # доля остатка, отдаваемая цитируемости
    META_SCALE = 0.18        # сумма положительных надбавок, считаемая максимумом
    CITATION_SCALE = 5000    # цитирований, после которых прирост считаем исчерпанным

    @staticmethod
    def _score(source: dict, current_year: int) -> tuple[float, list[str]]:
        notes: list[str] = []
        extra = source.get("extra") or {}

        base, base_note = effective_authority(source.get("url", ""), extra.get("doi"))
        notes.append(base_note)

        bonus = 0.0     # положительные признаки качества
        penalty = 0.0   # прямые вычеты: они бьют по итогу, а не по остатку

        source_type = source.get("source_type", "web")
        preprint = is_preprint(source.get("url", ""))
        if source_type == "publication" and not preprint:
            bonus += 0.08
            notes.append("рецензируемая публикация +0.08")
        elif preprint:
            notes.append("препринт: надбавки за рецензирование нет")
        elif source_type == "patent":
            bonus += 0.05
            notes.append("патентный документ +0.05")

        if extra.get("doi"):
            bonus += 0.05
            notes.append("есть DOI +0.05")

        engines = len(source.get("engines", []))
        if engines > 1:
            bonus += min(0.10, 0.04 * (engines - 1))
            notes.append(f"подтверждён {engines} движками")

        raw_year = str(source.get("published_at") or "")[:4]
        if raw_year.isdigit():
            age = current_year - int(raw_year)
            if age <= 2:
                bonus += 0.05
                notes.append("свежий источник +0.05")
            elif age > 10:
                penalty += 0.12
                notes.append(f"источник старше 10 лет ({age}) -0.12")
            elif age > 5:
                penalty += 0.05
                notes.append(f"источник старше 5 лет ({age}) -0.05")
        else:
            penalty += 0.05
            notes.append("дата публикации неизвестна -0.05")

        if not (source.get("snippet") or "").strip():
            penalty += 0.05
            notes.append("нет содержательного фрагмента -0.05")

        # ТЗ 5.5 прямо называет цитируемость среди критериев авторитетности.
        # Шкала логарифмическая: разница между 0 и 50 ссылками значима,
        # между 2000 и 5000 - уже нет.
        citations = extra.get("citations")
        citation_norm = 0.0
        if isinstance(citations, (int, float)) and citations > 0:
            citation_norm = min(
                1.0,
                math.log10(citations + 1) / math.log10(EvidenceAuditor.CITATION_SCALE),
            )
            notes.append(f"цитирований {int(citations)}")

        meta_norm = max(0.0, min(1.0, bonus / EvidenceAuditor.META_SCALE))
        headroom = max(0.0, 1.0 - base)
        score = base + headroom * (
            EvidenceAuditor.META_WEIGHT * meta_norm
            + EvidenceAuditor.CITATION_WEIGHT * citation_norm
        ) - penalty

        return round(max(0.02, min(EvidenceAuditor.MAX_SCORE, score)), 3), notes


class Critic(Agent):
    """W09: логические ошибки, необоснованные утверждения, пропущенные факторы."""

    id = "W09"
    name = "Critic"
    description = "Поиск логических ошибок и слабых мест в выводах"

    SYSTEM = (
        "Ты строгий критик аналитического отчёта. Найди логические ошибки, "
        "утверждения без опоры на источники, подмену корреляции причинностью, "
        "пропущенные факторы и необоснованные экстраполяции. Severity: low|medium|high. "
        "Не выдумывай замечаний ради количества: если вывод обоснован, так и скажи. "
        "JSON: {\"issues\": [{\"severity\": str, \"target_agent\": str, \"text\": str}], "
        "\"alternative_hypotheses\": [str], \"missing_factors\": [str], \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос миссии: {context.query}\n\nВыводы агентов:\n{upstream_digest(context)}",
        )

        issues = [i for i in (data.get("issues") or []) if isinstance(i, dict) and i.get("text")]
        weights = {"high": 3, "medium": 2, "low": 1}
        weighted = sum(weights.get(str(i.get("severity", "low")).lower(), 1) for i in issues)

        return self.result(
            summary=f"Критических замечаний: {len(issues)} (взвешенная тяжесть {weighted}).",
            metrics={
                "issue_count": len(issues),
                "weighted_severity": weighted,
                "high_severity": sum(1 for i in issues if str(i.get("severity")).lower() == "high"),
            },
            calculations={
                "issues": issues,
                "alternative_hypotheses": data.get("alternative_hypotheses", []),
                "missing_factors": data.get("missing_factors", []),
            },
            confidence=0.7,
        )


class RedTeam(Agent):
    """W10: преднамеренное опровержение основного вывода."""

    id = "W10"
    name = "Red Team"
    description = "Контраргументы, сценарии краха, критические уязвимости"

    SYSTEM = (
        "Ты red team. Твоя задача - опровергнуть основной вывод анализа. "
        "Строй контраргументы, ищи контрпримеры и сценарии, при которых вывод "
        "окажется ложным. Оценивай силу каждого контраргумента 0..1 честно: "
        "слабый контраргумент помечай слабым. "
        "JSON: {\"counterarguments\": [{\"text\": str, \"strength\": 0..1}], "
        "\"failure_scenarios\": [str], \"critical_vulnerabilities\": [str], "
        "\"verdict\": str, \"confidence\": 0..1}"
    )

    async def execute(self, context: AgentContext) -> AgentResult:
        data = await self.ask_json(
            context,
            self.SYSTEM,
            f"Запрос миссии: {context.query}\n\nВыводы, которые нужно опровергнуть:\n"
            f"{upstream_digest(context)}",
        )

        counters = [c for c in (data.get("counterarguments") or []) if isinstance(c, dict)]
        strengths = []
        for counter in counters:
            try:
                strengths.append(max(0.0, min(1.0, float(counter.get("strength", 0.5)))))
            except (TypeError, ValueError):
                strengths.append(0.5)
        max_strength = max(strengths) if strengths else 0.0

        return self.result(
            summary=str(data.get("verdict", ""))[:1500]
            or f"Сформировано контраргументов: {len(counters)}.",
            metrics={
                "counterargument_count": len(counters),
                "max_strength": round(max_strength, 3),
                "mean_strength": round(sum(strengths) / len(strengths), 3) if strengths else 0.0,
            },
            calculations={
                "counterarguments": counters,
                "failure_scenarios": data.get("failure_scenarios", []),
                "critical_vulnerabilities": data.get("critical_vulnerabilities", []),
            },
            confidence=0.7,
        )


class ConfidenceAnalyst(Agent):
    """W11: итоговая уверенность считается кодом по наблюдаемым факторам.

    Спрашивать уровень уверенности у LLM бессмысленно - она стабильно выдаёт
    0.8-0.9 независимо от того, что реально нашлось.
    """

    id = "W11"
    name = "Confidence Analyst"
    description = "Расчёт итогового уровня уверенности"

    async def execute(self, context: AgentContext) -> AgentResult:
        audited = context.upstream_result("W08").get("sources") or context.collected_sources()
        critic = context.upstream_result("W09")
        red_team = context.upstream_result("W10")

        agent_confidences = [
            float(result.get("confidence", 0.5))
            for agent_id, result in context.upstream.items()
            if agent_id in ("W02", "W03", "W04", "W05", "W06", "W07")
            and result.get("status") == "succeeded"
        ]

        report = confidence_utils.compute(
            audited,
            agent_confidences=agent_confidences or None,
            critic_issues=int(critic.get("metrics", {}).get("issue_count", 0)),
            red_team_strength=float(red_team.get("metrics", {}).get("max_strength", 0.0)),
            conflicts=len(context.params.get("conflicts", [])),
        )

        return self.result(
            summary=(
                f"Итоговая уверенность: {report.value * 100:.1f}% "
                f"({confidence_utils.label(report.value)})."
            ),
            metrics={"confidence": report.value, "label": confidence_utils.label(report.value)},
            calculations=report.to_dict(),
            confidence=report.value,
        )


def upstream_digest(context: AgentContext, limit: int = 700) -> str:
    """Сводка выводов предшественников для критикующих агентов."""
    lines = []
    for agent_id in sorted(context.upstream):
        result = context.upstream[agent_id]
        if result.get("status") != "succeeded" or agent_id in ("W08", "W09", "W10", "W11"):
            continue
        summary = (result.get("summary") or "").strip()
        claims = [c.get("text", "") for c in (result.get("claims") or [])][:4]
        block = f"[{agent_id}] {summary}"
        if claims:
            block += "\n    Утверждения: " + "; ".join(claims)
        lines.append(block[:limit])
    return "\n\n".join(lines) or "Выводы отсутствуют."
