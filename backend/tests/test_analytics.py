"""Тесты аналитического ядра: S-кривые, риски, уверенность, дедупликация."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.analytics import confidence, risks
from app.analytics.scurve import build_scenarios, fit_scurve
from app.llm.base import extract_json
from app.search.authority import domain_authority
from app.search.engines import SearchHit
from app.search.gateway import SearchGateway


def test_logistic_fit_recovers_saturation():
    """Ряд, сгенерированный логистикой, должен опознаваться как логистика."""
    K, r, t0 = 1000.0, 0.6, 2018.0
    years = list(range(2010, 2025))
    values = [K / (1 + pow(2.718281828, -r * (t - t0))) for t in years]

    fit = fit_scurve(years, values)

    assert fit.method == "logistic"
    assert fit.r2 > 0.98
    assert abs(fit.t0 - t0) < 1.5
    assert 0.8 * K <= fit.K <= 1.6 * K


def test_short_series_degrades_to_linear():
    fit = fit_scurve([2020, 2021, 2022], [10, 20, 30])
    assert fit.method in ("linear", "flat")


def test_scenarios_cover_all_horizons():
    fit = fit_scurve(list(range(2015, 2025)), [i**2 for i in range(1, 11)])
    scenarios = build_scenarios(fit, 2025, [1, 5, 10])

    assert len(scenarios) == 9  # 3 горизонта x 3 сценария
    assert {s["scenario"] for s in scenarios} == {"optimistic", "base", "pessimistic"}

    # Разброс обязан расширяться с горизонтом
    def spread(horizon):
        points = {s["scenario"]: s["value"] for s in scenarios if s["horizon"] == horizon}
        return points["optimistic"] - points["pessimistic"]

    assert spread(10) > spread(1)


def test_risk_normalization_handles_percentages():
    """LLM часто отдаёт вероятность в процентах - она должна стать долей."""
    risk = risks.normalize_risk({"type": "market", "title": "Спрос", "probability": 60, "impact": 0.8})
    assert risk["probability"] == 0.6
    assert risk["exposure"] == round(0.6 * 0.8, 4)
    assert 1 <= risk["probability_band"] <= 5


def test_risk_coverage_gaps():
    found = [risks.normalize_risk({"type": "technical", "title": "A"})]
    gaps = risks.coverage_gaps(found)
    assert len(gaps) == 4  # из пяти типов покрыт один


def test_confidence_penalized_by_criticism():
    sources = [
        {"domain": "nature.com", "reliability": 0.95, "published_at": "2024-01-01"},
        {"domain": "arxiv.org", "reliability": 0.78, "published_at": "2023-06-01"},
    ]
    clean = confidence.compute(sources, agent_confidences=[0.7, 0.75])
    criticized = confidence.compute(
        sources, agent_confidences=[0.7, 0.75], critic_issues=5, red_team_strength=0.8
    )
    assert criticized.value < clean.value


def test_deduplication_merges_and_counts_engines():
    hits = [
        SearchHit(url="https://www.example.com/a?utm_source=x", title="A", engine="duckduckgo"),
        SearchHit(url="http://example.com/a/", title="A", snippet="длиннее", engine="crossref"),
    ]
    merged = SearchGateway.deduplicate(hits)

    assert len(merged) == 1
    assert merged[0].snippet == "длиннее"
    assert set(merged[0].extra["engines"]) == {"duckduckgo", "crossref"}


def test_domain_authority_ranking():
    assert domain_authority("https://nature.com/x") > domain_authority("https://medium.com/x")
    assert domain_authority("https://sub.uspto.gov/y") > 0.8
    assert domain_authority("https://unknown-site.xyz/") == 0.5


def test_extract_json_from_noisy_output():
    assert extract_json('```json\n{"a": 1}\n```')["a"] == 1
    assert extract_json('Вот результат: {"b": {"c": 2}} - готово')["b"]["c"] == 2


def test_publisher_recognized_by_doi_prefix():
    """Ссылка через doi.org скрывает издателя - его определяет префикс DOI."""
    from app.search.authority import effective_authority, publisher_authority

    assert publisher_authority("10.1038/s41560-024-1") == 0.96  # Nature
    assert publisher_authority("10.99999/unknown") is None

    nature, _ = effective_authority("https://doi.org/10.1038/x", "10.1038/x")
    unknown, _ = effective_authority("https://doi.org/10.99999/x", "10.99999/x")
    assert nature > unknown


def test_preprints_do_not_get_peer_review_bonus():
    from app.search.authority import is_preprint

    assert is_preprint("https://arxiv.org/abs/2401.00001")
    assert is_preprint("https://www.biorxiv.org/content/10.1101/x")
    assert not is_preprint("https://doi.org/10.1038/x")


def test_source_ranking_is_defensible():
    """Статья в рецензируемом журнале должна обходить свежий препринт,
    а не наоборот - именно эта инверсия и была ошибкой."""
    from app.agents.verification_agents import EvidenceAuditor

    auditor = EvidenceAuditor()
    journal, _ = auditor._score(
        {"url": "https://doi.org/10.1038/x", "source_type": "publication",
         "published_at": "2023-01-01", "snippet": "текст",
         "extra": {"doi": "10.1038/x", "citations": 2540}, "engines": ["openalex", "crossref"]},
        2026,
    )
    preprint, _ = auditor._score(
        {"url": "https://arxiv.org/abs/2401.1", "source_type": "publication",
         "published_at": "2024-01-01", "snippet": "текст",
         "extra": {"citations": 3}, "engines": ["arxiv"]},
        2026,
    )
    blog, _ = auditor._score(
        {"url": "https://medium.com/@a/b", "source_type": "web", "snippet": "",
         "extra": {}, "engines": ["duckduckgo"]},
        2026,
    )
    assert journal > preprint > blog


def test_citations_raise_score_monotonically():
    from app.agents.verification_agents import EvidenceAuditor

    auditor = EvidenceAuditor()

    def score(citations):
        value, _ = auditor._score(
            {"url": "https://doi.org/10.1016/x", "source_type": "publication",
             "published_at": "2024-01-01", "snippet": "текст",
             "extra": {"doi": "10.1016/x", "citations": citations}, "engines": ["openalex"]},
            2026,
        )
        return value

    assert score(0) < score(10) < score(500)
    # Логарифмическая шкала: прирост затухает
    assert score(5000) - score(1000) < score(100) - score(10)


def test_openalex_abstract_reconstruction():
    """Аннотация приходит инвертированным индексом - её надо собрать обратно."""
    from app.search.engines import _openalex_abstract

    inverted = {"Твердотельные": [0], "аккумуляторы": [1], "перспективны": [2]}
    assert _openalex_abstract(inverted) == "Твердотельные аккумуляторы перспективны"
    assert _openalex_abstract(None) == ""


def test_relevance_rejects_false_stem_match():
    """«электронного» не должно совпадать с «электромобилей» - на коротком
    префиксе именно это выводило посторонние статьи в топ выдачи."""
    from app.search import relevance

    terms = relevance.extract_terms("твердотельные аккумуляторы для электромобилей")
    assert relevance.score("Обучение с применением электронного журнала", terms) == 0.0
    assert relevance.score("Аккумуляторы электромобилей: обзор", terms) > 0.5


def test_relevance_is_bilingual():
    """Поиск идёт на двух языках: англоязычная статья по теме не должна
    получать ноль только потому, что запрос был русским."""
    from app.search import relevance

    sets = relevance.build_term_sets(
        "Оцени перспективы твердотельных аккумуляторов для электромобилей",
        "solid-state batteries for electric vehicles",
    )
    assert relevance.best_score("Solid-state batteries for electric vehicles", sets) == 1.0
    assert relevance.best_score("Аккумуляторы электромобилей", sets) > 0.5
    assert relevance.best_score("Machine learning for stock market prediction", sets) == 0.0


def test_generic_queries_do_not_make_everything_relevant():
    """Формулировка из одних общих слов не должна давать всем подряд единицу."""
    from app.search import relevance

    assert relevance.build_term_sets("technology review", "market forecast") == []
    sets = relevance.build_term_sets("твердотельные аккумуляторы", "technology review")
    assert len(sets) == 1
    assert relevance.best_score("dPCR: A Technology Review", sets) == 0.0


def test_language_directive_follows_query_language():
    """Источники по технологиям почти всегда англоязычные, и без явного
    указания модель уходит на их язык: на русский запрос приходил отчёт
    целиком на английском."""
    from app.agents.base import language_directive

    assert "ПО-РУССКИ" in language_directive("Оцени перспективы аккумуляторов")
    assert "English" in language_directive("Assess solid-state battery prospects")


def test_sources_digest_stays_within_token_budget():
    """Выжимка источников - самая большая часть промпта каждого агента.
    На бесплатном тарифе (8000 токенов в минуту) двадцать источников
    по 300 символов выбирали весь минутный бюджет, и агенты вставали
    в очередь глубже собственного таймаута."""
    from app.agents.base import Agent

    sources = [
        {"title": f"Заголовок статьи номер {i}", "url": f"https://doi.org/10.1016/x{i}",
         "domain": "doi.org", "published_at": "2024-01-01", "snippet": "А" * 500}
        for i in range(30)
    ]
    digest = Agent.sources_digest(sources)

    assert digest.count("https://") == 8, "в выжимку должно попадать 8 источников"
    # ~3 символа на токен для кириллицы
    assert len(digest) // 3 < 900, f"выжимка слишком велика: {len(digest) // 3} токенов"


def test_daily_limit_switches_to_fallback_model():
    """Суточная квота у провайдера своя на каждую модель. Когда основная
    исчерпана, миссия должна продолжиться на запасной, а не встать."""
    from app.llm.openai_compat import OpenAICompatProvider

    provider = OpenAICompatProvider(
        name="test", base_url="http://localhost", api_key="x",
        model="model-a", fallback_models=["model-a", "model-b", "model-c"],
    )
    assert provider._next_model() == "model-b"   # текущая пропускается
    provider.model = "model-b"
    assert provider._next_model() == "model-c"
    assert provider._next_model() is None        # запасных больше нет


def test_daily_limit_detected_by_header_and_body():
    """Провайдер отличает минутный лимит от суточного: суточный помечен
    заголовком x-should-retry и упоминанием per day. Ожиданием он не лечится."""
    import httpx

    from app.llm.openai_compat import OpenAICompatProvider

    daily = httpx.Response(429, headers={"x-should-retry": "false"}, text="{}")
    per_minute = httpx.Response(429, headers={"retry-after": "12"}, text="rate limit on tokens per minute")
    body_daily = httpx.Response(429, text="Rate limit reached ... on tokens per day (TPD): Limit 200000")

    assert OpenAICompatProvider._is_daily_limit(daily)
    assert OpenAICompatProvider._is_daily_limit(body_daily)
    assert not OpenAICompatProvider._is_daily_limit(per_minute)
    assert OpenAICompatProvider._retry_after(per_minute) == 12.0


def test_strict_json_mode_rejection_is_recoverable():
    """Модели с рассуждением не всегда закрывают JSON в отведённом бюджете,
    и провайдер отклоняет генерацию. Свой разборщик устойчивее строгого
    режима, поэтому такой отказ должен приводить к повтору без него,
    а не к падению агента."""
    import httpx

    from app.llm.openai_compat import OpenAICompatProvider

    rejected = httpx.Response(400, text='{"error":{"code":"json_validate_failed"}}')
    other = httpx.Response(400, text='{"error":{"message":"bad model"}}')

    assert OpenAICompatProvider._json_mode_rejected(rejected, {"response_format": {}})
    # Без строгого режима такой отказ трактовать так нельзя
    assert not OpenAICompatProvider._json_mode_rejected(rejected, {})
    assert not OpenAICompatProvider._json_mode_rejected(other, {"response_format": {}})


def test_truncated_json_is_repaired():
    """Модель с исчерпанным лимитом обрывает объект на середине. Терять
    весь ответ агента из-за незакрытой скобки расточительно: так пропало
    итоговое заключение судьи, и отчёт остался без главного раздела."""
    from app.llm.base import extract_json

    truncated = (
        '{"summary": "Технология перспективна", '
        '"key_findings": ["первый вывод", "второй вывод"], '
        '"recommendations": [{"text": "пилот", "priority": "high"}], '
        '"detailed_analysis": "текст обрывается на середин'
    )
    data = extract_json(truncated)

    assert data["summary"] == "Технология перспективна"
    assert len(data["key_findings"]) == 2
    assert len(data["recommendations"]) == 1
    assert "detailed_analysis" not in data  # незавершённое поле отброшено


def test_judge_gets_larger_output_budget():
    """Судья сводит работу всех агентов, его JSON втрое длиннее прочих."""
    from app.agents.registry import REGISTRY

    assert REGISTRY["W12"].max_output_tokens >= 2500
    assert REGISTRY["W01"].max_output_tokens is None  # поисковику хватает общего


def test_cache_expires_and_counts_hits():
    """ТЗ 9 требует кэшировать внешние запросы на час. Кэш должен именно
    истекать: устаревшая выдача поиска хуже отсутствия кэша."""
    from app.cache import TTLCache, make_key

    cache = TTLCache(ttl_seconds=3600)
    key = make_key("search", "openalex", "батареи", 10)

    assert cache.get(key) is None          # промах на пустом кэше
    cache.set(key, ["результат"])
    assert cache.get(key) == ["результат"]  # попадание

    cache.ttl = 0                           # выключенный кэш ничего не хранит
    cache.set(make_key("x"), ["b"])
    assert cache.get(make_key("x")) is None

    stats = cache.stats()
    assert stats["hits"] == 1
    assert stats["misses"] == 2


def test_cache_key_is_order_independent():
    """Ключ строится по структуре запроса, а не по порядку полей в словаре."""
    from app.cache import make_key

    assert make_key({"b": 2, "a": 1}) == make_key({"a": 1, "b": 2})
    assert make_key("поиск", "запрос") != make_key("поиск", "другой запрос")


def test_reasoning_models_get_extra_token_headroom():
    """У рассуждающих моделей max_tokens - общий котёл на размышление и ответ.
    Без запаса модель додумывает до конца и обрывается, не начав отвечать:
    именно так падал агент прогноза."""
    from app.llm.openai_compat import OpenAICompatProvider

    reasoning = OpenAICompatProvider(
        name="t", base_url="http://x", api_key="k", model="openai/gpt-oss-120b")
    plain = OpenAICompatProvider(
        name="t", base_url="http://x", api_key="k", model="llama-3.3-70b")

    assert reasoning.is_reasoning_model
    assert not plain.is_reasoning_model


async def test_provider_chain_switches_on_quota_only():
    """Свободные тарифы ограничены посуточно, и квота у каждого провайдера
    своя. При её исчерпании работа должна продолжиться на следующем,
    но обычная ошибка запроса переключения вызывать не должна - иначе
    цепочка сгорит впустую на первой же опечатке в промпте."""
    from app.llm.base import LLMError, LLMMessage, LLMProvider, LLMResponse
    from app.llm.chain import ChainProvider

    class Failing(LLMProvider):
        def __init__(self, name, error):
            self.name, self.model, self.error = name, f"{name}-model", error

        async def complete(self, messages, **kwargs):
            raise LLMError(self.error)

    class Working(LLMProvider):
        name, model = "working", "working-model"

        async def complete(self, messages, **kwargs):
            return LLMResponse(text="готово", model=self.model)

    quota = ChainProvider([Failing("groq", "исчерпан суточный лимит токенов"), Working()])
    answer = await quota.complete([LLMMessage("user", "тест")])
    assert answer.text == "готово"
    assert quota.index == 1

    # Ошибка не про квоту переключения не вызывает
    other = ChainProvider([Failing("groq", "модель не найдена у провайдера"), Working()])
    try:
        await other.complete([LLMMessage("user", "тест")])
        raise AssertionError("ожидалась ошибка без переключения")
    except LLMError:
        assert other.index == 0
