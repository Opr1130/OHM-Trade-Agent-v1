"""Forward decision telemetry for Signal Quality v1 (Phase 3A).

Phase 2 reconstructs what OHM *would* have decided from the event-sampled
JSONL stream. That reconstruction is an approximation - it carries values
forward across gaps, and it cannot see anything the live process didn't
persist. This module exists to remove that approximation going forward: it
records the *actual* live decision state, at the moment the live scan
produced it, so future analysis has ground truth instead of only replay.

Phase 3B shadow telemetry is deliberately separated from Phase 3A recording.
The critical decision path can persist Phase 3A immediately without making any
Kraken OHLC request. A separate post-alert function performs bounded Phase 3B
completed-OHLC enrichment later, while retaining the immutable original
``decision_at`` so delayed HTTP responses cannot introduce future candles.

Both telemetry paths are fail-soft and one-directional. Nothing here can rank,
suppress, promote, alert, place, confirm, cancel, or modify a trade.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from app.services.phase3b_live_structure import collect_phase3b_live_structure
from app.services.phase3b_shadow_telemetry import record_phase3b_shadow_telemetry
from app.services.registry_io import registry_lock


SCHEMA_VERSION = 1
DEFAULT_TELEMETRY_FILE = Path("/app/data/decision_telemetry.jsonl")


@dataclass(frozen=True)
class DecisionTelemetryRecord:
    """One row needed to reconstruct what OHM knew at one live decision."""

    schema_version: int
    recorded_at: str
    scan_source: str
    symbol: str
    price: float | None
    liquidity_24h_usd_approx: float
    stage: str
    pattern: str | None
    opportunity_score: int
    explosion_potential_score: int
    tradeability_score: int
    pattern_strength_score: int
    volume_acceleration_score: int
    relative_strength_score: int
    persistence_scans: int
    exhaustion_penalty: int
    exhaustion_band: str
    relative_strength_percentile: float
    universe_size: int
    reasons: tuple[str, ...]
    suppressed: bool
    signal_quality_enabled: bool
    early_alerts_enabled: bool
    advisory_only: bool = True
    weights_are_calibrated: bool = False
    trade_authority_changed: bool = False
    production_execution_gate_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["reasons"] = list(self.reasons)
        return payload


def build_telemetry_record(
    candidate: Any,
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    now: datetime | None = None,
) -> DecisionTelemetryRecord:
    now = now or datetime.now(timezone.utc)
    symbol = str(getattr(candidate, "symbol", "") or "").upper()
    price = (reference_prices or {}).get(symbol)
    if price is None:
        price = getattr(candidate, "reference_price", None)
    return DecisionTelemetryRecord(
        schema_version=SCHEMA_VERSION,
        recorded_at=now.astimezone(timezone.utc).isoformat(),
        scan_source="LIVE",
        symbol=symbol,
        price=(float(price) if price is not None else None),
        liquidity_24h_usd_approx=float(
            getattr(candidate, "liquidity_24h_usd_approx", 0.0) or 0.0
        ),
        stage=str(getattr(candidate, "stage", "") or ""),
        pattern=getattr(candidate, "pattern", None),
        opportunity_score=int(getattr(candidate, "opportunity_score", 0) or 0),
        explosion_potential_score=int(
            getattr(candidate, "explosion_potential_score", 0) or 0
        ),
        tradeability_score=int(
            getattr(candidate, "tradeability_score", 0) or 0
        ),
        pattern_strength_score=int(
            getattr(candidate, "pattern_strength_score", 0) or 0
        ),
        volume_acceleration_score=int(
            getattr(candidate, "volume_acceleration_score", 0) or 0
        ),
        relative_strength_score=int(
            getattr(candidate, "relative_strength_score", 0) or 0
        ),
        persistence_scans=int(
            getattr(candidate, "persistence_scans", 0) or 0
        ),
        exhaustion_penalty=int(
            getattr(candidate, "exhaustion_penalty", 0) or 0
        ),
        exhaustion_band=str(
            getattr(candidate, "exhaustion_band", "") or ""
        ),
        relative_strength_percentile=float(
            getattr(candidate, "relative_strength_percentile", 0.0) or 0.0
        ),
        universe_size=int(getattr(candidate, "universe_size", 0) or 0),
        reasons=tuple(getattr(candidate, "reasons", ()) or ()),
        suppressed=bool(getattr(candidate, "suppressed", False)),
        signal_quality_enabled=bool(
            getattr(settings, "signal_quality_v1_enabled", False)
        ),
        early_alerts_enabled=bool(
            getattr(settings, "signal_quality_early_alerts_enabled", False)
        ),
    )


def record_phase3b_shadow_for_decision(
    candidates: Iterable[Any],
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    decision_at: datetime,
) -> int:
    """Post-alert Phase 3B capture using the immutable original decision time.

    This function is intended to run only after the alert-critical work for the
    scan has completed. It may perform bounded public Kraken OHLC reads, but all
    returned bars are filtered by ``decision_at`` in ``phase3b_live_structure``.
    The function is fully fail-soft and returns only a measurement count.
    """
    try:
        rows = list(candidates)
    except Exception:
        return 0
    if not rows or not bool(
        getattr(settings, "signal_quality_v1_enabled", False)
    ):
        return 0

    try:
        structure_samples = collect_phase3b_live_structure(
            rows,
            decision_at=decision_at,
        )
    except Exception as exc:
        print("Phase 3B OHLC enrichment: fail-soft", type(exc).__name__)
        structure_samples = {}

    failed = [
        sample
        for sample in structure_samples.values()
        if str(getattr(sample, "status", "")).startswith("UNAVAILABLE_")
    ]
    if failed:
        print(
            "Phase 3B OHLC unavailable:",
            len(failed),
            "of",
            len(structure_samples),
            "bounded candidates",
        )

    try:
        return record_phase3b_shadow_telemetry(
            rows,
            reference_prices=reference_prices,
            structure_samples=structure_samples,
            now=decision_at,
        )
    except Exception:
        return 0


def record_decision_telemetry(
    candidates: Iterable[Any],
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Capture Phase 3A live decision telemetry only.

    The Phase 3B OHLC path is intentionally not invoked here, so recording a
    decision cannot block mover detection or Telegram on external OHLC I/O.
    The return value remains the Phase 3A count for backward compatibility.
    """
    try:
        rows = list(candidates)
    except Exception:
        return 0

    if not bool(getattr(settings, "decision_telemetry_v1_enabled", False)):
        return 0
    try:
        if not rows:
            return 0
        target = path or DEFAULT_TELEMETRY_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        lock = target.parent / f".{target.name}.lock"
        written = 0
        with registry_lock(lock):
            with target.open("a", encoding="utf-8") as handle:
                for candidate in rows:
                    record = build_telemetry_record(
                        candidate,
                        settings=settings,
                        reference_prices=reference_prices,
                        now=now,
                    )
                    handle.write(
                        json.dumps(
                            record.as_dict(),
                            sort_keys=True,
                            default=str,
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    written += 1
                handle.flush()
        return written
    except Exception:
        return 0
