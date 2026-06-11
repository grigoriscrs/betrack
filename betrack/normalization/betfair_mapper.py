"""Map Betfair Exchange JSON (scan-inbf + ero/bymarket + ero/byevent) into
MappedEvent bundles. Convention mirrors stoiximan_mapper / novibet_mapper;
unknown market types are logged once and skipped."""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime, timedelta
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

BOOKMAKER = "Betfair"
# Asymmetric window: drop ante-post (openDate too far in the FUTURE) but keep
# long-running past events (tennis grand slams can run 5h, basketball OT 3h+,
# football extra-time / penalties 2.5h). Betfair's per-market `inplay` flag
# flickers between points/timeouts, so we don't filter on it — `state.status`
# is the authoritative tradability signal.
FUTURE_CUTOFF = timedelta(hours=3)
PAST_CUTOFF = timedelta(hours=12)
_OVER_UNDER_LINE = re.compile(r"OVER_UNDER_(\d+)$")
_SET_NUMBER = re.compile(r"SET[_\s]*(\d+)", re.IGNORECASE)
_warned_market_types: set[tuple[str, str]] = set()

SPORT_BY_EVENT_TYPE_ID: dict[int, str] = {1: "football", 7522: "basketball", 2: "tennis"}

_MARKET_TYPE_FOOTBALL: dict[str, MarketType] = {
    "MATCH_ODDS":          MarketType.FOOTBALL_FULL_TIME_1X2,
    "BOTH_TEAMS_TO_SCORE": MarketType.FOOTBALL_FULL_TIME_BTTS,
    "DOUBLE_CHANCE":       MarketType.FOOTBALL_DOUBLE_CHANCE,
    "DRAW_NO_BET":         MarketType.FOOTBALL_DRAW_NO_BET,
}
_MARKET_TYPE_BASKETBALL: dict[str, MarketType] = {
    "MATCH_ODDS":      MarketType.BASKETBALL_MATCH_WINNER,
    "HANDICAP":        MarketType.BASKETBALL_HANDICAP,
    "COMBINED_TOTAL":  MarketType.BASKETBALL_TOTAL_POINTS,
}
_MARKET_TYPE_TENNIS: dict[str, MarketType] = {
    "MATCH_ODDS":      MarketType.TENNIS_MATCH_WINNER,
    "HANDICAP":        MarketType.TENNIS_HANDICAP,
    "COMBINED_TOTAL":  MarketType.TENNIS_TOTAL_GAMES,
}

# Betfair market types whose marketNode contains MULTIPLE handicap lines
# packed together (e.g. Over/Under 18.0, 18.5, 19.0 in one node). We split
# them into one canonical market per handicap value.
_MULTILINE_MARKET_TYPES: set[str] = {"COMBINED_TOTAL", "HANDICAP"}

# OutcomeType has no HT/FT enum members; extending it is out of scope this build.
_DEFERRED_MARKET_TYPES: set[str] = {
    "HALF_TIME_FULL_TIME",
}


def _make_id(*parts: str) -> str:
    return hashlib.md5(":".join(parts).encode()).hexdigest()[:16]


def _event_id(native_event_id: int | str, sport: str) -> str:
    return _make_id(BOOKMAKER, str(native_event_id), sport)


def _market_id(event_id: str, market_type: MarketType, period: str,
               line: Optional[float]) -> str:
    return _make_id(BOOKMAKER, event_id, market_type.value, period,
                    "" if line is None else f"{line:g}")


def _outcome_id(market_id: str, outcome_type: OutcomeType,
                line: Optional[float]) -> str:
    parts = [market_id, outcome_type.value]
    if line is not None:
        parts.append(f"{line:g}")
    return _make_id(*parts)


