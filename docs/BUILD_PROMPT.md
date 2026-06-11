# BETrack — Custom Bookmaker Infrastructure Build

## Your Role

You are a **senior Python backend engineer** with strong async experience (asyncio, aiohttp), data-modeling experience (Pydantic v2, SQLite), production web experience (FastAPI), and comfort with TypeScript + React (for a small frontend refactor at the end). You have practical experience reverse-engineering private web APIs and dealing with Cloudflare TLS-fingerprint bot detection (via `curl_cffi`).

You operate by the conventions in `CLAUDE.md` at the repo root. **Read it first.** Specifically:

- Minimal scope — no features, abstractions, or error handling beyond what the task requires.
- No comments unless the **why** is non-obvious.
- Don't introduce a test suite, linter, or CI unless explicitly asked.
- Don't add backwards-compatibility shims or feature flags.
- Don't write planning/decision/analysis markdown files unless explicitly asked.
- For UI work, start the dev server and verify in a browser before claiming completion.

## Mission

Replace the project's current paid-odds-feed dependency (odds-api.io) with a **custom data-ingestion infrastructure** that polls private endpoints of two Greek bookmakers (Stoiximan and Novibet) directly. Persist the data in SQLite. Drive the existing FastAPI + React dashboard from that persisted state. No detection layer (value/arb/dropping) is in scope right now — this is purely **data acquisition + display** verification.

**Sports scope: Football (Soccer), Basketball, Tennis.** No other sports.

Detection logic (`betrack/comparison/engine.py`, `betrack/alerts/engine.py`) is to be **left intact but suspended** (no calls into it). It will be re-enabled in a later phase.

## Current Codebase State (what you're working from)

The repo is a Python 3.10+ project at `c:\Users\offic\projects\betrack\`. Read `CLAUDE.md` and `real_time_odds_discrepancy_monitor_mvp_spec.md` for full context. Key existing modules:

```
betrack/
  models/canonical.py           ← Pydantic v2 models: CanonicalEvent / CanonicalMarket /
                                   CanonicalOutcome / OddsQuote / EventStatus / MarketType /
                                   OutcomeType / OddsStatus. EXTEND, don't replace.
  ingestion/client.py           ← OddsApiClient (odds-api.io). KEEP as dead code, no longer called.
  normalization/mapper.py       ← Maps odds-api.io responses. KEEP as dead code.
  store/odds_store.py           ← In-memory OddsStore (latest-quote-only). WILL BE REPLACED
                                   by a SQLite-backed store (Phase 1).
  store/history.py              ← SQLite HistoryStore for detected opportunities.
                                   KEEP, but unused until detection comes back.
  comparison/engine.py          ← find_value, find_arbitrage. Suspended; do not call.
  alerts/engine.py              ← AlertEngine. Suspended; do not call.
  delivery/console.py           ← Console output. Suspended.
  labels.py                     ← market_label / outcome_label helpers. EXTEND for new sports.
  pipeline.py                   ← run_cycle. REWRITE for the new clients.
  web/app.py                    ← FastAPI app + background poll task. UPDATE for the new pipeline.
  web/frontend/                 ← React + Vite + TS + Tailwind dashboard. UPDATE in Phase 5.
main.py                         ← Console poller. UPDATE for the new pipeline.
serve.py                        ← uvicorn entrypoint for the web dashboard.
diagnose.py                     ← One-shot scan tool (odds-api.io). LEAVE for now.
requirements.txt                ← Add curl_cffi.
.gitignore                      ← Already excludes *.db, *.har, *.log, node_modules, dist.
```

Existing dependencies: `aiohttp`, `pydantic>=2`, `python-dotenv`, `fastapi`, `uvicorn`.
**New dependency:** `curl_cffi` (already installed in dev — must be added to `requirements.txt`).

## Bookmaker API Reference (you MUST follow these verbatim)

Both bookmakers are behind Cloudflare with TLS-fingerprint bot detection. **Plain `aiohttp` / `requests` / `httpx` will return 403.** You MUST use `curl_cffi` with `impersonate="chrome"` for every request.

The user's machine has a Greek consumer IP (OTE). Both bookmakers accept Greek IPs. No VPN or proxies needed.

### Stoiximan

- **Base URL:** `https://en.stoiximan.gr`
- **List endpoint (live overview, all sports):**
  - `GET /danae-webapi/api/live/overview/0?isInit=true&includeVirtuals=true`
  - Returns the full normalized state tree (~600 KB JSON) for **all live events across all sports**.
- **Per-event endpoint (full markets for one event):**
  - `GET /danae-webapi/api/live/events/{eventId}/latest`
  - Returns ~20 KB JSON with the full state for one event (~20-30 markets, ~50-100 selections).
- **Required headers** (in addition to those `curl_cffi` impersonation already provides):
  ```
  accept: application/json, text/plain, */*
  accept-language: en-GB,en;q=0.9
  referer: https://en.stoiximan.gr/live/
  x-language: 1
  x-operator: 2
  ```
