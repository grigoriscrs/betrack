from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

SCAN_BASE = "https://scan-inbf.betfair.com"
ERO_BASE = "https://ero.betfair.com"
IPS_BASE = "https://ips.betfair.com"

# scan-inbf validates _ak: an EMPTY value is accepted (so are real captured ones),
# but arbitrary made-up values get a DSC-0034 fault. ero / ips ignore it entirely.
AK = ""

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en;q=0.9",
    "origin": "https://www.betfair.com",
    "referer": "https://www.betfair.com/exchange/plus/",
}

# Betfair eventTypeId values for our target sports (verified against scan-inbf facets).
EVENT_TYPE_FOOTBALL = 1
EVENT_TYPE_TENNIS = 2
EVENT_TYPE_BASKETBALL = 7522

# The full projection used to pull every field we'll need for a market.
BYMARKET_TYPES_FULL = (
    "MARKET_STATE,MARKET_RATES,MARKET_DESCRIPTION,EVENT,"
    "RUNNER_DESCRIPTION,RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST,"
    "RUNNER_METADATA,MARKET_LICENCE,MARKET_LINE_RANGE_INFO"
)
# Lightweight projection for repeat polling once descriptions are cached client-side.
BYMARKET_TYPES_PRICES_ONLY = "MARKET_STATE,RUNNER_STATE,RUNNER_EXCHANGE_PRICES_BEST"


