# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Is

**BETrack** — a real-time odds discrepancy monitor for football betting. It ingests odds for Greek bookmakers (Stoiximan, Novibet), normalizes them to a canonical model, and detects value discrepancies and arbitrage opportunities. MVP scope: football only, markets `1X2` / `Over-Under` / `BTTS`.

This is **not** an auto-betting platform or scraping framework. Bookmaker data comes from a licensed odds-feed API only.

The full product spec is in [real_time_odds_discrepancy_monitor_mvp_spec.md](real_time_odds_discrepancy_monitor_mvp_spec.md). **Read the "Spec vs. implementation" section below before trusting the spec** — the code intentionally diverges from it in several places.

## Commands

```bash
pip install -r requirements.txt        # Python 3.10+ (uses 3.10+ union syntax)
echo "API_KEY=..." > .env              # required; key for api.odds-api.io

python main.py                         # run the continuous poller, console alerts (Ctrl-C to stop)
python serve.py                        # run the web dashboard (FastAPI) at http://127.0.0.1:8000
python diagnose.py                     # one-shot scan: print every comparable outcome + its edge
```

Frontend (the dashboard is a React/Vite SPA in [betrack/web/frontend/](betrack/web/frontend/)):

```bash
cd betrack/web/frontend
npm install
npm run build       # type-checks (tsc --noEmit) then builds into ../static/dist (served by serve.py)
npm run dev         # dev server on :5173, proxies /api -> :8000 (run `python serve.py` alongside it)
```

`API_KEY` is read from the environment / `.env` at startup; all three Python entry points raise immediately if it is missing. `main.py` and `serve.py` both run the same poll loop (via `betrack/pipeline.py:run_cycle`) — `main.py` delivers to console, `serve.py` persists to SQLite and serves the dashboard. **Do not run both at once against the free tier** — each is an independent poller and they share the 100 req/hr quota.

There is a frontend build (`npm run build`, which runs `tsc --noEmit` as a type-check gate) but **no Python test suite, linter, or build step** yet — do not claim one exists. (The `init tests` commit, despite its name, added the package, not tests.) When adding Python tests, also add the runner command here. `npm run build` output (`betrack/web/static/dist/`) and `node_modules/` are gitignored — you must build before `serve.py` shows the React UI; if `dist/` is absent, `serve.py` falls back to the legacy Alpine page at [betrack/web/static/index.html](betrack/web/static/index.html).

**Bookmaker names are capitalized** (`"Stoiximan"`, `"Novibet"`) — that's the exact casing the odds-api.io `/odds` endpoint requires, and it flows through unchanged as the canonical `bookmaker` value. The free tier also requires calling `/bookmakers/selected/select` once per key before `/odds` returns data; the client does this on startup.

## Architecture

The pipeline is a synchronous fan-through. [betrack/pipeline.py](betrack/pipeline.py)'s `run_cycle` is the shared heart, called by both entry points:

```
OddsApiClient (ingestion)  →  map_event / map_odds (normalization)
    →  OddsStore (in-memory)  →  find_value / find_arbitrage (comparison)
    →  AlertEngine (persistence + cooldown gating)  →  delivery
                                                         ├─ console (main.py)
                                                         └─ HistoryStore (SQLite) + FastAPI dashboard (serve.py)
```

One package, one module per stage — [betrack/ingestion/client.py](betrack/ingestion/client.py), [betrack/normalization/mapper.py](betrack/normalization/mapper.py), [betrack/store/odds_store.py](betrack/store/odds_store.py), [betrack/comparison/engine.py](betrack/comparison/engine.py), [betrack/alerts/engine.py](betrack/alerts/engine.py), [betrack/delivery/console.py](betrack/delivery/console.py). All canonical types live in [betrack/models/canonical.py](betrack/models/canonical.py) as pydantic models / str-enums. Human-readable labels for markets/outcomes live in [betrack/labels.py](betrack/labels.py).

Each poll cycle (`POLL_INTERVAL = 180s`, capped at `MAX_EVENTS_PER_CYCLE = 5` to stay under the free-tier 100 req/hr quota): fetch live + prematch events (**prematch first** — Greek-bookmaker live coverage is sparse at off-peak hours), fetch odds per event, skip events with no Stoiximan/Novibet odds, normalize, upsert into the store, then run value and arbitrage comparison. `run_cycle` selects the edge threshold per event (live vs prematch). Callers gate results through the alert engine and deliver.

### Web layer ([betrack/web/app.py](betrack/web/app.py))

`serve.py` runs a single-process FastAPI app: a `Runtime` object owns the poll loop as a background asyncio task that writes into the in-memory `OddsStore` and the SQLite `HistoryStore`; request handlers only read. **One process, one poller** — this is deliberate so the dashboard never doubles quota usage. JSON endpoints: `GET /api/opportunities` (active), `GET /api/history?limit=`, `GET /api/status`, and `GET /api/events/{event_id}` — a per-event drill-down serialized live from `OddsStore` (every market/outcome with both bookmakers' odds side by side + per-outcome edge; `{"found": false}` if the event isn't in the store, e.g. after a restart). `GET /` serves the built React SPA (`static/dist/index.html`, with `/assets` mounted) when present, otherwise the legacy Alpine page.

