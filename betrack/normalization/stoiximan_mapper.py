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

BOOKMAKER = "Stoiximan"

_SPORT = {"FOOT": "football", "BASK": "basketball", "TENN": "tennis"}

# Stoiximan files FIFA/NBA2K simulated matches under FOOT/BASK; they sit in
# dedicated zones. Skip them — only real football/basketball/tennis is in scope.
_ESPORTS_ZONES = {"esoccer", "ebasketball", "etennis"}


def _is_esports(zone_name: Optional[str]) -> bool:
    return bool(zone_name) and zone_name.strip().lower() in _ESPORTS_ZONES

# Stoiximan market typeId -> (MarketType, period, {selection typeId: OutcomeType}).
# Football typeIds 1/13/15 are documented; basketball/tennis verified from live
# per-event responses. Selection typeIds are stable per market type. The bool is
# `has_line` — only over/under and handicap carry a line; for 1X2/winner/BTTS the
# selection names are codes ("1"/"X"/"2") or team names and must NOT be parsed for
# a line. Unknown typeIds are skipped + logged once (see _warn_unknown).
_MARKETS: dict[int, tuple[MarketType, str, dict[int, OutcomeType], bool]] = {
    1:    (MarketType.FOOTBALL_FULL_TIME_1X2, "full_time",
           {1: OutcomeType.HOME_WIN, 2: OutcomeType.DRAW, 3: OutcomeType.AWAY_WIN}, False),
    13:   (MarketType.FOOTBALL_FULL_TIME_OVER_UNDER, "full_time",
           {39: OutcomeType.OVER, 40: OutcomeType.UNDER}, True),
    15:   (MarketType.FOOTBALL_FULL_TIME_BTTS, "full_time",
           {43: OutcomeType.BTTS_YES, 44: OutcomeType.BTTS_NO}, False),
    155:  (MarketType.BASKETBALL_MATCH_WINNER, "match",
           {447: OutcomeType.HOME_WIN, 448: OutcomeType.AWAY_WIN}, False),
    156:  (MarketType.BASKETBALL_HANDICAP, "match",
           {449: OutcomeType.HOME_WIN, 450: OutcomeType.AWAY_WIN}, True),
    157:  (MarketType.BASKETBALL_TOTAL_POINTS, "match",
           {451: OutcomeType.OVER, 452: OutcomeType.UNDER}, True),
    160:  (MarketType.TENNIS_MATCH_WINNER, "match",
           {455: OutcomeType.HOME_WIN, 456: OutcomeType.AWAY_WIN}, False),
    1541: (MarketType.TENNIS_TOTAL_GAMES, "match",
           {4039: OutcomeType.OVER, 4040: OutcomeType.UNDER}, True),
    1514: (MarketType.TENNIS_SET_WINNER, "set_1",
           {4014: OutcomeType.HOME_WIN, 4015: OutcomeType.AWAY_WIN}, False),
}

_warned: set[int] = set()
_TRAILING_NUMBER = re.compile(r"([-+]?\d+(?:\.\d+)?)\s*$")


def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def _warn_unknown(type_id: int, name: str) -> None:
    if type_id not in _warned:
        _warned.add(type_id)
        logger.info("unknown stoiximan market typeId=%s name=%r, skipping", type_id, name)


def _parse_line(name: str) -> Optional[float]:
    m = _TRAILING_NUMBER.search(name or "")
    return float(m.group(1)) if m else None


def _participants(ev: dict) -> tuple[str, str]:
    parts = ev.get("participants", [])
    home = next((p for p in parts if p.get("isHome")), parts[0] if parts else {})
    away = next((p for p in parts if p is not home), parts[1] if len(parts) > 1 else {})
    return normalize_team(home.get("name", "?")), normalize_team(away.get("name", "?"))


