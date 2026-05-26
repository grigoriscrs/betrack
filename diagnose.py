"""One-shot diagnostic: poll once, print every comparable outcome with its edge."""
import asyncio
import logging
import os
from datetime import datetime, timezone

from dotenv import load_dotenv

from betrack.comparison.engine import find_arbitrage
from betrack.ingestion.client import OddsApiClient
from betrack.normalization.mapper import map_event, map_odds
from betrack.store.odds_store import OddsStore

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

API_KEY = os.environ["API_KEY"]
BOOKMAKERS = ["Stoiximan", "Novibet"]


async def run() -> None:
    store = OddsStore()
    async with OddsApiClient(API_KEY, BOOKMAKERS) as client:
        await client.select_bookmakers()
        prematch = await client.get_prematch_events(limit=30)
        now = datetime.now(timezone.utc)

        covered = 0
        for raw_event in prematch[:8]:
            try:
                raw_odds = await client.get_odds(raw_event["id"])
            except Exception as exc:
                print(f"  fetch failed for {raw_event['id']}: {exc}")
                continue
            if not raw_odds.get("bookmakers"):
                continue
            covered += 1

            event = map_event(raw_event)
            store.upsert_event(event)
            markets, outcomes, quotes = map_odds(raw_odds, event, now)
            for m in markets:
                store.upsert_market(m)
            for o in outcomes:
                store.upsert_outcome(o)
            for q in quotes:
                store.upsert_quote(q)

            print(f"\n=== {event.home_team} vs {event.away_team} [{event.competition}] ===")
            for market in store.get_markets_for_event(event.event_id):
                line_str = f" line={market.line}" if market.line is not None else ""
                print(f"  Market: {market.market_type.value}{line_str}")
                for outcome in store.get_outcomes_for_market(market.market_id):
                    q = store.get_quotes_for_outcome(outcome.outcome_id)
                    if len(q) < 2:
                        # Print single-bookmaker outcomes too
                        for qq in q:
                            print(f"    {outcome.outcome_type.value:<10} {qq.bookmaker:<10} @ {qq.decimal_odds}")
                        continue
                    quotes_by_bm = {qq.bookmaker: qq.decimal_odds for qq in q}
                    sto = quotes_by_bm.get("Stoiximan")
                    nov = quotes_by_bm.get("Novibet")
                    if sto and nov:
                        edge = (nov / sto - 1) * 100
                        marker = "  <-- VALUE" if abs(edge) >= 2.5 else ""
                        print(f"    {outcome.outcome_type.value:<10} Stoix={sto:<6}  Novi={nov:<6}  edge(Novi vs Stoix)={edge:+.2f}%{marker}")

            arbs = find_arbitrage(store, event.event_id)
            for arb in arbs:
                print(f"  ARBITRAGE: margin={arb.margin*100:.2f}% legs={arb.legs}")

        print(f"\nScanned 8 prematch events. {covered} had Stoiximan/Novibet coverage.")


if __name__ == "__main__":
    asyncio.run(run())
