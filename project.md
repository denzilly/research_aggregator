# Phage Digest — Project Plan

## Goal
A daily research digest so my girlfriend can keep up with the latest academic
papers in her subfield (bacteriophages), without relying on Google Scholar
manually. Self-hosted on my own webserver, exposed via the same Cloudflare
Tunnel that already serves my blog.

## Non-goals for v1
- No auth system — this is just for the two of us on my own server.
- No mobile app / push notifications — a web page is enough.
- No multi-user support.

## Data sources
- **PubMed / NCBI E-utilities** — primary source. Free API, no key required
  for light use; get an NCBI API key to raise rate limit to 10 req/s.
  Search query built from configurable keywords (see below), e.g.
  `bacteriophage[Title/Abstract] OR phage therapy[Title/Abstract]`.
- **bioRxiv API** — preprints, often days-to-months ahead of PubMed.
- Explicitly NOT using arXiv (near-zero relevant biology coverage) or
  Google Scholar (no public API, scraping violates ToS).

## Storage: SQLite
No Postgres/Supabase — SQLite is a single file, easy to back up, and more
than sufficient for this volume (tens of papers/day). Use the FTS5 extension
for full-text search over titles/abstracts to support the search UI.

### Schema (draft)
```sql
CREATE TABLE papers (
  id TEXT PRIMARY KEY,          -- e.g. "pubmed:12345678" or "biorxiv:10.1101/xxxx"
  source TEXT NOT NULL,         -- 'pubmed' | 'biorxiv'
  title TEXT NOT NULL,
  authors TEXT,
  published_date TEXT,
  abstract TEXT,
  summary TEXT,                 -- LLM-generated
  relevance_score REAL,         -- LLM-generated, 0-10
  url TEXT,
  ingested_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE papers_fts USING fts5(
  title, abstract, summary, content='papers', content_rowid='rowid'
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
-- settings row: key='keywords', value='comma-separated or JSON list'
```

## Ingestion pipeline (runs on a cron/systemd timer, once daily)
1. Read current keyword list from `settings` table.
2. Query PubMed + bioRxiv for papers matching those keywords, published since
   last run.
3. Dedupe against `papers` table by id (pubmed id / DOI) — only keep genuinely
   new papers.
4. **Batch** new papers' abstracts into a single (or few, if many papers)
   prompt(s) to the Hermes agent via OpenRouter. Ask for structured JSON only:
   relevance score (0-10) against the current keyword list, and a 2-3
   sentence plain-language summary, per paper id. Do not call the LLM per
   paper — batch to cut call count and cost.
5. Write new rows into `papers` (and update the FTS index).
6. Nothing else touches the LLM — search/filtering at read time is pure
   SQLite queries, free and instant.

### Example batch prompt shape
```
System: You will be given N paper abstracts. For each, return relevance to
[current keyword list] as a 0-10 score, and a 2-sentence summary.
Respond ONLY with a JSON array, no other text, no markdown fences.

User: [{"id": "pubmed:12345", "abstract": "..."}, ...]
```

## Web app (small dynamic service, not a static regenerate)
Needed because of the search/filtering requirement. Lightweight
Flask/Express app, reads from SQLite on request:
- **Digest view** — today's (or latest run's) new papers, sorted by
  relevance score.
- **Search/filter UI** — full-text search over title/abstract/summary via
  FTS5, plus filter by source/date range.
- **Settings box** — a single textarea showing/editing the keyword list,
  saved back to the `settings` table. No auth needed given the trust model,
  but keep it on a non-obvious path if that feels more comfortable.

## Deployment
- Runs on my existing webserver alongside the Hermes agent.
- Exposed through the same Cloudflare Tunnel already used for the blog.
- Ingestion job: systemd timer (preferred over cron for logging/status).
- App + SQLite file live in their own directory; back up the SQLite file
  periodically (it's just a file copy).

## v2: Semantic Scholar recommendations feed
Not part of the initial build — revisit once the core daily pipeline (PubMed
+ bioRxiv ingestion, search UI, keyword settings) is working and tested.

- **Not** used as a third keyword-search source in the daily ingestion loop —
  it would mostly return papers PubMed/bioRxiv already caught, with dedup
  overhead for no real gain.
- Instead, a separate **weekly** job:
  1. Take the 5-10 highest `relevance_score` papers ingested in the past week.
  2. For each, call the Semantic Scholar Recommendations API
     (`GET /recommendations` given a paper ID) to get semantically similar
     papers — this surfaces conceptually related work even if it doesn't
     share her exact keywords, which is a different discovery axis than
     keyword search.
  3. Dedupe against the `papers` table by id/DOI.
  4. Store results in their own table (e.g. `recommendations`) or a `source`
     value distinguishing them from the daily digest, and surface as a
     separate "you might also like" section in the web app.
- No API key needed at this volume — unauthenticated limit is roughly 100
  requests / 5 minutes, comfortably enough for a weekly batch of ~10 lookups.
- Could also use the citation graph (papers citing / cited by a flagged
  paper) as a second discovery angle in the same v2 pass.

## Open questions / things to decide while building
- Exact keyword list to seed on day one (need her input on subtopics beyond
  "bacteriophage" — e.g. phage therapy, phage ecology, phage-host dynamics).
- Whether relevance scoring should support "must-have" vs "nice-to-have"
  keyword weighting, or stay a flat list for v1 (leaning flat list for v1).
- Retention: keep all papers forever (cheap at this volume) or prune low-
  relevance old entries eventually?
