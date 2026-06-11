# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BETrack** — a real-time odds monitor for two Greek bookmakers (**Stoiximan**, **Novibet**) across **football, basketball, and tennis**. It polls each bookmaker's own live-odds web endpoints directly, normalizes everything to a canonical model, persists it to SQLite, and shows both books' prices side by side on a React dashboard, matched across books by Sportradar id.

Current phase is **data acquisition + display**. The discrepancy-detection layer (value / arbitrage) exists in the tree but is **suspended** — `run_cycle` does not call it. It will be re-enabled in a later phase on top of the persisted data.

Bookmaker data comes from each book's **private live-odds web API** (the same endpoints their own site calls), fetched with `curl_cffi` Chrome TLS-impersonation to pass Cloudflare. There is no third-party odds feed and no headless browser. Scope is deliberately three sports and a handful of main markets per sport.

The original product spec is in [real_time_odds_discrepancy_monitor_mvp_spec.md](real_time_odds_discrepancy_monitor_mvp_spec.md), and the build brief for the current ingestion work is in [BUILD_PROMPT.md](BUILD_PROMPT.md). **Both predate the current code — read "Spec vs. implementation" below before trusting either.**

## Commands

```bash
pip install -r requirements.txt        # Python 3.10+ (uses 3.10+ union syntax); needs curl_cffi

python main.py                         # continuous console poller (prints per-book/sport counts)
python serve.py                        # web dashboard (FastAPI) at http://127.0.0.1:8000
python diagnose.py                     # LEGACY odds-api.io scan — dead, needs the old API_KEY; not part of the new pipeline
```

**No API key or `.env` is required** for `main.py` / `serve.py` — they call the bookmakers directly. (`diagnose.py` and the old `OddsApiClient` are dead code from the previous odds-api.io design and still expect `API_KEY`; ignore them.) The user's machine has a Greek consumer IP, which both bookmakers accept — no VPN/proxy.

`main.py` and `serve.py` are independent pollers (each opens its own bookmaker sessions and writes the same `betrack.db`). Run **one at a time** to avoid double-polling the bookmakers. Be polite: don't poll any endpoint faster than 5s.

Frontend (React/Vite/TS/Tailwind v4 SPA in [betrack/web/frontend/](betrack/web/frontend/)):

```bash
cd betrack/web/frontend
npm install
npm run build       # type-checks (tsc --noEmit) then builds into ../static/dist (served by serve.py)
npm run dev         # dev server on :5173, proxies /api -> :8000 (run `python serve.py` alongside it)
```

There is a frontend build (`npm run build`, which runs `tsc --noEmit` as a type-check gate) but **no Python test suite, linter, or CI** — do not claim one exists. `npm run build` output (`betrack/web/static/dist/`) and `node_modules/` are gitignored — you must build before `serve.py` shows the React UI; if `dist/` is absent, `serve.py` falls back to the legacy Alpine page at [betrack/web/static/index.html](betrack/web/static/index.html).

**Bookmaker names are capitalized** (`"Stoiximan"`, `"Novibet"`) and flow through unchanged as the canonical `bookmaker` value and as quote_latest/markets keys.

## Architecture

The pipeline is an async fan-through. [betrack/pipeline.py](betrack/pipeline.py)'s `run_cycle(stoiximan, novibet, store)` is the shared heart, called by both entry points:

```
StoiximanClient / NovibetClient (curl_cffi)         ← betrack/ingestion/{stoiximan,novibet}.py
   fetch_overview()  →  one call returns all sports' live events + headline markets
   fetch_event(id)   →  per-event full market set (fanned out, concurrency-capped)
        │ raw JSON
        ▼
stoiximan_mapper / novibet_mapper  →  MappedEvent bundles  ← betrack/normalization/{*}_mapper.py, bundle.py
   (CanonicalEvent + per-bookmaker CanonicalMarket / CanonicalOutcome / OddsQuote)
        │
        ▼
SqliteOddsStore (betrack.db)       ← betrack/store/odds_store_sqlite.py
   events / markets / outcomes (upsert)   quote_latest (one row per book×outcome)
   quote_history (append ONLY on price change)
        │
        ▼
FastAPI (reads only, cross-book grouping)  ← betrack/web/app.py
        │
        ▼
React dashboard (3 sport tabs, polls /api every 5s)  ← betrack/web/frontend/
```

