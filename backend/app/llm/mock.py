"""Детерминированная заглушка LLM.

Нужна для двух вещей: прогнать сквозной сценарий без ключей и написать тесты,
не зависящие от сети. Ответ подбирается по маркеру агента [Wxx] в системном
промпте, поэтому каждый агент получает структуру своей формы.
"""
from __future__ import annotations

import json
import re

from .base import LLMMessage, LLMProvider, LLMResponse

_AGENT_RE = re.compile(r"\[(W\d{2})\]")

_CANNED: dict[str, dict] = {
    "PLAN": {
        "task_type": "technology_assessment",
        # Ни сущностей, ни запросов заглушка не придумывает: планировщик в этом
        # случае ищет по самой формулировке миссии. Прежние общие запросы вида
        # «technology review» тянули в выдачу цитируемые работы не по теме.
        "entities": [],
        "search_queries": [],
        "agents": ["W01", "W02", "W03", "W04", "W05", "W06", "W07"],
        "rationale": "Заглушка планировщика (LLM_PROVIDER=mock): разбор запроса не выполнялся.",
    },
    "W03": {
        "trl": 6,
        "trl_rationale": "Демонстрационная оценка заглушки.",
        "characteristics": [
            {"name": "Производительность", "value": "н/д", "comparison": "на уровне аналогов"}
        ],
        "verdict": "Оценка недоступна: система работает на заглушке LLM.",
        "claims": [{"text": "TRL = 6 (заглушка)", "confidence": 0.3}],
        "unresolved": ["Нет реального LLM-провайдера: задайте LLM_PROVIDER и ключ."],
        "confidence": 0.3,
    },
    "W04": {
        "market_size_usd": None,
        "cagr": 0.1,
        "key_players": ["Игрок A", "Игрок B"],
        "price_range": "н/д",
        "claims": [{"text": "Рынок растёт (заглушка)", "confidence": 0.3}],
        "unresolved": ["Данные рынка не собраны: работа на заглушке."],
        "confidence": 0.3,
    },
    "W05": {
        "risks": [
            {"type": "technical", "title": "ДЕМО: незрелость технологии",
             "probability": 0.55, "impact": 0.7,
             "mitigation": "Пилотное внедрение с поэтапной проверкой гипотез."},
            {"type": "market", "title": "ДЕМО: неопределённость спроса",
             "probability": 0.45, "impact": 0.6,
             "mitigation": "Ранние договорённости с пилотными заказчиками."},
            {"type": "regulatory", "title": "ДЕМО: ужесточение требований сертификации",
             "probability": 0.35, "impact": 0.75,
             "mitigation": "Раннее вовлечение регулятора, запас по нормативам."},
            {"type": "financial", "title": "ДЕМО: рост стоимости сырья",
             "probability": 0.6, "impact": 0.45,
             "mitigation": "Долгосрочные контракты и диверсификация поставщиков."},
            {"type": "operational", "title": "ДЕМО: дефицит профильных кадров",
             "probability": 0.5, "impact": 0.4,
             "mitigation": "Программа подготовки и партнёрство с вузами."},
            {"type": "technical", "title": "ДЕМО: деградация характеристик в цикле",
             "probability": 0.3, "impact": 0.8,
             "mitigation": "Ускоренные ресурсные испытания до серии."},
        ],
        "confidence": 0.3,
    },
    "W06": {
        "metric": "ДЕМО: число публикаций в год",
        "unit": "публикаций",
        "series": [
            {"year": 2016, "value": 120}, {"year": 2017, "value": 190},
            {"year": 2018, "value": 310}, {"year": 2019, "value": 520},
            {"year": 2020, "value": 880}, {"year": 2021, "value": 1400},
            {"year": 2022, "value": 2050}, {"year": 2023, "value": 2700},
            {"year": 2024, "value": 3200}, {"year": 2025, "value": 3550},
        ],
        "drivers": ["ДЕМО-данные заглушки: реальные ряды не собирались."],
        "barriers": ["ДЕМО-данные заглушки."],
        "confidence": 0.3,
    },
    "W07": {
        "criteria": ["Стоимость", "Зрелость", "Доступность"],
        "alternatives": [
            {"name": "Альтернатива A", "scores": {"Стоимость": 3, "Зрелость": 4, "Доступность": 3}},
            {"name": "Альтернатива B", "scores": {"Стоимость": 4, "Зрелость": 3, "Доступность": 4}},
        ],
        "verdict": "Сравнение недоступно на заглушке.",
        "confidence": 0.3,
    },
    "W09": {
        "issues": [
            {"severity": "high", "text": "Выводы получены на заглушке LLM, а не на реальных данных."}
        ],
        "alternative_hypotheses": ["Технология может быть переоценена."],
        "confidence": 0.3,
    },
    "W10": {
        "counterarguments": [
            {"text": "Прогноз не подкреплён источниками.", "strength": 0.8}
        ],
        "failure_scenarios": ["Технология не выходит из лабораторной стадии."],
        "confidence": 0.3,
    },
    "W12": {
        "summary": (
            "ДЕМОНСТРАЦИОННЫЙ РЕЖИМ. LLM-провайдер не настроен, поэтому содержательные "
            "выводы не формировались: показана структура отчёта и работа конвейера "
            "на демо-данных заглушки."
        ),
        "key_findings": [
            "ДЕМО: конвейер отработал полностью, все агенты вернули результат.",
            "ДЕМО: источники собраны и проверены реальным поиском по интернету.",
            "Для содержательного анализа задайте LLM_PROVIDER и ключ в .env.",
        ],
        "detailed_analysis": (
            "Раздел заполняется агентом W12 (Judge) по результатам W01-W11. "
            "В демонстрационном режиме содержательный текст не генерируется."
        ),
        "recommendations": [
            {"text": "Задать ключ DeepSeek или Qwen в .env и перезапустить миссию",
             "priority": "high", "rationale": "Без LLM агенты не формируют выводы."},
            {"text": "Подключить патентные базы USPTO/Espacenet по ключу",
             "priority": "medium", "rationale": "Расширяет доказательную базу W02."},
        ],
        "conflicts_resolution": [],
        "accepted_criticism": ["ДЕМО: выводы не опираются на анализ LLM."],
        "unresolved_issues": ["Демонстрационный режим: содержательные выводы отсутствуют."],
        "confidence": 0.3,
    },
}

_DEFAULT = {
    "summary": "Ответ заглушки LLM.",
    "claims": [],
    "confidence": 0.3,
    "unresolved": ["Работа на заглушке: реальный провайдер не настроен."],
}


class MockProvider(LLMProvider):
    name = "mock"
    model = "mock-1"

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        joined = "\n".join(m.content for m in messages)
        key = "PLAN" if "[PLANNER]" in joined else None
        if key is None:
            found = _AGENT_RE.search(joined)
            key = found.group(1) if found else ""
        payload = _CANNED.get(key, _DEFAULT)
        return LLMResponse(text=json.dumps(payload, ensure_ascii=False), model=self.model)
