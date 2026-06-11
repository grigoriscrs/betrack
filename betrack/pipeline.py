from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from betrack.ingestion.betfair import (
    BetfairClient,
    EVENT_TYPE_BASKETBALL,
    EVENT_TYPE_FOOTBALL,
    EVENT_TYPE_TENNIS,
)
from betrack.ingestion.novibet import NovibetClient
from betrack.ingestion.pamestoixima import PamestoiximaClient
from betrack.ingestion.stoiximan import StoiximanClient
from betrack.normalization import (
    betfair_mapper,
    novibet_mapper,
    pamestoixima_mapper,
    stoiximan_mapper,
)
from betrack.normalization.bundle import MappedEvent
from betrack.normalization.mapper import normalize_team
from betrack.store.odds_store_sqlite import SqliteOddsStore

logger = logging.getLogger(__name__)


@dataclass
class CycleResult:
    ran_at: datetime
    counts: dict[str, dict[str, int]] = field(default_factory=dict)  # "Bookmaker/sport" -> metrics
    total_observed: int = 0
    total_changed: int = 0
    errors: list[str] = field(default_factory=list)


async def _capped(factories: list[Callable[[], Awaitable]], cap: int) -> list:
    sem = asyncio.Semaphore(cap)

    async def run(factory: Callable[[], Awaitable]):
        async with sem:
            try:
                return await factory()
            except Exception as exc:
                logger.warning("per-event detail fetch failed: %s", exc)
                return None

    return await asyncio.gather(*(run(f) for f in factories))


def _extract_market_ids(byevent: dict) -> list[str]:
    out: list[str] = []
    for et in byevent.get("eventTypes", []) or []:
        for en in et.get("eventNodes", []) or []:
            for mn in en.get("marketNodes", []) or []:
                mid = mn.get("marketId")
                if mid:
                    out.append(str(mid))
    return out


def _chunked(seq: list, size: int) -> list[list]:
    return [seq[i:i + size] for i in range(0, len(seq), size)]


_BETFAIR_SPORTS: tuple[tuple[str, int], ...] = (
    ("football", EVENT_TYPE_FOOTBALL),
    ("basketball", EVENT_TYPE_BASKETBALL),
    ("tennis", EVENT_TYPE_TENNIS),
)


def _build_xmatch_index(store: SqliteOddsStore, *, fresh_seconds: int = 180
                        ) -> dict[tuple[str, str, str], str]:
    """Index live Stoix/Novi events so Betfair can cross-match by team names.
    Keys are (sport, home_lower, away_lower). Both sides go through
    normalize_team — Stoix/Novi may pass through 'Arsenal FC' from a per-event
    detail call while Betfair has 'Arsenal' from the overview eventName; only
    the alias map collapses them. We do NOT key on start_time — Stoix/Novi
    stamp received_at for live events, not actual kickoff."""
    index: dict[tuple[str, str, str], str] = {}
    for sport in ("football", "basketball", "tennis"):
        for ev in store.get_events_by_sport(sport, status="live",
                                            fresh_within_seconds=fresh_seconds):
            home = normalize_team(ev.get("home_team") or "").lower()
            away = normalize_team(ev.get("away_team") or "").lower()
            if not home or not away:
                continue
            index[(sport, home, away)] = ev["event_id"]
    return index


