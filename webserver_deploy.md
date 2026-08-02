# Phage Digest — Webserver Deployment Plan

Originally a handoff doc for continuing this project in a Claude Code
session running *on* the webserver (built on a local Windows dev machine,
which had no access to the server). **Deployment is now done** (2026-08-02)
— this doc is kept as a record of what was actually built and why, since
the live setup ended up diverging from the plan below in a few real ways.
Read alongside `project.md` (original plan).

## Status: live

- **URL**: `https://research.btblog.dev` (password-gated — see below).
- **Ingestion pipeline** (`ingest/`): PubMed (E-utilities) + bioRxiv/medRxiv
  fetch, word-boundary keyword matching, OpenRouter batch scoring/
  summarization, SQLite storage with FTS5.
- **Web app** (`app/`): digest view, FTS5 search with filters, favorites
  (star toggle), settings (keyword editing + manual "run now" trigger).
  "ScholarStream" design system.
- **DB**: migrated from the dev machine (177 already-scored papers) rather
  than re-scoring from scratch — see `data/phage_digest.db`.
- **Scheduling**: OS-level crontab (`crontab -l` as `bart`), not Hermes —
  see "Deviation: cron" below.
- Local git history has the full build story if you want the "why" behind
  anything below — `git log`.

## Key decisions made since project.md

Worth knowing before touching anything, since some of these extend or
correct the original plan:

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
- **Write endpoints** (favorite toggle, settings save, run-now) are
  gated by `WRITE_SECRET`: requests must include it via `X-Write-Secret`
  header or `secret` form/query field. The web UI stores it in the
  browser's localStorage once entered on the Settings page.
- **Whole-site password gate added post-deployment**, on top of (not
  instead of) `WRITE_SECRET` — see "Deviation: auth" below. This
  supersedes project.md's "no auth system" non-goal.
- **`ingest/backfill_scores.py`** is a standalone maintenance script for
  scoring any papers with a null `relevance_score` (e.g. if the OpenRouter
  key was missing/broken during a run). Safe to re-run any time.
- `pip_system_certs` was dropped from `requirements.txt` — it was a
  Windows-network-specific SSL fix, unnecessary on Linux/Docker.
- **Fixed a lock-leak bug in `ingest/ingest.py`** during deployment
  verification: `db.get_connection()` was called *before* the
  `try/finally` that releases `.ingest.lock`, so any exception at that
  line (e.g. a bad `DATABASE_PATH`) left a permanent stale lock. Now
  wrapped so the lock always releases even if the connection itself
  fails.

## Deviation: Docker instead of native venv/systemd

The original plan (steps below, kept for reference) assumed a bare
`.venv` + systemd + a local `cloudflared` `config.yml`. The actual server
runs a shared Docker-based reverse-proxy stack (`~/projects/infra`: Caddy
+ cloudflared, joined by an external `web` network — see that project's
`README.md`/`SERVICES.md`), and `research_aggregator` didn't fit that
model as a bare venv. So instead:

- `Dockerfile` (python:3.12-slim, gunicorn) and `docker-compose.yml`
  (joins the external `web` network, mounts `./data`) were added to the
  repo.
- `docker compose up -d` replaces the systemd unit.
- Caddy block added in `~/projects/infra/caddy/Caddyfile`:
  `http://research.btblog.dev { reverse_proxy phage-digest:8000 }`.
- Cloudflare Tunnel Public Hostname route added in the dashboard:
  `research.btblog.dev` → `http://caddy:80` (same pattern as `btblog.dev`
  itself).
- **`DATABASE_PATH` must be set explicitly** to `/app/data/phage_digest.db`
  in `.env` — a present-but-empty value is NOT the same as unset, since
  `config.py`'s default only applies when the key is absent from the
  environment. This bit us once during deployment (`unable to open
  database file`).
- Row added to `~/projects/infra/SERVICES.md`.

## Deviation: cron runs via OS crontab, not Hermes

The original plan had Hermes shell-exec the ingestion command directly
("Hermes' role is scheduling only — deterministic script, not
LLM-improvised"). That intent still holds, but the mechanism changed:
Hermes' gateway runs in its own Docker container (`network_mode: host`,
only `~/.hermes` mounted — no filesystem access to this project), and its
no-agent cron scripts are sandboxed to files inside `~/.hermes/scripts/`.
It can't reach `docker compose run` here without mounting
`/var/run/docker.sock` into the Hermes container, which was deliberately
declined (that's effectively root-equivalent host access for that
container — too much blast radius for this).

