import sqlite3
from datetime import datetime, timedelta, timezone

import db
from ingest.keywords import matches_keywords

# Fixed palette for folder color swatches — kept in sync with FOLDER_COLORS in
# app/static/js/app.js (used to render dots for folders created client-side)
# and validated against server-side in routes.py so `folders.color` can only
# ever hold one of these values or NULL.
FOLDER_COLORS = ["#60a5fa", "#4ade80", "#fbbf24", "#f472b6", "#a78bfa", "#f87171", "#38bdf8", "#94a3b8"]


def get_latest_run_batch(conn=None):
    """All `runs` rows from the most recent *ingestion* invocation (the
    header pipeline-status pill's "Last run: ... N new" is specifically
    about fetching, so a background backfill job — see
    ingest/backfill_scores.py, run_type = 'backfill' — must never shadow it
    here even if it ran more recently). Before multi-query support there was
    one row per invocation; now there's one per (invocation x query), all
    sharing the same started_at timestamp (see ingest/ingest.py) — grouping
    on that gives invocation-level status for free, with legacy single-row
    invocations behaving as a batch of one."""
    conn = conn or db.get_connection()
    latest = conn.execute(
        "SELECT MAX(started_at) AS s FROM runs WHERE run_type = 'ingest'"
    ).fetchone()["s"]
    if latest is None:
        return []
    return conn.execute(
        "SELECT * FROM runs WHERE started_at = ? AND run_type = 'ingest' ORDER BY id", (latest,)
    ).fetchall()


RUN_LOG_LIMIT = 50