def _team_match(t1: str, t2: str) -> bool:
    """Two team names refer to the same team if any of:
    1. Identical or one is a substring of the other ('Arsenal' ↔ 'Arsenal FC',
       'Tiafoe' ↔ 'Frances Tiafoe', 'Cedevita Olimpija' ↔ 'KK Cedevita Olimpija
       Ljubljana').
    2. They share ≥2 substantive tokens of >3 chars ('Felix Auger Aliassime'
       ↔ 'F Auger-Aliassime', 'PFC Lokomotiv Plovdiv' ↔ 'Lokomotiv Plovdiv
       1926').
    3. They share ≥1 substantive token AND a short token (≤3 chars) in one side
       is a prefix of a substantive token in the other ('Ja Faria' ↔ 'Jaime
       Faria' — 'Ja' is prefix of 'Jaime', 'Faria' is shared).
    'Real Madrid' vs 'Real Sociedad' fails all three: only 'real' shared, no
    short prefix relation between 'Madrid' and 'Sociedad'."""
    if not t1 or not t2:
        return False
    if t1 == t2 or t1 in t2 or t2 in t1:
        return True
    toks = lambda s: s.replace("-", " ").replace("/", " ").split()
    s1, s2 = set(toks(t1)), set(toks(t2))
    # Token-subset rule (one is a "short form" of the other after tokenizing):
    # 'Athletico-PR' tokens {athletico, pr} ⊆ 'Athletico Paranaense PR' tokens
    # {athletico, paranaense, pr}. Doesn't match Libertad cases because the
    # FC/Loja suffix breaks subset: {libertad, fc} ⊄ {libertad, loja}.
    if s1 and s2 and (s1 <= s2 or s2 <= s1):
        return True
    long1, long2 = {x for x in s1 if len(x) > 3}, {x for x in s2 if len(x) > 3}
    shared_long = long1 & long2
    if len(shared_long) >= 2:
        return True
    if not shared_long:
        return False
    short1, short2 = {x for x in s1 if 0 < len(x) <= 3}, {x for x in s2 if 0 < len(x) <= 3}
    # Any short token in one side that prefixes a substantive token in the other?
    if any(any(lg.startswith(sh) for lg in long2) for sh in short1):
        return True
    if any(any(lg.startswith(sh) for lg in long1) for sh in short2):
        return True
    return False


def _xmatch_lookup(xmatch_index: Optional[dict[tuple[str, str, str], str]],
                   sport: str, home: str, away: str) -> Optional[str]:
    """Find an existing event_id whose (home, away) match these — exact first,
    fuzzy fall-back. Returns None if no candidate."""
    if not xmatch_index or not home or not away:
        return None
    h, a = home.lower(), away.lower()
    eid = xmatch_index.get((sport, h, a)) or xmatch_index.get((sport, a, h))
    if eid:
        return eid
    for (s, idx_h, idx_a), candidate in xmatch_index.items():
        if s != sport:
            continue
        if (_team_match(h, idx_h) and _team_match(a, idx_a)) or \
           (_team_match(h, idx_a) and _team_match(a, idx_h)):
            return candidate
    return None


