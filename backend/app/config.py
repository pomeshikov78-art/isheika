"""Конфигурация системы «Ищейка» (ТЗ 4.0, разделы 8, 9, 12)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

def _base_dir() -> Path:
    """Рабочий каталог: где искать .env и куда класть базу и отчёты.

    В собранном exe исходники распаковываются во временную папку, которая
    исчезает при выходе. Настройки и данные должны жить рядом с программой,
    поэтому лаунчер передаёт настоящий путь через SLEUTH_HOME.
    """
    override = os.environ.get("SLEUTH_HOME")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent


BASE_DIR = _base_dir()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env"), env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Ищейка"
    debug: bool = True
    secret_key: str = "dev-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    jwt_ttl_hours: int = 24  # ТЗ 8: срок жизни токена — 24 часа

    database_url: str = "sqlite+aiosqlite:///./sleuth.db"

    # --- LLM ---
    llm_provider: str = "groq"
    # Запасные провайдеры через запятую. Квота свободного тарифа своя
    # у каждого, поэтому три ключа дают втрое больший суточный запас
    llm_fallback_providers: str = "gemini,openrouter"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 1024
    llm_timeout: int = 120
    # Лимиты бесплатных тарифов. 0 - без ограничения; фактическое значение
    # система уточняет по заголовкам ответа провайдера
    llm_tokens_per_minute: int = 8000
    llm_max_concurrency: int = 2

    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_base_url: str = "https://api.deepseek.com/v1"

    qwen_api_key: str = ""
    qwen_model: str = "qwen-plus"
    qwen_base_url: str = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-opus-5"

    openai_api_key: str = ""
    openai_model: str = "gpt-4o"
    openai_base_url: str = "https://api.openai.com/v1"

    ollama_model: str = "qwen2.5:7b"
    ollama_base_url: str = "http://localhost:11434/v1"

    # --- бесплатные облачные провайдеры (регистрация без карты) ---
    # Groq: очень быстрый инференс, свободный тариф ~30 запросов/мин
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    # Суточная квота у Groq считается на каждую модель отдельно, поэтому
    # при её исчерпании работа продолжается на следующей из списка
    groq_fallback_models: str = "openai/gpt-oss-120b,openai/gpt-oss-20b"

    # Google Gemini через OpenAI-совместимый эндпоинт AI Studio
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"

    # OpenRouter: агрегатор, у моделей с суффиксом :free тариф нулевой
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-chat-v3-0324:free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # --- память ---
    embedding_provider: str = "hash"
    embedding_dim: int = 512
    memory_top_k: int = 5
    memory_min_similarity: float = 0.35

    # --- поиск ---
    search_engines: str = "openalex,crossref,arxiv,pubmed,semanticscholar,duckduckgo,patents"
    search_timeout: int = 20
    search_max_results_per_engine: int = 10
    search_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    # OpenAlex просит контакт для «вежливого пула» с приоритетной обработкой
    openalex_mailto: str = ""
    # Свой инстанс SearXNG снимает нестабильность публичных зеркал
    searxng_instances: str = ""

    google_cse_key: str = ""
    google_cse_cx: str = ""
    serpapi_key: str = ""
    bing_api_key: str = ""

    patentsview_api_key: str = ""
    epo_ops_key: str = ""
    epo_ops_secret: str = ""

    # --- лимиты (ТЗ 9) ---
    rate_limit_per_minute: int = 100
    mission_timeout: int = 1800
    agent_timeout: int = 300
    agent_max_retries: int = 3
    max_concurrent_missions: int = 50
    max_concurrent_agents: int = 8

    # ТЗ 9: кэширование результатов внешних запросов на час. 0 - выключено
    cache_ttl_seconds: int = 3600

    storage_dir: str = "./storage"

    @property
    def engines(self) -> list[str]:
        return [e.strip() for e in self.search_engines.split(",") if e.strip()]

    @property
    def storage_path(self) -> Path:
        path = Path(self.storage_dir)
        if not path.is_absolute():
            path = BASE_DIR / path
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
