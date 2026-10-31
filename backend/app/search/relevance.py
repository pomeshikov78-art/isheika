"""Оценка релевантности найденного источника запросу миссии.

Без этого шага шлюз ранжировал выдачу по авторитетности и числу подтвердивших
движков — и в топ попадали цитируемые, но посторонние работы: на запрос про
аккумуляторы поднимались обзоры по биржевым прогнозам, потому что планировщик
добавил общий запрос «technology review». Авторитетный источник не по теме
хуже среднего, но по теме.
"""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)

# Слова, которые есть почти в любом запросе и потому ничего не различают.
# Отдельно сюда входит служебная обвязка, которую планировщик добавляет
# к формулировкам: «market forecast», «technology review» и подобное.
# Без этого набор терминов из одной обвязки делал релевантным кого угодно.
_STOPWORDS = {
    # русский
    "оцени", "оценить", "оценка", "анализ", "проанализируй", "перспективы",
    "прогноз", "прогнозирование", "спрогнозируй", "выяви", "сравни", "сравнение",
    "для", "или", "как", "что", "это", "при", "над", "под", "года", "лет", "до",
    "технологии", "технология", "технологий", "рынок", "рынка", "обзор",
    "развитие", "применение", "исследование", "тенденции", "направления",
    "современные", "новые", "основные", "ключевые",
    # английский
    "the", "and", "for", "with", "using", "based", "study", "analysis",
    "technology", "technologies", "review", "overview", "market", "forecast",
    "forecasting", "trend", "trends", "prospects", "outlook", "future",
    "development", "applications", "application", "research", "current",
    "recent", "advances", "progress", "state", "of", "in", "on", "a", "an",
    "to", "is", "are", "new", "modern", "key", "main",
}

# Минимальная длина значимого токена
_MIN_LEN = 4
# Длина префикса для сопоставления словоформ. Восьми символов достаточно,
# чтобы «аккумуляторов» и «аккумуляторные» совпали, но «электромобилей»
# и «электронного» - уже нет: на пяти символах они давали ложное совпадение,
# и статья про обучение языкам выходила в топ по запросу об аккумуляторах.
_STEM = 8


def extract_terms(*texts: str) -> list[str]:
    """Значимые термины запроса: без стоп-слов, коротких и повторов."""
    terms: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for token in _TOKEN_RE.findall((text or "").lower()):
            if len(token) < _MIN_LEN or token in _STOPWORDS or token.isdigit():
                continue
            stem = token[:_STEM]
            if stem in seen:
                continue
            seen.add(stem)
            terms.append(token)
    return terms


def score(text: str, terms: list[str]) -> float:
    """Доля терминов запроса, встретившихся в тексте источника: 0..1."""
    if not terms:
        return 1.0  # не с чем сравнивать - не наказываем источник
    haystack = (text or "").lower()
    if not haystack:
        return 0.0
    matched = sum(1 for term in terms if term[:_STEM] in haystack)
    return matched / len(terms)


def build_term_sets(*texts: str) -> list[list[str]]:
    """Наборы терминов по каждой формулировке отдельно.

    Поиск идёт и на русском, и на английском, поэтому сравнивать источник
    с одним общим мешком слов нельзя: англоязычная статья ровно по теме
    не содержит ни одного русского термина и получала бы нулевую оценку.
    Формулировки, состоящие из одних общих слов («technology review»),
    отбрасываются: по ним релевантен кто угодно.
    """
    sets = []
    for text in texts:
        terms = extract_terms(text)
        if terms:
            sets.append(terms)
    return sets


def best_score(text: str, term_sets: list[list[str]]) -> float:
    """Максимум соответствия по всем формулировкам запроса."""
    if not term_sets:
        return 1.0
    return max(score(text, terms) for terms in term_sets)


def annotate_hits(hits: list, term_sets: list[list[str]]) -> None:
    """Проставляет каждому результату его релевантность (in-place)."""
    for hit in hits:
        text = f"{getattr(hit, 'title', '')} {getattr(hit, 'snippet', '')}"
        hit.extra["relevance"] = round(best_score(text, term_sets), 3)
