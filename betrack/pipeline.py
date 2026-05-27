from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from betrack.alerts.engine import MIN_EDGE_LIVE, MIN_EDGE_PREMATCH
from betrack.comparison.engine import (
    ArbitrageOpportunity,
    ValueOpportunity,
    find_arbitrage,
    find_value,
)
from betrack.ingestion.client import OddsApiClient
from betrack.models.canonical import EventStatus
from betrack.normalization.mapper import map_event, map_odds
from betrack.store.odds_store import OddsStore

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    ran_at: datetime
    live_count: int = 0
    prematch_count: int = 0
    scanned: int = 0
    covered: int = 0
    quota_remaining: str | None = None
    value_opps: list[ValueOpportunity] = field(default_factory=list)
    arb_opps: list[ArbitrageOpportunity] = field(default_factory=list)


async def run_cycle(
    client: OddsApiClient,
    store: OddsStore,
    max_events: int,
    prematch_limit: int = 30,
) -> CycleResult:
    """Fetch one round of events + odds, normalize into the store, and detect
    value/arbitrage opportunities. Prematch events are preferred over live ones
    because Greek-bookmaker live coverage is sparse at off-peak hours."""
    now = datetime.now(timezone.utc)
    result = CycleResult(ran_at=now)

    live = await client.get_live_events()
    prematch = await client.get_prematch_events(limit=prematch_limit)
    result.live_count = len(live)
    result.prematch_count = len(prematch)

    candidates = (prematch + live)[:max_events]
    result.scanned = len(candidates)

    for raw_event in candidates:
        try:
            raw_odds = await client.get_odds(raw_event["id"])
        except Exception as exc:
            logger.warning("odds fetch failed for event %s: %s", raw_event.get("id"), exc)
            continue
        if not raw_odds.get("bookmakers"):
            continue
        result.covered += 1

        event = map_event(raw_event)
        store.upsert_event(event)
        markets, outcomes, quotes = map_odds(raw_odds, event, now)
        for m in markets:
            store.upsert_market(m)
        for o in outcomes:
            store.upsert_outcome(o)
        for q in quotes:
            store.upsert_quote(q)

        min_edge = MIN_EDGE_LIVE if event.status == EventStatus.LIVE else MIN_EDGE_PREMATCH
        result.value_opps.extend(find_value(store, event.event_id, min_edge=min_edge))
        result.arb_opps.extend(find_arbitrage(store, event.event_id))

    result.quota_remaining = client.last_quota_remaining
    return result