def list_recent_runs(limit=RUN_LOG_LIMIT, conn=None):
    """Most recent completed-or-failed runs (one per query per invocation,
    ingestion and backfill jobs interleaved by recency), newest first, for
    the Settings page run log. `query_name` is NULL for a run whose query
    has since been deleted (runs.query_id is ON DELETE SET NULL — a
    historical log entry, not a pure join row) — the template labels those
    "Deleted query" rather than dropping them."""
    conn = conn or db.get_connection()
    return conn.execute(
        """
        SELECT runs.id, runs.started_at, runs.finished_at, runs.status, runs.run_type,
               runs.new_papers_count, runs.error_message, runs.cost_usd,
               queries.name AS query_name
        FROM runs
        LEFT JOIN queries ON queries.id = runs.query_id
        WHERE runs.status != 'running'
        ORDER BY runs.started_at DESC, runs.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()


DIGEST_WINDOW_DAYS = 7

# Digest time-range filter options, in display order. None means no cutoff ("all").
DIGEST_WINDOWS = {"day": 1, "week": DIGEST_WINDOW_DAYS, "month": 30, "all": None}
DEFAULT_DIGEST_WINDOW = "week"

# papers.* minus relevance_score/summary, which are DEPRECATED there (see
# db/schema.sql) — scoring now lives on paper_queries, scoped per query.
# Selected explicitly rather than `papers.*` so the real, aliased
# relevance_score/summary columns from _score_join() below don't collide
# with these deprecated same-named ones: sqlite3.Row resolves a name to
# whichever matching column came *first* in the result set, so if the
# deprecated papers.relevance_score were included, `row["relevance_score"]`
# (and Jinja's `paper.relevance_score`) would silently read stale/NULL data
# instead of the per-query score.
_PAPER_COLUMNS = (
    "papers.id, papers.source, papers.title, papers.authors, papers.published_date, "
    "papers.abstract, papers.url, papers.ingested_at, papers.read_at"
)


def _score_join(query_id):
    """Returns (join_sql, select_cols_sql, join_params) providing each
    paper's relevance_score/summary/score_query_name:

    - query_id given: that query's own score for the paper (an INNER JOIN,
      which doubles as the "paper belongs to this query" filter — replaces
      the old `papers.id IN (SELECT ... WHERE query_id = ?)` clause).
      score_query_name is always NULL here — redundant to label a score
      with the query the caller already knows is active.
    - query_id is None ("All queries" / unfiltered search): each paper's
      single *highest* score across every query it matches (decision:
      option 2 in the scoring-instructions discussion), via a
      ROW_NUMBER() window per paper. score_query_name names which query
      that best score came from, so the UI can label it."""
    if query_id:
        return (
            "JOIN paper_queries ON paper_queries.paper_id = papers.id AND paper_queries.query_id = ?",
            "paper_queries.relevance_score AS relevance_score, "
            "paper_queries.summary AS summary, "
            "NULL AS score_query_name",
            [query_id],
        )
    return (
        """
        LEFT JOIN (
            SELECT pq.paper_id, pq.relevance_score, pq.summary, q.name AS query_name,
                   ROW_NUMBER() OVER (
                     PARTITION BY pq.paper_id ORDER BY pq.relevance_score DESC NULLS LAST
                   ) AS rn
            FROM paper_queries pq
            JOIN queries q ON q.id = pq.query_id
        ) best ON best.paper_id = papers.id AND best.rn = 1
        """,
        "best.relevance_score AS relevance_score, best.summary AS summary, best.query_name AS score_query_name",
        [],
    )


def get_digest_papers(conn=None, window_days=DIGEST_WINDOW_DAYS, query_id=None, unread_only=False):
    """Papers ingested in the last window_days (None = no cutoff), relevance DESC.

    Deliberately a rolling window rather than "papers from the single most
    recent run" — steady-state runs often find 0 new papers on a given day
    (small subfield, daily cadence), so scoping strictly to the latest run
    would render an empty digest most of the time even with plenty of
    recent papers to show."""
    conn = conn or db.get_connection()
    join_sql, select_cols, params = _score_join(query_id)
    sql = f"SELECT {_PAPER_COLUMNS}, {select_cols} FROM papers {join_sql} WHERE 1=1"
    if window_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql += " AND papers.ingested_at >= ?"
        params.append(cutoff)
    if unread_only:
        sql += " AND papers.read_at IS NULL"
    sql += " ORDER BY relevance_score DESC NULLS LAST, papers.published_date DESC"
    return conn.execute(sql, params).fetchall()


def search_papers(query="", source="", since="", until="", folder_id=None, query_id=None, conn=None):
    conn = conn or db.get_connection()
    join_sql, select_cols, params = _score_join(query_id)

    if query.strip():
        sql = f"""
            SELECT {_PAPER_COLUMNS}, {select_cols} FROM papers
            JOIN papers_fts ON papers.rowid = papers_fts.rowid
            {join_sql}
            WHERE papers_fts MATCH ?
        """
        params = params + [query.strip()]
    else:
        sql = f"SELECT {_PAPER_COLUMNS}, {select_cols} FROM papers {join_sql} WHERE 1=1"

    if source:
        sql += " AND papers.source = ?"
        params.append(source)
    if since:
        sql += " AND papers.published_date >= ?"
        params.append(since)
    if until:
        sql += " AND papers.published_date <= ?"
        params.append(until)
    if folder_id:
        sql += " AND papers.id IN (SELECT paper_id FROM paper_folders WHERE folder_id = ?)"
        params.append(folder_id)

    sql += " ORDER BY relevance_score DESC NULLS LAST, papers.published_date DESC"

    return conn.execute(sql, params).fetchall()


def mark_paper_read(paper_id: str, conn=None) -> bool:
    """Idempotent — re-marking an already-read paper just refreshes read_at.
    Returns False if paper_id doesn't exist."""
    conn = conn or db.get_connection()
    cur = conn.execute("UPDATE papers SET read_at = datetime('now') WHERE id = ?", (paper_id,))
    conn.commit()
    return cur.rowcount > 0


def mark_paper_unread(paper_id: str, conn=None) -> bool:
    conn = conn or db.get_connection()
    cur = conn.execute("UPDATE papers SET read_at = NULL WHERE id = ?", (paper_id,))
    conn.commit()
    return cur.rowcount > 0