- **No cookies, no auth tokens required.**
- **Response shape (overview):**
  ```json
  {
    "version": 261490000703365,                // server watermark
    "sports": {
      "byId": {
        "FOOT": { "id": "FOOT", "sportId": 1, "name": "Soccer", ... },
        "BASK": { "id": "BASK", "sportId": 2, "name": "Basketball", ... },
        "TENN": { "id": "TENN", "sportId": 3, "name": "Tennis", ... },
        ...
      }
    },
    "zones":   { "<zoneId>": { ... } },        // regions
    "leagues": { "<leagueId>": { ... } },      // competitions
    "events":  { "<eventId>": {                // FLAT dict, key = event id (string-or-int)
      "id": 86328360,
      "zoneId": 189672,
      "leagueId": 197853,
      "sportId": "FOOT",                      // STRING code, not numeric
      "ardSportId": 1,
      "marketIdList": [ ... ],                // subset of markets in overview
      "totalMarketsAvailable": 31,            // full count
      "name": null,                            // or set for outrights
      "participants": [
        { "name": "Home Team", "isHome": true, "color": "...", "teamId": 12345 },
        { "name": "Away Team",                "color": "...", "teamId": 67890 }
      ],
      "isOutrightEvent": false,
      "isLive": true,
      "startTime": 1780057800000,             // unix MS
      "liveData": {
        "score": { "home": "1", "away": "0" },
        "clock": { "secondsSinceStart": 1724 }
      },
      "betradarMatchId": 71783730             // ← CROSS-BOOKMAKER MATCHING KEY
    }},
    "markets":    { "<marketId>": {
      "id": 2784873602,
      "name": "Match Result",
      "typeId": 1,
      "selectionIdList": [ ... ]
    }},
    "selections": { "<selectionId>": {
      "id": 9703935572,
      "name": "1",                            // or "Over 1.5" etc.
      "typeId": 1,
      "price": 2.35                           // ← DECIMAL ODDS
    }}
  }
  ```
- **Response shape (per-event):** same top-level shape but with a single `event` (dict, not nested), the full `markets` and `selections` for it.
- **Football market typeIds (verified):**
  - `1` = Match Result (1X2). Selection typeIds: `1`=Home, `2`=Draw, `3`=Away.
  - `13` = Over/Under Total Goals. Selection typeIds: `39`=Over, `40`=Under. Line is in selection `name` (e.g., `"Over 1.5"`).
  - `15` = Both Teams to Score. Selection typeIds: `43`=Yes, `44`=No.
- **Basketball / Tennis market typeIds: NOT pre-verified.** Discover them at runtime — log unknown `typeId`s and add to the mapping table iteratively. Probable Stoiximan names you'll see for these sports include (but verify): "Match Result", "Money Line", "Total Points", "Handicap", "Set Winner", "Match Winner", "Total Games", etc.

### Novibet

- **Base URL:** `https://www.novibet.gr`
- **List endpoint (live overview, per sport):**
  - `GET /spt/feed/marketviews/location/v2/{sportId}/4390/?lang=en-US&timeZ=GTB%20Standard%20Time&oddsR=1&usrGrp=GR&timestamp=0`
  - **`4390` = "live" location code.** Use it for in-play.
  - **`sportId` is bookmaker-specific:** `4324` = Soccer (verified). Other sport IDs discovered via the sports menu (see below).
- **Sports menu endpoint:**
  - `GET /spt/feed/navigation/menu/5?lang=en-US&timeZ=GTB%20Standard%20Time&oddsR=1&usrGrp=GR`
  - Returns the top-level sports list with their numeric IDs. Use this once at startup to discover the basketball and tennis sport IDs.
- **Per-event endpoint (full markets for one event):**
  - `GET /spt/feed/marketviews/event/{sportId}/{eventId}?lang=en-US&timeZ=GTB%20Standard%20Time&oddsR=1&usrGrp=GR&timestamp=0&filterAlias=`
  - Returns ~15-30 KB JSON with all market categories for one event.
- **Required headers** (in addition to `curl_cffi` impersonation):
  ```
  accept: application/json, text/plain, */*
  accept-language: en-GB,en;q=0.9
  referer: https://www.novibet.gr/en/live-betting
  x-gw-application-name: Novi
  x-gw-channel: WebPC
  x-gw-client-timezone: Europe/Athens
  x-gw-cms-key: _GR
  x-gw-country-sysname: GR
  x-gw-currency-sysname: EUR
  x-gw-domain-key: _GR
  x-gw-language-sysname: en-US
  x-gw-odds-representation: Decimal
  x-gw-original-referer: https://www.novibet.gr/stoixima
  ```
