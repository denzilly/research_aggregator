import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

DATABASE_PATH = Path(os.environ.get("DATABASE_PATH", BASE_DIR / "data" / "phage_digest.db"))
SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")

# Zotero OAuth 1.0a "Login with Zotero" — from registering an app at
# zotero.org/oauth/apps. Optional: when unset, Settings only offers the
# manual API-key connect path (app/static/js/app.js), which needs neither.
ZOTERO_CLIENT_KEY = os.environ.get("ZOTERO_CLIENT_KEY", "")
ZOTERO_CLIENT_SECRET = os.environ.get("ZOTERO_CLIENT_SECRET", "")

SITE_PASSWORD = os.environ.get("SITE_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")

INITIAL_BACKFILL_DAYS = int(os.environ.get("INITIAL_BACKFILL_DAYS", "30"))

# Re-fetch a small overlap into the previous run's window to catch papers
# that were indexed a day or two late by PubMed/bioRxiv. Safe because
# ingestion dedupes by id.
OVERLAP_DAYS = 1

LOCK_FILE_PATH = BASE_DIR / "data" / ".ingest.lock"
