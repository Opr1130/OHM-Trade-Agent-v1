"""Prospective opportunity evidence (ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1, P1).

Records what was known *at decision time* for a finalist, so a future calibration
study can join point-in-time features to matured outcomes without re-deriving
anything. This module is strictly non-authoritative:

* it cannot admit, notify, size, reserve or trade;
* it never receives an outcome - outcome linkage is by identifier only, and the
  join happens later on the learning plane;
* it is append-only, idempotent and bounded.

No field here may be derived from the future. ``FORBIDDEN_OUTCOME_FIELDS`` is
asserted on every record so a later edit cannot quietly leak an outcome into the
live decision path.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Sequence

RECORD_TYPE = "OPPORTUNITY_EVIDENCE"
SCHEMA_VERSION = 1

#: Any of these appearing in a record means future data leaked into the live path.
FORBIDDEN_OUTCOME_FIELDS = (
    "mfe_pct",
    "mae_pct",
    "close_return_pct",
    "hit_target",
    "outcome",
    "outcome_status",
    "realised_pnl",
    "realized_pnl",
    "exit_price",
    "future_return",
)

DEFAULT_MAX_BYTES = 32 * 1024 * 1024


class OutcomeLeakageError(ValueError):
    """Raised when a record carries a field that belongs to the future."""


@dataclass(frozen=True)
class OpportunityEvidence:
    """Point-in-time snapshot of one finalist evaluation."""

    # identity
    symbol: str
    venue: str
    instrument: str
    observed_at_utc: str
    episode_id: str
    scan_id: str | None = None
    release_sha: str | None = None
    policy_version: str | None = None
    strategy_version: str | None = None

    # detection features (decision-time only)
    direction: str | None = None
    technical_score: float | None = None
    precursor_score: float | None = None
    precursor_watch: bool | None = None
    phase: str | None = None
    market_regime: str | None = None

    # liquidity eligibility and verified depth
    depth_status: str | None = None
    weaker_side_depth_usd: float | None = None
    bid_depth_usd: float | None = None
    ask_depth_usd: float | None = None
    depth_band_pct: float | None = None
    depth_evidence_complete: bool | None = None
    depth_age_seconds: float | None = None
    depth_levels_returned: int | None = None
    depth_source: str | None = None
    monitoring_eligible: bool | None = None
    actionable_depth_eligible: bool | None = None

    # execution feasibility at the configured order size
    order_notional_usd: float | None = None
    spread_bps: float | None = None
    size_slippage_pct: float | None = None
    buy_fully_covered: bool | None = None
    sell_fully_covered: bool | None = None

    # economic feasibility
    economics_available: bool | None = None
    net_reward_to_risk: float | None = None
    round_trip_cost_pct: float | None = None

    # confidence provenance
    calibration_status: str | None = None
    calibrated_probability: float | None = None
    calibration_model_version: str | None = None

    # risk and disposition
    risk_gate_passed: bool | None = None
    protection_admissions_suspended: bool | None = None
    admission_decision: str | None = None
    suppression_reason: str | None = None
    rejection_codes: tuple[str, ...] = field(default_factory=tuple)

    # forward linkage (identifiers only - never an outcome)
    trade_id: str | None = None
    journey_id: str | None = None
    signal_id: str | None = None


def idempotency_key(evidence: OpportunityEvidence) -> str:
    """Stable identity for one evaluation of one episode at one instant."""
    raw = "|".join(
        (
            str(evidence.venue).strip().upper(),
            str(evidence.instrument).strip().upper(),
            str(evidence.episode_id).strip(),
            str(evidence.observed_at_utc).strip(),
            str(evidence.policy_version or "").strip(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def assert_no_leakage(payload: dict[str, Any]) -> None:
    """Raise if any future-derived field is present in a record."""
    leaked = [key for key in FORBIDDEN_OUTCOME_FIELDS if key in payload]
    if leaked:
        raise OutcomeLeakageError(
            "record carries future-derived fields: " + ", ".join(leaked)
        )


def build_record(evidence: OpportunityEvidence) -> dict[str, Any]:
    """Serialise one record, refusing any future-derived field."""
    payload = asdict(evidence)
    payload["rejection_codes"] = list(evidence.rejection_codes)
    payload["record_type"] = RECORD_TYPE
    payload["schema_version"] = SCHEMA_VERSION
    payload["idempotency_key"] = idempotency_key(evidence)
    assert_no_leakage(payload)
    return payload


def _bounded_rows(path: Path, *, max_bytes: int) -> tuple[list[str], int]:
    """Read the tail of an append-only file, never the whole history."""
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if size <= max_bytes:
        return path.read_text(encoding="utf-8").splitlines(), size
    with path.open("rb") as handle:
        handle.seek(max(0, size - max_bytes))
        tail = handle.read().decode("utf-8", errors="ignore")
    lines = tail.splitlines()
    if lines and not tail.startswith("\n"):
        lines = lines[1:]  # drop the partial first row
    return lines, size


def append_record(
    path: Path,
    evidence: OpportunityEvidence,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> bool:
    """Append one record once. Returns True when a new row was written.

    Idempotent on ``idempotency_key`` within the bounded tail, so a retried scan
    cannot duplicate evidence. Retention is enforced by refusing to grow past
    ``max_bytes`` rather than by rewriting history.
    """
    record = build_record(evidence)
    key = record["idempotency_key"]
    rows, size = _bounded_rows(path, max_bytes=max_bytes)
    for row in rows:
        if not row.strip():
            continue
        try:
            if json.loads(row).get("idempotency_key") == key:
                return False
        except (ValueError, AttributeError):
            continue
    if size > max_bytes:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
    return True


def read_records(path: Path, *, limit: int = 1000) -> list[dict[str, Any]]:
    """Read the most recent ``limit`` records. Read-only."""
    rows, _ = _bounded_rows(path, max_bytes=DEFAULT_MAX_BYTES)
    parsed: list[dict[str, Any]] = []
    for row in rows[-max(0, int(limit)):]:
        if not row.strip():
            continue
        try:
            parsed.append(json.loads(row))
        except ValueError:
            continue
    return parsed


def required_fields() -> tuple[str, ...]:
    """Fields a valid evidence record must carry."""
    return (
        "symbol", "venue", "instrument", "observed_at_utc", "episode_id",
        "depth_status", "weaker_side_depth_usd", "order_notional_usd",
        "admission_decision",
    )


def missing_required(record: dict[str, Any]) -> Sequence[str]:
    return tuple(name for name in required_fields() if record.get(name) is None)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "FORBIDDEN_OUTCOME_FIELDS",
    "OpportunityEvidence",
    "OutcomeLeakageError",
    "RECORD_TYPE",
    "SCHEMA_VERSION",
    "append_record",
    "build_record",
    "idempotency_key",
    "missing_required",
    "read_records",
    "required_fields",
]

