import asyncio
import logging
import os

from dotenv import load_dotenv

from betrack.alerts.engine import AlertEngine
from betrack.delivery.console import print_arb_alert, print_value_alert
from betrack.ingestion.client import OddsApiClient
from betrack.models.canonical import EventStatus
from betrack.pipeline import run_cycle
from betrack.store.odds_store import OddsStore

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

API_KEY = os.environ["API_KEY"]
BOOKMAKERS = ["Stoiximan", "Novibet"]

# Free tier: 100 req/hr. Each cycle costs 2 (events) + up to MAX_EVENTS_PER_CYCLE
# odds calls. POLL_INTERVAL keeps us comfortably under quota.
POLL_INTERVAL = 180
MAX_EVENTS_PER_CYCLE = 5


async def run() -> None:
    store = OddsStore()
    alerts = AlertEngine()

    async with OddsApiClient(API_KEY, BOOKMAKERS) as client:
        await client.select_bookmakers()
        logger.info("BETrack started — bookmakers: %s, poll interval: %ds", BOOKMAKERS, POLL_INTERVAL)

        while True:
            try:
                result = await run_cycle(client, store, MAX_EVENTS_PER_CYCLE)
                logger.info(
                    "%d live / %d prematch; coverage %d/%d; value=%d arb=%d",
                    result.live_count, result.prematch_count, result.covered, result.scanned,
                    len(result.value_opps), len(result.arb_opps),
                )

                for opp in result.value_opps:
                    event = store.get_event(opp.event_id)
                    is_live = event.status == EventStatus.LIVE
                    if alerts.evaluate_value(opp, is_live):
                        print_value_alert(opp, event, store)

                for opp in result.arb_opps:
                    event = store.get_event(opp.event_id)
                    if alerts.evaluate_arbitrage(opp):
                        print_arb_alert(opp, event, store)
            except Exception as exc:
                logger.error("Poll cycle failed: %s", exc)

            await asyncio.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    asyncio.run(run())
