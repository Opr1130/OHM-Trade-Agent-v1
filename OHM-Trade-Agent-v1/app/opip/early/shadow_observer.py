"""Shadow observation for Issue #223 Phases 1, 2 and 14.

This is the seam that connects the already-existing early-capable machinery to
the new selector, and it is deliberately the only place where the three shadow
phases meet. Nothing here changes production selection, alert eligibility or
any gate: every function returns evidence rows.

The delta features the challenger needs are derived from the bounded
full-universe per-symbol scan history that
:mod:`app.services.full_market_observation` already maintains
(``history_by_symbol``, schema 2, ``DEFAULT_HISTORY_SCANS`` deep). That history
is read, not rebuilt, and no new evidence plane is introduced: rows land in the
existing qualification directory through
:func:`app.opip.decision.store.append_early_timing_milestones`.

Point-in-time discipline: every feature is a difference between observations
whose ``observed_at`` is at or before ``decision_at``. No peak, MFE, MAE or
resolved outcome is reachable from here.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.opip.decision.store import append_early_timing_milestones
from app.opip.early.cohort_selector import (
    EarlyCandidateFeatures,
    compare_with_production,
    select_early_candidates,
)
from app.opip.early.flags import (
    early_selector_shadow_enabled,
    early_timeframe_shadow_enabled,
    early_timing_ledger_enabled,
)
from app.opip.early.point_in_time import assert_point_in_time_safe, parse_timestamp
from app.opip.early.taxonomy import MarketPhase, coerce_market_phase
from app.opip.early.timeframe_policy import compare_timeframe_policies
from app.opip.early.timing_ledger import (
    MILESTONE_FIRST_OPERATOR_ALERT,
    EpisodeTimingLedger,
    ledger_from_dict,
    observe_phase,
    record_card_created,
    record_card_edited,
    record_milestone,
    record_notification_delivered,
    resolve_episode_ledger,
)

logger = logging.getLogger(__name__)

SHADOW_OBSERVER_VERSION = "opip-early-shadow-observer-v1"

RECORD_SELECTOR_COMPARISON = "EARLY_SELECTOR_SHADOW_COMPARISON"
RECORD_TIMEFRAME_COMPARISON = "EARLY_TIMEFRAME_SHADOW_COMPARISON"
RECORD_TIMING_MILESTONE = "EARLY_TIMING_MILESTONE"
RECORD_CARD_DELIVERY = "EARLY_CARD_DELIVERY"

#: Where the full-universe per-symbol history already lives. Read-only here.
OBSERVATION_STATE_FILE = Path("/app/data/full_market_observation_state.json")
#: Bounded ledger state for milestone monotonicity across scans.
TIMING_LEDGER_STATE_FILE = Path("/app/data/opip/qualification/early_timing_ledger_state.json")
#: Ledger entries untouched for longer than this are dropped, so state stays
#: bounded without ever rewriting a persisted evidence row.
LEDGER_STATE_RETENTION_SECONDS = 14 * 24 * 3600.0


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat() if moment is not None else None


def load_observation_history(
    path: Path | None = None,
    *,
    history_scans: int = 8,
) -> dict[str, list[Mapping[str, Any]]]:
    """Read the existing full-universe scan history as plain mappings.

    Reading the raw state rather than importing
    ``full_market_observation.load_history_state`` keeps this module free of a
    dependency on the scanner's runtime types and makes it trivially testable
    with a fixture file. A missing or malformed file yields an empty history,
    which simply means no delta feature is available yet.
    """
    target = Path(path or OBSERVATION_STATE_FILE)
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(state, dict):
        return {}
    retain = max(2, int(history_scans))
    raw = state.get("history_by_symbol")
    history: dict[str, list[Mapping[str, Any]]] = {}
    if isinstance(raw, dict):
        for key, rows in raw.items():
            if not isinstance(rows, list):
                continue
            parsed = [row for row in rows if isinstance(row, dict)]
            parsed.sort(key=lambda row: str(row.get("observed_at") or ""))
            if parsed:
                history[str(key).upper()] = parsed[-retain:]
        return history

    latest = state.get("latest_by_symbol")
    if isinstance(latest, dict):
        for key, row in latest.items():
            if isinstance(row, dict):
                history[str(key).upper()] = [row]
    return history


def _pct_change(current: Any, previous: Any) -> float | None:
    now = _finite_optional(current)
    before = _finite_optional(previous)
    if now is None or before is None or before <= 0:
        return None
    return (now / before - 1.0) * 100.0


def derive_delta_features(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, float | None]:
    """Derive change/delta features from consecutive prior observations.

    Returns ``None`` for anything the history cannot support. A first sighting
    yields all-``None``, which the cohort selector treats as non-admission
    rather than as a zero.

    ``rolling_24h_volume_change`` is the scan-to-scan change in Kraken's
    rolling 24h volume field. It is deliberately *not* named
    ``relative_volume_change``: that would claim interval relative-volume
    acceleration the history does not contain. ``trade_count_acceleration``
    stays ``None`` until a genuine source supplies it.
    """
    empty: dict[str, float | None] = {
        "relative_volume_change": None,
        "rolling_24h_volume_change": None,
        "trade_count_acceleration": None,
        "momentum_acceleration": None,
        "distance_to_high_velocity_pct": None,
        "base_displacement_velocity_pct": None,
        "prior_observation_count": None,
    }
    usable = [row for row in rows if isinstance(row, Mapping)]
    empty["prior_observation_count"] = float(max(0, len(usable) - 1))
    if len(usable) < 2:
        return empty

    latest = usable[-1]
    prior = usable[-2]
    volume_ratio = _pct_change(latest.get("volume_24h"), prior.get("volume_24h"))
    latest_lift = _finite_optional(latest.get("lift_from_24h_low_pct"))
    prior_lift = _finite_optional(prior.get("lift_from_24h_low_pct"))
    latest_distance = _finite_optional(latest.get("distance_from_24h_high_pct"))
    prior_distance = _finite_optional(prior.get("distance_from_24h_high_pct"))

    features: dict[str, float | None] = dict(empty)
    features["rolling_24h_volume_change"] = (
        volume_ratio / 100.0 if volume_ratio is not None else None
    )
    if latest_lift is not None and prior_lift is not None:
        features["base_displacement_velocity_pct"] = latest_lift - prior_lift
    if latest_distance is not None and prior_distance is not None:
        features["distance_to_high_velocity_pct"] = prior_distance - latest_distance

    # Acceleration needs three observations: the change in per-interval return.
    if len(usable) >= 3:
        recent = _pct_change(latest.get("last_price"), prior.get("last_price"))
        earlier = _pct_change(prior.get("last_price"), usable[-3].get("last_price"))
        if recent is not None and earlier is not None:
            features["momentum_acceleration"] = recent - earlier
    return features


def _history_rows_for(
    history: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    base_asset: str,
    primary_pair: str,
) -> Sequence[Mapping[str, Any]]:
    """Look up history by pair symbol first, then by base asset."""
    for key in (primary_pair.upper(), base_asset.upper(), f"{base_asset.upper()}USD"):
        rows = history.get(key)
        if rows:
            return rows
    return ()


def candidate_features_from_movers(
    movers: Iterable[Any],
    *,
    history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
) -> list[EarlyCandidateFeatures]:
    """Build challenger input from coarse movers plus existing history.

    ``movers`` are ``CoarseMover`` instances, read by attribute so this module
    never imports the discovery module and cannot create a cycle.
    """
    resolved_history = dict(history or {})
    rows: list[EarlyCandidateFeatures] = []
    for mover in movers:
        base = str(getattr(mover, "base_asset", "") or "").upper()
        pair = str(getattr(mover, "primary_pair", "") or base).upper()
        deltas = derive_delta_features(
            _history_rows_for(resolved_history, base_asset=base, primary_pair=pair)
        )
        assert_point_in_time_safe(deltas)
        rows.append(
            EarlyCandidateFeatures(
                identifier=pair or base,
                base_asset=base,
                lift_from_24h_low_pct=_finite_optional(
                    getattr(mover, "lift_from_24h_low_pct", None)
                )
                or 0.0,
                distance_from_24h_high_pct=_finite_optional(
                    getattr(mover, "distance_from_24h_high_pct", None)
                )
                or 0.0,
                notional_usd=_finite_optional(getattr(mover, "notional_24h_usd_approx", None))
                or 0.0,
                relative_volume_change=deltas["relative_volume_change"],
                rolling_24h_volume_change=deltas["rolling_24h_volume_change"],
                trade_count_acceleration=deltas["trade_count_acceleration"],
                momentum_acceleration=deltas["momentum_acceleration"],
                distance_to_high_velocity_pct=deltas["distance_to_high_velocity_pct"],
                base_displacement_velocity_pct=deltas["base_displacement_velocity_pct"],
                production_coarse_score=_finite_optional(getattr(mover, "coarse_score", None)),
            )
        )
    return rows


def observe_selector_shadow(
    *,
    all_movers: Sequence[Any],
    production_selection: Sequence[Any],
    universe_count: int,
    scan_id: str | None,
    decision_at: datetime,
    history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    total_candidates: int | None = None,
) -> dict[str, Any]:
    """Run the challenger selector beside production and return one row.

    ``all_movers`` must be every coarse candidate that passed the coarse
    predicates, including the rank-limited tail; that tail is exactly where an
    igniting asset was previously lost.
    """
    rows = candidate_features_from_movers(all_movers, history=history)
    budget = int(total_candidates if total_candidates is not None else len(production_selection))
    selection = select_early_candidates(
        rows,
        total_candidates=budget or len(production_selection),
        universe_count=universe_count,
    )
    comparison = compare_with_production(
        production_identifiers=[
            str(getattr(item, "primary_pair", "") or "") for item in production_selection
        ],
        challenger=selection,
    )
    return {
        "record_type": RECORD_SELECTOR_COMPARISON,
        "observer_version": SHADOW_OBSERVER_VERSION,
        "scan_id": scan_id,
        "decision_at": _iso(decision_at),
        "universe_count": int(universe_count),
        "coarse_candidate_count": len(rows),
        "challenger": selection.as_dict(),
        "comparison": comparison,
        "shadow_only": True,
        "production_selection_changed": False,
        "trade_authority_changed": False,
    }


def observe_timeframe_shadow(
    *,
    signals: Sequence[Any],
    scan_id: str | None,
    decision_at: datetime,
) -> list[dict[str, Any]]:
    """Compare production and candidate timeframe policies per signal.

    Root cause D: production selects fine candles only under compression, so
    ignition — which raises bandwidth and ATR — can switch the fine timeframe
    off exactly when lead time matters. Each row records whether that
    inversion occurred for this candidate.
    """
    rows: list[dict[str, Any]] = []
    for signal in signals:
        phase = coerce_market_phase(getattr(signal, "market_phase", None), MarketPhase.DORMANT)
        comparison = compare_timeframe_policies(
            symbol=str(getattr(signal, "symbol", "") or ""),
            bandwidth_percentile=getattr(signal, "bollinger_bandwidth_percentile", None),
            atr_percentile=getattr(signal, "atr_percentile", None),
            phase=phase,
        )
        payload = comparison.as_dict()
        payload.update(
            {
                "record_type": RECORD_TIMEFRAME_COMPARISON,
                "observer_version": SHADOW_OBSERVER_VERSION,
                "scan_id": scan_id,
                "decision_at": _iso(decision_at),
                "production_detection_timeframe": str(
                    getattr(signal, "detection_timeframe", "1H") or "1H"
                ),
            }
        )
        rows.append(payload)
    return rows


def _latest_milestone_moment(row: Mapping[str, Any]) -> datetime | None:
    """Newest recorded milestone or card-lifecycle stamp, for retention."""
    stamps: list[Any] = []
    milestones = row.get("milestones")
    if isinstance(milestones, Mapping):
        stamps.extend(dict(milestones).values())
    for key in ("card_created_at", "card_edited_at", "notification_delivered_at"):
        value = row.get(key)
        if value:
            stamps.append(value)
    parsed: list[datetime] = []
    for stamp in stamps:
        moment = parse_timestamp(stamp)
        if moment is not None:
            parsed.append(moment)
    return max(parsed) if parsed else None


def _load_ledger_state(path: Path | None = None) -> dict[str, Any]:
    target = Path(path or TIMING_LEDGER_STATE_FILE)
    try:
        state = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_ledger_state(
    state: Mapping[str, Any],
    *,
    path: Path | None = None,
    now: datetime,
) -> None:
    target = Path(path or TIMING_LEDGER_STATE_FILE)
    retained: dict[str, Any] = {}
    index = state.get("_symbol_episode_index")
    live_episode_ids: set[str] = set()
    for key, row in state.items():
        if key == "_symbol_episode_index":
            continue
        if not isinstance(row, Mapping):
            continue
        seen = _latest_milestone_moment(row)
        if seen is None or (now - seen).total_seconds() <= LEDGER_STATE_RETENTION_SECONDS:
            retained[str(key)] = dict(row)
            live_episode_ids.add(str(key))
    if isinstance(index, Mapping):
        retained["_symbol_episode_index"] = {
            str(symbol): str(episode_id)
            for symbol, episode_id in index.items()
            if str(episode_id) in live_episode_ids
        }
    tmp = target.with_suffix(target.suffix + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(retained, sort_keys=True), encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning(
            "O'Pip early timing ledger state write failed open path=%s error=%s",
            target,
            type(exc).__name__,
        )
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning(
                "O'Pip early timing ledger temp cleanup failed open path=%s error=%s",
                tmp,
                type(cleanup_exc).__name__,
            )


def observe_timing_milestones(
    *,
    signals: Sequence[Any],
    scan_id: str | None,
    decision_at: datetime,
    state_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Advance the monotonic milestone ledger for each signal.

    Only ``card_created_at`` is knowable here. Delivery is recorded separately
    by the transport, because the alert governor may edit an existing card
    without ever notifying the operator, and a lead-time claim built on card
    creation would be wrong.
    """
    state = _load_ledger_state(state_path)
    # Symbol -> current episode_id index, kept alongside ledgers keyed by episode.
    index = state.get("_symbol_episode_index")
    if not isinstance(index, dict):
        index = {}
    rows: list[dict[str, Any]] = []
    for signal in signals:
        symbol = str(getattr(signal, "symbol", "") or "")
        if not symbol:
            continue
        key = symbol.upper()
        phase = getattr(signal, "market_phase", None)
        current_episode_id = str(index.get(key) or "")
        existing = (
            ledger_from_dict(state[current_episode_id])
            if current_episode_id and isinstance(state.get(current_episode_id), Mapping)
            else None
        )
        # Legacy rows keyed by symbol alone are migrated into a real episode id
        # on first touch so two separate moves cannot keep sharing them.
        if existing is None and isinstance(state.get(key), Mapping):
            legacy = ledger_from_dict(state[key])
            if legacy.episode_id == key or not legacy.episode_id:
                existing = legacy
        ledger = resolve_episode_ledger(
            symbol=key,
            decision_at=decision_at,
            phase=phase,
            existing=existing,
        )
        reference_price = _finite_optional(getattr(signal, "reference_price", None))
        ledger = observe_phase(
            ledger,
            phase=phase,
            grade=getattr(signal, "evidence_grade", None),
            observed_at=decision_at,
            reference_price=reference_price,
            actionable=not bool(getattr(signal, "actionability_reasons", ()) or ()),
        )
        if bool(getattr(signal, "alert_eligible", False)):
            ledger = record_milestone(
                ledger,
                milestone=MILESTONE_FIRST_OPERATOR_ALERT,
                observed_at=decision_at,
                anchor_price=reference_price,
            )
        payload = ledger.as_dict()
        payload.update(
            {
                "record_type": RECORD_TIMING_MILESTONE,
                "observer_version": SHADOW_OBSERVER_VERSION,
                "scan_id": scan_id,
                "decision_at": _iso(decision_at),
            }
        )
        state[ledger.episode_id] = ledger.as_dict()
        index[key] = ledger.episode_id
        # Drop the legacy symbol-keyed row once migrated.
        if key in state and key != ledger.episode_id:
            state.pop(key, None)
        rows.append(payload)
    state["_symbol_episode_index"] = index
    _save_ledger_state(state, path=state_path, now=decision_at)
    return rows


