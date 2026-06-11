"""Edge + arbitrage detection on top of SqliteOddsStore.

Betfair Exchange is the *sharp reference* — its commission-adjusted back/lay
midpoint approximates true probability. Stoix/Novi are sportsbooks and carry a
vig. A value bet exists when a sportsbook's price implies a probability lower
than Betfair's fair estimate (i.e. its decimal_odds × fair_prob > 1).

Arbitrage is separately detected across all 3 books — for an exhaustive set of
mutually-exclusive outcomes, if Σ 1/best_decimal_odds < 1, riskless profit
exists. We don't require Betfair to be present in the legs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from betrack.labels import market_label, outcome_label
from betrack.models.canonical import (
    CanonicalMarket,
    CanonicalOutcome,
    MarketType,
    OutcomeType,
)
from betrack.normalization.betfair_mapper import _team_match
from betrack.normalization.mapper import normalize_team

BETFAIR = "Betfair"

# Bookmakers split into two groups:
#   * LOCAL_BOOKMAKERS — bettable books we can act on. Value bets, arbitrage
#     legs, and cross-book diffs are all sourced from here. Edits here
#     automatically flow into every detector (the loops below filter by
#     `bm in LOCAL_BOOKMAKERS` rather than hardcoding names).
#   * REFERENCE_BOOKMAKERS — sharp markets used to validate signals but
#     never placed on. Today this is just Betfair Exchange; if Smarkets or
#     another exchange is added, plug it in here.
LOCAL_BOOKMAKERS: tuple[str, ...] = ("Stoiximan", "Novibet", "Pamestoixima")
REFERENCE_BOOKMAKERS: tuple[str, ...] = (BETFAIR,)

# Liquidity / spread floor for Betfair to count as a sharp reference. A market
# is disqualified ENTIRELY (returns None from _market_fair_probs) if ANY leg
# fails these — de-vig requires apples-to-apples midpoints on every leg, so a
# single illiquid leg poisons the whole calculation. Three independent signals:
#  * lay/back spread: how tight is the bid-ask
#  * back_size + lay_size: open order book depth
#  * total_matched: ACTUAL volume that has traded
# The third is decisive — markets with £91 of open back offers but £0 traded
# (e.g. Brazilian Serie B Betfair lines that nobody bets) carry seed prices
# from the bookmaker, not market consensus. Without this gate, those seeds
# produce phantom +200%+ edges against real Stoix/Novi prices.
MAX_LAY_OVER_BACK = 1.30
MIN_LIQUIDITY_PER_LEG = 20.0   # back_size + lay_size, in market currency (GBP)
MIN_TOTAL_MATCHED = 100.0      # cumulative volume actually traded on this runner

# Cross-book sanity ceiling: if two bettable books quote the same outcome with
# implied probabilities that differ by more than this ratio, one of them is on
# a different game state (pre-game vs live, stale snapshot, or vendor glitch)
# even if both rows are technically fresh. Their "arb" will collapse when the
# lagging book catches up. 1.5x corresponds to e.g. one book at 60% prob and
# the other at 40% — already aggressive disagreement for the same market.
MAX_BOOK_PROB_RATIO = 1.5
MAX_DIFF_PROB_RATIO = 1.5

# Additional cross-book validator using Betfair as the sharp reference: when
# Betfair quotes the same market with tight liquid prices, reject an arb if
# any bettable leg's implied probability is more than this ratio off Betfair's
# de-vigged fair probability. Catches the asymmetric case where the cross-book
# ratio is borderline but one side is clearly mispriced vs the sharp market
# (e.g. Stoix at 1.36 = 73% prob matching Betfair, Novi at 2.30 = 43% prob).
MAX_BOOK_VS_FAIR_RATIO = 1.6

# Per-leg quote-age ceiling for arbitrage legs. The SQL fresh_seconds filter
# at the store level admits ANY row younger than the threshold — but legs of
# the same arb can still differ by >2 min within a single result set, which
# kills the "place all bets before either moves" premise. Stricter than the
# value gate because arbs require simultaneous execution. With POLL_INTERVAL=10s
# this admits ~3 cycles of lag; at the old 30s polls it was a no-op (anything
# fresh-enough for the store filter passed), now it's an active staleness gate.
MAX_LEG_AGE_FOR_ARB = 30

# Pair-skew gate. Every signal type relies on comparing TWO quotes:
#   * arb  : leg-A vs leg-B (different bookmakers)
#   * value: bookmaker quote vs Betfair fair-reference
#   * diff : Stoix vs Novi
# Even if each side passes its own freshness filter individually, a pair where
# one side is e.g. 2s old and the other is 28s old is pricing different moments
# of the match. During live action that's the dominant phantom-signal source:
# the gap is just one side lagging, not a real disagreement. Cap the spread of
# observed_at across the relevant pair.
MAX_QUOTE_PAIR_SKEW = 15


# Markets whose canonical outcomes are exhaustive (Σ probabilities = 1) and
# therefore eligible for the simple arb formula. Double-chance is NOT here:
# 1X / 12 / X2 outcomes overlap (Σ probs = 2).
_EXHAUSTIVE_MARKET_TYPES: frozenset[MarketType] = frozenset({
    MarketType.FOOTBALL_FULL_TIME_1X2,
    MarketType.FOOTBALL_FULL_TIME_OVER_UNDER,
    MarketType.FOOTBALL_FULL_TIME_BTTS,
    MarketType.FOOTBALL_DRAW_NO_BET,
    MarketType.BASKETBALL_MATCH_WINNER,
    MarketType.BASKETBALL_TOTAL_POINTS,
    MarketType.BASKETBALL_HANDICAP,
    MarketType.TENNIS_MATCH_WINNER,
    MarketType.TENNIS_TOTAL_GAMES,
    MarketType.TENNIS_HANDICAP,
    MarketType.TENNIS_SET_WINNER,
})


def _books_disagree_too_much(outc: dict) -> bool:
    """True if the local books on this outcome quote implied probabilities
    so different that one of them is almost certainly on a stale / glitched
    game state. Compares across LOCAL_BOOKMAKERS only (reference books are
    sharp anchors, not bettable legs) and flags when max/min > MAX_BOOK_PROB_RATIO."""
    probs = []
    for bm, q in outc["books"].items():
        if bm not in LOCAL_BOOKMAKERS:
            continue
        o = q.get("decimal_odds")
        if o and o > 1.0:
            probs.append(1.0 / o)
    if len(probs) < 2:
        return False
    return max(probs) / min(probs) > MAX_BOOK_PROB_RATIO


def _is_local(bookmaker: str) -> bool:
    return bookmaker in LOCAL_BOOKMAKERS


def _is_reference(bookmaker: str) -> bool:
    return bookmaker in REFERENCE_BOOKMAKERS


def _team_refs_agree(outc: dict) -> bool:
    """Cross-bookmaker integrity check: when we group outcomes by outcome_type,
    each leg's books must point at the same real-world team. Otherwise an old
    sticky cross-match (e.g. 'Libertad FC' Paraguay vs 'Libertad Loja' Ecuador,
    both lazily merged earlier when one book temporarily dropped the suffix)
    pairs the wrong runners and produces phantom value/arb signals.

    Two team-references agree when `_team_match` (lowercased + alias-normalized)
    returns True — exact, substring, or token-set/prefix per fuzzy rules. Empty
    or missing labels are skipped (e.g. OVER/UNDER outcomes don't carry a team)."""
    refs = [outc["books"][bm].get("team_reference") for bm in outc["books"]]
    refs = [normalize_team((r or "").strip()).lower() for r in refs if r]
    if len(refs) < 2:
        return True
    pivot = refs[0]
    for r in refs[1:]:
        if not _team_match(pivot, r):
            return False
    return True


def _age_seconds(now: datetime, observed_at: Optional[str]) -> Optional[int]:
    if not observed_at:
        return None
    try:
        return int((now - datetime.fromisoformat(observed_at)).total_seconds())
    except ValueError:
        return None


def _quote_age(now: datetime, quote: dict) -> Optional[int]:
    """Age based on last_changed_at (when the price actually moved) with a
    fallback to observed_at. This is what every staleness/skew check should
    use: a price re-confirmed every cycle but frozen for many minutes IS
    stale even though observed_at is fresh — that's how phantom arbs survive
    the cross-book sanity gates."""
    return _age_seconds(now, quote.get("last_changed_at") or quote.get("observed_at"))


def _market_label_for(market_type: str, line: Optional[float]) -> str:
    return market_label(CanonicalMarket(
        market_id="_", event_id="_", market_type=MarketType(market_type), line=line,
    ))


def _outcome_label_for(outcome_type: str, team_reference: Optional[str],
                      line: Optional[float]) -> str:
    return outcome_label(CanonicalOutcome(
        outcome_id="_", market_id="_", outcome_type=OutcomeType(outcome_type),
        team_reference=team_reference, line=line,
    ))


def _group(rows: list[dict]) -> dict[tuple, dict]:
    """Group flat rows by (event_id, market_type, period, m_line). Each group
    contains the event metadata and an outcomes dict keyed by
    (outcome_type, o_line) → {bookmaker → quote-row}.

    `market_total_matched` is propagated per book — it's the same number for
    every row of the same (market, bookmaker), so we take the max across rows
    when looking up Betfair's market matched volume."""
    out: dict[tuple, dict] = {}
    for r in rows:
        mkey = (r["event_id"], r["market_type"], r["period"], r["m_line"])
        ent = out.setdefault(mkey, {
            "event_id": r["event_id"],
            "home_team": r["home_team"],
            "away_team": r["away_team"],
            "sport": r["sport"],
            "competition": r["competition"],
            "country": r.get("country"),
            "market_type": r["market_type"],
            "period": r["period"],
            "m_line": r["m_line"],
            "outcomes": {},
            "betfair_market_total_matched": None,
        })
        # Betfair rows carry market_total_matched in the joined column; track
        # the max we've seen (all rows for the same Betfair market share this
        # number since it's a market-level field, not per-outcome).
        if r["bookmaker"] == BETFAIR:
            mtm = r.get("market_total_matched")
            if mtm is not None and (ent["betfair_market_total_matched"] is None
                                    or mtm > ent["betfair_market_total_matched"]):
                ent["betfair_market_total_matched"] = mtm
        okey = (r["outcome_type"], r["o_line"])
        outc = ent["outcomes"].setdefault(okey, {
            "outcome_type": r["outcome_type"],
            "team_reference": r["team_reference"],
            "o_line": r["o_line"],
            # outcome_id varies per bookmaker (id includes bookmaker hash),
            # so store one per book for the EventDrawer drill-in.
            "outcome_ids": {},
            "books": {},
        })
        if r["team_reference"]:
            outc["team_reference"] = r["team_reference"]
        outc["outcome_ids"][r["bookmaker"]] = r["outcome_id"]
        outc["books"][r["bookmaker"]] = r
    return out


def _market_fair_probs(market: dict, outcomes: dict[tuple, dict]) -> Optional[dict[tuple, float]]:
    """De-vigged fair probability per outcome from Betfair midpoints.

    Returns None if Betfair doesn't have a tight, liquid two-sided market on
    EVERY outcome. The de-vig divides each leg's midpoint by the sum across
    legs, so a single illiquid leg (e.g. BTTS Yes with lay=75 vs back=1.45)
    would skew the entire result. By disqualifying the whole market we avoid
    the +250% phantom edges those Betfair markets used to produce.

    midpoint_prob_i = (1/back_i + 1/lay_i) / 2 — requires both sides present.
    fair_p_i = midpoint_i / Σ midpoint_j  (de-vig)."""
    # Market-level matched volume: Betfair's `state.totalMatched` on the
    # marketNode (per-runner totalMatched is always 0 in live API responses).
    # Below MIN_TOTAL_MATCHED, prices are seed/untraded and not real consensus.
    mtm = market.get("betfair_market_total_matched")
    if mtm is None or mtm < MIN_TOTAL_MATCHED:
        return None
    mids: dict[tuple, float] = {}
    for okey, outc in outcomes.items():
        if not _team_refs_agree(outc):
            return None  # sticky bad cross-match — books point at different teams
        bf = outc["books"].get(BETFAIR)
        if not bf:
            return None
        back = bf.get("decimal_odds")
        lay = bf.get("lay_price")
        back_size = bf.get("back_size") or 0
        lay_size = bf.get("lay_size") or 0
        if not back or back <= 1.0 or not lay or lay <= 1.0:
            return None
        if lay / back > MAX_LAY_OVER_BACK:
            return None
        if back_size + lay_size < MIN_LIQUIDITY_PER_LEG:
            return None
        mids[okey] = (1.0 / back + 1.0 / lay) / 2.0
    total = sum(mids.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in mids.items()}


def _value_opportunities(market: dict, now: datetime, min_edge: float) -> list[dict]:
    fair_probs = _market_fair_probs(market, market["outcomes"])
    if fair_probs is None:
        return []
    bf_outcomes = market["outcomes"]
    bf_back = {k: o["books"][BETFAIR].get("decimal_odds") for k, o in bf_outcomes.items()}
    bf_lay = {k: o["books"][BETFAIR].get("lay_price") for k, o in bf_outcomes.items()}
    market_matched = market.get("betfair_market_total_matched")
    out: list[dict] = []
    for okey, outc in bf_outcomes.items():
        fair_p = fair_probs[okey]
        bf_quote = outc["books"].get(BETFAIR) or {}
        bf_age = _quote_age(now, bf_quote)
        for bm, quote in outc["books"].items():
            if not _is_local(bm):
                continue  # value bets are placed on local books only
            odds = quote.get("decimal_odds")
            if not odds or odds <= 1.0:
                continue
            book_age = _quote_age(now, quote)
            # Pair-skew gate: edge vs sharp is meaningless if one side hasn't
            # refreshed since the other moved — the "value" is just a lag gap.
            if (book_age is not None and bf_age is not None
                    and abs(book_age - bf_age) > MAX_QUOTE_PAIR_SKEW):
                continue
            edge = odds * fair_p - 1.0
            if edge < min_edge:
                continue
            out.append({
                "type": "value",
                "event_id": market["event_id"],
                "home_team": market["home_team"],
                "away_team": market["away_team"],
                "sport": market["sport"],
                "competition": market["competition"],
                "country": market.get("country"),
                "market_type": market["market_type"],
                "market_label": _market_label_for(market["market_type"], market["m_line"]),
                "period": market["period"],
                "line": market["m_line"],
                "outcome_type": outc["outcome_type"],
                "outcome_label": _outcome_label_for(
                    outc["outcome_type"], outc["team_reference"], outc["o_line"]),
                "bookmaker": bm,
                "bookmaker_odds": odds,
                "implied_prob_at_book": 1.0 / odds,
                "fair_prob": fair_p,
                "edge_pct": edge,
                "betfair_back": bf_back[okey],
                "betfair_lay": bf_lay[okey],
                # Market-level matched volume from Betfair — the liquidity
                # signal that drives the sharp-reference gate.
                "liquidity_total_matched": market_matched,
                "age_seconds": book_age,
                "outcome_id": outc["outcome_ids"].get(bm),
            })
    return out


# Tiered min-gap for diffs: at high decimal odds, the underlying probability
# moves in tiny absolute steps (10.0 vs 15.0 = 10% vs 6.67% implied prob,
# only 3.3 percentage points apart), so a 50% ratio gap is normal noise rather
# than a real disagreement. We require a much wider gap to call it a signal,
# and we lift the sanity ceiling because the cross-book-staleness heuristic
# doesn't apply to low-probability outcomes the same way.
HIGH_ODDS_THRESHOLD = 10.0


def _local_diffs(market: dict, now: datetime, min_gap: float,
                 min_gap_high_odds: float) -> list[dict]:
    """Per-outcome cross-book disagreement across LOCAL_BOOKMAKERS. Reference
    books (Betfair) are shown alongside as context but never compared — this
    detector is about local-book mispricing relative to each other, distinct
    from value (vs sharp reference) and arb (cross-leg structure).

    For each outcome we collect every local book that quotes it and find the
    high/low pair. A row qualifies when at least two local books quote it AND
    the high/low ratio gap ≥ the applicable threshold. The threshold is
    `min_gap_high_odds` when BOTH legs of that pair are ≥ HIGH_ODDS_THRESHOLD,
    else `min_gap`. Normal-odds rows also enforce the data-sanity ceiling
    (gap < MAX_DIFF_PROB_RATIO - 1, i.e. odds_ratio < 2×); high-odds rows
    skip it because wide ratios there reflect small absolute-probability
    deltas, not staleness. Pair-skew applies to the (high, low) pair — the
    legs you'd actually bet — not to bystander books quoting somewhere in the
    middle. team_reference consistency required across all participating
    local books, same as before."""
    out: list[dict] = []
    for okey, outc in market["outcomes"].items():
        if not _team_refs_agree(outc):
            continue
        books = outc["books"]
        local_quotes: dict[str, dict] = {}
        for bm in LOCAL_BOOKMAKERS:
            q = books.get(bm)
            if not q:
                continue
            o = q.get("decimal_odds")
            if not o or o <= 1.0:
                continue
            local_quotes[bm] = q
        if len(local_quotes) < 2:
            continue
        # high = best price for the bettor (most underpriced book on this
        # outcome), low = the comparison side. Picking the extremes makes
        # this collapse cleanly to the old behavior for the 2-book case.
        high_book = max(local_quotes, key=lambda b: local_quotes[b]["decimal_odds"])
        low_book = min(local_quotes, key=lambda b: local_quotes[b]["decimal_odds"])
        if high_book == low_book:
            continue  # all books quote identically — no diff
        hi_odds = local_quotes[high_book]["decimal_odds"]
        lo_odds = local_quotes[low_book]["decimal_odds"]
        gap = hi_odds / lo_odds - 1.0

        is_high_odds = hi_odds >= HIGH_ODDS_THRESHOLD and lo_odds >= HIGH_ODDS_THRESHOLD
        effective_min_gap = min_gap_high_odds if is_high_odds else min_gap
        if gap < effective_min_gap:
            continue
        if not is_high_odds and gap >= MAX_DIFF_PROB_RATIO - 1.0:
            continue

        # Pair-skew on the chosen (high, low) pair. A third local book in
        # the middle isn't part of the implied bet, so its observed time
        # doesn't matter for the disagreement signal.
        hi_age = _quote_age(now, local_quotes[high_book])
        lo_age = _quote_age(now, local_quotes[low_book])
        if (hi_age is not None and lo_age is not None
                and abs(hi_age - lo_age) > MAX_QUOTE_PAIR_SKEW):
            continue

        books_payload = {
            bm: {
                "odds": local_quotes[bm]["decimal_odds"],
                "age_seconds": _quote_age(now, local_quotes[bm]),
                "outcome_id": outc["outcome_ids"].get(bm),
            }
            for bm in local_quotes
        }
        bf = books.get(BETFAIR)
        out.append({
            "type": "diff",
            "event_id": market["event_id"],
            "home_team": market["home_team"],
            "away_team": market["away_team"],
            "sport": market["sport"],
            "competition": market["competition"],
            "country": market.get("country"),
            "market_type": market["market_type"],
            "market_label": _market_label_for(market["market_type"], market["m_line"]),
            "period": market["period"],
            "line": market["m_line"],
            "outcome_type": outc["outcome_type"],
            "outcome_label": _outcome_label_for(
                outc["outcome_type"], outc["team_reference"], outc["o_line"]),
            "books": books_payload,
            "high_book": high_book,
            "high_odds": hi_odds,
            "low_book": low_book,
            "low_odds": lo_odds,
            "gap_pct": gap,
            "betfair_back": bf.get("decimal_odds") if bf else None,
            "betfair_lay": bf.get("lay_price") if bf else None,
        })
    return out


def _arbitrage_opportunity(market: dict, now: datetime,
                           min_roi: float) -> Optional[dict]:
    try:
        mt = MarketType(market["market_type"])
    except ValueError:
        return None
    if mt not in _EXHAUSTIVE_MARKET_TYPES:
        return None
    outcomes = market["outcomes"]
    if not outcomes:
        return None
    # For exhaustive markets, expected outcome counts by market_type:
    expected = {
        MarketType.FOOTBALL_FULL_TIME_1X2: 3,
        MarketType.FOOTBALL_FULL_TIME_OVER_UNDER: 2,
        MarketType.FOOTBALL_FULL_TIME_BTTS: 2,
        MarketType.FOOTBALL_DRAW_NO_BET: 2,
        MarketType.BASKETBALL_MATCH_WINNER: 2,
        MarketType.BASKETBALL_TOTAL_POINTS: 2,
        MarketType.BASKETBALL_HANDICAP: 2,
        MarketType.TENNIS_MATCH_WINNER: 2,
        MarketType.TENNIS_TOTAL_GAMES: 2,
        MarketType.TENNIS_HANDICAP: 2,
        MarketType.TENNIS_SET_WINNER: 2,
    }.get(mt)
    if expected is None or len(outcomes) != expected:
        return None  # short-changed market, skip — arb needs all legs

    # If Betfair quotes this market liquidly, use its de-vigged fair probs
    # as a tiebreaker — a leg whose bookmaker implied prob is way off the
    # sharp is the stale/wrong side, regardless of the cross-book ratio.
    fair_probs = _market_fair_probs(market, outcomes)

    legs: list[dict] = []
    inv_sum = 0.0
    for okey, outc in outcomes.items():
        if not _team_refs_agree(outc):
            return None  # sticky bad cross-match — books point at different teams
        if _books_disagree_too_much(outc):
            return None  # books are on different game states (pre-game vs live, stale, glitched)
        # Best decimal_odds across LOCAL_BOOKMAKERS only — reference books
        # (Betfair) are excluded from leg selection (their midpoint is the
        # fair-value signal; we never place an arb leg on the exchange itself).
        # Per-leg age also gated here: an arb requires all legs to be
        # simultaneously executable, so a leg older than the threshold
        # disqualifies the market entirely.
        best_bm = None
        best_odds = None
        best_oid = None
        best_age = None
        for bm, quote in outc["books"].items():
            if not _is_local(bm):
                continue  # arbs are placed on local books only
            o = quote.get("decimal_odds")
            if not o or o <= 1.0:
                continue
            age = _quote_age(now, quote)
            if age is None or age > MAX_LEG_AGE_FOR_ARB:
                continue
            if best_odds is None or o > best_odds:
                best_odds = o
                best_bm = bm
                best_oid = outc["outcome_ids"].get(bm)
                best_age = age
        if best_odds is None:
            return None
        # Betfair tiebreaker: if Betfair quotes a fair probability for this
        # outcome, the chosen leg's implied prob must be within MAX_BOOK_VS_FAIR_RATIO
        # of it. Catches asymmetric staleness — one book matching sharp, the
        # other lagging far behind — that slips under the cross-book ratio.
        if fair_probs is not None and okey in fair_probs:
            leg_prob = 1.0 / best_odds
            fair_p = fair_probs[okey]
            if fair_p > 0:
                ratio = max(leg_prob / fair_p, fair_p / leg_prob)
                if ratio > MAX_BOOK_VS_FAIR_RATIO:
                    return None
        inv_sum += 1.0 / best_odds
        legs.append({
            "outcome_type": outc["outcome_type"],
            "outcome_label": _outcome_label_for(
                outc["outcome_type"], outc["team_reference"], outc["o_line"]),
            "line": outc["o_line"],
            "bookmaker": best_bm,
            "odds": best_odds,
            "outcome_id": best_oid,
            "age_seconds": best_age,
        })
    # An "arb" needs at least 2 distinct bookmakers across the legs — otherwise
    # it's a single book mispricing itself (Stoix has 1X2 odds summing < 1 on
    # its own — that's their lookout, but not a cross-book arb).
    if len({leg["bookmaker"] for leg in legs}) < 2:
        return None
    # Pair-skew gate: the chosen legs must all be observed within a tight window
    # of each other. Per-leg MAX_LEG_AGE_FOR_ARB catches absolute staleness, but
    # a 2s-old leg paired with a 28s-old leg is still pricing different moments
    # of the match — that's the dominant phantom-arb source in live play.
    leg_ages = [leg["age_seconds"] for leg in legs if leg.get("age_seconds") is not None]
    if leg_ages and max(leg_ages) - min(leg_ages) > MAX_QUOTE_PAIR_SKEW:
        return None
    roi = 1.0 - inv_sum
    if roi < min_roi:
        return None
    return {
        "type": "arb",
        "event_id": market["event_id"],
        "home_team": market["home_team"],
        "away_team": market["away_team"],
        "sport": market["sport"],
        "competition": market["competition"],
        "country": market.get("country"),
        "market_type": market["market_type"],
        "market_label": _market_label_for(market["market_type"], market["m_line"]),
        "period": market["period"],
        "line": market["m_line"],
        "roi_pct": roi,
        "legs": legs,
    }


def find_opportunities(
    store: Any,
    *,
    sport: Optional[str] = None,
    min_edge: float = 0.03,
    min_gap: float = 0.15,
    min_gap_high_odds: float = 0.50,
    min_roi: float = 0.05,
    fresh_seconds: int = 30,
) -> dict[str, list[dict]]:
    """Returns {'value': [...], 'arb': [...], 'diffs': [...]} sorted by edge
    / roi / gap descending. Recomputed from quote_latest on every request —
    cheap at our scale. `min_edge` thresholds value (vs Betfair sharp);
    `min_gap` thresholds local-only Stoix-vs-Novi diffs; `min_gap_high_odds`
    is the (typically wider) threshold used when both books quote
    >= HIGH_ODDS_THRESHOLD on the outcome — see _local_diffs. `min_roi`
    thresholds arbitrage ROI (1 - Σ 1/odds) — sub-5% arbs aren't worth the
    execution risk at our 10s polling cadence."""
    now = datetime.now(timezone.utc)
    rows = store.get_all_quote_rows(sport=sport, fresh_seconds=fresh_seconds)
    grouped = _group(rows)
    value_opps: list[dict] = []
    arb_opps: list[dict] = []
    diff_opps: list[dict] = []
    for market in grouped.values():
        value_opps.extend(_value_opportunities(market, now, min_edge))
        arb = _arbitrage_opportunity(market, now, min_roi)
        if arb:
            arb_opps.append(arb)
        diff_opps.extend(_local_diffs(market, now, min_gap, min_gap_high_odds))
    value_opps.sort(key=lambda o: o["edge_pct"], reverse=True)
    arb_opps.sort(key=lambda o: o["roi_pct"], reverse=True)
    diff_opps.sort(key=lambda o: o["gap_pct"], reverse=True)
    return {"value": value_opps, "arb": arb_opps, "diffs": diff_opps}
