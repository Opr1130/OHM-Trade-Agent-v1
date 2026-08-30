"""Structured zero-trade and failure diagnostics for O'Pip Wave A2."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable, Mapping


_DEGRADED_PROVIDER_STATES = {
    "UNAVAILABLE",
    "RATE_LIMITED",
    "MISSING_CREDENTIALS",
    "DEGRADED",
    "STALE",
    "BACKOFF",
    "FAILED",
    "ERROR",
}


@dataclass(frozen=True)
class ZeroTradeDiagnostic:
    candidate_count: int
    rejected_count: int
    qualified_count: int
    unscored_count: int
    top_candidate_id: str | None
    top_pair: str | None
    binding_gate: str | None
    binding_reason_code: str | None
    nearest_miss_candidate_id: str | None
    nearest_miss_gate: str | None
    nearest_miss_distance: float | None
    event_or_risk_restriction: str | None
    degraded_providers: tuple[str, ...]
    operational_issues: tuple[str, ...]
    linkage_readiness: str | None
    measurement_only: bool = True
    affects_live_decisions: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return as dict."""
        row = asdict(self)
        row["degraded_providers"] = list(self.degraded_providers)
        row["operational_issues"] = list(self.operational_issues)
        return row


def _status(row: Mapping[str, Any]) -> str:
    """Return status."""
    return str(row.get("decision_status") or row.get("decision") or "").strip().upper()


def _rank_key(row: Mapping[str, Any]) -> tuple[float, float, str]:
    """Return rank key."""
    rank_raw = row.get("candidate_rank")
    try:
        rank = float(rank_raw)
    except (TypeError, ValueError):
        rank = math.inf
    if rank <= 0:
        rank = math.inf
    try:
        score = float(row.get("opportunity_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    candidate_id = str(row.get("candidate_id") or row.get("snapshot_id") or "")
    return rank, -score, candidate_id


def _nearest_miss(
    candidates: Iterable[Mapping[str, Any]],
) -> tuple[str | None, str | None, float | None]:
    """Return nearest miss."""
    best: tuple[float, str, str] | None = None
    for candidate in candidates:
        candidate_id = str(
            candidate.get("candidate_id") or candidate.get("snapshot_id") or ""
        )
        gate_rows = candidate.get("gate_results_ordered") or candidate.get("gate_results") or ()
        if not isinstance(gate_rows, (list, tuple)):
            continue
        for gate in gate_rows:
            if not isinstance(gate, Mapping):
                continue
            state = str(gate.get("status") or "").upper()
            if state not in {"FAIL", "FAILED", "REJECTED", "BLOCKED"}:
                continue
            raw = gate.get("threshold_distance")
            try:
                distance = abs(float(raw))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(distance):
                continue
            gate_name = str(gate.get("gate") or gate.get("name") or "")
            key = (distance, candidate_id, gate_name)
            if best is None or key < best:
                best = key
    if best is None:
        return None, None, None
    return best[1], best[2] or None, best[0]


def build_zero_trade_diagnostic(
    candidates: Iterable[Mapping[str, Any]],
    *,
    provider_health: Mapping[str, Any] | None = None,
    linkage_health: Mapping[str, Any] | None = None,
) -> ZeroTradeDiagnostic:
    """Return build zero trade diagnostic."""
    rows = tuple(candidates)
    qualified = [row for row in rows if _status(row) == "QUALIFIED"]
    rejected_states = {"REJECTED", "BLOCKED", "NOT_QUALIFIED", "DISQUALIFIED"}
    rejected = [row for row in rows if _status(row) in rejected_states]
    unscored = [
        row
        for row in rows
        if _status(row) not in rejected_states | {"QUALIFIED"}
    ]
    ordered = sorted(rows, key=_rank_key)
    top = ordered[0] if ordered else {}
    binding_gate = str(
        top.get("first_terminal_gate") or top.get("terminal_gate") or ""
    ) or None
    binding_reason = str(top.get("terminal_reason_code") or "") or None
    nearest_id, nearest_gate, nearest_distance = _nearest_miss(rows)

    restriction = None
    combined_reason = " ".join(
        filter(
            None,
            [
                str(binding_gate or ""),
                str(binding_reason or ""),
                str(top.get("terminal_reason_class") or ""),
                str(top.get("terminal_reason") or ""),
            ],
        )
    ).upper()
    if "EVENT" in combined_reason or "NEWS" in combined_reason or "CATALYST" in combined_reason:
        restriction = "EVENT_RESTRICTION"
    elif "RISK" in combined_reason or "EXPOSURE" in combined_reason:
        restriction = "RISK_RESTRICTION"

    degraded: list[str] = []
    for provider, value in sorted((provider_health or {}).items()):
        if isinstance(value, Mapping):
            state = str(value.get("status") or value.get("state") or "").upper()
        else:
            state = str(value or "").upper()
        if state in _DEGRADED_PROVIDER_STATES:
            degraded.append(f"{provider}:{state}")

    issues = [f"PROVIDER_{item}" for item in degraded]
    readiness = None
    if linkage_health:
        readiness = str(
            linkage_health.get("readiness_state")
            or linkage_health.get("status")
            or ""
        ) or None
        if readiness and readiness not in {"READY", "READY_FOR_OFFLINE_TRAINING", "OK"}:
            issues.append(f"LINKAGE_READINESS_{readiness}")

    return ZeroTradeDiagnostic(
        candidate_count=len(rows),
        rejected_count=len(rejected),
        qualified_count=len(qualified),
        unscored_count=len(unscored),
        top_candidate_id=(
            str(top.get("candidate_id") or top.get("snapshot_id") or "") or None
        ),
        top_pair=str(top.get("pair") or top.get("symbol") or "") or None,
        binding_gate=binding_gate,
        binding_reason_code=binding_reason,
        nearest_miss_candidate_id=nearest_id,
        nearest_miss_gate=nearest_gate,
        nearest_miss_distance=nearest_distance,
        event_or_risk_restriction=restriction,
        degraded_providers=tuple(degraded),
        operational_issues=tuple(sorted(set(issues))),
        linkage_readiness=readiness,
    )
