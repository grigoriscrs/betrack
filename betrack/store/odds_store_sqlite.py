from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from betrack.models.canonical import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    OddsQuote,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  event_id              TEXT PRIMARY KEY,
  sport                 TEXT NOT NULL,
  competition           TEXT,
  country               TEXT,
  home_team             TEXT NOT NULL,
  away_team             TEXT NOT NULL,
  start_time            TEXT NOT NULL,
  status                TEXT NOT NULL,
  sportradar_match_id   INTEGER,
  bookmaker_event_ids   TEXT NOT NULL,
  last_seen_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_sport_status ON events(sport, status);
CREATE INDEX IF NOT EXISTS idx_events_sr           ON events(sportradar_match_id);

CREATE TABLE IF NOT EXISTS markets (
  market_id        TEXT PRIMARY KEY,
  event_id         TEXT NOT NULL REFERENCES events(event_id),
  market_type      TEXT NOT NULL,
  period           TEXT NOT NULL DEFAULT 'full_time',
  line             REAL,
  bookmaker        TEXT NOT NULL,
  last_seen_at     TEXT NOT NULL,
  commission_rate  REAL,
  bet_delay        INTEGER,
  total_available  REAL,
  last_match_time  TEXT,
  total_matched    REAL
);
CREATE INDEX IF NOT EXISTS idx_markets_event ON markets(event_id);

