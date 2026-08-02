import time

import requests

from ingest.keywords import matches_keywords

DETAILS_URL = "https://api.biorxiv.org/details/{server}/{since}/{until}/{cursor}"
REQUEST_DELAY = 0.2

# bioRxiv/medRxiv have no free-text keyword search, but /details does accept
# a `category` querystring filter. Pre-filtering to the subject areas
# bacteriophage research actually lands in avoids pulling every paper across
# every subject each day. Adjust if her keyword list grows into new areas.
SERVER_CATEGORIES = {
    "biorxiv": ["microbiology", "immunology", "evolutionary biology", "genomics"],
    "medrxiv": ["infectious diseases"],
}


def _fetch_server_category(server: str, category: str, since_date: str, until_date: str) -> list[dict]:
    items = []
    cursor = 0
    while True:
        url = DETAILS_URL.format(server=server, since=since_date, until=until_date, cursor=cursor)
        resp = requests.get(url, params={"category": category}, timeout=30)
        resp.raise_for_status()
        time.sleep(REQUEST_DELAY)

        data = resp.json()
        messages = data.get("messages") or [{}]
        message = messages[0]
        if message.get("status") != "ok":
            break

        collection = data.get("collection", [])
        items.extend(collection)

        count = int(message.get("count", 0))
        total = int(message.get("total", 0))
        cursor += count
        if count == 0 or cursor >= total:
            break

    return items


def fetch_new_biorxiv_papers(keywords: list[str], since_date: str, until_date: str) -> list[dict]:
    """bioRxiv/medRxiv have no free-text keyword search — pull each relevant
    subject category in the date range per server, then keyword-filter
    client-side within that (much smaller) set."""
    papers_by_doi: dict[str, dict] = {}

    for server, categories in SERVER_CATEGORIES.items():
        for category in categories:
            for item in _fetch_server_category(server, category, since_date, until_date):
                title = item.get("title", "")
                abstract = item.get("abstract", "")
                if not matches_keywords(f"{title}\n{abstract}", keywords):
                    continue

                doi = item.get("doi", "")
                if not doi or doi in papers_by_doi:
                    continue

                version = item.get("version", "1")
                papers_by_doi[doi] = {
                    "id": f"biorxiv:{doi}",
                    "source": server,
                    "title": title,
                    "authors": item.get("authors", ""),
                    "published_date": item.get("date", ""),
                    "abstract": abstract,
                    "url": f"https://www.biorxiv.org/content/{doi}v{version}",
                }

    return list(papers_by_doi.values())
