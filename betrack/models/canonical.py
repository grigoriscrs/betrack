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
    FULL_TIME_1X2 = "football.full_time.1x2"
    FULL_TIME_OVER_UNDER = "football.full_time.over_under"
    FULL_TIME_BTTS = "football.full_time.btts"


class OutcomeType(str, Enum):
    HOME_WIN = "home_win"
    DRAW = "draw"
    AWAY_WIN = "away_win"
    OVER = "over"
    UNDER = "under"
    BTTS_YES = "btts_yes"
    BTTS_NO = "btts_no"


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


class CanonicalMarket(BaseModel):
    market_id: str
    event_id: str
    market_type: MarketType
    period: str = "full_time"
    line: Optional[float] = None
    settlement_scope: str = "full_time"


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
