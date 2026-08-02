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


def search_papers(query="", source="", since="", until="", favorites_only=False, conn=None):
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
    if favorites_only:
        sql += " AND is_favorite = 1"

    sql += " ORDER BY relevance_score DESC NULLS LAST, published_date DESC"

    return conn.execute(sql, params).fetchall()


def toggle_favorite(paper_id: str, conn=None) -> bool:
    """Flips is_favorite for a paper, returns the new value."""
    conn = conn or db.get_connection()
    row = conn.execute("SELECT is_favorite FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if row is None:
        return None
    new_value = 0 if row["is_favorite"] else 1
    conn.execute("UPDATE papers SET is_favorite = ? WHERE id = ?", (new_value, paper_id))
    conn.commit()
    return bool(new_value)


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
