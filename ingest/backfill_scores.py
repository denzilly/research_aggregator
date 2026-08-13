"""Maintenance/backfill utility: scores every paper_queries link that's
missing a relevance_score/summary under its query — new queries' overlap
with the existing archive (triggered automatically as a background
subprocess by app/routes.py: create_query, mirroring "Run now"), or, run
with no argument, every query at once as a general catch-up sweep (e.g.
after editing a query's scoring_instructions and wanting existing scores
refreshed, or recovering from a run that errored before scoring finished).

Logs to `runs` as run_type = 'backfill' so cost is still visible in the
Settings run log, but ingest/ingest.py's "since when do we fetch" watermark
(run_type = 'ingest') never sees these rows — a backfill fetches nothing.

Run as: python -m ingest.backfill_scores [query_id]
"""
import sys
from datetime import datetime, timezone

import db
from ingest.ingest import score_pending_for_query


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _score_one_query(conn, query_id: int) -> tuple[int, float]:
    run_id = conn.execute(
        "INSERT INTO runs (started_at, status, query_id, run_type) VALUES (?, 'running', ?, 'backfill')",
        (_now_iso(), query_id),
    ).lastrowid
    conn.commit()
    try:
        scored, cost = score_pending_for_query(conn, query_id)
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'ok', new_papers_count = ?, cost_usd = ? WHERE id = ?",
            (_now_iso(), scored, cost, run_id),
        )
        conn.commit()
        return scored, cost
    except Exception as exc:
        conn.rollback()
        conn.execute(
            "UPDATE runs SET finished_at = ?, status = 'error', error_message = ? WHERE id = ?",
            (_now_iso(), str(exc), run_id),
        )
        conn.commit()
        raise


def run(query_id: int | None = None) -> int:
    conn = db.get_connection()
    try:
        if query_id is not None:
            query_ids = [query_id]
        else:
            query_ids = [row["id"] for row in conn.execute("SELECT id FROM queries ORDER BY id")]

        if not query_ids:
            print("No queries configured — nothing to score.")
            return 0

        total_scored, total_cost, any_failed = 0, 0.0, False
        for qid in query_ids:
            try:
                scored, cost = _score_one_query(conn, qid)
            except Exception as exc:
                any_failed = True
                print(f"[query {qid}] backfill failed: {exc}", file=sys.stderr)
                continue
            total_scored += scored
            total_cost += cost
            if scored:
                print(f"[query {qid}] scored {scored} papers (${cost:.4f}).")

        print(
            f"Done — {total_scored} papers scored across "
            f"{len(query_ids)} quer{'y' if len(query_ids) == 1 else 'ies'} (${total_cost:.4f})."
        )
        return 1 if any_failed else 0
    finally:
        conn.close()


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    sys.exit(run(int(arg) if arg else None))
