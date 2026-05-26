# Real-Time Odds Discrepancy Monitor

## Project Summary

A real-time odds intelligence and alerting system that:

- Uses Betfair Exchange as the primary market baseline.
- Uses a licensed odds-feed provider for local bookmakers such as Stoiximan and Novibet.
- Compares equivalent markets across bookmakers.
- Detects value discrepancies, stale lines, and arbitrage opportunities.
- Produces actionable alerts for live and prematch football markets.

The system is NOT intended to start as:

- An auto-betting platform
- A scraping framework
- An all-sports/all-markets platform
- A fully automated trading bot

The first goal is to prove:

1. Reliable market matching
2. Reliable real-time odds ingestion
3. Reliable discrepancy detection
4. Useful and actionable alerts
5. Low false-positive rate

---

# Core Concept

The system continuously monitors odds from:

## Baseline Source

### Betfair Exchange

Used as:

- Primary market efficiency reference
- Fair price estimator
- Market movement detector
- Liquidity reference

Data required:

- Back odds
- Lay odds
- Available liquidity
- Market status
- Market timestamps
- Market movement

Preferred access:

- Betfair Exchange API
- Betfair Stream API

---

## Local Bookmaker Sources

Target bookmakers:

- Stoiximan
- Novibet

Data acquisition:

- Through licensed/provider odds feeds
- NOT through direct scraping

Provider requirements:

- Live odds support
- Prematch odds support
- Stable event IDs
- Stable market IDs
- Market suspension status
- Event clock and score (preferred)
- Real-time or near-real-time updates
- Commercial/private use clarity

---

# MVP Scope

## Sports

### Included

- Football only

### Excluded

- Basketball
- Tennis
- Esports
- Other sports

---

## Event Phases

### Phase 1

- Prematch monitoring

### Phase 2

- Live monitoring

---

## Markets

### Included Markets

1. Full Time Result (1X2)
2. Over/Under 2.5 Goals
3. Both Teams To Score (BTTS)

### Excluded Markets

- Asian Handicap
- Correct Score
- Player Props
- Corners
- Cards
- First Half Markets
- Set/Game Markets

---

# System Goals

The system must:

- Detect bookmaker pricing inefficiencies
- Detect stale local bookmaker lines
- Detect arbitrage opportunities
- Operate in near real time
- Minimize false positives
- Provide actionable alerts

---

# Alert Categories

## 1. Value Discrepancy Alert

Triggered when a local bookmaker offers significantly better odds than the estimated fair market price.

### Example

Betfair fair price:

```text
2.00
```

Stoiximan price:

```text
2.14
```

Estimated edge:

```text
+7%
```

---

## 2. Stale-Line Alert

Triggered when Betfair moves aggressively but the local bookmaker has not updated.

### Example

Betfair:

```text
2.20 -> 2.02
```

Novibet:

```text
still 2.18
```

Potential interpretation:

- Local bookmaker update delay
- Stale line opportunity

---

## 3. Arbitrage Alert

Triggered when the best odds across bookmakers produce a mathematical arbitrage.

### Formula

```text
1/home_odds + 1/draw_odds + 1/away_odds < 1
```

### Example

```text
Home: 2.20
Draw: 3.60
Away: 4.20
```

Result:

```text
0.9704
```

Arbitrage margin:

```text
2.96%
```

---

# Canonical Data Model

A normalization layer is mandatory.

The system must correctly map:

- Events
- Markets
- Outcomes
- Lines
- Periods

across all bookmakers.

---

## Canonical Event

```text
CanonicalEvent
  event_id
  sport
  competition
  home_team
  away_team
  start_time
  status
```

---

## Canonical Market

```text
CanonicalMarket
  market_id
  event_id
  market_type
  period
  line
  settlement_scope
```

---

## Canonical Outcome

```text
CanonicalOutcome
  outcome_id
  market_id
  outcome_type
  team_reference
  line
```

---

## Odds Quote

```text
OddsQuote
  bookmaker
  event_id
  market_id
  outcome_id
  decimal_odds
  timestamp_received
  source_timestamp
  status
  liquidity
  raw_payload_reference
```

---

# Market Normalization Requirements

The system must correctly normalize:

## Event Naming

Examples:

```text
Olympiacos
Olympiakos Piraeus
Ολυμπιακός
```

Must map to one canonical team.

---

## Market Naming

Examples:

```text
Match Odds
Τελικό Αποτέλεσμα
Match Result
```

Must map to:

```text
football.full_time.1x2
```

---

## Outcome Mapping

Examples:

```text
Home
1
Olympiacos
```

Must map to:

```text
home_win
```

---

## Line Mapping

Examples:

```text
Over 2.5
Over 2.25
Over 2.75
```

Must NEVER be treated as identical.

---

# Betfair Reference Prices

The system should maintain multiple Betfair-derived references.

## Required References

```text
betfair_back_price
betfair_lay_price
betfair_fair_price
```

---

## Suggested Fair Price Approximation

Example:

```text
Back: 2.00
Lay: 2.04
Fair: 2.02
```

Fair price logic can evolve later.

---

# Alert Rules

## General Rules

Alerts should NOT fire from:

- Single unstable ticks
- Suspended markets
- Old/stale data
- Low liquidity markets
- Wide Betfair spreads
- Duplicate opportunities

---

## Suggested Live Thresholds

