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

WRITE_SECRET = os.environ.get("WRITE_SECRET", "")

INITIAL_BACKFILL_DAYS = int(os.environ.get("INITIAL_BACKFILL_DAYS", "30"))

# Re-fetch a small overlap into the previous run's window to catch papers
# that were indexed a day or two late by PubMed/bioRxiv. Safe because
# ingestion dedupes by id.
OVERLAP_DAYS = 1

LOCK_FILE_PATH = BASE_DIR / "data" / ".ingest.lock"
