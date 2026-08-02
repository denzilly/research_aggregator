# Phage Digest — Webserver Deployment Plan

Handoff doc for continuing this project in a Claude Code session running
*on* the webserver (this was built on a local Windows dev machine, which
had no access to the server). Read this alongside `project.md` (original
plan) — this doc covers what's actually been built, decisions made along
the way that extend beyond the original plan, and the steps left to go
live.

## Status: what's done

Built and verified end-to-end against live APIs on the dev machine:

- **Ingestion pipeline** (`ingest/`): PubMed (E-utilities) + bioRxiv/medRxiv
  fetch, word-boundary keyword matching, OpenRouter batch scoring/
  summarization, SQLite storage with FTS5. Verified with real runs
  (170-paper backfill, incremental runs, concurrent-run lock).
- **Web app** (`app/`): digest view, FTS5 search with filters, favorites
  (star toggle), settings (keyword editing + manual "run now" trigger).
  Styled per the "ScholarStream" design system pulled from the project's
  Stitch mockups. Verified in-browser against real data.
- Local git history has the full build story if you want the "why" behind
  anything below — `git log`.

**Not done**: actual deployment (this doc), `WRITE_SECRET` not finalized,
real keyword list not yet set (still a placeholder), Hermes cron not wired
up, no Cloudflare Tunnel route yet.

## Key decisions made since project.md

Worth knowing before touching anything, since some of these extend or
correct the original plan:

- **Hermes' role is scheduling only.** The cron job should shell-exec the
  ingestion script directly (`python -m ingest.ingest`), *not* describe
  ingestion in natural language for Hermes' agent loop to improvise. The
  script must stay the single source of truth for ingestion logic —
  deterministic dedupe/scoring, not re-reasoned per run.
- **LLM calls go direct to OpenRouter**, not through Hermes. Model is
  pinned to `deepseek/deepseek-v4-flash` via `OPENROUTER_MODEL` in
  `.env`. Note: OpenRouter's `response_format: json_object` mode is
  deliberately *not* used (see `ingest/scoring.py` comment) — it forces a
  top-level JSON object incompatible with the array-shaped prompt/parser
  here, and reintroducing it will silently break scoring again.
- **bioRxiv/medRxiv have no keyword search API** — the ingestion fetch
  pre-filters by subject category (`ingest/biorxiv.py:SERVER_CATEGORIES`)
  before client-side keyword matching, because pulling every subject's
  daily output was too slow (8+ min for a 2-week window). If her keyword
  list later drifts into a new subject area, add the category there.
- **Digest view is a rolling 7-day window** (`app/queries.py:
  DIGEST_WINDOW_DAYS`), not "papers from the single most recent run" —
  steady-state runs often find 0 new papers on a given day, so scoping to
  one run's boundary made the digest render empty most of the time.
- **First-run backfill is 30 days** (`INITIAL_BACKFILL_DAYS`), steady
  state re-fetches since the last successful run with a 1-day overlap
  (`OVERLAP_DAYS`) to catch late-indexed papers. Dedupe by id makes the
  overlap free.
- **No real auth** — by design (2-person tool). Write endpoints (favorite
  toggle, settings save, run-now) are optionally gated by `WRITE_SECRET`:
  if set in `.env`, requests must include it via `X-Write-Secret` header
  or `secret` form/query field. The web UI stores it in the browser's
  localStorage once entered on the Settings page. If `WRITE_SECRET` is
  left empty, those endpoints stay open to anyone with the URL.
- **`ingest/backfill_scores.py`** is a standalone maintenance script for
  scoring any papers with a null `relevance_score` (e.g. if the OpenRouter
  key was missing/broken during a run). Safe to re-run any time.
- The dev machine needed `pip_system_certs` in `requirements.txt` to fix
  an SSL trust-store issue specific to its Windows network. Almost
  certainly unnecessary on a normal Linux server — try without it first;
  only keep it if `pip install` / API calls hit SSL verification errors.

## Deployment steps

Fill in the `<TODO>` placeholders with this server's actual paths/setup.

### 1. Get the code onto the server

```bash
git clone <TODO: this repo's remote> /path/to/research_aggregator
cd /path/to/research_aggregator
```

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

