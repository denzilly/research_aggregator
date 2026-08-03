-- Phage Digest schema. Applied once at setup via: sqlite3 data/phage_digest.db < db/schema.sql

CREATE TABLE IF NOT EXISTS papers (
  id TEXT PRIMARY KEY,          -- e.g. "pubmed:12345678" or "biorxiv:10.1101/xxxx"
  source TEXT NOT NULL,         -- 'pubmed' | 'biorxiv'
  title TEXT NOT NULL,
  authors TEXT,
  published_date TEXT,
  abstract TEXT,
  summary TEXT,                 -- LLM-generated
  relevance_score REAL,         -- LLM-generated, 0-10
  url TEXT,
  is_favorite INTEGER NOT NULL DEFAULT 0,  -- DEPRECATED: superseded by folders (see below). Do not read/write from app code.
  ingested_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_papers_ingested_at ON papers(ingested_at);
CREATE INDEX IF NOT EXISTS idx_papers_relevance ON papers(relevance_score);

CREATE TABLE IF NOT EXISTS folders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_folders (
  paper_id  TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
  added_at  TEXT NOT NULL,
  PRIMARY KEY (paper_id, folder_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_folders_folder ON paper_folders(folder_id);
CREATE INDEX IF NOT EXISTS idx_paper_folders_paper ON paper_folders(paper_id);

CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
  title, abstract, summary, content='papers', content_rowid='rowid'
);

-- Keep papers_fts in sync with papers. Required because FTS5 content-linked
-- tables do not update themselves automatically.
CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
  INSERT INTO papers_fts(rowid, title, abstract, summary)
  VALUES (new.rowid, new.title, new.abstract, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS papers_ad AFTER DELETE ON papers BEGIN
  INSERT INTO papers_fts(papers_fts, rowid, title, abstract, summary)
  VALUES('delete', old.rowid, old.title, old.abstract, old.summary);
END;

CREATE TRIGGER IF NOT EXISTS papers_au AFTER UPDATE ON papers BEGIN
  INSERT INTO papers_fts(papers_fts, rowid, title, abstract, summary)
  VALUES('delete', old.rowid, old.title, old.abstract, old.summary);
  INSERT INTO papers_fts(rowid, title, abstract, summary)
  VALUES (new.rowid, new.title, new.abstract, new.summary);
END;

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);

-- Placeholder seed — replace via the settings UI with the real subtopic list.
INSERT OR IGNORE INTO settings (key, value) VALUES ('keywords', 'bacteriophage');

-- One row per ingestion run. Source of truth for "since when do we fetch"
-- (see ingest/ingest.py) and for the pipeline-health line shown in the web UI.
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL DEFAULT 'running',  -- 'running' | 'ok' | 'error'
  new_papers_count INTEGER,
  error_message TEXT
);

-- One-time migration: preserve previously-starred papers as a "Favorites"
-- folder now that folders supersede is_favorite. Idempotent (INSERT OR
-- IGNORE + UNIQUE(name) / PK(paper_id, folder_id)) — safe to re-run on every
-- init_db() call, including on already-migrated or fresh-install DBs.
INSERT OR IGNORE INTO folders (name, created_at)
SELECT 'Favorites', datetime('now')
WHERE EXISTS (SELECT 1 FROM papers WHERE is_favorite = 1);

INSERT OR IGNORE INTO paper_folders (paper_id, folder_id, added_at)
SELECT papers.id, folders.id, datetime('now')
FROM papers, folders
WHERE papers.is_favorite = 1 AND folders.name = 'Favorites';

DROP INDEX IF EXISTS idx_papers_favorite;
