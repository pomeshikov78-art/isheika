"""Оценка авторитетности источника (ТЗ 5.5, агент W08).

Базовая оценка выводится из класса домена: рецензируемые издательства и
патентные ведомства весят больше, чем блоги и агрегаторы контента. Дальше
W08 корректирует её по дате и наличию DOI.
"""
from __future__ import annotations

from urllib.parse import urlparse

# Точные домены с известной репутацией
_DOMAIN_SCORES: dict[str, float] = {
    "nature.com": 0.97,
    "science.org": 0.97,
    "sciencedirect.com": 0.92,
    "springer.com": 0.9,
    "link.springer.com": 0.9,
    "ieee.org": 0.94,
    "ieeexplore.ieee.org": 0.94,
    "acm.org": 0.92,
    "pubmed.ncbi.nlm.nih.gov": 0.93,
    "ncbi.nlm.nih.gov": 0.92,
    "arxiv.org": 0.70,  # препринт: рецензирования не проходил
    "doi.org": 0.72,  # резолвер, издатель неизвестен
    "crossref.org": 0.75,
    "semanticscholar.org": 0.82,
    "patents.google.com": 0.88,
    "uspto.gov": 0.95,
    "epo.org": 0.95,
    "worldwide.espacenet.com": 0.93,
    "fips.ru": 0.9,
    "wipo.int": 0.93,
    "iea.org": 0.9,
    "oecd.org": 0.9,
    "worldbank.org": 0.9,
    "statista.com": 0.72,
    "mckinsey.com": 0.78,
    "bloomberg.com": 0.78,
    "reuters.com": 0.8,
    "ft.com": 0.78,
    "economist.com": 0.78,
    "nikkei.com": 0.76,
    "wikipedia.org": 0.6,
    "medium.com": 0.35,
    "substack.com": 0.35,
    "reddit.com": 0.25,
    "quora.com": 0.2,
    "youtube.com": 0.25,
    "blogspot.com": 0.2,
    "wordpress.com": 0.2,
}

# Классы доменов по суффиксу
_SUFFIX_SCORES: list[tuple[str, float]] = [
    (".gov", 0.9),
    (".gov.uk", 0.9),
    (".edu", 0.85),
    (".ac.uk", 0.85),
    (".int", 0.85),
    (".mil", 0.85),
    (".org", 0.62),
    (".ru", 0.5),
    (".com", 0.5),
    (".io", 0.45),
]

DEFAULT_SCORE = 0.5


def domain_of(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def domain_authority(url: str) -> float:
    """Оценка 0..1. Неизвестный домен получает нейтральные 0.5, а не ноль:
    отсутствие домена в списке - это незнание, а не признак недостоверности."""
    domain = domain_of(url)
    if not domain:
        return 0.3

    if domain in _DOMAIN_SCORES:
        return _DOMAIN_SCORES[domain]

    # Поддомен известного домена наследует его оценку с небольшой скидкой
    for known, score in _DOMAIN_SCORES.items():
        if domain.endswith("." + known):
            return round(score * 0.95, 3)

    for suffix, score in _SUFFIX_SCORES:
        if domain.endswith(suffix):
            return score

    return DEFAULT_SCORE


# Префикс DOI однозначно определяет издателя, а ссылка вида
# https://doi.org/10.1038/... этого не показывает: домен резолвера одинаков
# и для Nature, и для хищного журнала. Без этой таблицы статья в Nature
# получала ту же базовую оценку, что и любой другой DOI.
_DOI_PREFIX_SCORES: dict[str, float] = {
    "10.1038": 0.96,  # Nature Portfolio
    "10.1126": 0.96,  # Science / AAAS
    "10.1073": 0.94,  # PNAS
    "10.1109": 0.93,  # IEEE
    "10.1021": 0.92,  # American Chemical Society
    "10.1039": 0.91,  # Royal Society of Chemistry
    "10.1016": 0.90,  # Elsevier
    "10.1007": 0.89,  # Springer
    "10.1002": 0.88,  # Wiley
    "10.1088": 0.88,  # IOP Publishing
    "10.1103": 0.92,  # American Physical Society
    "10.1145": 0.90,  # ACM
    "10.3390": 0.72,  # MDPI - качество сильно разнится по журналам
    "10.1371": 0.88,  # PLOS
    "10.1093": 0.88,  # Oxford University Press
    "10.1017": 0.88,  # Cambridge University Press
}

# Домены-резолверы: сами по себе ничего не говорят о качестве публикации
RESOLVER_DOMAINS = {"doi.org", "dx.doi.org", "crossref.org", "semanticscholar.org"}

# Архивы препринтов: публикация есть, рецензирования нет. Надбавку за
# рецензируемость такие источники получать не должны - иначе свежий препринт
# обгоняет статью в журнале только за счёт даты.
PREPRINT_DOMAINS = {"arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com",
                    "chemrxiv.org", "preprints.org", "osf.io", "techrxiv.org"}


def is_preprint(url: str) -> bool:
    domain = domain_of(url)
    return any(domain == d or domain.endswith("." + d) for d in PREPRINT_DOMAINS)


def publisher_authority(doi: str | None) -> float | None:
    """Оценка издателя по префиксу DOI. None - издатель неизвестен."""
    if not doi:
        return None
    prefix = str(doi).strip().lstrip("https://doi.org/").split("/")[0]
    return _DOI_PREFIX_SCORES.get(prefix)


def effective_authority(url: str, doi: str | None = None) -> tuple[float, str]:
    """Базовая авторитетность с учётом издателя. Возвращает оценку и пояснение."""
    domain = domain_of(url)
    base = domain_authority(url)

    if domain in RESOLVER_DOMAINS:
        publisher = publisher_authority(doi)
        if publisher is not None:
            return publisher, f"издатель по DOI {str(doi).split('/')[0]}: {publisher:.2f}"
        return base, f"резолвер {domain}, издатель неизвестен: {base:.2f}"

    return base, f"авторитетность домена {base:.2f}"
