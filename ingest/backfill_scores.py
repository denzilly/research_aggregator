"""One-off/maintenance utility: score any papers in the DB that are missing
relevance_score/summary (e.g. ingested before OPENROUTER_API_KEY was set).
Run as: python -m ingest.backfill_scores
"""
import sys

import db
from ingest.keywords import parse_keywords
from ingest.scoring import score_and_summarize


def run() -> int:
    conn = db.get_connection()
    keywords = parse_keywords(
        conn.execute("SELECT value FROM settings WHERE key = 'keywords'").fetchone()["value"]
    )
    rows = conn.execute(
        "SELECT id, title, abstract FROM papers WHERE relevance_score IS NULL"
    ).fetchall()
    papers = [dict(r) for r in rows]

    if not papers:
        print("No papers need backfilling.")
        return 0

    results = score_and_summarize(papers, keywords)
    conn.executemany(
        "UPDATE papers SET relevance_score = :relevance_score, summary = :summary WHERE id = :id",
        [{"id": p["id"], **results[p["id"]]} for p in papers],
    )
    conn.commit()

    scored = sum(1 for r in results.values() if r["relevance_score"] is not None)
    print(f"Backfilled {scored}/{len(papers)} papers.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