CREATE TABLE IF NOT EXISTS outcomes (
  outcome_id     TEXT PRIMARY KEY,
  market_id      TEXT NOT NULL REFERENCES markets(market_id),
  outcome_type   TEXT NOT NULL,
  team_reference TEXT,
  line           REAL,
  last_seen_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_outcomes_market ON outcomes(market_id);

CREATE TABLE IF NOT EXISTS quote_latest (
  bookmaker         TEXT NOT NULL,
  outcome_id        TEXT NOT NULL REFERENCES outcomes(outcome_id),
  decimal_odds      REAL NOT NULL,
  source_timestamp  TEXT,
  observed_at       TEXT NOT NULL,
  -- Distinct from observed_at: only advances when decimal_odds actually moves.
  -- observed_at answers 'is the book still quoting this outcome?'; this column
  -- answers 'when did the current price come into existence?'. The strategy
  -- layer uses THIS for staleness — a quote re-confirmed every cycle but
  -- frozen at the same odds for many minutes is stale even if observed_at=now.
  last_changed_at   TEXT,
  status            TEXT NOT NULL DEFAULT 'active',
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
  back_size         REAL,
  lay_price         REAL,
  lay_size          REAL,
  back_price_2      REAL,
  back_size_2       REAL,
  lay_price_2       REAL,
  lay_size_2        REAL,
  total_matched     REAL
);
CREATE INDEX IF NOT EXISTS idx_qh_outcome_obs ON quote_history(outcome_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_qh_book_obs    ON quote_history(bookmaker, observed_at);
"""


_NEW_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "events": [
        ("country", "TEXT"),
    ],
    "markets": [
        ("commission_rate", "REAL"),
        ("bet_delay", "INTEGER"),
        ("total_available", "REAL"),
        ("last_match_time", "TEXT"),
        ("total_matched", "REAL"),
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
        ("last_changed_at", "TEXT"),
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
        assert info_rows, f"_migrate: table {table!r} missing — schema executescript did not run"
        existing = {row["name"] for row in info_rows}
        for col_name, col_type in cols:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
    # One-shot backfill: any rows that pre-date last_changed_at get observed_at
    # as their initial change time. We don't actually know when their price
    # last moved, but observed_at is the only signal we have and it's a sane
    # floor (they were definitely current as of that observation).
    conn.execute(
        "UPDATE quote_latest SET last_changed_at = observed_at "
        "WHERE last_changed_at IS NULL"
    )
    conn.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class SqliteOddsStore:
    """SQLite-backed odds store. Reference tables (events/markets/outcomes)
    upsert in place; quote_latest holds one row per (bookmaker, outcome) and
    quote_history grows only when a price actually changes."""

    def __init__(self, path: str = "betrack.db") -> None:
        self._path = str(path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            _migrate(conn)
            # WAL lets dashboard reads run concurrently with the poller's writes;
            # NORMAL sync avoids an fsync per commit (safe under WAL).
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # --- connection-bound writers (used both standalone and inside write_bundles) ---

    @staticmethod
    def _event_in(conn, event: CanonicalEvent, bookmaker: str, native_event_id: str,
                  sportradar_match_id: Optional[int], now: str) -> None:
        row = conn.execute(
            "SELECT bookmaker_event_ids FROM events WHERE event_id = ?", (event.event_id,)
        ).fetchone()
        ids = json.loads(row["bookmaker_event_ids"]) if row else {}
        ids[bookmaker] = str(native_event_id)
        conn.execute(
            "INSERT INTO events (event_id, sport, competition, country, home_team, away_team, "
            "start_time, status, sportradar_match_id, bookmaker_event_ids, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET "
            # Keep a known competition rather than letting a bookmaker that omits
            # it (e.g. Novibet's per-event feed) clobber it with 'Unknown'.
            "  competition = COALESCE(NULLIF(NULLIF(excluded.competition, 'Unknown'), ''), events.competition), "
            "  country = COALESCE(NULLIF(excluded.country, ''), events.country), "
            "  home_team = excluded.home_team, "
            "  away_team = excluded.away_team, start_time = excluded.start_time, "
            "  status = excluded.status, "
            "  sportradar_match_id = COALESCE(excluded.sportradar_match_id, events.sportradar_match_id), "
            "  bookmaker_event_ids = excluded.bookmaker_event_ids, "
            "  last_seen_at = excluded.last_seen_at",
            (
                event.event_id, event.sport, event.competition, event.country, event.home_team,
                event.away_team, _iso(event.start_time), event.status.value,
                sportradar_match_id, json.dumps(ids), now,
            ),
        )

    @staticmethod
    def _market_in(conn, market: CanonicalMarket, bookmaker: str, now: str) -> None:
        last_match = _iso(market.last_match_time) if market.last_match_time else None
        conn.execute(
            "INSERT INTO markets (market_id, event_id, market_type, period, line, "
            "bookmaker, last_seen_at, commission_rate, bet_delay, total_available, last_match_time, total_matched) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(market_id) DO UPDATE SET "
            "  line = excluded.line, "
            "  period = excluded.period, "
            "  last_seen_at = excluded.last_seen_at, "
            "  commission_rate = excluded.commission_rate, "
            "  bet_delay = excluded.bet_delay, "
            "  total_available = excluded.total_available, "
            "  last_match_time = excluded.last_match_time, "
            "  total_matched = excluded.total_matched",
            (
                market.market_id, market.event_id, market.market_type.value,
                market.period, market.line, bookmaker, now,
                market.commission_rate, market.bet_delay, market.total_available, last_match,
                market.total_matched,
            ),
        )

    @staticmethod
    def _outcome_in(conn, outcome: CanonicalOutcome, now: str) -> None:
        conn.execute(
            "INSERT INTO outcomes (outcome_id, market_id, outcome_type, team_reference, "
            "line, last_seen_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(outcome_id) DO UPDATE SET team_reference = excluded.team_reference, "
            "  line = excluded.line, last_seen_at = excluded.last_seen_at",
            (
                outcome.outcome_id, outcome.market_id, outcome.outcome_type.value,
                outcome.team_reference, outcome.line, now,
            ),
        )

    @staticmethod
    def _quote_in(conn, quote: OddsQuote) -> bool:
        observed = _iso(quote.timestamp_received)
        src = _iso(quote.source_timestamp) if quote.source_timestamp else None
        row = conn.execute(
            "SELECT decimal_odds FROM quote_latest WHERE bookmaker = ? AND outcome_id = ?",
            (quote.bookmaker, quote.outcome_id),
        ).fetchone()
        # Only decimal_odds (= best back for Betfair) triggers history. Depth
        # ticks every second; appending it would explode quote_history.
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
            # On change, last_changed_at = the moment we saw the new price.
            conn.execute(
                "INSERT OR REPLACE INTO quote_latest (bookmaker, outcome_id, decimal_odds, "
                " source_timestamp, observed_at, last_changed_at, status, back_size, lay_price, lay_size, "
                " back_price_2, back_size_2, lay_price_2, lay_size_2, total_matched) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (quote.bookmaker, quote.outcome_id, quote.decimal_odds, src, observed,
                 observed, quote.status.value,
                 quote.back_size, quote.lay_price, quote.lay_size,
                 quote.back_price_2, quote.back_size_2, quote.lay_price_2,
                 quote.lay_size_2, quote.total_matched),
            )
        else:
            # Refresh observed_at + status + depth on unchanged-price tick, but
            # leave last_changed_at frozen — that's the whole point of this path.
            # COALESCE backfills rows that pre-date the column on their first tick.
            # Invariant: only Betfair rows ever carry non-NULL depth; Stoix/Novi
            # mappers leave the depth fields as None, so this writes NULL over NULL.
            conn.execute(
                "UPDATE quote_latest SET observed_at = ?, source_timestamp = ?, status = ?, "
                " last_changed_at = COALESCE(last_changed_at, ?), "
                " back_size = ?, lay_price = ?, lay_size = ?, "
                " back_price_2 = ?, back_size_2 = ?, lay_price_2 = ?, lay_size_2 = ?, "
                " total_matched = ? "
                "WHERE bookmaker = ? AND outcome_id = ?",
                (observed, src, quote.status.value, observed,
                 quote.back_size, quote.lay_price, quote.lay_size,
                 quote.back_price_2, quote.back_size_2, quote.lay_price_2,
                 quote.lay_size_2, quote.total_matched,
                 quote.bookmaker, quote.outcome_id),
            )
        return changed

    def upsert_event(self, event: CanonicalEvent, *, bookmaker: str,
                     native_event_id: str, sportradar_match_id: Optional[int]) -> None:
        now = _iso(datetime.now(timezone.utc))
        with self._connect() as conn:
            self._event_in(conn, event, bookmaker, native_event_id, sportradar_match_id, now)

    def upsert_market(self, market: CanonicalMarket, *, bookmaker: str) -> None:
        with self._connect() as conn:
            self._market_in(conn, market, bookmaker, _iso(datetime.now(timezone.utc)))

    def upsert_outcome(self, outcome: CanonicalOutcome) -> None:
        with self._connect() as conn:
            self._outcome_in(conn, outcome, _iso(datetime.now(timezone.utc)))

    def upsert_quote(self, quote: OddsQuote) -> bool:
        """Append-on-change: write a quote_history row only when the price differs
        from the last seen one. Returns True if a history row was appended."""
        with self._connect() as conn:
            return self._quote_in(conn, quote)

    def write_bundles(self, bundles: list, counts: dict, seen: dict) -> tuple[int, int]:
        """Write a whole cycle's worth of MappedEvent bundles in ONE transaction
        (one commit/fsync instead of thousands). Mutates `counts`/`seen` with the
        per-'Bookmaker/sport' tallies. Returns (quotes_observed, quotes_changed)."""
        now = _iso(datetime.now(timezone.utc))
        total_obs = total_chg = 0
        with self._connect() as conn:
            for b in bundles:
                self._event_in(conn, b.event, b.bookmaker, b.native_event_id,
                               b.sportradar_match_id, now)
                for m in b.markets:
                    self._market_in(conn, m, b.bookmaker, now)
                for o in b.outcomes:
                    self._outcome_in(conn, o, now)
                changed = sum(1 for q in b.quotes if self._quote_in(conn, q))
                observed = len(b.quotes)
                total_obs += observed
                total_chg += changed

                key = f"{b.bookmaker}/{b.event.sport}"
                c = counts.setdefault(
                    key, {"events": 0, "markets": 0, "quotes_observed": 0, "quotes_changed": 0})
                s = seen.setdefault(key, {"events": set(), "markets": set()})
                s["events"].add(b.event.event_id)
                s["markets"].update(m.market_id for m in b.markets)
                c["events"] = len(s["events"])
                c["markets"] = len(s["markets"])
                c["quotes_observed"] += observed
                c["quotes_changed"] += changed
        return total_obs, total_chg

    def get_event(self, event_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
        return self._event_to_dict(row) if row else None

    def get_events_by_sport(
        self,
        sport: str,
        *,
        status: str = "live",
        fresh_within_seconds: Optional[int] = None,
    ) -> list[dict]:
        params: list = [sport, status]
        sql = "SELECT * FROM events WHERE sport = ? AND status = ?"
        if fresh_within_seconds is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(seconds=fresh_within_seconds)
            sql += " AND last_seen_at >= ?"
            params.append(_iso(cutoff))
        sql += " ORDER BY last_seen_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._event_to_dict(r) for r in rows]

    def get_markets_for_event(self, event_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM markets WHERE event_id = ?", (event_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_outcomes_for_market(self, market_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE market_id = ?", (market_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_latest_quotes_for_outcome(self, outcome_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM quote_latest WHERE outcome_id = ?", (outcome_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def get_headline_rows(
        self, sport: str, market_type: str, *, status: str = "live",
        fresh_within_seconds: int = 120,
    ) -> list[dict]:
        """One JOIN: every live event of a sport with its headline-market outcomes
        and both books' latest quotes (events without that market still return,
        with null odds). Replaces per-event fan-out for the events list."""
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=fresh_within_seconds))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT e.event_id, e.home_team, e.away_team, e.competition, e.country, e.status, "
                "       e.start_time, e.sportradar_match_id, e.bookmaker_event_ids, "
                "       m.bookmaker, m.market_type, m.line AS m_line, m.period, "
                "       o.outcome_type, o.team_reference, o.line AS o_line, o.outcome_id, "
                "       ql.decimal_odds, ql.observed_at, ql.last_changed_at "
                "FROM events e "
                "LEFT JOIN markets m ON m.event_id = e.event_id AND m.market_type = ? "
                "  AND m.last_seen_at >= ? "
                "LEFT JOIN outcomes o ON o.market_id = m.market_id "
                "LEFT JOIN quote_latest ql ON ql.outcome_id = o.outcome_id AND ql.bookmaker = m.bookmaker "
                "WHERE e.sport = ? AND e.status = ? AND e.last_seen_at >= ? "
                "ORDER BY e.event_id",
                (market_type, cutoff, sport, status, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_event_market_rows(
        self, event_id: str, *, fresh_within_seconds: int = 120
    ) -> list[dict]:
        """One JOIN: every fresh market/outcome of an event with both books' latest
        quotes. Replaces per-market/per-outcome fan-out for the event detail."""
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=fresh_within_seconds))
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT m.market_type, m.period, m.line AS m_line, m.bookmaker, "
                "       o.outcome_type, o.team_reference, o.line AS o_line, o.outcome_id, "
                "       ql.decimal_odds, ql.observed_at, ql.last_changed_at "
                "FROM markets m "
                "JOIN outcomes o ON o.market_id = m.market_id "
                "JOIN quote_latest ql ON ql.outcome_id = o.outcome_id AND ql.bookmaker = m.bookmaker "
                "WHERE m.event_id = ? AND m.last_seen_at >= ?",
                (event_id, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_observed_per_bookmaker(self) -> dict[str, str]:
        """Most-recent observed_at per bookmaker across quote_latest. The
        dashboard uses this to compute per-book freshness ("Xs ago") — it
        answers 'when did each book last give us a quote?' which captures
        silent failures (cycle ran, no quotes flowed) that the global last_run
        timestamp masks."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT bookmaker, MAX(observed_at) AS last_obs FROM quote_latest "
                "GROUP BY bookmaker"
            ).fetchall()
        return {r["bookmaker"]: r["last_obs"] for r in rows if r["last_obs"]}

    def get_all_quote_rows(
        self,
        sport: Optional[str] = None,
        *,
        fresh_seconds: int = 30,
    ) -> list[dict]:
        """Flat JOIN across every live event × market × outcome × bookmaker
        quote, with the exchange-only columns (back_size, lay_price, ...) so
        the strategy layer can compute Betfair fair-value midpoints in one
        pass. Filters to events status='live' + market last_seen + quote
        observed_at within `fresh_seconds`. Optional `sport` filter."""
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(seconds=fresh_seconds))
        params: list = [cutoff, cutoff, cutoff]
        sql = (
            "SELECT e.event_id, e.home_team, e.away_team, e.sport, e.competition, e.country, "
            "       m.market_type, m.period, m.line AS m_line, "
            "       m.total_matched AS market_total_matched, "
            "       o.outcome_type, o.team_reference, o.line AS o_line, o.outcome_id, "
            "       ql.bookmaker, ql.decimal_odds, ql.observed_at, ql.last_changed_at, "
            "       ql.back_size, ql.lay_price, ql.lay_size, ql.total_matched "
            "FROM events e "
            "JOIN markets m ON m.event_id = e.event_id "
            "JOIN outcomes o ON o.market_id = m.market_id "
            "JOIN quote_latest ql ON ql.outcome_id = o.outcome_id AND ql.bookmaker = m.bookmaker "
            "WHERE e.status = 'live' AND e.last_seen_at >= ? "
            "  AND m.last_seen_at >= ? "
            "  AND ql.observed_at >= ?"
        )
        if sport:
            sql += " AND e.sport = ?"
            params.append(sport)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_quote_history(
        self, outcome_id: str, bookmaker: str, limit: int = 200
    ) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM quote_history WHERE outcome_id = ? AND bookmaker = ? "
                "ORDER BY id DESC LIMIT ?",
                (outcome_id, bookmaker, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def prune_quote_history(self, older_than_days: int = 14) -> int:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=older_than_days))
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM quote_history WHERE observed_at < ?", (cutoff,)
            )
            return cur.rowcount

    @staticmethod
    def _event_to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        try:
            d["bookmaker_event_ids"] = json.loads(d["bookmaker_event_ids"])
        except (json.JSONDecodeError, TypeError):
            d["bookmaker_event_ids"] = {}
        return d
