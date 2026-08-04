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
    """All `runs` rows from the most recent ingestion invocation. Before
    multi-query support there was one row per invocation; now there's one
    per (invocation x query), all sharing the same started_at timestamp
    (see ingest/ingest.py) — grouping on that gives invocation-level status
    for free, with legacy single-row invocations behaving as a batch of one."""
    conn = conn or db.get_connection()
    latest = conn.execute("SELECT MAX(started_at) AS s FROM runs").fetchone()["s"]
    if latest is None:
        return []
    return conn.execute(
        "SELECT * FROM runs WHERE started_at = ? ORDER BY id", (latest,)
    ).fetchall()


DIGEST_WINDOW_DAYS = 7

# Digest time-range filter options, in display order. None means no cutoff ("all").
DIGEST_WINDOWS = {"day": 1, "week": DIGEST_WINDOW_DAYS, "month": 30, "all": None}
DEFAULT_DIGEST_WINDOW = "week"


def get_digest_papers(conn=None, window_days=DIGEST_WINDOW_DAYS, query_id=None):
    """Papers ingested in the last window_days (None = no cutoff), relevance DESC.

    Deliberately a rolling window rather than "papers from the single most
    recent run" — steady-state runs often find 0 new papers on a given day
    (small subfield, daily cadence), so scoping strictly to the latest run
    would render an empty digest most of the time even with plenty of
    recent papers to show."""
    conn = conn or db.get_connection()
    sql = "SELECT * FROM papers WHERE 1=1"
    params = []
    if window_days is not None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql += " AND ingested_at >= ?"
        params.append(cutoff)
    if query_id:
        sql += " AND papers.id IN (SELECT paper_id FROM paper_queries WHERE query_id = ?)"
        params.append(query_id)
    sql += " ORDER BY relevance_score DESC NULLS LAST, published_date DESC"
    return conn.execute(sql, params).fetchall()


def search_papers(query="", source="", since="", until="", folder_id=None, query_id=None, conn=None):
    conn = conn or db.get_connection()

    if query.strip():
        sql = """
            SELECT papers.* FROM papers
            JOIN papers_fts ON papers.rowid = papers_fts.rowid
            WHERE papers_fts MATCH ?
        """
        params = [query.strip()]
    else:
        sql = "SELECT * FROM papers WHERE 1=1"
        params = []

    if source:
        sql += " AND source = ?"
        params.append(source)
    if since:
        sql += " AND published_date >= ?"
        params.append(since)
    if until:
        sql += " AND published_date <= ?"
        params.append(until)
    if folder_id:
        sql += " AND papers.id IN (SELECT paper_id FROM paper_folders WHERE folder_id = ?)"
        params.append(folder_id)
    if query_id:
        sql += " AND papers.id IN (SELECT paper_id FROM paper_queries WHERE query_id = ?)"
        params.append(query_id)

    sql += " ORDER BY relevance_score DESC NULLS LAST, published_date DESC"

    return conn.execute(sql, params).fetchall()


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


def create_query(name: str, keywords_raw: str, conn=None):
    """Returns the new row, None if the name is taken, or "max_reached" if
    MAX_QUERIES already exist."""
    conn = conn or db.get_connection()
    if count_queries(conn) >= MAX_QUERIES:
        return "max_reached"
    try:
        cur = conn.execute(
            "INSERT INTO queries (name, keywords_raw, created_at) VALUES (?, ?, datetime('now'))",
            (name, keywords_raw),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return conn.execute("SELECT * FROM queries WHERE id = ?", (cur.lastrowid,)).fetchone()


def update_query(query_id: int, name: str, keywords_raw: str, conn=None):
    """Returns True on success, False if the query doesn't exist, None if the
    new name is taken by a different query."""
    conn = conn or db.get_connection()
    try:
        cur = conn.execute(
            "UPDATE queries SET name = ?, keywords_raw = ? WHERE id = ?",
            (name, keywords_raw, query_id),
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
    """Scans every already-ingested paper for a keyword match and associates
    it with this query immediately, so a newly-created query isn't empty
    until the next ingestion run. Pure regex match, same function bioRxiv
    filtering already uses (ingest/keywords.py) — no LLM/API cost, and no
    re-scoring: a matched paper keeps whichever relevance_score/summary it
    already has. Returns the number of papers associated."""
    conn = conn or db.get_connection()
    rows = conn.execute("SELECT id, title, abstract FROM papers").fetchall()
    matches = [
        row["id"] for row in rows
        if matches_keywords(f"{row['title']}\n{row['abstract'] or ''}", keywords)
    ]
    associate_papers_with_query(matches, query_id, conn)
    return len(matches)
