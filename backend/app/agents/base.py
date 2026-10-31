"""Общий контракт агента (ТЗ 5).

    class Agent:
        id, name, version
        async def execute(context: AgentContext) -> AgentResult
"""
from __future__ import annotations

import abc
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from ..llm.base import LLMProvider
from ..search.gateway import SearchGateway

log = logging.getLogger(__name__)

NEWLINE = chr(10)
NEWLINES = NEWLINE + NEWLINE

_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)


def language_directive(query: str) -> str:
    """Указание агенту, на каком языке отвечать.

    Источники по технологиям почти всегда англоязычные, и без явного указания
    модель уходит на язык источников: на русский запрос приходил отчёт целиком
    на английском. Отчёт читает заказчик, а не поисковая система.
    """
    if _CYRILLIC.search(query or ""):
        return (
            "ВАЖНО: весь текст в ответе (выводы, обоснования, названия характеристик, "
            "формулировки рисков) пиши ПО-РУССКИ, даже если источники англоязычные. "
            "Термины, названия компаний и единицы измерения оставляй как в оригинале."
        )
    return "IMPORTANT: write all text in the response in English."


@dataclass
class Claim:
    """Утверждение с привязкой к доказательствам (ТЗ 3.4: доказательная база)."""

    text: str
    claim_type: str = "fact"
    confidence: float = 0.5
    evidence_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "type": self.claim_type,
            "confidence": round(self.confidence, 3),
            "evidence_urls": self.evidence_urls,
        }


@dataclass
class AgentContext:
    """Вход агента (ТЗ 5: AgentContext)."""

    mission_id: str
    task_id: str
    query: str
    entities: list[str] = field(default_factory=list)
    horizons: list[int] = field(default_factory=lambda: [1, 3, 5, 10])
    params: dict[str, Any] = field(default_factory=dict)
    max_sources: int = 20
    timeout: int = 120
    memory: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    upstream: dict[str, dict] = field(default_factory=dict)  # результаты агентов-предшественников
    llm: LLMProvider | None = None
    search: SearchGateway | None = None

    def upstream_result(self, agent_id: str) -> dict:
        return self.upstream.get(agent_id, {}) or {}

    def collected_sources(self) -> list[dict]:
        """Доказательная база агента: найденное поиском плюс документы
        пользователя.

        Загруженный документ - такой же источник, как найденная статья,
        и должен попадать в перечень источников отчёта: иначе факт из него
        нарушает требование ТЗ 3.4 о ссылке на источник у каждого
        утверждения.
        """
        sources: list[dict] = []
        seen: set[str] = set()

        for document in self.documents:
            reference = f"user-doc://{document.get('id')}"
            sources.append({
                "url": reference,
                "title": document.get("filename") or "документ пользователя",
                "snippet": (document.get("text") or "")[:400],
                "published_at": None,
                "domain": "документ пользователя",
                "source_type": "user_doc",
                "engines": ["upload"],
                # Достоверность пользовательского документа система оценить
                # не может: он не проходил ни рецензирования, ни проверки
                # цитируемостью. Нейтральная оценка честнее завышенной.
                "authority": 0.6,
                "reliability": 0.6,
                "extra": {"pages": document.get("pages")},
            })
            seen.add(reference)

        for agent_id in ("W01", "W02"):
            for source in self.upstream_result(agent_id).get("sources", []):
                url = source.get("url")
                if url and url not in seen:
                    seen.add(url)
                    sources.append(source)
        return sources


@dataclass
class AgentResult:
    """Выход агента (ТЗ 5: AgentResult)."""

    agent_id: str
    status: str = "succeeded"  # succeeded | failed
    summary: str = ""
    claims: list[Claim] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    calculations: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.5
    unresolved: list[str] = field(default_factory=list)
    memory_refs: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "summary": self.summary,
            "claims": [c.to_dict() for c in self.claims],
            "sources": self.sources,
            "metrics": self.metrics,
            "calculations": self.calculations,
            "confidence": round(self.confidence, 3),
            "unresolved": self.unresolved,
            "memory_refs": self.memory_refs,
            "error": self.error,
        }


