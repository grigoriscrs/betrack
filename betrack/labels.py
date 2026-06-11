from __future__ import annotations

from betrack.models.canonical import (
    CanonicalMarket,
    CanonicalOutcome,
    MarketType,
    OutcomeType,
)

_MARKET_LABELS = {
    MarketType.FOOTBALL_FULL_TIME_1X2: "1X2",
    MarketType.FOOTBALL_FULL_TIME_OVER_UNDER: "Over/Under",
    MarketType.FOOTBALL_FULL_TIME_BTTS: "BTTS",
    MarketType.FOOTBALL_DOUBLE_CHANCE: "Double Chance",
    MarketType.FOOTBALL_DRAW_NO_BET: "Draw No Bet",
    MarketType.FOOTBALL_HALFTIME_FULLTIME: "Half Time / Full Time",
    MarketType.BASKETBALL_MATCH_WINNER: "Match Winner",
    MarketType.BASKETBALL_TOTAL_POINTS: "Total Points",
    MarketType.BASKETBALL_HANDICAP: "Handicap",
    MarketType.TENNIS_MATCH_WINNER: "Match Winner",
    MarketType.TENNIS_TOTAL_GAMES: "Total Games",
    MarketType.TENNIS_SET_WINNER: "Set Winner",
}

_OUTCOME_LABELS = {
    OutcomeType.HOME_WIN: "Home",
    OutcomeType.DRAW: "Draw",
    OutcomeType.AWAY_WIN: "Away",
    OutcomeType.OVER: "Over",
    OutcomeType.UNDER: "Under",
    OutcomeType.BTTS_YES: "BTTS Yes",
    OutcomeType.BTTS_NO: "BTTS No",
    OutcomeType.DOUBLE_CHANCE_HOME_DRAW: "1X",
    OutcomeType.DOUBLE_CHANCE_HOME_AWAY: "12",
    OutcomeType.DOUBLE_CHANCE_DRAW_AWAY: "X2",
}

_LINE_OUTCOMES = (OutcomeType.OVER, OutcomeType.UNDER, OutcomeType.HOME_WIN, OutcomeType.AWAY_WIN)


def market_label(market: CanonicalMarket) -> str:
    base = _MARKET_LABELS.get(market.market_type, market.market_type.value)
    if market.line is not None:
        return f"{base} {market.line}"
    return base


def outcome_label(outcome: CanonicalOutcome) -> str:
    base = _OUTCOME_LABELS.get(outcome.outcome_type, outcome.outcome_type.value)
    if outcome.team_reference and outcome.outcome_type in (OutcomeType.HOME_WIN, OutcomeType.AWAY_WIN):
        base = outcome.team_reference
    if outcome.line is not None and outcome.outcome_type in _LINE_OUTCOMES:
        return f"{base} {outcome.line:+g}" if outcome.outcome_type in (OutcomeType.HOME_WIN, OutcomeType.AWAY_WIN) else f"{base} {outcome.line}"
    return base