def _map_event(
    ev: dict,
    market_ids: list,
    markets: dict,
    selections: dict,
    competition: str,
    received_at: datetime,
    source_ts: Optional[datetime],
    zone_name: Optional[str] = None,
) -> Optional[MappedEvent]:
    sport = _SPORT.get(str(ev.get("sportId")))
    if sport is None or ev.get("isOutrightEvent") or _is_esports(zone_name):
        return None

    home, away = _participants(ev)
    native_id = str(ev["id"])
    sportradar = ev.get("betradarMatchId")
    event_id = (
        _make_id(str(sportradar), sport) if sportradar
        else _make_id(BOOKMAKER, native_id, sport)
    )
    if not sportradar:
        # Common for minor-league / women's tour / qualifier / lower-tier tennis;
        # debug-level only so it doesn't spam the per-cycle log.
        logger.debug("stoiximan event %s (%s vs %s) has no betradarMatchId", native_id, home, away)

    start_ms = ev.get("startTime")
    start_time = (
        datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc) if start_ms else received_at
    )
    event = CanonicalEvent(
        event_id=event_id, sport=sport, competition=competition, country=zone_name,
        home_team=home, away_team=away, start_time=start_time, status=EventStatus.LIVE,
    )
    bundle = MappedEvent(
        event=event, bookmaker=BOOKMAKER, native_event_id=native_id,
        sportradar_match_id=sportradar,
    )

    for mid in market_ids:
        market = markets.get(str(mid)) or markets.get(mid)
        if not market:
            continue
        type_id = market.get("typeId")
        spec = _MARKETS.get(type_id)
        if spec is None:
            _warn_unknown(type_id, market.get("name", ""))
            continue
        market_type, period, sel_map, has_line = spec

        parsed: list[tuple[OutcomeType, float, Optional[float], str]] = []
        for sid in market.get("selectionIdList", []):
            sel = selections.get(str(sid)) or selections.get(sid)
            if not sel:
                continue
            outcome_type = sel_map.get(sel.get("typeId"))
            price = sel.get("price")
            if outcome_type is None or price is None:
                continue
            line = _parse_line(sel.get("name", "")) if has_line else None
            parsed.append((outcome_type, float(price), line, sel.get("name", "")))
        if not parsed:
            continue

        market_line = next(
            (ln for ot, _, ln, _ in parsed if ot == OutcomeType.OVER), None
        )
        if market_line is None:
            market_line = next(
                (ln for ot, _, ln, _ in parsed if ot == OutcomeType.HOME_WIN), None
            )
        market_id = _make_id(BOOKMAKER, event_id, market_type.value, period, str(market_line or ""))
        bundle.markets.append(CanonicalMarket(
            market_id=market_id, event_id=event_id, market_type=market_type,
            period=period, line=market_line,
        ))
        for outcome_type, price, sel_line, _name in parsed:
            team_ref = home if outcome_type == OutcomeType.HOME_WIN else (
                away if outcome_type == OutcomeType.AWAY_WIN else None)
            outcome_id = _make_id(market_id, outcome_type.value, str(sel_line or ""))
            bundle.outcomes.append(CanonicalOutcome(
                outcome_id=outcome_id, market_id=market_id, outcome_type=outcome_type,
                team_reference=team_ref, line=sel_line,
            ))
            bundle.quotes.append(OddsQuote(
                bookmaker=BOOKMAKER, event_id=event_id, market_id=market_id,
                outcome_id=outcome_id, decimal_odds=price, timestamp_received=received_at,
                source_timestamp=source_ts, status=OddsStatus.ACTIVE,
            ))
    return bundle


def map_overview(overview: dict, received_at: datetime) -> list[MappedEvent]:
    """Headline markets for every live football/basketball/tennis event."""
    events = overview.get("events", {})
    markets = overview.get("markets", {})
    selections = overview.get("selections", {})
    leagues = overview.get("leagues", {})
    zones = overview.get("zones", {})
    out: list[MappedEvent] = []
    for ev in events.values():
        if str(ev.get("sportId")) not in _SPORT or not ev.get("isLive"):
            continue
        league = leagues.get(str(ev.get("leagueId")), {})
        zone_name = zones.get(str(ev.get("zoneId")), {}).get("name")
        bundle = _map_event(
            ev, ev.get("marketIdList", []), markets, selections,
            league.get("name", "Unknown"), received_at, None, zone_name,
        )
        if bundle and bundle.markets:
            out.append(bundle)
    return out


def map_event_detail(detail: dict, received_at: datetime) -> Optional[MappedEvent]:
    """Full market set for one event (per-event response)."""
    ev = detail.get("event")
    if not ev:
        return None
    source_ts = None
    synced = detail.get("syncedAtUtc")
    if isinstance(synced, (int, float)):  # epoch milliseconds
        source_ts = datetime.fromtimestamp(synced / 1000, tz=timezone.utc)
    elif isinstance(synced, str):
        try:
            source_ts = datetime.fromisoformat(synced.replace("Z", "+00:00"))
        except ValueError:
            source_ts = None
    competition = detail.get("league", {}).get("name", "Unknown")
    zone_name = detail.get("zone", {}).get("name")
    return _map_event(
        ev, list(detail.get("markets", {}).keys()), detail.get("markets", {}),
        detail.get("selections", {}), competition, received_at, source_ts, zone_name,
    )


def live_event_ids(overview: dict) -> list[tuple[str, int]]:
    """(sport, native_event_id) for every live target-sport event in the overview."""
    zones = overview.get("zones", {})
    out: list[tuple[str, int]] = []
    for ev in overview.get("events", {}).values():
        sport = _SPORT.get(str(ev.get("sportId")))
        zone_name = zones.get(str(ev.get("zoneId")), {}).get("name")
        if sport and ev.get("isLive") and not ev.get("isOutrightEvent") and not _is_esports(zone_name):
            out.append((sport, ev["id"]))
    return out