class BetfairClient:
    """Betfair Exchange unauthenticated web API. Requires a UK (or other non-blocked
    EU country) outbound IP — Greek IPs get 403 from the app layer regardless of
    Cloudflare. TLS-fingerprint bot detection still applies; curl_cffi handles it."""

    def __init__(self, proxy: str | None = None) -> None:
        self._session: AsyncSession | None = None
        # Tunneling Betfair traffic through a UK exit (Pattern 2 — SSH SOCKS5 to
        # a UK VPS). Set BETRACK_BETFAIR_PROXY=socks5h://127.0.0.1:1080 to engage.
        self._proxy = proxy or os.environ.get("BETRACK_BETFAIR_PROXY")

    async def __aenter__(self) -> "BetfairClient":
        kwargs: dict[str, Any] = {"impersonate": "chrome", "headers": HEADERS, "timeout": 30}
        if self._proxy:
            kwargs["proxies"] = {"all": self._proxy}
        self._session = AsyncSession(**kwargs)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def list_in_play(self, sport_id: int, max_results: int = 100) -> dict:
        """Returns {facets, results, attachments}. Useful pieces:
          - attachments.events[<eventId>] = {name, openDate, competitionId, ...}
          - results = list of {eventId, marketId, competitionId, eventTypeId} tuples
        `selectBy: FIRST_TO_START` is critical — RANK orders by trading
        volume, so World Cup qualifiers etc. fill the result quota and crowd
        out actually-live matches. FIRST_TO_START sorts ascending by start
        time, surfacing in-play first then about-to-start. `inPlay: true`
        still also surfaces long-running outright markets whose openDate
        is in the future; the asymmetric ±window filter in
        betfair_mapper.live_event_ids drops those at mapper time."""
        body = {
            "filter": {
                "marketBettingTypes": [
                    "ODDS",
                    "ASIAN_HANDICAP_SINGLE_LINE",
                    "ASIAN_HANDICAP_DOUBLE_LINE",
                    "LINE",
                ],
                "exchangeIds": [1],
                "productTypes": ["EXCHANGE"],
                "eventTypeIds": [sport_id],
                "inPlay": True,
                "contentGroup": {"language": "en", "regionCode": "UK"},
                "selectBy": "FIRST_TO_START",
                "maxResults": max_results,
            },
            "facets": [
                {"type": "EVENT", "maxValues": max_results, "skipValues": 0, "applyNextTo": 0},
            ],
            "currencyCode": "GBP",
            "locale": "en_GB",
        }
        r = await self._session.post(
            f"{SCAN_BASE}/www/sports/navigation/facet/v1/search?_ak={AK}&alt=json",
            json=body,
            headers={"content-type": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    async def event_type_counts(self) -> dict:
        """Sport-level cardinality (how many EXCHANGE markets per eventTypeId).
        Useful for discovering eventTypeIds we don't have constants for."""
        body = {
            "filter": {
                "marketBettingTypes": [
                    "ASIAN_HANDICAP_SINGLE_LINE",
                    "ASIAN_HANDICAP_DOUBLE_LINE",
                    "ODDS",
                    "LINE",
                ],
                "exchangeIds": [1],
                "productTypes": ["EXCHANGE"],
                "contentGroup": {"language": "en", "regionCode": "UK"},
                "selectBy": "RANK",
                "maxResults": 0,
            },
            "textQuery": None,
            "facets": [{"type": "EVENT_TYPE", "maxValues": 50, "skipValues": 0, "applyNextTo": 0}],
            "currencyCode": "GBP",
            "locale": "en_GB",
        }
        r = await self._session.post(
            f"{SCAN_BASE}/www/sports/navigation/facet/v1/search?_ak={AK}&alt=json",
            json=body,
            headers={"content-type": "application/json"},
        )
        r.raise_for_status()
        return r.json()

    async def fetch_markets(
        self,
        market_ids: list[str],
        rollup_limit: int = 10,
        types: str = BYMARKET_TYPES_FULL,
    ) -> dict:
        ids = ",".join(market_ids)
        r = await self._session.get(
            f"{ERO_BASE}/www/sports/exchange/readonly/v1/bymarket"
            f"?_ak={AK}&alt=json&currencyCode=GBP&locale=en_GB"
            f"&marketIds={ids}&rollupLimit={rollup_limit}&rollupModel=STAKE&types={types}"
        )
        r.raise_for_status()
        return r.json()

    async def fetch_event_markets(self, event_ids: list[int]) -> dict:
        ids = ",".join(str(i) for i in event_ids)
        r = await self._session.get(
            f"{ERO_BASE}/www/sports/exchange/readonly/v1/byevent"
            f"?_ak={AK}&alt=json&currencyCode=GBP&locale=en_GB"
            f"&eventIds={ids}&rollupLimit=10&rollupModel=STAKE"
            f"&types=MARKET_STATE,EVENT,MARKET_DESCRIPTION"
        )
        r.raise_for_status()
        return r.json()

    async def fetch_scores(self, event_ids: list[int]) -> dict:
        ids = ",".join(str(i) for i in event_ids)
        r = await self._session.get(
            f"{IPS_BASE}/inplayservice/v1/scoresAndBroadcast"
            f"?_ak={AK}&alt=json&eventIds={ids}&locale=en_GB&regionCode=UK"
        )
        r.raise_for_status()
        return r.json()


async def _smoke() -> None:
    """python -m betrack.ingestion.betfair → list live football events and
    print sample back/lay prices. Requires a UK outbound IP."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    async with BetfairClient() as bf:
        listing = await bf.list_in_play(EVENT_TYPE_FOOTBALL, max_results=50)
        events = (listing.get("attachments") or {}).get("events") or {}
        results = listing.get("results") or []
        logger.info("scan-inbf: %d events in attachments, %d (event,market) tuples in results",
                    len(events), len(results))
        # show a few events
        for eid, ev in list(events.items())[:5]:
            logger.info("  ev %s | %s | opens %s", eid, ev.get("name"), ev.get("openDate"))

        # seed bymarket from results (already includes a representative market per event)
        seed_market_ids = [r["marketId"] for r in results[:6] if "marketId" in r]
        if not seed_market_ids:
            logger.warning("no seed market IDs — exiting")
            return

        data = await bf.fetch_markets(seed_market_ids)
        for et in data.get("eventTypes", []):
            for en in et.get("eventNodes", []):
                name = (en.get("event") or {}).get("eventName") or f"event {en.get('eventId')}"
                for m in en.get("marketNodes", []):
                    mkt = (m.get("description") or {}).get("marketName", "?")
                    state = m.get("state") or {}
                    matched = state.get("totalMatched", 0) or 0
                    inplay = state.get("inplay")
                    logger.info("  %s | %s | inplay=%s matched=£%.0f", name, mkt, inplay, matched)
                    for runner in (m.get("runners") or [])[:3]:
                        rname = (runner.get("description") or {}).get("runnerName", "?")
                        back = ((runner.get("exchange") or {}).get("availableToBack") or [{}])[0]
                        lay = ((runner.get("exchange") or {}).get("availableToLay") or [{}])[0]
                        logger.info(
                            "    %-30s back=%s@£%s  lay=%s@£%s",
                            rname[:30],
                            back.get("price"), back.get("size"),
                            lay.get("price"), lay.get("size"),
                        )


if __name__ == "__main__":
    asyncio.run(_smoke())
