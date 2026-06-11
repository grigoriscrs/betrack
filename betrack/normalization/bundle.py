from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from betrack.models.canonical import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    OddsQuote,
)


@dataclass
class MappedEvent:
    """Everything one bookmaker knows about one event in a single cycle, ready
    to upsert into the store. `event_id` is shared across bookmakers (keyed by
    sportradar id) while markets/outcomes/quotes are per-bookmaker."""

    event: CanonicalEvent
    bookmaker: str
    native_event_id: str
    sportradar_match_id: Optional[int]
    markets: list[CanonicalMarket] = field(default_factory=list)
    outcomes: list[CanonicalOutcome] = field(default_factory=list)
    quotes: list[OddsQuote] = field(default_factory=list)
