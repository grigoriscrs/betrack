from __future__ import annotations

from betrack.comparison.engine import ArbitrageOpportunity, ValueOpportunity
from betrack.models.canonical import CanonicalEvent
from betrack.store.odds_store import OddsStore

_SEP = "=" * 52


def print_value_alert(opp: ValueOpportunity, event: CanonicalEvent, store: OddsStore) -> None:
    outcome = store._outcomes.get(opp.outcome_id)
    market = store._markets.get(opp.market_id)
    outcome_label = outcome.outcome_type.value if outcome else opp.outcome_id
    market_label = market.market_type.value if market else opp.market_id

    print(
        f"\n{_SEP}\n"
        f"VALUE ALERT\n"
        f"Event:     {event.home_team} vs {event.away_team}\n"
        f"Status:    {event.status.value.upper()}\n"
        f"Market:    {market_label}\n"
        f"Outcome:   {outcome_label}\n"
        f"Bookmaker: {opp.bookmaker} @ {opp.bookmaker_odds:.2f}\n"
        f"Reference: {opp.reference_odds:.2f} (stoiximan)\n"
        f"Edge:      +{opp.edge_pct * 100:.1f}%\n"
        f"Time:      {opp.timestamp.strftime('%H:%M:%S UTC')}\n"
        f"{_SEP}"
    )


def print_arb_alert(opp: ArbitrageOpportunity, event: CanonicalEvent, store: OddsStore) -> None:
    lines = [
        f"\n{_SEP}",
        "ARBITRAGE ALERT",
        f"Event:  {event.home_team} vs {event.away_team}",
        f"Margin: +{opp.margin * 100:.2f}%",
    ]
    for outcome_id, (bookmaker, odds) in opp.legs.items():
        outcome = store._outcomes.get(outcome_id)
        label = outcome.outcome_type.value if outcome else outcome_id
        lines.append(f"  {label:<12} {bookmaker} @ {odds:.2f}")
    lines.append(f"Time:   {opp.timestamp.strftime('%H:%M:%S UTC')}")
    lines.append(_SEP)
    print("\n".join(lines))
