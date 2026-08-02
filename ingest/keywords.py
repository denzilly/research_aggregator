import re


def parse_keywords(raw: str) -> list[str]:
    return [kw.strip() for kw in raw.split(",") if kw.strip()]


def build_pubmed_query(keywords: list[str]) -> str:
    terms = [f'{kw}[Title/Abstract]' for kw in keywords]
    return " OR ".join(terms)


def _keyword_pattern(keyword: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(keyword)}\b", re.IGNORECASE)


def matches_keywords(text: str, keywords: list[str]) -> bool:
    """Word-boundary match so e.g. 'phage' doesn't false-positive on
    'macrophage' or 'autophagy'."""
    if not text:
        return False
    return any(_keyword_pattern(kw).search(text) for kw in keywords)
