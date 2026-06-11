from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class EventStatus(str, Enum):
    PREMATCH = "prematch"
    LIVE = "live"
    SETTLED = "settled"
    SUSPENDED = "suspended"


class MarketType(str, Enum):
    # Football
    FOOTBALL_FULL_TIME_1X2 = "football.full_time.1x2"
    FOOTBALL_FULL_TIME_OVER_UNDER = "football.full_time.over_under"
    FOOTBALL_FULL_TIME_BTTS = "football.full_time.btts"
    FOOTBALL_DOUBLE_CHANCE = "football.full_time.double_chance"
    FOOTBALL_DRAW_NO_BET = "football.full_time.draw_no_bet"
    FOOTBALL_HALFTIME_FULLTIME = "football.halftime_fulltime"
    # Basketball
    BASKETBALL_MATCH_WINNER = "basketball.match.winner"
    BASKETBALL_TOTAL_POINTS = "basketball.match.total_points"
    BASKETBALL_HANDICAP = "basketball.match.handicap"
    # Tennis
    TENNIS_MATCH_WINNER = "tennis.match.winner"
    TENNIS_TOTAL_GAMES = "tennis.match.total_games"
    TENNIS_SET_WINNER = "tennis.set.winner"
    TENNIS_HANDICAP = "tennis.match.handicap"


class OutcomeType(str, Enum):
    HOME_WIN = "home_win"
    DRAW = "draw"
    AWAY_WIN = "away_win"
    OVER = "over"
    UNDER = "under"
    BTTS_YES = "btts_yes"
    BTTS_NO = "btts_no"
    # Double chance (1X / 12 / X2)
    DOUBLE_CHANCE_HOME_DRAW = "double_chance_home_draw"
    DOUBLE_CHANCE_HOME_AWAY = "double_chance_home_away"
    DOUBLE_CHANCE_DRAW_AWAY = "double_chance_draw_away"


class OddsStatus(str, Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    SETTLED = "settled"


class CanonicalEvent(BaseModel):
    event_id: str
    sport: str
    competition: str
    home_team: str
    away_team: str
    start_time: datetime
    status: EventStatus
    # Country/region the league belongs to, populated by mappers from each
    # book's region field (Stoix `zone.name`, Novi `competition.regionCaption`,
    # Betfair `event.countryCode`). Optional because we don't always have it.
    country: Optional[str] = None


class CanonicalMarket(BaseModel):
    market_id: str
    event_id: str
    market_type: MarketType
    period: str = "full_time"
    line: Optional[float] = None
    settlement_scope: str = "full_time"

    # Betfair-only market metadata; None for Stoiximan / Novibet.
    commission_rate: Optional[float] = None
    bet_delay: Optional[int] = None
    total_available: Optional[float] = None
    last_match_time: Optional[datetime] = None
    # Cumulative volume actually traded on this market (sum across all runners).
    # Sourced from Betfair's `state.totalMatched`. The corresponding runner-level
    # totalMatched returns 0 in live responses, so this is the canonical
    # liquidity signal for the strategy layer's sharp-reference gate.
    total_matched: Optional[float] = None


class CanonicalOutcome(BaseModel):
    outcome_id: str
    market_id: str
    outcome_type: OutcomeType
    team_reference: Optional[str] = None
    line: Optional[float] = None


class OddsQuote(BaseModel):
    bookmaker: str
    event_id: str
    market_id: str
    outcome_id: str
    decimal_odds: float
    timestamp_received: datetime
    source_timestamp: Optional[datetime] = None
    status: OddsStatus = OddsStatus.ACTIVE
    liquidity: Optional[float] = None
    raw_payload_reference: Optional[str] = None

    # Betfair-only exchange depth + volume; None for Stoiximan / Novibet.
    # decimal_odds is the best back price; the rest of the top-2 ladder lives here.
    back_size: Optional[float] = None
    lay_price: Optional[float] = None
    lay_size: Optional[float] = None
    back_price_2: Optional[float] = None
    back_size_2: Optional[float] = None
    lay_price_2: Optional[float] = None
    lay_size_2: Optional[float] = None
    total_matched: Optional[float] = None
