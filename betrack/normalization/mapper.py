from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from betrack.models.canonical import (
    CanonicalEvent,
    CanonicalMarket,
    CanonicalOutcome,
    EventStatus,
    MarketType,
    OddsQuote,
    OddsStatus,
    OutcomeType,
)

# Team name variants → canonical name
TEAM_ALIASES: dict[str, str] = {
    "olympiacos": "Olympiacos",
    "olympiakos": "Olympiacos",
    "olympiakos piraeus": "Olympiacos",
    "ολυμπιακός": "Olympiacos",
    "paok": "PAOK",
    "paok fc": "PAOK",
    "aek": "AEK Athens",
    "aek athens": "AEK Athens",
    "panathinaikos": "Panathinaikos",
    "panathinaikos fc": "Panathinaikos",
}

# API market name → canonical MarketType
MARKET_NAME_MAP: dict[str, MarketType] = {
    "ml": MarketType.FULL_TIME_1X2,
    "match line": MarketType.FULL_TIME_1X2,
    "1x2": MarketType.FULL_TIME_1X2,
    "match result": MarketType.FULL_TIME_1X2,
    "match odds": MarketType.FULL_TIME_1X2,
    "totals": MarketType.FULL_TIME_OVER_UNDER,
    "over/under": MarketType.FULL_TIME_OVER_UNDER,
    "total goals": MarketType.FULL_TIME_OVER_UNDER,
    "btts": MarketType.FULL_TIME_BTTS,
    "both teams to score": MarketType.FULL_TIME_BTTS,
    "gg/ng": MarketType.FULL_TIME_BTTS,
    "goal/no goal": MarketType.FULL_TIME_BTTS,
}

_EVENT_STATUS_MAP: dict[str, EventStatus] = {
    "pending": EventStatus.PREMATCH,
    "live": EventStatus.LIVE,
    "settled": EventStatus.SETTLED,
    "suspended": EventStatus.SUSPENDED,
}


def normalize_team(name: str) -> str:
    return TEAM_ALIASES.get(name.lower().strip(), name.strip())


def normalize_market(name: str) -> Optional[MarketType]:
    return MARKET_NAME_MAP.get(name.lower().strip())


def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def map_event(raw: dict) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=str(raw["id"]),
        sport="football",
        competition=raw.get("league", {}).get("name", "Unknown"),
        home_team=normalize_team(raw["home"]),
        away_team=normalize_team(raw["away"]),
        start_time=_parse_dt(raw["date"]),
        status=_EVENT_STATUS_MAP.get(raw.get("status", "pending"), EventStatus.PREMATCH),
    )


def map_odds(
    raw_odds: dict,
    event: CanonicalEvent,
    received_at: datetime,
) -> tuple[list[CanonicalMarket], list[CanonicalOutcome], list[OddsQuote]]:
    markets: list[CanonicalMarket] = []
    outcomes: list[CanonicalOutcome] = []
    quotes: list[OddsQuote] = []
    seen_markets: set[str] = set()

    for bookmaker, raw_markets in raw_odds.get("bookmakers", {}).items():
        for raw_market in raw_markets:
            market_type = normalize_market(raw_market.get("name", ""))
            if market_type is None:
                continue

            updated_at_raw = raw_market.get("updatedAt")
            source_ts = _parse_dt(updated_at_raw) if updated_at_raw else None

            for odds_entry in raw_market.get("odds", []):
                line = _extract_line(odds_entry)
                market_id = _make_id(event.event_id, market_type.value, str(line or ""))

                if market_id not in seen_markets:
                    markets.append(CanonicalMarket(
                        market_id=market_id,
                        event_id=event.event_id,
                        market_type=market_type,
                        line=line,
                    ))
                    seen_markets.add(market_id)

                for outcome, price in _extract_outcomes(market_type, odds_entry, market_id, event, line):
                    outcomes.append(outcome)
                    quotes.append(OddsQuote(
                        bookmaker=bookmaker,
                        event_id=event.event_id,
                        market_id=market_id,
                        outcome_id=outcome.outcome_id,
                        decimal_odds=price,
                        timestamp_received=received_at,
                        source_timestamp=source_ts,
                        status=OddsStatus.ACTIVE,
                    ))

    return markets, outcomes, quotes


def _extract_line(odds_entry: dict) -> Optional[float]:
    for key in ("hdp", "handicap", "line", "point"):
        val = odds_entry.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass
    return None


def _extract_outcomes(
    market_type: MarketType,
    odds_entry: dict,
    market_id: str,
    event: CanonicalEvent,
    line: Optional[float],
) -> list[tuple[CanonicalOutcome, float]]:
    results = []

    if market_type == MarketType.FULL_TIME_1X2:
        for key, outcome_type, team_ref in [
            ("home", OutcomeType.HOME_WIN, event.home_team),
            ("draw", OutcomeType.DRAW, None),
            ("away", OutcomeType.AWAY_WIN, event.away_team),
        ]:
            price = odds_entry.get(key)
            if price is not None:
                results.append((
                    CanonicalOutcome(
                        outcome_id=_make_id(market_id, outcome_type.value),
                        market_id=market_id,
                        outcome_type=outcome_type,
                        team_reference=team_ref,
                    ),
                    float(price),
                ))

    elif market_type == MarketType.FULL_TIME_OVER_UNDER:
        for key, outcome_type in [("over", OutcomeType.OVER), ("under", OutcomeType.UNDER)]:
            price = odds_entry.get(key)
            if price is not None:
                results.append((
                    CanonicalOutcome(
                        outcome_id=_make_id(market_id, outcome_type.value, str(line or "")),
                        market_id=market_id,
                        outcome_type=outcome_type,
                        line=line,
                    ),
                    float(price),
                ))

    elif market_type == MarketType.FULL_TIME_BTTS:
        for key, outcome_type in [("yes", OutcomeType.BTTS_YES), ("no", OutcomeType.BTTS_NO)]:
            price = odds_entry.get(key)
            if price is not None:
                results.append((
                    CanonicalOutcome(
                        outcome_id=_make_id(market_id, outcome_type.value),
                        market_id=market_id,
                        outcome_type=outcome_type,
                    ),
                    float(price),
                ))

    return results