#: Governor outcomes that genuinely notified the operator. ``EDITED`` is
#: deliberately absent: an in-place edit may be silent, so counting it as
#: delivery is exactly the mistake that would inflate a lead-time claim.
DELIVERED_ACTIONS = frozenset({"CREATED", "TRANSITION_PUSHED"})
#: Governor outcomes that produced a card without notifying anyone.
EDITED_ACTIONS = frozenset({"EDITED"})


def record_card_delivery_outcomes(
    delivery_by_symbol: Mapping[str, tuple[str, bool]],
    *,
    anchor_prices: Mapping[str, float] | None = None,
    decision_at: datetime | None = None,
    state_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Separate card creation, silent edits and real operator notification.

    ``delivery_by_symbol`` is the alert governor's own outcome map, so this
    reads the transport result rather than assuming a card reached anyone.
    Dark by default and never consulted by any gate.
    """
    if not early_timing_ledger_enabled(environ) or not delivery_by_symbol:
        return []
    try:
        return _record_card_delivery_outcomes(
            delivery_by_symbol,
            anchor_prices=anchor_prices,
            decision_at=decision_at,
            state_path=state_path,
        )
    except Exception as exc:  # pragma: no cover - measurement must fail soft
        logger.warning(
            "O'Pip early card delivery capture failed open error=%s",
            type(exc).__name__,
        )
        return []


def _record_card_delivery_outcomes(
    delivery_by_symbol: Mapping[str, tuple[str, bool]],
    *,
    anchor_prices: Mapping[str, float] | None,
    decision_at: datetime | None,
    state_path: Path | None,
) -> list[dict[str, Any]]:
    moment = decision_at or datetime.now(timezone.utc)
    prices = dict(anchor_prices or {})
    state = _load_ledger_state(state_path)
    index = state.get("_symbol_episode_index")
    if not isinstance(index, dict):
        index = {}
    rows: list[dict[str, Any]] = []
    for symbol, outcome in delivery_by_symbol.items():
        key = str(symbol).upper()
        action = str(outcome[0]) if isinstance(outcome, (tuple, list)) and outcome else ""
        episode_id = str(index.get(key) or "")
        existing_row = state.get(episode_id) if episode_id else state.get(key)
        existing = ledger_from_dict(existing_row) if isinstance(existing_row, Mapping) else None
        ledger = resolve_episode_ledger(
            symbol=key, decision_at=moment, existing=existing
        )
        if action in DELIVERED_ACTIONS:
            ledger = record_card_created(ledger, created_at=moment)
            ledger = record_notification_delivered(
                ledger, delivered_at=moment, anchor_price=prices.get(key)
            )
        elif action in EDITED_ACTIONS:
            ledger = record_card_edited(ledger, edited_at=moment)
        else:
            continue
        payload = ledger.as_dict()
        payload.update(
            {
                "record_type": RECORD_CARD_DELIVERY,
                "observer_version": SHADOW_OBSERVER_VERSION,
                "decision_at": _iso(moment),
                "governor_action": action,
            }
        )
        state[ledger.episode_id] = ledger.as_dict()
        index[key] = ledger.episode_id
        if key in state and key != ledger.episode_id:
            state.pop(key, None)
        rows.append(payload)
    state["_symbol_episode_index"] = index
    _save_ledger_state(state, path=state_path, now=moment)
    return rows


def persist_shadow_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    path: Path | None = None,
    enabled: bool | None = None,
) -> int:
    """Append shadow evidence to the existing qualification plane."""
    if not rows:
        return 0
    try:
        return append_early_timing_milestones(rows, path=path, enabled=enabled)
    except Exception as exc:  # pragma: no cover - measurement must fail soft
        logger.warning(
            "O'Pip early shadow persistence failed open rows=%d error=%s",
            len(rows),
            type(exc).__name__,
        )
        return 0


def observe_scan_shadow(
    *,
    all_movers: Sequence[Any],
    production_selection: Sequence[Any],
    signals: Sequence[Any],
    universe_count: int,
    scan_id: str | None = None,
    decision_at: datetime | None = None,
    history: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    environ: Mapping[str, str] | None = None,
    observation_state_path: Path | None = None,
    ledger_state_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Build every enabled shadow row for one scan.

    Each phase is independently flagged and every flag is dark by default, so
    an unflagged deployment does exactly what it did before and pays no cost.
    """
    moment = decision_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []

    if early_selector_shadow_enabled(environ):
        resolved_history = (
            history
            if history is not None
            else load_observation_history(observation_state_path)
        )
        rows.append(
            observe_selector_shadow(
                all_movers=all_movers,
                production_selection=production_selection,
                universe_count=universe_count,
                scan_id=scan_id,
                decision_at=moment,
                history=resolved_history,
            )
        )

    if early_timeframe_shadow_enabled(environ):
        rows.extend(
            observe_timeframe_shadow(signals=signals, scan_id=scan_id, decision_at=moment)
        )

    if early_timing_ledger_enabled(environ):
        rows.extend(
            observe_timing_milestones(
                signals=signals,
                scan_id=scan_id,
                decision_at=moment,
                state_path=ledger_state_path,
            )
        )
    return rows
