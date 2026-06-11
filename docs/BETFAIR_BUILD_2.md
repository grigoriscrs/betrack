# BETrack — Betfair Phase 2: End-to-End Wiring

A focused build brief that picks up where [BETFAIR_BUILD.md](BETFAIR_BUILD.md) (Phase 1) left off. Phase 1 delivered the standalone `BetfairClient` ingestion module. Phase 2 wires Betfair through every other layer: canonical model, SQLite store, a new `betfair_mapper.py`, and the `run_cycle` pipeline. After this build, Betfair appears as a third bookmaker column in the existing per-event drawer with zero UI code changes.

Read [CLAUDE.md](CLAUDE.md) and [BETFAIR_BUILD.md](BETFAIR_BUILD.md) first. Conventions are unchanged: minimal scope, no comments unless the *why* is non-obvious, no test suite / lint / CI, no error handling for cases that can't happen.

---

## 1. Mission

### What Phase 2 adds

Betfair Exchange becomes a fully integrated third bookmaker alongside Stoiximan and Novibet, flowing through the existing pipeline:

```
BetfairClient.fetch_*  →  betfair_mapper.map_*  →  SqliteOddsStore.write_bundles  →  /api/event/{id}
```

Concretely:

1. **Canonical model** gains 8 optional fields on `OddsQuote` (7 order-book depth fields + 1 cumulative-volume `total_matched`; all Betfair-only) and 4 optional fields on `CanonicalMarket` (Betfair-only market metadata).
2. **SQLite store** gains the same columns on `quote_latest`, `quote_history`, and `markets`, plus a runtime `PRAGMA table_info` migration so existing dev databases auto-upgrade without `rm betrack.db`.
3. **New module** `betrack/normalization/betfair_mapper.py` exposes `map_overview(...) -> list[MappedEvent]` (no-op stub for Phase 2), `map_event_detail(...) -> list[MappedEvent]` (returns a list because Betfair's bymarket batches across events), and `live_event_ids(...)`. Shape mirrors Stoiximan/Novibet mappers so the pipeline integrates with a single extra block.
4. **Pipeline** (`betrack/pipeline.py`, `run_cycle`) gains a Betfair block. Per (client × sport) failure isolation via `asyncio.gather(return_exceptions=True)` so that a Betfair-football fault doesn't kill Betfair-tennis, and a Betfair fault doesn't kill Stoiximan/Novibet.
5. **Runtime** (`betrack/web/app.py`) constructs `BetfairClient()` with no args (it reads `BETRACK_BETFAIR_PROXY` itself, per Phase 1) and runs it through the same `__aenter__` / `__aexit__` lifecycle as the other two clients.

### What Phase 1 already delivered (do NOT redo)

- `betrack/ingestion/betfair.py` with `BetfairClient.list_in_play(...)`, `fetch_markets(...)`, `fetch_event_markets(...)`, `fetch_scores(...)`.
- Async context manager, `curl_cffi` Chrome impersonation, `BETRACK_BETFAIR_PROXY` env-var wired into `__init__`.
- The Betfair endpoint quirks (`_ak` empty for `scan-inbf`, omitted elsewhere; required headers; `eventTypeId` constants 1/2/7522).

### Deferred to a future phase (explicitly out of scope here)

- **Cross-bookmaker matching.** Betfair has no Sportradar ID. Fuzzy match by team names + start time lands later. For now, Betfair events live in a separate `event_id` namespace and never collapse with Stoix/Novi rows.
- **Live scores / clock** (`ips.betfair.com/scoresAndBroadcast`). The drawer will show Betfair odds without score/period context until cross-matching arrives.
- **Strategy / reference layer** (commission-adjusted back/lay midpoint as the "true" probability). The exchange fields persist to SQLite but aren't yet consumed.
- **Full order-book ladder.** We capture only the top 2 levels per side (best + second-best). Deeper depth is a future migration.
- **Polling cadence tuning.** Betfair piggybacks the existing 30-second `POLL_INTERVAL`.
- **Running BetfairClient on a separate UK host** (cross-host coordination). The local SSH SOCKS tunnel (Pattern 2) remains the dev workflow.

---

## 2. Locked-in decisions

| # | Decision | One-liner |
|---|---|---|
| 1 | Failure handling | Pipeline logs warning + skips Betfair on failure; Stoix/Novi keep flowing. Within Betfair, per (client × sport) isolation via `asyncio.gather(return_exceptions=True)`. |
| 2 | Order-book depth | Best back/lay + 2nd-best back/lay. First-level back price reuses existing `OddsQuote.decimal_odds`. 7 new depth fields (`back_size`, `lay_price`, `lay_size`, `back_price_2`, `back_size_2`, `lay_price_2`, `lay_size_2`) + 1 cumulative-volume field (`total_matched`, per-runner) = 8 new optional fields total. |
| 3 | Sports scope | Football + Basketball + Tennis (`eventTypeId` 1, 7522, 2) from day one. |
| 4 | Live scores | Skipped. `ips.betfair.com/scoresAndBroadcast` not wired until cross-matching lands. |
| 5 | Market metadata | 4 new fields on `CanonicalMarket`: `bet_delay`, `commission_rate` (Betfair's 5.0 → 0.05 via `/100`), `total_available`, `last_match_time`. |
| 6 | Market-type coverage | Full canonical set per sport (see Section 6.6 mapping table). **`FOOTBALL_HALFTIME_FULLTIME` is explicitly DEFERRED** (added to `_DEFERRED_MARKET_TYPES`, skipped silently — `OutcomeType` enum has no HT/FT members, extending it is out of scope this build). Other unknown types: log once at INFO, skip silently — same policy as Stoix/Novi mappers. |
| 7 | Filter policy | Strict: include market ONLY IF `marketNode.state.inplay == true` AND `event.openDate` within ±3 h of `now`. Drops ante-post outrights and future fixtures. **Filtering happens at mapper time only** — `live_event_ids` does not pre-filter by openDate (we don't have it until `byevent` returns), so wasted `fetch_event_markets` / `fetch_markets` calls on outrights are accepted. The ±3 h window in the mapper drops them before any DB write. Acceptable cost: at most ~1 extra fetch per outright eventId returned by `list_in_play`. |
| 8 | Cross-bookmaker matching | Deferred. `event_id = _make_id("Betfair", native_event_id, sport)` — separate namespace. |
| 9 | Bookmaker name | Literal `"Betfair"` in `OddsQuote.bookmaker`, all SQLite rows, UI labels. `BOOKMAKERS = ["Stoiximan", "Novibet", "Betfair"]` in `betrack/web/app.py`. |
| 10 | UI changes | Zero. Drawer already renders N bookmakers per outcome; Betfair shows up as a third column when present. Exchange fields persist but aren't surfaced. |
| 11 | SOCKS5 proxy | `BetfairClient.__init__` reads `BETRACK_BETFAIR_PROXY` itself (Phase 1 work; do not redo). Constructor signature: `BetfairClient(proxy: str | None = None)` — when `proxy` is None, falls back to the env var; when the env var is unset, runs with no proxy (Greek IP — Betfair will 403). Pipeline always calls `BetfairClient()` with no args. **Use `socks5h://` not `socks5://`** — DNS goes through the tunnel; the `h` is mandatory or `betfair.com` resolves on the Greek host and leaks. Dead tunnel / no env var → per-cycle warning + skip, the dashboard still starts cleanly (the lifecycle `__aenter__` does not require an active proxy; only the per-cycle fetches will fail). |

---

## 3. File map

### New files

| Path | Purpose |
|---|---|
| `betrack/normalization/betfair_mapper.py` | Maps Betfair `scan-inbf` / `ero` JSON to `MappedEvent` bundles. Mirrors `stoiximan_mapper.py` shape. |

### Modified files

| Path | Change |
|---|---|
| `betrack/models/canonical.py` | Add 8 optional fields to `OddsQuote` (7 depth + `total_matched`); add 4 optional fields to `CanonicalMarket`. |
| `betrack/store/odds_store_sqlite.py` | Extend `_SCHEMA` (new columns on `quote_latest`, `quote_history`, `markets`); add `_NEW_COLUMNS` + `_migrate()` invoked in `__init__`; full-body replacement of `_quote_in()` and `_market_in()` to bind the new fields. |
| `betrack/pipeline.py` | Add `_extract_market_ids` + `_chunked` module helpers; add `betfair: BetfairClient | None` parameter to `run_cycle`; add Betfair block to body with per (sport) `asyncio.gather(return_exceptions=True)`. |
| `betrack/web/app.py` | `BOOKMAKERS += ["Betfair"]`. `Runtime.__init__` constructs `BetfairClient()`; `start()` / `stop()` add a third `__aenter__` / `__aexit__`; `_loop()` passes the new arg to `run_cycle`. |
| `main.py` | Add `BetfairClient` import; extend the `async with` chain; pass `betfair` as third positional arg to `run_cycle`. |
| `betrack/normalization/mapper.py` | **Only if** a Betfair runner name surfaces a team spelling not yet in `TEAM_ALIASES` (e.g. `"Man Utd"` → `"Manchester United"`). Extend the dict in place; do **not** create a new aliases file. If no new alias is needed, this file is unchanged. |
| `BETFAIR_BUILD_2.md` | This document. |

### Unchanged (cite — do NOT modify)

- `betrack/ingestion/betfair.py` (Phase 1 product; treat as frozen API).
- `betrack/normalization/stoiximan_mapper.py`, `betrack/normalization/novibet_mapper.py` (reference; don't touch).
- `betrack/normalization/bundle.py` (the `MappedEvent` dataclass — but **read it**: Betfair mapper must construct `MappedEvent` with `bookmaker="Betfair"`, `native_event_id=str(...)`, `sportradar_match_id=None`).
- `betrack/web/frontend/**` (UI is unchanged this build — see Section 9).

---

## 4. Canonical model changes

The discovery captured the current shape:

```python
class OddsQuote(BaseModel):
    bookmaker: str
    event_id: str
    market_id: str
    outcome_id: str
    decimal_odds: float
    timestamp_received: datetime
    source_timestamp: Optional[datetime] = None
    status: OddsStatus = OddsStatus.ACTIVE
    liquidity: Optional[float] = None
    raw_payload_reference: Optional[str] = None

class CanonicalMarket(BaseModel):
    market_id: str
    event_id: str
    market_type: MarketType
    period: str = "full_time"
    line: Optional[float] = None
    settlement_scope: str = "full_time"
```

No validators, aliases, discriminators, or computed properties exist on either model. The discovery findings explicitly confirm: *"All proposed OddsQuote fields ... can be safely added as Optional[float] = None"* and *"All proposed CanonicalMarket fields ... can be safely added as Optional[float] or Optional[datetime]"*.

### After: `OddsQuote` (additions only; existing fields unchanged)

```python
class OddsQuote(BaseModel):
    bookmaker: str
    event_id: str
    market_id: str
    outcome_id: str
    decimal_odds: float                     # = best back price for Betfair (level 1)
    timestamp_received: datetime
    source_timestamp: Optional[datetime] = None
    status: OddsStatus = OddsStatus.ACTIVE
    liquidity: Optional[float] = None
    raw_payload_reference: Optional[str] = None

    # --- Betfair-only exchange fields (None for Stoiximan/Novibet) ---
    back_size: Optional[float] = None       # availableToBack[0].size
    lay_price: Optional[float] = None       # availableToLay[0].price
    lay_size: Optional[float] = None        # availableToLay[0].size
    back_price_2: Optional[float] = None    # availableToBack[1].price
    back_size_2: Optional[float] = None     # availableToBack[1].size
    lay_price_2: Optional[float] = None     # availableToLay[1].price
    lay_size_2: Optional[float] = None      # availableToLay[1].size
    total_matched: Optional[float] = None   # runner.state.totalMatched (per-runner volume)
```

### After: `CanonicalMarket` (additions only)

```python
class CanonicalMarket(BaseModel):
    market_id: str
    event_id: str
    market_type: MarketType
    period: str = "full_time"
    line: Optional[float] = None
    settlement_scope: str = "full_time"

    # --- Betfair-only market metadata (None for Stoiximan/Novibet) ---
    commission_rate: Optional[float] = None     # rates.marketBaseRate / 100 (5.0 → 0.05)
    bet_delay: Optional[int] = None             # state.betDelay (seconds; 0 outside in-play)
    total_available: Optional[float] = None     # state.totalAvailable (unmatched £)
    last_match_time: Optional[datetime] = None  # state.lastMatchTime (ISO 8601 UTC)
```

Notes:

- **Backward compatibility is total.** Every existing call site that builds an `OddsQuote` or `CanonicalMarket` continues to work — the new fields default to `None`. Stoiximan and Novibet mappers leave them unset.
- **`decimal_odds` semantics for Betfair = best back price.** This keeps the "what does this book offer you?" framing consistent across all three bookmakers in the existing dashboard. The lay side is captured separately.
- **Type for `bet_delay`.** Betfair returns an integer (`betDelay: 0`); use `Optional[int]`. The other three are `Optional[float]` / `Optional[datetime]`.

---

## 5. Storage schema changes

The discovery captured `_SCHEMA` as `CREATE TABLE IF NOT EXISTS` blocks that are idempotent but **do not** auto-migrate. Existing columns cannot be altered; new columns need explicit `ALTER TABLE ADD COLUMN`.

### 5.1 Extended `CREATE TABLE IF NOT EXISTS` in `_SCHEMA`

For fresh databases (post-Phase 2). Replace the three table definitions:

```sql
CREATE TABLE IF NOT EXISTS markets (
  market_id        TEXT PRIMARY KEY,
  event_id         TEXT NOT NULL REFERENCES events(event_id),
  market_type      TEXT NOT NULL,
  period           TEXT NOT NULL DEFAULT 'full_time',
  line             REAL,
  bookmaker        TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  -- Betfair-only metadata; NULL for Stoiximan / Novibet rows
  commission_rate  REAL,
  bet_delay        INTEGER,
  total_available  REAL,
  last_match_time  TEXT
);

CREATE TABLE IF NOT EXISTS quote_latest (
  bookmaker         TEXT NOT NULL,
  outcome_id        TEXT NOT NULL REFERENCES outcomes(outcome_id),
  decimal_odds      REAL NOT NULL,
  source_timestamp  TEXT,
  observed_at       TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'active',
  -- Betfair-only exchange fields; NULL for Stoiximan / Novibet rows
  back_size         REAL,
  lay_price         REAL,
  lay_size          REAL,
  back_price_2      REAL,
  back_size_2       REAL,
  lay_price_2       REAL,
  lay_size_2        REAL,
  total_matched     REAL,
  PRIMARY KEY (bookmaker, outcome_id)
);

CREATE TABLE IF NOT EXISTS quote_history (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  bookmaker         TEXT NOT NULL,
  outcome_id        TEXT NOT NULL,
  decimal_odds      REAL NOT NULL,
  source_timestamp  TEXT,
  observed_at       TEXT NOT NULL,
  -- Same exchange fields as quote_latest; captured per history row
  back_size         REAL,
  lay_price         REAL,
  lay_size          REAL,
  back_price_2      REAL,
  back_size_2       REAL,
  lay_price_2       REAL,
  lay_size_2        REAL,
  total_matched     REAL
);
```

### 5.2 Runtime migration via `PRAGMA table_info`

For existing dev databases that already have these tables without the new columns. Add a `_migrate(conn)` helper invoked once at `__init__` time, right after `conn.executescript(_SCHEMA)` (per the discovery, line 88 of `odds_store_sqlite.py`):

```python
_NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "markets": [
        ("commission_rate", "REAL"),
        ("bet_delay", "INTEGER"),
        ("total_available", "REAL"),
        ("last_match_time", "TEXT"),
    ],
    "quote_latest": [
        ("back_size", "REAL"),
        ("lay_price", "REAL"),
        ("lay_size", "REAL"),
        ("back_price_2", "REAL"),
        ("back_size_2", "REAL"),
        ("lay_price_2", "REAL"),
        ("lay_size_2", "REAL"),
        ("total_matched", "REAL"),
    ],
    "quote_history": [
        ("back_size", "REAL"),
        ("lay_price", "REAL"),
        ("lay_size", "REAL"),
        ("back_price_2", "REAL"),
        ("back_size_2", "REAL"),
        ("lay_price_2", "REAL"),
        ("lay_size_2", "REAL"),
        ("total_matched", "REAL"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _NEW_COLUMNS.items():
        info_rows = list(conn.execute(f"PRAGMA table_info({table})"))
        # Belt-and-braces: PRAGMA table_info on a non-existent table returns 0 rows,
        # which would silently make every ALTER below succeed for a typo'd table name.
        assert info_rows, f"_migrate: table {table!r} missing — schema executescript did not run"
        existing = {row["name"] for row in info_rows}
        for col_name, col_type in cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    conn.commit()
```

**Invocation site**: in `SqliteOddsStore.__init__`, the existing body is:

```python
with self._connect() as conn:
    conn.executescript(_SCHEMA)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
```

Insert `_migrate(conn)` after `executescript` and before the PRAGMA lines. The PRAGMAs themselves are fine running after the migration; they only affect future statements in the connection, and `_migrate`'s `ALTER TABLE` statements work under any journal mode.

**Idempotency**: on a fresh DB, `CREATE TABLE` already includes every column, so `existing` is a superset of `_NEW_COLUMNS` and no `ALTER TABLE` runs. On an old dev DB (pre-Phase-2), the missing columns are added one by one.

**Concrete ALTER syntax produced** (example, for `quote_latest` upgrading from the pre-Phase-2 schema):

```sql
ALTER TABLE quote_latest ADD COLUMN back_size REAL;
ALTER TABLE quote_latest ADD COLUMN lay_price REAL;
ALTER TABLE quote_latest ADD COLUMN lay_size REAL;
ALTER TABLE quote_latest ADD COLUMN back_price_2 REAL;
ALTER TABLE quote_latest ADD COLUMN back_size_2 REAL;
ALTER TABLE quote_latest ADD COLUMN lay_price_2 REAL;
ALTER TABLE quote_latest ADD COLUMN lay_size_2 REAL;
ALTER TABLE quote_latest ADD COLUMN total_matched REAL;
```

`PRAGMA table_info(quote_latest)` before migration on an old DB:

```
cid|name             |type|notnull|dflt_value|pk
0  |bookmaker        |TEXT|1      |NULL      |1
1  |outcome_id       |TEXT|1      |NULL      |2
2  |decimal_odds     |REAL|1      |NULL      |0
3  |source_timestamp |TEXT|0      |NULL      |0
4  |observed_at      |TEXT|1      |NULL      |0
5  |status           |TEXT|1      |'active'  |0
```

After migration, the same `PRAGMA` adds rows 6-13 for `back_size`, `lay_price`, `lay_size`, `back_price_2`, `back_size_2`, `lay_price_2`, `lay_size_2`, `total_matched`. **No existing column changes**; the primary-key composite is preserved.

**No new indexes** on the exchange columns in `quote_history`. History is read by `outcome_id` and `bookmaker` only (see `get_quote_history` in `odds_store_sqlite.py`); `back_size` et al. are write-only payload. Don't add indexes "just in case" — `quote_history` is large enough that extra indexes are a non-trivial write cost.

### 5.3 Extending `_quote_in()` and `_market_in()` — full replacement bodies

The discovery captured `_quote_in` as the write-on-change site. Both the `INSERT` into `quote_history` and the `INSERT OR REPLACE` into `quote_latest` need the 8 new columns; the `UPDATE` (unchanged-price branch) needs to write the exchange depth columns too — because depth shifts second-by-second even when the best-back doesn't.

**Full replacement `_quote_in`** (preserves the existing change-detection branch exactly):

```python
@staticmethod
def _quote_in(conn, quote: OddsQuote) -> bool:
    observed = _iso(quote.timestamp_received)
    src = _iso(quote.source_timestamp) if quote.source_timestamp else None
    row = conn.execute(
        "SELECT decimal_odds FROM quote_latest WHERE bookmaker = ? AND outcome_id = ?",
        (quote.bookmaker, quote.outcome_id),
    ).fetchone()
    # Change-detection condition UNCHANGED — only decimal_odds (= best back for
    # Betfair) triggers a history append. Exchange depth shifts second-by-second;
    # including it here would explode quote_history.
    changed = row is None or row["decimal_odds"] != quote.decimal_odds
    if changed:
        conn.execute(
            "INSERT INTO quote_history (bookmaker, outcome_id, decimal_odds, "
            " source_timestamp, observed_at, back_size, lay_price, lay_size, "
            " back_price_2, back_size_2, lay_price_2, lay_size_2, total_matched) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote.bookmaker, quote.outcome_id, quote.decimal_odds, src, observed,
             quote.back_size, quote.lay_price, quote.lay_size,
             quote.back_price_2, quote.back_size_2, quote.lay_price_2,
             quote.lay_size_2, quote.total_matched),
        )
        conn.execute(
            "INSERT OR REPLACE INTO quote_latest (bookmaker, outcome_id, decimal_odds, "
            " source_timestamp, observed_at, status, back_size, lay_price, lay_size, "
            " back_price_2, back_size_2, lay_price_2, lay_size_2, total_matched) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (quote.bookmaker, quote.outcome_id, quote.decimal_odds, src, observed,
             quote.status.value,
             quote.back_size, quote.lay_price, quote.lay_size,
             quote.back_price_2, quote.back_size_2, quote.lay_price_2,
             quote.lay_size_2, quote.total_matched),
        )
    else:
        # Unchanged-price branch: still refresh observed_at, status, and exchange
        # depth so quote_latest stays current. For Stoiximan/Novibet, all eight
        # exchange columns on `quote` are None (no mapper sets them), so this
        # writes NULL over NULL — invariant: only Betfair rows ever carry non-NULL
        # exchange depth. A future code path that ever sets a depth field on a
        # non-Betfair bookmaker MUST update that bookmaker's mapper to keep this
        # invariant, otherwise depth will silently zero-out each unchanged tick.
        conn.execute(
            "UPDATE quote_latest SET observed_at = ?, source_timestamp = ?, status = ?, "
            " back_size = ?, lay_price = ?, lay_size = ?, "
            " back_price_2 = ?, back_size_2 = ?, lay_price_2 = ?, lay_size_2 = ?, "
            " total_matched = ? "
            "WHERE bookmaker = ? AND outcome_id = ?",
            (observed, src, quote.status.value,
             quote.back_size, quote.lay_price, quote.lay_size,
             quote.back_price_2, quote.back_size_2, quote.lay_price_2,
             quote.lay_size_2, quote.total_matched,
             quote.bookmaker, quote.outcome_id),
        )
    return changed
```

**Full replacement `_market_in`** (uses direct attribute access — fields are always present with `None` defaults; `getattr` is unnecessary):

```python
@staticmethod
def _market_in(conn, market: CanonicalMarket, bookmaker: str, now: str) -> None:
    last_match = _iso(market.last_match_time) if market.last_match_time else None
    conn.execute(
        "INSERT INTO markets (market_id, event_id, market_type, period, line, "
        "bookmaker, last_seen_at, commission_rate, bet_delay, total_available, last_match_time) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(market_id) DO UPDATE SET "
        "  line = excluded.line, "
        "  period = excluded.period, "
        "  last_seen_at = excluded.last_seen_at, "
        # Betfair updates total_available / last_match_time every cycle — must be
        # part of UPSERT, not insert-only, or the row goes stale immediately.
        "  commission_rate = excluded.commission_rate, "
        "  bet_delay = excluded.bet_delay, "
        "  total_available = excluded.total_available, "
        "  last_match_time = excluded.last_match_time",
        (
            market.market_id, market.event_id, market.market_type.value,
            market.period, market.line, bookmaker, now,
            market.commission_rate, market.bet_delay, market.total_available, last_match,
        ),
    )
```

**Note on `total_matched` semantics** (refining decision #2): the 8 new fields on `OddsQuote` actually break down as **7 order-book depth fields** (`back_size` at level 1, then `lay_price` / `lay_size` at level 1, plus `back_price_2` / `back_size_2` / `lay_price_2` / `lay_size_2` at level 2) **plus 1 cumulative-volume field** (`total_matched`, per-runner). The volume field is monotonically increasing while depth fluctuates — they're semantically distinct, but both correctly excluded from the change-detection condition (else every tick would append history). The brief decision text says "7 new fields" historically; the schema is 8. The schema number is correct — both must be persisted, only their semantics differ.

---

## 6. The mapper — `betrack/normalization/betfair_mapper.py`

### 6.1 Module outline

```python
"""Map Betfair Exchange JSON (scan-inbf + ero/bymarket + ero/byevent) into
MappedEvent bundles. Convention mirrors stoiximan_mapper / novibet_mapper:
map_overview returns list[MappedEvent]; map_event_detail returns
Optional[MappedEvent]; unknown market types are logged once and skipped."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from betrack.models.canonical import (
    CanonicalEvent, CanonicalMarket, CanonicalOutcome,
    EventStatus, MarketType, OddsQuote, OddsStatus, OutcomeType,
)
from betrack.normalization.bundle import MappedEvent
from betrack.normalization.mapper import normalize_team  # uses TEAM_ALIASES

logger = logging.getLogger(__name__)

BOOKMAKER = "Betfair"
LIVE_WINDOW = timedelta(hours=3)            # decision #7: openDate within ±3h
_OVER_UNDER_LINE = re.compile(r"OVER_UNDER_(\d+)$")
_SET_NUMBER = re.compile(r"SET[_\s]*(\d+)", re.IGNORECASE)
_warned_market_types: set[str] = set()       # log-once cache, like Stoix/Novi

# Betfair eventTypeId -> canonical sport slug
SPORT_BY_EVENT_TYPE_ID = {1: "football", 7522: "basketball", 2: "tennis"}

# Per-sport canonical market mapping (decision #6)
MARKET_TYPE_FOOTBALL = {
    "MATCH_ODDS":           MarketType.FOOTBALL_FULL_TIME_1X2,
    "BOTH_TEAMS_TO_SCORE":  MarketType.FOOTBALL_FULL_TIME_BTTS,
    "DOUBLE_CHANCE":        MarketType.FOOTBALL_DOUBLE_CHANCE,
    "DRAW_NO_BET":          MarketType.FOOTBALL_DRAW_NO_BET,
    "HALF_TIME_FULL_TIME":  MarketType.FOOTBALL_HALFTIME_FULLTIME,
    # OVER_UNDER_* handled by the regex prefix matcher below
}
MARKET_TYPE_BASKETBALL = {
    "MATCH_ODDS":       MarketType.BASKETBALL_MATCH_WINNER,
    "HANDICAP":         MarketType.BASKETBALL_HANDICAP,
    # OVER_UNDER_* -> BASKETBALL_TOTAL_POINTS (handled by regex below)
}
MARKET_TYPE_TENNIS = {
    "MATCH_ODDS":       MarketType.TENNIS_MATCH_WINNER,
    # OVER_UNDER_* -> TENNIS_TOTAL_GAMES (regex)
    # SET_*_WINNER    -> TENNIS_SET_WINNER (regex, parse set number)
}

# Deferred — see Section 6.6
_DEFERRED_MARKET_TYPES: set[str] = {
    "HALF_TIME_FULL_TIME",   # TODO(phase3): support HALF_TIME_FULL_TIME — needs OutcomeType extension
}
```

**Market-type lookup order** (per-sport dispatcher inside `_map_market`):

1. If `marketType in _DEFERRED_MARKET_TYPES`: skip silently (no INFO log).
2. Exact dict lookup on the sport-specific table (`MARKET_TYPE_FOOTBALL` / `_BASKETBALL` / `_TENNIS`).
3. Regex match: `_OVER_UNDER_LINE` (yields totals + line); then `_SET_NUMBER` (tennis only, yields `TENNIS_SET_WINNER` + set number).
4. Fallthrough: `_warn_once(marketType, sport)` and skip.

### 6.2 `_make_id` and the deterministic ID rule

Decision #8: Betfair events live in their own namespace. Reuse the existing helper pattern (the discovery cites it in both Stoix and Novi mappers as `hashlib.md5(":".join(parts).encode()).hexdigest()[:16]`). Either import from an existing mapper or define a local copy with the identical body — match whichever pattern the existing mappers already use to avoid divergence.

```python
def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]

def _event_id(native_event_id: int | str, sport: str) -> str:
    return _make_id(BOOKMAKER, str(native_event_id), sport)

def _market_id(event_id: str, market_type: MarketType, period: str,
               line: Optional[float]) -> str:
    return _make_id(BOOKMAKER, event_id, market_type.value, period,
                    "" if line is None else f"{line:g}")

def _outcome_id(market_id: str, outcome_type: OutcomeType,
                line: Optional[float]) -> str:
    parts = [market_id, outcome_type.value]
    if line is not None:
        parts.append(f"{line:g}")
    return _make_id(*parts)
```

### 6.3 Public signatures

Per discovery: pipeline.py calls Stoix as `map_overview(overview, now)` then `map_event_detail(detail, now)`, and Novi as `map_event_detail(detail, sport, now)`. Betfair's `bymarket` response already includes `eventTypeId` per `eventNodes` parent, so we don't need a sport parameter — match Stoiximan's simpler signature:

```python
def map_overview(scan_response: dict, received_at: datetime) -> list[MappedEvent]:
    """No-op stub for Phase 2. scan-inbf does not return enough market/price
    data to materialize MappedEvents; it only returns event/market IDs.
    Real prices come via map_event_detail on bymarket payloads (Section 7.2).
    Returns []. Kept as a public function so pipeline.py's call shape can
    match Stoix/Novi mappers symmetrically.
    # Phase 3: when scan-inbf provides headline prices, populate here."""
    return []

def map_event_detail(bymarket_response: dict, received_at: datetime
                     ) -> list[MappedEvent]:
    """Map a bymarket response into MappedEvent bundles, one per eventNode.
    Unlike Stoix/Novi (one event per detail call), Betfair's bymarket can
    return many events when batched. Returns a list to keep the pipeline
    loop uniform; the pipeline block uses `bundles.extend(...)`."""

def live_event_ids(scan_response: dict) -> list[tuple[str, str]]:
    """Return [(sport_slug, native_event_id), ...] from a list_in_play
    response. See Section 6.11 for the concrete walk."""
```

Notes:

- `map_event_detail` returns `list[MappedEvent]` (not `Optional[MappedEvent]` as Stoix's version does). Deliberate deviation: Betfair's `bymarket` is batched across markets and frequently spans multiple events.
- `map_overview` is a **deliberate no-op stub**, not a TODO. Phase 1's `BetfairClient.list_in_play` does not return prices — only IDs — so there is nothing to map at overview time. The function exists purely so pipeline.py can call mappers symmetrically; the body is `return []` and a `# Phase 3` comment.
- `live_event_ids` returns `(sport, native_event_id)` tuples so the pipeline can group detail fetches per sport (decision #1 isolation).

### 6.4 Walk pseudocode for `map_event_detail`

```python
def _parse_iso_utc(s: str | None) -> Optional[datetime]:
    """Parse Betfair ISO-8601 strings (e.g. '2026-05-29T18:50:16.387Z')
    on Python 3.10+. The .replace('Z', '+00:00') is required for 3.10
    because fromisoformat there does not accept 'Z'. Microseconds parse
    cleanly. Returns None on missing input or parse failure — callers
    must supply their own fallback (see _parse_last_match_time)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def map_event_detail(response: dict, received_at: datetime) -> list[MappedEvent]:
    bundles: list[MappedEvent] = []
    for event_type in response.get("eventTypes", []):       # tolerate {'faults': [...]}-shaped responses
        sport = SPORT_BY_EVENT_TYPE_ID.get(event_type.get("eventTypeId"))
        if not sport:
            continue                                         # ignore non-target sports

        for event_node in event_type.get("eventNodes", []):
            evt = event_node.get("event", {})
            open_date = _parse_iso_utc(evt.get("openDate")) or received_at
            if abs(open_date - received_at) > LIVE_WINDOW:   # decision #7 part 1
                continue                                      # ante-post / future fixture

            event_id = _event_id(event_node["eventId"], sport)
            home_raw, _, away_raw = evt.get("eventName", "").partition(" v ")
            if not away_raw:
                home_raw, _, away_raw = evt.get("eventName", "").partition(" vs ")
            home = normalize_team(home_raw.strip())
            away = normalize_team(away_raw.strip())
            canonical_event = CanonicalEvent(
                event_id=event_id, sport=sport,
                competition=evt.get("countryCode", "") or "",
                home_team=home, away_team=away,
                start_time=open_date,
                status=EventStatus.LIVE,
            )

            markets, outcomes, quotes = [], [], []
            for mn in event_node.get("marketNodes", []):
                state = mn.get("state") or {}                # guard partial-data
                if not state:
                    continue                                  # skip markets with no state block
                if not state.get("inplay"):                  # decision #7 part 2
                    continue
                market_meta = _map_market(mn, sport, event_id, received_at)
                if not market_meta:
                    continue                                  # unknown type -> warn + skip
                market, market_outcomes, market_quotes = market_meta
                markets.append(market)
                outcomes.extend(market_outcomes)
                quotes.extend(market_quotes)

            if markets:                                       # only yield events with real markets
                bundles.append(MappedEvent(
                    event=canonical_event,
                    bookmaker=BOOKMAKER,                       # required by MappedEvent; write_bundles keys counts by f'{bookmaker}/{sport}'
                    native_event_id=str(event_node["eventId"]),# required; passed into _event_in
                    sportradar_match_id=None,                  # Betfair has no Sportradar ID (decision #8)
                    markets=markets,
                    outcomes=outcomes,
                    quotes=quotes,
                ))
    return bundles
```

**Critical MappedEvent fields**: per `betrack/normalization/bundle.py`, `MappedEvent` requires `bookmaker`, `native_event_id`, and `sportradar_match_id` in addition to `event`/`markets`/`outcomes`/`quotes`. `write_bundles` reads `b.bookmaker` for the counts key, and `_event_in` reads `b.native_event_id` / `b.sportradar_match_id`. Constructing a `MappedEvent` without them will fail (positional dataclass) or crash `write_bundles`.

**Outcome/quote ordering inside `_map_market`**: build the `CanonicalOutcome` and its `OddsQuote` together inside the runner loop. If `runner.exchange.availableToBack` is empty, **skip both** (don't `outcomes.append` then bail out on the quote — that leaves orphan outcomes in `quote_latest`-less limbo). Pseudocode in 6.5 has the `if not back: continue` placed before either is appended.

**openDate parse policy**: use `_parse_iso_utc` (defined above) everywhere — `openDate`, `lastMatchTime`, and any other ISO-8601 string from Betfair. The `.replace('Z', '+00:00')` shim is required on Python 3.10; Python 3.11+ would accept `Z` natively but `CLAUDE.md` pins 3.10+. A failed parse on `openDate` falls back to `received_at` so the ±3 h filter still has a meaningful comparison.

### 6.5 `_map_market` — runner-to-outcome and exchange-depth capture

```python
description.marketType  →  canonical MarketType + line  (sport-aware)
state.inplay            already filtered above
state.betDelay          → CanonicalMarket.bet_delay
state.totalAvailable    → CanonicalMarket.total_available
state.lastMatchTime     → CanonicalMarket.last_match_time (parse iso)
rates.marketBaseRate    → CanonicalMarket.commission_rate  (divide by 100)

state = mn.get("state") or {}                        # guard
desc  = mn.get("description") or {}
rates = mn.get("rates") or {}
runners = mn.get("runners") or []

market_outcomes: list[CanonicalOutcome] = []
market_quotes:   list[OddsQuote] = []

for runner in runners:
    r_state = runner.get("state") or {}
    if r_state.get("status") != "ACTIVE":            # WINNER / LOSER / REMOVED → skip
        continue

    back = (runner.get("exchange") or {}).get("availableToBack") or []
    lay  = (runner.get("exchange") or {}).get("availableToLay")  or []
    if not back:
        continue                                      # no price → skip both outcome AND quote

    outcome_type = _resolve_outcome(market_type, runner, event_node, home_team=home, away_team=away)
    if not outcome_type:
        _warn_once(desc.get("marketType"), sport)
        continue

    outcome = CanonicalOutcome(
        outcome_id=_outcome_id(market.market_id, outcome_type, outcome_line),
        market_id=market.market_id,
        outcome_type=outcome_type,
        team_reference=(runner.get("description") or {}).get("runnerName"),
        line=outcome_line,
    )

    quote = OddsQuote(
        bookmaker=BOOKMAKER,
        event_id=event_id,
        market_id=market.market_id,
        outcome_id=outcome.outcome_id,
        decimal_odds=back[0]["price"],                # best back
        back_size  =back[0]["size"],
        lay_price  =lay[0]["price"] if lay else None,
        lay_size   =lay[0]["size"]  if lay else None,
        back_price_2=back[1]["price"] if len(back) > 1 else None,
        back_size_2 =back[1]["size"]  if len(back) > 1 else None,
        lay_price_2 =lay[1]["price"]  if len(lay)  > 1 else None,
        lay_size_2  =lay[1]["size"]   if len(lay)  > 1 else None,
        total_matched=r_state.get("totalMatched"),
        timestamp_received=received_at,
        source_timestamp=_parse_last_match_time(mn, received_at),  # falls back to received_at
        status=OddsStatus.ACTIVE if state.get("status") == "OPEN"
               else OddsStatus.SUSPENDED,
        liquidity=state.get("totalMatched"),          # market-level matched
    )

    market_outcomes.append(outcome)
    market_quotes.append(quote)
```

Notes:

- **Dict-access syntax**: Betfair payloads are plain `dict` instances (no Pydantic / dataclass wrapping). Use bracket / `.get(...)` access everywhere; the dot-notation in earlier drafts was illustrative shorthand. Always `.get(..., default)` chains on optional state / exchange / runners — Betfair can return partial markets during settlement transitions.
- **Outcome + quote built together**: an unpriced runner skips both, so `quote_latest` never has orphan outcomes.

### 6.6 Per-sport market mapping table

Combining decision #6 with the cited Betfair `marketType` values from BETFAIR_BUILD.md:

| Sport | Betfair `marketType` | Canonical `MarketType` | Line source | Runner → Outcome |
|---|---|---|---|---|
| Football | `MATCH_ODDS` (3 runners) | `FOOTBALL_FULL_TIME_1X2` | none | runner = home / draw / away (match `runnerName` against `event.home_team` / `"The Draw"` / `event.away_team`) |
| Football | `OVER_UNDER_05` … `OVER_UNDER_65` | `FOOTBALL_FULL_TIME_OVER_UNDER` | parse digits after `OVER_UNDER_` and `/10` (e.g. `25 → 2.5`) | runnerName `"Over 2.5"` → `OVER` ; `"Under 2.5"` → `UNDER` |
| Football | `BOTH_TEAMS_TO_SCORE` | `FOOTBALL_FULL_TIME_BTTS` | none | `"Yes"` → `BTTS_YES` ; `"No"` → `BTTS_NO` |
| Football | `DOUBLE_CHANCE` | `FOOTBALL_DOUBLE_CHANCE` | none | Betfair runnerName contains the **actual team name**, e.g. `"{HomeTeam} or Draw"` / `"{HomeTeam} or {AwayTeam}"` / `"Draw or {AwayTeam}"`. Match algorithm: lower-case the runner name, split on `" or "`, then: if one half is `"draw"` and the other contains `home_team` tokens → `DOUBLE_CHANCE_HOME_DRAW`; if one half is `"draw"` and the other contains `away_team` tokens → `DOUBLE_CHANCE_DRAW_AWAY`; otherwise (both halves are team names) → `DOUBLE_CHANCE_HOME_AWAY`. Use `normalize_team`-trimmed tokens for the substring check to handle alias differences. |
| Football | `DRAW_NO_BET` | `FOOTBALL_DRAW_NO_BET` | none | match runner name vs home/away team |
| Football | `HALF_TIME_FULL_TIME` | **deferred — DO NOT MAP** | — | `OutcomeType` (canonical.py lines 35-46) has no HT/FT enum members. **Decision (resolves brief decision #6)**: this market is deferred to a future phase. Add `"HALF_TIME_FULL_TIME"` to a module-level `_DEFERRED_MARKET_TYPES = {"HALF_TIME_FULL_TIME"}` set; the dispatcher checks this set FIRST and skips silently (no INFO log spam — these are known-deferred, not unknown). A future build will either extend `OutcomeType` with `HT_HOME_FT_HOME`, … 9 combinations, or fold HT/FT into a generic "compound outcome" with `team_reference` carrying the runner string. **TODO marker**: leave a `# TODO(phase3): support HALF_TIME_FULL_TIME — needs OutcomeType extension` comment at the `_DEFERRED_MARKET_TYPES` definition site. |
| Basketball | `MATCH_ODDS` (2 runners) | `BASKETBALL_MATCH_WINNER` | none | runnerName vs home/away |
| Basketball | `OVER_UNDER_*` (total points) | `BASKETBALL_TOTAL_POINTS` | extract digits + `/10` from marketType; the regex must accept 2-, 3-, or 4-digit numbers (basketball totals are commonly 200+, e.g. `OVER_UNDER_2105` → 210.5; the existing `_OVER_UNDER_LINE` capture group `(\d+)` already supports this — the `/10` divisor is uniform). | `"Over X"` → `OVER` ; `"Under X"` → `UNDER` |
| Basketball | `HANDICAP` | `BASKETBALL_HANDICAP` | parse `runner.handicap` (Betfair attaches the line to the runner, not the market) | match runner name vs home/away; the line itself lives on `CanonicalOutcome.line` and the market. **Note**: `ASIAN_HANDICAP` is a football marketType, not basketball — drop it from `MARKET_TYPE_BASKETBALL` (Section 6.1 outline shows it; remove). |
| Tennis | `MATCH_ODDS` (2 runners) | `TENNIS_MATCH_WINNER` | none | runnerName vs home/away |
| Tennis | `OVER_UNDER_*` (total games) | `TENNIS_TOTAL_GAMES` | digits + `/10` from marketType | `"Over X"` → `OVER` ; `"Under X"` → `UNDER` |
| Tennis | `SET_*_WINNER` (e.g. `SET_1_WINNER`) | `TENNIS_SET_WINNER` | set number parsed via `_SET_NUMBER` regex; store on `CanonicalMarket.line` (mirrors the existing tennis-set-winner convention noted in CLAUDE.md "tennis set-winner maps set 1 only") | runnerName vs home/away |
| any | anything else | **skip** | — | `logger.info("unknown betfair marketType=%s (sport=%s), skipping", mt, sport)` once per `(sport, marketType)` tuple (track in `_warned_market_types`) — matches existing Stoix/Novi convention per the discovery |

### 6.7 Commission conversion

```python
raw_rate = (market_node.get("rates") or {}).get("marketBaseRate")   # e.g. 5.0
commission_rate = raw_rate / 100.0 if raw_rate is not None else None  # → 0.05
```

The `/100.0` happens in the mapper, not the store. The canonical field stores the conventional decimal form (0.05 = 5 %), so downstream consumers don't have to remember "is it a percent or a fraction?".

**Edge cases**: Betfair fee-free markets (rare — free-bet promos, special tournaments) may have the entire `rates` block absent or `marketBaseRate: 0.0`. Both yield correct behavior:
- absent `rates` → `commission_rate = None` (unknown — strategy layer can default to standard 5% later);
- `marketBaseRate: 0.0` → `commission_rate = 0.0` (genuinely free; strategy layer reads literal 0).

### 6.8 Team-name aliasing

Per the discovery: `from betrack.normalization.mapper import normalize_team`. Use it on both team names extracted from `event.eventName` (split on `" v "`, falling back to `" vs "`). Do not duplicate the `TEAM_ALIASES` dict — the import is the single source of truth.

If a Betfair-specific spelling (e.g. `"Man Utd"`) isn't in `TEAM_ALIASES` yet, extend the dict in `betrack/normalization/mapper.py`. **One source of truth shared by all three mappers, per the discovery's findings.**

### 6.9 Line parsing for `OVER_UNDER_*`

```python
_OVER_UNDER_LINE = re.compile(r"OVER_UNDER_(\d+)$")   # end-anchored

m = _OVER_UNDER_LINE.match(market_type_str)
if m:
    line = int(m.group(1)) / 10.0      # OVER_UNDER_25 -> 2.5; OVER_UNDER_05 -> 0.5; OVER_UNDER_2105 -> 210.5 (basketball totals)
```

**Anchor assumption**: the `$` end-anchor means a Betfair marketType like `OVER_UNDER_205_GAMES` (tennis variant some sportsbooks use) would NOT match. Per the Phase 1 discovery, Betfair's actual football / basketball / tennis marketType strings are the plain `OVER_UNDER_NN[NN]` form (no suffix). If a future Betfair payload surfaces a suffixed variant, either:
- remove the `$` anchor and adjust the divisor logic, or
- add the suffixed marketType as an explicit dict entry in the relevant `MARKET_TYPE_*` table.

For Phase 2, the end-anchored regex is correct against the documented Betfair marketType strings. The 2-, 3-, and 4-digit capture group covers football (`OVER_UNDER_05` through `OVER_UNDER_65`), basketball totals (`OVER_UNDER_2105` etc.), and tennis games (`OVER_UNDER_205` etc.) uniformly via the `/10` divisor.

For `HANDICAP` the line lives per-runner (`runner.handicap`) — each runner's handicap value becomes the `CanonicalOutcome.line` (and is part of the outcome_id via `_outcome_id`).

### 6.10 source_timestamp

Prefer `marketNode.state.lastMatchTime` (parse as ISO 8601, UTC); fall back to `received_at` when missing or unparseable. This matches the existing convention: per the discovery, Stoiximan uses `detail['syncedAtUtc']` and Novibet uses `liveData['referenceTime']`.

```python
def _parse_last_match_time(market_node: dict, received_at: datetime) -> datetime:
    """Return CanonicalMarket.last_match_time / OddsQuote.source_timestamp.
    Returns received_at when the Betfair field is absent or malformed so
    the freshness story stays consistent across bookmakers."""
    lmt = (market_node.get("state") or {}).get("lastMatchTime")
    parsed = _parse_iso_utc(lmt)
    return parsed if parsed is not None else received_at
```

The signature change (taking `received_at`) is what makes the "fall back to received_at" prose actually implementable — the helper must know the caller's clock. Section 6.5's `OddsQuote(...)` call passes `received_at` through.

### 6.11 `live_event_ids` — concrete walk against `BetfairClient.list_in_play`

**Decision (resolves brief decision #1 dependency)**: per `BetfairClient.list_in_play` (Phase 1), the response is shape `{"attachments": {...}, "results": [{"marketId": "...", "eventId": "...", "eventTypeId": <int>, ...}, ...], "facets": [...]}`. The simplest concrete walk: iterate `results`, filter by known `eventTypeId`, return `(sport, str(eventId))`. No facets/attachments traversal required for the basic fan-out.

```python
def live_event_ids(scan_response: dict) -> list[tuple[str, str]]:
    """Return [(sport_slug, native_event_id), ...] from a list_in_play response.
    Filters by SPORT_BY_EVENT_TYPE_ID; deduplicates eventIds (a single event
    can appear under multiple results when multiple markets match).
    Caller may further filter to a single sport before fan-out."""
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for r in scan_response.get("results", []):
        sport = SPORT_BY_EVENT_TYPE_ID.get(r.get("eventTypeId"))
        if not sport:
            continue
        eid = str(r.get("eventId") or "")
        if not eid:
            continue
        key = (sport, eid)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out
```

**Pipeline implication**: because `list_in_play` is called **per sport** in Section 7.2 (`betfair.list_in_play(event_type_id, ...)`), every result inside `results` is already pre-filtered to that sport on the wire. The `SPORT_BY_EVENT_TYPE_ID` check in `live_event_ids` is therefore a sanity belt-and-braces, not a real filter — but keep it so the helper is reusable if a future caller passes a combined response.

If a future build needs the richer facet tree (e.g. for navigation labels), it can extend this helper; for now the flat `results` walk is fully sufficient and matches the Phase 1 smoke test which reads `r['marketId']` directly.

---

## 7. Pipeline wiring

The discovery cites `run_cycle`'s current signature:

```python
async def run_cycle(
    stoiximan: StoiximanClient,
    novibet: NovibetClient,
    store: SqliteOddsStore,
    *,
    detail_concurrency: int = 8,
    detail_limit: int = 60,
) -> CycleResult:
```

### 7.1 New signature

```python
async def run_cycle(
    stoiximan: StoiximanClient,
    novibet: NovibetClient,
    betfair: "BetfairClient | None",      # None disables Betfair this cycle
    store: SqliteOddsStore,
    *,
    detail_concurrency: int = 8,
    detail_limit: int = 60,
) -> CycleResult:
```

`betfair: None` allows callers to disable Betfair entirely (e.g. dev runs without the tunnel) without conditional argument passing — the body checks `if betfair is None: skip` early.

### 7.2 Betfair block (added after the existing Stoiximan + Novibet blocks)

Decision #1 mandates two layers of isolation:

1. **Outer:** the whole Betfair block is wrapped in `try / except Exception` that logs `"betfair cycle failed: …"`, appends to `result.errors`, and continues. Stoix/Novi were already running by then in their own try blocks, so they are unaffected.
2. **Inner:** within Betfair, run the three sports (`football`, `basketball`, `tennis`) concurrently and isolate each. If Betfair-tennis 403s, the football + basketball results still upsert.

**Placement & shared state**: the block sits **after the Novibet block, before `return result`**, at the same indentation level (inside `run_cycle`, top-level of its body). It reuses the function-scoped `now` (line 52) and `seen` (line 54) — do **not** re-declare them.

**Helpers** (define at module scope in `pipeline.py`, right above `run_cycle`):

```python
def _extract_market_ids(byevent: dict) -> list[str]:
    """Walk an ero/byevent response and collect every in-play marketId.
    Shape: byevent['eventTypes'][i]['eventNodes'][j]['marketNodes'][k]['marketId'].
    We DO NOT filter by inplay here — the bymarket call returns the same
    markets with full state, and betfair_mapper.map_event_detail applies
    the inplay==true filter at mapping time (decision #7). Filtering twice
    is harmless but adds branching; do it once in the mapper."""
    out: list[str] = []
    for et in byevent.get("eventTypes", []):
        for en in et.get("eventNodes", []):
            for mn in en.get("marketNodes", []):
                mid = mn.get("marketId")
                if mid:
                    out.append(str(mid))
    return out


def _chunked(seq: list, size: int) -> list[list]:
    """[1,2,3,4,5,6] / 2 -> [[1,2],[3,4],[5,6]]. Plain slicing; no itertools
    import needed because all call sites pass already-materialised lists."""
    return [seq[i:i + size] for i in range(0, len(seq), size)]
```

Sketch (using `from betrack.normalization import betfair_mapper`):

```python
if betfair is not None:
    try:
        async def _one_sport(sport_slug: str, event_type_id: int) -> list[MappedEvent]:
            scan = await betfair.list_in_play(event_type_id, max_results=detail_limit)
            ev_ids = [eid for (_s, eid) in betfair_mapper.live_event_ids(scan)
                      if _s == sport_slug][:detail_limit]
            if not ev_ids:
                return []

            # fetch_event_markets joins event_ids with commas into a URL — chunk to
            # <= 25 eventIds per call to stay under Betfair's URL-length limits, same
            # cap we use for fetch_markets (Section 12).
            byevent_payloads: list[dict] = []
            for ev_chunk in _chunked(ev_ids, 25):
                try:
                    byevent_payloads.append(await betfair.fetch_event_markets(ev_chunk))
                except Exception as exc:
                    logger.warning("betfair/%s byevent chunk failed: %s", sport_slug, exc)
                    # bubble nothing — keep gathering the other chunks for this sport

            market_ids: list[str] = []
            for p in byevent_payloads:
                market_ids.extend(_extract_market_ids(p))

            if not market_ids:
                return []

            # bymarket: <= 25 marketIds per call (BETFAIR_BUILD.md limit)
            factories = [
                (lambda mids=chunk: betfair.fetch_markets(mids, rollup_limit=10))
                for chunk in _chunked(market_ids, 25)
            ]
            bundles_for_sport: list[MappedEvent] = []
            for payload in await _capped(factories, detail_concurrency):
                if not payload:
                    continue                                # _capped returned None for a failed factory
                bundles_for_sport.extend(
                    betfair_mapper.map_event_detail(payload, now)
                )
            return bundles_for_sport

        sport_tasks = [
            _one_sport("football",   1),
            _one_sport("basketball", 7522),
            _one_sport("tennis",     2),
        ]
        per_sport = await asyncio.gather(*sport_tasks, return_exceptions=True)

        betfair_bundles: list[MappedEvent] = []
        for sport_slug, outcome in zip(("football","basketball","tennis"), per_sport):
            if isinstance(outcome, Exception):
                msg = f"betfair/{sport_slug}: {outcome.__class__.__name__}: {outcome}"
                logger.warning(msg)
                result.errors.append(msg)
                continue
            betfair_bundles.extend(outcome)

        if betfair_bundles:
            obs, chg = await asyncio.to_thread(
                store.write_bundles, betfair_bundles, result.counts, seen
            )
            result.total_observed += obs
            result.total_changed  += chg

    except Exception as exc:
        msg = f"betfair: {exc.__class__.__name__}: {exc}"
        logger.warning("betfair cycle failed: %s", exc)
        result.errors.append(msg)
```

**Exception layering** (3 levels — be explicit so the implementer doesn't double-handle):

1. **Innermost (`_capped`)**: a single `fetch_markets` chunk failing (e.g. one chunk gets a transient 403) is swallowed inside `_capped` (existing helper at pipeline.py line 27); it returns `None` in that slot. The `if not payload: continue` line above drops the slot from `bundles_for_sport`. **Other chunks for the same sport still flow.**
2. **Mid (`_one_sport`)**: a `list_in_play` failure, or the `byevent` step failing for *all* chunks of a sport, raises out of `_one_sport`. `asyncio.gather(..., return_exceptions=True)` captures the exception per sport. The `zip` loop turns it into a `betfair/<sport>:` entry in `result.errors`. **Other sports still flow.**
3. **Outer (`try/except Exception`)**: a `BetfairClient` connection error before any sport task spawns (e.g. dead tunnel detected on the very first `await`), or any unhandled error in the gather/zip plumbing itself, lands here as a single `betfair: <Exc>: <msg>` entry. **Stoiximan/Novibet are unaffected — their blocks ran before this one.**

Do **not** wrap individual chunks in `try/except` inside `_one_sport` *and* rely on `_capped`-style swallowing in the same code path; pick one per call site (the sketch above wraps `fetch_event_markets` chunks with try/except because there is no `_capped` around them, and relies on `_capped` for the `fetch_markets` chunks).

Key points:

- **`asyncio.gather(*sport_tasks, return_exceptions=True)`** is the per-sport isolation. One sport raising doesn't abort the others.
- **Per-sport key `b.bookmaker + "/" + b.event.sport`** is already how `write_bundles` keys `counts` (per the discovery: `key = f"{b.bookmaker}/{b.event.sport}"`). Betfair bundles flow through unchanged; the dashboard's `counts` dict gains `"Betfair/football"`, `"Betfair/basketball"`, `"Betfair/tennis"` keys automatically (decision #9 links to decision #8 via this mechanism).
- **Empty-sport edge cases**: if `list_in_play` returns 0 events for a sport (no in-play tennis right now), `_one_sport` returns `[]` early — no warning, no error. The sport simply doesn't appear in `counts` until next cycle.
- **Malformed responses**: `_extract_market_ids` and `betfair_mapper.map_event_detail` both use `.get("eventTypes", [])`, so a Betfair `{"faults": [...]}` response (e.g. wrong `_ak`) yields zero markets without raising — the empty result naturally degrades. If the Phase 1 `BetfairClient` raises on `faults` instead, the mid-layer captures it.
- **Exception class names**: curl_cffi raises `curl_cffi.requests.errors.RequestsError` and HTTPError-shaped exceptions, **not** aiohttp's `ClientResponseError`. Example error strings will read like `betfair/football: RequestsError: Failed to connect to proxy` or `betfair/tennis: HTTPError: HTTP 403`.

### 7.3 `Runtime` wiring in `betrack/web/app.py`

Add to `Runtime.__init__` immediately after the Stoiximan / Novibet constructions (per the discovery, line 62–66):

```python
self.betfair = BetfairClient()   # reads BETRACK_BETFAIR_PROXY itself; do NOT pass proxy explicitly
```

`Runtime.start()` (per the discovery, lines 80-81) gains a third `__aenter__`:

```python
await self.stoiximan.__aenter__()
await self.novibet.__aenter__()
await self.betfair.__aenter__()
```

`Runtime.stop()` (lines 94-95) gains the matching `__aexit__`:

```python
await self.betfair.__aexit__(None, None, None)
await self.novibet.__aexit__(None, None, None)
await self.stoiximan.__aexit__(None, None, None)
```

`Runtime._loop()` (line 100) passes the new arg:

```python
result = await run_cycle(
    self.stoiximan, self.novibet, self.betfair, self.store
)
```

The `BOOKMAKERS` list is extended:

```python
BOOKMAKERS = ["Stoiximan", "Novibet", "Betfair"]
```

### 7.4 Status dict updates

Per the discovery, the status dict shape is:

```python
self.status: dict = {
    "last_run": None,
    "poll_interval": POLL_INTERVAL,
    "bookmakers": BOOKMAKERS,
    "counts": {},
    "total_observed": 0,
    "total_changed": 0,
    "errors": [],
    "detection": "suspended",
}
```

Decision #1 plus the planning rationale: **keep the flat `errors` list, prefix each entry with the bookmaker**. No new top-level keys. Examples after a cycle where Betfair-football and Betfair-tennis both failed but basketball succeeded:

```python
"errors": [
    "betfair/football: RequestsError: Failed to connect to proxy 127.0.0.1:1080",
    "betfair/tennis: HTTPError: HTTP 403",
]
"counts": {
    "Stoiximan/football": {...}, "Novibet/football": {...},
    "Betfair/basketball": {...},                  # only the successful sport
}
```

(`curl_cffi` raises `RequestsError` / `HTTPError`-shaped exceptions, not aiohttp's `ClientResponseError`.)

The UI's existing `StatusBar` reads `errors` as a flat list of strings and already handles arbitrary content; no UI work is needed to render Betfair-specific errors.

### 7.5 `main.py`

The console poller mirrors the Runtime lifecycle. Construct `BetfairClient()` (reads `BETRACK_BETFAIR_PROXY` itself), enter it in the same `async with` chain as Stoiximan/Novibet, pass it as the new third positional argument to `run_cycle`, exit it on shutdown.

Concrete patch (replace lines 4-25 of the current `main.py`):

```python
from betrack.ingestion.betfair import BetfairClient                 # NEW import
from betrack.ingestion.novibet import NovibetClient
from betrack.ingestion.stoiximan import StoiximanClient
from betrack.pipeline import run_cycle
from betrack.store.odds_store_sqlite import SqliteOddsStore

...

async def run() -> None:
    store = SqliteOddsStore()
    store.prune_quote_history()

    async with StoiximanClient() as stoiximan, \
               NovibetClient() as novibet, \
               BetfairClient() as betfair:                          # NEW
        logger.info("BETrack started — custom ingestion (Stoiximan + Novibet + Betfair)")
        logger.info("  sports: football/basketball/tennis  poll: %ds  detection: suspended", POLL_INTERVAL)

        while True:
            try:
                result = await run_cycle(stoiximan, novibet, betfair, store)   # NEW positional arg
                ...
```

If the operator wants to run without Betfair (no proxy, no UK host), simply pass `None` instead of constructing `BetfairClient`. The `run_cycle` body's `if betfair is None: skip` early-return handles it cleanly.

---

## 8. Operational

### 8.1 Running with proxy ON

Two windows.

Window 1 — keep the SSH SOCKS tunnel up (from BETFAIR_BUILD.md, Section "SOCKS5 proxy support"):

```powershell
ssh -i "$HOME\.ssh\betrack-vps.key" -D 1080 -N opc@<vps-public-ip>
```

Window 2 — start the dashboard with Betfair routing enabled:

```powershell
$env:BETRACK_BETFAIR_PROXY = "socks5h://127.0.0.1:1080"
python serve.py
```

`socks5h://` (not `socks5://`) — DNS through the tunnel. Greek-IP DNS lookups for `betfair.com` leak the request before it ever reaches the proxy.

For `main.py` (console-only):

```powershell
$env:BETRACK_BETFAIR_PROXY = "socks5h://127.0.0.1:1080"
python main.py
```

### 8.2 Verifying Betfair flows

After `serve.py` starts and the first cycle completes (~30 s):

```powershell
# Status — expect counts keys for Betfair/{football,basketball,tennis}
Invoke-RestMethod http://127.0.0.1:8000/api/status | ConvertTo-Json -Depth 5

# Events — pick a live football event, then drill into it
Invoke-RestMethod "http://127.0.0.1:8000/api/events?sport=football"
Invoke-RestMethod "http://127.0.0.1:8000/api/event/<event_id>" | ConvertTo-Json -Depth 6
```

A successful Betfair event row in `/api/event/{id}` will show `Betfair` as a third book alongside `Stoiximan` / `Novibet` for each outcome's quote map. Inspect a SQL row directly to confirm exchange columns landed:

```powershell
sqlite3 betrack.db "SELECT bookmaker, decimal_odds, back_size, lay_price, lay_size, total_matched FROM quote_latest WHERE bookmaker='Betfair' LIMIT 5;"
sqlite3 betrack.db "SELECT bookmaker, commission_rate, bet_delay, total_available FROM markets WHERE bookmaker='Betfair' LIMIT 5;"
```

### 8.3 Verifying graceful degradation

Kill the SSH tunnel (Ctrl-C window 1) while `serve.py` is still running. Within one poll cycle:

- `/api/status` shows new entries in `errors`: `"betfair/football: …"`, `"betfair/basketball: …"`, `"betfair/tennis: …"` (or a single `"betfair: …"` if the failure preceded the per-sport gather).
- `counts` for `Stoiximan/*` and `Novibet/*` keep advancing normally.
- `quote_latest` rows for `Betfair` grow stale (their `observed_at` doesn't advance) but aren't deleted; freshness gating in the web layer (`FRESH_SECONDS`) drops them from the dashboard naturally.

Restart the tunnel; within one cycle Betfair quotes come back without restarting `serve.py`.

---

## 9. UI

**Zero changes for this build.**

Per CLAUDE.md, the dashboard's per-event drawer (`betrack/web/frontend/src/components/EventDrawer.tsx`) calls `GET /api/event/{event_id}` and renders one column per bookmaker discovered in the response's quote maps. The backend's cross-book grouping logic in `betrack/web/app.py` (the discovery describes it as: *"each outcome with both books' quotes (odds, age_seconds, outcome_id)"*) already accepts N bookmakers per outcome — the "two books" wording is incidental; the actual code groups by whatever bookmaker keys appear in `quote_latest`.

Consequently, the moment `quote_latest` contains `Betfair` rows for a market's outcomes that already cross-link to the same `outcome_id`, the drawer paints a third column labelled "Betfair". Because cross-bookmaker matching is **deferred** (decision #8), Betfair events will appear as **separate event rows** in the events list — they live in their own `event_id` namespace until a future build adds fuzzy matching. The drawer's third-column rendering will fully activate once that landing.

For Phase 2, the visible behaviour:

- New Betfair-only event rows appear in the `/api/events?sport=…` lists, with `Betfair` as the sole bookmaker for that row.
- Clicking such a row opens the drawer with just the Betfair column populated.
- Stoiximan and Novibet events are unchanged (still show as two-book rows with their existing cross-match).
- Exchange depth (`back_size`, `lay_price`, …) and market metadata (`commission_rate`, `bet_delay`, …) **persist to SQLite** but are not surfaced in the UI. They're staged for the future strategy / reference layer.

---

## 10. Testing & verification

No automated test suite (CLAUDE.md: *"There is no Python test suite, linter, or CI"*). The verification below is manual smoke testing — adequate for this build.

### a) Schema migration is idempotent

PowerShell note: multi-statement `python -c "..."` requires careful quoting. Use single-quoted strings with the embedded code on one line, or pipe stdin:

```powershell
# Cold start — fresh DB
Remove-Item betrack.db -ErrorAction SilentlyContinue
python -c 'from betrack.store.odds_store_sqlite import SqliteOddsStore; SqliteOddsStore(\"betrack.db\")'
# Note: backslash-escape the inner quotes because PowerShell consumes the outer set.
# Equivalent alternative using a here-string:
@'
from betrack.store.odds_store_sqlite import SqliteOddsStore
SqliteOddsStore("betrack.db")
'@ | python -

sqlite3 betrack.db "PRAGMA table_info(quote_latest);"
# Expect: rows include back_size, lay_price, lay_size, back_price_2, back_size_2, lay_price_2, lay_size_2, total_matched

# Run a second time — same DB, must not error
python -c 'from betrack.store.odds_store_sqlite import SqliteOddsStore; SqliteOddsStore(\"betrack.db\")'
sqlite3 betrack.db "SELECT COUNT(*) FROM pragma_table_info('quote_latest') WHERE name='back_size';"
# Expect: 1 (not 2 — no duplicate columns)
```

`sqlite3.exe` may not be on PATH on a default Windows install. If absent, replace `sqlite3 betrack.db "<sql>"` with:

```powershell
python -c 'import sqlite3, sys; c=sqlite3.connect(\"betrack.db\"); [print(r) for r in c.execute(sys.argv[1])]' "PRAGMA table_info(quote_latest);"
```

Simulate a pre-Phase-2 database by dropping the new columns and re-running:

```powershell
# Requires SQLite 3.35+ (March 2021) for ALTER TABLE DROP COLUMN. Check first:
python -c "import sqlite3; print(sqlite3.sqlite_version)"
# Expect: 3.35.0 or higher. If older, skip this sub-test or recreate the table without the column manually.

sqlite3 betrack.db "ALTER TABLE quote_latest DROP COLUMN back_size;"
python -c 'from betrack.store.odds_store_sqlite import SqliteOddsStore; SqliteOddsStore(\"betrack.db\")'
sqlite3 betrack.db "PRAGMA table_info(quote_latest);"
# Expect: back_size is back, no error raised
```

### b) Proxy ON + tunnel UP — Betfair appears (end-to-end depth verification)

Run the Section 8.1 setup. After 30 seconds:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/status | Select-Object -ExpandProperty counts
# Expect: keys include "Betfair/football", "Betfair/basketball", "Betfair/tennis"
```

```powershell
sqlite3 betrack.db "SELECT COUNT(*) FROM quote_latest WHERE bookmaker='Betfair';"
# Expect: > 0

# Verify the new OddsQuote depth fields landed end-to-end (decision #2):
sqlite3 betrack.db "SELECT COUNT(*) FROM quote_latest WHERE bookmaker='Betfair' AND back_size IS NOT NULL AND lay_price IS NOT NULL AND lay_size IS NOT NULL AND total_matched IS NOT NULL;"
# Expect: > 0 (at least one row has non-NULL depth — best back/lay always populated for ACTIVE runners)

# Verify the new CanonicalMarket metadata fields landed end-to-end (decision #5):
sqlite3 betrack.db "SELECT COUNT(*) FROM markets WHERE bookmaker='Betfair' AND commission_rate IS NOT NULL AND bet_delay IS NOT NULL;"
# Expect: > 0
```

### c) Proxy OFF — Stoix/Novi flow, Betfair absent with warning, dashboard still starts

```powershell
Remove-Item env:BETRACK_BETFAIR_PROXY -ErrorAction SilentlyContinue
python serve.py
# Expect: serve.py starts cleanly (the BetfairClient.__aenter__ does NOT require an
# active proxy — only per-cycle fetches will fail). The dashboard becomes reachable
# at http://127.0.0.1:8000 within a few seconds.
# Wait ~30 s for the first cycle.
Invoke-RestMethod http://127.0.0.1:8000/api/status | ConvertTo-Json -Depth 4
# Expect: errors[] contains at least one "betfair/..." entry (likely all three sports)
# Expect: counts has Stoiximan/* and Novibet/* entries with positive observed
# Expect: no Betfair/* counts (or all zero)
```

### d) Unknown market types — log once, skip

In `betfair_mapper.py`, the `_warn_unknown_market_type(market_type, sport)` helper checks the `_warned_market_types` set. Verify by tailing logs through one cycle:

```powershell
$env:BETRACK_BETFAIR_PROXY = "socks5h://127.0.0.1:1080"
python serve.py 2>&1 | Select-String "unknown betfair marketType"
# Expect: each unique (sport, marketType) tuple logged exactly once across the whole process lifetime
```

Hand-verify a known unknown (e.g. `CORRECT_SCORE` if not mapped) renders one INFO line on first encounter, no further lines on subsequent cycles.

### e) Filter: ante-post outright must NOT appear

Pick a known Betfair football outright (e.g. "Top Goalscorer", "Premier League Winner") via direct API exploration:

```powershell
# Verify a market with state.inplay==false and openDate far in the future is in the API
$env:BETRACK_BETFAIR_PROXY = "socks5h://127.0.0.1:1080"
python -c "import asyncio; from betrack.ingestion.betfair import BetfairClient
async def go():
    async with BetfairClient() as c:
        scan = await c.list_in_play(1, max_results=100)
        # Inspect facets/attachments — find a Top Goalscorer or Tournament Winner event
        # Note its eventId, then fetch its markets via byevent
asyncio.run(go())"

# Then verify those event_ids do NOT show up in our store
sqlite3 betrack.db "SELECT event_id, home_team, away_team, start_time FROM events WHERE event_id IN (SELECT event_id FROM markets WHERE bookmaker='Betfair') AND (home_team LIKE '%Goalscorer%' OR home_team LIKE '%Winner%');"
# Expect: zero rows
```

The two filters (decision #7: `inplay == true` AND `openDate` within ±3 h) together drop these. If either filter is skipped during implementation, this test catches it.

---

## 11. Out of scope (deferred)

Repeat from Section 1 for reviewer convenience:

- **Cross-bookmaker matching** — Betfair has no Sportradar ID. Fuzzy match by `home_team` + `away_team` + `start_time` (±5 min), tie-breaking on country/competition. Belongs with a unified-events refactor. **Important consequence**: events created in their own Betfair namespace this build can never collapse retroactively into a shared row — the deterministic `event_id` is baked into `markets`, `outcomes`, and `quote_history` rows. Future cross-matching will need either (a) a backfill migration that rewrites `event_id` across all four tables, or (b) accepting a permanent fork where pre-Phase-3 Betfair events live separately and only post-Phase-3 events collapse.

- **UI presentation when a row has only one bookmaker** — Because Betfair events live in their own namespace this build, the `/api/events?sport=…` list will return Betfair-only rows alongside Stoix+Novi cross-matched rows. `EventsTable` already handles this by showing `best`/`gap_pct` as null when fewer than two books are present; the row simply has a single populated odds cell. Verify by inspection on the live dashboard; no UI code change required, but confirm visual rendering is acceptable (it should be — the same code path already runs for Stoix-or-Novi-only events when one book lacks coverage).
- **Strategy / reference layer** — commission-adjusted back/lay midpoint as the "true" probability against which to score Stoix/Novi value edges. The exchange fields persist; consumption is a future build.
- **Live scores / clock from `ips.betfair.com`** — `scoresAndBroadcast`, `eventTimelines`, `eventDetails`. Wire when the dashboard needs them.
- **Full order-book ladder** — capture deeper levels of `availableToBack` / `availableToLay`. Best + 2nd-best is the floor; extending is an additive migration (more columns, no semantic shift).
- **Polling cadence tuning** — Betfair currently piggybacks the 30 s `POLL_INTERVAL`. Betfair's own frontend polls 1-2 s during in-play; we don't need that resolution. If we ever want it, decouple Betfair's loop from `run_cycle`.
- **Running BetfairClient on a separate UK host** (Pattern 3 from BETFAIR_BUILD.md) — a UK VPS runs the Betfair fetcher and writes back to a shared DB, while the Greek host runs Stoix/Novi. Operational, not a code change.

---

## 12. File map summary (dependency order)

| Order | Path | New / Modified | Purpose |
|---|---|---|---|
| 1 | `betrack/models/canonical.py` | M | Add 8 fields to `OddsQuote` (7 depth + `total_matched`), 4 to `CanonicalMarket`. Pure additive, no validators. |
| 2 | `betrack/store/odds_store_sqlite.py` | M | Extend `_SCHEMA` (3 tables); add module-level `_NEW_COLUMNS` + `_migrate(conn)` invoked in `__init__`; full-body replacement of `_quote_in()` and `_market_in()`. No new indexes. |
| 3 | `betrack/normalization/betfair_mapper.py` | **N** | `BOOKMAKER = "Betfair"`. Public: `map_overview` (no-op stub), `map_event_detail`, `live_event_ids`. Reuse `normalize_team` from `betrack.normalization.mapper`. Log-once `_warned_market_types`; `_DEFERRED_MARKET_TYPES = {"HALF_TIME_FULL_TIME"}`. Per-sport `MARKET_TYPE_*` tables, `_OVER_UNDER_LINE` regex, `_SET_NUMBER` regex, ±3 h `openDate` + `inplay==true` filter, dict-access throughout. MappedEvent built with `bookmaker="Betfair"`, `native_event_id=str(...)`, `sportradar_match_id=None`. |
| 4 | `betrack/pipeline.py` | M | Add `_extract_market_ids` + `_chunked` module helpers; add `betfair: BetfairClient \| None` parameter to `run_cycle`; insert Betfair block after Novibet block, before `return result`; per-sport `asyncio.gather(return_exceptions=True)`; chunk `fetch_event_markets` and `fetch_markets` calls to ≤ 25 IDs each. |
| 5 | `betrack/web/app.py` | M | `BOOKMAKERS = ["Stoiximan", "Novibet", "Betfair"]`. `Runtime.__init__` constructs `BetfairClient()`. `start()` / `stop()` add a third `__aenter__` / `__aexit__` (LIFO on `stop`: betfair → novibet → stoiximan — intentional; if betfair's aexit raises, novibet/stoiximan still need to close, so wrap aexit calls individually or rely on async context-manager suppression). `_loop()` passes betfair to `run_cycle`. |
| 6 | `main.py` | M | Add `BetfairClient` import; extend `async with` chain; pass `betfair` as third positional arg to `run_cycle`. |
