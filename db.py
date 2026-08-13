import sqlite3

import config


def get_connection():
    config.DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _add_column_if_missing(conn, table, column, coldef):
    """SQLite has no `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, so schema.sql
    can't express this idempotently on its own — do the existence check here
    instead, so init_db() stays safe to re-run against an already-migrated DB."""
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")


def _migrate_scores_to_paper_queries(conn):
    """One-time copy of the old paper-level relevance_score/summary into the
    new per-(paper, query) columns on paper_queries, now that scoring is
    scoped to a query's own keywords/scoring_instructions rather than shared
    across every query a paper happens to match (see db/schema.sql). Copies
    into every query a paper is currently linked to — an approximation
    (those scores were computed under whichever query originally ingested
    the paper, not necessarily the query they're being copied to) but a far
    better starting point than leaving every link unscored. Guarded by
    `relevance_score IS NULL` so it's a no-op once a link has a real score,
    whether from this copy or from an actual scoring pass — safe to re-run
    on every init_db() call."""
    conn.execute("""
        UPDATE paper_queries
        SET relevance_score = (SELECT relevance_score FROM papers WHERE papers.id = paper_queries.paper_id),
            summary = (SELECT summary FROM papers WHERE papers.id = paper_queries.paper_id),
            scored_at = (SELECT ingested_at FROM papers WHERE papers.id = paper_queries.paper_id)
        WHERE relevance_score IS NULL
          AND EXISTS (
            SELECT 1 FROM papers
            WHERE papers.id = paper_queries.paper_id AND papers.relevance_score IS NOT NULL
          )
    """)


def _migrate_fts_drop_summary(conn):
    """papers_fts originally indexed title+abstract+summary; summary moved to
    paper_queries (per-query, not per-paper — see db/schema.sql) so it no
    longer belongs in a paper-level search index. Detects the old 3-column
    shape and rebuilds the FTS table + its sync triggers without it. No-op
    on a fresh install (schema.sql already creates the 2-column shape) or an
    already-migrated DB."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(papers_fts)")}
    if "summary" not in cols:
        return
    conn.executescript("""
        DROP TRIGGER IF EXISTS papers_ai;
        DROP TRIGGER IF EXISTS papers_ad;
        DROP TRIGGER IF EXISTS papers_au;
        DROP TABLE papers_fts;
        CREATE VIRTUAL TABLE papers_fts USING fts5(
          title, abstract, content='papers', content_rowid='rowid'
        );
        CREATE TRIGGER papers_ai AFTER INSERT ON papers BEGIN
          INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
        END;
        CREATE TRIGGER papers_ad AFTER DELETE ON papers BEGIN
          INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES('delete', old.rowid, old.title, old.abstract);
        END;
        CREATE TRIGGER papers_au AFTER UPDATE ON papers BEGIN
          INSERT INTO papers_fts(papers_fts, rowid, title, abstract) VALUES('delete', old.rowid, old.title, old.abstract);
          INSERT INTO papers_fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract);
        END;
    """)
    conn.execute("INSERT INTO papers_fts(rowid, title, abstract) SELECT rowid, title, abstract FROM papers")


def init_db():
    conn = get_connection()
    with open(config.SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    # One row per (invocation x query) now that ingestion loops over multiple
    # queries — nullable + ON DELETE SET NULL since runs is a historical log,
    # not a pure join table (deleting a query shouldn't erase that it ran).
    _add_column_if_missing(conn, "runs", "query_id", "INTEGER REFERENCES queries(id) ON DELETE SET NULL")
    # Approx USD cost of that run's OpenRouter scoring calls (see
    # ingest/scoring.py), summed across its batches. NULL on runs from
    # before this column existed, and on error runs where scoring never
    # ran — the Settings run log renders both as "—", not "$0.0000".
    _add_column_if_missing(conn, "runs", "cost_usd", "REAL")
    # Optional per-folder color swatch (hex string from queries.FOLDER_COLORS),
    # NULL meaning "no color set" — rendered as a neutral dot.
    _add_column_if_missing(conn, "folders", "color", "TEXT")
    # Read/unread tracking. NULL = unread; a timestamp once marked read.
    # A plain column (not a join table like folders) since "read" is a single
    # boolean-ish flag per paper, not a many-valued relationship.
    _add_column_if_missing(conn, "papers", "read_at", "TEXT")
    # Free-text scoring guidance appended to the LLM prompt (see
    # ingest/scoring.py) — separate from keywords_raw, which still drives
    # what gets fetched/filtered.
    _add_column_if_missing(conn, "queries", "scoring_instructions", "TEXT")
    # Per-query scoring — see db/schema.sql's paper_queries comment and
    # _migrate_scores_to_paper_queries below.
    _add_column_if_missing(conn, "paper_queries", "relevance_score", "REAL")
    _add_column_if_missing(conn, "paper_queries", "summary", "TEXT")
    _add_column_if_missing(conn, "paper_queries", "scored_at", "TEXT")
    # Distinguishes a background rescoring job (ingest/backfill_scores.py)
    # from a normal ingestion run — ingest/ingest.py's "since when do we
    # fetch" watermark only looks at run_type = 'ingest', so a backfill run
    # must never be mistaken for one.
    _add_column_if_missing(conn, "runs", "run_type", "TEXT NOT NULL DEFAULT 'ingest'")
    conn.commit()
    _migrate_scores_to_paper_queries(conn)
    conn.commit()
    _migrate_fts_drop_summary(conn)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {config.DATABASE_PATH}")
