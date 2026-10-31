"""Эмбеддинги для памяти миссий (ТЗ 6.2).

По умолчанию - локальный hash-эмбеддинг: никаких ключей, детерминирован,
работает офлайн. Качество ниже нейросетевого, но для поиска похожих миссий
по ключевым словам достаточно. При наличии ключа переключается на Qwen/OpenAI.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re

import httpx

from ..config import Settings, settings as default_settings

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


class Embedder:
    dim: int = 512

    async def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


class HashEmbedder(Embedder):
    """Bag-of-words с хешированием признаков и суб-словными триграммами.

    Триграммы дают устойчивость к морфологии: «аккумулятор» и «аккумуляторов»
    попадают в пересекающиеся признаки, чего не даёт чистый bag-of-words.
    """

    def __init__(self, dim: int = 512) -> None:
        self.dim = dim

    async def embed(self, text: str) -> list[float]:
        return self.embed_sync(text)

    def embed_sync(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        tokens = _TOKEN_RE.findall((text or "").lower())
        for token in tokens:
            self._add(vector, token, 1.0)
            for i in range(len(token) - 2):
                self._add(vector, token[i : i + 3], 0.4)

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]

    def _add(self, vector: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % self.dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign * weight


class ApiEmbedder(Embedder):
    """OpenAI-совместимый эндпоинт /embeddings (Qwen DashScope, OpenAI)."""

    def __init__(self, *, base_url: str, api_key: str, model: str, dim: int, timeout: int = 60) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dim = dim
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        self._fallback = HashEmbedder(dim)

    async def embed(self, text: str) -> list[float]:
        try:
            response = await self._client.post(
                f"{self.base_url}/embeddings",
                json={"model": self.model, "input": text[:8000]},
            )
            response.raise_for_status()
            vector = response.json()["data"][0]["embedding"]
            self.dim = len(vector)
            return vector
        except Exception as exc:
            # Память - вспомогательный контур: её отказ не должен ронять миссию
            log.warning("Эмбеддинг через API не получен (%s), используется hash-фолбэк", exc)
            return self._fallback.embed_sync(text)

    async def aclose(self) -> None:
        await self._client.aclose()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_embedder: Embedder | None = None


def get_embedder(cfg: Settings | None = None) -> Embedder:
    global _embedder
    if _embedder is not None:
        return _embedder

    cfg = cfg or default_settings
    provider = (cfg.embedding_provider or "hash").lower()

    if provider == "qwen" and cfg.qwen_api_key:
        _embedder = ApiEmbedder(
            base_url=cfg.qwen_base_url, api_key=cfg.qwen_api_key,
            model="text-embedding-v3", dim=1024,
        )
    elif provider == "openai" and cfg.openai_api_key:
        _embedder = ApiEmbedder(
            base_url=cfg.openai_base_url, api_key=cfg.openai_api_key,
            model="text-embedding-3-small", dim=1536,
        )
    else:
        if provider not in ("hash", ""):
            log.warning("EMBEDDING_PROVIDER=%s без ключа - используется hash", provider)
        _embedder = HashEmbedder(cfg.embedding_dim)
    return _embedder


async def close_embedder() -> None:
    global _embedder
    if _embedder is not None:
        await _embedder.aclose()
        _embedder = None