```text
Minimum edge:
  5%

Minimum persistence:
  2 consecutive checks

Maximum Betfair data age:
  2-3 seconds

Maximum provider data age:
  5-8 seconds

Minimum liquidity:
  configurable
```

---

## Suggested Prematch Thresholds

```text
Minimum edge:
  2-3%

Minimum persistence:
  2-5 minutes
```

---

# Data Freshness

The system must track:

```text
source_timestamp
received_timestamp
market_status
```

The engine must reject:

- Expired quotes
- Old quotes
- Unsynchronized quotes

---

# Scan Strategy

The system should be event-driven where possible.

---

## Preferred Approach

### Betfair

Use:

- Stream API
- Websocket-style updates

---

### Provider Feed

Use:

- Push updates if available
- Polling only when necessary

---

## Priority-Based Monitoring

### Priority 1

- Live events
- High liquidity
- Recent movement

Update interval:

```text
1-5 seconds
```

---

### Priority 2

- Stable live events

Update interval:

```text
5-15 seconds
```

---

### Priority 3

- Events starting soon

Update interval:

```text
15-30 seconds
```

---

### Priority 4

- General prematch events

Update interval:

```text
1-5 minutes
```

---

# Suggested Architecture

```text
Data Sources
  Betfair API / Stream
  Licensed Odds Provider

        ↓

Ingestion Layer
  Fetch
  Validate
  Timestamp
  Deduplicate

        ↓

Normalization Layer
  Canonical events
  Canonical markets
  Canonical outcomes

        ↓

Odds Store
  Latest quotes
  Historical quotes

        ↓

Comparison Engine
  Fair price estimation
  Edge calculations
  Arbitrage calculations

        ↓

Alert Engine
  Threshold checks
  Persistence checks
  Confidence scoring
  Cooldowns

        ↓

Delivery Layer
  Telegram
  Discord
  Dashboard
```

---

# Historical Data Requirements

The system should persist:

- Odds history
- Alert history
- Market movement history
- Opportunity duration
- Liquidity snapshots

---

## Purpose of Historical Data

Used to evaluate:

- Alert quality
- False positives
- Opportunity duration
- Provider latency
- Best-performing markets
- Best-performing bookmakers
- Threshold tuning

---

# Alert Payload Example

```text
LIVE VALUE ALERT

Sport:
  Football

Event:
  AEK Athens vs PAOK

Time:
  37', score 0-0

Market:
  Full Time Result

Outcome:
  AEK Athens

Bookmaker:
  Stoiximan

Stoiximan odds:
  2.14

Betfair reference:
  Fair 2.01
  Back 2.00
  Lay 2.04

Estimated edge:
  +6.5%

Persistence:
  3 checks / 11 seconds

Betfair liquidity:
  €2,400 near lay side

Reason:
  Betfair moved 2.13 → 2.01
  Stoiximan unchanged

Confidence:
  B+
```

---

# Provider Evaluation Checklist

Before implementation, evaluate providers using the following matrix.

## Coverage

```text
Supports Stoiximan?
Supports Novibet?
Supports live odds?
Supports prematch odds?
Supports football?
Supports Greek football?
```

---

## Data Quality

```text
Update frequency?
Latency?
Stable IDs?
Market status?
Score/time included?
Historical odds?
```

---

## Technical Access

```text
REST?
Websocket?
XML feed?
JSON API?
Polling limits?
```

---

## Commercial/Legal

```text
Commercial use allowed?
Private use allowed?
Redistribution allowed?
Monthly cost?
```

---

# Proposed Development Phases

## Phase 1 — Product Specification

Define:

- Markets
- Alert types
- Thresholds
- Canonical model
- Success criteria

---

## Phase 2 — Data Access Validation

Validate:

- Betfair API access
- Provider availability
- Stoiximan coverage
- Novibet coverage
- Live latency

---

## Phase 3 — Mapping Design

Implement:

- Event normalization
- Market normalization
- Outcome normalization

---

## Phase 4 — Alert Engine Design

Implement:

- Value calculations
- Stale-line detection
- Arbitrage detection
- Confidence scoring
- Cooldowns

---

## Phase 5 — Prototype

Build:

- Small proof of concept
- One sport
- Few events
- Few markets
- Console or Telegram alerts

---

## Phase 6 — Live Pilot

Test:

- Real live matches
- Real latency
- Real persistence
- False-positive rates
- Opportunity duration

---

## Phase 7 — Expansion

Potential future additions:

- Additional bookmakers
- Additional sports
- Additional markets
- Dashboard UI
- Historical analytics
- Stake sizing
- Auto-betting integrations
- ML-based confidence scoring

---

# Non-Goals for v1

The following are intentionally excluded from the MVP.

```text
Direct scraping
Automated betting
All sports support
All bookmaker support
Complex player props
Advanced AI models
Public SaaS platform
Mobile app
```

---

# Final MVP Definition

## Project

Real-Time Odds Discrepancy Monitor

---

## Baseline

Betfair Exchange

---

## Target Bookmakers

- Stoiximan
- Novibet

---

## Access Method

Licensed odds-feed provider

---

## Sport

Football only

---

## Markets

- Full Time 1X2
- Over/Under 2.5 Goals
- BTTS

---

## Alert Types

- Value discrepancy
- Stale-line movement
- Arbitrage candidate

---

## Delivery

Telegram alerts initially

---

## Core Success Metric

Reliable actionable alerts with low false-positive rate.