- **No cookies, no auth tokens required.**
- **Response shape (overview):** top-level is a JSON **array** of one viewbox object:
  ```json
  [{
    "totalCount": 132,
    "betViews": [                                       // one per sport in the response
      {
        "competitionContextCaption": "Soccer",
        "totalCount": 31,
        "marketCaptions": [                             // available column market types
          { "betTypeSysname": "SOCCER_MATCH_RESULT",    "marketCaption": "Full Time Result" },
          { "betTypeSysname": "SOCCER_UNDER_OVER",      "marketCaption": "Goals Over/Under" },
          { "betTypeSysname": "SOCCER_BOTH_TEAMS_TO_SCORE", "marketCaption": "Both teams to score" },
          ...
        ],
        "competitions": [
          {
            "betContextId": 237024,
            "caption": "Super League",
            "regionCaption": "China",
            "events": [
              {
                "betContextId": 46199422,                // event id
                "eventSysname": "SOCCER_MATCH",
                "path": "matches/...",
                "additionalCaptions": {
                  "competitor1": "Home Team",
                  "competitor2": "Away Team"
                },
                "liveData": {
                  "homeGoals": 2,
                  "awayGoals": 1,
                  "phaseSysname": "SOCCER_MATCH_SECOND_HALF",
                  "phaseCaption": "2H",
                  "elapsedSeconds": 4577.4,
                  "referenceTime": "2026-05-29T13:13:28.881...+00:00",  // ← USE AS source_timestamp
                  "isLive": true,
                  "sportradarMatchId": 68995188          // ← CROSS-BOOKMAKER MATCHING KEY
                },
                "markets": [                              // only featured markets in overview
                  {
                    "marketId": 1655214184,
                    "betTypeSysname": "SOCCER_MATCH_RESULT",
                    "betItems": [
                      { "id": "...", "code": "1", "caption": "1", "instanceCaption": null,
                        "price": 1.32, "oddsText": "1.32", "isAvailable": true },
                      { "id": "...", "code": "X", "caption": "X", "price": 4.0, ... },
                      { "id": "...", "code": "2", "caption": "2", "price": 16.0, ... }
                    ]
                  },
                  ...
                ]
              }
            ]
          }
        ]
      },
      { "competitionContextCaption": "Basketball", ... },
      { "competitionContextCaption": "Tennis", ... }
    ]
  }]
  ```
- **Response shape (per-event):** top-level is a dict (NOT array) describing one event:
  ```json
  {
    "betContextId": 46381259,
    "sportradarMatchId": 71732938,             // ← matching key
    "competitors": [...],
    "liveData": {...},
    "marketCategories": [
      {
        "sysname": "MAIN",
        "caption": "Βασικές",
        "items": [                              // market groups
          {
            "id": 21448,
            "sysname": "MAIN|0",
            "caption": null,
            "betViews": [                        // each = one specific market/line
              {
                "marketId": 1657569564,
                "marketSysname": "SOCCER_MATCH_RESULT",
                "betItems": [
                  { "code": "1", "caption": "Home Name", "instanceCaption": null,
                    "price": 3.45, "isAvailable": true },
                  { "code": "X", "caption": "Draw",      "instanceCaption": null,
                    "price": 2.85, "isAvailable": true },
                  { "code": "2", "caption": "Away Name", "instanceCaption": null,
                    "price": 1.65, "isAvailable": true }
                ]
              }
            ]
          },
          {
            "sysname": "MAIN|1",
            "caption": "Goals Over/Under",
            "betViews": [
              {
                "marketSysname": "SOCCER_UNDER_OVER",
                "betItems": [
                  { "code": "O", "caption": "Over 1.5",  "instanceCaption": "1.5", "price": 1.58 },
                  { "code": "U", "caption": "Under 1.5", "instanceCaption": "1.5", "price": 2.2  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
  ```
- **Soccer market `marketSysname`s (verified):**
  - `SOCCER_MATCH_RESULT` (1X2). Codes: `"1"`/`"X"`/`"2"` = Home/Draw/Away.
  - `SOCCER_UNDER_OVER` (goals total). Codes: `"O"`/`"U"`. Line in `instanceCaption` (e.g. `"1.5"`).
  - `SOCCER_BOTH_TEAMS_TO_SCORE` (BTTS). Codes likely `"Y"`/`"N"` or `"Yes"`/`"No"` — verify.
- **Basketball / Tennis sysnames: NOT pre-verified.** Expect prefixes like `BASKETBALL_*` and `TENNIS_*`. Discover and map adaptively (see "Unknown-market policy" below).

### Cross-bookmaker matching

Both bookmakers populate a Sportradar match ID for each event:
- Stoiximan: `event.betradarMatchId`
- Novibet: `event.liveData.sportradarMatchId` (also at `event.sportradarMatchId` in the per-event response)

**This is the canonical join key.** Persist it on `events.sportradar_match_id`. When you later need to compare odds across bookmakers, you join on that.

### Cloudflare TLS bypass

