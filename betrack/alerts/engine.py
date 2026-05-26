from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from betrack.comparison.engine import ArbitrageOpportunity, ValueOpportunity

MIN_EDGE_LIVE = 0.05
MIN_EDGE_PREMATCH = 0.025
MIN_PERSISTENCE_LIVE = 2
MIN_PERSISTENCE_PREMATCH = 3
COOLDOWN_SECONDS = 300


@dataclass
class _ValueState:
    consecutive_hits: int = 0
    last_alerted: datetime | None = None


class AlertEngine:
    def __init__(self) -> None:
        self._value_states: dict[tuple, _ValueState] = defaultdict(_ValueState)
        self._arb_last_alerted: dict[tuple, datetime] = {}

    def evaluate_value(self, opp: ValueOpportunity, is_live: bool) -> bool:
        min_edge = MIN_EDGE_LIVE if is_live else MIN_EDGE_PREMATCH
        min_persistence = MIN_PERSISTENCE_LIVE if is_live else MIN_PERSISTENCE_PREMATCH

        if opp.edge_pct < min_edge:
            self._reset_value(opp)
            return False

        key = (opp.event_id, opp.market_id, opp.outcome_id, opp.bookmaker)
        state = self._value_states[key]
        state.consecutive_hits += 1

        if state.consecutive_hits < min_persistence:
            return False

        now = datetime.now(timezone.utc)
        if state.last_alerted:
            elapsed = (now - state.last_alerted).total_seconds()
            if elapsed < COOLDOWN_SECONDS:
                return False

        state.last_alerted = now
        return True

    def evaluate_arbitrage(self, opp: ArbitrageOpportunity) -> bool:
        key = (opp.event_id, opp.market_id)
        last = self._arb_last_alerted.get(key)
        now = datetime.now(timezone.utc)

        if last and (now - last).total_seconds() < COOLDOWN_SECONDS:
            return False

        self._arb_last_alerted[key] = now
        return True

    def _reset_value(self, opp: ValueOpportunity) -> None:
        key = (opp.event_id, opp.market_id, opp.outcome_id, opp.bookmaker)
        self._value_states.pop(key, None)
