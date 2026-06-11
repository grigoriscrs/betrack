import asyncio
import logging
import time

from betrack.ingestion.betfair import BetfairClient
from betrack.ingestion.novibet import NovibetClient
from betrack.ingestion.pamestoixima import PamestoiximaClient
from betrack.ingestion.stoiximan import StoiximanClient
from betrack.pipeline import run_cycle
from betrack.store.odds_store_sqlite import SqliteOddsStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Cycle cadence. Live odds move on goal/timeout-scale (seconds), so 30s
# bookmaker-side staleness produces phantom cross-book arbs. With parallel
# fetches a cycle runs ~3-6s, so 10s is the politely-tight floor; MIN_SLEEP
# guarantees a pause between cycles even if one finishes faster.
POLL_INTERVAL = 10
MIN_SLEEP = 2


async def run() -> None:
    store = SqliteOddsStore()
    store.prune_quote_history()

    async with StoiximanClient() as stoiximan, \
               NovibetClient() as novibet, \
               PamestoiximaClient() as pamestoixima, \
               BetfairClient() as betfair:
        logger.info("BETrack started — Stoiximan + Novibet + Pamestoixima + Betfair")
        logger.info("  sports: football/basketball/tennis  poll: %ds", POLL_INTERVAL)

        while True:
            t0 = time.monotonic()
            try:
                result = await run_cycle(stoiximan, novibet, pamestoixima, betfair, store)
                for key, c in sorted(result.counts.items()):
                    logger.info(
                        "  %-22s events=%d markets=%d quotes=%d changed=%d",
                        key, c["events"], c["markets"], c["quotes_observed"], c["quotes_changed"],
                    )
                logger.info(
                    "cycle done in %.1fs: observed=%d changed=%d errors=%s",
                    time.monotonic() - t0,
                    result.total_observed, result.total_changed, result.errors or "none",
                )
            except Exception as exc:
                logger.error("poll cycle failed: %s", exc)

            await asyncio.sleep(max(MIN_SLEEP, POLL_INTERVAL - (time.monotonic() - t0)))


if __name__ == "__main__":
    asyncio.run(run())
