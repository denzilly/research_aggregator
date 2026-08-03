import sqlite3
from datetime import datetime, timedelta, timezone

import db


def get_latest_run(conn=None):
    conn = conn or db.get_connection()
    return conn.execute(
        "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()


DIGEST_WINDOW_DAYS = 7


def get_digest_papers(conn=None):
    """Papers ingested in the last DIGEST_WINDOW_DAYS, relevance DESC.

    Deliberately a rolling window rather than "papers from the single most
    recent run" — steady-state runs often find 0 new papers on a given day
    (small subfield, daily cadence), so scoping strictly to the latest run
    would render an empty digest most of the time even with plenty of
    recent papers to show."""
    conn = conn or db.get_connection()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=DIGEST_WINDOW_DAYS)).isoformat()
    return conn.execute(
        """
        SELECT * FROM papers
        WHERE ingested_at >= ?
        ORDER BY relevance_score DESC NULLS LAST, published_date DESC
        """,
        (cutoff,),
    ).fetchall()


def search_papers(query="", source="", since="", until="", folder_id=None, conn=None):
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


def create_folder(name: str, conn=None):
    """Creates a folder and returns the new row (with paper_count=0), or None if the name is taken."""
    conn = conn or db.get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO folders (name, created_at) VALUES (?, datetime('now'))", (name,)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return None
    return conn.execute(
        "SELECT *, 0 AS paper_count FROM folders WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def rename_folder(folder_id: int, name: str, conn=None):
    """Returns True on success, False if the folder doesn't exist, None if the name is taken."""
    conn = conn or db.get_connection()
    try:
        cur = conn.execute("UPDATE folders SET name = ? WHERE id = ?", (name, folder_id))
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


def get_keywords_raw(conn=None) -> str:
    conn = conn or db.get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = 'keywords'").fetchone()
    return row["value"] if row else ""


def save_keywords_raw(value: str, conn=None) -> None:
    conn = conn or db.get_connection()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('keywords', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (value,),
    )
    conn.commit()