Each cycle (`POLL_INTERVAL = 10s`, adaptive sleep so we don't pile on if a cycle runs long; `MIN_SLEEP = 2s` floor): bookmakers fetch **concurrently** via `asyncio.gather` (Stoix, Novi, Betfair all in parallel), each does its overview (one call covers all three sports with headline markets) and fans out per-event detail (`detail_concurrency=8`, `detail_limit=60`); writes to SQLite serialize after the gather. `run_cycle` returns a `CycleResult` with per-`"Bookmaker/sport"` counts (events, markets, quotes_observed, quotes_changed) and totals. Cycle wall-time is logged and surfaced as `status.cycle_seconds`. **Concurrent fetches matter** — sequential fetches let a goal/timeout mid-cycle produce phantom cross-book arbs because one book was queried pre-event and another post-event.

### Bookmaker clients & endpoints

- **Stoiximan** (`en.stoiximan.gr`): overview `GET /danae-webapi/api/live/overview/0?...` (all sports, ~600KB, includes headline `markets`+`selections` with prices); per-event `GET /danae-webapi/api/live/events/{id}/latest` (eventId-only, full markets). Requires `x-language:1`, `x-operator:2` headers.
- **Novibet** (`www.novibet.gr`): overview `GET /spt/feed/marketviews/location/v2/4324/4390/?...` (the `4390` live group returns **all sports** in one call); per-event `GET /spt/feed/marketviews/event/4324/{id}?...`. The leading `4324` is a **fixed segment used for all sports** — it is *not* a per-sport id (verified via HAR captures), and `navigation/menu/5` is promo shortcuts, not a sports list. Requires the `x-gw-*` header set.
- Both are behind Cloudflare: you **must** use `curl_cffi.requests.AsyncSession(impersonate="chrome")`. Plain `aiohttp`/`requests`/`httpx` get a 403.

### Cross-bookmaker matching

Both books carry a Sportradar match id (Stoiximan `event.betradarMatchId`, Novibet `liveData.sportradarMatchId`). The canonical `event_id = md5(sportradar_match_id, sport)` so **the same match from both books collapses to one `events` row** (its `bookmaker_event_ids` JSON holds each book's native id). Markets/outcomes are per-bookmaker (their ids fold in the bookmaker), and the web layer regroups them across books by `(market_type, period, line)` / `(outcome_type, line)`. Events with **no** Sportradar id fall back to `md5(bookmaker, native_id, sport)` and therefore won't cross-join (common for lower-tier tennis/doubles).

### Esports filtering

Stoiximan files FIFA/NBA2K simulations under `FOOT`/`BASK`; they're skipped by **zone name** (`Esoccer`/`Ebasketball`/`Etennis`). Novibet's equivalents are skipped by `"esports"` appearing in competitor names. Only real football/basketball/tennis is ingested.

### SQLite store ([betrack/store/odds_store_sqlite.py](betrack/store/odds_store_sqlite.py))

`SqliteOddsStore` mirrors the connect-per-call pattern of `history.py`. Tables: `events`, `markets`, `outcomes`, `quote_latest` (one row per `(bookmaker, outcome_id)`), `quote_history` (append-only). **Critical write rule** — `upsert_quote` appends a `quote_history` row *only when the price differs* from `quote_latest`; an unchanged price just bumps `observed_at`. This keeps history proportional to real price movement, not poll frequency. `prune_quote_history(days=14)` trims old history. The store filters `events` by freshness (`last_seen_at` within `FRESH_SECONDS`) so events that stop appearing drop off the dashboard.

### Web layer ([betrack/web/app.py](betrack/web/app.py))

`serve.py` runs a single-process FastAPI app: a `Runtime` owns the poll loop (background asyncio task) that writes `SqliteOddsStore`; handlers only read and do the cross-book grouping server-side. JSON endpoints:
- `GET /api/sports` → `[{key,label,live_count}]` for football/basketball/tennis.
- `GET /api/events?sport=` → event rows with the headline market (1X2 for football, Match Winner for the others) showing both books' odds, `best`, and `gap_pct`.
- `GET /api/event/{event_id}` → every market grouped across books, each outcome with both books' quotes (`odds`, `age_seconds`, `outcome_id`), `best`, `gap_pct`; `{"found": false}` if absent.
- `GET /api/quote-history/{outcome_id}?bookmaker=&limit=` → price-change time series (for sparklines).
- `GET /api/status` → last cycle metrics (`counts`, `total_observed`, `total_changed`, `errors`, `detection: "suspended"`).
- `GET /api/opportunities`, `GET /api/history` → return `[]` (detection suspended).

Freshness `age_seconds` is computed from `observed_at` (our fetch time), **not** `source_timestamp` — the latter is the book's own clock (Novibet's `referenceTime` is the live-match clock) with inconsistent semantics. `GET /` serves the built React SPA when present, else the legacy Alpine page.

The dashboard ([betrack/web/frontend/src/](betrack/web/frontend/src/)): `App.tsx` shell with Football/Basketball/Tennis tabs + bookmaker/search filters; `EventsTable.tsx` (rows = match + both books' headline odds, best in green, gap %, freshness badge, book badges); `EventDrawer.tsx` (click a row → `/api/event/{id}`, all markets both books side by side). Polls every 5s. Tailwind v4 via `@tailwindcss/vite`.

### Canonical data model ([betrack/models/canonical.py](betrack/models/canonical.py))

- **CanonicalEvent** — `event_id, sport, competition, home_team, away_team, start_time, status`
- **CanonicalMarket** — `market_id, event_id, market_type, period, line, settlement_scope`
- **CanonicalOutcome** — `outcome_id, market_id, outcome_type, team_reference, line`
- **OddsQuote** — `bookmaker, event_id, market_id, outcome_id, decimal_odds, timestamp_received, source_timestamp, status, liquidity`

`MarketType` / `OutcomeType` are str-enums covering all three sports (football 1X2 / O-U / BTTS / double-chance / draw-no-bet; basketball match-winner / total-points / handicap; tennis match-winner / total-games / set-winner). IDs are deterministic md5 hashes (`_make_id`), so re-ingesting upserts in place. Per-bookmaker market/outcome ids fold in the bookmaker; `event_id` does not (that's the cross-book key).

Normalization rules to preserve:
- Team-name variants collapse via `TEAM_ALIASES` (in the old `betrack/normalization/mapper.py`, reused by both new mappers).
- Each mapper has a verified market table (Stoiximan by `typeId`, Novibet by `marketSysname`). An **unknown** market type is **skipped and logged once** at INFO — grow the table to support more.
- Only markets that genuinely carry a line (over/under, handicap) parse one; 1X2/winner/BTTS must not (their selection names are codes like `"1"`/`"X"`/`"2"` or team names). Lines are part of the market/outcome id, so different lines never merge.
- Human-readable labels live in [betrack/labels.py](betrack/labels.py).

## Spec vs. implementation (important divergences)

Match new work to the **code's** current shape unless explicitly asked to close a gap:

- **Detection is suspended.** `comparison/engine.py` (`find_value`/`find_arbitrage`), `alerts/engine.py` (`AlertEngine`), `delivery/console.py`, and `HistoryStore` are intact but **not called**. The dashboard shows raw both-books prices + a `gap_pct` (max/min−1 across the two books), not detected value/arb opportunities. `/api/opportunities` and `/api/history` return `[]`.
- **No odds-api.io, no Betfair.** The old `OddsApiClient` ([betrack/ingestion/client.py](betrack/ingestion/client.py)), `map_event`/`map_odds` ([betrack/normalization/mapper.py](betrack/normalization/mapper.py)), `OddsStore` ([betrack/store/odds_store.py](betrack/store/odds_store.py)), and `diagnose.py` are **dead code** kept for reference. Data now comes directly from the two bookmakers.
- **Polling, 30s.** No WebSocket/push (Stoiximan has a SignalR stream; not used).
- **Raw quotes now persist.** Unlike the old in-memory `OddsStore`, `SqliteOddsStore` persists `quote_latest` + `quote_history` (append-on-change) across restarts.
- **Known limitations / nuances:** home/away is each book's own designation (rare disagreement, mainly tennis, can mismatch a 2-way outcome across books); only a few main markets per sport are mapped (others skipped+logged); tennis set-winner maps set 1 only; `source_timestamp` is stored but not used for the freshness badge.

There is **no Python test suite, linter, or CI.** Don't add one unless asked. New modules go under `betrack/`; root is for entry points only (`main.py`, `serve.py`, `diagnose.py`).