def list_folders(conn=None):
    """All folders with paper counts. 'Favorites' (if present) sorts first, then alphabetical."""
    conn = conn or db.get_connection()
    return conn.execute("""
        SELECT folders.*, COUNT(paper_folders.paper_id) AS paper_count
        FROM folders
        LEFT JOIN paper_folders ON paper_folders.folder_id = folders.id
        GROUP BY folders.id
        ORDER BY (folders.name != 'Favorites'), folders.name COLLATE NOCASE
    """).fetchall()


def get_folder(folder_id, conn=None):
    conn = conn or db.get_connection()
    return conn.execute("SELECT * FROM folders WHERE id = ?", (folder_id,)).fetchone()


def create_folder(name: str, color: str | None = None, conn=None):
    """Creates a folder and returns the new row (with paper_count=0), or None if the name is taken."""
    conn = conn or db.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO folders (name, color, created_at) VALUES (?, ?, datetime('now'))", (name, color)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return conn.execute(
        "SELECT *, 0 AS paper_count FROM folders WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def update_folder(folder_id: int, name: str | None = None, color: str | None = None, conn=None):
    """Partial update — only the fields passed (non-None) are changed. Returns
    True on success, False if the folder doesn't exist, None if the new name
    is already taken by another folder."""
    conn = conn or db.get_connection()
    sets, params = [], []
    if name is not None:
        sets.append("name = ?")
        params.append(name)
    if color is not None:
        sets.append("color = ?")
        params.append(color)
    if not sets:
        return True
    params.append(folder_id)
    try:
        cur = conn.execute(f"UPDATE folders SET {', '.join(sets)} WHERE id = ?", params)
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return cur.rowcount > 0


def delete_folder(folder_id: int, conn=None) -> bool:
    """Deletes the folder; paper_folders rows cascade, papers themselves are untouched."""
    conn = conn or db.get_connection()
    cur = conn.execute("DELETE FROM folders WHERE id = ?", (folder_id,))
    conn.commit()
    return cur.rowcount > 0


def get_folder_ids_for_papers(paper_ids: list[str], conn=None) -> dict[str, list[int]]:
    """Bulk folder-membership lookup for a page of papers, to avoid N+1 queries per card."""
    conn = conn or db.get_connection()
    if not paper_ids:
        return {}
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT paper_id, folder_id FROM paper_folders WHERE paper_id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    result: dict[str, list[int]] = {}
    for row in rows:
        result.setdefault(row["paper_id"], []).append(row["folder_id"])
    return result


def get_query_ids_for_papers(paper_ids: list[str], conn=None) -> dict[str, list[int]]:
    """Bulk query-membership lookup for a page of papers — lets the client
    know which sidebar unread badges to decrement/increment when a paper's
    read state is toggled, without a round trip."""
    conn = conn or db.get_connection()
    if not paper_ids:
        return {}
    placeholders = ",".join("?" * len(paper_ids))
    rows = conn.execute(
        f"SELECT paper_id, query_id FROM paper_queries WHERE paper_id IN ({placeholders})",
        paper_ids,
    ).fetchall()
    result: dict[str, list[int]] = {}
    for row in rows:
        result.setdefault(row["paper_id"], []).append(row["query_id"])
    return result


