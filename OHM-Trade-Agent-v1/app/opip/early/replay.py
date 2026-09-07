"""Forensic and counterfactual replay for early detection (Issue #223 §15).

Two clearly separated modes:

``FORENSIC``
    replay what production actually knew and decided, from persisted
    ``ScreeningEvaluation`` evidence. Used to verify that a replay reproduces
    the production decision exactly, which is the precondition for trusting
    any retrospective claim.

``COUNTERFACTUAL``
    evaluate a proposed selector against captured point-in-time history. The
    candidate feature set is validated with
    :func:`app.opip.early.point_in_time.assert_point_in_time_safe`, so a
    counterfactual cannot acquire lookahead.

Outcome cohorts are defined from forward price behaviour and a liquidity
floor, deliberately *independently* of whether O'Pip ever alerted. The
negative cohort holds assets that showed comparable early precursor features
and then failed, which is what keeps precision estimates free of survivorship
bias. Forward information lives only in this offline module.

This module reads evidence and returns reports. It writes nothing, ranks
nothing in production, and grants no authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import math
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.opip.early.point_in_time import (
    PointInTimeWindow,
    assert_point_in_time_safe,
    parse_timestamp,
)
from app.opip.early.taxonomy import EvidenceGrade, MarketPhase, is_early_phase

REPLAY_VERSION = "opip-early-replay-v1"


class ReplayMode(str, Enum):
    FORENSIC = "FORENSIC"
    COUNTERFACTUAL = "COUNTERFACTUAL"


class CohortLabel(str, Enum):
    """Forward outcome label. Offline only; never visible to a runtime scorer."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    UNRESOLVED = "UNRESOLVED"


#: Forward move that defines a "large mover" for the positive cohort.
DEFAULT_POSITIVE_MOVE_PCT = 20.0
#: Liquidity floor below which an asset is excluded from either cohort.
DEFAULT_COHORT_LIQUIDITY_FLOOR_USD = 50_000.0


def _finite_optional(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return round(ordered[middle], 6)
    return round((ordered[middle - 1] + ordered[middle]) / 2.0, 6)


# ---------------------------------------------------------------------------
# Forensic replay
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ForensicRow:
    """One reconstructed production screening decision."""

    venue_instrument_id: str
    outcome: str
    scan_id: str
    observed_at: str | None
    long_score: float | None
    reason: str | None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def reconstructable(self) -> bool:
        """Whether the row carries enough evidence to explain the decision.

        A ``COARSE_RANK_LIMIT`` row is reconstructable only when the Phase 0A
        rank/cutoff/margin evidence is present. That was the missing piece
        that made the RAY retrospective unfalsifiable.
        """
        metadata = dict(self.metadata)
        if self.outcome == "COARSE_RANK_LIMIT":
            rank = metadata.get("coarse_rank")
            if not isinstance(rank, Mapping):
                return False
            return rank.get("rank_position") is not None and (
                rank.get("cutoff_score") is not None or rank.get("margin_to_cutoff") is not None
            )
        if self.outcome in {"BELOW_THRESHOLD", "BELOW_COARSE_THRESHOLD"}:
            score = metadata.get("score_evidence")
            if isinstance(score, Mapping):
                return score.get("achieved_score") is not None or bool(
                    score.get("failed_predicates")
                )
            return bool(metadata.get("failed_predicates"))
        return True

    @property
    def rejection_gate(self) -> str | None:
        """The gate that stopped this candidate, when one applies."""
        if self.outcome in {"ADVANCED"}:
            return None
        metadata = dict(self.metadata)
        score = metadata.get("score_evidence")
        if isinstance(score, Mapping):
            failed = list(score.get("failed_predicates") or ())
            if failed:
                return str(failed[0])
            blocking = score.get("blocking_reason")
            if blocking:
                return str(blocking)
        failed_top = list(metadata.get("failed_predicates") or ())
        if failed_top:
            return str(failed_top[0])
        return self.outcome

    @property
    def rank_margin(self) -> float | None:
        rank = dict(self.metadata).get("coarse_rank")
        if isinstance(rank, Mapping):
            return _finite_optional(rank.get("margin_to_cutoff"))
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "venue_instrument_id": self.venue_instrument_id,
            "outcome": self.outcome,
            "scan_id": self.scan_id,
            "observed_at": self.observed_at,
            "long_score": self.long_score,
            "reason": self.reason,
            "reconstructable": self.reconstructable,
            "rejection_gate": self.rejection_gate,
            "rank_margin": self.rank_margin,
        }