If `pip_system_certs` causes problems on Linux (it's a Windows/macOS-
oriented fix), just remove that line from `requirements.txt`.

### 3. Configure secrets

```bash
cp .env.example .env
```

Fill in:
- `OPENROUTER_API_KEY` — required for scoring/summarization.
- `NCBI_API_KEY` — optional, raises PubMed rate limit 3→10 req/s.
- `WRITE_SECRET` — generate one now, e.g. `openssl rand -hex 32`. Set it
  before this is reachable from the internet.
- `DATABASE_PATH` — defaults to `./data/phage_digest.db`, fine to leave.

**Never commit `.env`** — it's gitignored, keep it that way.

### 4. Database: fresh vs. migrated

Two options:

- **Fresh start**: run `python db.py` to create an empty schema, then let
  the first ingestion run do its normal 30-day backfill. Simple, but
  redisks the OpenRouter cost already paid once during dev.
- **Migrate the dev DB** (recommended): copy the dev machine's
  `data/phage_digest.db` (170+ already-scored papers) to this server's
  `data/` directory. It's just a file copy — no export/import needed. Then
  skip straight to step 6.

### 5. (If fresh start) Initialize schema

```bash
python db.py
```

### 6. Verify ingestion works manually before automating it

```bash
python -m ingest.ingest
```

Check it completes with `status: ok` and sensible paper counts. This also
confirms outbound network access to PubMed/bioRxiv/OpenRouter from the
server works before wiring up cron.

### 7. Wire up Hermes cron

Set up a Hermes cron job that shell-execs (not natural-language-describes)
this exact command on a daily schedule, e.g. early morning:

```bash
cd /path/to/research_aggregator && .venv/bin/python -m ingest.ingest
```

Use the venv's python explicitly so it picks up installed dependencies
regardless of Hermes' own environment. Confirm via Hermes' cron job list
that it fires and check the `runs` table (or the web UI's header status
line) the next day to confirm it actually ran.

### 8. Run the web app as a persistent service

`python run.py` is the Flask dev server — not for production. Add a
production WSGI server:

```bash
pip install gunicorn
```

Then create a systemd service, e.g. `/etc/systemd/system/phage-digest.service`:

```ini
[Unit]
Description=Phage Digest web app
After=network.target

[Service]
WorkingDirectory=/path/to/research_aggregator
ExecStart=/path/to/research_aggregator/.venv/bin/gunicorn -w 2 -b 127.0.0.1:<TODO: pick a free port> run:app
Restart=on-failure
EnvironmentFile=/path/to/research_aggregator/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now phage-digest
sudo systemctl status phage-digest
```

### 9. Add a Cloudflare Tunnel route

Add an ingress rule to the existing tunnel config (`<TODO: locate the
existing cloudflared config.yml used for the blog>`) pointing a
hostname/path to `http://127.0.0.1:<port>`. Keep it on a non-obvious
path/subdomain per the original plan's trust model. Reload/restart
cloudflared to pick up the new route.

### 10. Set the write secret in the browser

Visit the deployed Settings page once, paste the `WRITE_SECRET` value into
the "Write secret" field, save. Confirm favoriting a paper and saving
keywords both work through the tunnel.

## Verification checklist

- [ ] `python -m ingest.ingest` runs clean manually
- [ ] Hermes cron job fires on schedule (check next day)
- [ ] `systemctl status phage-digest` shows active/running
- [ ] Digest/Search/Settings all load through the Cloudflare Tunnel URL
- [ ] Favorite toggle and settings save work (write secret entered in browser)
- [ ] Write endpoints reject requests without the correct secret (curl test without header should 403)
- [ ] `data/phage_digest.db` is included in whatever backup process this server already uses (it's a single file — periodic copy is enough, no special tooling needed)

## Still open (not blockers, but worth deciding)

- **Real keyword list** — currently a placeholder
  (`bacteriophage, phage therapy, phage ecology`). Needs her actual
  subtopic input, set via the Settings page once live.
- **Retention/pruning** — no policy yet; fine to leave papers accumulating
  indefinitely for now given the volume (project.md notes this is cheap
  at this scale).
- **v2 (Semantic Scholar recommendations)** — deliberately out of scope
  until the core pipeline above has run unattended for a while and proven
  itself.
