from __future__ import annotations

import logging
from typing import Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

BASE = "https://www.novibet.gr"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en;q=0.9",
    "referer": f"{BASE}/en/live-betting",
    "x-gw-application-name": "Novi",
    "x-gw-channel": "WebPC",
    "x-gw-client-timezone": "Europe/Athens",
    "x-gw-cms-key": "_GR",
    "x-gw-country-sysname": "GR",
    "x-gw-currency-sysname": "EUR",
    "x-gw-domain-key": "_GR",
    "x-gw-language-sysname": "en-US",
    "x-gw-odds-representation": "Decimal",
    "x-gw-original-referer": f"{BASE}/stoixima",
}

_COMMON = "lang=en-US&timeZ=GTB%20Standard%20Time&oddsR=1&usrGrp=GR"
# The live overview at location 4390 returns every sport in one response, and
# the per-event feed uses the same fixed 4324 segment for all sports (verified
# via HAR captures for basketball/tennis). It is not a per-sport id.
LIVE_LOCATION = "4324/4390"
EVENT_SEGMENT = "4324"


class NovibetClient:
    """Novibet live odds feed. Cloudflare requires curl_cffi TLS impersonation."""

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "NovibetClient":
        self._session = AsyncSession(impersonate="chrome", headers=HEADERS, timeout=30)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def fetch_overview(self) -> list:
        r = await self._session.get(
            f"{BASE}/spt/feed/marketviews/location/v2/{LIVE_LOCATION}/?{_COMMON}&timestamp=0"
        )
        r.raise_for_status()
        return r.json()

    async def fetch_event(self, event_id: int | str) -> dict:
        r = await self._session.get(
            f"{BASE}/spt/feed/marketviews/event/{EVENT_SEGMENT}/{event_id}"
            f"?{_COMMON}&timestamp=0&filterAlias="
        )
        r.raise_for_status()
        return r.json()
