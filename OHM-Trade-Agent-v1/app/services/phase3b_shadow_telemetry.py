"""Production shadow telemetry for Phase 3B structure + chase-risk intelligence.

This module observes already-scored Signal Quality candidates and writes an
append-only research stream. It cannot rank, suppress, promote, alert, place,
confirm, cancel, or modify any trade. When the existing live scan supplies a
completed-OHLC structure sample, the same point-in-time context is recorded and
may enrich chase-risk measurement; missing structure remains explicit rather
than being inferred from 24h ticker fields.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.chase_risk import ChaseRiskInput, assess_chase_risk
from app.services.registry_io import registry_lock

SCHEMA_VERSION = 2
DEFAULT_SHADOW_FILE = Path("/app/data/phase3b_shadow_telemetry.jsonl")


@dataclass(frozen=True)
class Phase3BShadowRecord:
    schema_version: int
    recorded_at: str
    symbol: str
    reference_price: float | None
    signal_stage: str
    suppressed: bool
    liquidity_24h_usd_approx: float
    opportunity_score: int
    persistence_scans: int
    exhaustion_penalty: int
    near_high_component: float | None
    inferred_distance_from_24h_high_pct: float | None
    window_run_up_pct: float | None
    chase_risk_score: int
    chase_risk_band: str
    late_entry: bool
    retest_available: bool
    chase_reasons: tuple[str, ...]
    structure_status: str
    structure_pair: str | None
    structure_interval_minutes: int | None
    structure_completed_bars: int
    structure_latest_completed_at: str | None
    structure_bias: str | None
    bullish_break_level: float | None
    bearish_break_level: float | None
    breakout_level_used_for_chase: float | None
    last_swing_high: float | None
    last_swing_low: float | None
    change_of_character: bool
    imbalance_zone_low: float | None
    imbalance_zone_high: float | None
    liquidity_sweep: str | None
    retest_state: str | None
    distance_from_breakout_pct: float | None
    structure_reasons: tuple[str, ...]
    structure_error_type: str | None
    measurement_only: bool = True
    advisory_only: bool = True
    affects_ranking: bool = False
    affects_telegram: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["chase_reasons"] = list(self.chase_reasons)
        payload["structure_reasons"] = list(self.structure_reasons)
        return payload


def _finite_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _inferred_distance_from_near_high_component(candidate: Any) -> float | None:
    """Invert Phase-1's reviewed linear near-high component when informative.

    ``near_high_component`` is 100 at the high and declines linearly to zero at
    8%. A zero score is deliberately treated as unknown (it means >=8%, not an
    exact distance). This is an explicit lineage-preserving inference, not a
    new market-data fetch.
    """
    components = getattr(candidate, "components", {}) or {}
    near_high = _finite_float(components.get("near_high"))
    if near_high is None or near_high <= 0.0:
        return None
    bounded = max(0.0, min(100.0, near_high))
    return (100.0 - bounded) / 12.5


def _structure_fields(symbol: str, structure_samples: Mapping[str, Any] | None) -> dict[str, Any]:
    sample = (structure_samples or {}).get(symbol)
    if sample is None:
        return {
            "structure_status": "UNAVAILABLE_NO_COMPLETED_OHLC_HISTORY",
            "structure_pair": None,
            "structure_interval_minutes": None,
            "structure_completed_bars": 0,
            "structure_latest_completed_at": None,
            "structure_bias": None,
            "bullish_break_level": None,
            "bearish_break_level": None,
            "breakout_level_used_for_chase": None,
            "last_swing_high": None,
            "last_swing_low": None,
            "change_of_character": False,
            "imbalance_zone_low": None,
            "imbalance_zone_high": None,
            "liquidity_sweep": None,
            "retest_state": None,
            "distance_from_breakout_pct": None,
            "structure_reasons": (),
            "structure_error_type": None,
        }

    context = getattr(sample, "context", None)
    bias = getattr(context, "bias", None) if context is not None else None
    bullish_break = _finite_float(getattr(context, "bullish_break_level", None)) if context else None
    bearish_break = _finite_float(getattr(context, "bearish_break_level", None)) if context else None
    # Spot-only advisory model: bearish structure is retained as risk/context,
    # but a bearish break must never be interpreted as a short entry geometry.
    breakout_for_chase = bullish_break if bias == "BULLISH" else None

    latest_completed = getattr(sample, "latest_completed_at", None)
    return {
        "structure_status": str(getattr(sample, "status", "UNAVAILABLE") or "UNAVAILABLE"),
        "structure_pair": getattr(sample, "kraken_pair", None),
        "structure_interval_minutes": int(getattr(sample, "interval_minutes", 0) or 0) or None,
        "structure_completed_bars": int(getattr(sample, "completed_bar_count", 0) or 0),
        "structure_latest_completed_at": latest_completed.astimezone(timezone.utc).isoformat() if latest_completed else None,
        "structure_bias": bias,
        "bullish_break_level": bullish_break,
        "bearish_break_level": bearish_break,
        "breakout_level_used_for_chase": breakout_for_chase,
        "last_swing_high": _finite_float(getattr(context, "last_swing_high", None)) if context else None,
        "last_swing_low": _finite_float(getattr(context, "last_swing_low", None)) if context else None,
        "change_of_character": bool(getattr(context, "change_of_character", False)) if context else False,
        "imbalance_zone_low": _finite_float(getattr(context, "imbalance_zone_low", None)) if context else None,
        "imbalance_zone_high": _finite_float(getattr(context, "imbalance_zone_high", None)) if context else None,
        "liquidity_sweep": getattr(context, "liquidity_sweep", None) if context else None,
        "retest_state": getattr(context, "retest_state", None) if context else None,
        "distance_from_breakout_pct": _finite_float(getattr(context, "distance_from_breakout_pct", None)) if context else None,
        "structure_reasons": tuple(getattr(context, "reasons", ()) or ()) if context else (),
        "structure_error_type": getattr(sample, "error_type", None),
    }


def build_phase3b_shadow_record(
    candidate: Any,
    *,
    reference_prices: Mapping[str, float] | None = None,
    structure_samples: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> Phase3BShadowRecord:
    now = now or datetime.now(timezone.utc)
    symbol = str(getattr(candidate, "symbol", "") or "").upper()
    price = _finite_float((reference_prices or {}).get(symbol))
    components = getattr(candidate, "components", {}) or {}
    near_high = _finite_float(components.get("near_high"))
    run_up = _finite_float(components.get("window_run_up_pct"))
    inferred_distance = _inferred_distance_from_near_high_component(candidate)
    structure = _structure_fields(symbol, structure_samples)
    bullish_structure = structure["structure_bias"] == "BULLISH"

    assessment = assess_chase_risk(
        ChaseRiskInput(
            current_price=price or 0.0,
            breakout_level=structure["breakout_level_used_for_chase"],
            recent_high=structure["last_swing_high"],
            distance_from_24h_high_pct=inferred_distance,
            persistence_scans=int(getattr(candidate, "persistence_scans", 0) or 0),
            exhaustion_penalty=int(getattr(candidate, "exhaustion_penalty", 0) or 0),
            retest_state=structure["retest_state"] if bullish_structure else None,
            # Deliberately absent: no Phase-3A future-derived
            # move-completed value is allowed into this point-in-time score.
        )
    )

    return Phase3BShadowRecord(
        schema_version=SCHEMA_VERSION,
        recorded_at=now.astimezone(timezone.utc).isoformat(),
        symbol=symbol,
        reference_price=price,
        signal_stage=str(getattr(candidate, "stage", "") or ""),
        suppressed=bool(getattr(candidate, "suppressed", False)),
        liquidity_24h_usd_approx=float(getattr(candidate, "liquidity_24h_usd_approx", 0.0) or 0.0),
        opportunity_score=int(getattr(candidate, "opportunity_score", 0) or 0),
        persistence_scans=int(getattr(candidate, "persistence_scans", 0) or 0),
        exhaustion_penalty=int(getattr(candidate, "exhaustion_penalty", 0) or 0),
        near_high_component=near_high,
        inferred_distance_from_24h_high_pct=inferred_distance,
        window_run_up_pct=run_up,
        chase_risk_score=assessment.score,
        chase_risk_band=assessment.band,
        late_entry=assessment.late_entry,
        retest_available=assessment.retest_available,
        chase_reasons=assessment.reasons,
        **structure,
    )


def record_phase3b_shadow_telemetry(
    candidates: Iterable[Any],
    *,
    reference_prices: Mapping[str, float] | None = None,
    structure_samples: Mapping[str, Any] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Fail-soft append-only capture for all scored candidates, suppressed too."""
    try:
        rows = list(candidates)
        if not rows:
            return 0
        target = path or DEFAULT_SHADOW_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f".{target.name}.lock"
        written = 0
        with registry_lock(lock):
            with target.open("a", encoding="utf-8") as handle:
                for candidate in rows:
                    record = build_phase3b_shadow_record(
                        candidate,
                        reference_prices=reference_prices,
                        structure_samples=structure_samples,
                        now=now,
                    )
                    handle.write(json.dumps(record.as_dict(), sort_keys=True, allow_nan=False) + "\n")
                    written += 1
                handle.flush()
        return written
    except Exception:
        return 0
