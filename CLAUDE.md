# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Status

**Pre-implementation.** The repository currently contains only the MVP specification ([real_time_odds_discrepancy_monitor_mvp_spec.md](real_time_odds_discrepancy_monitor_mvp_spec.md)). No build system, package.json, or source code exists yet. Build/test/lint commands will be added here once implementation begins.

## What This Project Is

**BETrack** — a real-time odds discrepancy monitor for sports betting. It compares Betfair Exchange odds against licensed feeds for Stoiximan and Novibet, detecting value discrepancies, stale lines, and arbitrage opportunities. MVP scope: football only, markets 1X2 / Over-Under 2.5 / BTTS.

This is **not** an auto-betting platform or scraping framework. Data from local bookmakers must come from licensed odds-feed providers only.

## Planned Architecture

```
Data Sources (Betfair API/Stream + Licensed Odds Provider)
    ↓
Ingestion Layer (Fetch, Validate, Timestamp, Deduplicate)
    ↓
Normalization Layer (Canonical events, markets, outcomes)
    ↓
Odds Store (Latest + Historical quotes)
    ↓
Comparison Engine (Fair price estimation, Edge calc, Arbitrage calc)
    ↓
Alert Engine (Threshold checks, Persistence checks, Confidence scoring, Cooldowns)
    ↓
Delivery Layer (Telegram initially, then Discord + Dashboard)
```

The system must be **event-driven** where possible. Betfair should use Stream API (WebSocket). Provider feeds should use push updates; fall back to polling only when necessary.

## Canonical Data Model

The normalization layer is mandatory — all sources must map to these canonical structures before comparison:

- **CanonicalEvent:** `event_id, sport, competition, home_team, away_team, start_time, status`
- **CanonicalMarket:** `market_id, event_id, market_type, period, line, settlement_scope`
- **CanonicalOutcome:** `outcome_id, market_id, outcome_type, team_reference, line`
- **OddsQuote:** `bookmaker, event_id, market_id, outcome_id, decimal_odds, timestamp_received, source_timestamp, status, liquidity, raw_payload_reference`

Critical normalization rules:
- Team names like `"Olympiacos"`, `"Olympiakos Piraeus"`, `"Ολυμπιακός"` must map to one canonical team.
- Market names like `"Match Odds"`, `"Τελικό Αποτέλεσμα"`, `"Match Result"` must all map to `football.full_time.1x2`.
- Lines `Over 2.5`, `Over 2.25`, `Over 2.75` must **never** be treated as identical.

## Alert Logic

Three alert types:

1. **Value Discrepancy** — bookmaker odds significantly exceed Betfair fair price. Fair price = `(back + lay) / 2` as starting approximation.
2. **Stale-Line** — Betfair moves aggressively but local bookmaker hasn't updated.
3. **Arbitrage** — best odds across bookmakers satisfy `1/home + 1/draw + 1/away < 1`.

Alerts must **not** fire from single unstable ticks, suspended markets, stale/expired data, low-liquidity markets, or wide Betfair spreads.

### Thresholds

| Context | Min Edge | Min Persistence | Max Betfair data age | Max provider data age |
|---|---|---|---|---|
| Live | 5% | 2 consecutive checks | 2–3 seconds | 5–8 seconds |
| Prematch | 2–3% | 2–5 minutes | — | — |

### Priority-based update intervals

| Priority | Context | Interval |
|---|---|---|
| 1 | Live, high liquidity, recent movement | 1–5 s |
| 2 | Stable live | 5–15 s |
| 3 | Starting soon | 15–30 s |
| 4 | General prematch | 1–5 min |

## Development Phases

- **Phase 1 (done):** Product specification
- **Phase 2:** Validate Betfair API access + provider availability (Stoiximan/Novibet coverage, live latency)
- **Phase 3:** Implement normalization layer (event/market/outcome mapping)
- **Phase 4:** Implement Alert Engine (value calc, stale-line detection, arbitrage, confidence scoring, cooldowns)
- **Phase 5:** Prototype — console or Telegram alerts, one sport, few events
- **Phase 6:** Live pilot — measure real latency, persistence, false-positive rates
- **Phase 7:** Expansion (more bookmakers, sports, dashboard, analytics)
