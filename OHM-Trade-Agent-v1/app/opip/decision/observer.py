"""Fail-soft façade that the live scan drives to record the O'Pip funnel.

The observer exists so ``app.jobs.scan_opportunities`` gains instrumentation
call sites rather than instrumentation logic. Every public method is a thin,
exception-swallowing recorder: an observer failure prints one line and the scan
continues unchanged.

Two distinct things happen here, and keeping them distinct is the point:

* the **funnel** records what the legacy production path actually did, reusing
  the results production already computed;
* the **shadow engine** independently re-derives the decision from the same
  evidence, and the two are compared.

If the observer instead fed the engine's own answers back into the funnel, the
comparison would be a tautology and would prove nothing about equivalence.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from app.opip.decision.comparison import (
    LEGACY_OPERATIONAL_FAILURE,
    LEGACY_QUALIFIED,
    LEGACY_REJECTED,
    compare_candidate,
)
from app.opip.decision.engine import CandidateEvidence, OPipDecisionEngine
from app.opip.decision.funnel import (
    AI_BUDGET_BLOCKED,
    AI_CACHE_REUSED,
    AI_FAILED,
    AI_NOT_REACHED,
    AI_SKIPPED_NO_ELIGIBLE,
    AI_SUCCEEDED,
    QualificationFunnel,
)
from app.opip.decision.gates import (
    economic_quality_gate_from_result,
    evaluate_cross_market_gate,
    evaluate_execution_gate,
    evaluate_margin_gate,
    evaluate_market_intelligence_gate,
    evaluate_recommendation_gate_item,
    evaluate_reference_gate,
    target_quality_gate_from_result,
)
from app.opip.decision.identity import (
    candidate_key,
    normalize_direction,
    opip_scan_id,
)
from app.opip.decision.models import (
    GateName,
    GateResult,
    GateStatus,
    ReasonCode,
)
from app.opip.decision.store import (
    append_funnel_events,
    append_scan_summary,
    opip_funnel_telemetry_enabled,
)
from app.opip.decision.summary import build_scan_summary, render_scan_summary_text
from app.services.canonical_episode_capture import (
    canonical_cohort_id,
    canonical_episode_id,
)


logger = logging.getLogger(__name__)


_AI_STATUS_MAP = {
    "SKIPPED_NO_ELIGIBLE_CANDIDATES": AI_SKIPPED_NO_ELIGIBLE,
    "BUDGET_BLOCKED": AI_BUDGET_BLOCKED,
    "CACHE_REUSED": AI_CACHE_REUSED,
    "FAILED": AI_FAILED,
    "SUCCEEDED": AI_SUCCEEDED,
}


class OPipScanObserver:
    """Records the qualification funnel for one live opportunity scan."""

    def __init__(
        self,
        *,
        snapshots: Sequence[Any],
        decision_at: datetime,
        account_equity: float | None,
        telemetry_enabled: bool | None = None,
    ) -> None:
        self.decision_at = decision_at
        self.account_equity = account_equity
        self.telemetry_enabled = (
            opip_funnel_telemetry_enabled()
            if telemetry_enabled is None
            else bool(telemetry_enabled)
        )
        self._snapshots = list(snapshots)
        self._by_key: dict[tuple[str, str], Any] = {}
        self._ai_items: dict[tuple[str, str], dict[str, Any]] = {}
        self._degraded = False

        try:
            cohort_id = canonical_cohort_id(self._snapshots, decision_at=decision_at)
        except Exception:
            cohort_id = None
        self.cohort_id = cohort_id
        self.funnel = QualificationFunnel(
            scan_id=opip_scan_id(
                cohort_id=cohort_id or "NO_COHORT",
                decision_at=decision_at,
            ),
            decision_at=decision_at,
            cohort_id=cohort_id,
        )

    # -- internals ------------------------------------------------------

    def _degrade(self, stage: str, exc: Exception) -> None:
        """Record that instrumentation lost a stage, without touching the scan."""
        self._degraded = True
        logger.warning(
            "O'Pip funnel stage %s failed open: %s", stage, type(exc).__name__
        )

    @staticmethod
    def _evaluated_at() -> datetime:
        """Timestamp instrumentation when a production stage actually completes."""
        return datetime.now(timezone.utc)

    def _completed(self, result: GateResult) -> GateResult:
        """Stamp a translated gate only after its observer evaluation completes."""
        return replace(result, evaluated_at=self._evaluated_at().isoformat())

    def _evidence_at(self, value: Any) -> datetime:
        """Use a source-stage timestamp when present, otherwise fail soft to now."""
        if value:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except (TypeError, ValueError):
                parsed = None
            if parsed is not None:
                if parsed.tzinfo is None or parsed.utcoffset() is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return parsed.astimezone(timezone.utc)
        return self._evaluated_at()

    def _episode_id(self, symbol: str) -> str | None:
        try:
            return canonical_episode_id(
                self._snapshots,
                decision_at=self.decision_at,
                symbol=symbol,
            )
        except Exception:
            return None

    def _remember(self, candidate: Any) -> tuple[str, str]:
        key = candidate_key(
            getattr(candidate, "symbol", ""),
            getattr(candidate, "trade_direction", "LONG"),
        )
        self._by_key[key] = candidate
        return key

    def _record(self, candidate: Any, result: GateResult) -> None:
        self.funnel.record(
            getattr(candidate, "symbol", ""),
            getattr(candidate, "trade_direction", "LONG"),
            result,
        )

    def _reject(self, candidate: Any, reason: str) -> None:
        self.funnel.record_legacy_outcome(
            getattr(candidate, "symbol", ""),
            getattr(candidate, "trade_direction", "LONG"),
            decision=LEGACY_REJECTED,
            terminal_reason=reason,
        )

    # -- stage hooks ----------------------------------------------------

    def register_candidates(self, candidates: Iterable[Any]) -> None:
        """Admit the directional shortlist into the funnel."""
        try:
            for candidate in candidates:
                symbol = str(getattr(candidate, "symbol", "") or "")
                direction = normalize_direction(
                    getattr(candidate, "trade_direction", "LONG")
                )
                self.funnel.register(
                    symbol=symbol,
                    direction=direction,
                    asset=getattr(candidate, "underlying_asset", None) or symbol,
                    pair=getattr(candidate, "primary_pair", None) or symbol,
                    episode_id=self._episode_id(symbol),
                )
                self._remember(candidate)
        except Exception as exc:
            self._degrade("register_candidates", exc)

    def record_margin(self, candidates: Iterable[Any]) -> None:
        try:
            for candidate in candidates:
                self._remember(candidate)
                result = self._completed(
                    evaluate_margin_gate(candidate, evaluated_at=self.decision_at)
                )
                self._record(candidate, result)
                if result.is_terminal:
                    self._reject(candidate, result.reason)
        except Exception as exc:
            self._degrade("record_margin", exc)

    def record_cross_market(self, candidates: Iterable[Any]) -> None:
        try:
            for candidate in candidates:
                self._remember(candidate)
                result = self._completed(
                    evaluate_cross_market_gate(
                        candidate,
                        evaluated_at=self.decision_at,
                    )
                )
                self._record(candidate, result)
        except Exception as exc:
            self._degrade("record_cross_market", exc)

    def record_execution(self, candidates: Iterable[Any]) -> None:
        """Record structural and short-quality execution validation.

        Called with the pre-filter candidate list after production has already
        attached execution evidence, so the gate reads that evidence rather
        than issuing its own exchange request.
        """
        try:
            for candidate in candidates:
                self._remember(candidate)
                result = self._completed(
                    evaluate_execution_gate(
                        candidate,
                        evaluated_at=self.decision_at,
                    )
                )
                self._record(candidate, result)
                if result.is_terminal:
                    self._reject(candidate, result.reason)
        except Exception as exc:
            self._degrade("record_execution", exc)

    def record_reference(self, candidates: Iterable[Any]) -> None:
        try:
            for candidate in candidates:
                self._remember(candidate)
                result = self._completed(
                    evaluate_reference_gate(
                        candidate,
                        evaluated_at=self.decision_at,
                    )
                )
                self._record(candidate, result)
        except Exception as exc:
            self._degrade("record_reference", exc)

    def record_market_intelligence(
        self,
        candidates: Iterable[Any],
        assessments: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            lookup = assessments or {}
            for candidate in candidates:
                self._remember(candidate)
                result = self._completed(
                    evaluate_market_intelligence_gate(
                        candidate,
                        assessment=lookup.get(getattr(candidate, "symbol", "")),
                        evaluated_at=self.decision_at,
                    )
                )
                self._record(candidate, result)
        except Exception as exc:
            self._degrade("record_market_intelligence", exc)

    def record_ai_stage(self, review: Mapping[str, Any]) -> None:
        """Attribute the deterministic prefilter and the whole Chief stage.

        This is the stage the existing operator output cannot explain. The
        evidence comes from ``chief_analyst``, which now reports which
        candidates it dropped before the call, whether it called at all, and
        why not when it did not.
        """
        try:
            evidence = review.get("opip_stage_evidence") or {}
            stage = self.funnel.ai_stage
            stage.invocation_status = _AI_STATUS_MAP.get(
                str(evidence.get("invocation_status") or ""),
                AI_NOT_REACHED,
            )
            stage.failure_type = evidence.get("failure_type")
            stage.invoked_at = evidence.get("invoked_at")
            stage.model = evidence.get("model")
            stage.prompt_version = evidence.get("prompt_version")
            stage.eligible_candidates_before_ai = int(
                evidence.get("eligible_candidate_count") or 0
            )
            returned = review.get("top_candidates") or []
            stage.candidates_returned_by_ai = int(
                evidence.get("returned_candidate_count") or len(returned)
            )

            self._record_prefilter(evidence.get("prefiltered") or [])
            self._record_ai_eligibility(
                evidence.get("eligible") or [],
                invocation_completed_at=evidence.get("completed_at"),
            )
            self._record_ai_outcome(returned)
        except Exception as exc:
            self._degrade("record_ai_stage", exc)

    def _record_prefilter(self, prefiltered: Sequence[Mapping[str, Any]]) -> None:
        for row in prefiltered:
            symbol = str(row.get("symbol") or "")
            direction = normalize_direction(row.get("direction"))
            result = GateResult.build(
                GateName.DETERMINISTIC_QUALITY,
                GateStatus.FAIL,
                ReasonCode.DETERMINISTIC_VIABILITY_FAILED,
                reason=str(row.get("reason") or "deterministic prefilter rejected"),
                measured_value=row.get("binding_measured"),
                threshold=row.get("binding_threshold"),
                higher_is_better=bool(row.get("binding_higher_is_better", True)),
                metadata={
                    "binding_metric": row.get("binding_metric"),
                    "binding_higher_is_better": bool(
                        row.get("binding_higher_is_better", True)
                    ),
                    "best_target_quality_score": row.get("best_target_quality_score"),
                    "best_economic_net_profit": row.get("best_economic_net_profit"),
                    "target_qualified_any": bool(row.get("target_qualified_any")),
                    "economic_qualified_any": bool(row.get("economic_qualified_any")),
                    "risk_levels": (
                        dict(row.get("risk_levels"))
                        if isinstance(row.get("risk_levels"), Mapping)
                        else {}
                    ),
                    "measurement_only": True,
                    "affects_trade_authority": False,
                },
                evaluated_at=self._evidence_at(row.get("evaluated_at")),
            )
            self.funnel.record(symbol, direction, result)
            self.funnel.record_legacy_outcome(
                symbol,
                direction,
                decision=LEGACY_REJECTED,
                terminal_reason=result.reason,
            )

    def _record_ai_eligibility(
        self,
        eligible: Sequence[Mapping[str, Any]],
        *,
        invocation_completed_at: Any = None,
    ) -> None:
        stage = self.funnel.ai_stage
        invocation_at = self._evidence_at(invocation_completed_at)
        for row in eligible:
            symbol = str(row.get("symbol") or "")
            direction = normalize_direction(row.get("direction"))
            eligible_at = self._evidence_at(row.get("evaluated_at"))
            self.funnel.record(
                symbol,
                direction,
                GateResult.build(
                    GateName.DETERMINISTIC_QUALITY,
                    GateStatus.PASS,
                    ReasonCode.GATE_PASSED,
                    reason="cleared the deterministic viability prefilter",
                    evaluated_at=eligible_at,
                ),
            )
            self.funnel.record(
                symbol,
                direction,
                GateResult.build(
                    GateName.AI_ELIGIBILITY,
                    GateStatus.PASS,
                    ReasonCode.GATE_PASSED,
                    reason="candidate was submitted for Chief review",
                    evaluated_at=eligible_at,
                ),
            )

            invocation = self._ai_invocation_result(evaluated_at=invocation_at)
            self.funnel.record(symbol, direction, invocation)
            if invocation.is_terminal:
                self.funnel.record_legacy_outcome(
                    symbol,
                    direction,
                    decision=(
                        LEGACY_OPERATIONAL_FAILURE
                        if stage.unavailable
                        else LEGACY_REJECTED
                    ),
                    terminal_reason=invocation.reason,
                )

    def _ai_invocation_result(self, *, evaluated_at: datetime) -> GateResult:
        stage = self.funnel.ai_stage
        metadata = {
            "invocation_status": stage.invocation_status,
            "failure_type": stage.failure_type,
            "model": stage.model,
            "prompt_version": stage.prompt_version,
            "eligible_candidates_before_ai": stage.eligible_candidates_before_ai,
            "candidates_returned_by_ai": stage.candidates_returned_by_ai,
        }
        if stage.invocation_status == AI_BUDGET_BLOCKED:
            return GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_BUDGET_LIMIT,
                reason="Chief review suppressed by the daily budget guard",
                evaluated_at=evaluated_at,
                metadata=metadata,
            )
        if stage.invocation_status == AI_FAILED:
            return GateResult.build(
                GateName.AI_INVOCATION,
                GateStatus.FAIL,
                ReasonCode.AI_SERVICE_UNAVAILABLE,
                reason=f"Chief unavailable: {stage.failure_type or 'unknown'}",
                evaluated_at=evaluated_at,
                metadata=metadata,
            )
        return GateResult.build(
            GateName.AI_INVOCATION,
            GateStatus.PASS,
            ReasonCode.GATE_PASSED,
            reason=f"Chief review {stage.invocation_status}",
            evaluated_at=evaluated_at,
            metadata=metadata,
        )

    def _record_ai_outcome(self, returned: Sequence[Mapping[str, Any]]) -> None:
        stage = self.funnel.ai_stage
        if stage.invocation_status in {
            AI_BUDGET_BLOCKED,
            AI_FAILED,
            AI_NOT_REACHED,
            AI_SKIPPED_NO_ELIGIBLE,
        }:
            return

        returned_keys: set[tuple[str, str]] = set()
        for item in returned:
            if not isinstance(item, Mapping):
                continue
            symbol = str(item.get("symbol") or "")
            direction = normalize_direction(item.get("direction"))
            key = candidate_key(symbol, direction)
            returned_keys.add(key)
            self._ai_items[key] = dict(item)
            try:
                stage.confidences.append(int(item.get("confidence", 0)))
            except (TypeError, ValueError):
                stage.confidences.append(0)

            self.funnel.record(
                symbol,
                direction,
                GateResult.build(
                    GateName.AI_RESULT,
                    GateStatus.PASS,
                    ReasonCode.GATE_PASSED,
                    reason="Chief returned this candidate",
                    evaluated_at=self._evaluated_at(),
                    metadata={"ai_rank": item.get("rank")},
                ),
            )
            self.funnel.record(
                symbol,
                direction,
                GateResult.build(
                    GateName.AI_CONFIDENCE,
                    GateStatus.PASS,
                    ReasonCode.GATE_PASSED,
                    reason="Chief comparative review confidence recorded",
                    measured_value=item.get("confidence"),
                    evaluated_at=self._evaluated_at(),
                    metadata={
                        "ai_confidence": item.get("confidence"),
                        "ai_rank": item.get("rank"),
                        "ai_risk_level": item.get("risk_level"),
                        "ai_decision": item.get("decision"),
                        "calibrated_probability": False,
                    },
                ),
            )
            gate = self._completed(
                evaluate_recommendation_gate_item(
                    dict(item),
                    evaluated_at=self.decision_at,
                )
            )
            self.funnel.record(symbol, direction, gate)
            if gate.is_terminal:
                self.funnel.record_legacy_outcome(
                    symbol,
                    direction,
                    decision=LEGACY_REJECTED,
                    terminal_reason=gate.reason,
                )

        no_candidates = not returned_keys
        for state in self.funnel.candidates:
            if state.key in returned_keys or state.legacy_decision is not None:
                continue
            if not any(
                result.gate is GateName.AI_ELIGIBILITY
                for result in state.gate_results
            ):
                continue
            code = (
                ReasonCode.AI_RETURNED_NO_CANDIDATES
                if no_candidates
                else ReasonCode.AI_NOT_SELECTED
            )
            reason = (
                "Chief was consulted and returned no candidates"
                if no_candidates
                else "Chief compared this candidate but omitted it from the result"
            )
            result = GateResult.build(
                GateName.AI_RESULT,
                GateStatus.FAIL,
                code,
                reason=reason,
                evaluated_at=self._evaluated_at(),
                metadata={
                    "candidates_returned_by_ai": stage.candidates_returned_by_ai
                },
            )
            self.funnel.record(state.symbol, state.direction, result)
            self.funnel.record_legacy_outcome(
                state.symbol,
                state.direction,
                decision=LEGACY_REJECTED,
                terminal_reason=reason,
            )

    def record_snapshot_missing(self, symbol: str, direction: str) -> None:
        """Record an alert whose snapshot could not be resolved.

        Production skips these silently. Silently is exactly the problem: the
        candidate leaves the pipeline with no attribution at all.
        """
        try:
            self.funnel.record(
                symbol,
                direction,
                GateResult.build(
                    GateName.TARGET_QUALITY,
                    GateStatus.ERROR,
                    ReasonCode.SNAPSHOT_MISSING,
                    reason="snapshot for the alerted symbol/direction was not found",
                    evaluated_at=self._evaluated_at(),
                ),
            )
            self.funnel.record_legacy_outcome(
                symbol,
                direction,
                decision=LEGACY_OPERATIONAL_FAILURE,
                terminal_reason="snapshot missing",
            )
        except Exception as exc:
            self._degrade("record_snapshot_missing", exc)

    def record_trade_quality(
        self,
        snapshot: Any,
        *,
        actionable: bool | None,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        """Record the production trade-quality monitor as an explicit gate."""
        try:
            self._remember(snapshot)
            if actionable is None:
                status = GateStatus.ERROR
                code = ReasonCode.TRADE_QUALITY_UNAVAILABLE
            elif actionable:
                status = GateStatus.PASS
                code = ReasonCode.GATE_PASSED
            else:
                status = GateStatus.FAIL
                code = ReasonCode.TRADE_QUALITY_REJECTED
            gate = GateResult.build(
                GateName.TRADE_QUALITY,
                status,
                code,
                reason=reason,
                evaluated_at=self._evaluated_at(),
                metadata=metadata,
            )
            self._record(snapshot, gate)
            if gate.is_terminal:
                if actionable is None:
                    self.funnel.record_legacy_outcome(
                        getattr(snapshot, "symbol", ""),
                        getattr(snapshot, "trade_direction", "LONG"),
                        decision=LEGACY_OPERATIONAL_FAILURE,
                        terminal_reason=reason,
                    )
                else:
                    self._reject(snapshot, reason)
        except Exception as exc:
            self._degrade("record_trade_quality", exc)

    def record_target_quality(self, snapshot: Any, result: Any) -> None:
        """Record production's own target attainability verdict."""
        try:
            self._remember(snapshot)
            gate = self._completed(
                target_quality_gate_from_result(
                    result,
                    evaluated_at=self.decision_at,
                )
            )
            self._record(snapshot, gate)
            if gate.is_terminal:
                self._reject(snapshot, gate.reason)
        except Exception as exc:
            self._degrade("record_target_quality", exc)

    def record_economic_quality(self, snapshot: Any, result: Any) -> None:
        """Record production's own economic quality verdict."""
        try:
            self._remember(snapshot)
            gate = self._completed(
                economic_quality_gate_from_result(
                    result,
                    evaluated_at=self.decision_at,
                )
            )
            self._record(snapshot, gate)
            if gate.is_terminal:
                self._reject(snapshot, gate.reason)
        except Exception as exc:
            self._degrade("record_economic_quality", exc)

    def record_action_gate(
        self,
        ranked: Any,
        *,
        allowed: bool | None,
        reason: str,
    ) -> None:
        """Capture the production capital/portfolio authority verbatim.

        ``None`` means the authority could not be evaluated and is operational,
        never a policy rejection.  This hook remains fail-soft like every other
        observer method and cannot change the gate's production verdict.
        """
        try:
            snapshot = ranked.opportunity.snapshot
            if allowed is None:
                status = GateStatus.ERROR
                code = ReasonCode.GATE_EVALUATION_ERROR
            elif allowed:
                status = GateStatus.PASS
                code = ReasonCode.GATE_PASSED
            else:
                status = GateStatus.FAIL
                code = ReasonCode.NO_CAPITAL
            result = GateResult.build(
                GateName.CAPITAL_PORTFOLIO_GATE,
                status,
                code,
                reason=reason,
                evaluated_at=self._evaluated_at(),
                metadata={"profit_rank": getattr(ranked, "rank", None)},
            )
            self._record(snapshot, result)
            if result.is_terminal:
                if allowed is None:
                    self.funnel.record_legacy_outcome(
                        getattr(snapshot, "symbol", ""),
                        getattr(snapshot, "trade_direction", "LONG"),
                        decision=LEGACY_OPERATIONAL_FAILURE,
                        terminal_reason=reason,
                    )
                else:
                    self._reject(snapshot, reason)
        except Exception as exc:
            self._degrade("record_action_gate", exc)

    def record_qualified(self, ranked_opportunities: Iterable[Any]) -> None:
        """Record the candidates production actually qualified."""
        try:
            for ranked in ranked_opportunities:
                snapshot = ranked.opportunity.snapshot
                self._remember(snapshot)
                self._record(
                    snapshot,
                    GateResult.build(
                        GateName.FINAL_QUALIFICATION,
                        GateStatus.PASS,
                        ReasonCode.QUALIFIED,
                        reason="candidate cleared every production qualification gate",
                        measured_value=ranked.profit_ranking.total_score,
                        evaluated_at=self._evaluated_at(),
                        metadata={"profit_rank": ranked.rank},
                    ),
                )
                self.funnel.record_legacy_outcome(
                    getattr(snapshot, "symbol", ""),
                    getattr(snapshot, "trade_direction", "LONG"),
                    decision=LEGACY_QUALIFIED,
                    terminal_reason="qualified",
                )
                signal_id = ranked.opportunity.alert.get("signal_id")
                if signal_id:
                    self.funnel.attach_signal_id(
                        getattr(snapshot, "symbol", ""),
                        getattr(snapshot, "trade_direction", "LONG"),
                        str(signal_id),
                    )
        except Exception as exc:
            self._degrade("record_qualified", exc)

    def record_paper_admission_eligibility(
        self,
        ranked_opportunities: Iterable[Any],
        *,
        paper_enabled: bool,
    ) -> int:
        """Record paper admission *eligibility* only.

        Build 1 does not admit anything. The authoritative paper engine is spot
        LONG only, so a qualified SHORT is recorded as ineligible rather than
        being silently absent.
        """
        eligible = 0
        try:
            for ranked in ranked_opportunities:
                snapshot = ranked.opportunity.snapshot
                direction = normalize_direction(
                    getattr(snapshot, "trade_direction", "LONG")
                )
                if direction != "LONG":
                    code = ReasonCode.PAPER_ENGINE_DIRECTION_UNSUPPORTED
                    reason = "v1 authoritative paper engine is spot LONG only"
                elif not paper_enabled:
                    code = ReasonCode.PAPER_ENGINE_DISABLED
                    reason = "paper trading is switched off"
                else:
                    code = ReasonCode.PAPER_ADMISSION_ELIGIBLE
                    reason = "eligible for authoritative paper admission"
                    eligible += 1
                self._record(
                    snapshot,
                    GateResult.build(
                        GateName.PAPER_ADMISSION_ELIGIBILITY,
                        GateStatus.PASS
                        if code is ReasonCode.PAPER_ADMISSION_ELIGIBLE
                        else GateStatus.SKIPPED,
                        code,
                        reason=reason,
                        evaluated_at=self._evaluated_at(),
                        metadata={"paper_enabled": bool(paper_enabled)},
                    ),
                )
        except Exception as exc:
            self._degrade("record_paper_admission_eligibility", exc)
        return eligible

    # -- shadow comparison and finalisation ------------------------------

    def run_shadow_engine(self) -> list[dict[str, Any]]:
        """Evaluate every candidate with the O'Pip engine and compare.

        Read-only: the returned comparisons are recorded and nothing else. The
        engine's verdict never reaches an alert, a ranking, or a paper
        admission.
        """
        comparisons: list[dict[str, Any]] = []
        engine = OPipDecisionEngine(
            account_equity=self.account_equity,
            decision_at=self.decision_at,
            ai_stage=self.funnel.ai_stage,
        )
        legacy_decisions = {
            state.key: state.to_decision(decided_at=self.funnel.decision_at_iso)
            for state in self.funnel.candidates
        }
        for state in self.funnel.candidates:
            snapshot = self._by_key.get(state.key)
            if snapshot is None:
                continue
            try:
                shadow = engine.evaluate(
                    CandidateEvidence(
                        snapshot=snapshot,
                        episode_id=state.episode_id,
                        signal_id=state.signal_id,
                        asset_display_name=state.asset_display_name,
                        pair=state.pair,
                        ai_item=self._ai_items.get(state.key),
                    )
                )
            except Exception as exc:
                self._degrade(f"shadow_engine[{state.symbol}]", exc)
                continue
            legacy = legacy_decisions.get(state.key)
            comparisons.append(
                compare_candidate(
                    candidate_id=state.candidate_id,
                    asset=state.asset,
                    pair=state.pair,
                    direction=state.direction,
                    legacy_decision=state.legacy_decision,
                    legacy_terminal_reason=state.legacy_terminal_reason,
                    legacy_terminal_gate=(
                        legacy.first_terminal_gate.value
                        if legacy is not None and legacy.first_terminal_gate is not None
                        else None
                    ),
                    legacy_terminal_reason_code=(
                        legacy.terminal_reason_code.value
                        if legacy is not None
                        and legacy.terminal_reason_code is not None
                        else None
                    ),
                    shadow=shadow,
                )
            )
        return comparisons

    def finalize(
        self,
        *,
        scan_context: Mapping[str, Any] | None = None,
        paper_admission_eligible: int = 0,
        print_summary: bool = True,
    ) -> dict[str, Any]:
        """Close the funnel: compare, summarise, print, and persist.

        Returns the machine-readable summary. Never raises.
        """
        try:
            comparisons = self.run_shadow_engine()
        except Exception as exc:
            self._degrade("run_shadow_engine", exc)
            comparisons = []

        try:
            context = dict(scan_context or {})
            context["instrumentation_degraded"] = self._degraded
            summary = build_scan_summary(
                self.funnel,
                comparisons=comparisons,
                scan_context=context,
                paper_admission_eligible=paper_admission_eligible,
            )
        except Exception as exc:
            self._degrade("build_scan_summary", exc)
            return {}

        if print_summary:
            try:
                print("")
                print("===== O'PIP QUALIFICATION FUNNEL =====")
                print(render_scan_summary_text(summary))
            except Exception as exc:
                self._degrade("render_scan_summary_text", exc)

        try:
            append_funnel_events(
                self.funnel.funnel_events(),
                enabled=self.telemetry_enabled,
            )
            append_scan_summary(summary, enabled=self.telemetry_enabled)
        except Exception as exc:
            self._degrade("persist", exc)
        return summary