The dashboard is a **React + Vite + TypeScript + Tailwind v4** SPA in [betrack/web/frontend/](betrack/web/frontend/) ([src/App.tsx](betrack/web/frontend/src/App.tsx) is the shell; filtering/sorting are done client-side over the polled JSON; the drill-down drawer fetches `/api/events/{id}` on row click). It polls the JSON endpoints every 5s. Tailwind is wired via the `@tailwindcss/vite` plugin (no PostCSS config); build output lands in `static/dist`. The legacy single-file Alpine page ([betrack/web/static/index.html](betrack/web/static/index.html)) is kept only as a no-build fallback.

`HistoryStore` ([betrack/store/history.py](betrack/store/history.py)) persists each opportunity *occurrence* as one row in `betrack.db`: while an opportunity keeps being detected it stays `active=1` and `last_seen` advances (so `last_seen − first_seen` is its duration); when a cycle no longer detects it, `expire_missing` flips it to `active=0`. A reappearance creates a new occurrence row. Identity is a `signature` string (`value|event|market|outcome|bookmaker` or `arb|event|market`). On startup `reset_active` clears stale `active` flags from a previous process.

### Canonical data model (the contract every source maps to)

- **CanonicalEvent** — `event_id, sport, competition, home_team, away_team, start_time, status`
- **CanonicalMarket** — `market_id, event_id, market_type, period, line, settlement_scope`
- **CanonicalOutcome** — `outcome_id, market_id, outcome_type, team_reference, line`
- **OddsQuote** — `bookmaker, event_id, market_id, ..., decimal_odds, timestamp_received, source_timestamp, status, liquidity`

IDs are deterministic md5 hashes of their natural keys (see `_make_id` in the mapper), so re-ingesting the same event/market/outcome upserts in place rather than duplicating.

Normalization rules the mapper enforces and that must be preserved:
- Team-name variants (`"Olympiacos"`, `"Olympiakos Piraeus"`, `"Ολυμπιακός"`) collapse to one canonical name via `TEAM_ALIASES`.
- Market-name variants (`"Match Odds"`, `"1x2"`, `"ml"`, `"Match Result"`) map to `MarketType` via `MARKET_NAME_MAP`. An unmapped market name is **skipped silently** — extend the map to support a new market.
- Over/Under lines are part of the market and outcome ID, so `Over 2.5` and `Over 2.75` are **never** merged.

### Alert logic

Two detectors run today (see "Spec vs. implementation" for the missing third):

1. **Value** (`find_value`) — an outcome priced higher than the reference bookmaker by ≥ `min_edge`. Edge = `bookmaker_odds / reference_odds - 1`.
2. **Arbitrage** (`find_arbitrage`) — `1X2` only: best odds per outcome across bookmakers satisfy `Σ(1/odds) < 1`.

`AlertEngine` gates raw opportunities so alerts don't fire from single unstable ticks:

| Context | Min edge | Min persistence (consecutive cycles) |
|---|---|---|
| Live | 5% (`MIN_EDGE_LIVE`) | 2 (`MIN_PERSISTENCE_LIVE`) |
| Prematch | 2.5% (`MIN_EDGE_PREMATCH`) | 3 (`MIN_PERSISTENCE_PREMATCH`) |

A 300s cooldown (`COOLDOWN_SECONDS`) suppresses repeat alerts per `(event, market, outcome, bookmaker)`. A cycle where the edge drops below threshold resets the persistence counter.

## Spec vs. implementation (important divergences)

The code is a Phase-2/3 prototype and deliberately departs from the spec. Match new work to the **code's** current shape unless explicitly asked to close one of these gaps:

- **No Betfair.** The spec frames everything as Betfair-vs-bookmaker with `fair_price = (back + lay) / 2`. The implementation has no Betfair/Stream integration — it pulls Stoiximan and Novibet from a single odds API (`api.odds-api.io/v3`) and uses **Stoiximan as a soft reference line** (`REFERENCE_BOOKMAKER` in the comparison engine). This is a known stand-in for a sharp line until Phase 6+.
- **Polling, not event-driven.** The spec wants WebSocket/push; the prototype polls every 180s because of the free-tier quota. The priority-based update intervals in the spec are not implemented.
- **Stale-line detection is not implemented.** Only value and arbitrage exist. `source_timestamp` is captured on quotes but not yet used for staleness checks.
- **Quote store is in-memory and latest-quote-only.** `OddsStore` keeps one quote per `(bookmaker, outcome_id)`; raw quotes do not persist across restarts. What *does* persist is detected **opportunities** — `HistoryStore` (SQLite) retains value/arb opportunities with first/last-seen and duration. So the spec's "Latest + Historical" is partially met at the opportunity level, not the raw-quote level.
- **Delivery: console + web dashboard.** No Telegram/Discord yet. `main.py` delivers to console (`delivery/console.py`, which reaches into `OddsStore._outcomes` / `._markets` for labels); `serve.py` delivers to the SQLite-backed FastAPI dashboard. The web `Runtime._cycle` also reaches into `OddsStore._markets` / `._outcomes` for labels.
