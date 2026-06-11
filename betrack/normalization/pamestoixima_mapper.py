from __future__ import annotations

import hashlib
import logging
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

BOOKMAKER = "Pamestoixima"

# Top-level drilldown tag IDs per sport (verified via getLiveDrilldownSports).
# eSports (id 24) is omitted intentionally — only real ball-sports in scope.
_SPORT_ID_TO_SLUG: dict[str, str] = {
    "11": "football",
    "5": "basketball",
    "12": "tennis",
}

# Markets keyed by (groupCode, sport_slug) -> (MarketType, period, has_line).
# Pamestoixima's groupCode taxonomy is per-sport — the same conceptual market
# uses different codes across football/basketball/tennis (e.g. match winner
# = MATCH_RESULT / MONEY_LINE / MATCH_WINNER), and basketball's MATCH_RESULT
# is the 3-way regulation-only market (with a Draw leg) which DOESN'T match
# the canonical 2-way BASKETBALL_MATCH_WINNER — we use MONEY_LINE for that.
# `has_line` tells us whether to read `handicapValue` off the market and
# stamp it onto the market+outcome canonical IDs.
_MARKETS: dict[tuple[str, str], tuple[MarketType, str, bool]] = {
    # Football
    ("MATCH_RESULT",            "football"):   (MarketType.FOOTBALL_FULL_TIME_1X2,        "full_time", False),
    ("TOTAL_GOALS_OVER/UNDER",  "football"):   (MarketType.FOOTBALL_FULL_TIME_OVER_UNDER, "full_time", True),
    ("BOTH_TEAMS_TO_SCORE",     "football"):   (MarketType.FOOTBALL_FULL_TIME_BTTS,       "full_time", False),
    ("NO_BET_DRAW",             "football"):   (MarketType.FOOTBALL_DRAW_NO_BET,          "full_time", False),
    # Basketball
    ("MONEY_LINE",              "basketball"): (MarketType.BASKETBALL_MATCH_WINNER,       "match",     False),
    ("TOTAL_POINTS_OVER/UNDER", "basketball"): (MarketType.BASKETBALL_TOTAL_POINTS,       "match",     True),
    ("HANDICAP_2_WAY",          "basketball"): (MarketType.BASKETBALL_HANDICAP,           "match",     True),
    # Tennis
    ("MATCH_WINNER",            "tennis"):     (MarketType.TENNIS_MATCH_WINNER,           "match",     False),
    ("TOTAL_GAMES_OVER/UNDER",  "tennis"):     (MarketType.TENNIS_TOTAL_GAMES,            "match",     True),
}

# Outcome subType code → OutcomeType, scoped to the canonical MarketType so
# the same letter ("H") can map differently per market (HOME_WIN for 1X2 /
# match-winner / DNB, OVER for totals). BTTS has null subType in the feed —
# resolved via displayOrder fallback below.
_OUTCOMES_BY_MARKET: dict[MarketType, dict[str, OutcomeType]] = {
    MarketType.FOOTBALL_FULL_TIME_1X2: {
        "H": OutcomeType.HOME_WIN, "D": OutcomeType.DRAW, "A": OutcomeType.AWAY_WIN,
    },
    MarketType.FOOTBALL_FULL_TIME_OVER_UNDER: {
        "H": OutcomeType.OVER, "L": OutcomeType.UNDER,
    },
    MarketType.FOOTBALL_DRAW_NO_BET: {
        "H": OutcomeType.HOME_WIN, "A": OutcomeType.AWAY_WIN,
    },
    MarketType.BASKETBALL_MATCH_WINNER: {
        "H": OutcomeType.HOME_WIN, "A": OutcomeType.AWAY_WIN,
    },
    MarketType.BASKETBALL_TOTAL_POINTS: {
        "H": OutcomeType.OVER, "L": OutcomeType.UNDER,
    },
    MarketType.BASKETBALL_HANDICAP: {
        "H": OutcomeType.HOME_WIN, "A": OutcomeType.AWAY_WIN,
    },
    MarketType.TENNIS_MATCH_WINNER: {
        "H": OutcomeType.HOME_WIN, "A": OutcomeType.AWAY_WIN,
    },
    MarketType.TENNIS_TOTAL_GAMES: {
        "H": OutcomeType.OVER, "L": OutcomeType.UNDER,
    },
}

