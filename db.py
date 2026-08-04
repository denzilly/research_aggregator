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


def init_db():
    conn = get_connection()
    with open(config.SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    # One row per (invocation x query) now that ingestion loops over multiple
    # queries — nullable + ON DELETE SET NULL since runs is a historical log,
    # not a pure join table (deleting a query shouldn't erase that it ran).
    _add_column_if_missing(conn, "runs", "query_id", "INTEGER REFERENCES queries(id) ON DELETE SET NULL")
    # Optional per-folder color swatch (hex string from queries.FOLDER_COLORS),
    # NULL meaning "no color set" — rendered as a neutral dot.
    _add_column_if_missing(conn, "folders", "color", "TEXT")
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Initialized database at {config.DATABASE_PATH}")
