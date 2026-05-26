from __future__ import annotations

from betrack.models.canonical import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    OddsQuote,
)


class OddsStore:
    def __init__(self) -> None:
        self._events: dict[str, CanonicalEvent] = {}
        self._markets: dict[str, CanonicalMarket] = {}
        self._outcomes: dict[str, CanonicalOutcome] = {}
        # (bookmaker, outcome_id) → latest quote
        self._quotes: dict[tuple[str, str], OddsQuote] = {}

    def upsert_event(self, event: CanonicalEvent) -> None:
        self._events[event.event_id] = event

    def upsert_market(self, market: CanonicalMarket) -> None:
        self._markets[market.market_id] = market

    def upsert_outcome(self, outcome: CanonicalOutcome) -> None:
        self._outcomes[outcome.outcome_id] = outcome

    def upsert_quote(self, quote: OddsQuote) -> None:
        self._quotes[(quote.bookmaker, quote.outcome_id)] = quote

    def get_event(self, event_id: str) -> CanonicalEvent | None:
        return self._events.get(event_id)

    def get_markets_for_event(self, event_id: str) -> list[CanonicalMarket]:
        return [m for m in self._markets.values() if m.event_id == event_id]

    def get_outcomes_for_market(self, market_id: str) -> list[CanonicalOutcome]:
        return [o for o in self._outcomes.values() if o.market_id == market_id]

    def get_quotes_for_outcome(self, outcome_id: str) -> list[OddsQuote]:
        return [q for (_, oid), q in self._quotes.items() if oid == outcome_id]

    def all_events(self) -> list[CanonicalEvent]:
        return list(self._events.values())