def add_paper_to_folder(paper_id: str, folder_id: int, conn=None) -> bool:
    """Idempotent add. Returns False if paper_id or folder_id don't exist."""
    conn = conn or db.get_connection()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO paper_folders (paper_id, folder_id, added_at) "
            "VALUES (?, ?, datetime('now'))",
            (paper_id, folder_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return False
    return True


def remove_paper_from_folder(paper_id: str, folder_id: int, conn=None) -> None:
    conn = conn or db.get_connection()
    conn.execute(
        "DELETE FROM paper_folders WHERE paper_id = ? AND folder_id = ?", (paper_id, folder_id)
    )
    conn.commit()


MAX_QUERIES = 5


def list_queries(conn=None):
    conn = conn or db.get_connection()
    return conn.execute("SELECT * FROM queries ORDER BY id").fetchall()


def get_query(query_id, conn=None):
    conn = conn or db.get_connection()
    return conn.execute("SELECT * FROM queries WHERE id = ?", (query_id,)).fetchone()


def count_queries(conn=None) -> int:
    conn = conn or db.get_connection()
    return conn.execute("SELECT COUNT(*) AS n FROM queries").fetchone()["n"]


def create_query(name: str, keywords_raw: str, scoring_instructions: str | None = None, conn=None):
    """Returns the new row, None if the name is taken, or "max_reached" if
    MAX_QUERIES already exist."""
    conn = conn or db.get_connection()
    if count_queries(conn) >= MAX_QUERIES:
        return "max_reached"
    try:
        cur = conn.execute(
            "INSERT INTO queries (name, keywords_raw, scoring_instructions, created_at) "
            "VALUES (?, ?, ?, datetime('now'))",
            (name, keywords_raw, scoring_instructions),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return conn.execute("SELECT * FROM queries WHERE id = ?", (cur.lastrowid,)).fetchone()


def update_query(query_id: int, name: str, keywords_raw: str, scoring_instructions: str | None = None, conn=None):
    """Returns True on success, False if the query doesn't exist, None if the
    new name is taken by a different query. Deliberately doesn't retroactively
    rescore anything already scored under this query, even though a
    keywords/scoring_instructions edit can change what "should" happen to
    existing scores — same trade-off backfill_query_matches already makes for
    keyword edits, kept consistent, and avoids surprise LLM spend on every
    save. Run `python -m ingest.backfill_scores <query_id>` manually to
    refresh (it only rescores NULL links, so first null out the ones you
    want redone)."""
    conn = conn or db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE queries SET name = ?, keywords_raw = ?, scoring_instructions = ? WHERE id = ?",
            (name, keywords_raw, scoring_instructions, query_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return cur.rowcount > 0


def delete_query(query_id: int, conn=None) -> bool:
    """Deletes the query; paper_queries rows cascade, papers themselves are untouched."""
    conn = conn or db.get_connection()
    cur = conn.execute("DELETE FROM queries WHERE id = ?", (query_id,))
    conn.commit()
    return cur.rowcount > 0


def associate_papers_with_query(paper_ids: list[str], query_id: int, conn=None) -> None:
    conn = conn or db.get_connection()
    if not paper_ids:
        return
    conn.executemany(
        "INSERT OR IGNORE INTO paper_queries (paper_id, query_id, added_at) "
        "VALUES (?, ?, datetime('now'))",
        [(pid, query_id) for pid in paper_ids],
    )
    conn.commit()


def backfill_query_matches(query_id: int, keywords: list[str], conn=None) -> int:
    """Scans every already-ingested paper for a keyword match and links it to
    this query immediately, so a newly-created query isn't empty until the
    next ingestion run. Pure regex match, same function bioRxiv filtering
    already uses (ingest/keywords.py) — no LLM/API cost here, and each new
    link starts with relevance_score NULL (unscored under this query, even
    if the paper already has a score under some other query it also
    matches). The caller (routes.py: create_query) is responsible for
    kicking off the actual scoring — a background `ingest.backfill_scores`
    subprocess, same pattern as the "Run now" trigger — since that costs
    real LLM calls and shouldn't block this request. Returns the number of
    papers linked."""
    conn = conn or db.get_connection()
    rows = conn.execute("SELECT id, title, abstract FROM papers").fetchall()
    matches = [
        row["id"] for row in rows
        if matches_keywords(f"{row['title']}\n{row['abstract'] or ''}", keywords)
    ]
    associate_papers_with_query(matches, query_id, conn)
    return len(matches)


def get_unread_counts(conn=None) -> dict[int, int]:
    """All-time unread paper count per query, for the small badge next to each
    query in the sidebar — deliberately ignores the digest window/read-filter
    that's currently applied, since it's meant to answer "how much is waiting
    across the whole archive", not "how much is on screen right now"."""
    conn = conn or db.get_connection()
    rows = conn.execute("""
        SELECT paper_queries.query_id AS query_id, COUNT(*) AS n
        FROM paper_queries
        JOIN papers ON papers.id = paper_queries.paper_id
        WHERE papers.read_at IS NULL
        GROUP BY paper_queries.query_id
    """).fetchall()
    return {row["query_id"]: row["n"] for row in rows}
