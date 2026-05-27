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

python main.py                         # run the continuous poller (Ctrl-C to stop)
python diagnose.py                     # one-shot scan: print every comparable outcome + its edge
```

`API_KEY` is read from the environment / `.env` at startup; both entry points raise immediately if it is missing. There is **no test suite, linter, or build step** yet — do not claim one exists. (The `init tests` commit, despite its name, added the package, not tests.) When adding tests, also add the runner command here.

## Architecture

The pipeline is a synchronous fan-through driven by an async poll loop in [main.py](main.py):

```
OddsApiClient (ingestion)  →  map_event / map_odds (normalization)
    →  OddsStore (in-memory)  →  find_value / find_arbitrage (comparison)
    →  AlertEngine (persistence + cooldown gating)  →  console delivery
```

One package, one module per stage — [betrack/ingestion/client.py](betrack/ingestion/client.py), [betrack/normalization/mapper.py](betrack/normalization/mapper.py), [betrack/store/odds_store.py](betrack/store/odds_store.py), [betrack/comparison/engine.py](betrack/comparison/engine.py), [betrack/alerts/engine.py](betrack/alerts/engine.py), [betrack/delivery/console.py](betrack/delivery/console.py). All canonical types live in [betrack/models/canonical.py](betrack/models/canonical.py) as pydantic models / str-enums.

Each poll cycle (`POLL_INTERVAL = 180s`, capped at `MAX_EVENTS_PER_CYCLE = 5` to stay under the free-tier 100 req/hr quota): fetch live + prematch events, fetch odds per event, normalize, upsert into the store, then run value and arbitrage comparison and gate the results through the alert engine.

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
- **Store is in-memory and latest-quote-only.** `OddsStore` keeps one quote per `(bookmaker, outcome_id)`; there is no historical-quote retention despite the spec's "Latest + Historical" store. Nothing persists across process restarts.
- **Delivery is console-only.** No Telegram/Discord yet. `delivery/console.py` reaches into `OddsStore._outcomes` / `._markets` directly for labels.