```python
from curl_cffi import requests as cffi_requests

# Sync (simple):
r = cffi_requests.get(url, headers={...}, impersonate="chrome")

# Async (preferred for fan-out across many events):
from curl_cffi.requests import AsyncSession
async with AsyncSession(impersonate="chrome") as s:
    r = await s.get(url, headers={...})
```

Plain `aiohttp` / `requests` / `httpx` will receive a 403 + a 1.2 MB block page from Cloudflare. Do not waste time trying.

## Architecture (what you are building)

```
┌─────────────────────────────────────────────────────────┐
│ Extractors (async pollers, one task per bookmaker)     │
│   StoiximanClient    NovibetClient                      │
└────────────────────────┬────────────────────────────────┘
                         │ raw JSON
                         ▼
┌─────────────────────────────────────────────────────────┐
│ Normalizers (per bookmaker × per sport)                │
│   stoiximan_mapper / novibet_mapper                     │
│   → CanonicalEvent / Market / Outcome / OddsQuote       │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ SQLite store (betrack.db)                              │
│   events / markets / outcomes  (reference, upsert)      │
│   quote_latest                 (one row per book×outcome)│
│   quote_history                (append on price change) │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ FastAPI (reads only)                                    │
│   /api/sports  /api/events  /api/event/{id}             │
│   /api/quote-history/{book}/{outcome_id}                │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│ React dashboard (polls /api every 5s)                  │
└─────────────────────────────────────────────────────────┘
```

### Storage schema (SQLite, `betrack.db`)

Use raw SQL via `sqlite3` (no ORM). Pattern after `betrack/store/history.py`. Schema:

```sql
CREATE TABLE IF NOT EXISTS events (
  event_id              TEXT PRIMARY KEY,           -- canonical id (hash of bookmaker+native_id, see below)
  sport                 TEXT NOT NULL,              -- 'football' | 'basketball' | 'tennis'
  competition           TEXT,
  home_team             TEXT NOT NULL,
  away_team             TEXT NOT NULL,
  start_time            TEXT NOT NULL,              -- ISO-8601 UTC
  status                TEXT NOT NULL,              -- 'live' | 'prematch' | 'settled' | 'suspended'
  sportradar_match_id   INTEGER,                    -- nullable; the cross-bookmaker key
  bookmaker_event_ids   TEXT NOT NULL,              -- JSON {"Stoiximan": "86489675", "Novibet": "46199422"}
  last_seen_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_sport_status ON events(sport, status);
CREATE INDEX IF NOT EXISTS idx_events_sr           ON events(sportradar_match_id);

CREATE TABLE IF NOT EXISTS markets (
  market_id     TEXT PRIMARY KEY,
  event_id      TEXT NOT NULL REFERENCES events(event_id),
  market_type   TEXT NOT NULL,                       -- canonical key, e.g. 'football.full_time.1x2'
  period        TEXT NOT NULL DEFAULT 'full_time',
  line          REAL,
  bookmaker     TEXT NOT NULL,                       -- markets are per-bookmaker in this store
  last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_markets_event ON markets(event_id);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id     TEXT PRIMARY KEY,
  market_id      TEXT NOT NULL REFERENCES markets(market_id),
  outcome_type   TEXT NOT NULL,                      -- canonical, e.g. 'home_win'
  team_reference TEXT,
  line           REAL,
  last_seen_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_market ON outcomes(market_id);

CREATE TABLE IF NOT EXISTS quote_latest (
  bookmaker         TEXT NOT NULL,
  outcome_id        TEXT NOT NULL REFERENCES outcomes(outcome_id),
  decimal_odds      REAL NOT NULL,
  source_timestamp  TEXT,                            -- ISO-8601, from API if available
  observed_at       TEXT NOT NULL,                   -- when we received it
  status            TEXT NOT NULL DEFAULT 'active',
  PRIMARY KEY (bookmaker, outcome_id)
);

CREATE TABLE IF NOT EXISTS quote_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  bookmaker         TEXT NOT NULL,
  outcome_id        TEXT NOT NULL,
  decimal_odds      REAL NOT NULL,
  source_timestamp  TEXT,
  observed_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qh_outcome_obs ON quote_history(outcome_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_qh_book_obs    ON quote_history(bookmaker, observed_at);
```

### Critical write rule

When a cycle produces a new `OddsQuote` for `(bookmaker, outcome_id)`:

1. `SELECT decimal_odds FROM quote_latest WHERE bookmaker=? AND outcome_id=?`.
2. If no row exists OR `decimal_odds` differs from the new price:
   - `INSERT INTO quote_history (...)`
   - `INSERT OR REPLACE INTO quote_latest (...)`
3. If price is identical:
   - `UPDATE quote_latest SET observed_at=?, source_timestamp=? WHERE ...`
   - **Do NOT** insert into `quote_history`.

This keeps `quote_history` proportional to actual price movement, not poll frequency.

### Canonical IDs

