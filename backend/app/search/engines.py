"""Движки поискового шлюза (ТЗ 4.1: Search Gateway).

Бесключевые: DuckDuckGo (HTML), arXiv, Crossref, PubMed E-utilities.
Ключевые: Google CSE, SerpAPI, Bing - включаются, если задан ключ.
Каждый движок возвращает список SearchHit и НЕ поднимает исключений наружу:
падение одного движка не должно валить весь поиск.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse

import httpx

log = logging.getLogger(__name__)


@dataclass
class SearchHit:
    url: str
    title: str = ""
    snippet: str = ""
    published_at: str | None = None
    engine: str = ""
    source_type: str = "web"  # web | publication | patent
    extra: dict = field(default_factory=dict)


class Engine:
    name = "base"
    source_type = "web"
    # Минимальный интервал между запросами к этому движку, секунды.
    # Бесплатные API живут на общих лимитах: без пауз они отвечают 429
    # или антибот-заглушкой, что мы уже проходили с DuckDuckGo.
    min_interval = 0.0

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        raise NotImplementedError


# --------------------------------------------------------------- DuckDuckGo
_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?(?:class="result__snippet"[^>]*>(?P<snippet>.*?)</a>)?',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text)).strip()


# Признаки антибот-заглушки DuckDuckGo. Её отдают с кодом 200/202, и без
# этой проверки движок молча возвращал бы пустой список, а система считала бы,
# что по запросу просто ничего не нашлось.
_DDG_CHALLENGE_MARKERS = ("anomaly-modal", "Unfortunately, bots use DuckDuckGo too")


class DuckDuckGoEngine(Engine):
    """HTML-эндпоинт DuckDuckGo: работает без ключа и без регистрации.

    Ограничение: при частых запросах включается антибот-защита. Движок делает
    одну повторную попытку с паузой, после чего честно сообщает об отказе.
    Для стабильной работы под нагрузкой нужен ключевой движок (ТЗ 12).
    """

    name = "duckduckgo"
    RETRY_DELAY = 3.0
    min_interval = 2.5

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        text = await self._fetch(client, query)
        hits: list[SearchHit] = []
        for match in _DDG_RESULT_RE.finditer(response.text):
            url = _unwrap_ddg(match.group("href"))
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=_clean(match.group("title")),
                    snippet=_clean(match.group("snippet")),
                    engine=self.name,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    async def _fetch(self, client: httpx.AsyncClient, query: str) -> str:
        for attempt in range(2):
            response = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            response.raise_for_status()
            body = response.text

            if not any(marker in body for marker in _DDG_CHALLENGE_MARKERS):
                return body

            if attempt == 0:
                log.info("DuckDuckGo включил антибот-защиту, повтор через %.0f с", self.RETRY_DELAY)
                await asyncio.sleep(self.RETRY_DELAY)

        raise RuntimeError(
            "DuckDuckGo отклонил запрос антибот-защитой. "
            "Для стабильного веб-поиска задайте ключ SerpAPI или Google CSE."
        )


def _unwrap_ddg(href: str) -> str:
    """DuckDuckGo отдаёт ссылки через редирект /l/?uddg=<url>."""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target)
    return href if href.startswith("http") else ""


# --------------------------------------------------------------------- arXiv
_ARXIV_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)


def _xml_field(block: str, tag: str) -> str:
    match = re.search(rf"<{tag}[^>]*>(.*?)</{tag}>", block, re.DOTALL)
    return _clean(match.group(1)) if match else ""


class ArxivEngine(Engine):
    name = "arxiv"
    source_type = "publication"
    min_interval = 3.0  # рекомендация arXiv API: не чаще раза в 3 секунды

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        response = await client.get(
            "http://export.arxiv.org/api/query",
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": limit,
                "sortBy": "relevance",
            },
        )
        response.raise_for_status()

        hits = []
        for block in _ARXIV_ENTRY_RE.findall(response.text):
            url = _xml_field(block, "id")
            if not url:
                continue
            hits.append(
                SearchHit(
                    url=url,
                    title=_xml_field(block, "title"),
                    snippet=_xml_field(block, "summary")[:600],
                    published_at=_xml_field(block, "published")[:10] or None,
                    engine=self.name,
                    source_type=self.source_type,
                )
            )
        return hits


# ------------------------------------------------------------------ Crossref
class CrossrefEngine(Engine):
    name = "crossref"
    source_type = "publication"
    min_interval = 1.0

    def __init__(self, mailto: str = "") -> None:
        self.mailto = mailto

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        params = {
            "query": query,
            "rows": limit,
            "select": "DOI,title,abstract,issued,URL,container-title,is-referenced-by-count",
        }
        if self.mailto:
            # Вежливый пул Crossref: с контактом лимиты заметно мягче
            params["mailto"] = self.mailto

        response = await client.get("https://api.crossref.org/works", params=params)
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])

        hits = []
        for item in items:
            doi = item.get("DOI")
            url = item.get("URL") or (f"https://doi.org/{doi}" if doi else "")
            if not url:
                continue
            issued = item.get("issued", {}).get("date-parts", [[None]])[0]
            published = "-".join(str(p) for p in issued if p) or None
            hits.append(
                SearchHit(
                    url=url,
                    title=_clean((item.get("title") or [""])[0]),
                    snippet=_clean(item.get("abstract", ""))[:600],
                    published_at=published,
                    engine=self.name,
                    source_type=self.source_type,
                    extra={
                        "doi": doi,
                        "journal": (item.get("container-title") or [""])[0],
                        "citations": item.get("is-referenced-by-count"),
                    },
                )
            )
        return hits


# -------------------------------------------------------------------- PubMed
class PubMedEngine(Engine):
    """E-utilities: esearch отдаёт PMID, esummary - метаданные. Ключ не нужен."""

    name = "pubmed"
    source_type = "publication"
    min_interval = 1.0  # NCBI без ключа: не более 3 запросов в секунду, а мы шлём два

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        found = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
            params={"db": "pubmed", "term": query, "retmax": limit, "retmode": "json"},
        )
        found.raise_for_status()
        ids = found.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        summary = await client.get(
            "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
        )
        summary.raise_for_status()
        result = summary.json().get("result", {})

        hits = []
        for pmid in ids:
            item = result.get(pmid)
            if not item:
                continue
            hits.append(
                SearchHit(
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    title=_clean(item.get("title", "")),
                    snippet=_clean(item.get("source", "")),
                    published_at=(item.get("pubdate") or "")[:10] or None,
                    engine=self.name,
                    source_type=self.source_type,
                    extra={"pmid": pmid},
                )
            )
        return hits


# ------------------------------------------------------------- Google Patents
class GooglePatentsEngine(Engine):
    """Патентный поиск бесключевым путём - через веб-поиск по patents.google.com.

    Это компромисс MVP: полноценные USPTO/Espacenet требуют ключей и
    подключаются как отдельные движки на следующем этапе.
    """

    name = "patents"
    source_type = "patent"

    def __init__(self) -> None:
        self._web = DuckDuckGoEngine()

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        hits = await self._web.search(client, f"site:patents.google.com {query}", limit)
        for hit in hits:
            hit.engine = self.name
            hit.source_type = self.source_type
        return hits


# --------------------------------------------------------- ключевые движки
class GoogleCSEEngine(Engine):
    name = "google_cse"

    def __init__(self, key: str, cx: str) -> None:
        self.key = key
        self.cx = cx

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        response = await client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": self.key, "cx": self.cx, "q": query, "num": min(limit, 10)},
        )
        response.raise_for_status()
        return [
            SearchHit(
                url=item["link"],
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                engine=self.name,
            )
            for item in response.json().get("items", [])
        ]


class SerpApiEngine(Engine):
    name = "serpapi"

    def __init__(self, key: str) -> None:
        self.key = key

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        response = await client.get(
            "https://serpapi.com/search",
            params={"api_key": self.key, "q": query, "num": limit, "engine": "google"},
        )
        response.raise_for_status()
        return [
            SearchHit(
                url=item["link"],
                title=item.get("title", ""),
                snippet=item.get("snippet", ""),
                published_at=item.get("date"),
                engine=self.name,
            )
            for item in response.json().get("organic_results", [])
        ]


class BingEngine(Engine):
    name = "bing"

    def __init__(self, key: str) -> None:
        self.key = key

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        response = await client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": limit},
            headers={"Ocp-Apim-Subscription-Key": self.key},
        )
        response.raise_for_status()
        return [
            SearchHit(
                url=item["url"],
                title=item.get("name", ""),
                snippet=item.get("snippet", ""),
                published_at=item.get("datePublished", "")[:10] or None,
                engine=self.name,
            )
            for item in response.json().get("webPages", {}).get("value", [])
        ]


# ------------------------------------------------------------------ OpenAlex
def _openalex_abstract(inverted: dict | None) -> str:
    """OpenAlex хранит аннотацию инвертированным индексом «слово -> позиции».
    Восстанавливаем читаемый текст."""
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, places in inverted.items():
        for place in places:
            positions.append((place, word))
    positions.sort()
    return " ".join(word for _, word in positions)


class OpenAlexEngine(Engine):
    """OpenAlex: около 250 млн научных работ, официальный API без ключа.

    Даёт то, чего нет у остальных бесключевых источников: число цитирований
    и издателя. И то и другое идёт в оценку авторитетности (агент W08).
    Вежливый пул запросов включается параметром mailto.
    """

    name = "openalex"
    source_type = "publication"
    min_interval = 0.5

    def __init__(self, mailto: str = "") -> None:
        self.mailto = mailto

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        params = {"search": query, "per-page": min(limit, 25)}
        if self.mailto:
            params["mailto"] = self.mailto

        response = await client.get("https://api.openalex.org/works", params=params)
        response.raise_for_status()

        hits = []
        for work in response.json().get("results", []):
            doi = (work.get("doi") or "").replace("https://doi.org/", "")
            url = work.get("doi") or (work.get("primary_location") or {}).get("landing_page_url")
            if not url:
                continue

            venue = ((work.get("primary_location") or {}).get("source") or {}).get("display_name")
            hits.append(
                SearchHit(
                    url=url,
                    title=_clean(work.get("display_name")),
                    snippet=_openalex_abstract(work.get("abstract_inverted_index"))[:600],
                    published_at=work.get("publication_date"),
                    engine=self.name,
                    source_type=self.source_type,
                    extra={
                        "doi": doi or None,
                        "journal": venue,
                        "citations": work.get("cited_by_count", 0),
                        "is_oa": (work.get("open_access") or {}).get("is_oa"),
                    },
                )
            )
        return hits


# ---------------------------------------------------------- Semantic Scholar
class SemanticScholarEngine(Engine):
    """Semantic Scholar: бесплатный API без ключа, общий пул с жёстким лимитом,
    поэтому интервал между запросами больше, чем у остальных движков."""

    name = "semanticscholar"
    source_type = "publication"
    min_interval = 3.0

    FIELDS = "title,abstract,year,url,citationCount,externalIds,venue,publicationDate"

    RETRY_DELAY = 5.0

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        # Пул общий на всех анонимных клиентов, поэтому 429 здесь - обычное
        # состояние занятости, а не отказ: со второй попытки обычно отвечает.
        for attempt in range(2):
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params={"query": query, "limit": min(limit, 20), "fields": self.FIELDS},
            )
            if response.status_code != 429:
                break
            if attempt == 0:
                log.info("Semantic Scholar занят, повтор через %.0f с", self.RETRY_DELAY)
                await asyncio.sleep(self.RETRY_DELAY)
        else:
            raise RuntimeError("Semantic Scholar: общий лимит запросов исчерпан")

        response.raise_for_status()

        hits = []
        for paper in response.json().get("data", []):
            external = paper.get("externalIds") or {}
            doi = external.get("DOI")
            url = paper.get("url") or (f"https://doi.org/{doi}" if doi else "")
            if not url:
                continue

            hits.append(
                SearchHit(
                    url=url,
                    title=_clean(paper.get("title")),
                    snippet=_clean(paper.get("abstract"))[:600],
                    published_at=paper.get("publicationDate") or (
                        str(paper["year"]) if paper.get("year") else None
                    ),
                    engine=self.name,
                    source_type=self.source_type,
                    extra={
                        "doi": doi,
                        "journal": paper.get("venue"),
                        "citations": paper.get("citationCount", 0),
                    },
                )
            )
        return hits


# ------------------------------------------------------------------- SearXNG
class SearxngEngine(Engine):
    """SearXNG — метапоисковик: сам опрашивает Google, Bing, DuckDuckGo и отдаёт
    сведённый результат в JSON.

    Смысл подключения: снимает зависимость от единственного канала общего
    веб-поиска. Публичные зеркала работают нестабильно и часто отключают JSON,
    поэтому список инстансов перебирается по очереди до первого ответившего.
    Свой инстанс в контуре заказчика убирает эту проблему совсем.
    """

    name = "searxng"
    min_interval = 2.0

    DEFAULT_INSTANCES = (
        "https://searx.be",
        "https://search.inetol.net",
        "https://priv.au",
        "https://searxng.site",
    )

    def __init__(self, instances: list[str] | None = None) -> None:
        self.instances = [i.rstrip("/") for i in (instances or self.DEFAULT_INSTANCES)]

    async def search(self, client: httpx.AsyncClient, query: str, limit: int) -> list[SearchHit]:
        errors: list[str] = []

        for instance in self.instances:
            try:
                response = await client.get(
                    f"{instance}/search",
                    params={"q": query, "format": "json", "language": "all"},
                    timeout=20.0,
                )
                if response.status_code != 200:
                    errors.append(f"{instance}: HTTP {response.status_code}")
                    continue

                results = response.json().get("results", [])
                if not results:
                    errors.append(f"{instance}: пустая выдача")
                    continue

                return [
                    SearchHit(
                        url=item["url"],
                        title=_clean(item.get("title")),
                        snippet=_clean(item.get("content"))[:600],
                        published_at=(item.get("publishedDate") or "")[:10] or None,
                        engine=self.name,
                        extra={"via": instance, "searx_engines": item.get("engines", [])},
                    )
                    for item in results[:limit]
                    if item.get("url", "").startswith("http")
                ]
            except Exception as exc:
                errors.append(f"{instance}: {type(exc).__name__}")

        raise RuntimeError("Ни одно зеркало SearXNG не ответило: " + "; ".join(errors[:4]))


async def openalex_publication_trend(
    client: httpx.AsyncClient,
    query: str,
    *,
    mailto: str = "",
    current_year: int | None = None,
) -> list[dict]:
    """Динамика публикаций по годам из OpenAlex — реальный временной ряд.

    ТЗ 5.3 требует строить тренды «на основе динамики публикаций». Просить
    такой ряд у языковой модели бессмысленно: она выдаёт правдоподобные, но
    невоспроизводимые числа. OpenAlex отдаёт фактические счётчики по годам
    одним запросом с group_by.

    Текущий год отбрасывается: он ещё не закончился, и его неполный счётчик
    выглядит как обвал тренда.
    """
    import datetime

    current_year = current_year or datetime.datetime.now(datetime.timezone.utc).year
    params = {"search": query, "group_by": "publication_year"}
    if mailto:
        params["mailto"] = mailto

    response = await client.get("https://api.openalex.org/works", params=params, timeout=30.0)
    response.raise_for_status()

    series: list[dict] = []
    for group in response.json().get("group_by", []):
        key = str(group.get("key", ""))
        if not key.isdigit():
            continue
        year = int(key)
        if year >= current_year or year < current_year - 25:
            continue
        series.append({"year": year, "value": float(group.get("count", 0)),
                       "source_url": "https://openalex.org"})

    series.sort(key=lambda point: point["year"])
    return series
