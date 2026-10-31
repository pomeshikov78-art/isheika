"""Реестр агентов W01-W13 и зависимости между ними (ТЗ 5, 6.3)."""
from __future__ import annotations

from .analysis_agents import ComparativeAnalyst, MarketAnalyst, TechnicalAnalyst
from .base import Agent
from .foresight_agents import ForecastAnalyst, RiskAnalyst
from .search_agents import PatentAnalyst, WebSearcher
from .synthesis_agents import Finalizer, Judge
from .verification_agents import ConfidenceAnalyst, Critic, EvidenceAuditor, RedTeam

AGENT_CLASSES: list[type[Agent]] = [
    WebSearcher,        # W01
    PatentAnalyst,      # W02
    TechnicalAnalyst,   # W03
    MarketAnalyst,      # W04
    RiskAnalyst,        # W05
    ForecastAnalyst,    # W06
    ComparativeAnalyst, # W07
    EvidenceAuditor,    # W08
    Critic,             # W09
    RedTeam,            # W10
    ConfidenceAnalyst,  # W11
    Judge,              # W12
    Finalizer,          # W13
]

REGISTRY: dict[str, Agent] = {cls.id: cls() for cls in AGENT_CLASSES}

# Рёбра DAG: агент -> кого дождаться. Это порядок выполнения (ТЗ 4.3),
# а не обязательность: критик работает с тем, что реально отработало.
DEPENDENCIES: dict[str, list[str]] = {
    "W01": [],
    "W02": [],
    "W03": ["W01"],
    "W04": ["W01"],
    "W05": ["W01", "W03", "W04"],
    "W06": ["W01", "W02"],
    "W07": ["W01", "W03"],
    "W08": ["W01", "W02"],
    "W09": ["W03", "W04", "W05", "W06", "W07"],
    "W10": ["W03", "W04", "W05", "W06", "W07"],
    "W11": ["W08", "W09", "W10"],
    "W12": ["W05", "W06", "W07", "W09", "W10", "W11"],
    "W13": ["W12"],
}

# Жёсткие требования: без этих входов агент физически не может работать.
# Отделены от DEPENDENCIES, иначе замыкание по мягким рёбрам затягивает
# в «быстрый» режим весь состав из 13 агентов.
REQUIRES: dict[str, list[str]] = {
    "W03": ["W01"],
    "W04": ["W01"],
    "W05": ["W01"],
    "W06": ["W01"],
    "W07": ["W01"],
    "W08": ["W01"],
    "W11": ["W08"],
    "W12": ["W11"],
    "W13": ["W12"],
}

# Обязательные агенты контура проверки и синтеза: планировщик не вправе их выкинуть,
# иначе отчёт останется без оценки достоверности и критики (ТЗ 3.3-3.4).
MANDATORY = ["W08", "W11", "W12", "W13"]

# Сокращённые наборы под режимы запроса (ТЗ 10.3: mode)
MODE_AGENTS: dict[str, list[str]] = {
    "quick": ["W01", "W03", "W08", "W11", "W12", "W13"],
    "forecast": ["W01", "W02", "W06", "W08", "W09", "W11", "W12", "W13"],
    "risk": ["W01", "W03", "W04", "W05", "W08", "W09", "W10", "W11", "W12", "W13"],
    "comprehensive": [a.id for a in REGISTRY.values()],
    "full": [a.id for a in REGISTRY.values()],
}


def get_agent(agent_id: str) -> Agent:
    agent = REGISTRY.get(agent_id)
    if agent is None:
        raise KeyError(f"Неизвестный агент: {agent_id}")
    return agent


def describe() -> list[dict]:
    return [
        {
            "id": agent.id,
            "name": agent.name,
            "version": agent.version,
            "description": agent.description,
            "depends_on": DEPENDENCIES.get(agent.id, []),
        }
        for agent in REGISTRY.values()
    ]


def resolve_agents(
    mode: str,
    requested: list[str] | None = None,
    suggested: list[str] | None = None,
) -> list[str]:
    """Итоговый набор агентов.

    Приоритет строгий:
      1. requested - явный список из запроса пользователя, замещает режим;
      2. mode - выбор пользователя в интерфейсе;
      3. suggested - предложение планировщика, только РАСШИРЯЕТ набор.

    Планировщик не вправе сужать выбор пользователя: запрошенный режим
    comprehensive означает «все тринадцать», и молчаливое сокращение до шести
    оставляло отчёт без TRL, рисков и прогноза - при том что пользователь
    просил полный анализ.
    """
    requested = [a for a in (requested or []) if a in REGISTRY]
    selected = set(requested or MODE_AGENTS.get(mode, MODE_AGENTS["comprehensive"]))
    selected.update(a for a in (suggested or []) if a in REGISTRY)
    selected.update(MANDATORY)

    # Замыкание только по жёстким требованиям: агент без входных данных
    # бесполезен, но ждать всех подряд «на всякий случай» не нужно.
    changed = True
    while changed:
        changed = False
        for agent_id in list(selected):
            for dependency in REQUIRES.get(agent_id, []):
                if dependency not in selected:
                    selected.add(dependency)
                    changed = True

    return [agent_id for agent_id in REGISTRY if agent_id in selected]