async def run_cycle(
    stoiximan: StoiximanClient,
    novibet: NovibetClient,
    pamestoixima: PamestoiximaClient,
    betfair: Optional[BetfairClient],
    store: SqliteOddsStore,
    *,
    detail_concurrency: int = 8,
    detail_limit: int = 60,
    betfair_list_max: int = 300,
) -> CycleResult:
    """One poll round: fetch every bookmaker concurrently, then write to SQLite
    off the event loop so request handlers stay responsive. Concurrent fetches
    minimise the time-skew between books — sequential fetches let a goal scored
    mid-cycle produce phantom cross-book arbs because one book was queried
    pre-goal and another post-goal. Each book stamps its OWN now() inside its
    task so quote_latest.observed_at still drifts realistically per book."""
    cycle_start = datetime.now(timezone.utc)
    result = CycleResult(ran_at=cycle_start)
    seen: dict = {}

    # Cross-match index uses store state from the PREVIOUS cycle so Betfair can
    # cross-match without waiting for Stoix/Novi to finish + write. Brand-new
    # events that first appear this cycle get cross-matched next cycle (one
    # cycle of latency, fine).
    async def _build_xmatch_async() -> dict:
        return await asyncio.to_thread(_build_xmatch_index, store)

    async def _stoix_bundles() -> list[MappedEvent]:
        s_now = datetime.now(timezone.utc)
        overview = await stoiximan.fetch_overview()
        bundles = stoiximan_mapper.map_overview(overview, s_now)
        live = stoiximan_mapper.live_event_ids(overview)[:detail_limit]
        factories = [
            (lambda eid=eid: stoiximan.fetch_event(eid)) for _sport, eid in live
        ]
        for detail in await _capped(factories, detail_concurrency):
            if not detail:
                continue
            bundle = stoiximan_mapper.map_event_detail(detail, s_now)
            if bundle:
                bundles.append(bundle)
        return bundles

    async def _novi_bundles() -> list[MappedEvent]:
        n_now = datetime.now(timezone.utc)
        overview = await novibet.fetch_overview()
        bundles = novibet_mapper.map_overview(overview, n_now)
        live = novibet_mapper.live_event_ids(overview)[:detail_limit]

        async def _novi_detail(sport: str, eid: int) -> Optional[MappedEvent]:
            detail = await novibet.fetch_event(eid)
            return novibet_mapper.map_event_detail(detail, sport, n_now)

        factories = [
            (lambda sport=sport, eid=eid: _novi_detail(sport, eid)) for sport, eid in live
        ]
        for bundle in await _capped(factories, detail_concurrency):
            if bundle:
                bundles.append(bundle)
        return bundles

    async def _pame_bundles() -> list[MappedEvent]:
        p_now = datetime.now(timezone.utc)
        overview = await pamestoixima.fetch_overview()
        bundles = pamestoixima_mapper.map_overview(overview, p_now)
        live = pamestoixima_mapper.live_event_ids(overview)[:detail_limit]
        factories = [
            (lambda eid=eid: pamestoixima.fetch_event(eid)) for _sport, eid in live
        ]
        for detail in await _capped(factories, detail_concurrency):
            if not detail:
                continue
            bundle = pamestoixima_mapper.map_event_detail(detail, p_now)
            if bundle:
                bundles.append(bundle)
        return bundles

    async def _betfair_bundles_and_errors() -> tuple[list[MappedEvent], list[str]]:
        """Returns (bundles, per-sport errors). Empty list if betfair is None."""
        if betfair is None:
            return [], []
        xmatch = await _build_xmatch_async()
        b_now = datetime.now(timezone.utc)

        async def _one_sport(sport_slug: str, event_type_id: int) -> list[MappedEvent]:
            scan = await betfair.list_in_play(event_type_id, max_results=betfair_list_max)
            ev_ids = [eid for (_s, eid) in betfair_mapper.live_event_ids(scan, b_now)
                      if _s == sport_slug][:detail_limit]
            if not ev_ids:
                return []
            byevent_payloads: list[dict] = []
            for ev_chunk in _chunked(ev_ids, 3):
                try:
                    byevent_payloads.append(await betfair.fetch_event_markets(ev_chunk))
                except Exception as exc:
                    logger.warning("betfair/%s byevent chunk failed: %s", sport_slug, exc)
            market_ids: list[str] = []
            for p in byevent_payloads:
                market_ids.extend(_extract_market_ids(p))
            if not market_ids:
                return []
            factories = [
                (lambda mids=chunk: betfair.fetch_markets(mids, rollup_limit=10))
                for chunk in _chunked(market_ids, 25)
            ]
            bundles_for_sport: list[MappedEvent] = []
            for payload in await _capped(factories, detail_concurrency):
                if not payload:
                    continue
                bundles_for_sport.extend(
                    betfair_mapper.map_event_detail(payload, b_now, xmatch)
                )
            return bundles_for_sport

        sport_tasks = [_one_sport(slug, etid) for slug, etid in _BETFAIR_SPORTS]
        per_sport = await asyncio.gather(*sport_tasks, return_exceptions=True)
        all_bundles: list[MappedEvent] = []
        errors: list[str] = []
        for (slug, _etid), outcome in zip(_BETFAIR_SPORTS, per_sport):
            if isinstance(outcome, Exception):
                msg = f"betfair/{slug}: {outcome.__class__.__name__}: {outcome}"
                logger.warning(msg)
                errors.append(msg)
                continue
            all_bundles.extend(outcome)
        return all_bundles, errors

    # --- Run all 4 books concurrently ---
    stoix_res, novi_res, pame_res, betfair_res = await asyncio.gather(
        _stoix_bundles(),
        _novi_bundles(),
        _pame_bundles(),
        _betfair_bundles_and_errors(),
        return_exceptions=True,
    )

    # --- Write in order: Stoix, Novi, Pame, Betfair. SQLite serializes
    # writers anyway under WAL; explicit ordering keeps counts deterministic. ---
    for label, res in (("stoiximan", stoix_res),
                       ("novibet", novi_res),
                       ("pamestoixima", pame_res)):
        if isinstance(res, Exception):
            logger.warning("%s cycle failed: %s", label, res)
            result.errors.append(f"{label}: {res}")
            continue
        if res:
            obs, chg = await asyncio.to_thread(store.write_bundles, res, result.counts, seen)
            result.total_observed += obs
            result.total_changed += chg

    if isinstance(betfair_res, Exception):
        logger.warning("betfair cycle failed: %s", betfair_res)
        result.errors.append(f"betfair: {betfair_res.__class__.__name__}: {betfair_res}")
    else:
        bf_bundles, bf_errors = betfair_res
        result.errors.extend(bf_errors)
        if bf_bundles:
            obs, chg = await asyncio.to_thread(
                store.write_bundles, bf_bundles, result.counts, seen,
            )
            result.total_observed += obs
            result.total_changed += chg

    return result
