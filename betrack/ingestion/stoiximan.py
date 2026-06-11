from __future__ import annotations

import logging
from typing import Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

BASE = "https://en.stoiximan.gr"
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "en-GB,en;q=0.9",
    "referer": f"{BASE}/live/",
    "x-language": "1",
    "x-operator": "2",
}


class StoiximanClient:
    """Stoiximan live odds feed. Cloudflare requires curl_cffi TLS impersonation;
    plain aiohttp/requests get a 403."""

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "StoiximanClient":
        self._session = AsyncSession(impersonate="chrome", headers=HEADERS, timeout=30)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def fetch_overview(self) -> dict:
        r = await self._session.get(
            f"{BASE}/danae-webapi/api/live/overview/0?isInit=true&includeVirtuals=true"
        )
        r.raise_for_status()
        return r.json()

    async def fetch_event(self, event_id: int | str) -> dict:
        r = await self._session.get(
            f"{BASE}/danae-webapi/api/live/events/{event_id}/latest"
        )
        r.raise_for_status()
        return r.json()
