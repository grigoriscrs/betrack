from __future__ import annotations

from betrack.models.canonical import (
    CanonicalMarket,
    CanonicalOutcome,
    MarketType,
    OutcomeType,
)

_MARKET_LABELS = {
    MarketType.FULL_TIME_1X2: "1X2",
    MarketType.FULL_TIME_OVER_UNDER: "Over/Under",
    MarketType.FULL_TIME_BTTS: "BTTS",
}

_OUTCOME_LABELS = {
    OutcomeType.HOME_WIN: "Home",
    OutcomeType.DRAW: "Draw",
    OutcomeType.AWAY_WIN: "Away",
    OutcomeType.OVER: "Over",
    OutcomeType.UNDER: "Under",
    OutcomeType.BTTS_YES: "BTTS Yes",
    OutcomeType.BTTS_NO: "BTTS No",
}


def market_label(market: CanonicalMarket) -> str:
    base = _MARKET_LABELS.get(market.market_type, market.market_type.value)
    if market.line is not None:
        return f"{base} {market.line}"
    return base


def outcome_label(outcome: CanonicalOutcome) -> str:
    base = _OUTCOME_LABELS.get(outcome.outcome_type, outcome.outcome_type.value)
    if outcome.line is not None and outcome.outcome_type in (OutcomeType.OVER, OutcomeType.UNDER):
        return f"{base} {outcome.line}"
    return base