class NullScanObserver:
    """No-op observer used when funnel construction itself fails.

    Instrumentation must never be able to abort a scan, including at the point
    where it is created. Every hook is accepted and discarded so the call sites
    in ``scan_opportunities`` need no conditional guards.
    """

    telemetry_enabled = False

    def register_candidates(self, candidates: Iterable[Any]) -> None:
        return None

    def record_margin(self, candidates: Iterable[Any]) -> None:
        return None

    def record_cross_market(self, candidates: Iterable[Any]) -> None:
        return None

    def record_execution(self, candidates: Iterable[Any]) -> None:
        return None

    def record_reference(self, candidates: Iterable[Any]) -> None:
        return None

    def record_market_intelligence(
        self,
        candidates: Iterable[Any],
        assessments: Mapping[str, Any] | None = None,
    ) -> None:
        return None

    def record_ai_stage(self, review: Mapping[str, Any]) -> None:
        return None

    def record_snapshot_missing(self, symbol: str, direction: str) -> None:
        return None

    def record_trade_quality(
        self,
        snapshot: Any,
        *,
        actionable: bool | None,
        reason: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        return None

    def record_target_quality(self, snapshot: Any, result: Any) -> None:
        return None

    def record_economic_quality(self, snapshot: Any, result: Any) -> None:
        return None

    def record_action_gate(
        self,
        ranked: Any,
        *,
        allowed: bool | None,
        reason: str,
    ) -> None:
        return None

    def record_qualified(self, ranked_opportunities: Iterable[Any]) -> None:
        return None

    def record_paper_admission_eligibility(
        self,
        ranked_opportunities: Iterable[Any],
        *,
        paper_enabled: bool,
    ) -> int:
        return 0

    def finalize(
        self,
        *,
        scan_context: Mapping[str, Any] | None = None,
        paper_admission_eligible: int = 0,
        print_summary: bool = True,
    ) -> dict[str, Any]:
        return {}


def build_scan_observer(
    *,
    snapshots: Sequence[Any],
    decision_at: datetime,
    account_equity: float | None,
) -> "OPipScanObserver | NullScanObserver":
    """Return a scan observer, degrading to a no-op rather than raising."""
    try:
        return OPipScanObserver(
            snapshots=snapshots,
            decision_at=decision_at,
            account_equity=account_equity,
        )
    except Exception as exc:
        logger.warning(
            "O'Pip funnel observer unavailable; scan continues uninstrumented: %s",
            type(exc).__name__,
        )
        return NullScanObserver()
