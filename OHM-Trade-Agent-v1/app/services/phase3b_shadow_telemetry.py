"""Production shadow telemetry for Phase 3B chase-risk intelligence.

This module observes already-scored Signal Quality candidates and writes an
append-only research stream. It cannot rank, suppress, promote, alert, place,
confirm, cancel, or modify any trade. Technical-structure fields are explicitly
UNAVAILABLE until the existing scan path supplies completed OHLC history.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.chase_risk import ChaseRiskInput, assess_chase_risk
from app.services.registry_io import registry_lock

SCHEMA_VERSION = 1
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
    structure_status: str = "UNAVAILABLE_NO_COMPLETED_OHLC_HISTORY"
    structure_bias: str | None = None
    breakout_level: float | None = None
    retest_state: str | None = None
    measurement_only: bool = True
    advisory_only: bool = True
    affects_ranking: bool = False
    affects_telegram: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["chase_reasons"] = list(self.chase_reasons)
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


def build_phase3b_shadow_record(
    candidate: Any,
    *,
    reference_prices: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> Phase3BShadowRecord:
    now = now or datetime.now(timezone.utc)
    symbol = str(getattr(candidate, "symbol", "") or "").upper()
    price = _finite_float((reference_prices or {}).get(symbol))
    components = getattr(candidate, "components", {}) or {}
    near_high = _finite_float(components.get("near_high"))
    run_up = _finite_float(components.get("window_run_up_pct"))
    inferred_distance = _inferred_distance_from_near_high_component(candidate)

    assessment = assess_chase_risk(
        ChaseRiskInput(
            current_price=price or 0.0,
            distance_from_24h_high_pct=inferred_distance,
            persistence_scans=int(getattr(candidate, "persistence_scans", 0) or 0),
            exhaustion_penalty=int(getattr(candidate, "exhaustion_penalty", 0) or 0),
            # Deliberately absent: no structural breakout/retest and no
            # Phase-3A future-derived move-completed value are available here.
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
    )


def record_phase3b_shadow_telemetry(
    candidates: Iterable[Any],
    *,
    reference_prices: Mapping[str, float] | None = None,
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
                        candidate, reference_prices=reference_prices, now=now
                    )
                    handle.write(json.dumps(record.as_dict(), sort_keys=True, allow_nan=False) + "\n")
                    written += 1
                handle.flush()
        return written
    except Exception:
        return 0
