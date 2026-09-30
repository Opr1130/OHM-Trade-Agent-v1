"""The pure v1 Feasibility & Safety seam (R3 F5).

One seam sits in front of the (not yet built) forecast. It evaluates one ACTIVE
F4 opportunity episode against three required pre-forecast checks in the live
hard-filter order:

* ``MARKET_DATA`` maps the existing market-data validation evidence.
* ``MARGIN_ELIGIBILITY`` reuses the thin margin adapter (SHORT-only).
* ``EXECUTION_LIQUIDITY`` reuses the thin execution adapter on its precomputed
  OFFLINE route (no exchange/network refresh).

It calls the existing vetoes and can abstain with ``INSUFFICIENT_EVIDENCE``.
Missing evidence is never favorable evidence: absent or unavailable required
evidence is ``INSUFFICIENT_EVIDENCE``, never ``FEASIBLE``. A present but
malformed required structure raises :class:`FeasibilityContractError` and is
never coerced into zero, default or pass. An explicit INELIGIBLE/INVALID result
is a policy ``VETO``; an evidence UNAVAILABLE/MISSING result is an abstention,
not a veto.

The seam is pure: it reads no clock, environment, filesystem, database or
network, and it observes no input outside the supplied arguments. ``evaluation_time``
is the only timing input, and every thin adapter is called with an explicit
``evaluated_at=evaluation_time`` so no ``GateResult`` default clock is ever read.

No new numeric threshold is introduced; the seam only re-maps existing evaluator
outcomes. It grants no production, admission, allocation, risk, paper, order or
funded authority.

SHADOW / NON-AUTHORITATIVE. This seam is a research artifact. It is not wired
into ``run_cycle`` or ``scan_opportunities``, it activates no Feature Bus, and it
writes no canonical evidence.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.opip.contracts import (
    FEASIBILITY_CHECK_ORDER,
    FeasibilityCheck,
    FeasibilityCheckName,
    FeasibilityCheckStatus,
    FeasibilityContractError,
    FeasibilityDecision,
    FeasibilityDisposition,
    FeasibilityPolicy,
    OpportunityEpisode,
    OpportunityLifecycleState,
    feasibility_decision_identity,
    feasibility_evidence_fingerprint,
    require_feasibility_utc,
)
from app.opip.decision.gates import evaluate_execution_gate, evaluate_margin_gate
from app.opip.decision.models import GateName, GateStatus, ReasonCode
from app.scanner import market_data_validation as market_data
from app.scanner import execution_validation as execution_evidence
from app.scanner.models import MarketSnapshot

#: The live absent-evidence sentinel the scanner prints when a snapshot carries
#: no market-data validation object. It is an explicit unavailability marker, not
#: a favorable result.
MARKET_DATA_UNAVAILABLE_SENTINEL = "UNAVAILABLE"

#: The exact SHORT margin status tokens the live scanner attaches in
#: ``app/scanner/margin_eligibility.py`` (``margin_validation_status``). Reused,
#: not re-invented.
MARGIN_ELIGIBLE = "ELIGIBLE"
MARGIN_INELIGIBLE = "INELIGIBLE"
MARGIN_UNAVAILABLE = "UNAVAILABLE"

_LONG = "LONG"
_SHORT = "SHORT"


def _require_policy(policy: object) -> FeasibilityPolicy:
    if not isinstance(policy, FeasibilityPolicy):
        raise FeasibilityContractError("policy must be a FeasibilityPolicy")
    return policy


def _require_episode(episode: object) -> OpportunityEpisode:
    """F5 evaluates exactly one ACTIVE F4 episode; any other state fails closed."""
    if not isinstance(episode, OpportunityEpisode):
        raise FeasibilityContractError(
            "episode must be an ACTIVE F4 OpportunityEpisode"
        )
    if episode.lifecycle_state is not OpportunityLifecycleState.ACTIVE:
        raise FeasibilityContractError(
            "F5 evaluates only an ACTIVE F4 episode; DEFERRED and TERMINAL fail closed"
        )
    return episode


def _require_snapshot(evidence: object) -> MarketSnapshot:
    if not isinstance(evidence, MarketSnapshot):
        raise FeasibilityContractError(
            "evidence must be a MarketSnapshot candidate carrying the "
            "pre-forecast feasibility inputs"
        )
    return evidence


def _direction(snapshot: MarketSnapshot) -> str:
    raw = getattr(snapshot, "trade_direction", None)
    if not isinstance(raw, str) or raw != raw.strip():
        raise FeasibilityContractError(
            "trade_direction must be a non-empty, whitespace-free string"
        )
    token = raw.upper()
    if token not in {_LONG, _SHORT}:
        raise FeasibilityContractError(
            f"trade_direction has an unsupported token: {raw!r}"
        )
    return token


def _text_or_none(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FeasibilityContractError(f"{field_name} must be text or None")
    return value


def _number_or_none(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FeasibilityContractError(f"{field_name} must be numeric or None")
    number = float(value)
    if not math.isfinite(number):
        raise FeasibilityContractError(f"{field_name} must be finite")
    return number


def _bool_or_none(value: Any, *, field_name: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise FeasibilityContractError(f"{field_name} must be a bool or None")
    return value


def _string_list_or_none(value: Any, *, field_name: str) -> tuple[str, ...] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)):
        raise FeasibilityContractError(f"{field_name} must be a list of text or None")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise FeasibilityContractError(f"{field_name} must contain only text")
        items.append(item)
    return tuple(items)


def _canonical_evidence_summary(snapshot: MarketSnapshot) -> dict[str, Any]:
    """The normalized F5-required evidence inputs used for the fingerprint.

    Only well-typed primitives and primitive sequences are kept, so the
    fingerprint can never depend on an object repr and changes whenever a
    required input changes. A malformed required field fails closed here.
    """
    market = getattr(snapshot, "market_data_validation", None)
    if market is None:
        market_summary: dict[str, Any] | None = None
    else:
        market_summary = {
            "status": _text_or_none(
                getattr(market, "status", None), field_name="market.status"
            ),
            "qualified": _bool_or_none(
                getattr(market, "qualified", None), field_name="market.qualified"
            ),
            "rejection_reasons": _string_list_or_none(
                getattr(market, "rejection_reasons", None),
                field_name="market.rejection_reasons",
            ),
        }

    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        execution_summary: dict[str, Any] | None = None
    else:
        execution_summary = {
            "status": _text_or_none(
                getattr(execution, "status", None), field_name="execution.status"
            ),
            "book_coverage_status": _text_or_none(
                getattr(execution, "book_coverage_status", None),
                field_name="execution.book_coverage_status",
            ),
            "spread_bps": _number_or_none(
                getattr(execution, "spread_bps", None),
                field_name="execution.spread_bps",
            ),
            "buy_visible_coverage_pct": _number_or_none(
                getattr(execution, "buy_visible_coverage_pct", None),
                field_name="execution.buy_visible_coverage_pct",
            ),
            "sell_visible_coverage_pct": _number_or_none(
                getattr(execution, "sell_visible_coverage_pct", None),
                field_name="execution.sell_visible_coverage_pct",
            ),
            "buy_fully_covered": _bool_or_none(
                getattr(execution, "buy_fully_covered", None),
                field_name="execution.buy_fully_covered",
            ),
            "sell_fully_covered": _bool_or_none(
                getattr(execution, "sell_fully_covered", None),
                field_name="execution.sell_fully_covered",
            ),
            "short_round_trip_drag_pct": _number_or_none(
                getattr(
                    execution,
                    "estimated_visible_short_round_trip_market_drag_pct",
                    None,
                ),
                field_name="execution.short_round_trip_drag_pct",
            ),
            "recent_trade_status": _text_or_none(
                getattr(execution, "recent_trade_status", None),
                field_name="execution.recent_trade_status",
            ),
        }

    return {
        "direction": _direction(snapshot),
        "market": market_summary,
        "margin_status": _text_or_none(
            getattr(snapshot, "margin_validation_status", None),
            field_name="margin_status",
        ),
        "margin_eligible": _bool_or_none(
            getattr(snapshot, "margin_eligible", None), field_name="margin_eligible"
        ),
        "margin_venue_symbol": _text_or_none(
            getattr(snapshot, "margin_venue_symbol", None),
            field_name="margin_venue_symbol",
        ),
        "margin_max_leverage": _number_or_none(
            getattr(snapshot, "margin_max_leverage", None),
            field_name="margin_max_leverage",
        ),
        "execution": execution_summary,
    }


def _check(
    name: FeasibilityCheckName,
    status: FeasibilityCheckStatus,
    reason: str,
) -> FeasibilityCheck:
    return FeasibilityCheck(name=name, status=status, reason=reason)


def _market_check(snapshot: MarketSnapshot) -> FeasibilityCheck:
    """Map the existing market-data validation evidence (no new threshold)."""
    validation = getattr(snapshot, "market_data_validation", None)
    if validation is None:
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "required market-data evidence is absent",
        )

    status = getattr(validation, "status", None)
    qualified = getattr(validation, "qualified", None)
    if isinstance(status, bool) or not isinstance(status, str):
        raise FeasibilityContractError("market-data status must be a text token")
    if not isinstance(qualified, bool):
        raise FeasibilityContractError("market-data qualified must be a bool")

    if status == MARKET_DATA_UNAVAILABLE_SENTINEL:
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "market-data evidence is explicitly unavailable",
        )

    if status not in {
        market_data.PASS,
        market_data.WARN,
        market_data.REJECT,
    }:
        raise FeasibilityContractError(
            f"market-data status has an unsupported token: {status!r}"
        )

    if status == market_data.REJECT:
        if qualified:
            raise FeasibilityContractError(
                "contradictory market-data evidence: REJECT with qualified=True"
            )
        return _check(
            FeasibilityCheckName.MARKET_DATA,
            FeasibilityCheckStatus.VETO,
            "market-data validation rejected the candidate",
        )

    if not qualified:
        raise FeasibilityContractError(
            f"contradictory market-data evidence: {status} with qualified=False"
        )
    return _check(
        FeasibilityCheckName.MARKET_DATA,
        FeasibilityCheckStatus.PASS,
        f"market-data validation is usable ({status})",
    )


def _margin_check(
    snapshot: MarketSnapshot, evaluation_time: datetime
) -> FeasibilityCheck:
    """Reuse the thin margin adapter; LONG is NOT_APPLICABLE, never PASS."""
    direction = _direction(snapshot)
    if direction == _SHORT:
        raw = getattr(snapshot, "margin_validation_status", None)
        if isinstance(raw, bool) or not isinstance(raw, str):
            raise FeasibilityContractError(
                "SHORT margin_validation_status must be a text token"
            )
        if raw != raw.strip():
            raise FeasibilityContractError(
                "SHORT margin_validation_status must be whitespace-free"
            )
        if raw.upper() not in {
            MARGIN_ELIGIBLE,
            MARGIN_INELIGIBLE,
            MARGIN_UNAVAILABLE,
        }:
            raise FeasibilityContractError(
                f"SHORT margin_validation_status has an unsupported token: {raw!r}"
            )

    result = evaluate_margin_gate(snapshot, evaluated_at=evaluation_time)

    if result.gate is not GateName.MARGIN_ELIGIBILITY:
        raise FeasibilityContractError("margin adapter returned an unexpected gate")

    if result.status is GateStatus.SKIPPED:
        return _check(
            FeasibilityCheckName.MARGIN_ELIGIBILITY,
            FeasibilityCheckStatus.NOT_APPLICABLE,
            "LONG candidate does not use the margin venue",
        )
    if result.status is GateStatus.PASS:
        return _check(
            FeasibilityCheckName.MARGIN_ELIGIBILITY,
            FeasibilityCheckStatus.PASS,
            "SHORT candidate is margin eligible",
        )
    if result.status is GateStatus.FAIL:
        if result.reason_code is ReasonCode.MARGIN_VALIDATION_UNAVAILABLE:
            return _check(
                FeasibilityCheckName.MARGIN_ELIGIBILITY,
                FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
                "SHORT margin eligibility discovery is unavailable",
            )
        if result.reason_code is ReasonCode.MARGIN_INELIGIBLE:
            return _check(
                FeasibilityCheckName.MARGIN_ELIGIBILITY,
                FeasibilityCheckStatus.VETO,
                "SHORT candidate is margin ineligible",
            )
    raise FeasibilityContractError(
        "margin adapter returned an unrecognized outcome"
    )


def _validate_execution_fields(execution: Any) -> None:
    """Validate the F5-required execution fields; malformed fails closed."""
    status = getattr(execution, "status", None)
    if isinstance(status, bool) or not isinstance(status, str):
        raise FeasibilityContractError("execution status must be a text token")
    if status not in {execution_evidence.VALID, execution_evidence.INVALID,
                      execution_evidence.UNAVAILABLE}:
        raise FeasibilityContractError(
            f"execution status has an unsupported token: {status!r}"
        )
    _text_or_none(
        getattr(execution, "book_coverage_status", None),
        field_name="execution.book_coverage_status",
    )
    _bool_or_none(
        getattr(execution, "buy_fully_covered", None),
        field_name="execution.buy_fully_covered",
    )
    _bool_or_none(
        getattr(execution, "sell_fully_covered", None),
        field_name="execution.sell_fully_covered",
    )
    for field_name in (
        "spread_bps",
        "buy_visible_coverage_pct",
        "sell_visible_coverage_pct",
        "estimated_visible_short_round_trip_market_drag_pct",
    ):
        _number_or_none(
            getattr(execution, field_name, None), field_name=f"execution.{field_name}"
        )


def _execution_check(
    snapshot: MarketSnapshot, evaluation_time: datetime
) -> FeasibilityCheck:
    """Reuse the thin execution adapter on its OFFLINE route (no refresh)."""
    execution = getattr(snapshot, "execution_validation", None)
    if execution is None:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "required execution validation evidence is absent",
        )

    _validate_execution_fields(execution)
    status = getattr(execution, "status", None)
    if status == execution_evidence.UNAVAILABLE:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "execution validation evidence is explicitly unavailable",
        )

    result = evaluate_execution_gate(snapshot, evaluated_at=evaluation_time)

    if result.gate is not GateName.EXECUTION_VALIDATION:
        raise FeasibilityContractError("execution adapter returned an unexpected gate")

    if result.status is GateStatus.PASS:
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.PASS,
            f"execution evidence is usable ({status})",
        )
    if result.status is GateStatus.FAIL:
        if result.reason_code is ReasonCode.EXECUTION_VALIDATION_FAILED:
            return _check(
                FeasibilityCheckName.EXECUTION_LIQUIDITY,
                FeasibilityCheckStatus.VETO,
                "structural execution validation returned INVALID",
            )
        if result.reason_code is ReasonCode.SHORT_EXECUTION_QUALITY_FAILED:
            return _check(
                FeasibilityCheckName.EXECUTION_LIQUIDITY,
                FeasibilityCheckStatus.VETO,
                "SHORT execution-quality evaluator rejected the candidate",
            )
    if result.status is GateStatus.ERROR and (
        result.reason_code is ReasonCode.GATE_EVALUATION_ERROR
    ):
        return _check(
            FeasibilityCheckName.EXECUTION_LIQUIDITY,
            FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE,
            "execution validation evidence is missing",
        )
    raise FeasibilityContractError(
        "execution adapter returned an unrecognized outcome"
    )


def _run_check(
    name: FeasibilityCheckName,
    snapshot: MarketSnapshot,
    evaluation_time: datetime,
) -> FeasibilityCheck:
    if name is FeasibilityCheckName.MARKET_DATA:
        return _market_check(snapshot)
    if name is FeasibilityCheckName.MARGIN_ELIGIBILITY:
        return _margin_check(snapshot, evaluation_time)
    if name is FeasibilityCheckName.EXECUTION_LIQUIDITY:
        return _execution_check(snapshot, evaluation_time)
    raise FeasibilityContractError(f"unknown feasibility check: {name!r}")


def evaluate_feasibility(
    episode: OpportunityEpisode,
    evidence: MarketSnapshot,
    evaluation_time: datetime,
    policy: FeasibilityPolicy,
) -> FeasibilityDecision:
    """Evaluate one ACTIVE F4 episode against the F5 required checks. Pure.

    Deterministic sequential aggregation in the recorded live hard-filter order.
    An evaluated hard ``VETO`` yields overall ``VETO`` and short-circuits the
    remaining checks, so a proven veto is never downgraded to an abstention.
    Otherwise any ``INSUFFICIENT_EVIDENCE`` yields an abstention; otherwise all
    applicable checks are ``PASS`` and the episode is ``FEASIBLE``.
    ``NOT_APPLICABLE`` never blocks.
    """
    policy = _require_policy(policy)
    evaluation_time = require_feasibility_utc(
        evaluation_time, field_name="evaluation_time"
    )
    episode = _require_episode(episode)
    snapshot = _require_snapshot(evidence)

    fingerprint = feasibility_evidence_fingerprint(
        _canonical_evidence_summary(snapshot)
    )

    evaluated: list[FeasibilityCheck] = []
    disposition = FeasibilityDisposition.FEASIBLE
    for name in FEASIBILITY_CHECK_ORDER:
        check = _run_check(name, snapshot, evaluation_time)
        evaluated.append(check)
        if check.status is FeasibilityCheckStatus.VETO:
            disposition = FeasibilityDisposition.VETO
            break
    else:
        if any(
            check.status is FeasibilityCheckStatus.INSUFFICIENT_EVIDENCE
            for check in evaluated
        ):
            disposition = FeasibilityDisposition.INSUFFICIENT_EVIDENCE

    decision_id = feasibility_decision_identity(
        decision_schema_version=policy.decision_schema_version,
        episode_id=episode.episode_id,
        evidence_fingerprint=fingerprint,
        evaluation_time=evaluation_time,
        feasibility_version=policy.feasibility_version,
        policy_version=policy.policy_version,
    )

    return FeasibilityDecision(
        decision_id=decision_id,
        decision_schema_version=policy.decision_schema_version,
        feasibility_version=policy.feasibility_version,
        policy_version=policy.policy_version,
        episode_id=episode.episode_id,
        source_claim_id=episode.source_claim_id,
        instrument_version_id=episode.instrument_version_id,
        venue_instrument_id=episode.venue_instrument_id,
        detector_snapshot_id=episode.snapshot_id,
        evaluation_time=evaluation_time,
        evidence_fingerprint=fingerprint,
        disposition=disposition,
        checks=tuple(evaluated),
    )


__all__ = [
    "MARGIN_ELIGIBLE",
    "MARGIN_INELIGIBLE",
    "MARGIN_UNAVAILABLE",
    "MARKET_DATA_UNAVAILABLE_SENTINEL",
    "evaluate_feasibility",
]