def forensic_rows(
    screening_rows: Iterable[Mapping[str, Any]],
    *,
    decision_at: datetime | None = None,
) -> list[ForensicRow]:
    """Rebuild production decisions from persisted screening evidence.

    When ``decision_at`` is supplied, rows observed after it are dropped, so a
    forensic replay sees exactly the evidence the decision had.
    """
    window = PointInTimeWindow(decision_at) if decision_at is not None else None
    rebuilt: list[ForensicRow] = []
    for row in screening_rows:
        if not isinstance(row, Mapping):
            continue
        observed_at = parse_timestamp(row.get("observed_at"))
        if window is not None and not window.admits(observed_at):
            continue
        metadata = row.get("metadata")
        rebuilt.append(
            ForensicRow(
                venue_instrument_id=str(
                    row.get("venue_instrument_id")
                    or (row.get("venue_instrument") or {}).get("venue_instrument_id")
                    or ""
                ),
                outcome=str(row.get("outcome") or "UNKNOWN"),
                scan_id=str(row.get("scan_id") or ""),
                observed_at=observed_at.isoformat() if observed_at is not None else None,
                long_score=_finite_optional(row.get("long_score")),
                reason=str(row["reason"]) if row.get("reason") is not None else None,
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        )
    return rebuilt


def authoritative_outcomes_by_instrument(
    screening_rows: Iterable[Mapping[str, Any]],
    *,
    decision_at: datetime | None = None,
) -> dict[str, str]:
    """Exactly one Stage-0 outcome per instrument.

    Prefers rows marked ``metadata.authoritative``. When the selector is not
    promoted, legacy scans already persist one final outcome per instrument.
    """
    rows = forensic_rows(screening_rows, decision_at=decision_at)
    chosen: dict[str, ForensicRow] = {}
    for row in rows:
        key = row.venue_instrument_id
        if not key:
            continue
        existing = chosen.get(key)
        if existing is None:
            chosen[key] = row
            continue
        existing_auth = bool(dict(existing.metadata).get("authoritative"))
        new_auth = bool(dict(row.metadata).get("authoritative"))
        if new_auth and not existing_auth:
            chosen[key] = row
    return {key: row.outcome for key, row in chosen.items()}


def replay_forensic(
    screening_rows: Iterable[Mapping[str, Any]],
    *,
    decision_at: datetime | None = None,
) -> dict[str, Any]:
    """Report what production decided and whether it can be reconstructed."""
    rows = forensic_rows(screening_rows, decision_at=decision_at)
    by_outcome: dict[str, int] = {}
    for row in rows:
        by_outcome[row.outcome] = by_outcome.get(row.outcome, 0) + 1
    unreconstructable = [row.venue_instrument_id for row in rows if not row.reconstructable]
    margins = [
        value for value in (row.rank_margin for row in rows) if value is not None
    ]
    gates: dict[str, int] = {}
    for row in rows:
        gate = row.rejection_gate
        if gate is not None:
            gates[gate] = gates.get(gate, 0) + 1
    return {
        "version": REPLAY_VERSION,
        "mode": ReplayMode.FORENSIC.value,
        "rows": len(rows),
        "outcome_counts": by_outcome,
        "rejection_reason_distribution": gates,
        "reconstructable_rows": len(rows) - len(unreconstructable),
        "unreconstructable_rows": len(unreconstructable),
        "unreconstructable_instruments": sorted(set(unreconstructable)),
        "rank_margin_distribution": {
            "count": len(margins),
            "median": _median(margins),
            "min": round(min(margins), 6) if margins else None,
            "max": round(max(margins), 6) if margins else None,
        },
        "measurement_only": True,
        "trade_authority_changed": False,
    }


def verify_forensic_replay_matches_production(
    *,
    replayed: Mapping[str, str],
    production: Mapping[str, str],
) -> dict[str, Any]:
    """Assert a forensic replay reproduces the production outcome per instrument."""
    replayed_map = {str(key): str(value) for key, value in replayed.items()}
    production_map = {str(key): str(value) for key, value in production.items()}
    mismatches = {
        key: {"replayed": replayed_map.get(key), "production": value}
        for key, value in production_map.items()
        if replayed_map.get(key) != value
    }
    missing = sorted(set(production_map) - set(replayed_map))
    return {
        "version": REPLAY_VERSION,
        "mode": ReplayMode.FORENSIC.value,
        "compared": len(production_map),
        "exact_match": not mismatches and not missing,
        "mismatches": mismatches,
        "missing_from_replay": missing,
    }


# ---------------------------------------------------------------------------
# Counterfactual replay
# ---------------------------------------------------------------------------

def replay_counterfactual(
    *,
    history_rows: Sequence[Mapping[str, Any]],
    decision_at: datetime,
    selector: Callable[[Sequence[Mapping[str, Any]]], Sequence[str]],
    production_selection: Sequence[str],
) -> dict[str, Any]:
    """Evaluate a candidate selector against point-in-time history.

    Every admitted row's feature names are checked for forward information
    before the selector sees them, so lookahead fails closed rather than
    silently inflating a counterfactual result.
    """
    window = PointInTimeWindow(decision_at)
    admitted = window.filter(history_rows)
    for row in admitted:
        assert_point_in_time_safe(row)

    challenger = [str(item) for item in selector(admitted)]
    production = [str(item) for item in production_selection]
    challenger_set = set(challenger)
    production_set = set(production)
    return {
        "version": REPLAY_VERSION,
        "mode": ReplayMode.COUNTERFACTUAL.value,
        "decision_at": window.decision_at.isoformat(),
        "history_rows_supplied": len(history_rows),
        "history_rows_admitted": len(admitted),
        "history_rows_excluded_as_future": len(history_rows) - len(admitted),
        "challenger_selection": challenger,
        "production_selection": production,
        "challenger_only": sorted(challenger_set - production_set),
        "production_only": sorted(production_set - challenger_set),
        "shared": sorted(challenger_set & production_set),
        "lookahead_detected": False,
        "production_selection_changed": False,
        "trade_authority_changed": False,
    }


# ---------------------------------------------------------------------------
# Outcome cohorts (offline only)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CohortMember:
    """One asset with its offline forward outcome and decision-time context."""

    symbol: str
    label: CohortLabel
    liquidity_24h_usd: float | None
    forward_max_move_pct: float | None
    forward_min_move_pct: float | None
    first_observed_phase: str | None = None
    first_qualified_phase: str | None = None
    evidence_grade: str | None = None
    alerted: bool = False
    delivered: bool = False
    actionable_at_alert: bool = False
    move_consumed_before_alert_pct: float | None = None
    move_consumed_before_qualification_pct: float | None = None
    detection_delay_seconds: float | None = None
    qualification_delay_seconds: float | None = None
    rejection_gate: str | None = None
    rank_margin: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "label": self.label.value,
            "liquidity_24h_usd": self.liquidity_24h_usd,
            "forward_max_move_pct": self.forward_max_move_pct,
            "forward_min_move_pct": self.forward_min_move_pct,
            "first_observed_phase": self.first_observed_phase,
            "first_qualified_phase": self.first_qualified_phase,
            "evidence_grade": self.evidence_grade,
            "alerted": self.alerted,
            "delivered": self.delivered,
            "actionable_at_alert": self.actionable_at_alert,
        }