# Track unknown (groupCode, sport) tuples so we log each one only once per
# process. Spammy on first run with a long-lived sport pair (e.g. all the
# 30+ exotic football groupCodes from the detail feed), but quiet after.
_warned: set[tuple[str, str]] = set()


def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def _warn_unknown(group_code: str, sport: str, market_name: str) -> None:
    key = (group_code, sport)
    if key not in _warned:
        _warned.add(key)
        logger.info("unknown pamestoixima market groupCode=%r sport=%s name=%r, skipping",
                    group_code, sport, market_name)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _extract_sportradar_id(ev: dict) -> Optional[int]:
    """Pamestoixima carries a Sportradar match id under externalIds[*] with
    provider 'betradar' and an id string like 'sr:match:66886826'. That
    integer is the same one Stoiximan exposes as betradarMatchId and
    Novibet as liveData.sportradarMatchId — the cross-book key."""
    for ext in ev.get("externalIds") or []:
        if (ext.get("provider") == "betradar"):
            raw = (ext.get("id") or "").strip()
            # Format: "sr:match:NNNNNNN" — take the trailing integer.
            for prefix in ("sr:match:", "sr:competition:", "sr:season:"):
                if raw.startswith(prefix):
                    raw = raw[len(prefix):]
                    break
            try:
                return int(raw)
            except ValueError:
                return None
    return None


def _participants(ev: dict) -> tuple[str, str]:
    teams = ev.get("teams") or []
    home = next((t for t in teams if t.get("side") == "HOME"), None)
    away = next((t for t in teams if t.get("side") == "AWAY"), None)
    if home is None and len(teams) >= 1:
        home = teams[0]
    if away is None and len(teams) >= 2:
        away = teams[1]
    return (
        normalize_team((home or {}).get("name") or "?"),
        normalize_team((away or {}).get("name") or "?"),
    )


def _resolve_outcome(market_type: MarketType, outcome: dict) -> Optional[OutcomeType]:
    by_sub = _OUTCOMES_BY_MARKET.get(market_type)
    if by_sub is not None:
        return by_sub.get(outcome.get("subType"))
    # BTTS feed has null subType on outcomes; the Greek names "Ναι"/"Όχι"
    # vary by locale and aren't safe to key on, so we use the stable
    # displayOrder: 1 = first listed (Yes), 2 = second (No).
    if market_type == MarketType.FOOTBALL_FULL_TIME_BTTS:
        order = outcome.get("displayOrder")
        if order == 1:
            return OutcomeType.BTTS_YES
        if order == 2:
            return OutcomeType.BTTS_NO
    return None


def _outcome_price(outcome: dict) -> Optional[float]:
    prices = outcome.get("prices") or []
    for p in prices:
        d = p.get("decimal")
        if d is not None:
            try:
                return float(d)
            except (TypeError, ValueError):
                continue
    return None


