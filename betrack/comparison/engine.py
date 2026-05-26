from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from betrack.models.canonical import MarketType, OddsQuote, OutcomeType
from betrack.store.odds_store import OddsStore

# With only 2 bookmakers available (stoiximan + novibet), we use one as a soft
# reference. This is intentionally simplified — a proper sharp line (Betfair,
# Pinnacle) replaces this in Phase 6+.
REFERENCE_BOOKMAKER = "Stoiximan"


@dataclass
class ValueOpportunity:
    event_id: str
    market_id: str
    outcome_id: str
    bookmaker: str
    bookmaker_odds: float
    reference_odds: float
    edge_pct: float
    timestamp: datetime


@dataclass
class ArbitrageOpportunity:
    event_id: str
    market_id: str
    # outcome_id → (bookmaker, odds)
    legs: dict[str, tuple[str, float]]
    margin: float
    timestamp: datetime


def _best_reference_odds(quotes: list[OddsQuote], reference: str) -> float | None:
    ref = next((q for q in quotes if q.bookmaker == reference), None)
    return ref.decimal_odds if ref else None


def _edge(bookmaker_odds: float, fair_odds: float) -> float:
    return (bookmaker_odds / fair_odds) - 1.0


def find_value(
    store: OddsStore,
    event_id: str,
    min_edge: float = 0.05,
) -> list[ValueOpportunity]:
    results: list[ValueOpportunity] = []
    now = datetime.now(timezone.utc)

    for market in store.get_markets_for_event(event_id):
        for outcome in store.get_outcomes_for_market(market.market_id):
            quotes = store.get_quotes_for_outcome(outcome.outcome_id)
            if len(quotes) < 2:
                continue

            fair = _best_reference_odds(quotes, REFERENCE_BOOKMAKER)
            if fair is None:
                continue

            for q in quotes:
                if q.bookmaker == REFERENCE_BOOKMAKER:
                    continue
                edge = _edge(q.decimal_odds, fair)
                if edge >= min_edge:
                    results.append(ValueOpportunity(
                        event_id=event_id,
                        market_id=market.market_id,
                        outcome_id=outcome.outcome_id,
                        bookmaker=q.bookmaker,
                        bookmaker_odds=q.decimal_odds,
                        reference_odds=fair,
                        edge_pct=edge,
                        timestamp=now,
                    ))

    return results


def find_arbitrage(
    store: OddsStore,
    event_id: str,
) -> list[ArbitrageOpportunity]:
    results: list[ArbitrageOpportunity] = []
    now = datetime.now(timezone.utc)

    for market in store.get_markets_for_event(event_id):
        if market.market_type != MarketType.FULL_TIME_1X2:
            continue

        outcomes = store.get_outcomes_for_market(market.market_id)
        if {o.outcome_type for o in outcomes} != {OutcomeType.HOME_WIN, OutcomeType.DRAW, OutcomeType.AWAY_WIN}:
            continue

        legs: dict[str, tuple[str, float]] = {}
        for outcome in outcomes:
            quotes = store.get_quotes_for_outcome(outcome.outcome_id)
            if not quotes:
                continue
            best = max(quotes, key=lambda q: q.decimal_odds)
            legs[outcome.outcome_id] = (best.bookmaker, best.decimal_odds)

        if len(legs) != 3:
            continue

        total_prob = sum(1.0 / odds for _, odds in legs.values())
        if total_prob < 1.0:
            results.append(ArbitrageOpportunity(
                event_id=event_id,
                market_id=market.market_id,
                legs=legs,
                margin=1.0 - total_prob,
                timestamp=now,
            ))

    return results