def label_cohort_member(
    *,
    symbol: str,
    liquidity_24h_usd: Any,
    forward_max_move_pct: Any,
    forward_min_move_pct: Any = None,
    positive_move_pct: float = DEFAULT_POSITIVE_MOVE_PCT,
    liquidity_floor_usd: float = DEFAULT_COHORT_LIQUIDITY_FLOOR_USD,
    had_early_precursor: bool = False,
    **context: Any,
) -> CohortMember:
    """Label one asset from forward behaviour, independently of O'Pip alerts.

    Positive cohort: a forward move at or above ``positive_move_pct`` above the
    liquidity floor. Negative cohort: showed comparable early precursor
    features and did not deliver the move. Everything else is ``UNRESOLVED``
    and is excluded from precision arithmetic rather than counted as a success.
    """
    liquidity = _finite_optional(liquidity_24h_usd)
    forward_max = _finite_optional(forward_max_move_pct)

    if liquidity is None or liquidity < float(liquidity_floor_usd) or forward_max is None:
        label = CohortLabel.UNRESOLVED
    elif forward_max >= float(positive_move_pct):
        label = CohortLabel.POSITIVE
    elif had_early_precursor:
        label = CohortLabel.NEGATIVE
    else:
        label = CohortLabel.UNRESOLVED

    return CohortMember(
        symbol=str(symbol).upper(),
        label=label,
        liquidity_24h_usd=liquidity,
        forward_max_move_pct=forward_max,
        forward_min_move_pct=_finite_optional(forward_min_move_pct),
        first_observed_phase=context.get("first_observed_phase"),
        first_qualified_phase=context.get("first_qualified_phase"),
        evidence_grade=context.get("evidence_grade"),
        alerted=bool(context.get("alerted", False)),
        delivered=bool(context.get("delivered", False)),
        actionable_at_alert=bool(context.get("actionable_at_alert", False)),
        move_consumed_before_alert_pct=_finite_optional(
            context.get("move_consumed_before_alert_pct")
        ),
        move_consumed_before_qualification_pct=_finite_optional(
            context.get("move_consumed_before_qualification_pct")
        ),
        detection_delay_seconds=_finite_optional(context.get("detection_delay_seconds")),
        qualification_delay_seconds=_finite_optional(
            context.get("qualification_delay_seconds")
        ),
        rejection_gate=context.get("rejection_gate"),
        rank_margin=_finite_optional(context.get("rank_margin")),
    )


