"""Exposure-level risk aggregation and transition governance (BUILD 3.3).

This module decides *whether* a meaningful advisory transition exists. It does
not send Telegram messages and has no exchange/order authority. De-escalation
is frozen whenever evidence/exposure coverage is incomplete; missing data is
never interpreted as lower risk.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from app.opip.events.contract import EventSeverity, require_utc, stable_payload_hash, utc_iso
from app.opip.risk.contract import ExposureFamily, ExposureView, RiskAssessment, RiskState, risk_state_rank
from app.opip.risk.policy import SEVERITY_RANK
from app.services.registry_io import load_json, registry_lock, save_json_atomic

STATE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ExposureRiskAggregate:
    exposure_id: str
    family: ExposureFamily
    risk_state: RiskState
    selected_assessment: RiskAssessment | None
    active_assessment_count: int


@dataclass(frozen=True)
class AlertTransitionDecision:
    exposure_id: str
    family: ExposureFamily
    previous_state: RiskState
    current_state: RiskState
    transition: str
    should_notify: bool
    notification_reason: str
    fingerprint: str
    selected_assessment_id: str | None
    selected_effective_event_id: str | None
    coverage_complete: bool
    state_frozen: bool = False
    previous_token: str = ""


def _severity_rank(assessment: RiskAssessment | None) -> int:
    return SEVERITY_RANK.get(assessment.event_severity, 0) if assessment else -1


def aggregate_exposure_risk(
    *,
    exposure: ExposureView,
    assessments: Sequence[RiskAssessment],
    decision_at: datetime,
) -> ExposureRiskAggregate:
    cutoff = require_utc(decision_at, field_name="decision_at")
    active = [
        item for item in assessments
        if item.exposure_id == exposure.exposure_id
        and item.exposure_family is exposure.exposure_family
        and (item.event_expires_at_utc is None or item.event_expires_at_utc >= cutoff)
    ]
    if not active:
        return ExposureRiskAggregate(
            exposure_id=exposure.exposure_id,
            family=exposure.exposure_family,
            risk_state=RiskState.NONE,
            selected_assessment=None,
            active_assessment_count=0,
        )
    # Highest current deterministic state wins. Tie-breakers are deterministic
    # and never use append order / "last row wins" semantics.
    selected = max(
        active,
        key=lambda item: (
            risk_state_rank(item.risk_state),
            _severity_rank(item),
            item.event_decision_visible_at_utc or item.decision_at_utc,
            item.event_source_time_utc or item.decision_at_utc,
            item.effective_event_id,
            item.assessment_id,
        ),
    )
    return ExposureRiskAggregate(
        exposure_id=exposure.exposure_id,
        family=exposure.exposure_family,
        risk_state=selected.risk_state,
        selected_assessment=selected,
        active_assessment_count=len(active),
    )


def _state_token(row: dict[str, Any] | None) -> str:
    return stable_payload_hash(row or {})


def _fingerprint(
    exposure_id: str,
    state: RiskState,
    assessment: RiskAssessment | None,
) -> str:
    return stable_payload_hash(
        {
            "exposure_id": exposure_id,
            "risk_state": state.value,
            "assessment_id": assessment.assessment_id if assessment else None,
            "effective_event_id": assessment.effective_event_id if assessment else None,
            "event_severity": assessment.event_severity.value if assessment else None,
            "policy_version": assessment.policy_version if assessment else None,
        }
    )


class AlertStateManager:
    """Family-isolated durable transition state. Evaluation and commit are split.

    This two-phase shape lets the observer persist T0 attribution before the
    transition is committed. If T0 persistence fails after an assessment was
    stored, the next cycle sees the same previous alert state and can heal the
    missing T0 without losing the original notification decision.
    """
    def __init__(self, *, family: ExposureFamily, root: Path) -> None:
        self.family = family
        family_root = root / family.value.lower()
        self.state_file = family_root / "alert_state.json"
        self.lock_file = family_root / ".alert_state.lock"

    def _load(self) -> dict[str, Any]:
        try:
            state = load_json(self.state_file)
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        exposures = state.get("exposures")
        if not isinstance(exposures, dict):
            exposures = {}
        return {"schema_version": STATE_SCHEMA_VERSION, "family": self.family.value, "exposures": exposures}

    def evaluate(
        self,
        *,
        exposure: ExposureView,
        aggregate: ExposureRiskAggregate,
        decision_at: datetime,
        coverage_complete: bool,
    ) -> AlertTransitionDecision:
        cutoff = require_utc(decision_at, field_name="decision_at")
        with registry_lock(self.lock_file):
            state = self._load()
            previous = state["exposures"].get(exposure.exposure_id)
            previous = previous if isinstance(previous, dict) else {}
        previous_state = RiskState(str(previous.get("risk_state") or RiskState.NONE.value))
        desired_state = aggregate.risk_state
        selected = aggregate.selected_assessment
        raw_previous_severity = previous.get("severity_rank")
        previous_severity = int(raw_previous_severity) if raw_previous_severity is not None else -1
        current_severity = _severity_rank(selected)

        frozen = False
        transition = "UNCHANGED"
        notify = False
        reason = "UNCHANGED_STATE"
        current_state = desired_state

        if risk_state_rank(desired_state) < risk_state_rank(previous_state) and not coverage_complete:
            frozen = True
            current_state = previous_state
            transition = "DEESCALATION_FROZEN"
            reason = "COVERAGE_INCOMPLETE_NO_DEESCALATION"
        elif desired_state == previous_state:
            changed_event = bool(
                selected
                and str(previous.get("effective_event_id") or "") != selected.effective_event_id
            )
            if changed_event and current_severity > previous_severity:
                transition = "MATERIAL_REVISION_OR_EVENT"
                notify = exposure.is_real_advisory and desired_state in {
                    RiskState.AVOID_NEW_ENTRY, RiskState.PROTECT_REVIEW, RiskState.EXIT_REVIEW
                }
                reason = "SAME_STATE_HIGHER_SEVERITY"
        elif risk_state_rank(desired_state) > risk_state_rank(previous_state):
            transition = "ESCALATION"
            notify = exposure.is_real_advisory and desired_state in {
                RiskState.AVOID_NEW_ENTRY, RiskState.PROTECT_REVIEW, RiskState.EXIT_REVIEW
            }
            reason = "MEANINGFUL_RISK_ESCALATION" if notify else "STATE_ESCALATION_RECORDED"
        else:
            transition = "DEESCALATION"
            notify = False
            reason = "DEESCALATION_RECORDED_SILENTLY"

        # WATCH is internal evidence. Paper is never a notification candidate.
        if current_state is RiskState.WATCH:
            notify = False
        if exposure.exposure_family is ExposureFamily.PAPER:
            notify = False
            if transition != "UNCHANGED":
                reason = "PAPER_STATE_RECORDED_NO_NOTIFICATION"

        return AlertTransitionDecision(
            exposure_id=exposure.exposure_id,
            family=exposure.exposure_family,
            previous_state=previous_state,
            current_state=current_state,
            transition=transition,
            should_notify=notify,
            notification_reason=reason,
            fingerprint=_fingerprint(exposure.exposure_id, current_state, selected),
            selected_assessment_id=selected.assessment_id if selected else None,
            selected_effective_event_id=selected.effective_event_id if selected else None,
            coverage_complete=coverage_complete,
            state_frozen=frozen,
            previous_token=_state_token(previous),
        )

    def commit(
        self,
        *,
        decision: AlertTransitionDecision,
        aggregate: ExposureRiskAggregate,
        decision_at: datetime,
    ) -> bool:
        cutoff = require_utc(decision_at, field_name="decision_at")
        selected = aggregate.selected_assessment
        with registry_lock(self.lock_file):
            state = self._load()
            exposures = state["exposures"]
            current = exposures.get(decision.exposure_id)
            current = current if isinstance(current, dict) else {}
            if _state_token(current) != decision.previous_token:
                return False
            # Frozen/unchanged state needs no churn unless no state existed and
            # there is a non-NONE current state to establish.
            if decision.state_frozen:
                return True
            if decision.transition == "UNCHANGED":
                # Do not create state for an exposure that has never carried
                # risk; unchanged existing state also needs no write churn.
                return True
            exposures[decision.exposure_id] = {
                "risk_state": decision.current_state.value,
                "assessment_id": selected.assessment_id if selected else None,
                "effective_event_id": selected.effective_event_id if selected else None,
                "event_severity": selected.event_severity.value if selected else None,
                "severity_rank": _severity_rank(selected),
                "fingerprint": decision.fingerprint,
                "transition": decision.transition,
                "updated_at_utc": utc_iso(cutoff),
            }
            state["updated_at_utc"] = utc_iso(cutoff)
            save_json_atomic(self.state_file, state)
        return True
    def prune_absent(self, *, active_exposure_ids: set[str], coverage_complete: bool) -> int:
        """Drop current-state rows for exposures definitively absent from a complete snapshot.

        This is state maintenance only; historical assessments/T0 remain append-only.
        If coverage is incomplete, absence is not trusted and nothing is pruned.
        """
        if not coverage_complete:
            return 0
        with registry_lock(self.lock_file):
            state = self._load()
            exposures = state["exposures"]
            stale = [key for key in exposures if key not in active_exposure_ids]
            if not stale:
                return 0
            for key in stale:
                exposures.pop(key, None)
            save_json_atomic(self.state_file, state)
            return len(stale)
