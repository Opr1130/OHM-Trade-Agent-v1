"""PR-B/C-0 canonical event contracts for Paper v2 evidence.

This module freezes event names, parent-reference boundaries, identity fields,
and payload vocabulary before any producer or CanonicalWriter integration.

Important: importing this module does NOT register these events with the
canonical writer. Runtime emission/persistence starts only in B/C-1/B/C-2 after
their transaction and ancestry rules are implemented and reviewed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
    PAPER_PROTECTION_MODEL_VERSION,
    EvaluationPopulation,
    ExecutionState,
    PositionState,
    ProtectionState,
    QualifiedOpportunityDisposition,
    TemporalBasis,
    TemporalEvidence,
    TemporalPrecision,
    TerminalReconciliationState,
)
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES


PAPER_EXECUTION_STREAM = "paper_execution.v2"
PAPER_EXECUTION_PRIORITY = "LOW"

PAPER_OPPORTUNITY_DISPOSITION_RECORDED = (
    "paper_execution.opportunity_disposition.recorded"
)
PAPER_ORDER_INTENT_RECORDED = "paper_execution.order_intent.recorded"
PAPER_EXECUTION_ATTEMPT_RECORDED = "paper_execution.attempt.recorded"
PAPER_FILL_RECORDED = "paper_execution.fill.recorded"
PAPER_PROTECTION_PLAN_RECORDED = "paper_protection.plan.recorded"
PAPER_PROTECTION_STATE_RECORDED = "paper_protection.state.recorded"
PAPER_PROTECTION_TRIGGER_RECORDED = "paper_protection.trigger.recorded"
PAPER_RECONCILIATION_RECORDED = "paper_execution.reconciliation.recorded"

PAPER_EXECUTION_EVENT_TYPES = frozenset(
    {
        PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
        PAPER_ORDER_INTENT_RECORDED,
        PAPER_EXECUTION_ATTEMPT_RECORDED,
        PAPER_FILL_RECORDED,
        PAPER_PROTECTION_PLAN_RECORDED,
        PAPER_PROTECTION_STATE_RECORDED,
        PAPER_PROTECTION_TRIGGER_RECORDED,
        PAPER_RECONCILIATION_RECORDED,
    }
)


@dataclass(frozen=True)
class PaperEvidenceEventContract:
    event_type: str
    identity_field: str
    required_fields: frozenset[str]
    optional_fields: frozenset[str]
    parent_refs: tuple[str, ...]
    temporal_field: str

    @property
    def allowed_fields(self) -> frozenset[str]:
        return self.required_fields | self.optional_fields


_COMMON = frozenset({"schema_version", "engine"})

#: Reference fields naming another artifact. `reservation_id` and `paper_trade_id`
#: are already declared parent refs, so they are validated as ancestry instead.
_REFERENCE_FIELDS = frozenset({"market_evidence_ref"})

#: Tolerance for the aggregate protection-target allocation. Wide enough to absorb
#: binary float representation error, far too tight to admit a real over-allocation.
_TARGET_FRACTION_TOLERANCE = 1e-9

_EVENT_CONTRACTS = {
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
        identity_field="disposition_id",
        required_fields=_COMMON
        | frozenset(
            {
                "disposition_id",
                "decision_context_id",
                "disposition_seq",
                "disposition",
                "evaluation_population",
                "quote_currency",
                "requested_capital",
                "disposition_time",
                "reason_code",
            }
        ),
        optional_fields=frozenset(
            {
                "paper_trade_id",
                "reservation_id",
                "expected_portfolio_version",
                "capital_policy_version",
                "portfolio_equity_limit",
                "portfolio_position_limit",
                "requested_reservation_amount",
            }
        ),
        parent_refs=("decision_context_id",),
        temporal_field="disposition_time",
    ),
    PAPER_ORDER_INTENT_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_ORDER_INTENT_RECORDED,
        identity_field="order_intent_id",
        required_fields=_COMMON
        | frozenset(
            {
                "order_intent_id",
                "paper_trade_id",
                "decision_context_id",
                "intent_seq",
                "intent_role",
                "side",
                "order_type",
                "requested_quantity",
                "requested_notional",
                "reason_code",
                "intent_time",
                "execution_model_version",
                "reservation_id",
            }
        ),
        optional_fields=frozenset({"limit_price"}),
        # The admitting reservation is declared ancestry, not a free-floating
        # attribute: a writer must resolve the reservation created by the ADMITTED
        # disposition of the same decision context before this intent can be
        # accepted, so an intent cannot claim an arbitrary reservation.
        parent_refs=("decision_context_id", "paper_trade_id", "reservation_id"),
        temporal_field="intent_time",
    ),
    PAPER_EXECUTION_ATTEMPT_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_EXECUTION_ATTEMPT_RECORDED,
        identity_field="execution_attempt_id",
        required_fields=_COMMON
        | frozenset(
            {
                "execution_attempt_id",
                "order_intent_id",
                "paper_trade_id",
                "attempt_seq",
                "execution_state",
                "attempt_time",
                "execution_model_version",
            }
        ),
        optional_fields=frozenset(
            {
                "market_evidence_ref",
                "accepted_quantity",
                "rejection_reason",
            }
        ),
        parent_refs=("order_intent_id", "paper_trade_id"),
        temporal_field="attempt_time",
    ),
    PAPER_FILL_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_FILL_RECORDED,
        identity_field="fill_id",
        required_fields=_COMMON
        | frozenset(
            {
                "fill_id",
                "execution_attempt_id",
                "order_intent_id",
                "paper_trade_id",
                "fill_seq",
                "side",
                "quantity",
                "price",
                "fee_cost",
                "spread_cost",
                "slippage_cost",
                "other_supported_cost",
                "fill_time",
                "execution_model_version",
                "economic_model_version",
            }
        ),
        optional_fields=frozenset({"market_evidence_ref"}),
        parent_refs=("execution_attempt_id", "order_intent_id", "paper_trade_id"),
        temporal_field="fill_time",
    ),
    PAPER_PROTECTION_PLAN_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_PROTECTION_PLAN_RECORDED,
        identity_field="protection_plan_id",
        required_fields=_COMMON
        | frozenset(
            {
                "protection_plan_id",
                "paper_trade_id",
                "plan_seq",
                "stop_price",
                "targets",
                "max_hold_seconds",
                "plan_time",
                "protection_model_version",
            }
        ),
        optional_fields=frozenset(),
        parent_refs=("paper_trade_id",),
        temporal_field="plan_time",
    ),
    PAPER_PROTECTION_STATE_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_PROTECTION_STATE_RECORDED,
        identity_field="protection_event_id",
        required_fields=_COMMON
        | frozenset(
            {
                "protection_event_id",
                "protection_plan_id",
                "paper_trade_id",
                "state_seq",
                "from_state",
                "to_state",
                "reason_code",
                "state_time",
                "protection_model_version",
            }
        ),
        optional_fields=frozenset(),
        parent_refs=("protection_plan_id", "paper_trade_id"),
        temporal_field="state_time",
    ),
    PAPER_PROTECTION_TRIGGER_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_PROTECTION_TRIGGER_RECORDED,
        identity_field="protection_trigger_id",
        required_fields=_COMMON
        | frozenset(
            {
                "protection_trigger_id",
                "protection_plan_id",
                "paper_trade_id",
                "trigger_seq",
                "trigger_type",
                "reference_price",
                "trigger_time",
                "protection_model_version",
            }
        ),
        optional_fields=frozenset({"market_evidence_ref"}),
        parent_refs=("protection_plan_id", "paper_trade_id"),
        temporal_field="trigger_time",
    ),
    PAPER_RECONCILIATION_RECORDED: PaperEvidenceEventContract(
        event_type=PAPER_RECONCILIATION_RECORDED,
        identity_field="reconciliation_id",
        required_fields=_COMMON
        | frozenset(
            {
                "reconciliation_id",
                "paper_trade_id",
                "reconciliation_seq",
                "position_state",
                "terminal_reconciliation_state",
                "filled_entry_quantity",
                "filled_exit_quantity",
                "remaining_quantity",
                "reserved_capital",
                "realized_gross_pnl",
                "recorded_execution_costs",
                "realized_net_pnl",
                "reconciled_time",
                "economic_model_version",
            }
        ),
        optional_fields=frozenset({"unresolved_reason"}),
        parent_refs=("paper_trade_id",),
        temporal_field="reconciled_time",
    ),
}

PAPER_EXECUTION_EVENT_CONTRACTS: Mapping[str, PaperEvidenceEventContract] = (
    MappingProxyType(_EVENT_CONTRACTS)
)


def event_contract(event_type: str) -> PaperEvidenceEventContract:
    if not isinstance(event_type, str):
        raise ValueError("Paper v2 event type must be a canonical string")
    try:
        return PAPER_EXECUTION_EVENT_CONTRACTS[event_type]
    except KeyError as exc:
        raise ValueError(f"unsupported Paper v2 event type: {event_type}") from exc


def paper_evidence_idempotency_key(event_type: str, payload: Mapping[str, Any]) -> str:
    contract = event_contract(event_type)
    # Keyed from the same canonical contract validation enforces. Nothing is
    # stripped here: a normalizer that disagreed with validation would let two
    # different payload identities collapse into one key.
    identity = _require_canonical_identity(payload, contract.identity_field)
    return f"{event_type}:{identity}"


def _require_nonempty_string(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _require_canonical_identity(payload: Mapping[str, Any], field_name: str) -> str:
    """Require an identity/reference string in its exact canonical form.

    Identity and reference fields are never normalized. Validation and
    idempotency keying must agree on the identical value, so a padded identity is
    rejected rather than trimmed: trimming would let `" fill-1 "` and `"fill-1"`
    validate as two distinct payload identities while collapsing to a single
    idempotency key. Returning the value unchanged is what keeps the contract
    single-sourced between the two.
    """
    value = payload.get(field_name)
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    if value != value.strip():
        raise ValueError(
            f"{field_name} must not have leading or trailing whitespace"
        )
    if not value:
        raise ValueError(f"{field_name} must be a non-empty canonical string")
    return value


def _require_nonnegative_int(payload: Mapping[str, Any], field_name: str) -> int:
    value = payload.get(field_name)
    if type(value) is not int or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return value


def _require_finite_number(
    payload: Mapping[str, Any],
    field_name: str,
    *,
    nonnegative: bool = False,
) -> float:
    value = payload.get(field_name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{field_name} must be a finite number")
    if nonnegative and number < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return number


def _parse_iso(value: Any, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return parsed


def _validate_temporal(value: Any, *, field_name: str) -> None:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a temporal evidence object")

    precision_raw = value.get("precision")
    basis_raw = value.get("basis")
    try:
        precision = TemporalPrecision(str(precision_raw))
    except ValueError as exc:
        raise ValueError(f"{field_name}.precision is unsupported") from exc
    try:
        basis = TemporalBasis(str(basis_raw))
    except ValueError as exc:
        raise ValueError(f"{field_name}.basis is unsupported") from exc

    if precision is TemporalPrecision.EXACT:
        evidence = TemporalEvidence(
            precision=precision,
            basis=basis,
            occurred_at=_parse_iso(
                value.get("occurred_at"),
                field_name=f"{field_name}.occurred_at",
            ),
            reason=value.get("reason"),
        )
    elif precision is TemporalPrecision.BOUNDED:
        evidence = TemporalEvidence(
            precision=precision,
            basis=basis,
            window_start=_parse_iso(
                value.get("window_start"),
                field_name=f"{field_name}.window_start",
            ),
            window_end=_parse_iso(
                value.get("window_end"),
                field_name=f"{field_name}.window_end",
            ),
            reason=value.get("reason"),
        )
    else:
        evidence = TemporalEvidence(
            precision=precision,
            basis=basis,
            reason=value.get("reason"),
        )

    if evidence.as_dict() != dict(value):
        raise ValueError(
            f"{field_name} must use the canonical TemporalEvidence serialization"
        )


def _validate_common(
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("Paper v2 event payload must be a mapping")

    contract = event_contract(event_type)
    fields = frozenset(str(key) for key in payload)
    missing = sorted(contract.required_fields - fields)
    if missing:
        raise ValueError("Paper v2 event missing fields: " + ", ".join(missing))
    extra = sorted(fields - contract.allowed_fields)
    if extra:
        raise ValueError("Paper v2 event has unsupported fields: " + ", ".join(extra))

    if (
        type(payload.get("schema_version")) is not int
        or payload["schema_version"] != PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported Paper v2 event schema version")

    if payload.get("engine") != ENGINE_OPIP_PAPER_V2:
        raise ValueError("Paper v2 event engine must be OPIP_PAPER_V2")

    _require_canonical_identity(payload, contract.identity_field)
    for parent_ref in contract.parent_refs:
        _require_canonical_identity(payload, parent_ref)
    for reference_field in sorted(_REFERENCE_FIELDS):
        if payload.get(reference_field) is not None:
            _require_canonical_identity(payload, reference_field)
    _validate_temporal(payload.get(contract.temporal_field), field_name=contract.temporal_field)

    normalized = dict(payload)
    return normalized


def validate_paper_evidence_payload(
    event_type: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = _validate_common(event_type, payload)

    if event_type == PAPER_OPPORTUNITY_DISPOSITION_RECORDED:
        _require_nonnegative_int(normalized, "disposition_seq")
        try:
            disposition = QualifiedOpportunityDisposition(str(normalized["disposition"]))
        except ValueError as exc:
            raise ValueError("unsupported opportunity disposition") from exc
        try:
            population = EvaluationPopulation(str(normalized["evaluation_population"]))
        except ValueError as exc:
            raise ValueError("unsupported evaluation population") from exc
        quote_currency = _require_nonempty_string(normalized, "quote_currency")
        if quote_currency not in QUOTE_CURRENCIES:
            raise ValueError("unsupported quote_currency")
        _require_nonempty_string(normalized, "reason_code")
        requested_capital = _require_finite_number(
            normalized, "requested_capital", nonnegative=True
        )
        if requested_capital <= 0:
            raise ValueError("requested_capital must be positive")

        capital_decision = disposition in {
            QualifiedOpportunityDisposition.ADMITTED,
            QualifiedOpportunityDisposition.CAPITAL_REJECTED,
            QualifiedOpportunityDisposition.CAPACITY_REJECTED,
        }
        if capital_decision:
            _require_nonnegative_int(
                normalized, "expected_portfolio_version"
            )
            _require_nonempty_string(normalized, "capital_policy_version")
            equity_limit = _require_finite_number(
                normalized, "portfolio_equity_limit", nonnegative=True
            )
            position_limit = _require_nonnegative_int(
                normalized, "portfolio_position_limit"
            )
            requested_reservation_amount = _require_finite_number(
                normalized, "requested_reservation_amount", nonnegative=True
            )
            if equity_limit <= 0:
                raise ValueError("portfolio_equity_limit must be positive")
            if position_limit <= 0:
                raise ValueError("portfolio_position_limit must be positive")
            if requested_reservation_amount < requested_capital:
                raise ValueError(
                    "requested_reservation_amount cannot be less than requested_capital"
                )
            if disposition is QualifiedOpportunityDisposition.ADMITTED:
                _require_nonempty_string(normalized, "paper_trade_id")
                _require_nonempty_string(normalized, "reservation_id")
            else:
                if normalized.get("reservation_id") is not None:
                    raise ValueError(
                        "rejected capital disposition cannot carry reservation_id"
                    )
                if normalized.get("paper_trade_id") is not None:
                    raise ValueError(
                        "rejected capital disposition cannot carry paper_trade_id"
                    )
        elif any(
            normalized.get(field_name) is not None
            for field_name in (
                "paper_trade_id",
                "reservation_id",
                "expected_portfolio_version",
                "capital_policy_version",
                "portfolio_equity_limit",
                "portfolio_position_limit",
                "requested_reservation_amount",
            )
        ):
            raise ValueError(
                "non-capital disposition cannot carry reservation-policy fields"
            )

        if population is not EvaluationPopulation.QUALIFIED_INTENT:
            raise ValueError(
                "opportunity disposition evidence belongs to QUALIFIED_INTENT population"
            )

    elif event_type == PAPER_ORDER_INTENT_RECORDED:
        _require_nonnegative_int(normalized, "intent_seq")
        if normalized["intent_role"] not in {"ENTRY", "EXIT"}:
            raise ValueError("intent_role must be ENTRY or EXIT")
        if normalized["side"] not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        if normalized["order_type"] not in {"MARKET", "LIMIT"}:
            raise ValueError("order_type must be MARKET or LIMIT")
        _require_nonempty_string(normalized, "reason_code")
        requested_quantity = _require_finite_number(
            normalized, "requested_quantity", nonnegative=True
        )
        if requested_quantity <= 0:
            raise ValueError("requested_quantity must be positive")
        # An executable intent must carry positive economic size. A zero (or
        # negative) notional alongside a positive quantity is not an executable
        # order and must not be recordable as one.
        requested_notional = _require_finite_number(
            normalized, "requested_notional", nonnegative=True
        )
        if requested_notional <= 0:
            raise ValueError("requested_notional must be positive")
        if normalized["order_type"] == "LIMIT":
            limit_price = _require_finite_number(
                normalized, "limit_price", nonnegative=True
            )
            if limit_price <= 0:
                raise ValueError("limit_price must be positive")
        elif normalized.get("limit_price") is not None:
            raise ValueError("MARKET order intent cannot carry limit_price")
        if normalized["execution_model_version"] != PAPER_EXECUTION_MODEL_VERSION:
            raise ValueError("unsupported execution model version")

    elif event_type == PAPER_EXECUTION_ATTEMPT_RECORDED:
        _require_nonnegative_int(normalized, "attempt_seq")
        try:
            state = ExecutionState(str(normalized["execution_state"]))
        except ValueError as exc:
            raise ValueError("unsupported execution_state") from exc
        if state is ExecutionState.INTENT_RECORDED:
            raise ValueError("execution attempt cannot restate INTENT_RECORDED")
        if normalized["execution_model_version"] != PAPER_EXECUTION_MODEL_VERSION:
            raise ValueError("unsupported execution model version")
        if normalized.get("accepted_quantity") is not None:
            _require_finite_number(normalized, "accepted_quantity", nonnegative=True)
        if state is ExecutionState.REJECTED:
            _require_nonempty_string(normalized, "rejection_reason")

    elif event_type == PAPER_FILL_RECORDED:
        _require_nonnegative_int(normalized, "fill_seq")
        if normalized["side"] not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")
        for field_name in (
            "quantity",
            "price",
            "fee_cost",
            "spread_cost",
            "slippage_cost",
            "other_supported_cost",
        ):
            _require_finite_number(normalized, field_name, nonnegative=True)
        if float(normalized["quantity"]) <= 0 or float(normalized["price"]) <= 0:
            raise ValueError("fill quantity and price must be positive")
        if normalized["execution_model_version"] != PAPER_EXECUTION_MODEL_VERSION:
            raise ValueError("unsupported execution model version")
        if normalized["economic_model_version"] != PAPER_ECONOMIC_MODEL_VERSION:
            raise ValueError("unsupported economic model version")

    elif event_type == PAPER_PROTECTION_PLAN_RECORDED:
        _require_nonnegative_int(normalized, "plan_seq")
        # A declared stop is a real protective price level; zero (or negative) is
        # not a usable stop and would silently disable protection.
        stop_price = _require_finite_number(
            normalized, "stop_price", nonnegative=True
        )
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        _require_nonnegative_int(normalized, "max_hold_seconds")
        targets = normalized["targets"]
        if not isinstance(targets, list) or not targets:
            raise ValueError("targets must be a non-empty canonical array")
        total_target_fraction = 0.0
        for target in targets:
            if not isinstance(target, Mapping):
                raise ValueError("each protection target must be an object")
            required_target_fields = {"target_id", "price", "fraction"}
            if set(target) != required_target_fields:
                raise ValueError(
                    "each protection target must contain exactly "
                    "target_id, price, and fraction"
                )
            _require_canonical_identity(target, "target_id")
            target_price = _require_finite_number(
                target, "price", nonnegative=True
            )
            target_fraction = _require_finite_number(
                target, "fraction", nonnegative=True
            )
            if target_price <= 0:
                raise ValueError("protection target price must be positive")
            if target_fraction <= 0 or target_fraction > 1:
                raise ValueError(
                    "protection target fraction must be within (0, 1]"
                )
            total_target_fraction += target_fraction
        # Each fraction is bounded on its own above, but the plan as a whole must
        # still not allocate more than the position. A residual below 1.0 is legal
        # because another exit mechanism may intentionally own the remainder.
        if total_target_fraction > 1.0 + _TARGET_FRACTION_TOLERANCE:
            raise ValueError(
                "protection target fractions must not sum to more than 1.0"
            )
        if normalized["protection_model_version"] != PAPER_PROTECTION_MODEL_VERSION:
            raise ValueError("unsupported protection model version")

    elif event_type == PAPER_PROTECTION_STATE_RECORDED:
        _require_nonnegative_int(normalized, "state_seq")
        try:
            from_state = ProtectionState(str(normalized["from_state"]))
            to_state = ProtectionState(str(normalized["to_state"]))
        except ValueError as exc:
            raise ValueError("unsupported protection state") from exc
        if from_state is to_state:
            raise ValueError("protection state transition must change state")
        _require_nonempty_string(normalized, "reason_code")
        if normalized["protection_model_version"] != PAPER_PROTECTION_MODEL_VERSION:
            raise ValueError("unsupported protection model version")

    elif event_type == PAPER_PROTECTION_TRIGGER_RECORDED:
        _require_nonnegative_int(normalized, "trigger_seq")
        if normalized["trigger_type"] not in {"STOP", "TARGET", "TIME", "EMERGENCY"}:
            raise ValueError("unsupported protection trigger_type")
        _require_finite_number(normalized, "reference_price", nonnegative=True)
        if float(normalized["reference_price"]) <= 0:
            raise ValueError("reference_price must be positive")
        if normalized["protection_model_version"] != PAPER_PROTECTION_MODEL_VERSION:
            raise ValueError("unsupported protection model version")

    elif event_type == PAPER_RECONCILIATION_RECORDED:
        _require_nonnegative_int(normalized, "reconciliation_seq")
        try:
            position_state = PositionState(str(normalized["position_state"]))
        except ValueError as exc:
            raise ValueError("unsupported position_state") from exc
        try:
            reconciliation_state = TerminalReconciliationState(
                str(normalized["terminal_reconciliation_state"])
            )
        except ValueError as exc:
            raise ValueError("unsupported terminal_reconciliation_state") from exc

        for field_name in (
            "filled_entry_quantity",
            "filled_exit_quantity",
            "remaining_quantity",
            "reserved_capital",
            "recorded_execution_costs",
        ):
            _require_finite_number(normalized, field_name, nonnegative=True)
        gross = _require_finite_number(normalized, "realized_gross_pnl")
        net = _require_finite_number(normalized, "realized_net_pnl")
        costs = float(normalized["recorded_execution_costs"])
        if abs(net - (gross - costs)) > 1e-6:
            raise ValueError(
                "realized_net_pnl must equal realized_gross_pnl - recorded_execution_costs"
            )
        if normalized["economic_model_version"] != PAPER_ECONOMIC_MODEL_VERSION:
            raise ValueError("unsupported economic model version")

        entry = float(normalized["filled_entry_quantity"])
        exit_quantity = float(normalized["filled_exit_quantity"])
        remaining = float(normalized["remaining_quantity"])
        if abs(entry - (exit_quantity + remaining)) > 1e-6:
            raise ValueError(
                "filled_entry_quantity must equal "
                "filled_exit_quantity + remaining_quantity"
            )
        if reconciliation_state is TerminalReconciliationState.FINAL_VERIFIED:
            if position_state is not PositionState.FLAT or remaining > 0.0:
                raise ValueError(
                    "FINAL_VERIFIED reconciliation requires FLAT position and zero remaining quantity"
                )
            if normalized.get("unresolved_reason") is not None:
                raise ValueError(
                    "FINAL_VERIFIED reconciliation cannot carry unresolved_reason"
                )
        if (
            reconciliation_state
            is TerminalReconciliationState.FLAT_AWAITING_RECONCILIATION
        ):
            if position_state is not PositionState.FLAT or remaining > 0.0:
                raise ValueError(
                    "FLAT_AWAITING_RECONCILIATION requires FLAT position "
                    "and zero remaining quantity"
                )
        if reconciliation_state is TerminalReconciliationState.UNRESOLVED_EVIDENCE:
            _require_nonempty_string(normalized, "unresolved_reason")

    paper_evidence_idempotency_key(event_type, normalized)
    return normalized


__all__ = [
    "PAPER_EXECUTION_ATTEMPT_RECORDED",
    "PAPER_EXECUTION_EVENT_CONTRACTS",
    "PAPER_EXECUTION_EVENT_TYPES",
    "PAPER_EXECUTION_PRIORITY",
    "PAPER_EXECUTION_STREAM",
    "PAPER_FILL_RECORDED",
    "PAPER_OPPORTUNITY_DISPOSITION_RECORDED",
    "PAPER_ORDER_INTENT_RECORDED",
    "PAPER_PROTECTION_PLAN_RECORDED",
    "PAPER_PROTECTION_STATE_RECORDED",
    "PAPER_PROTECTION_TRIGGER_RECORDED",
    "PAPER_RECONCILIATION_RECORDED",
    "PaperEvidenceEventContract",
    "event_contract",
    "paper_evidence_idempotency_key",
    "validate_paper_evidence_payload",
]
