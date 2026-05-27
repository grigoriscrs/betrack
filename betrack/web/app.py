from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from betrack.alerts.engine import AlertEngine
from betrack.ingestion.client import OddsApiClient
from betrack.labels import market_label, outcome_label
from betrack.models.canonical import EventStatus
from betrack.pipeline import run_cycle
from betrack.store.history import HistoryStore
from betrack.store.odds_store import OddsStore

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.environ["API_KEY"]
BOOKMAKERS = ["Stoiximan", "Novibet"]
REFERENCE = "Stoiximan"
POLL_INTERVAL = 180
MAX_EVENTS_PER_CYCLE = 5
STATIC_DIR = Path(__file__).parent / "static"


class Runtime:
    """Owns the shared state: a single poll loop writes into the store + history,
    request handlers read from them. One process, one poller — respects quota."""

    def __init__(self) -> None:
        self.store = OddsStore()
        self.alerts = AlertEngine()
        self.history = HistoryStore()
        self.client = OddsApiClient(API_KEY, BOOKMAKERS)
        self.status: dict = {
            "last_run": None,
            "live": 0,
            "prematch": 0,
            "scanned": 0,
            "covered": 0,
            "quota_remaining": None,
            "bookmakers": BOOKMAKERS,
            "reference": REFERENCE,
            "poll_interval": POLL_INTERVAL,
        }
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        await self.client.__aenter__()
        await self.client.select_bookmakers()
        self.history.reset_active()
        self._task = asyncio.create_task(self._loop())
        logger.info("BETrack web runtime started")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.client.__aexit__(None, None, None)

    async def _loop(self) -> None:
        while True:
            try:
                await self._cycle()
            except Exception as exc:
                logger.error("cycle failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)

    async def _cycle(self) -> None:
        result = await run_cycle(self.client, self.store, MAX_EVENTS_PER_CYCLE)
        now = result.ran_at
        seen: set[str] = set()

        for opp in result.value_opps:
            event = self.store.get_event(opp.event_id)
            market = self.store._markets.get(opp.market_id)
            outcome = self.store._outcomes.get(opp.outcome_id)
            sig = self.history.record_value(
                event_id=opp.event_id,
                market_id=opp.market_id,
                outcome_id=opp.outcome_id,
                bookmaker=opp.bookmaker,
                bookmaker_odds=opp.bookmaker_odds,
                reference_odds=opp.reference_odds,
                edge_pct=opp.edge_pct,
                event_label=f"{event.home_team} vs {event.away_team}",
                competition=event.competition,
                status=event.status.value,
                market_label=market_label(market) if market else opp.market_id,
                outcome_label=outcome_label(outcome) if outcome else opp.outcome_id,
                now=now,
            )
            seen.add(sig)
            if self.alerts.evaluate_value(opp, event.status == EventStatus.LIVE):
                self.history.mark_alerted(sig)

        for opp in result.arb_opps:
            event = self.store.get_event(opp.event_id)
            market = self.store._markets.get(opp.market_id)
            legs_display = {}
            for outcome_id, (bookmaker, odds) in opp.legs.items():
                outcome = self.store._outcomes.get(outcome_id)
                label = outcome_label(outcome) if outcome else outcome_id
                legs_display[label] = {"bookmaker": bookmaker, "odds": odds}
            sig = self.history.record_arb(
                event_id=opp.event_id,
                market_id=opp.market_id,
                margin=opp.margin,
                legs_display=legs_display,
                event_label=f"{event.home_team} vs {event.away_team}",
                competition=event.competition,
                status=event.status.value,
                market_label=market_label(market) if market else opp.market_id,
                now=now,
            )
            seen.add(sig)
            if self.alerts.evaluate_arbitrage(opp):
                self.history.mark_alerted(sig)

        self.history.expire_missing(seen)
        self.status.update(
            last_run=now.isoformat(),
            live=result.live_count,
            prematch=result.prematch_count,
            scanned=result.scanned,
            covered=result.covered,
            quota_remaining=result.quota_remaining,
        )
        logger.info(
            "cycle done: coverage %d/%d, value=%d arb=%d",
            result.covered, result.scanned, len(result.value_opps), len(result.arb_opps),
        )


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


@app.get("/api/opportunities")
async def opportunities() -> list[dict]:
    return app.state.runtime.history.active()


@app.get("/api/history")
async def history(limit: int = 100) -> list[dict]:
    return app.state.runtime.history.history(limit=limit)


@app.get("/api/status")
async def status() -> dict:
    return app.state.runtime.status


@app.get("/api/events/{event_id}")
async def event_detail(event_id: str) -> dict:
    """Full snapshot of one event: every market/outcome with both bookmakers'
    odds side by side (and the per-outcome edge). Powers the drill-down view.
    Reads the in-memory store, so it reflects the most recent cycle."""
    store = app.state.runtime.store
    event = store.get_event(event_id)
    if event is None:
        return {"found": False}

    markets = []
    for m in store.get_markets_for_event(event_id):
        outcomes = []
        for o in store.get_outcomes_for_market(m.market_id):
            quotes = {q.bookmaker: q.decimal_odds for q in store.get_quotes_for_outcome(o.outcome_id)}
            ref = quotes.get(REFERENCE)
            edge = None
            if ref:
                others = [v for k, v in quotes.items() if k != REFERENCE]
                if others:
                    edge = max(others) / ref - 1.0
            outcomes.append({
                "outcome_label": outcome_label(o),
                "outcome_type": o.outcome_type.value,
                "quotes": quotes,
                "edge_pct": edge,
            })
        markets.append({
            "market_label": market_label(m),
            "market_type": m.market_type.value,
            "line": m.line,
            "outcomes": outcomes,
        })

    return {
        "found": True,
        "event_id": event.event_id,
        "event_label": f"{event.home_team} vs {event.away_team}",
        "competition": event.competition,
        "status": event.status.value,
        "start_time": event.start_time.isoformat(),
        "bookmakers": BOOKMAKERS,
        "reference": REFERENCE,
        "markets": markets,
    }


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
