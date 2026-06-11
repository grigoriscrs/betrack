from __future__ import annotations

import hashlib
import logging
import re
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
from betrack.normalization.bundle import MappedEvent
from betrack.normalization.mapper import normalize_team

logger = logging.getLogger(__name__)

BOOKMAKER = "Novibet"

_SPORT = {"Soccer": "football", "Basketball": "basketball", "Tennis": "tennis"}

_OT = OutcomeType
# code -> OutcomeType, plus whether the market carries a line.
_CODES_1X2 = ({"1": _OT.HOME_WIN, "X": _OT.DRAW, "2": _OT.AWAY_WIN}, False)
_CODES_WINNER2 = ({"1": _OT.HOME_WIN, "2": _OT.AWAY_WIN}, False)
_CODES_OU = ({"O": _OT.OVER, "U": _OT.UNDER}, True)
_CODES_BTTS = ({"Y": _OT.BTTS_YES, "N": _OT.BTTS_NO, "Yes": _OT.BTTS_YES, "No": _OT.BTTS_NO}, False)
_CODES_DC = ({"1X": _OT.DOUBLE_CHANCE_HOME_DRAW, "X2": _OT.DOUBLE_CHANCE_DRAW_AWAY,
              "12": _OT.DOUBLE_CHANCE_HOME_AWAY}, False)
_CODES_HANDICAP = ({"1": _OT.HOME_WIN, "2": _OT.AWAY_WIN}, True)

# Novibet marketSysname -> (MarketType, period, code_map). Verified from live
# responses; unknown sysnames are skipped + logged once.
_MARKETS: dict[str, tuple[MarketType, str, tuple[dict, bool]]] = {
    "SOCCER_MATCH_RESULT": (MarketType.FOOTBALL_FULL_TIME_1X2, "full_time", _CODES_1X2),
    "SOCCER_UNDER_OVER": (MarketType.FOOTBALL_FULL_TIME_OVER_UNDER, "full_time", _CODES_OU),
    "SOCCER_BOTH_TEAMS_TO_SCORE": (MarketType.FOOTBALL_FULL_TIME_BTTS, "full_time", _CODES_BTTS),
    "SOCCER_DOUBLE_CHANCE": (MarketType.FOOTBALL_DOUBLE_CHANCE, "full_time", _CODES_DC),
    "SOCCER_MATCH_RESULT_NODRAW": (MarketType.FOOTBALL_DRAW_NO_BET, "full_time", _CODES_WINNER2),
    "BASKETBALL_MATCH_RESULT_NODRAW": (MarketType.BASKETBALL_MATCH_WINNER, "match", _CODES_WINNER2),
    "BASKETBALL_MATCH_RESULT_HANDICAP": (MarketType.BASKETBALL_HANDICAP, "match", _CODES_HANDICAP),
    "BASKETBALL_UNDER_OVER": (MarketType.BASKETBALL_TOTAL_POINTS, "match", _CODES_OU),
    "TENNIS_SINGLES_MATCH_WINNER": (MarketType.TENNIS_MATCH_WINNER, "match", _CODES_WINNER2),
    "TENNIS_SINGLES_MATCH_GAMES_UNDER_OVER": (MarketType.TENNIS_TOTAL_GAMES, "match", _CODES_OU),
    "TENNIS_SINGLES_SET_1_WINNER": (MarketType.TENNIS_SET_WINNER, "set_1", _CODES_WINNER2),
}

_warned: set[str] = set()
_TRAILING_NUMBER = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*$")


def _is_esports(*names: str) -> bool:
    # Novibet lists FIFA/NBA2K under Soccer/Basketball; competitor names carry "Esports".
    return any("esports" in (n or "").lower() for n in names)


def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def _warn_unknown(sysname: str) -> None:
    if sysname and sysname not in _warned:
        _warned.add(sysname)
        logger.info("unknown novibet market sysname=%r, skipping", sysname)


def _item_line(item: dict) -> Optional[float]:
    ic = item.get("instanceCaption")
    if ic is not None:
        try:
            return float(ic)
        except (TypeError, ValueError):
            pass
    m = _TRAILING_NUMBER.search(item.get("caption", "") or "")
    return float(m.group(1)) if m else None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _build_event(
    native_id: str, sport: str, home: str, away: str, competition: str,
    sportradar: Optional[int], source_ts: Optional[datetime], received_at: datetime,
    country: Optional[str] = None,
) -> tuple[str, CanonicalEvent, MappedEvent]:
    event_id = (
        _make_id(str(sportradar), sport) if sportradar
        else _make_id(BOOKMAKER, native_id, sport)
    )
    event = CanonicalEvent(
        event_id=event_id, sport=sport, competition=competition, country=country,
        home_team=home, away_team=away, start_time=source_ts or received_at,
        status=EventStatus.LIVE,
    )
    bundle = MappedEvent(
        event=event, bookmaker=BOOKMAKER, native_event_id=native_id,
        sportradar_match_id=sportradar,
    )
    return event_id, event, bundle


