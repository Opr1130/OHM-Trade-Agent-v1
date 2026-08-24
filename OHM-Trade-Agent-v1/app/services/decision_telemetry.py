"""Forward decision telemetry for Signal Quality v1 (Phase 3A).

Phase 2 reconstructs what OHM *would* have decided from the event-sampled
JSONL stream. That reconstruction is an approximation - it carries values
forward across gaps, and it cannot see anything the live process didn't
persist. This module exists to remove that approximation going forward: it
records the *actual* live decision state, at the moment the live scan
produced it, so future analysis has ground truth instead of only replay.

The same already-computed candidate stream also feeds Phase 3B shadow
telemetry. Phase 3B is measurement-only: its output never feeds scoring,
ranking, Telegram, PendingSetup, or execution. On the default live path a
bounded subset of already-ranked, non-suppressed candidates also receives
completed Kraken spot 15m OHLC context through ``phase3b_live_structure``.
That is an additional public-data read inside the existing scan cycle, not a
second scanner, and the still-forming Kraken candle is explicitly excluded.

Three properties make this safe:

* **Fail-soft.** Every failure mode - a permissions error, a full disk, a
  malformed candidate, or a Kraken OHLC error - is caught here or in the
  Phase 3B services. Telemetry can fail or be unavailable; it cannot change
  scoring or alerting.
* **One-directional.** Nothing in either telemetry module reads its JSONL back
  into a live decision.
* **No duplicate scanner.** Both telemetry streams consume candidates already
  produced by the existing scan_movers cycle. Phase 3B OHLC is a bounded
  enrichment pass for those candidates only.

Phase 3A's own JSONL remains dark behind ``DECISION_TELEMETRY_V1_ENABLED``.
Phase 3B shadow capture is active on the default live scan path whenever Signal
Quality has produced scored candidates, reflecting the separately approved
production measurement period. Callers that supply a custom Phase-3A path
(such as isolated unit tests/offline tools) do not implicitly create the live
Phase-3B shadow file or perform Kraken OHLC enrichment.
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
    """One row: everything needed to reconstruct what OHM knew about one
    symbol at one live scan, without depending on JSONL reconstruction.
    """

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
        liquidity_24h_usd_approx=float(getattr(candidate, "liquidity_24h_usd_approx", 0.0) or 0.0),
        stage=str(getattr(candidate, "stage", "") or ""),
        pattern=getattr(candidate, "pattern", None),
        opportunity_score=int(getattr(candidate, "opportunity_score", 0) or 0),
        explosion_potential_score=int(getattr(candidate, "explosion_potential_score", 0) or 0),
        tradeability_score=int(getattr(candidate, "tradeability_score", 0) or 0),
        pattern_strength_score=int(getattr(candidate, "pattern_strength_score", 0) or 0),
        volume_acceleration_score=int(getattr(candidate, "volume_acceleration_score", 0) or 0),
        relative_strength_score=int(getattr(candidate, "relative_strength_score", 0) or 0),
        persistence_scans=int(getattr(candidate, "persistence_scans", 0) or 0),
        exhaustion_penalty=int(getattr(candidate, "exhaustion_penalty", 0) or 0),
        exhaustion_band=str(getattr(candidate, "exhaustion_band", "") or ""),
        relative_strength_percentile=float(getattr(candidate, "relative_strength_percentile", 0.0) or 0.0),
        universe_size=int(getattr(candidate, "universe_size", 0) or 0),
        reasons=tuple(getattr(candidate, "reasons", ()) or ()),
        suppressed=bool(getattr(candidate, "suppressed", False)),
        signal_quality_enabled=bool(getattr(settings, "signal_quality_v1_enabled", False)),
        early_alerts_enabled=bool(getattr(settings, "signal_quality_early_alerts_enabled", False)),
    )


def record_decision_telemetry(
    candidates: Iterable[Any],
    *,
    settings: Any,
    reference_prices: Mapping[str, float] | None = None,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Capture Phase 3B shadow evidence and, when enabled, Phase 3A telemetry.

    Phase 3B shadow capture is independent of the Phase 3A decision-telemetry
    flag on the default live path, but only has rows when Signal Quality is
    enabled and produced candidates. Live completed-OHLC structure enrichment
    is bounded, fail-soft, and measurement-only. The return value remains the
    Phase 3A count for backward compatibility.
    """
    try:
        rows = list(candidates)
    except Exception:
        return 0

    if (
        rows
        and path is None
        and bool(getattr(settings, "signal_quality_v1_enabled", False))
    ):
        decision_at = now or datetime.now(timezone.utc)
        structure_samples = {}
        try:
            structure_samples = collect_phase3b_live_structure(
                rows,
                decision_at=decision_at,
            )
        except Exception:
            # Defence in depth: the collector is already fail-soft per symbol,
            # but its call site is not allowed to affect the scan either.
            structure_samples = {}

        # Separately fail-soft inside the writer. No return value is consumed by
        # live logic, so this cannot affect ranking, Telegram, or execution.
        record_phase3b_shadow_telemetry(
            rows,
            reference_prices=reference_prices,
            structure_samples=structure_samples,
            now=decision_at,
        )

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
                        json.dumps(record.as_dict(), sort_keys=True, default=str, allow_nan=False)
                        + "\n"
                    )
                    written += 1
                handle.flush()
        return written
    except Exception:
        return 0
