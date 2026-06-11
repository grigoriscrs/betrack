from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from betrack.ingestion.betfair import BetfairClient
from betrack.ingestion.novibet import NovibetClient
from betrack.ingestion.pamestoixima import PamestoiximaClient
from betrack.ingestion.stoiximan import StoiximanClient
from betrack.labels import market_label, outcome_label
from betrack.models.canonical import (
    CanonicalMarket,
    CanonicalOutcome,
    MarketType,
    OutcomeType,
)
from betrack.pipeline import run_cycle
from betrack.store.history import HistoryStore
from betrack.store.odds_store_sqlite import SqliteOddsStore
from betrack.strategy import find_opportunities

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

BOOKMAKERS = ["Stoiximan", "Novibet", "Pamestoixima", "Betfair"]
# Cycle cadence. Live odds move on goal/timeout-scale (seconds), so 30s
# bookmaker-side staleness produces phantom cross-book arbs. With parallel
# fetches a cycle runs ~3-6s, so 10s is the politely-tight floor; MIN_SLEEP
# guarantees a pause between cycles even if one finishes faster.
POLL_INTERVAL = 10
MIN_SLEEP = 2
# Events not re-seen within this window are treated as no longer live.
FRESH_SECONDS = 120
STATIC_DIR = Path(__file__).parent / "static"

SPORTS = [
    {"key": "football", "label": "Football"},
    {"key": "basketball", "label": "Basketball"},
    {"key": "tennis", "label": "Tennis"},
]

# The headline market shown on each event row, per sport.
HEADLINE = {
    "football": MarketType.FOOTBALL_FULL_TIME_1X2.value,
    "basketball": MarketType.BASKETBALL_MATCH_WINNER.value,
    "tennis": MarketType.TENNIS_MATCH_WINNER.value,
}

_OUTCOME_ORDER = {
    OutcomeType.HOME_WIN.value: 0, OutcomeType.DRAW.value: 1, OutcomeType.AWAY_WIN.value: 2,
    OutcomeType.OVER.value: 0, OutcomeType.UNDER.value: 1,
    OutcomeType.BTTS_YES.value: 0, OutcomeType.BTTS_NO.value: 1,
    OutcomeType.DOUBLE_CHANCE_HOME_DRAW.value: 0, OutcomeType.DOUBLE_CHANCE_HOME_AWAY.value: 1,
    OutcomeType.DOUBLE_CHANCE_DRAW_AWAY.value: 2,
}


class Runtime:
    """One process, one poller: a background task writes both bookmakers' live
    odds into SQLite; request handlers only read. Detection is suspended."""

    def __init__(self) -> None:
        self.store = SqliteOddsStore()
        self.history = HistoryStore()  # idle until detection is re-enabled
        self.stoiximan = StoiximanClient()
        self.novibet = NovibetClient()
        self.pamestoixima = PamestoiximaClient()
        # Reads BETRACK_BETFAIR_PROXY itself; routes via UK SSH SOCKS tunnel
        # when set, else fetches will 403 from Greek IP and the cycle skips it.
        self.betfair = BetfairClient()
        self.status: dict = {
            "last_run": None,
            "poll_interval": POLL_INTERVAL,
            "bookmakers": BOOKMAKERS,
            "counts": {},
            "total_observed": 0,
            "total_changed": 0,
            "errors": [],
            "detection": "live",
        }
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.stoiximan.__aenter__()
        await self.novibet.__aenter__()
        await self.pamestoixima.__aenter__()
        await self.betfair.__aenter__()
        self.store.prune_quote_history()
        self._task = asyncio.create_task(self._loop())
        logger.info("BETrack runtime started — Stoiximan + Novibet + Pamestoixima + Betfair")
        logger.info("  sports: football/basketball/tennis  poll: %ds", POLL_INTERVAL)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # LIFO close; isolate each so one failure doesn't strand the others.
        for client in (self.betfair, self.pamestoixima, self.novibet, self.stoiximan):
            try:
                await client.__aexit__(None, None, None)
            except Exception as exc:
                logger.warning("client shutdown failed: %s", exc)

    async def _loop(self) -> None:
        while True:
            t0 = time.monotonic()
            try:
                result = await run_cycle(
                    self.stoiximan, self.novibet, self.pamestoixima, self.betfair, self.store,
                )
                elapsed = time.monotonic() - t0
                self.status.update(
                    last_run=result.ran_at.isoformat(),
                    counts=result.counts,
                    total_observed=result.total_observed,
                    total_changed=result.total_changed,
                    errors=result.errors,
                    cycle_seconds=round(elapsed, 2),
                )
                logger.info(
                    "cycle done in %.1fs: observed=%d changed=%d errors=%d",
                    elapsed,
                    result.total_observed, result.total_changed, len(result.errors),
                )
            except Exception as exc:
                logger.error("cycle failed: %s", exc)
            await asyncio.sleep(max(MIN_SLEEP, POLL_INTERVAL - (time.monotonic() - t0)))