def _add_market(
    bundle: MappedEvent, event_id: str, home: str, away: str,
    sysname: str, bet_items: list, received_at: datetime, source_ts: Optional[datetime],
) -> None:
    spec = _MARKETS.get(sysname)
    if spec is None:
        _warn_unknown(sysname)
        return
    market_type, period, (code_map, has_line) = spec

    parsed: list[tuple[OutcomeType, float, Optional[float]]] = []
    for item in bet_items:
        if not item or item.get("isAvailable") is False:
            continue
        outcome_type = code_map.get(item.get("code"))
        price = item.get("price")
        if outcome_type is None or price is None:
            continue
        line = _item_line(item) if has_line else None
        parsed.append((outcome_type, float(price), line))
    if not parsed:
        return

    market_line = next((ln for ot, _, ln in parsed if ot == OutcomeType.OVER), None)
    if market_line is None:
        market_line = next((ln for ot, _, ln in parsed if ot == OutcomeType.HOME_WIN), None)
    market_id = _make_id(BOOKMAKER, event_id, market_type.value, period, str(market_line or ""))
    if any(m.market_id == market_id for m in bundle.markets):
        return
    bundle.markets.append(CanonicalMarket(
        market_id=market_id, event_id=event_id, market_type=market_type,
        period=period, line=market_line,
    ))
    for outcome_type, price, line in parsed:
        team_ref = home if outcome_type == OutcomeType.HOME_WIN else (
            away if outcome_type == OutcomeType.AWAY_WIN else None)
        outcome_id = _make_id(market_id, outcome_type.value, str(line or ""))
        bundle.outcomes.append(CanonicalOutcome(
            outcome_id=outcome_id, market_id=market_id, outcome_type=outcome_type,
            team_reference=team_ref, line=line,
        ))
        bundle.quotes.append(OddsQuote(
            bookmaker=BOOKMAKER, event_id=event_id, market_id=market_id,
            outcome_id=outcome_id, decimal_odds=price, timestamp_received=received_at,
            source_timestamp=source_ts, status=OddsStatus.ACTIVE,
        ))


def map_overview(overview: list, received_at: datetime) -> list[MappedEvent]:
    """Featured markets for every live event across all three sports (one call)."""
    if not overview:
        return []
    out: list[MappedEvent] = []
    for view in overview[0].get("betViews", []):
        sport = _SPORT.get(view.get("competitionContextCaption"))
        if sport is None:
            continue
        for comp in view.get("competitions", []):
            competition = comp.get("caption", "Unknown")
            country = comp.get("regionCaption") or None
            for ev in comp.get("events", []):
                live = ev.get("liveData") or {}
                caps = ev.get("additionalCaptions") or {}
                if _is_esports(caps.get("competitor1"), caps.get("competitor2")):
                    continue
                home = normalize_team(caps.get("competitor1", "?"))
                away = normalize_team(caps.get("competitor2", "?"))
                event_id, _, bundle = _build_event(
                    str(ev.get("betContextId")), sport, home, away, competition,
                    live.get("sportradarMatchId"), _parse_dt(live.get("referenceTime")),
                    received_at, country=country,
                )
                for m in ev.get("markets", []):
                    _add_market(bundle, event_id, home, away, m.get("betTypeSysname"),
                                m.get("betItems", []), received_at,
                                _parse_dt(live.get("referenceTime")))
                if bundle.markets:
                    out.append(bundle)
    return out


def map_event_detail(detail: dict, sport: str, received_at: datetime) -> Optional[MappedEvent]:
    """Full market set for one event (per-event response)."""
    live = detail.get("liveData") or {}
    caps = detail.get("additionalCaptions") or {}
    competitors = detail.get("competitors") or []
    home = normalize_team(caps.get("competitor1") or
                          (competitors[0].get("caption") if competitors else "?"))
    away = normalize_team(caps.get("competitor2") or
                          (competitors[1].get("caption") if len(competitors) > 1 else "?"))
    sportradar = detail.get("sportradarMatchId") or live.get("sportradarMatchId")
    source_ts = _parse_dt(live.get("referenceTime"))
    event_id, _, bundle = _build_event(
        str(detail.get("betContextId")), sport, home, away,
        detail.get("competitionCaption", "Unknown"), sportradar, source_ts, received_at,
    )
    for cat in detail.get("marketCategories", []):
        for item in cat.get("items", []):
            for bv in item.get("betViews", []):
                _add_market(bundle, event_id, home, away, bv.get("marketSysname"),
                            bv.get("betItems", []), received_at, source_ts)
    return bundle if bundle.markets else None


def live_event_ids(overview: list) -> list[tuple[str, int]]:
    """(sport, betContextId) for every live target-sport event in the overview."""
    if not overview:
        return []
    out: list[tuple[str, int]] = []
    for view in overview[0].get("betViews", []):
        sport = _SPORT.get(view.get("competitionContextCaption"))
        if sport is None:
            continue
        for comp in view.get("competitions", []):
            for ev in comp.get("events", []):
                caps = ev.get("additionalCaptions") or {}
                if _is_esports(caps.get("competitor1"), caps.get("competitor2")):
                    continue
                eid = ev.get("betContextId")
                if eid is not None:
                    out.append((sport, eid))
    return out