def evaluate_cohort_metrics(
    members: Sequence[CohortMember],
    *,
    observation_days: float | None = None,
) -> dict[str, Any]:
    """Compute the promotion metric set over a labelled cohort.

    Lead time is reported as a diagnostic. The promotion objective is
    precision at ``QUALIFIED`` subject to alert volume, which
    :mod:`app.opip.early.promotion` enforces.
    """
    resolved = [item for item in members if item.label is not CohortLabel.UNRESOLVED]
    qualified = [
        item for item in resolved if str(item.evidence_grade) == EvidenceGrade.QUALIFIED.value
    ]
    qualified_positive = [item for item in qualified if item.label is CohortLabel.POSITIVE]
    delivered = [item for item in resolved if item.delivered]

    def _phase_share(items: Sequence[CohortMember], attribute: str) -> float | None:
        phases = [getattr(item, attribute) for item in items]
        present = [value for value in phases if value]
        if not present:
            return None
        early = sum(1 for value in present if is_early_phase(value))
        return round(early / len(present) * 100.0, 4)

    consumed_before_alert = [
        item.move_consumed_before_alert_pct
        for item in resolved
        if item.move_consumed_before_alert_pct is not None
    ]
    consumed_before_qualification = [
        item.move_consumed_before_qualification_pct
        for item in resolved
        if item.move_consumed_before_qualification_pct is not None
    ]
    mfe = [
        item.forward_max_move_pct for item in resolved if item.forward_max_move_pct is not None
    ]
    mae = [
        item.forward_min_move_pct for item in resolved if item.forward_min_move_pct is not None
    ]
    gates: dict[str, int] = {}
    for item in resolved:
        if item.rejection_gate:
            gates[item.rejection_gate] = gates.get(item.rejection_gate, 0) + 1
    margins = [item.rank_margin for item in resolved if item.rank_margin is not None]

    alerted = [item for item in resolved if item.alerted]
    false_positives = [item for item in alerted if item.label is CohortLabel.NEGATIVE]
    days = _finite_optional(observation_days)

    return {
        "version": REPLAY_VERSION,
        "members": len(members),
        "resolved_members": len(resolved),
        "positive_cohort": sum(1 for item in resolved if item.label is CohortLabel.POSITIVE),
        "negative_cohort": sum(1 for item in resolved if item.label is CohortLabel.NEGATIVE),
        "unresolved_excluded": len(members) - len(resolved),
        "qualified_precision_pct": (
            round(len(qualified_positive) / len(qualified) * 100.0, 4) if qualified else None
        ),
        "false_positive_rate_pct": (
            round(len(false_positives) / len(alerted) * 100.0, 4) if alerted else None
        ),
        "operator_alert_volume": len(alerted),
        "delivered_notification_volume": len(delivered),
        "false_alerts_per_day": (
            round(len(false_positives) / days, 6) if days and days > 0 else None
        ),
        "median_move_consumed_before_alert_pct": _median(consumed_before_alert),
        "median_move_consumed_before_qualification_pct": _median(
            consumed_before_qualification
        ),
        "first_observation_early_phase_share_pct": _phase_share(
            resolved, "first_observed_phase"
        ),
        "first_qualification_early_phase_share_pct": _phase_share(
            resolved, "first_qualified_phase"
        ),
        "median_detection_delay_seconds": _median(
            [
                item.detection_delay_seconds
                for item in resolved
                if item.detection_delay_seconds is not None
            ]
        ),
        "median_qualification_delay_seconds": _median(
            [
                item.qualification_delay_seconds
                for item in resolved
                if item.qualification_delay_seconds is not None
            ]
        ),
        "median_mfe_pct": _median(mfe),
        "median_mae_pct": _median(mae),
        "forward_expectancy_pct": (
            round(sum(mfe + mae) / len(mfe + mae), 6) if (mfe or mae) else None
        ),
        "actionability_at_alert_pct": (
            round(
                sum(1 for item in alerted if item.actionable_at_alert) / len(alerted) * 100.0,
                4,
            )
            if alerted
            else None
        ),
        "rejection_reason_distribution": gates,
        "rank_margin_distribution": {
            "count": len(margins),
            "median": _median(margins),
        },
        "measurement_only": True,
        "trade_authority_changed": False,
    }


def phase_at_first_observation(members: Sequence[CohortMember]) -> dict[str, int]:
    """Distribution of the phase in which each eventual mover was first seen."""
    counts: dict[str, int] = {phase.value: 0 for phase in MarketPhase}
    for item in members:
        if item.label is not CohortLabel.POSITIVE:
            continue
        phase = str(item.first_observed_phase or "")
        if phase in counts:
            counts[phase] += 1
    return counts
