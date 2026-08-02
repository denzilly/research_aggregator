"""Daily ingestion pipeline entrypoint. Run as: python -m ingest.ingest

Meant to be invoked on a schedule (Hermes cron) and also on-demand from the
web app's "Run now" trigger after keywords change — same code path either way.
"""
import sys
from datetime import datetime, timedelta, timezone

import config
import db
from ingest.biorxiv import fetch_new_biorxiv_papers
from ingest.keywords import parse_keywords
from ingest.pubmed import fetch_new_pubmed_papers
from ingest.scoring import score_and_summarize


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _acquire_lock() -> bool:
    config.LOCK_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = open(config.LOCK_FILE_PATH, "x")
        fd.close()
        return True
    except FileExistsError:
        return False


def _release_lock() -> None:
    config.LOCK_FILE_PATH.unlink(missing_ok=True)


def _get_keywords(conn) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = 'keywords'").fetchone()
    return parse_keywords(row["value"]) if row else []


def _get_since_date(conn) -> "datetime.date":
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE status = 'ok' ORDER BY finished_at DESC LIMIT 1"
    ).fetchone()
    if row and row["finished_at"]:
        last_finished = datetime.fromisoformat(row["finished_at"])
        return (last_finished - timedelta(days=config.OVERLAP_DAYS)).date()
    return (datetime.now(timezone.utc) - timedelta(days=config.INITIAL_BACKFILL_DAYS)).date()


def _dedupe_against_db(conn, papers: list[dict]) -> list[dict]:
    if not papers:
        return []
    ids = [p["id"] for p in papers]
    placeholders = ",".join("?" * len(ids))
    existing = {
        row["id"]
        for row in conn.execute(f"SELECT id FROM papers WHERE id IN ({placeholders})", ids)
    }
    return [p for p in papers if p["id"] not in existing]


def _insert_papers(conn, papers: list[dict], scores: dict[str, dict]) -> None:
    now = _now_iso()
    conn.executemany(
        """
        INSERT INTO papers (id, source, title, authors, published_date, abstract,
                             summary, relevance_score, url, is_favorite, ingested_at)
        VALUES (:id, :source, :title, :authors, :published_date, :abstract,
                :summary, :relevance_score, :url, 0, :ingested_at)
        """,
        [
            {
                **p,
                "summary": scores.get(p["id"], {}).get("summary"),
                "relevance_score": scores.get(p["id"], {}).get("relevance_score"),
                "ingested_at": now,
            }
            for p in papers
        ],
    )
    conn.commit()


def run() -> int:
    if not _acquire_lock():
        print("Another ingestion run is already in progress — skipping.")
        return 0

    conn = db.get_connection()
    started_at = _now_iso()
    cur = conn.execute(
        "INSERT INTO runs (started_at, status) VALUES (?, 'running')", (started_at,)
    )
    conn.commit()
    run_id = cur.lastrowid

    try:
        keywords = _get_keywords(conn)
        if not keywords:
            raise RuntimeError("No keywords configured in settings table.")

        since_date = _get_since_date(conn)
        until_date = datetime.now(timezone.utc).date()

        pubmed_papers = fetch_new_pubmed_papers(
            keywords,
            since_date.strftime("%Y/%m/%d"),
            until_date.strftime("%Y/%m/%d"),
            api_key=config.NCBI_API_KEY,
        )
        biorxiv_papers = fetch_new_biorxiv_papers(
            keywords, since_date.isoformat(), until_date.isoformat()
        )

        all_papers = pubmed_papers + biorxiv_papers
        new_papers = _dedupe_against_db(conn, all_papers)

        scores = score_and_summarize(new_papers, keywords) if new_papers else {}
        _insert_papers(conn, new_papers, scores)

        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'ok', new_papers_count = ? WHERE id = ?",
            (_now_iso(), len(new_papers), run_id),
        )
        conn.commit()

        print(
            f"Ingestion complete: {len(new_papers)} new papers "
            f"({len(pubmed_papers)} pubmed matches, {len(biorxiv_papers)} biorxiv matches, "
            f"since {since_date.isoformat()})."
        )
        return 0

    except Exception as exc:
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'error', error_message = ? WHERE id = ?",
            (_now_iso(), str(exc), run_id),
        )
        conn.commit()
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        return 1

    finally:
        conn.close()
        _release_lock()


if __name__ == "__main__":
    sys.exit(run())
