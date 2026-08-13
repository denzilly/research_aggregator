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


def _get_since_date(conn, query_id) -> "datetime.date":
    """Per-query watermark — a query with no successful runs yet (brand new,
    or never completed) falls through to the initial-backfill window, giving
    each query independent history regardless of how long other queries have
    been running. run_type = 'ingest' excludes background rescoring jobs
    (ingest/backfill_scores.py) — those don't fetch anything and must never
    be mistaken for "we already fetched up to here"."""
    row = conn.execute(
        "SELECT finished_at FROM runs WHERE status = 'ok' AND query_id = ? AND run_type = 'ingest' "
        "ORDER BY finished_at DESC LIMIT 1",
        (query_id,),
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


def _insert_papers(conn, papers: list[dict]) -> None:
    """Inserts the bare paper rows only — no score/summary here anymore,
    those are scoped per-query now (see score_pending_for_query) and get
    filled in by whichever query(s) actually score this paper."""
    if not papers:
        return
    now = _now_iso()
    conn.executemany(
        """
        INSERT INTO papers (id, source, title, authors, published_date, abstract, url, ingested_at)
        VALUES (:id, :source, :title, :authors, :published_date, :abstract, :url, :ingested_at)
        """,
        [{**p, "ingested_at": now} for p in papers],
    )
    conn.commit()


def _associate_with_query(conn, paper_ids: list[str], query_id: int) -> None:
    """Links every fetched paper (new or already-existing) to this query, so
    a paper first ingested for one query is still discoverable — and, unlike
    before per-query scoring, still gets *scored* — under another query
    whose keywords also match it. This is what makes cross-query overlap
    correct: dedup (_dedupe_against_db) only decides what gets *inserted*
    into papers; this decides what gets *linked*, and runs against the full
    fetched set regardless of dedup's outcome. Newly-linked rows start with
    relevance_score NULL — score_pending_for_query is what actually scores
    them, whether that's this same run (below) or a later backfill."""
    if not paper_ids:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO paper_queries (paper_id, query_id, added_at) VALUES (?, ?, ?)",
        [(pid, query_id, _now_iso()) for pid in paper_ids],
    )
    conn.commit()


def _fetch_pending_for_query(conn, query_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT papers.id, papers.title, papers.abstract
        FROM paper_queries
        JOIN papers ON papers.id = paper_queries.paper_id
        WHERE paper_queries.query_id = ? AND paper_queries.relevance_score IS NULL
        """,
        (query_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def score_pending_for_query(conn, query_id: int) -> tuple[int, float]:
    """Scores every paper linked to this query that doesn't yet have a
    relevance_score *under this query* — whether that's a paper this run
    just inserted, or a pre-existing paper a keyword backfill just linked
    (see app/queries.py: backfill_query_matches) without scoring it. Shared
    by the normal per-query ingestion loop (below) and the standalone
    background backfill job (ingest/backfill_scores.py) so both write scores
    the exact same way. Returns (papers_scored, cost_usd)."""
    query = conn.execute(
        "SELECT keywords_raw, scoring_instructions FROM queries WHERE id = ?", (query_id,)
    ).fetchone()
    if query is None:
        return 0, 0.0
    keywords = parse_keywords(query["keywords_raw"])
    pending = _fetch_pending_for_query(conn, query_id)
    if not pending or not keywords:
        return 0, 0.0

    scores, cost = score_and_summarize(pending, keywords, query["scoring_instructions"])
    now = _now_iso()
    conn.executemany(
        """
        UPDATE paper_queries SET relevance_score = :relevance_score, summary = :summary, scored_at = :scored_at
        WHERE paper_id = :paper_id AND query_id = :query_id
        """,
        [
            {
                "paper_id": p["id"],
                "query_id": query_id,
                "relevance_score": scores[p["id"]]["relevance_score"],
                "summary": scores[p["id"]]["summary"],
                "scored_at": now,
            }
            for p in pending
        ],
    )
    conn.commit()
    return len(pending), cost


def run() -> int:
    """Loops over every defined query in one locked invocation — same
    zero-argument entrypoint as before (cron / "Run now" both still just
    call `python -m ingest.ingest`), multi-query support lives entirely in
    this loop. One query's failure doesn't abort the others; the overall
    exit code is non-zero if any query failed, so cron/log monitoring still
    surfaces problems."""
    if not _acquire_lock():
        print("Another ingestion run is already in progress — skipping.")
        return 0

    try:
        conn = db.get_connection()
    except Exception as exc:
        print(f"Ingestion failed: {exc}", file=sys.stderr)
        _release_lock()
        return 1

    try:
        query_rows = conn.execute(
            "SELECT id, name, keywords_raw FROM queries ORDER BY id"
        ).fetchall()
        if not query_rows:
            print("No queries configured — nothing to do.")
            return 0

        # Shared across every runs row this invocation writes, so
        # app/queries.py: get_latest_run_batch can group them back together
        # for the header pipeline-status pill.
        invocation_started_at = _now_iso()
        any_failed = False

        for q in query_rows:
            query_id, name = q["id"], q["name"]
            keywords = parse_keywords(q["keywords_raw"])

            cur = conn.execute(
                "INSERT INTO runs (started_at, status, query_id) VALUES (?, 'running', ?)",
                (invocation_started_at, query_id),
            )
            conn.commit()
            run_id = cur.lastrowid

            try:
                if not keywords:
                    raise RuntimeError(f"Query {name!r} has no keywords configured.")

                since_date = _get_since_date(conn, query_id)
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

                _insert_papers(conn, new_papers)
                # Link the *entire* fetched set, not just the new subset — a
                # paper this query re-discovers (already ingested by an
                # earlier query) still needs a paper_queries row, so it gets
                # scored under this query's own criteria below too.
                _associate_with_query(conn, [p["id"] for p in all_papers], query_id)
                scored_count, cost = score_pending_for_query(conn, query_id)

                conn.execute(
                    "UPDATE runs SET finished_at = ?, status = 'ok', new_papers_count = ?, cost_usd = ? WHERE id = ?",
                    (_now_iso(), len(new_papers), cost, run_id),
                )
                conn.commit()

                print(
                    f"[{name}] {len(new_papers)} new papers, {scored_count} scored "
                    f"({len(pubmed_papers)} pubmed matches, {len(biorxiv_papers)} biorxiv matches, "
                    f"since {since_date.isoformat()})."
                )

            except Exception as exc:
                conn.rollback()
                conn.execute(
                    "UPDATE runs SET finished_at = ?, status = 'error', error_message = ? WHERE id = ?",
                    (_now_iso(), str(exc), run_id),
                )
                conn.commit()
                any_failed = True
                print(f"[{name}] failed: {exc}", file=sys.stderr)
                continue

        return 1 if any_failed else 0

    finally:
        conn.close()
        _release_lock()


if __name__ == "__main__":
    sys.exit(run())
