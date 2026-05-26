import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from betrack.alerts.engine import AlertEngine
from betrack.comparison.engine import find_arbitrage, find_value
from betrack.delivery.console import print_arb_alert, print_value_alert
from betrack.ingestion.client import OddsApiClient
from betrack.normalization.mapper import map_event, map_odds
from betrack.store.odds_store import OddsStore

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.environ["API_KEY"]
BOOKMAKERS = ["Stoiximan", "Novibet"]

# Free tier: 100 req/hr. Each cycle costs: 1 (live) + 1 (prematch) + N odds calls.
# Capping events at MAX_EVENTS_PER_CYCLE keeps us well under quota.
POLL_INTERVAL = 180          # 3 minutes between cycles
MAX_EVENTS_PER_CYCLE = 5     # check at most 5 events per cycle


async def fetch_event_odds(client: OddsApiClient, raw_events: list[dict]) -> list[tuple[dict, dict]]:
    pairs: list[tuple[dict, dict]] = []
    for raw_event in raw_events:
        try:
            odds = await client.get_odds(raw_event["id"])
        except Exception as exc:
            logger.warning("Odds fetch failed for event %s: %s", raw_event["id"], exc)
            continue
        if odds.get("bookmakers"):
            pairs.append((raw_event, odds))
    return pairs


async def poll_cycle(client: OddsApiClient, store: OddsStore, alerts: AlertEngine) -> None:
    now = datetime.now(timezone.utc)

    live = await client.get_live_events()
    prematch = await client.get_prematch_events(limit=30)
    logger.info("found %d live / %d prematch football events", len(live), len(prematch))

    # Prefer prematch events: they have wider Greek-bookmaker coverage than
    # live events at off-peak hours (most live events are obscure leagues).
    candidates = (prematch + live)[:MAX_EVENTS_PER_CYCLE]
    pairs = await fetch_event_odds(client, candidates)
    logger.info("events with Stoiximan/Novibet coverage: %d/%d", len(pairs), len(candidates))

    for raw_event, raw_odds in pairs:
        event = map_event(raw_event)
        store.upsert_event(event)

        markets, outcomes, quotes = map_odds(raw_odds, event, now)
        for m in markets:
            store.upsert_market(m)
        for o in outcomes:
            store.upsert_outcome(o)
        for q in quotes:
            store.upsert_quote(q)

        is_live = event.status.value == "live"

        for opp in find_value(store, event.event_id):
            if alerts.evaluate_value(opp, is_live):
                print_value_alert(opp, event, store)

        for opp in find_arbitrage(store, event.event_id):
            if alerts.evaluate_arbitrage(opp):
                print_arb_alert(opp, event, store)


async def run() -> None:
    store = OddsStore()
    alerts = AlertEngine()

    async with OddsApiClient(API_KEY, BOOKMAKERS) as client:
        await client.select_bookmakers()
        selected = await client.get_selected_bookmakers()
        logger.info("BETrack started — selected bookmakers: %s", selected)
        logger.info("poll interval: %ds, max events per cycle: %d", POLL_INTERVAL, MAX_EVENTS_PER_CYCLE)

        while True:
            try:
                await poll_cycle(client, store, alerts)
            except Exception as exc:
                logger.error("Poll cycle failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
