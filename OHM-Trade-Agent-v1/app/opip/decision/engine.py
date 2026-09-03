"""The O'Pip Decision Engine, in shadow / read-only comparison mode.

The engine walks the canonical gate sequence over evidence that the live scan
has already gathered and produces a single ``AdmissionDecision``. It exists to
centralise the decision representation and to establish the architectural seam
that later O'Pip builds need.

Build 1 explicitly does NOT make it authoritative. Production admissions are
still produced by ``app.jobs.scan_opportunities``; the engine runs beside that
path and its result is compared, recorded, and otherwise discarded.

Safety contract, enforced by ``tests/test_opip_decision_engine_safety_v1.py``:

* the engine holds no exchange client and imports no exchange module;
* it exposes no method that places, modifies, or cancels an order;
* every gate it calls is pure over already-fetched evidence, so evaluating a
  candidate issues no network request;
* it mutates nothing the production path reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.opip.decision.funnel import (
    AI_BUDGET_BLOCKED,
    AI_FAILED,
    AI_NOT_REACHED,
    AI_SKIPPED_NO_ELIGIBLE,
    AIStageEvidence,
)
from app.opip.decision.gates import (
    evaluate_cross_market_gate,
    evaluate_deterministic_quality_gate,
    evaluate_economic_quality_gate,
    evaluate_execution_gate,
    evaluate_margin_gate,
    evaluate_market_intelligence_gate,
    evaluate_recommendation_gate_item,
    evaluate_reference_gate,
    evaluate_target_quality_gate,
)
from app.opip.decision.identity import (
    market_type_for,
    normalize_direction,
    opip_candidate_id,
)
from app.opip.decision.models import (
    AdmissionDecision,
    DecisionOutcome,
    GateName,
    GateResult,
    GateStatus,
    ReasonCode,
    terminal_attribution,
)
from app.opip.decision.versioning import (
    GATE_POLICY_VERSION,
    INTELLIGENCE_VERSION,
    STRATEGY_VERSION,
)
from app.services.entry_exit_advisor import build_entry_exit_plan


@dataclass(frozen=True)
class CandidateEvidence:
    """Everything the engine is allowed to look at for one candidate.

    Deliberately a value object: the engine cannot reach past this into a
    client, a registry, or the live scan's mutable state.
    """

    snapshot: Any
    episode_id: str | None = None
    signal_id: str | None = None
    asset_display_name: str | None = None
    pair: str | None = None
    ai_item: Mapping[str, Any] | None = None
    market_intelligence: Any = None


class OPipDecisionEngine:
    """Shadow reproduction of the production qualification decision.

    The engine is constructed per scan with the same account equity the live
    path used, and evaluates one candidate at a time. It stops at the first
    gate that stops the candidate, exactly like production does.
    """

    #: Read-only marker consumed by the structural safety tests. The engine is
    #: an evidence evaluator; granting it execution authority would require
    #: changing this contract deliberately.
    AUTHORITATIVE = False
    CAN_PLACE_ORDERS = False

    def __init__(
        self,
        *,
        account_equity: float | None,
        decision_at: datetime,
        ai_stage: AIStageEvidence | None = None,
    ) -> None:
        self.account_equity = account_equity
        self.decision_at = decision_at
        self.ai_stage = ai_stage or AIStageEvidence()

    # -- gate sequence --------------------------------------------------

    def _ai_invocation_gate(self) -> GateResult:
        """Attribute the AI stage to its actual cause.

        Budget suppression, service failure and "the model answered" are three
        different events. Collapsing them - which is what the current
        ``AI top candidates = 0`` line does - makes a zero-trade scan
        unexplainable.
        """
        status = self.ai_stage.invocation_status
        metadata = {
            "invocation_status": status,
            "failure_type": self.ai_stage.failure_type,
            "eligible_candidates_before_ai": self.ai_stage.eligible_candidates_before_ai,
            "candidates_returned_by_ai": self.ai_stage.candidates_returned_by_ai,
            "model": self.ai_stage.model,
        }
        if status == AI_BUDGET_BLOCKED:
            return GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_BUDGET_LIMIT,
                reason="Chief review suppressed by the daily budget guard",
                evaluated_at=self.decision_at,
                metadata=metadata,
            )
        if status == AI_FAILED:
            return GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_SERVICE_UNAVAILABLE,
                reason=(
                    f"Chief unavailable: {self.ai_stage.failure_type}"
                    if self.ai_stage.failure_type
                    else "Chief unavailable"
                ),
                evaluated_at=self.decision_at,
                metadata=metadata,
            )
        if status in {AI_NOT_REACHED, AI_SKIPPED_NO_ELIGIBLE}:
            return GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.SKIPPED,
                ReasonCode.GATE_PASSED,
                reason="no candidate reached the Chief review stage",
                evaluated_at=self.decision_at,
                metadata=metadata,
            )
        return GateResult.build(
            GateName.AI_INVOCATION,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason=f"Chief review {status}",
            evaluated_at=self.decision_at,
            metadata=metadata,
        )

    def _ai_result_gate(self, evidence: CandidateEvidence) -> GateResult:
        metadata = {
            "candidates_returned_by_ai": self.ai_stage.candidates_returned_by_ai,
        }
        if evidence.ai_item is not None:
            return GateResult.build(
                GateName.AI_RESULT,
                GateStatus.PASS,
                ReasonCode.GATE_PASSED,
                reason="Chief returned this candidate",
                evaluated_at=self.decision_at,
                metadata={
                    **metadata,
                    "ai_rank": evidence.ai_item.get("rank"),
                    "ai_decision": evidence.ai_item.get("decision"),
                },
            )
        if self.ai_stage.candidates_returned_by_ai <= 0:
            return GateResult.build(
                GateName.AI_RESULT,
                GateStatus.FAIL,
                ReasonCode.AI_RETURNED_NO_CANDIDATES,
                reason="Chief was consulted and returned no candidates",
                evaluated_at=self.decision_at,
                metadata=metadata,
            )
        return GateResult.build(
            GateName.AI_RESULT,
            GateStatus.FAIL,
            ReasonCode.AI_NOT_SELECTED,
            reason="Chief compared this candidate but omitted it from the result",
            evaluated_at=self.decision_at,
            metadata=metadata,
        )

    def _ai_confidence_gate(self, evidence: CandidateEvidence) -> GateResult:
        """Record the model's confidence as an observation, not a judgement.

        The recommendation gate immediately after this one is what actually
        applies the threshold. Recording confidence separately is what makes
        the distribution measurable before anyone proposes moving the bar.
        """
        item = evidence.ai_item or {}
        try:
            confidence = int(item.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0
        return GateResult.build(
            GateName.AI_CONFIDENCE,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason=f"Chief comparative review confidence {confidence}",
            measured_value=confidence,
            evaluated_at=self.decision_at,
            metadata={
                "ai_confidence": confidence,
                "ai_rank": item.get("rank"),
                "ai_risk_level": item.get("risk_level"),
                "calibrated_probability": False,
            },
        )

    # -- public API -----------------------------------------------------

    def evaluate(self, evidence: CandidateEvidence) -> AdmissionDecision:
        """Return the shadow admission decision for one candidate.

        Evaluation stops at the first FAIL or ERROR, so a candidate rejected at
        the margin gate never pays for a target or economic computation.
        """
        snapshot = evidence.snapshot
        direction = normalize_direction(getattr(snapshot, "trade_direction", "LONG"))
        symbol = str(getattr(snapshot, "symbol", "") or "").upper()
        pair = str(
            evidence.pair
            or getattr(snapshot, "primary_pair", None)
            or symbol
        ).upper()
        asset = str(getattr(snapshot, "underlying_asset", None) or symbol).upper()
        market_type = market_type_for(direction)

        results: list[GateResult] = [
            GateResult.build(
                GateName.CANDIDATE_CREATED,
                GateStatus.PASS,
                ReasonCode.CANDIDATE_ADMITTED,
                reason="directional candidate entered the shadow engine",
                evaluated_at=self.decision_at,
                metadata={"market_type": market_type},
            ),
            GateResult.build(
                GateName.DIRECTION_SELECTED,
                GateStatus.PASS,
                ReasonCode.GATE_PASSED,
                reason=f"direction {direction}",
                evaluated_at=self.decision_at,
                metadata={"direction": direction},
            ),
        ]

        def _finish() -> AdmissionDecision:
            outcome, gate, code, reason = terminal_attribution(results)
            return AdmissionDecision(
                candidate_id=opip_candidate_id(
                    episode_id=evidence.episode_id or "",
                    pair=pair,
                    direction=direction,
                    market_type=market_type,
                ),
                episode_id=evidence.episode_id,
                signal_id=evidence.signal_id,
                asset=asset,
                asset_display_name=evidence.asset_display_name,
                pair=pair,
                market_type=market_type,
                direction=direction,
                decided_at=self.decision_at.isoformat(),
                decision=outcome,
                first_terminal_gate=gate,
                terminal_reason_code=code,
                terminal_reason=reason,
                gate_results=tuple(results),
                counterfactual_eligible=(
                    outcome is DecisionOutcome.REJECTED
                    and any(
                        result.gate
                        in {
                            GateName.DETERMINISTIC_QUALITY,
                            GateName.TARGET_QUALITY,
                            GateName.ECONOMIC_QUALITY,
                        }
                        for result in results
                    )
                ),
                strategy_version=STRATEGY_VERSION,
                intelligence_version=INTELLIGENCE_VERSION,
                gate_policy_version=GATE_POLICY_VERSION,
            )

        def _step(result: GateResult) -> bool:
            results.append(result)
            return not result.is_terminal

        try:
            if not _step(evaluate_margin_gate(snapshot, evaluated_at=self.decision_at)):
                return _finish()
            if not _step(
                evaluate_execution_gate(snapshot, evaluated_at=self.decision_at)
            ):
                return _finish()
            _step(evaluate_cross_market_gate(snapshot, evaluated_at=self.decision_at))
            _step(evaluate_reference_gate(snapshot, evaluated_at=self.decision_at))
            _step(
                evaluate_market_intelligence_gate(
                    snapshot,
                    assessment=evidence.market_intelligence,
                    evaluated_at=self.decision_at,
                )
            )
            if not _step(
                evaluate_deterministic_quality_gate(
                    snapshot,
                    account_equity=self.account_equity,
                    evaluated_at=self.decision_at,
                )
            ):
                return _finish()

            results.append(
                GateResult.build(
                    GateName.AI_ELIGIBILITY,
                    GateStatus.PASS,
                    ReasonCode.GATE_PASSED,
                    reason="candidate is eligible for Chief review",
                    evaluated_at=self.decision_at,
                )
            )
            if not _step(self._ai_invocation_gate()):
                return _finish()
            if not _step(self._ai_result_gate(evidence)):
                return _finish()
            _step(self._ai_confidence_gate(evidence))

            item = dict(evidence.ai_item or {})
            if not _step(
                evaluate_recommendation_gate_item(item, evaluated_at=self.decision_at)
            ):
                return _finish()

            results.append(
                GateResult.build(
                    GateName.TRADE_QUALITY,
                    GateStatus.SKIPPED,
                    ReasonCode.TRADE_QUALITY_NOT_APPLICABLE_TO_SHADOW_ENGINE,
                    reason=(
                        "shadow engine does not reproduce the production "
                        "trade-quality monitor; the live observer captures "
                        "its authoritative production verdict"
                    ),
                    evaluated_at=self.decision_at,
                )
            )

            risk_level = str(item.get("risk_level") or "medium").lower()
            plan = (
                build_entry_exit_plan(snapshot, risk_level, direction="SHORT")
                if direction == "SHORT"
                else build_entry_exit_plan(snapshot, risk_level)
            )
            if not _step(
                evaluate_target_quality_gate(
                    plan, snapshot, evaluated_at=self.decision_at
                )
            ):
                return _finish()
            if not _step(
                evaluate_economic_quality_gate(
                    plan,
                    snapshot,
                    account_equity=float(self.account_equity or 0.0),
                    evaluated_at=self.decision_at,
                )
            ):
                return _finish()

            results.append(
                GateResult.build(
                    GateName.CAPITAL_PORTFOLIO_GATE,
                    GateStatus.SKIPPED,
                    ReasonCode.CAPITAL_GATE_NOT_APPLICABLE_TO_SHADOW_ENGINE,
                    reason=(
                        "shadow engine has no capital allocation authority; "
                        "production verdict is captured by the live observer"
                    ),
                    evaluated_at=self.decision_at,
                )
            )

            results.append(
                GateResult.build(
                    GateName.FINAL_QUALIFICATION,
                    GateStatus.PASS,
                    ReasonCode.QUALIFIED,
                    reason="candidate cleared every qualification gate",
                    evaluated_at=self.decision_at,
                )
            )
        except Exception as exc:  # pragma: no cover - defensive shadow guard
            results.append(
                GateResult.build(
                    GateName.FINAL_QUALIFICATION,
                    GateStatus.ERROR,
                    ReasonCode.GATE_EVALUATION_ERROR,
                    reason=f"{type(exc).__name__}: {exc}",
                    evaluated_at=self.decision_at,
                )
            )
        return _finish()