def _map_event(ev: dict, received_at: datetime,
               source_ts: Optional[datetime] = None) -> Optional[MappedEvent]:
    sport_slug = _SPORT_ID_TO_SLUG.get(str(ev.get("sportDrilldownTagId")))
    if sport_slug is None or not ev.get("liveNow"):
        return None

    home, away = _participants(ev)
    native_id = str(ev["id"])
    sportradar = _extract_sportradar_id(ev)
    event_id = (
        _make_id(str(sportradar), sport_slug) if sportradar
        else _make_id(BOOKMAKER, native_id, sport_slug)
    )
    if sportradar is None:
        logger.debug("pamestoixima event %s (%s vs %s) has no betradar externalId",
                     native_id, home, away)

    competition = ((ev.get("type") or {}).get("name") or "Unknown")
    country = (ev.get("class") or {}).get("name")
    start_time = _parse_iso(ev.get("startTime")) or received_at
    event = CanonicalEvent(
        event_id=event_id, sport=sport_slug, competition=competition, country=country,
        home_team=home, away_team=away, start_time=start_time, status=EventStatus.LIVE,
    )
    bundle = MappedEvent(
        event=event, bookmaker=BOOKMAKER, native_event_id=native_id,
        sportradar_match_id=sportradar,
    )

    for m in ev.get("markets") or []:
        group_code = m.get("groupCode") or ""
        spec = _MARKETS.get((group_code, sport_slug))
        if spec is None:
            _warn_unknown(group_code, sport_slug, m.get("name", ""))
            continue
        market_type, period, has_line = spec
        if not m.get("active", True) or not m.get("displayed", True):
            continue

        line: Optional[float] = None
        if has_line and m.get("handicapValue") is not None:
            try:
                line = float(m["handicapValue"])
            except (TypeError, ValueError):
                line = None
        if has_line and line is None:
            continue  # line-bearing market with no usable line — skip

        market_id = _make_id(BOOKMAKER, event_id, market_type.value, period, str(line or ""))
        bundle.markets.append(CanonicalMarket(
            market_id=market_id, event_id=event_id, market_type=market_type,
            period=period, line=line,
        ))

        for o in m.get("outcomes") or []:
            if not o.get("active", True) or not o.get("displayed", True):
                continue
            outcome_type = _resolve_outcome(market_type, o)
            if outcome_type is None:
                continue
            price = _outcome_price(o)
            if price is None or price <= 1.0:
                continue
            team_ref = home if outcome_type == OutcomeType.HOME_WIN else (
                away if outcome_type == OutcomeType.AWAY_WIN else None)
            o_line = line if has_line else None
            outcome_id = _make_id(market_id, outcome_type.value, str(o_line or ""))
            bundle.outcomes.append(CanonicalOutcome(
                outcome_id=outcome_id, market_id=market_id, outcome_type=outcome_type,
                team_reference=team_ref, line=o_line,
            ))
            bundle.quotes.append(OddsQuote(
                bookmaker=BOOKMAKER, event_id=event_id, market_id=market_id,
                outcome_id=outcome_id, decimal_odds=price, timestamp_received=received_at,
                source_timestamp=source_ts, status=OddsStatus.ACTIVE,
            ))
    return bundle


def map_overview(overview: dict, received_at: datetime) -> list[MappedEvent]:
    """Headline markets for every live football/basketball/tennis event from
    the merged per-sport overview payload."""
    out: list[MappedEvent] = []
    for ev in overview.get("events") or []:
        bundle = _map_event(ev, received_at)
        if bundle and bundle.markets:
            out.append(bundle)
    return out


def map_event_detail(detail: dict, received_at: datetime) -> Optional[MappedEvent]:
    """Full market set for one event (per-event detail response). The detail
    envelope wraps a list under `data.events`; we expect exactly one element."""
    events = ((detail.get("data") or {}).get("events")) or []
    if not events:
        return None
    return _map_event(events[0], received_at)


def live_event_ids(overview: dict) -> list[tuple[str, str]]:
    """(sport_slug, native_event_id_str) for every live target-sport event in
    the merged overview. Pamestoixima event ids are stringly-typed numerics."""
    out: list[tuple[str, str]] = []
    for ev in overview.get("events") or []:
        sport_slug = _SPORT_ID_TO_SLUG.get(str(ev.get("sportDrilldownTagId")))
        if sport_slug and ev.get("liveNow"):
            out.append((sport_slug, str(ev["id"])))
    return out