def _parse_iso_utc(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _warn_once(market_type: Optional[str], sport: str) -> None:
    key = (sport, market_type or "")
    if key in _warned_market_types:
        return
    _warned_market_types.add(key)
    logger.info("unknown betfair marketType=%s (sport=%s), skipping", market_type, sport)


def live_event_ids(scan_response: dict,
                   received_at: Optional[datetime] = None) -> list[tuple[str, str]]:
    """Walk a list_in_play response and return [(sport_slug, native_event_id), ...].
    Deduplicates eventIds. When `received_at` is supplied, also drop eventIds
    whose openDate (from `scan.attachments.events`) is outside ±LIVE_WINDOW —
    Betfair's `inPlay: true` filter surfaces outright tournament events
    (e.g. 'NBA Playoff Series Markets') whose IDs cause `byevent` to return
    HTTP 400 when mixed with real match IDs."""
    attachments = (scan_response.get("attachments") or {}).get("events") or {}
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for r in scan_response.get("results", []):
        sport = SPORT_BY_EVENT_TYPE_ID.get(r.get("eventTypeId"))
        if not sport:
            continue
        eid = str(r.get("eventId") or "")
        if not eid:
            continue
        key = (sport, eid)
        if key in seen:
            continue
        seen.add(key)
        if received_at is not None:
            # attachments keys may be str OR int depending on the Betfair response
            ev = attachments.get(eid)
            if ev is None and eid.isdigit():
                ev = attachments.get(int(eid))
            parsed = _parse_iso_utc((ev or {}).get("openDate"))
            # Drop ante-post only — keep past matches even if they've been running
            # for hours. Missing openDate is treated as "trust scan-inbf inPlay flag".
            if parsed is not None and parsed - received_at > FUTURE_CUTOFF:
                continue
            if parsed is not None and received_at - parsed > PAST_CUTOFF:
                continue
        out.append(key)
    return out


def map_overview(scan_response: dict, received_at: datetime) -> list[MappedEvent]:
    """No-op: scan-inbf returns event/market IDs but no prices. Real bundles
    come from map_event_detail on bymarket payloads. Kept as a public function
    so the pipeline's call shape mirrors Stoiximan/Novibet mappers."""
    return []


def map_event_detail(response: dict, received_at: datetime,
                     xmatch_index: Optional[dict[tuple[str, str, str], str]] = None,
                     ) -> list[MappedEvent]:
    """Map a bymarket response into MappedEvent bundles, one per eventNode.
    Unlike Stoix/Novi (one event per detail call), Betfair's bymarket can
    return many events when batched.

    `xmatch_index` maps (sport, normalized_home_lower, normalized_away_lower)
    → existing event_id. When a Betfair event matches a Stoix/Novi event
    already in the store, we reuse that event_id so the three books collapse
    into one row in the dashboard. Betfair has no Sportradar ID, so this is
    the only cross-match mechanism available."""
    bundles: list[MappedEvent] = []
    for event_type in response.get("eventTypes", []) or []:
        sport = SPORT_BY_EVENT_TYPE_ID.get(event_type.get("eventTypeId"))
        if not sport:
            continue

        for event_node in event_type.get("eventNodes", []) or []:
            evt = event_node.get("event") or {}
            native_event_id = event_node.get("eventId")
            if native_event_id is None:
                continue

            open_date = _parse_iso_utc(evt.get("openDate")) or received_at
            # Same asymmetric filter as live_event_ids: drop only ante-post.
            if open_date - received_at > FUTURE_CUTOFF:
                continue
            if received_at - open_date > PAST_CUTOFF:
                continue

            event_name = evt.get("eventName") or ""
            # Betfair separators by sport: football/tennis use " v ", basketball
            # (NBA/WNBA) uses "AWAY @ HOME". For "@" the order flips — first
            # team is away, second is home.
            home_raw, sep, away_raw = event_name.partition(" v ")
            if not sep:
                home_raw, sep, away_raw = event_name.partition(" vs ")
            if not sep:
                away_raw, sep, home_raw = event_name.partition(" @ ")
            home = normalize_team(home_raw.strip())
            away = normalize_team(away_raw.strip())
            if not home or not away:
                continue

            # Cross-match with existing Stoix/Novi event (exact or fuzzy by
            # team-name substring/token-set). Falls back to a Betfair-namespace
            # event_id when no match — that row appears solo in the UI.
            event_id = _xmatch_lookup(xmatch_index, sport, home, away)
            if not event_id:
                event_id = _event_id(native_event_id, sport)
            # Betfair's bymarket response carries no competition name (only an
            # ID) — store the 2-letter country code as country, leave
            # competition empty. When this event cross-matches an existing
            # Stoix/Novi row, the store's COALESCE keeps their richer
            # competition string. Solo Betfair rows show just the country.
            canonical_event = CanonicalEvent(
                event_id=event_id,
                sport=sport,
                competition="",
                country=(evt.get("countryCode") or None),
                home_team=home,
                away_team=away,
                start_time=open_date,
                status=EventStatus.LIVE,
            )

            markets: list[CanonicalMarket] = []
            outcomes: list[CanonicalOutcome] = []
            quotes: list[OddsQuote] = []

            for mn in event_node.get("marketNodes", []) or []:
                state = mn.get("state") or {}
                if not state:
                    continue
                # status is per-market tradability (OPEN/SUSPENDED/CLOSED/SETTLED);
                # inplay flickers and is event-wide. SUSPENDED is included so we
                # don't blink out during routine in-play pauses (Betfair suspends
                # markets after goals/cards/timeouts for ~10s); the quote.status
                # is propagated downstream so freshness badges still show stale.
                # CLOSED + SETTLED have no usable prices.
                if state.get("status") not in ("OPEN", "SUSPENDED"):
                    continue
                for m, m_outcomes, m_quotes in _map_market(mn, sport, event_id, home, away, received_at):
                    markets.append(m)
                    outcomes.extend(m_outcomes)
                    quotes.extend(m_quotes)

            if not markets:
                continue

            bundles.append(MappedEvent(
                event=canonical_event,
                bookmaker=BOOKMAKER,
                native_event_id=str(native_event_id),
                sportradar_match_id=None,
                markets=markets,
                outcomes=outcomes,
                quotes=quotes,
            ))
    return bundles


def _resolve_market_type(market_type_str: str, sport: str
                         ) -> Optional[tuple[MarketType, str, Optional[float]]]:
    """Returns (market_type, period, line) or None. Period defaults to 'full_time'
    for football and 'match' elsewhere; tennis SET_*_WINNER becomes 'set_N'.
    line is the canonical market line where Betfair encodes it in the type name
    (OVER_UNDER_25 → 2.5); per-runner handicap lines are pulled later."""
    if sport == "football":
        if market_type_str in _MARKET_TYPE_FOOTBALL:
            return _MARKET_TYPE_FOOTBALL[market_type_str], "full_time", None
        m = _OVER_UNDER_LINE.match(market_type_str)
        if m:
            return MarketType.FOOTBALL_FULL_TIME_OVER_UNDER, "full_time", int(m.group(1)) / 10.0
    elif sport == "basketball":
        if market_type_str in _MARKET_TYPE_BASKETBALL:
            return _MARKET_TYPE_BASKETBALL[market_type_str], "match", None
        m = _OVER_UNDER_LINE.match(market_type_str)
        if m:
            return MarketType.BASKETBALL_TOTAL_POINTS, "match", int(m.group(1)) / 10.0
    elif sport == "tennis":
        if market_type_str in _MARKET_TYPE_TENNIS:
            return _MARKET_TYPE_TENNIS[market_type_str], "match", None
        m = _OVER_UNDER_LINE.match(market_type_str)
        if m:
            return MarketType.TENNIS_TOTAL_GAMES, "match", int(m.group(1)) / 10.0
        s = _SET_NUMBER.search(market_type_str)
        if s and "WINNER" in market_type_str.upper():
            return MarketType.TENNIS_SET_WINNER, f"set_{s.group(1)}", None
    return None


def _resolve_outcome(market_type: MarketType, runner: dict, home: str, away: str
                     ) -> tuple[Optional[OutcomeType], Optional[float]]:
    """Map a Betfair runner to (OutcomeType, outcome_line). The outcome_line is
    None except for handicap markets, where Betfair attaches the line per-runner."""
    raw_name = ((runner.get("description") or {}).get("runnerName") or "").strip()
    # Pass each runner name through TEAM_ALIASES so e.g. 'Olympiakos' matches a
    # home team already normalized to 'Olympiacos' — otherwise spelling drift
    # between event.eventName and runnerName silently drops outcomes.
    rn_lower = normalize_team(raw_name).lower()
    home_l = home.lower()
    away_l = away.lower()

    if market_type in (MarketType.FOOTBALL_FULL_TIME_1X2,):
        if rn_lower == "the draw" or rn_lower == "draw":
            return OutcomeType.DRAW, None
        if home_l and (home_l in rn_lower or rn_lower in home_l):
            return OutcomeType.HOME_WIN, None
        if away_l and (away_l in rn_lower or rn_lower in away_l):
            return OutcomeType.AWAY_WIN, None
        return None, None

    if market_type in (MarketType.BASKETBALL_MATCH_WINNER, MarketType.TENNIS_MATCH_WINNER,
                       MarketType.FOOTBALL_DRAW_NO_BET, MarketType.TENNIS_SET_WINNER):
        # Bidirectional substring catches truncated forms — Betfair tennis often
        # has runnerName="Tiafoe" while eventName had "Ja Faria v Tiafoe", so
        # away_l="tiafoe" is contained in rn_lower="tiafoe" (and vice versa).
        if home_l and (home_l in rn_lower or rn_lower in home_l):
            return OutcomeType.HOME_WIN, None
        if away_l and (away_l in rn_lower or rn_lower in away_l):
            return OutcomeType.AWAY_WIN, None
        return None, None

    if market_type == MarketType.FOOTBALL_FULL_TIME_BTTS:
        if rn_lower == "yes":
            return OutcomeType.BTTS_YES, None
        if rn_lower == "no":
            return OutcomeType.BTTS_NO, None
        return None, None

    if market_type == MarketType.FOOTBALL_DOUBLE_CHANCE:
        # Re-split the RAW name and normalize each half, since compound strings
        # like "Olympiakos or Draw" don't hit a TEAM_ALIASES entry as a whole.
        halves = [normalize_team(p.strip()).lower() for p in raw_name.split(" or ")]
        if len(halves) != 2:
            return None, None
        has_draw = any(p == "draw" or p == "the draw" for p in halves)
        non_draw = [p for p in halves if p not in ("draw", "the draw")]
        if has_draw and non_draw:
            half = non_draw[0]
            if home_l and home_l in half:
                return OutcomeType.DOUBLE_CHANCE_HOME_DRAW, None
            if away_l and away_l in half:
                return OutcomeType.DOUBLE_CHANCE_DRAW_AWAY, None
            return None, None
        return OutcomeType.DOUBLE_CHANCE_HOME_AWAY, None

    if market_type in (MarketType.FOOTBALL_FULL_TIME_OVER_UNDER,
                       MarketType.BASKETBALL_TOTAL_POINTS,
                       MarketType.TENNIS_TOTAL_GAMES):
        if rn_lower.startswith("over"):
            return OutcomeType.OVER, None
        if rn_lower.startswith("under"):
            return OutcomeType.UNDER, None
        return None, None

    if market_type in (MarketType.BASKETBALL_HANDICAP, MarketType.TENNIS_HANDICAP):
        handicap = runner.get("handicap")
        line = float(handicap) if handicap is not None else None
        if home_l and (home_l in rn_lower or rn_lower in home_l):
            return OutcomeType.HOME_WIN, line
        if away_l and (away_l in rn_lower or rn_lower in away_l):
            return OutcomeType.AWAY_WIN, line
        return None, None

    return None, None


def _map_market(mn: dict, sport: str, event_id: str, home: str, away: str,
                received_at: datetime
                ) -> list[tuple[CanonicalMarket, list[CanonicalOutcome], list[OddsQuote]]]:
    """Map one Betfair marketNode to ≥0 canonical markets. Most Betfair markets
    yield one canonical market; COMBINED_TOTAL / HANDICAP marketNodes pack
    several handicap lines together and yield one canonical market per line
    (Over/Under 18.0, 18.5, 19.0 → 3 markets). Returns [] for unknown /
    deferred types or markets with no priced runners."""
    desc = mn.get("description") or {}
    state = mn.get("state") or {}
    rates = mn.get("rates") or {}
    market_type_str = desc.get("marketType")

    if market_type_str in _DEFERRED_MARKET_TYPES:
        return []

    resolved = _resolve_market_type(market_type_str or "", sport)
    if not resolved:
        _warn_once(market_type_str, sport)
        return []
    market_type, period, market_line = resolved

    raw_rate = rates.get("marketBaseRate")
    commission_rate = raw_rate / 100.0 if raw_rate is not None else None
    bet_delay = state.get("betDelay")
    total_available = state.get("totalAvailable")
    last_match_time = _parse_iso_utc(state.get("lastMatchTime"))
    source_ts = last_match_time if last_match_time is not None else received_at
    market_status = OddsStatus.ACTIVE if state.get("status") == "OPEN" else OddsStatus.SUSPENDED
    market_total_matched = state.get("totalMatched")

    # Group runners. For non-multi-line markets, everything is in one group
    # keyed by the market's static line. For COMBINED_TOTAL / HANDICAP, group
    # by the runner's `handicap` (rounded) so each spread/total becomes its
    # own canonical market with its own market_id.
    multi_line = market_type_str in _MULTILINE_MARKET_TYPES
    groups: dict[Optional[float], list[dict]] = {}
    for runner in mn.get("runners") or []:
        if (runner.get("state") or {}).get("status") != "ACTIVE":
            continue
        back = (runner.get("exchange") or {}).get("availableToBack") or []
        if not back:
            continue
        if multi_line:
            hcap = runner.get("handicap")
            if hcap is None:
                continue
            # For HANDICAP the home runner gets -line, away gets +line; group
            # by abs(line) so they end up in the same market.
            key = round(abs(float(hcap)), 2) if market_type in (MarketType.BASKETBALL_HANDICAP,
                                                                MarketType.TENNIS_HANDICAP) \
                  else round(float(hcap), 2)
        else:
            key = market_line
        groups.setdefault(key, []).append(runner)

    out: list[tuple[CanonicalMarket, list[CanonicalOutcome], list[OddsQuote]]] = []
    for group_line, runners in groups.items():
        market_id = _market_id(event_id, market_type, period, group_line)
        market = CanonicalMarket(
            market_id=market_id,
            event_id=event_id,
            market_type=market_type,
            period=period,
            line=group_line,
            commission_rate=commission_rate,
            bet_delay=bet_delay,
            total_available=total_available,
            last_match_time=last_match_time,
            total_matched=market_total_matched,
        )
        group_outcomes: list[CanonicalOutcome] = []
        group_quotes: list[OddsQuote] = []
        for runner in runners:
            exchange = runner.get("exchange") or {}
            back = exchange.get("availableToBack") or []
            lay = exchange.get("availableToLay") or []
            outcome_type, outcome_line = _resolve_outcome(market_type, runner, home, away)
            if outcome_type is None:
                continue
            # When the line lives on the market (e.g. COMBINED_TOTAL split per
            # handicap group), clear it from the outcome so the outcome_id
            # doesn't double-encode it. Handicap markets carry the line on the
            # outcome because home gets -line and away gets +line — different
            # outcomes within the same market.
            if not multi_line or market_type in (MarketType.BASKETBALL_HANDICAP,
                                                 MarketType.TENNIS_HANDICAP):
                outcome_line_final = outcome_line
            else:
                outcome_line_final = None
            outcome_id = _outcome_id(market_id, outcome_type, outcome_line_final)
            r_state = runner.get("state") or {}
            group_outcomes.append(CanonicalOutcome(
                outcome_id=outcome_id,
                market_id=market_id,
                outcome_type=outcome_type,
                team_reference=(runner.get("description") or {}).get("runnerName"),
                line=outcome_line_final,
            ))
            group_quotes.append(OddsQuote(
                bookmaker=BOOKMAKER,
                event_id=event_id,
                market_id=market_id,
                outcome_id=outcome_id,
                decimal_odds=back[0].get("price"),
                timestamp_received=received_at,
                source_timestamp=source_ts,
                status=market_status,
                liquidity=market_total_matched,
                back_size=back[0].get("size"),
                lay_price=lay[0].get("price") if lay else None,
                lay_size=lay[0].get("size") if lay else None,
                back_price_2=back[1].get("price") if len(back) > 1 else None,
                back_size_2=back[1].get("size") if len(back) > 1 else None,
                lay_price_2=lay[1].get("price") if len(lay) > 1 else None,
                lay_size_2=lay[1].get("size") if len(lay) > 1 else None,
                total_matched=r_state.get("totalMatched"),
            ))
        if group_quotes:
            out.append((market, group_outcomes, group_quotes))
    return out