IDs in our store are **deterministic md5 hashes** of natural keys (existing `_make_id` pattern in `betrack/normalization/mapper.py`). Use the same pattern, but now factor in the bookmaker for market/outcome IDs (a Stoiximan "Match Result Home" outcome is a different row from Novibet's):

```python
def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]

# Examples:
event_id    = _make_id(sportradar_match_id_or_fallback, sport)
market_id   = _make_id(bookmaker, event_id, market_type, str(line or ""))
outcome_id  = _make_id(market_id, outcome_type, str(line or ""))
```

If `sportradar_match_id` is missing for an event, fall back to `_make_id(bookmaker, native_event_id, sport)` and log a warning. This is rare.

### Polling cadence

- **Overview calls:** every 10 seconds (1 per bookmaker per sport — so 6 calls per cycle total: 2 books × 3 sports).
- **Per-event calls:** every 30 seconds, only for events confirmed live in the most recent overview, fan out concurrent (use `asyncio.gather` with a concurrency cap of ~10).
- Use the SignalR push WebSocket on Stoiximan **only if you have time after Phase 5**. Don't invest in it yet — REST polling is fine.

### Sport identity

Internally use lowercase sport keys: `'football'`, `'basketball'`, `'tennis'`. Map per-bookmaker:

| Sport | Stoiximan `sportId` | Novibet sport id |
|---|---|---|
| Football | `"FOOT"` | `4324` (verified) |
| Basketball | `"BASK"` | discover from menu endpoint |
| Tennis | `"TENN"` | discover from menu endpoint |

### Canonical market keys to support

Extend `MarketType` enum (`betrack/models/canonical.py`) with these. Use the existing dotted-name convention. Existing football entries stay; add the rest:

```python
class MarketType(str, Enum):
    # Football
    FOOTBALL_FULL_TIME_1X2          = "football.full_time.1x2"
    FOOTBALL_FULL_TIME_OVER_UNDER   = "football.full_time.over_under"
    FOOTBALL_FULL_TIME_BTTS         = "football.full_time.btts"
    FOOTBALL_DOUBLE_CHANCE          = "football.full_time.double_chance"
    FOOTBALL_DRAW_NO_BET            = "football.full_time.draw_no_bet"
    FOOTBALL_HALFTIME_FULLTIME      = "football.halftime_fulltime"
    # Basketball
    BASKETBALL_MATCH_WINNER         = "basketball.match.winner"        # incl. OT, 2-way
    BASKETBALL_TOTAL_POINTS         = "basketball.match.total_points"
    BASKETBALL_HANDICAP             = "basketball.match.handicap"
    # Tennis
    TENNIS_MATCH_WINNER             = "tennis.match.winner"            # 2-way
    TENNIS_TOTAL_GAMES              = "tennis.match.total_games"
    TENNIS_SET_WINNER               = "tennis.set.winner"
```

Rename the existing `FULL_TIME_1X2` etc. → `FOOTBALL_FULL_TIME_1X2` etc., and update any references. (The old odds-api.io mapper uses the old names — update it too, or comment its body out since it's dead code.)

Extend `OutcomeType` enum similarly (`home_win`, `draw`, `away_win`, `over`, `under`, `btts_yes`, `btts_no` stay; add what you need for the new markets — e.g., `match_winner_home`, `match_winner_away` if you want sport-neutral two-way semantics, or reuse `home_win`/`away_win` if it doesn't cause confusion).

### Unknown-market policy

When a normalizer encounters a market `typeId` / `marketSysname` it doesn't know about:

1. **Skip it silently** (don't insert anything into the store).
2. **Log it once** per process lifetime at `INFO` level: `"unknown stoiximan market typeId=X name=Y, skipping"`. Use a `set()` of already-warned identifiers to avoid spam.

This lets the system run unattended while you grow the mapping table as new types appear.

## Phased build

Do these phases in order. After each phase, verify locally before moving to the next.

### Phase 1 — Storage refactor

**Goal:** Replace the in-memory `OddsStore` with a SQLite-backed equivalent.

1. Create `betrack/store/odds_store_sqlite.py` (don't delete the old `odds_store.py` yet — keep it for reference until Phase 5).
2. Implement the schema above in `_SCHEMA` string, run via `executescript`.
3. Public methods (mirror the existing `OddsStore` interface so the rest of the code can drop in):
   - `upsert_event(event: CanonicalEvent, *, bookmaker: str, native_event_id: str, sportradar_match_id: int | None)`
   - `upsert_market(market: CanonicalMarket, *, bookmaker: str)`
   - `upsert_outcome(outcome: CanonicalOutcome)`
   - `upsert_quote(quote: OddsQuote) -> bool`  — returns `True` if price actually changed (i.e., a history row was appended), `False` otherwise. This is the **append-on-change** logic.
   - `get_event(event_id) -> dict | None`
   - `get_events_by_sport(sport: str, *, status: str = "live") -> list[dict]`
   - `get_markets_for_event(event_id) -> list[dict]`
   - `get_outcomes_for_market(market_id) -> list[dict]`
   - `get_latest_quotes_for_outcome(outcome_id) -> list[dict]`   # all bookmakers
   - `get_quote_history(outcome_id, bookmaker, limit=200) -> list[dict]`
   - `prune_quote_history(older_than_days: int = 14) -> int`     # returns row count deleted
4. Use a `contextmanager` connection pattern (open per call, commit, close) — same as `history.py`. SQLite handles concurrent readers fine; the single writer is the poller task.
5. Smoke-test: write a tiny script that inserts a few synthetic quotes (some with same price, some with new price) and confirms `quote_history` only grows on real changes.

**Verify Phase 1 before moving on**: open SQLite in a viewer, see the tables, confirm a `quote_history` insert happens on price change and not on no-op.

### Phase 2 — Stoiximan client + football

**Goal:** Stoiximan live football odds flowing into the dashboard.

1. Add `curl_cffi>=0.7` to `requirements.txt`.
2. Create `betrack/ingestion/stoiximan.py`:
   ```python
   from curl_cffi.requests import AsyncSession

   BASE = "https://en.stoiximan.gr"
   HEADERS = {
       "accept": "application/json, text/plain, */*",
       "accept-language": "en-GB,en;q=0.9",
       "referer": f"{BASE}/live/",
       "x-language": "1",
       "x-operator": "2",
   }

   class StoiximanClient:
       async def __aenter__(self): ...   # open AsyncSession(impersonate="chrome", headers=HEADERS)
       async def __aexit__(self, *a): ...
       async def fetch_overview(self) -> dict: ...
       async def fetch_event(self, event_id: int | str) -> dict: ...
   ```
3. Create `betrack/normalization/stoiximan_mapper.py` with:
   - A football-specific mapper that, given an overview response, yields `(event, [markets], [outcomes], [quotes])` tuples for all live football events.
   - A second function that takes a per-event response and yields the same tuples for that event's full market set.
   - The market typeId table (start with `{1, 13, 15}` for football; leave a clear extension point).
   - Team-name normalization via the existing `TEAM_ALIASES` in `betrack/normalization/mapper.py` — reuse it; don't duplicate.
4. Rewrite `betrack/pipeline.py:run_cycle` to:
   - Take a list of `(client, mapper)` pairs.
   - For each pair: fetch overview → emit canonical objects → upsert → for each live event with our target markets, fetch per-event → emit canonical objects → upsert.
   - Return a `CycleResult` dataclass with counts (per bookmaker × sport: events_seen, markets_seen, quotes_observed, quotes_changed).
5. Update `betrack/web/app.py` `Runtime`:
   - Construct one `StoiximanClient` (Novibet added in Phase 3).
   - Replace the old odds-api.io call sites.
   - Drop the `select_bookmakers` startup call (no longer needed).
   - Set `POLL_INTERVAL = 30` (per-event); add a separate `OVERVIEW_INTERVAL = 10` if you want them on different cadences. Simpler: a single 30s cycle that fetches both overview and per-event details is fine to start.
   - Keep `HistoryStore` constructed (idle).
6. Update `main.py` similarly for the console mode.
7. Run `python serve.py`, navigate to `http://127.0.0.1:8000`, confirm:
   - `/api/status` shows new counters.
   - `betrack.db` has rows in `events`, `markets`, `outcomes`, `quote_latest`, and (after a few cycles where prices changed) `quote_history`.
   - The existing dashboard still loads (it'll be stale visually until Phase 5, but the backend should be healthy).

**Verify Phase 2 before moving on**: confirm Stoiximan football events appear with real prices in `quote_latest`. Spot-check 2-3 against `stoiximan.gr` in a browser. Prices should match within the latency of one poll cycle.

### Phase 3 — Novibet client + football

**Goal:** Novibet live football odds flowing into the same store, joined to Stoiximan via `sportradar_match_id`.

1. Create `betrack/ingestion/novibet.py`:
   ```python
   BASE = "https://www.novibet.gr"
   HEADERS = {
       "accept": "application/json, text/plain, */*",
       "accept-language": "en-GB,en;q=0.9",
       "referer": f"{BASE}/en/live-betting",
       "x-gw-application-name": "Novi",
       "x-gw-channel": "WebPC",
       "x-gw-client-timezone": "Europe/Athens",
       "x-gw-cms-key": "_GR",
       "x-gw-country-sysname": "GR",
       "x-gw-currency-sysname": "EUR",
       "x-gw-domain-key": "_GR",
       "x-gw-language-sysname": "en-US",
       "x-gw-odds-representation": "Decimal",
       "x-gw-original-referer": f"{BASE}/stoixima",
   }

   class NovibetClient:
       async def __aenter__(self): ...
       async def __aexit__(self, *a): ...
       async def fetch_sports_menu(self) -> dict: ...
       async def fetch_overview(self, sport_id: int) -> list: ...   # top-level is a list
       async def fetch_event(self, sport_id: int, event_id: int) -> dict: ...
   ```
2. Create `betrack/normalization/novibet_mapper.py`. Key points:
   - Overview top level is a 1-element list; the betViews live at `data[0]["betViews"]`.
   - For football, filter to `competitionContextCaption == "Soccer"`.
   - Walk competitions → events → markets → betItems.
   - Use Novibet's `sportradarMatchId` (via `event.liveData.sportradarMatchId` in overview, or `event.sportradarMatchId` in per-event) for the cross-bookmaker key.
   - For Over/Under: line is in `betItem.instanceCaption`. For 1X2 / BTTS: no line.
   - In the per-event endpoint, markets are nested two levels deeper: `marketCategories[].items[].betViews[]`.
3. Add `NovibetClient`/`novibet_mapper` to the `run_cycle` client list. Now you're polling both.
4. Verify in the DB: events with the same `sportradar_match_id` have two rows in `quote_latest` per outcome (one per bookmaker). Pick a match that's live on both books (Premier-tier matches usually qualify) and spot-check.

**Verify Phase 3 before moving on**: open `betrack.db` and run
```sql
SELECT events.home_team, events.away_team, events.sportradar_match_id,
       COUNT(DISTINCT quote_latest.bookmaker) AS books
FROM events JOIN markets ON markets.event_id = events.event_id
JOIN outcomes ON outcomes.market_id = markets.market_id
JOIN quote_latest ON quote_latest.outcome_id = outcomes.outcome_id
WHERE events.sport = 'football'
GROUP BY events.event_id
HAVING books = 2
LIMIT 5;
```
Expect at least 1-2 matches with both books represented (when live football is on both — may be quiet during EU off-season).

### Phase 4 — Extend to basketball + tennis

**Goal:** Same pipeline, three sports.

1. Discover basketball + tennis numeric sport IDs on Novibet via `fetch_sports_menu`. Hardcode the resulting IDs in `betrack/ingestion/novibet.py` as constants (e.g., `NOVIBET_SPORT_IDS = {"football": 4324, "basketball": ..., "tennis": ...}`).
2. For Stoiximan, the sport key is in the response — just filter `events` by `sportId in {"FOOT", "BASK", "TENN"}`.
3. Extend `MarketType` enum as listed above. Extend the per-bookmaker mapping tables with at least the **main market** of each new sport:
   - Basketball: Match Winner (2-way, includes OT) → `BASKETBALL_MATCH_WINNER`; Total Points → `BASKETBALL_TOTAL_POINTS`; Handicap → `BASKETBALL_HANDICAP`.
   - Tennis: Match Winner (2-way) → `TENNIS_MATCH_WINNER`; Total Games → `TENNIS_TOTAL_GAMES`; Set Winner → `TENNIS_SET_WINNER`.
4. **Unknown markets are silently skipped (with a one-time INFO log per type).** Do not crash on a typeId you haven't mapped.
5. Extend `betrack/labels.py` with friendly names for the new market types and outcome types.
6. Run, confirm three sports produce rows in `events` with the right `sport` column.

**Verify Phase 4**:
```sql
SELECT sport, COUNT(*) FROM events WHERE status='live' GROUP BY sport;
```
Expect three sports with nonzero counts (subject to live-event availability).

### Phase 5 — UI refactor

**Goal:** Dashboard surfaces the new data shape (live events grouped by sport, both books' odds side by side, freshness ages, optional odds-movement sparkline from `quote_history`).

1. New / updated FastAPI endpoints (in `betrack/web/app.py`):
   - `GET /api/sports` → `[{key: 'football', label: 'Football', live_count: N}, ...]`
   - `GET /api/events?sport=football` → array of events with team names, competition, status, both books' top-line odds (1X2/Match Winner), freshness ages.
   - `GET /api/event/{event_id}` → full breakdown: all markets, both books' odds, source_timestamps, ages.
   - `GET /api/quote-history/{outcome_id}?bookmaker=Stoiximan&limit=200` → time-series of price changes for sparklines.
   - Keep `/api/status` updated to reflect the new shape.
   - **Remove or empty out** `/api/opportunities` and `/api/history` for now (return `[]`) — they were for detection.
2. Frontend refactor in `betrack/web/frontend/src/`:
   - Replace the **Live / History** tabs with **Football / Basketball / Tennis** tabs (driven by `/api/sports`).
   - Replace the opportunity-row table with an **event row table**: per row, show home vs away, competition, status (live + clock), and the headline market for that sport (1X2 for football, Match Winner for the others) with both books' decimal odds side by side and a freshness badge per book.
   - Click a row → existing `EventDrawer` shows ALL markets for that event with both books' prices in a grid.
   - In the drawer, add a small line-chart sparkline per outcome showing the last ~30 minutes of `quote_history` for that bookmaker (basic SVG path is fine — no chart library). Optional but nice; can defer.
   - Drop the filter for `kind=value|arb`; keep filters for **bookmaker** (Both / Stoiximan only / Novibet only) and **min freshness** if useful.
3. **Tailwind v4** is already set up. Keep the dark slate aesthetic. Don't introduce new design systems.
4. **TypeScript strictness** is already configured (`tsconfig.json` — strict on, `noUnusedLocals/Params` off). The build runs `tsc --noEmit && vite build`. Don't change the build pipeline.
5. After each meaningful change: `npm run build` in `betrack/web/frontend/`, then `python serve.py`, then verify in a browser. **Take a screenshot to confirm the rendering** before claiming completion.

## Style and conventions (from CLAUDE.md)

- Python 3.10+, type hints throughout.
- Pydantic v2 (already in use). Don't add validators unless behavior depends on it.
- `asyncio` + `aiohttp` for the async event loop; `curl_cffi.requests.AsyncSession` for outbound HTTP to bookmakers.
- SQLite via `sqlite3` stdlib. No SQLAlchemy.
- No retry libraries (`tenacity`, etc.) unless transient failures actually warrant it. A simple try/except around a cycle and a `logger.warning` is fine.
- No structured logging libraries. Stdlib `logging` configured at module load.
- No comments unless the **why** is non-obvious. Names should carry the meaning.
- Don't write planning, decision, or analysis markdown files. (This file is the only one; do not write more.)
- Don't add tests / linters / CI unless explicitly requested.
- Don't create files in the repo root unless they are entry points (`main.py`, `serve.py`, `diagnose.py`). New modules go under `betrack/`.

## What NOT to do (anti-mess-up checklist)

- **Do NOT** use `aiohttp` / `requests` / `httpx` for Stoiximan or Novibet calls. Cloudflare WILL return 403. Use `curl_cffi` with `impersonate="chrome"`.
- **Do NOT** delete `OddsApiClient`, `mapper.py` (the odds-api.io one), `comparison/engine.py`, `alerts/engine.py`, `delivery/console.py`, or `HistoryStore`. Leave them in place, unused. They will return in a later phase.
- **Do NOT** rename or restructure existing Pydantic models in ways that break their JSON shape. **Extend** the `MarketType`/`OutcomeType` enums; don't recreate them.
- **Do NOT** insert into `quote_history` on every cycle. **Append only when the price has actually changed** (existing-vs-new comparison). This is critical for performance and storage scale.
- **Do NOT** use `Date.now()` / `Math.random()` / argless `new Date()` in any Vite workflow scripts — see the harness rules. (This won't apply to runtime React code, only to anything driven by a workflow tool.)
- **Do NOT** invent endpoints. If you need a new endpoint that wasn't captured in the HAR samples, **stop and ask**.
- **Do NOT** add a VPN, proxy, or scraping framework. The Greek IP works fine for both bookmakers with `curl_cffi`.
- **Do NOT** call the detection layer (`find_value`, `find_arbitrage`, `AlertEngine`, `delivery/console`). They're suspended.
- **Do NOT** rebuild the dashboard CSS framework or change the React stack. Tailwind v4 + Vite + TS is locked in.
- **Do NOT** create tests, GitHub Actions, or linter configs.
- **Do NOT** make the polling cadence faster than 5s on any endpoint. Be polite to the bookmakers' infrastructure.
- **Do NOT** persist sensitive headers (e.g., Sentry trace IDs) to the DB. Only what's needed.
- **Do NOT** write multi-paragraph docstrings. One-line comment max, only if the why isn't obvious.
- **Do NOT** silently drop data when you encounter unknown market types. **Log it once at INFO** (with the unknown identifier), then skip. The user wants to know what they're missing.

## Final deliverable

After all five phases:

- `python serve.py` launches the dashboard.
- Dashboard shows three sport tabs (Football / Basketball / Tennis), each with a table of live events from Stoiximan + Novibet.
- Each event row shows the headline market with both books' decimal odds and a freshness age badge.
- Clicking a row opens a drawer with all markets for that event, both books side by side.
- `betrack.db` grows with `quote_history` only as prices actually change.
- `/api/status` reports: per-bookmaker × per-sport event counts, last cycle time, total quotes observed and total quotes changed in the latest cycle.
- No 403s, no Cloudflare blocks, no crashes when an unknown market type appears.
- `requirements.txt` includes `curl_cffi`.
- `CLAUDE.md` is updated to reflect the new architecture (new commands if any, the bookmaker clients, the SQLite tables, the suspended detection layer).

When everything in the deliverable list works, the build is done.

## If anything is ambiguous

If you hit a question this document doesn't answer — about a market typeId, about how an endpoint behaves, about a UI detail — **stop and ask the user** rather than guessing. The cost of pausing is low; the cost of building on a wrong assumption is high.

End of brief.
