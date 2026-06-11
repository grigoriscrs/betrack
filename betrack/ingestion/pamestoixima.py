from __future__ import annotations

import asyncio
import logging
from typing import Any

from curl_cffi.requests import AsyncSession

logger = logging.getLogger(__name__)

BASE = "https://capi.pamestoixima.gr"
SITE = "https://www.pamestoixima.gr"
# x-accept-language flips the response payload's localisable strings (team /
# competition / country / market names) between Greek and English. The HAR
# was captured in Greek but we ask for English here so team_reference values
# match Stoiximan/Novibet — they quote Latin-script names too, and the
# strategy layer's _team_refs_agree gate would otherwise reject every Pame
# leg as a cross-match mismatch.
HEADERS = {
    "accept": "*/*",
    "accept-language": "en-GB,en-US;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": SITE,
    "referer": f"{SITE}/",
    "x-accept-language": "en-GB",
    "x-ob-channel": "I",
}

# Pamestoixima exposes a per-sport drilldown taxonomy — football/basketball/
# tennis are three distinct top-level "drilldown tag" IDs, verified via the
# getLiveDrilldownSports endpoint. eSports (id 24) is deliberately omitted.
SPORT_IDS: dict[str, str] = {
    "football": "11",
    "basketball": "5",
    "tennis": "12",
}

# Headline groupCodes requested per sport for the overview call. The detail
# call returns all markets unfiltered, so this is just about keeping the
# overview payload from ballooning to the full ~40+ groupCodes per event.
#
# Pamestoixima's groupCode taxonomy differs by sport:
#  * Football match winner = MATCH_RESULT (1X2, three-way)
#  * Basketball match winner = MONEY_LINE (two-way, includes OT). MATCH_RESULT
#    on basketball is the regulation-time 3-way market (with a Draw leg) and
#    is rarely useful for cross-book comparison since Stoix/Novi quote 2-way.
#  * Tennis match winner = MATCH_WINNER (two-way, no draw possible).
_OVERVIEW_GROUP_CODES: dict[str, str] = {
    "football":   "MATCH_RESULT,TOTAL_GOALS_OVER/UNDER",
    "basketball": "MONEY_LINE,TOTAL_POINTS_OVER/UNDER",
    "tennis":     "MATCH_WINNER,TOTAL_GAMES_OVER/UNDER",
}


class PamestoiximaClient:
    """Pamestoixima (OPAP) live odds feed. Same Cloudflare TLS-impersonation
    requirement as Stoiximan/Novibet. Distinct from those two in that the
    overview is per-sport — there's no all-sports-in-one-call endpoint — so
    `fetch_overview` issues three concurrent requests internally and merges
    them. Per-event detail mirrors the other clients (one call per event id)."""

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "PamestoiximaClient":
        self._session = AsyncSession(impersonate="chrome", headers=HEADERS, timeout=30)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def _fetch_sport_overview(self, sport_id: str, groups: str) -> list[dict]:
        url = (
            f"{BASE}/content-service/api/v1/q/getLiveEvents"
            f"?drilldownTagIds={sport_id}"
            f"&limit=500&orderEventsBy=upperHierarchy"
            f"&marketGroupCodesIncluded={groups}"
        )
        r = await self._session.get(url)
        r.raise_for_status()
        data = r.json()
        return (data.get("data") or {}).get("events") or []

    async def fetch_overview(self) -> dict:
        """Fetch headline live events for all three target sports in parallel
        and return them in a single envelope. Each event's
        sportDrilldownTagId identifies its sport — the mapper resolves that
        back to football/basketball/tennis."""
        results = await asyncio.gather(
            *(self._fetch_sport_overview(sid, _OVERVIEW_GROUP_CODES[slug])
              for slug, sid in SPORT_IDS.items()),
            return_exceptions=True,
        )
        events: list[dict] = []
        for slug, res in zip(SPORT_IDS.keys(), results):
            if isinstance(res, Exception):
                logger.warning("pamestoixima overview/%s failed: %s", slug, res)
                continue
            events.extend(res)
        return {"events": events}

    async def fetch_event(self, event_id: int | str) -> dict:
        """Full market set for a single event. `oddsTypeMarket=FIXED` returns
        decimal-odds prices (vs fractional/spread). includeCommentary off —
        we don't need the play-by-play text."""
        url = (
            f"{BASE}/content-service/api/v1/q/getLiveEventDetails"
            f"?eventIds={event_id}&oddsTypeMarket=FIXED&includeCommentary=false"
        )
        r = await self._session.get(url)
        r.raise_for_status()
        return r.json()
