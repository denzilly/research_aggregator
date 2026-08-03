# Phage Digest

A daily research digest of bacteriophage-related papers, self-hosted and
exposed via Cloudflare Tunnel at `research.btblog.dev`. Pulls new papers
from PubMed and bioRxiv/medRxiv by keyword, scores/summarizes them with an
LLM via OpenRouter, and serves them through a small Flask app
("phageDB") with a digest view, full-text search, and folders for
saving papers.

## Docs

- `project.md` — original plan and design.
- `webserver_deploy.md` — how it's actually deployed (Docker + Caddy +
  Cloudflare Tunnel, OS-level cron, password gate), and where that
  diverged from the original plan.

## Layout

- `ingest/` — fetch, keyword-match, score/summarize, and store papers.
  Entry point: `python -m ingest.ingest`.
- `app/` — the Flask web app (`run.py` / `app:create_app`).
- `db/schema.sql` — SQLite schema (FTS5 for search).
- `Dockerfile` / `docker-compose.yml` — production runtime.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in OPENROUTER_API_KEY at minimum
python db.py           # initialize schema
python run.py           # dev server on :5000
```

## Production

```bash
docker compose up -d --build
```

See `webserver_deploy.md` for the full story (env vars, cron, Caddy/tunnel
wiring, password gate).
