"""Выбор LLM-провайдера по конфигурации.

Провайдер один на процесс: у httpx-клиента есть пул соединений, и создавать
его на каждый вызов агента бессмысленно дорого.
"""
from __future__ import annotations

import logging

from ..config import Settings, settings as default_settings
from .base import LLMProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatProvider

log = logging.getLogger(__name__)

_provider: LLMProvider | None = None


def build_provider(cfg: Settings | None = None) -> LLMProvider:
    """Провайдер по конфигурации, при наличии запасных - цепочка."""
    cfg = cfg or default_settings
    primary = build_single(cfg, cfg.llm_provider)

    fallbacks: list[LLMProvider] = []
    seen = {(cfg.llm_provider or "").strip().lower()}
    for name in (cfg.llm_fallback_providers or "").split(","):
        name = name.strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        candidate = build_single(cfg, name, quiet=True)
        # Провайдер без ключа возвращается заглушкой - в цепочке она бесполезна
        if candidate.name != "mock":
            fallbacks.append(candidate)

    if not fallbacks or primary.name == "mock":
        return primary

    from .chain import ChainProvider

    chain = ChainProvider([primary, *fallbacks])
    log.info("Цепочка провайдеров: %s", " -> ".join(p.name for p in chain.providers))
    return chain


def build_single(cfg: Settings, provider: str, quiet: bool = False) -> LLMProvider:
    name = (provider or "mock").strip().lower()

    common = {
        "timeout": cfg.llm_timeout,
        "temperature": cfg.llm_temperature,
        "max_tokens": cfg.llm_max_tokens,
        "tokens_per_minute": cfg.llm_tokens_per_minute,
        "max_concurrency": cfg.llm_max_concurrency,
    }

    if name == "mock":
        return MockProvider()

    if name == "deepseek":
        if not cfg.deepseek_api_key:
            return _fallback("deepseek", "DEEPSEEK_API_KEY", quiet)
        return OpenAICompatProvider(
            name="deepseek",
            base_url=cfg.deepseek_base_url,
            api_key=cfg.deepseek_api_key,
            model=cfg.deepseek_model,
            **common,
        )

    if name == "qwen":
        if not cfg.qwen_api_key:
            return _fallback("qwen", "QWEN_API_KEY", quiet)
        return OpenAICompatProvider(
            name="qwen",
            base_url=cfg.qwen_base_url,
            api_key=cfg.qwen_api_key,
            model=cfg.qwen_model,
            **common,
        )

    if name == "openai":
        if not cfg.openai_api_key:
            return _fallback("openai", "OPENAI_API_KEY", quiet)
        return OpenAICompatProvider(
            name="openai",
            base_url=cfg.openai_base_url,
            api_key=cfg.openai_api_key,
            model=cfg.openai_model,
            **common,
        )

    if name == "groq":
        if not cfg.groq_api_key:
            return _fallback("groq", "GROQ_API_KEY", quiet)
        return OpenAICompatProvider(
            name="groq",
            base_url=cfg.groq_base_url,
            api_key=cfg.groq_api_key,
            model=cfg.groq_model,
            fallback_models=[m for m in cfg.groq_fallback_models.split(",")],
            **common,
        )

    if name == "gemini":
        if not cfg.gemini_api_key:
            return _fallback("gemini", "GEMINI_API_KEY", quiet)
        return OpenAICompatProvider(
            name="gemini",
            base_url=cfg.gemini_base_url,
            api_key=cfg.gemini_api_key,
            model=cfg.gemini_model,
            **common,
        )

    if name == "openrouter":
        if not cfg.openrouter_api_key:
            return _fallback("openrouter", "OPENROUTER_API_KEY", quiet)
        return OpenAICompatProvider(
            name="openrouter",
            base_url=cfg.openrouter_base_url,
            api_key=cfg.openrouter_api_key,
            model=cfg.openrouter_model,
            **common,
        )

    if name == "ollama":
        # Локальный сервер: ключ не нужен, JSON-режим поддержан не всеми моделями.
        return OpenAICompatProvider(
            name="ollama",
            base_url=cfg.ollama_base_url,
            api_key="",
            model=cfg.ollama_model,
            supports_json_mode=False,
            **common,
        )

    if name == "anthropic":
        if not cfg.anthropic_api_key:
            return _fallback("anthropic", "ANTHROPIC_API_KEY", quiet)
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(
            api_key=cfg.anthropic_api_key,
            model=cfg.anthropic_model,
            timeout=cfg.llm_timeout,
            temperature=cfg.llm_temperature,
            max_tokens=cfg.llm_max_tokens,
        )

    log.warning("Неизвестный LLM_PROVIDER=%r, используется mock", name)
    return MockProvider()


def _fallback(provider: str, env_var: str, quiet: bool = False) -> LLMProvider:
    if quiet:
        return MockProvider()
    log.warning(
        "LLM_PROVIDER=%s, но %s не задан - система переключена на заглушку. "
        "Отчёты будут демонстрационными.",
        provider,
        env_var,
    )
    return MockProvider()


def get_provider() -> LLMProvider:
    global _provider
    if _provider is None:
        _provider = build_provider()
        log.info("LLM-провайдер: %s (%s)", _provider.name, _provider.model)
    return _provider


async def close_provider() -> None:
    global _provider
    if _provider is not None:
        await _provider.aclose()
        _provider = None
