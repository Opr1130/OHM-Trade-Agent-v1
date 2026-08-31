"""The O'Pip qualification funnel: one terminal state for every candidate.

The funnel is an in-memory recorder that the live scan drives as it runs. It
observes; it never decides. Recording a gate result cannot change what the
production path does with that candidate - the production code has already
acted by the time the funnel is told about it.

Its central guarantee is exact terminal attribution:

    candidates entering the funnel
        == qualified + rejected + operationally unresolved

No candidate may simply disappear. A candidate that the scan never terminated
is reported as INCOMPLETE, which counts as operationally unresolved - an
admission that the instrumentation lost track of it, never a rejection it did
not receive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from app.opip.decision.identity import (
    candidate_key,
    market_type_for,
    normalize_direction,
    opip_candidate_id,
)
from app.opip.decision.models import (
    COUNTERFACTUAL_EVIDENCE_GATE,
    GATE_INDEX,
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateResult,
    GateStatus,
    ReasonClass,
    ReasonCode,
    terminal_attribution,
)
from app.opip.decision.versioning import (
    GATE_POLICY_VERSION,
    INTELLIGENCE_VERSION,
    STRATEGY_VERSION,
    version_stamp,
)


#: Invocation states for the Chief/AI stage. These are not interchangeable and
#: the summary must never collapse them.
AI_NOT_REACHED = "NOT_REACHED"
AI_SKIPPED_NO_ELIGIBLE = "SKIPPED_NO_ELIGIBLE_CANDIDATES"
AI_BUDGET_BLOCKED = "BUDGET_BLOCKED"
AI_CACHE_REUSED = "CACHE_REUSED"
AI_FAILED = "FAILED"
AI_SUCCEEDED = "SUCCEEDED"


@dataclass
class AIStageEvidence:
    """What actually happened at the AI/Chief stage of one scan.

    ``AI top candidates = 0`` is true for every one of these states, which is
    exactly why the existing operator line cannot explain a zero-trade scan.
    """

    invocation_status: str = AI_NOT_REACHED
    failure_type: str | None = None
    invoked_at: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    eligible_candidates_before_ai: int = 0
    candidates_returned_by_ai: int = 0
    confidences: list[int] = field(default_factory=list)

    @property
    def invoked(self) -> bool:
        return self.invocation_status in {AI_SUCCEEDED, AI_CACHE_REUSED}

    @property
    def unavailable(self) -> bool:
        return self.invocation_status == AI_FAILED

    @property
    def budget_exhausted(self) -> bool:
        return self.invocation_status == AI_BUDGET_BLOCKED

    def confidence_summary(self) -> dict[str, Any]:
        """Return distribution statistics for the observed AI confidences.

        These describe the model's *comparative review confidence* as emitted.
        They are explicitly not calibrated probabilities and must not be read
        as win rates.
        """
        values = sorted(int(item) for item in self.confidences)
        if not values:
            return {
                "count": 0,
                "min": None,
                "max": None,
                "mean": None,
                "median": None,
                "calibrated_probability": False,
            }
        count = len(values)
        middle = count // 2
        median = (
            float(values[middle])
            if count % 2
            else (values[middle - 1] + values[middle]) / 2.0
        )
        return {
            "count": count,
            "min": values[0],
            "max": values[-1],
            "mean": round(sum(values) / count, 4),
            "median": median,
            "calibrated_probability": False,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "invocation_status": self.invocation_status,
            "failure_type": self.failure_type,
            "invoked_at": self.invoked_at,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "eligible_candidates_before_ai": int(self.eligible_candidates_before_ai),
            "candidates_returned_by_ai": int(self.candidates_returned_by_ai),
            "invoked": self.invoked,
            "unavailable": self.unavailable,
            "budget_exhausted": self.budget_exhausted,
            "confidence_summary": self.confidence_summary(),
        }


@dataclass
class CandidateFunnelState:
    """Everything the funnel knows about one candidate in one scan."""

    candidate_id: str
    symbol: str
    asset: str
    pair: str
    direction: str
    market_type: str
    episode_id: str | None = None
    signal_id: str | None = None
    asset_display_name: str | None = None
    gate_results: list[GateResult] = field(default_factory=list)
    legacy_decision: str | None = None
    legacy_terminal_reason: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return candidate_key(self.symbol, self.direction)

    def record(self, result: GateResult) -> None:
        """Append a gate result, keeping the sequence in canonical gate order.

        Recording is idempotent per gate: a repeated gate replaces the earlier
        result rather than appending a duplicate, so a retried stage cannot
        inflate the funnel or produce two terminal reasons for one candidate.
        """
        for index, existing in enumerate(self.gate_results):
            if existing.gate is result.gate:
                self.gate_results[index] = result
                break
        else:
            self.gate_results.append(result)
        self.gate_results.sort(key=lambda item: GATE_INDEX.get(item.gate, 0))

    def counterfactual_eligible(self) -> bool:
        """Whether a future build could replay this candidate counterfactually.

        Eligibility requires a full deterministic evaluation to exist, so the
        candidate must have reached the gate at which a plan is built. Build 1
        records this and nothing else: it never routes a counterfactual trade.
        """
        threshold = GATE_INDEX[COUNTERFACTUAL_EVIDENCE_GATE]
        return any(
            GATE_INDEX.get(result.gate, -1) >= threshold
            for result in self.gate_results
        )

    def to_decision(self, *, decided_at: str) -> AdmissionDecision:
        outcome, gate, code, reason = terminal_attribution(self.gate_results)
        return AdmissionDecision(
            candidate_id=self.candidate_id,
            episode_id=self.episode_id,
            signal_id=self.signal_id,
            asset=self.asset,
            asset_display_name=self.asset_display_name,
            pair=self.pair,
            market_type=self.market_type,
            direction=self.direction,
            decided_at=decided_at,
            decision=outcome,
            first_terminal_gate=gate,
            terminal_reason_code=code,
            terminal_reason=reason,
            gate_results=tuple(self.gate_results),
            counterfactual_eligible=(
                outcome is DecisionOutcome.REJECTED and self.counterfactual_eligible()
            ),
            strategy_version=STRATEGY_VERSION,
            intelligence_version=INTELLIGENCE_VERSION,
            gate_policy_version=GATE_POLICY_VERSION,
        )


class QualificationFunnel:
    """Per-scan recorder for the complete O'Pip qualification funnel."""

    def __init__(
        self,
        *,
        scan_id: str,
        decision_at: datetime,
        cohort_id: str | None = None,
    ) -> None:
        if decision_at.tzinfo is None or decision_at.utcoffset() is None:
            raise ValueError("decision_at must be timezone-aware")
        self.scan_id = scan_id
        self.cohort_id = cohort_id
        self.decision_at = decision_at.astimezone(timezone.utc)
        self.decision_at_iso = self.decision_at.isoformat()
        self.ai_stage = AIStageEvidence()
        self._candidates: dict[tuple[str, str], CandidateFunnelState] = {}
        self._order: list[tuple[str, str]] = []

    # -- registration ---------------------------------------------------

    def register(
        self,
        *,
        symbol: str,
        direction: str,
        asset: str | None = None,
        pair: str | None = None,
        episode_id: str | None = None,
        asset_display_name: str | None = None,
    ) -> CandidateFunnelState:
        """Admit a directional candidate into the funnel, idempotently."""
        normalized_direction = normalize_direction(direction)
        key = candidate_key(symbol, normalized_direction)
        existing = self._candidates.get(key)
        if existing is not None:
            if episode_id and not existing.episode_id:
                existing.episode_id = episode_id
                existing.candidate_id = opip_candidate_id(
                    episode_id=episode_id,
                    pair=existing.pair,
                    direction=existing.direction,
                    market_type=existing.market_type,
                )
            return existing

        resolved_symbol = str(symbol or "").upper()
        resolved_pair = str(pair or resolved_symbol).upper()
        resolved_asset = str(asset or resolved_symbol).upper()
        market_type = market_type_for(normalized_direction)
        state = CandidateFunnelState(
            candidate_id=opip_candidate_id(
                episode_id=episode_id or self.scan_id,
                pair=resolved_pair,
                direction=normalized_direction,
                market_type=market_type,
            ),
            symbol=resolved_symbol,
            asset=resolved_asset,
            pair=resolved_pair,
            direction=normalized_direction,
            market_type=market_type,
            episode_id=episode_id,
            asset_display_name=asset_display_name,
        )
        state.record(
            GateResult.build(
                GateName.CANDIDATE_CREATED,
                GateStatus.PASS,
                ReasonCode.CANDIDATE_ADMITTED,
                reason="directional candidate entered the qualification funnel",
                evaluated_at=self.decision_at,
                metadata={"market_type": market_type},
            )
        )
        state.record(
            GateResult.build(
                GateName.DIRECTION_SELECTED,
                GateStatus.PASS,
                ReasonCode.GATE_PASSED,
                reason=f"direction {normalized_direction} selected by the scanner",
                evaluated_at=self.decision_at,
                metadata={"direction": normalized_direction},
            )
        )
        self._candidates[key] = state
        self._order.append(key)
        return state

    # -- recording ------------------------------------------------------

    def get(self, symbol: str, direction: str) -> CandidateFunnelState | None:
        return self._candidates.get(candidate_key(symbol, direction))

    def record(
        self,
        symbol: str,
        direction: str,
        result: GateResult,
    ) -> bool:
        """Record one gate result. Returns False for an unregistered candidate."""
        state = self.get(symbol, direction)
        if state is None:
            return False
        state.record(result)
        return True

    def record_legacy_outcome(
        self,
        symbol: str,
        direction: str,
        *,
        decision: str,
        terminal_reason: str | None = None,
    ) -> bool:
        """Record what the legacy production path concluded, for comparison."""
        state = self.get(symbol, direction)
        if state is None:
            return False
        state.legacy_decision = str(decision)
        state.legacy_terminal_reason = terminal_reason
        return True

    def attach_signal_id(self, symbol: str, direction: str, signal_id: str) -> bool:
        state = self.get(symbol, direction)
        if state is None:
            return False
        state.signal_id = str(signal_id)
        return True

    def attach_episode_id(self, symbol: str, direction: str, episode_id: str) -> bool:
        """Bind the canonical episode and rebuild the candidate identity on it."""
        state = self.get(symbol, direction)
        if state is None or not episode_id:
            return False
        state.episode_id = str(episode_id)
        state.candidate_id = opip_candidate_id(
            episode_id=state.episode_id,
            pair=state.pair,
            direction=state.direction,
            market_type=state.market_type,
        )
        return True

    # -- finalisation ---------------------------------------------------

    @property
    def candidates(self) -> list[CandidateFunnelState]:
        return [self._candidates[key] for key in self._order]

    def decisions(self) -> list[AdmissionDecision]:
        """Return one AdmissionDecision per registered candidate."""
        return [
            state.to_decision(decided_at=self.decision_at_iso)
            for state in self.candidates
        ]

    def funnel_events(self) -> list[dict[str, Any]]:
        """Return the persistable funnel rows for this scan."""
        stamp = version_stamp()
        rows: list[dict[str, Any]] = []
        for state, decision in zip(self.candidates, self.decisions()):
            row = {
                "record_type": "OPIP_QUALIFICATION_FUNNEL",
                "scan_id": self.scan_id,
                "cohort_id": self.cohort_id,
                "decision_at_utc": self.decision_at_iso,
                "legacy_decision": state.legacy_decision,
                "legacy_terminal_reason": state.legacy_terminal_reason,
            }
            row.update(stamp)
            row.update(decision.as_dict())
            rows.append(row)
        return rows


