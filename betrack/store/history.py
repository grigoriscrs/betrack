from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterable, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS opportunities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signature TEXT NOT NULL,
    kind TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_label TEXT NOT NULL,
    competition TEXT,
    status TEXT,
    market_label TEXT,
    outcome_label TEXT,
    bookmaker TEXT,
    bookmaker_odds REAL,
    reference_odds REAL,
    edge_pct REAL,
    margin REAL,
    legs_json TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    alerted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_active_sig ON opportunities(active, signature);
CREATE INDEX IF NOT EXISTS idx_last_seen ON opportunities(last_seen);
"""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class HistoryStore:
    """SQLite-backed record of opportunities over time.

    Each contiguous span an opportunity is observed (active) is one row, so
    `last_seen - first_seen` gives the opportunity's duration. When a previously
    seen opportunity reappears after expiring, a new row (occurrence) is created.
    """

    def __init__(self, path: str = "betrack.db") -> None:
        self._path = str(path)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def reset_active(self) -> None:
        """Clear stale active flags from a previous process on startup."""
        with self._connect() as conn:
            conn.execute("UPDATE opportunities SET active = 0 WHERE active = 1")

    def record_value(
        self,
        *,
        event_id: str,
        market_id: str,
        outcome_id: str,
        bookmaker: str,
        bookmaker_odds: float,
        reference_odds: float,
        edge_pct: float,
        event_label: str,
        competition: str,
        status: str,
        market_label: str,
        outcome_label: str,
        now: datetime,
    ) -> str:
        sig = f"value|{event_id}|{market_id}|{outcome_id}|{bookmaker}"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM opportunities WHERE active = 1 AND signature = ?", (sig,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE opportunities SET last_seen = ?, bookmaker_odds = ?, "
                    "reference_odds = ?, edge_pct = ?, status = ? WHERE id = ?",
                    (_iso(now), bookmaker_odds, reference_odds, edge_pct, status, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO opportunities (signature, kind, event_id, event_label, "
                    "competition, status, market_label, outcome_label, bookmaker, "
                    "bookmaker_odds, reference_odds, edge_pct, first_seen, last_seen) "
                    "VALUES (?, 'value', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sig, event_id, event_label, competition, status, market_label,
                        outcome_label, bookmaker, bookmaker_odds, reference_odds, edge_pct,
                        _iso(now), _iso(now),
                    ),
                )
        return sig

    def record_arb(
        self,
        *,
        event_id: str,
        market_id: str,
        margin: float,
        legs_display: dict,
        event_label: str,
        competition: str,
        status: str,
        market_label: str,
        now: datetime,
    ) -> str:
        sig = f"arb|{event_id}|{market_id}"
        legs_json = json.dumps(legs_display)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM opportunities WHERE active = 1 AND signature = ?", (sig,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE opportunities SET last_seen = ?, margin = ?, legs_json = ?, "
                    "status = ? WHERE id = ?",
                    (_iso(now), margin, legs_json, status, row["id"]),
                )
            else:
                conn.execute(
                    "INSERT INTO opportunities (signature, kind, event_id, event_label, "
                    "competition, status, market_label, margin, legs_json, first_seen, last_seen) "
                    "VALUES (?, 'arb', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        sig, event_id, event_label, competition, status, market_label,
                        margin, legs_json, _iso(now), _iso(now),
                    ),
                )
        return sig

    def mark_alerted(self, signature: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE opportunities SET alerted = 1 WHERE active = 1 AND signature = ?",
                (signature,),
            )

    def expire_missing(self, seen: Iterable[str]) -> None:
        seen_set = set(seen)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, signature FROM opportunities WHERE active = 1"
            ).fetchall()
            stale = [(r["id"],) for r in rows if r["signature"] not in seen_set]
            if stale:
                conn.executemany("UPDATE opportunities SET active = 0 WHERE id = ?", stale)

    def active(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM opportunities WHERE active = 1 "
                "ORDER BY COALESCE(edge_pct, margin) DESC"
            ).fetchall()
        return [self._to_dict(r) for r in rows]

    def history(self, limit: int = 100) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM opportunities ORDER BY last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._to_dict(r) for r in rows]

    @staticmethod
    def _to_dict(row: sqlite3.Row) -> dict:
        d = dict(row)
        legs_json = d.pop("legs_json", None)
        if legs_json:
            try:
                d["legs"] = json.loads(legs_json)
            except json.JSONDecodeError:
                d["legs"] = None
        try:
            first = datetime.fromisoformat(d["first_seen"])
            last = datetime.fromisoformat(d["last_seen"])
            d["duration_seconds"] = int((last - first).total_seconds())
        except (ValueError, KeyError):
            d["duration_seconds"] = 0
        d["active"] = bool(d["active"])
        d["alerted"] = bool(d["alerted"])
        return d
