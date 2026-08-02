import time
import xml.etree.ElementTree as ET

import requests

from ingest.keywords import build_pubmed_query

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

EFETCH_CHUNK_SIZE = 200


def _rate_limit_delay(api_key: str) -> float:
    # NCBI allows 3 req/s without a key, 10 req/s with one.
    return 0.11 if api_key else 0.35


def search_pubmed(keywords: list[str], since_date: str, until_date: str, api_key: str = "") -> list[str]:
    params = {
        "db": "pubmed",
        "term": build_pubmed_query(keywords),
        "datetype": "pdat",
        "mindate": since_date,
        "maxdate": until_date,
        "retmode": "json",
        "retmax": 2000,
    }
    if api_key:
        params["api_key"] = api_key

    resp = requests.get(ESEARCH_URL, params=params, timeout=30)
    resp.raise_for_status()
    time.sleep(_rate_limit_delay(api_key))
    return resp.json().get("esearchresult", {}).get("idlist", [])


def _parse_article(article_el) -> dict | None:
    medline = article_el.find("MedlineCitation")
    if medline is None:
        return None

    pmid_el = medline.find("PMID")
    if pmid_el is None or not pmid_el.text:
        return None
    pmid = pmid_el.text

    article = medline.find("Article")
    if article is None:
        return None

    title_el = article.find("ArticleTitle")
    title = "".join(title_el.itertext()).strip() if title_el is not None else ""

    abstract_parts = []
    abstract_el = article.find("Abstract")
    if abstract_el is not None:
        for piece in abstract_el.findall("AbstractText"):
            text = "".join(piece.itertext()).strip()
            label = piece.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)
    abstract = "\n".join(abstract_parts)

    authors = []
    author_list = article.find("AuthorList")
    if author_list is not None:
        for author_el in author_list.findall("Author"):
            last = author_el.findtext("LastName")
            initials = author_el.findtext("Initials")
            collective = author_el.findtext("CollectiveName")
            if last:
                authors.append(f"{last} {initials}" if initials else last)
            elif collective:
                authors.append(collective)
    authors_str = ", ".join(authors)

    published_date = ""
    pub_date_el = article.find("Journal/JournalIssue/PubDate")
    if pub_date_el is not None:
        year = pub_date_el.findtext("Year")
        month = pub_date_el.findtext("Month", default="01")
        day = pub_date_el.findtext("Day", default="01")
        medline_date = pub_date_el.findtext("MedlineDate")
        if year:
            published_date = f"{year}-{month}-{day}"
        elif medline_date:
            published_date = medline_date

    return {
        "id": f"pubmed:{pmid}",
        "source": "pubmed",
        "title": title,
        "authors": authors_str,
        "published_date": published_date,
        "abstract": abstract,
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


def fetch_pubmed_details(pmids: list[str], api_key: str = "") -> list[dict]:
    papers = []
    for i in range(0, len(pmids), EFETCH_CHUNK_SIZE):
        chunk = pmids[i:i + EFETCH_CHUNK_SIZE]
        params = {
            "db": "pubmed",
            "id": ",".join(chunk),
            "rettype": "abstract",
            "retmode": "xml",
        }
        if api_key:
            params["api_key"] = api_key

        resp = requests.get(EFETCH_URL, params=params, timeout=60)
        resp.raise_for_status()
        time.sleep(_rate_limit_delay(api_key))

        root = ET.fromstring(resp.content)
        for article_el in root.findall("PubmedArticle"):
            parsed = _parse_article(article_el)
            if parsed:
                papers.append(parsed)

    return papers


def fetch_new_pubmed_papers(keywords: list[str], since_date: str, until_date: str, api_key: str = "") -> list[dict]:
    pmids = search_pubmed(keywords, since_date, until_date, api_key)
    if not pmids:
        return []
    return fetch_pubmed_details(pmids, api_key)