class Agent(abc.ABC):
    id: str = "W00"
    name: str = "Agent"
    version: str = "1.0"
    description: str = ""
    # Потолок ответа для конкретного агента. Общий лимит рассчитан на короткий
    # структурированный вывод, но судья сводит работу всех остальных, и его
    # JSON втрое длиннее: на общем лимите объект обрывался на середине,
    # и отчёт оставался без итогового заключения.
    max_output_tokens: int | None = None

    @abc.abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult:
        ...

    # --- вспомогательное для наследников ---

    def result(self, **kwargs) -> AgentResult:
        return AgentResult(agent_id=self.id, **kwargs)

    async def ask_json(self, context: AgentContext, system: str, user: str) -> dict:
        """Запрос к LLM с маркером агента: по нему заглушка выбирает форму ответа,
        а логи становятся читаемыми. Язык ответа задаётся языком запроса миссии."""
        if context.llm is None:
            raise RuntimeError(f"{self.id}: LLM-провайдер не передан в контекст")
        prompt = f"[{self.id}] {system}" + NEWLINES + language_directive(context.query)
        return await context.llm.complete_json(prompt, user, max_tokens=self.max_output_tokens)

    @staticmethod
    def parse_claims(raw: list | None, default_confidence: float = 0.5) -> list[Claim]:
        claims: list[Claim] = []
        for item in raw or []:
            if isinstance(item, str):
                claims.append(Claim(text=item, confidence=default_confidence))
                continue
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or item.get("claim") or "").strip()
            if not text:
                continue
            claims.append(
                Claim(
                    text=text,
                    claim_type=str(item.get("type", "fact")),
                    confidence=_to_float(item.get("confidence"), default_confidence),
                    evidence_urls=[u for u in (item.get("evidence_urls") or item.get("sources") or []) if u],
                )
            )
        return claims

    @staticmethod
    def documents_digest(documents: list[dict], total_chars: int = 2400) -> str:
        """Фрагменты загруженных документов для промпта.

        Документу отводится больше места, чем сниппету из поиска: пользователь
        принёс его намеренно, и разбирать его по трёхстрочной выжимке
        бессмысленно. Общий объём ограничен - минутный бюджет провайдера
        списывается по размеру запроса целиком.
        """
        if not documents:
            return ""

        budget = total_chars // max(1, len(documents))
        blocks: list[str] = []

        for index, document in enumerate(documents, start=1):
            name = document.get("filename") or "документ"
            text = " ".join((document.get("text") or "").split())
            if not text:
                # Скан без текстового слоя: молчать об этом нельзя, иначе
                # пользователь решит, что документ учтён
                blocks.append(
                    f"[Д{index}] {name}: текстовый слой отсутствует "
                    f"(вероятно скан, нужен OCR)"
                )
                continue
            blocks.append(f"[Д{index}] {name}" + NEWLINE + f"    {text[:budget]}")

        return NEWLINE.join(blocks)

    @staticmethod
    def sources_digest(sources: list[dict], limit: int = 8, snippet_chars: int = 160) -> str:
        """Компактная выжимка источников для промпта - с номерами, чтобы модель
        могла ссылаться на них при формировании claims.

        Размер намеренно скромный: на бесплатных тарифах лимит считается
        в токенах в минуту, и двадцать источников по 300 символов на каждого
        из тринадцати агентов выбирали минутный бюджет целиком - агенты
        вставали в очередь глубже собственного таймаута и падали. Восьми
        хватает: они отсортированы по релевантности, и девятый добавляет
        к выводу меньше, чем стоит по бюджету.
        """
        lines = []
        for index, source in enumerate(sources[:limit], start=1):
            date = source.get("published_at") or "дата неизв."
            snippet = (source.get("snippet") or "")[:snippet_chars].replace("\n", " ")
            lines.append(
                f"[{index}] {source.get('title') or 'без заголовка'} "
                f"({source.get('domain')}, {date})\n    {source.get('url')}\n    {snippet}"
            )
        return "\n".join(lines) if lines else "Источники не найдены."


def _to_float(value, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number > 1.0:
        number = number / 100.0 if number <= 100.0 else 1.0
    return max(0.0, min(1.0, number))