def _age(now: datetime, ts: str | None) -> int | None:
    """Seconds since the given UTC ISO timestamp. Source-of-truth selection
    (last_changed_at vs observed_at) is the caller's job — this helper just
    subtracts. Source clocks (`source_timestamp`) are deliberately ignored:
    the bookmakers have inconsistent semantics for that field."""
    if not ts:
        return None
    try:
        return int((now - datetime.fromisoformat(ts)).total_seconds())
    except ValueError:
        return None


def _market_label(market_type: str, line: float | None) -> str:
    return market_label(CanonicalMarket(
        market_id="_", event_id="_", market_type=MarketType(market_type), line=line,
    ))


def _outcome_label(outcome_type: str, team_reference: str | None, line: float | None) -> str:
    return outcome_label(CanonicalOutcome(
        outcome_id="_", market_id="_", outcome_type=OutcomeType(outcome_type),
        team_reference=team_reference, line=line,
    ))


def _build_markets(rows: list[dict], now: datetime) -> list[dict]:
    """Collapse flat (market × outcome × bookmaker-quote) rows into cross-book
    market groups keyed by (market_type, period, line) and (outcome_type, line),
    with each bookmaker's latest quote side by side. Rows come from the store's
    JOIN helpers, so this does no per-row DB work."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        if r["market_type"] is None or r["outcome_type"] is None or r["decimal_odds"] is None:
            continue  # LEFT JOIN filler for an event with no fresh headline market
        mkey = (r["market_type"], r["period"], r["m_line"])
        g = groups.setdefault(mkey, {
            "market_type": r["market_type"], "period": r["period"],
            "line": r["m_line"], "outcomes": {},
        })
        okey = (r["outcome_type"], r["o_line"])
        og = g["outcomes"].setdefault(okey, {
            "outcome_type": r["outcome_type"], "line": r["o_line"],
            "team_reference": r["team_reference"], "books": {},
        })
        if r["team_reference"]:
            og["team_reference"] = r["team_reference"]
        og["books"][r["bookmaker"]] = {
            "odds": r["decimal_odds"],
            # The age badge tracks the price-change time so a quote frozen at
            # the same odds for many cycles correctly appears stale, even though
            # we keep re-confirming it from the bookmaker every poll. Fallback
            # to observed_at for rows that pre-date the column.
            "stamp": r.get("last_changed_at") or r["observed_at"],
            "outcome_id": r["outcome_id"],
        }

    out: list[dict] = []
    for g in groups.values():
        outcomes = []
        for og in g["outcomes"].values():
            quotes = {
                bm: {"odds": q["odds"], "age_seconds": _age(now, q["stamp"]),
                     "outcome_id": q["outcome_id"]}
                for bm, q in og["books"].items()
            }
            vals = [q["odds"] for q in og["books"].values()]
            best = max(og["books"], key=lambda b: og["books"][b]["odds"]) if len(vals) > 1 else None
            gap = (max(vals) / min(vals) - 1.0) if len(vals) > 1 else None
            outcomes.append({
                "outcome_type": og["outcome_type"],
                "label": _outcome_label(og["outcome_type"], og["team_reference"], og["line"]),
                "line": og["line"], "quotes": quotes, "best": best, "gap_pct": gap,
            })
        outcomes.sort(key=lambda o: (_OUTCOME_ORDER.get(o["outcome_type"], 9), o["line"] or 0))
        out.append({
            "market_type": g["market_type"],
            "market_label": _market_label(g["market_type"], g["line"]),
            "period": g["period"], "line": g["line"], "outcomes": outcomes,
        })
    out.sort(key=lambda m: (m["market_type"], m["line"] or 0))
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    runtime = Runtime()
    await runtime.start()
    app.state.runtime = runtime
    try:
        yield
    finally:
        await runtime.stop()


app = FastAPI(title="BETrack", lifespan=lifespan)


@app.get("/api/status")
async def status() -> dict:
    runtime = app.state.runtime
    # Always-fresh per-book observation timestamps from the store. Cheap (one
    # GROUP BY across at most ~5k rows of quote_latest) so do it per request
    # rather than mirror state in the runtime status dict.
    return {**runtime.status,
            "book_last_observed": runtime.store.latest_observed_per_bookmaker()}


@app.get("/api/sports")
def sports() -> list[dict]:
    store = app.state.runtime.store
    out = []
    for s in SPORTS:
        live = store.get_events_by_sport(s["key"], fresh_within_seconds=FRESH_SECONDS)
        out.append({**s, "live_count": len(live)})
    return out


@app.get("/api/events")
def events(sport: str) -> list[dict]:
    store = app.state.runtime.store
    now = datetime.now(timezone.utc)
    rows = store.get_headline_rows(sport, HEADLINE.get(sport, ""), fresh_within_seconds=FRESH_SECONDS)
    by_event: dict[str, dict] = {}
    for r in rows:
        ev = by_event.setdefault(r["event_id"], {"meta": r, "rows": []})
        ev["rows"].append(r)
    out = []
    for data in by_event.values():
        meta = data["meta"]
        try:
            books = list(json.loads(meta["bookmaker_event_ids"]).keys())
        except (json.JSONDecodeError, TypeError):
            books = []
        headline = _build_markets(data["rows"], now)
        out.append({
            "event_id": meta["event_id"],
            "home_team": meta["home_team"],
            "away_team": meta["away_team"],
            "competition": meta["competition"],
            "country": meta.get("country"),
            "status": meta["status"],
            "start_time": meta["start_time"],
            "sportradar_match_id": meta["sportradar_match_id"],
            "books": books,
            "headline": headline[0] if headline else None,
        })
    out.sort(key=lambda e: (len(e["books"]) < 2, e["home_team"]))
    return out


@app.get("/api/event/{event_id}")
def event_detail(event_id: str) -> dict:
    store = app.state.runtime.store
    now = datetime.now(timezone.utc)
    ev = store.get_event(event_id)
    if ev is None:
        return {"found": False}
    rows = store.get_event_market_rows(event_id, fresh_within_seconds=FRESH_SECONDS)
    return {
        "found": True,
        "event_id": ev["event_id"],
        "home_team": ev["home_team"],
        "away_team": ev["away_team"],
        "competition": ev["competition"],
        "country": ev.get("country"),
        "status": ev["status"],
        "start_time": ev["start_time"],
        "sport": ev["sport"],
        "sportradar_match_id": ev["sportradar_match_id"],
        "books": list(ev["bookmaker_event_ids"].keys()),
        "markets": _build_markets(rows, now),
    }


@app.get("/api/quote-history/{outcome_id}")
def quote_history(outcome_id: str, bookmaker: str, limit: int = 200) -> list[dict]:
    store = app.state.runtime.store
    return store.get_quote_history(outcome_id, bookmaker, limit=limit)


@app.get("/api/opportunities")
def opportunities(
    min_edge: float = 0.03,
    min_gap: float = 0.15,
    min_gap_high_odds: float = 0.50,
    min_roi: float = 0.05,
    sport: str | None = None,
    fresh_seconds: int = 30,
) -> dict:
    """Recompute on every request — cheap at this scale + lets the UI slider
    re-tune without a backend redeploy. Returns {value, arb, diffs}."""
    return find_opportunities(
        app.state.runtime.store,
        sport=sport,
        min_edge=min_edge,
        min_gap=min_gap,
        min_gap_high_odds=min_gap_high_odds,
        min_roi=min_roi,
        fresh_seconds=fresh_seconds,
    )


@app.get("/api/history")
async def history(limit: int = 100) -> list[dict]:
    return []


# Serve the built React SPA when present, falling back to the Alpine page.
DIST_DIR = STATIC_DIR / "dist"
if (DIST_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST_DIR / "assets"), name="assets")


@app.get("/")
async def index() -> FileResponse:
    dist_index = DIST_DIR / "index.html"
    if dist_index.exists():
        return FileResponse(dist_index)
    return FileResponse(STATIC_DIR / "index.html")