def counts_by_outcome(
    decisions: Iterable[AdmissionDecision],
) -> dict[str, int]:
    """Return terminal counters without conflating policy, budget, and model stops."""
    tally = {
        "entered": 0,
        "qualified": 0,
        "rejected_total": 0,
        "rejected_by_policy": 0,
        "rejected_by_budget": 0,
        "rejected_by_model": 0,
        "rejected_other": 0,
        "operational_failures": 0,
        "incomplete": 0,
    }
    for decision in decisions:
        tally["entered"] += 1
        if decision.decision is DecisionOutcome.QUALIFIED:
            tally["qualified"] += 1
        elif decision.decision is DecisionOutcome.REJECTED:
            tally["rejected_total"] += 1
            reason_class = decision.terminal_reason_class
            if reason_class is ReasonClass.POLICY:
                tally["rejected_by_policy"] += 1
            elif reason_class is ReasonClass.BUDGET:
                tally["rejected_by_budget"] += 1
            elif reason_class is ReasonClass.MODEL:
                tally["rejected_by_model"] += 1
            else:
                tally["rejected_other"] += 1
        elif decision.decision is DecisionOutcome.OPERATIONAL_FAILURE:
            tally["operational_failures"] += 1
        else:
            tally["incomplete"] += 1
    tally["operationally_unresolved"] = (
        tally["operational_failures"] + tally["incomplete"]
    )
    return tally


def invariant_holds(counts: Mapping[str, int]) -> bool:
    """Return whether every candidate that entered the funnel was attributed."""
    return int(counts.get("entered", 0)) == (
        int(counts.get("qualified", 0))
        + int(counts.get("rejected_total", counts.get("rejected_by_policy", 0)))
        + int(counts.get("operationally_unresolved", 0))
    )


def reason_class_counts(
    decisions: Iterable[AdmissionDecision],
) -> dict[str, int]:
    """Return terminal counts grouped by reason class.

    Policy, operational, budget and model stops are counted separately because
    they demand different operator responses.
    """
    tally = {member.value: 0 for member in ReasonClass}
    for decision in decisions:
        if decision.decision is DecisionOutcome.QUALIFIED:
            continue
        tally[decision.terminal_reason_class.value] += 1
    return tally
