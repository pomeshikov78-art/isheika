"""Task Planner: нормализация запроса, декомпозиция, построение DAG (ТЗ 6.2-6.3)."""
from __future__ import annotations

import logging
import re

from ..agents.registry import DEPENDENCIES, resolve_agents
from ..domains import detect as detect_domain, prompt_hint
from ..llm.base import LLMError, LLMProvider

log = logging.getLogger(__name__)

SYSTEM = (
    "[PLANNER] Ты планировщик исследовательской миссии. Разбери запрос пользователя: "
    "определи тип задачи, целевые сущности (технологии, изделия, организации, DOI) "
    "и сформулируй 3-6 поисковых запросов - обязательно и на русском, и на английском, "
    "поскольку поиск идёт по всему интернету. "
    "Типы задач: technology_assessment, product_evaluation, foresight, "
    "risk_analysis, comparison, publication_analysis. "
    "JSON: {\"task_type\": str, \"entities\": [str], \"search_queries\": [str], "
    "\"suggested_agents\": [\"W01\"...], \"rationale\": str}"
)


def normalize_query(query: str) -> str:
    """ТЗ 6, шаг [1]: нормализация - схлопывание пробелов и служебных символов."""
    text = re.sub(r"\s+", " ", (query or "").strip())
    return text[:4000]


async def plan(
    llm: LLMProvider,
    query: str,
    *,
    mode: str = "comprehensive",
    memory_context: str = "",
    requested_agents: list[str] | None = None,
) -> dict:
    """Возвращает план миссии: тип, сущности, поисковые запросы, DAG задач."""
    query = normalize_query(query)
    user = f"Запрос пользователя: {query}"

    # Отраслевая подсказка: без профильных английских терминов выборка
    # в международных базах беднее в разы, а критерии сравнения у каждого
    # класса решений свои
    domain = detect_domain(query)
    hint = prompt_hint(domain)
    if hint:
        user += chr(10) + chr(10) + hint

    if memory_context:
        user += f"\n\nПамять о прошлых миссиях:\n{memory_context}"

    try:
        data = await llm.complete_json(SYSTEM, user)
    except LLMError as exc:
        # Планировщик не имеет права уронить миссию: без него работает
        # безопасный план по умолчанию с запросом «как есть».
        log.warning("Планировщик не смог разобрать ответ LLM (%s), используется план по умолчанию", exc)
        data = {}

    entities = [str(e).strip() for e in (data.get("entities") or []) if str(e).strip()]
    queries = [str(q).strip() for q in (data.get("search_queries") or []) if str(q).strip()]
    if not queries:
        queries = [query]
    if query not in queries:
        queries.insert(0, query)

    # Предложение модели только дополняет выбор пользователя, но не урезает его
    agent_ids = resolve_agents(mode, requested_agents, data.get("suggested_agents"))

    return {
        "task_type": data.get("task_type") or "technology_assessment",
        "domain": domain.key if domain else None,
        "domain_title": domain.title if domain else None,
        "entities": entities or [query],
        "search_queries": queries[:6],
        "rationale": data.get("rationale", ""),
        "agents": agent_ids,
        "dag": build_dag(agent_ids),
    }


def build_dag(agent_ids: list[str]) -> dict[str, list[str]]:
    """DAG только по выбранным агентам: зависимости от невыбранных отбрасываются."""
    selected = set(agent_ids)
    return {
        agent_id: [d for d in DEPENDENCIES.get(agent_id, []) if d in selected]
        for agent_id in agent_ids
    }


def topological_levels(dag: dict[str, list[str]]) -> list[list[str]]:
    """Разбивка DAG на уровни: внутри уровня агенты выполняются параллельно.

    Заодно защита от цикла: если граф не осушается, оставшиеся узлы
    возвращаются одним уровнем, а не зависают навсегда.
    """
    remaining = {node: set(deps) for node, deps in dag.items()}
    levels: list[list[str]] = []

    while remaining:
        ready = sorted(node for node, deps in remaining.items() if not deps)
        if not ready:
            log.error("Цикл в DAG задач: %s", sorted(remaining))
            levels.append(sorted(remaining))
            break
        levels.append(ready)
        for node in ready:
            remaining.pop(node)
        for deps in remaining.values():
            deps.difference_update(ready)

    return levels