Instead, the `bart` user's OS crontab runs it directly:

```
15 6 * * * cd /home/bart/projects/research_aggregator && /usr/bin/docker compose run --rm phage-digest python -m ingest.ingest >> /home/bart/projects/research_aggregator/data/ingest-cron.log 2>&1
```

Still fully deterministic/script-driven (the original goal), just
scheduled by cron instead of Hermes. Tradeoff: no Telegram/Discord alert
on run/failure the way a Hermes-managed job would give for free — the
`runs` table and the web UI's header status line are the source of truth
for pipeline health instead. `data/ingest-cron.log` has raw output if
something needs debugging.

## Deviation: whole-site password gate

Added after initial deployment, superseding project.md's "no auth
system" non-goal. Two new env vars:

- `SITE_PASSWORD` — if set, every page requires a password before
  anything renders (`/login`, `/logout`, static assets are exempt).
  Session cookie lasts 30 days. If left empty, the gate is disabled
  entirely (matches the existing `WRITE_SECRET` "empty = open" pattern).
- `SECRET_KEY` — signs the Flask session cookie. Required whenever
  `SITE_PASSWORD` is set.

This sits *in front of* `WRITE_SECRET`, which still separately gates
write endpoints — the two are independent. Implementation:
`app/__init__.py` (`before_request` guard + `site_password_configured`
context processor), `app/routes.py` (`/login`, `/logout`),
`app/templates/login.html`.

## Original deployment steps (superseded — kept for reference)

<details>
<summary>What the plan looked like before the Docker/cron/auth
deviations above (click to expand)</summary>

### 1. Get the code onto the server

```bash
git clone <repo> /path/to/research_aggregator
cd /path/to/research_aggregator
```

### 2. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure secrets

```bash
cp .env.example .env
```

### 4. Database: fresh vs. migrated

Migrated the dev DB (recommended) rather than fresh-starting, to avoid
re-paying OpenRouter scoring cost.

### 5. (If fresh start) Initialize schema

```bash
python db.py
```

### 6. Verify ingestion works manually before automating it

```bash
python -m ingest.ingest
```

### 7. Wire up Hermes cron

Superseded — see "Deviation: cron" above.

### 8. Run the web app as a persistent service

Superseded — see "Deviation: Docker" above.

### 9. Add a Cloudflare Tunnel route

Superseded — see "Deviation: Docker" above (same idea, different
mechanics: Cloudflare dashboard → `http://caddy:80` → Caddy → container).

### 10. Set the write secret in the browser

Still applies as-is — see verification checklist below.

</details>

## Verification checklist

- [x] `python -m ingest.ingest` runs clean manually (via `docker compose
      run --rm phage-digest python -m ingest.ingest`)
- [x] Crontab job installed and dry-run tested in a minimal environment
      (fires daily 06:15 — confirm again after the first real overnight run)
- [x] `docker compose ps` shows `phage-digest` up
- [x] Digest/Search/Settings all load through the Cloudflare Tunnel URL
- [x] Login gate: wrong password rejected, correct password grants a
      session, logout revokes it
- [x] Favorite toggle and settings save work (write secret entered in
      browser)
- [x] Write endpoints reject requests without the correct secret (403
      confirmed) and accept it with the correct one (200 confirmed)
- [ ] `data/phage_digest.db` is included in whatever backup process this
      server already uses (it's a single file — periodic copy is enough,
      no special tooling needed) — **still needs confirming**

## Still open (not blockers, but worth deciding)

- **Real keyword list** — currently still the placeholder
  (`bacteriophage, phage therapy, phage ecology`). Needs her actual
  subtopic input, set via the Settings page.
- **Backup coverage** — confirm `data/phage_digest.db` is actually swept
  up by this server's existing backup process.
- **Retention/pruning** — no policy yet; fine to leave papers accumulating
  indefinitely for now given the volume (project.md notes this is cheap
  at this scale).
- **v2 (Semantic Scholar recommendations)** — deliberately out of scope
  until the core pipeline above has run unattended for a while and proven
  itself.
