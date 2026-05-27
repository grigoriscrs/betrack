from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://api.odds-api.io/v3"


class OddsApiClient:
    def __init__(self, api_key: str, bookmakers: list[str]):
        self._api_key = api_key
        self._bookmakers = bookmakers
        self._session: aiohttp.ClientSession | None = None
        self.last_quota_remaining: str | None = None

    async def __aenter__(self) -> OddsApiClient:
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    def _params(self, **kwargs: Any) -> dict:
        return {"apiKey": self._api_key, **{k: v for k, v in kwargs.items() if v is not None}}

    async def _get(self, path: str, **params: Any) -> Any:
        url = f"{BASE_URL}{path}"
        async with self._session.get(url, params=self._params(**params)) as resp:
            self.last_quota_remaining = resp.headers.get("x-ratelimit-remaining")
            logger.debug("GET %s status=%d quota_remaining=%s", path, resp.status, self.last_quota_remaining)
            resp.raise_for_status()
            return await resp.json()

    async def select_bookmakers(self) -> dict:
        """Register the bookmakers on this API key. Free tier requires this first."""
        url = f"{BASE_URL}/bookmakers/selected/select"
        params = self._params(bookmakers=",".join(self._bookmakers))
        async with self._session.put(url, params=params) as resp:
            data = await resp.json()
            if resp.status >= 400:
                # 400 here is common if bookmakers are already selected; treat as info.
                logger.info("select_bookmakers status=%d body=%s", resp.status, data)
            return data

    async def get_selected_bookmakers(self) -> dict:
        return await self._get("/bookmakers/selected")

    async def get_live_events(self) -> list[dict]:
        result = await self._get("/events/live", sport="football")
        return result if isinstance(result, list) else result.get("data", [])

    async def get_prematch_events(self, limit: int = 50) -> list[dict]:
        result = await self._get("/events", sport="football", status="pending", limit=limit)
        return result if isinstance(result, list) else result.get("data", [])

    async def get_odds(self, event_id: int) -> dict:
        return await self._get(
            "/odds",
            eventId=event_id,
            bookmakers=",".join(self._bookmakers),
        )

    async def get_bookmakers(self) -> list[dict]:
        return await self._get("/bookmakers")
