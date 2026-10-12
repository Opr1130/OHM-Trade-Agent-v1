"""PR-B/C-0 paper execution and protection contract vocabulary.

This module freezes the semantics needed by O'Pip Paper v2 before any runtime
producer is wired. It deliberately has no side effects, persistence, scheduler,
exchange, alert, learning, or trading-authority behavior.

Key invariants:
* new primary paper evidence is OPIP_PAPER_V2;
* legacy v1 evidence keeps its original meaning;
* Freqtrade is a reference/sensitivity population only;
* temporal evidence never invents point precision;
* one immutable decision-context reference anchors execution lineage;
* USD and USDT remain distinct quote-currency portfolios;
* protection triggers are evidence, not exits;
* actual, counterfactual, and hindsight populations never alias.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any

from app.opip.contracts.paper_outcome import (
    ENGINE_FREQTRADE_DRY_RUN,
    ENGINE_OHM_PAPER_SIM,
    QUOTE_CURRENCIES,
)
from app.opip.contracts.portfolio import (
    PORTFOLIO_COMPARISON_ID_PREFIX,
    PORTFOLIO_DIRECTIONS,
    PORTFOLIO_PLAN_ID_PREFIX,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc


PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION = 1

#: Frozen v2 model identifiers. These identify semantics, not deployed code.
PAPER_EXECUTION_MODEL_VERSION = "opip-paper-exec-l1-v2"
PAPER_ECONOMIC_MODEL_VERSION = "opip-paper-economics-v2"
PAPER_PROTECTION_MODEL_VERSION = "opip-paper-protection-v2"

#: Primary and compatibility engine identities.
ENGINE_OPIP_PAPER_V2 = "OPIP_PAPER_V2"


class PaperEngineRole(str, Enum):
    PRIMARY = "PRIMARY"
    REFERENCE = "REFERENCE"
    LEGACY = "LEGACY"


ENGINE_ROLE_BY_ENGINE: dict[str, PaperEngineRole] = {
    ENGINE_OPIP_PAPER_V2: PaperEngineRole.PRIMARY,
    ENGINE_FREQTRADE_DRY_RUN: PaperEngineRole.REFERENCE,
    ENGINE_OHM_PAPER_SIM: PaperEngineRole.LEGACY,
}


class TemporalPrecision(str, Enum):
    EXACT = "EXACT"
    BOUNDED = "BOUNDED"
    UNKNOWN = "UNKNOWN"


class TemporalBasis(str, Enum):
    SOURCE_REPORTED = "SOURCE_REPORTED"
    LOCALLY_OBSERVED = "LOCALLY_OBSERVED"
    MODEL_ASSIGNED = "MODEL_ASSIGNED"


class QualifiedOpportunityDisposition(str, Enum):
    """Why a qualified opportunity did or did not become an admitted intent.

    Every member is a distinct outcome; none aliases another. ``UNRESOLVED`` stays
    reserved for genuinely uncategorizable dispositions and is deliberately *not*
    reused for the specific reasons below. Terminal post-execution reconciliation
    is a separate vocabulary (``TerminalReconciliationState.UNRESOLVED_EVIDENCE``)
    and must never be expressed here.
    """

    ADMITTED = "ADMITTED"
    CAPACITY_REJECTED = "CAPACITY_REJECTED"
    ALREADY_TRACKED = "ALREADY_TRACKED"
    CAPITAL_REJECTED = "CAPITAL_REJECTED"
    NOT_ACTIONABLE = "NOT_ACTIONABLE"
    NO_FILL_EXPIRED = "NO_FILL_EXPIRED"
    DO_NOT_CHASE = "DO_NOT_CHASE"
    CANCELLED = "CANCELLED"
    UNSUPPORTED = "UNSUPPORTED"
    #: Qualified evaluation cannot safely proceed because required
    #: decision/admission evidence is incomplete. Distinct from UNRESOLVED: the
    #: reason is known and specific, not uncategorizable.
    EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
    #: Qualified evaluation intentionally not admitted because the relevant Paper
    #: v2 admission/execution capability is disabled. Distinct from UNRESOLVED and
    #: from a capital/capacity rejection.
    DISABLED = "DISABLED"
    UNRESOLVED = "UNRESOLVED"


class EvaluationPopulation(str, Enum):
    QUALIFIED_INTENT = "QUALIFIED_INTENT"
    ACTUAL_REALIZED = "ACTUAL_REALIZED"
    COUNTERFACTUAL_POLICY = "COUNTERFACTUAL_POLICY"
    HINDSIGHT_DIAGNOSTIC = "HINDSIGHT_DIAGNOSTIC"


class ExecutionState(str, Enum):
    INTENT_RECORDED = "INTENT_RECORDED"
    ACCEPTED = "ACCEPTED"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class PositionState(str, Enum):
    NO_POSITION = "NO_POSITION"
    OPEN = "OPEN"
    REDUCING = "REDUCING"
    FLAT = "FLAT"


class ProtectionState(str, Enum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    TRIGGERED = "TRIGGERED"


class TerminalReconciliationState(str, Enum):
    OPEN = "OPEN"
    FLAT_AWAITING_RECONCILIATION = "FLAT_AWAITING_RECONCILIATION"
    FINAL_VERIFIED = "FINAL_VERIFIED"
    UNRESOLVED_EVIDENCE = "UNRESOLVED_EVIDENCE"


@dataclass(frozen=True)
class TemporalEvidence:
    """Truthful occurrence-time representation for one evidence item.

    EXACT carries one proven occurrence timestamp.
    BOUNDED carries only the defensible occurrence interval.
    UNKNOWN carries a reason and no invented timestamp.

    basis states whether the time came from a source, local observation, or
    an explicitly declared model assumption. Canonical commit/receipt time is a
    separate fact and must not be substituted here.
    """

    precision: TemporalPrecision
    basis: TemporalBasis
    occurred_at: datetime | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        try:
            precision = (
                self.precision
                if isinstance(self.precision, TemporalPrecision)
                else TemporalPrecision(str(self.precision))
            )
        except ValueError as exc:
            raise ValueError("unsupported temporal precision") from exc
        try:
            basis = (
                self.basis
                if isinstance(self.basis, TemporalBasis)
                else TemporalBasis(str(self.basis))
            )
        except ValueError as exc:
            raise ValueError("unsupported temporal basis") from exc

        object.__setattr__(self, "precision", precision)
        object.__setattr__(self, "basis", basis)

        if precision is TemporalPrecision.EXACT:
            if basis is TemporalBasis.MODEL_ASSIGNED:
                raise ValueError("EXACT temporal evidence cannot be MODEL_ASSIGNED")
            if self.occurred_at is None:
                raise ValueError("EXACT temporal evidence requires occurred_at")
            occurred_at = require_utc(
                self.occurred_at,
                field_name="occurred_at",
            )
            if self.window_start is not None or self.window_end is not None:
                raise ValueError("EXACT temporal evidence cannot carry a bounded window")
            if str(self.reason or "").strip():
                raise ValueError("EXACT temporal evidence cannot carry an unknown reason")
            object.__setattr__(self, "occurred_at", occurred_at)
            return

        if precision is TemporalPrecision.BOUNDED:
            if self.occurred_at is not None:
                raise ValueError("BOUNDED temporal evidence cannot carry occurred_at")
            if self.window_start is None or self.window_end is None:
                raise ValueError(
                    "BOUNDED temporal evidence requires window_start and window_end"
                )
            start = require_utc(self.window_start, field_name="window_start")
            end = require_utc(self.window_end, field_name="window_end")
            if end < start:
                raise ValueError("window_end cannot precede window_start")
            if str(self.reason or "").strip():
                raise ValueError("BOUNDED temporal evidence cannot carry an unknown reason")
            object.__setattr__(self, "window_start", start)
            object.__setattr__(self, "window_end", end)
            return

        if any(
            value is not None
            for value in (self.occurred_at, self.window_start, self.window_end)
        ):
            raise ValueError("UNKNOWN temporal evidence cannot carry timestamps")
        reason = str(self.reason or "").strip()
        if not reason:
            raise ValueError("UNKNOWN temporal evidence requires a reason")
        object.__setattr__(self, "reason", reason)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "precision": self.precision.value,
            "basis": self.basis.value,
        }
        if self.occurred_at is not None:
            payload["occurred_at"] = iso_z(
                self.occurred_at,
                field_name="occurred_at",
            )
        if self.window_start is not None:
            payload["window_start"] = iso_z(
                self.window_start,
                field_name="window_start",
            )
        if self.window_end is not None:
            payload["window_end"] = iso_z(
                self.window_end,
                field_name="window_end",
            )
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True)
class PaperExecutionLineage:
    """Minimum normalized immutable lineage carried by a new v2 paper trade.

    Decision provenance stays owned by decision_context_id rather than
    duplicating candidate/evaluation/build fields into every downstream event.
    Execution/protection model versions remain explicit because they are owned
    by this subsystem and can evolve independently of the decision context.
    """

    decision_context_id: str
    paper_trade_id: str
    engine: str
    quote_currency: str
    execution_model_version: str = PAPER_EXECUTION_MODEL_VERSION
    economic_model_version: str = PAPER_ECONOMIC_MODEL_VERSION
    protection_model_version: str = PAPER_PROTECTION_MODEL_VERSION
    schema_version: int = PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for field_name in (
            "decision_context_id",
            "paper_trade_id",
            "engine",
            "quote_currency",
            "execution_model_version",
            "economic_model_version",
            "protection_model_version",
        ):
            value = str(getattr(self, field_name) or "").strip()
            if not value:
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value)

        if (
            type(self.schema_version) is not int
            or self.schema_version != PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported paper execution contract schema version")

        if self.engine not in ENGINE_ROLE_BY_ENGINE:
            raise ValueError(f"unsupported paper execution engine: {self.engine}")

        if self.quote_currency not in QUOTE_CURRENCIES:
            raise ValueError(f"unknown quote currency: {self.quote_currency}")

        if self.engine == ENGINE_OPIP_PAPER_V2:
            expected = (
                PAPER_EXECUTION_MODEL_VERSION,
                PAPER_ECONOMIC_MODEL_VERSION,
                PAPER_PROTECTION_MODEL_VERSION,
            )
            actual = (
                self.execution_model_version,
                self.economic_model_version,
                self.protection_model_version,
            )
            if actual != expected:
                raise ValueError(
                    "OPIP_PAPER_V2 requires the frozen v2 execution/economic/"
                    "protection model versions"
                )

    @property
    def engine_role(self) -> PaperEngineRole:
        return ENGINE_ROLE_BY_ENGINE[self.engine]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "decision_context_id": self.decision_context_id,
            "paper_trade_id": self.paper_trade_id,
            "engine": self.engine,
            "engine_role": self.engine_role.value,
            "quote_currency": self.quote_currency,
            "execution_model_version": self.execution_model_version,
            "economic_model_version": self.economic_model_version,
            "protection_model_version": self.protection_model_version,
        }


def protection_trigger_implies_exit() -> bool:
    """Pinned safety invariant: a protection trigger is never itself an exit."""

    return False


# ---------------------------------------------------------------------------
# SC-09 — rotation group record (R5-D portfolio planning and rotation)
#
# The frozen R5-D increment splits the work in two halves:
#
# * ``app/opip/contracts/portfolio.py`` (SC-08) decides *whether* a switch is
#   admissible at all: preregistered common-window comparisons, exit evidence,
#   anti-churn and abstention precedence.
# * this section (SC-09) records what an admissible switch actually did: the old
#   position it retains or exits, the replacement thesis, both legs' intent and
#   fill identifiers, the committed proceeds, the requalification decision, the
#   residual exposure and the terminal state.
#
# Dormancy is explicit and is part of the contract rather than an omission: the
# record is declared, serializable and fail-closed, but nothing produces or
# consumes it at runtime. ``ROTATION_GROUP_RECORDING_ENABLED`` is ``False``, every
# record declares ``authoritative = False``, and owner inputs this record cannot
# decide (``OQ-05``) are reported as ``OWNER_DECISION_REQUIRED`` instead of being
# defaulted to a number. This section calls no venue, reads no clock, opens no
# file, holds no position registry and raises no order.
# ---------------------------------------------------------------------------

#: Schema version of the durable ``RotationGroup`` payload.
ROTATION_GROUP_SCHEMA_VERSION = "rotation-group-v1"

#: Semantic prefix of the deterministic rotation-group identity.
ROTATION_GROUP_ID_PREFIX = "PRG"

#: The frozen ``SC-09`` record keys, mapped to the payload keys that carry them.
#: This mapping *is* the frozen record: a payload that drops, renames or reorders
#: a key fails closed rather than being read loosely.
ROTATION_GROUP_RECORD_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "old position": ("old_position",),
        "replacement thesis": ("replacement_thesis",),
        "both-leg intent/fill IDs": ("legs",),
        "committed proceeds": ("committed_proceeds",),
        "requalification decision": ("requalification",),
        "residual exposure": ("residual_exposure",),
        "terminal state": ("terminal_state",),
        "reasons": ("reasons",),
    }
)

#: Record-level context a durable payload carries in addition to the eight frozen
#: keys. It is appended *after* them and never interleaved, so the frozen key
#: order stays stable for every reader of the record.
ROTATION_GROUP_CONTEXT_KEYS: tuple[str, ...] = (
    "plan_id",
    "window_identity",
    "comparison_id",
    "group_id",
    "schema_version",
    "authoritative",
)

#: The full key order of one serialized rotation-group payload: the payload key
#: that carries each frozen ``SC-09`` key, in frozen order, then the record context.
ROTATION_GROUP_PAYLOAD_KEYS: tuple[str, ...] = (
    tuple(
        payload_key
        for aliases in ROTATION_GROUP_RECORD_KEYS.values()
        for payload_key in aliases
    )
    + ROTATION_GROUP_CONTEXT_KEYS
)

#: The declared dormancy of this increment: no runtime producer writes the record.
ROTATION_GROUP_RECORDING_ENABLED = False

#: Owner inputs this record cannot decide. ``OQ-05`` (minimum economic advantage,
#: uncertainty treatment, cooldown, requalification rule and turnover budget) is
#: an open register row, so it is never defaulted here.
OPEN_OWNER_INPUTS: tuple[str, ...] = ("OQ-05",)

#: How an undecided owner input is reported. Never a value, never a default.
OWNER_INPUT_STATUS = "OWNER_DECISION_REQUIRED"

#: Tolerance of the record's reconciliation laws (proceeds split, exited versus
#: retained quantity). This is a float-comparison engineering tolerance, not a
#: policy threshold, and it approves no switch.
ROTATION_RECONCILIATION_TOLERANCE = 1e-9


class RotationGroupContractError(ValueError):
    """Refusal raised by the ``SC-09`` rotation-group record.

    Every refusal is fail-closed: an unlinked leg, a second exit leg, a proceeds
    split that does not reconcile, an unratified requalification, a residual
    exposure that does not match the exited quantity or a forged ``group_id``
    raise instead of being silently repaired.
    """


class RotationLeg(str, Enum):
    """The two linked legs of one rotation. There is deliberately no third leg."""

    EXIT = "EXIT"
    ENTRY = "ENTRY"


class RotationTerminalState(str, Enum):
    """The terminal state of one linked intent group (``RO-03``)."""

    AWAITING_EXIT_FILL = "AWAITING_EXIT_FILL"
    EXIT_COMMITTED = "EXIT_COMMITTED"
    ENTRY_COMMITTED = "ENTRY_COMMITTED"
    COMPLETED = "COMPLETED"
    ABANDONED_TO_CASH = "ABANDONED_TO_CASH"
    ABSTAINED = "ABSTAINED"


class RotationRequalificationDecision(str, Enum):
    """The requalification decision of the replacement thesis (``RO-04``).

    The vocabulary is deliberately a *decision*: a pending or an abstained
    decision is never a silent pass, and only a decided requalification may
    accompany an entered replacement.
    """

    PENDING = "PENDING"
    REQUALIFIED = "REQUALIFIED"
    NOT_REQUALIFIED = "NOT_REQUALIFIED"
    NOT_REQUIRED = "NOT_REQUIRED"
    ABSTAINED = "ABSTAINED"


class RotationGroupReason(str, Enum):
    """The bounded reason vocabulary of one rotation group."""

    EXIT_FILLS_COMMITTED = "EXIT_FILLS_COMMITTED"
    PARTIAL_EXIT_RESIDUAL_RETAINED = "PARTIAL_EXIT_RESIDUAL_RETAINED"
    ENTRY_FUNDED_BY_EXIT_PROCEEDS = "ENTRY_FUNDED_BY_EXIT_PROCEEDS"
    ENTRY_FILLS_COMMITTED = "ENTRY_FILLS_COMMITTED"
    REPLACEMENT_REQUALIFIED = "REPLACEMENT_REQUALIFIED"
    REPLACEMENT_NOT_REQUALIFIED = "REPLACEMENT_NOT_REQUALIFIED"
    SWITCH_ABSTAINED = "SWITCH_ABSTAINED"
    PROCEEDS_RELEASED_TO_CASH = "PROCEEDS_RELEASED_TO_CASH"


#: Which requalification decisions each terminal state admits. A terminal state
#: that claims an entered replacement without a requalified thesis fails closed.
TERMINAL_STATE_REQUALIFICATION_DECISIONS: Mapping[
    str, tuple[RotationRequalificationDecision, ...]
] = MappingProxyType(
    {
        RotationTerminalState.AWAITING_EXIT_FILL.value: (
            RotationRequalificationDecision.PENDING,
            RotationRequalificationDecision.NOT_REQUIRED,
        ),
        RotationTerminalState.EXIT_COMMITTED.value: (
            RotationRequalificationDecision.PENDING,
            RotationRequalificationDecision.NOT_REQUIRED,
        ),
        RotationTerminalState.ENTRY_COMMITTED.value: (
            RotationRequalificationDecision.PENDING,
            RotationRequalificationDecision.REQUALIFIED,
            RotationRequalificationDecision.NOT_REQUIRED,
        ),
        RotationTerminalState.COMPLETED.value: (
            RotationRequalificationDecision.REQUALIFIED,
        ),
        RotationTerminalState.ABANDONED_TO_CASH.value: (
            RotationRequalificationDecision.NOT_REQUALIFIED,
            RotationRequalificationDecision.NOT_REQUIRED,
            RotationRequalificationDecision.ABSTAINED,
        ),
        RotationTerminalState.ABSTAINED.value: (
            RotationRequalificationDecision.ABSTAINED,
        ),
    }
)


def rotation_requalification_is_admitted(
    terminal_state: RotationTerminalState,
    decision: RotationRequalificationDecision,
) -> bool:
    """Whether ``decision`` may accompany ``terminal_state``. Unknown input is ``False``."""

    try:
        state = RotationTerminalState(terminal_state)
        resolved = RotationRequalificationDecision(decision)
    except ValueError:
        return False
    return resolved in TERMINAL_STATE_REQUALIFICATION_DECISIONS[state.value]


def _rotation_text(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RotationGroupContractError(f"{field_name} is required")
    return value.strip()


def _rotation_number(value: Any, *, field_name: str, allow_zero: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RotationGroupContractError(f"{field_name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise RotationGroupContractError(f"{field_name} must be a finite number")
    if number < 0 or (number == 0 and not allow_zero):
        raise RotationGroupContractError(
            f"{field_name} must be strictly positive"
            if not allow_zero
            else f"{field_name} cannot be negative"
        )
    return number


def _rotation_sequence(value: Any, *, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise RotationGroupContractError(f"{field_name} must be a sequence")
    return tuple(value)


def _rotation_id_tuple(value: Any, *, field_name: str) -> tuple[str, ...]:
    items = _rotation_sequence(value, field_name=field_name)
    cleaned = tuple(
        _rotation_text(item, field_name=f"{field_name} entry") for item in items
    )
    if len(set(cleaned)) != len(cleaned):
        raise RotationGroupContractError(f"{field_name} cannot repeat an id")
    return cleaned


def _rotation_direction(value: Any) -> str:
    direction = str(value or "").strip().upper()
    if direction not in PORTFOLIO_DIRECTIONS:
        raise RotationGroupContractError(f"unsupported direction: {value!r}")
    return direction


def _rotation_mapping(
    value: Any, *, field_name: str, expected_keys: tuple[str, ...]
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RotationGroupContractError(f"{field_name} must be a mapping")
    body = {str(key): item for key, item in value.items()}
    missing = tuple(key for key in expected_keys if key not in body)
    if missing:
        raise RotationGroupContractError(
            f"{field_name} is missing the SC-09 key(s): {', '.join(missing)}"
        )
    unexpected = tuple(sorted(key for key in body if key not in expected_keys))
    if unexpected:
        raise RotationGroupContractError(
            f"{field_name} carries unknown key(s): {', '.join(unexpected)}"
        )
    return body


def _rotation_enum(value: Any, enum_type: Any, *, field_name: str) -> Any:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(value)
    except ValueError as exc:
        raise RotationGroupContractError(f"unsupported {field_name}: {value!r}") from exc


def _rotation_utc(value: Any, *, field_name: str) -> datetime:
    """Read one UTC instant, accepting both a ``datetime`` and its persisted ISO form."""

    if isinstance(value, datetime):
        return require_utc(value, field_name=field_name)
    if not isinstance(value, str) or not value.strip():
        raise RotationGroupContractError(f"{field_name} is required")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RotationGroupContractError(
            f"{field_name} must be an ISO-8601 UTC instant"
        ) from exc
    return require_utc(parsed, field_name=field_name)


@dataclass(frozen=True)
class RotationOldPosition:
    """The position a rotation group either retains or exits (SC-09 ``old position``).

    ``position_ref`` references the identity already owned by the paper execution
    contracts. This record never becomes a second position registry.
    """

    subject_ref: str
    direction: str
    quantity: float
    position_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_ref",
            _rotation_text(self.subject_ref, field_name="subject_ref"),
        )
        object.__setattr__(
            self,
            "position_ref",
            _rotation_text(self.position_ref, field_name="position_ref"),
        )
        object.__setattr__(self, "direction", _rotation_direction(self.direction))
        object.__setattr__(
            self,
            "quantity",
            _rotation_number(self.quantity, field_name="quantity", allow_zero=False),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "direction": self.direction,
            "quantity": self.quantity,
            "position_ref": self.position_ref,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationOldPosition":
        body = _rotation_mapping(
            raw,
            field_name="old_position",
            expected_keys=("subject_ref", "direction", "quantity", "position_ref"),
        )
        return cls(
            subject_ref=body["subject_ref"],
            direction=body["direction"],
            quantity=body["quantity"],
            position_ref=body["position_ref"],
        )


@dataclass(frozen=True)
class RotationReplacementThesis:
    """The replacement thesis of a rotation group (SC-09 ``replacement thesis``).

    ``entry_policy_forecast_id`` is the declared policy binding of the entry leg
    and must be one of ``policy_forecast_ids``. That is how a partial exit funds
    only policy-allowed entries without this contract inventing a policy.
    """

    subject_ref: str
    thesis_ref: str
    direction: str
    entry_policy_forecast_id: str
    policy_forecast_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_ref",
            _rotation_text(self.subject_ref, field_name="subject_ref"),
        )
        object.__setattr__(
            self,
            "thesis_ref",
            _rotation_text(self.thesis_ref, field_name="thesis_ref"),
        )
        object.__setattr__(self, "direction", _rotation_direction(self.direction))
        entry_policy = _rotation_text(
            self.entry_policy_forecast_id, field_name="entry_policy_forecast_id"
        )
        declared = _rotation_id_tuple(
            self.policy_forecast_ids, field_name="policy_forecast_ids"
        )
        if not declared:
            raise RotationGroupContractError(
                "a replacement thesis must declare the policy forecast ids it is allowed under"
            )
        if entry_policy not in declared:
            raise RotationGroupContractError(
                "the entry policy forecast must be one of the declared policy forecast ids"
            )
        object.__setattr__(self, "entry_policy_forecast_id", entry_policy)
        object.__setattr__(self, "policy_forecast_ids", declared)

    def as_dict(self) -> dict[str, Any]:
        return {
            "subject_ref": self.subject_ref,
            "thesis_ref": self.thesis_ref,
            "direction": self.direction,
            "entry_policy_forecast_id": self.entry_policy_forecast_id,
            "policy_forecast_ids": list(self.policy_forecast_ids),
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationReplacementThesis":
        body = _rotation_mapping(
            raw,
            field_name="replacement_thesis",
            expected_keys=(
                "subject_ref",
                "thesis_ref",
                "direction",
                "entry_policy_forecast_id",
                "policy_forecast_ids",
            ),
        )
        return cls(
            subject_ref=body["subject_ref"],
            thesis_ref=body["thesis_ref"],
            direction=body["direction"],
            entry_policy_forecast_id=body["entry_policy_forecast_id"],
            policy_forecast_ids=body["policy_forecast_ids"],
        )


@dataclass(frozen=True)
class RotationLegFill:
    """One committed fill of one leg, referenced by stable execution identity.

    The record stores the identity, quantity, price and fee of a fill that was
    actually committed. It never stores or rebuilds order state: the paper
    execution event contracts remain the single owner of execution evidence.
    """

    fill_id: str
    quantity: float
    price: float
    fee: float = 0.0

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "fill_id", _rotation_text(self.fill_id, field_name="fill_id")
        )
        object.__setattr__(
            self,
            "quantity",
            _rotation_number(self.quantity, field_name="quantity", allow_zero=False),
        )
        object.__setattr__(
            self, "price", _rotation_number(self.price, field_name="price", allow_zero=False)
        )
        object.__setattr__(
            self, "fee", _rotation_number(self.fee, field_name="fee", allow_zero=True)
        )

    @property
    def gross_notional(self) -> float:
        return self.quantity * self.price

    @property
    def net_proceeds(self) -> float:
        """What the fill actually released: gross notional less its own fee."""

        return self.gross_notional - self.fee

    def as_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "quantity": self.quantity,
            "price": self.price,
            "fee": self.fee,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationLegFill":
        body = _rotation_mapping(
            raw,
            field_name="legs[].fills[]",
            expected_keys=("fill_id", "quantity", "price", "fee"),
        )
        return cls(
            fill_id=body["fill_id"],
            quantity=body["quantity"],
            price=body["price"],
            fee=body["fee"],
        )


@dataclass(frozen=True)
class RotationLegReference:
    """The stable identifiers linking one leg's intent and its committed fills.

    ``intent_id`` is the link between the two legs of the same group revision and
    ``policy_forecast_id`` is present on the entry leg only: an exit leg is not a
    policy-allowed *entry*, and an entry leg without a declared policy is refused.
    """

    leg: RotationLeg
    intent_id: str
    policy_forecast_id: str | None = None
    fills: tuple[RotationLegFill, ...] = ()

    def __post_init__(self) -> None:
        leg = _rotation_enum(self.leg, RotationLeg, field_name="leg")
        object.__setattr__(self, "leg", leg)
        object.__setattr__(
            self, "intent_id", _rotation_text(self.intent_id, field_name="intent_id")
        )
        if leg is RotationLeg.ENTRY:
            object.__setattr__(
                self,
                "policy_forecast_id",
                _rotation_text(
                    self.policy_forecast_id, field_name="policy_forecast_id"
                ),
            )
        else:
            if str(self.policy_forecast_id or "").strip():
                raise RotationGroupContractError(
                    "an exit leg is not a policy-allowed entry"
                )
            object.__setattr__(self, "policy_forecast_id", None)

        fills = _rotation_sequence(self.fills, field_name="fills")
        for fill in fills:
            if not isinstance(fill, RotationLegFill):
                raise RotationGroupContractError("fills must be RotationLegFill records")
        if len({fill.fill_id for fill in fills}) != len(fills):
            raise RotationGroupContractError("fills cannot repeat a fill id")
        object.__setattr__(self, "fills", fills)

    @property
    def filled_quantity(self) -> float:
        return math.fsum(fill.quantity for fill in self.fills)

    @property
    def proceeds(self) -> float:
        """Net proceeds the leg actually committed: gross notional less all fees."""

        return math.fsum(fill.net_proceeds for fill in self.fills)

    @property
    def is_filled(self) -> bool:
        return bool(self.fills)

    def as_dict(self) -> dict[str, Any]:
        return {
            "leg": self.leg.value,
            "intent_id": self.intent_id,
            "policy_forecast_id": self.policy_forecast_id,
            "fills": [fill.as_dict() for fill in self.fills],
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationLegReference":
        body = _rotation_mapping(
            raw,
            field_name="legs[]",
            expected_keys=("leg", "intent_id", "policy_forecast_id", "fills"),
        )
        return cls(
            leg=body["leg"],
            intent_id=body["intent_id"],
            policy_forecast_id=body["policy_forecast_id"],
            fills=tuple(
                RotationLegFill.from_dict(fill)
                for fill in _rotation_sequence(body["fills"], field_name="legs[].fills")
            ),
        )


@dataclass(frozen=True)
class RotationCommittedProceeds:
    """What the exit actually released and where it went (SC-09 ``committed proceeds``).

    The split is closed by construction: exit proceeds are either committed to the
    entry or released to cash, never both and never silently dropped. The recorded
    figure is what the committed fills released, never an estimate of what the exit
    might have released.
    """

    quote_currency: str
    exit_proceeds: float
    committed_to_entry: float = 0.0
    released_to_cash: float = 0.0

    def __post_init__(self) -> None:
        currency = str(self.quote_currency or "").strip().upper()
        if currency not in QUOTE_CURRENCIES:
            raise RotationGroupContractError(
                f"unknown quote currency: {self.quote_currency!r}"
            )
        object.__setattr__(self, "quote_currency", currency)
        for field_name in ("exit_proceeds", "committed_to_entry", "released_to_cash"):
            object.__setattr__(
                self,
                field_name,
                _rotation_number(
                    getattr(self, field_name), field_name=field_name, allow_zero=True
                ),
            )
        unreconciled = (
            self.committed_to_entry + self.released_to_cash - self.exit_proceeds
        )
        if abs(unreconciled) > ROTATION_RECONCILIATION_TOLERANCE:
            raise RotationGroupContractError(
                "committed proceeds do not reconcile: exit proceeds must equal what "
                "is committed to the entry plus what is released to cash"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_currency": self.quote_currency,
            "exit_proceeds": self.exit_proceeds,
            "committed_to_entry": self.committed_to_entry,
            "released_to_cash": self.released_to_cash,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationCommittedProceeds":
        body = _rotation_mapping(
            raw,
            field_name="committed_proceeds",
            expected_keys=(
                "quote_currency",
                "exit_proceeds",
                "committed_to_entry",
                "released_to_cash",
            ),
        )
        return cls(
            quote_currency=body["quote_currency"],
            exit_proceeds=body["exit_proceeds"],
            committed_to_entry=body["committed_to_entry"],
            released_to_cash=body["released_to_cash"],
        )


@dataclass(frozen=True)
class RotationRequalification:
    """The requalification decision of the replacement (SC-09 ``requalification decision``).

    A decided requalification must name the instant and the policy it was decided
    under; a pending, not-required or abstained decision must carry neither. An
    undecided requalification therefore cannot borrow the shape of a decided one.
    """

    decision: RotationRequalificationDecision
    decided_at: datetime | None = None
    policy_forecast_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "decision",
            _rotation_enum(
                self.decision,
                RotationRequalificationDecision,
                field_name="requalification decision",
            ),
        )
        if self.is_decided:
            if self.decided_at is None:
                raise RotationGroupContractError(
                    "a decided requalification requires decided_at"
                )
            if not str(self.policy_forecast_id or "").strip():
                raise RotationGroupContractError(
                    "a decided requalification requires the policy forecast it was decided under"
                )
            object.__setattr__(
                self,
                "decided_at",
                _rotation_utc(self.decided_at, field_name="decided_at"),
            )
            object.__setattr__(
                self,
                "policy_forecast_id",
                _rotation_text(
                    self.policy_forecast_id, field_name="policy_forecast_id"
                ),
            )
            return
        if self.decided_at is not None:
            raise RotationGroupContractError(
                "an undecided requalification cannot carry decided_at"
            )
        if str(self.policy_forecast_id or "").strip():
            raise RotationGroupContractError(
                "an undecided requalification cannot carry a policy forecast id"
            )
        object.__setattr__(self, "policy_forecast_id", None)

    @property
    def is_decided(self) -> bool:
        return self.decision in (
            RotationRequalificationDecision.REQUALIFIED,
            RotationRequalificationDecision.NOT_REQUALIFIED,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "decided_at": (
                None
                if self.decided_at is None
                else iso_z(self.decided_at, field_name="decided_at")
            ),
            "policy_forecast_id": self.policy_forecast_id,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationRequalification":
        body = _rotation_mapping(
            raw,
            field_name="requalification",
            expected_keys=("decision", "decided_at", "policy_forecast_id"),
        )
        return cls(
            decision=body["decision"],
            decided_at=body["decided_at"],
            policy_forecast_id=body["policy_forecast_id"],
        )


@dataclass(frozen=True)
class RotationResidualExposure:
    """What is left of the old position after the linked legs (SC-09 ``residual exposure``).

    ``quantity`` is stated in the units of the old position and must reconcile with
    what the exit leg actually committed, so a partial exit can never quietly claim
    more or less exposure than its own fills released.
    """

    subject_ref: str
    quantity: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "subject_ref",
            _rotation_text(self.subject_ref, field_name="subject_ref"),
        )
        object.__setattr__(
            self,
            "quantity",
            _rotation_number(self.quantity, field_name="quantity", allow_zero=True),
        )

    @property
    def is_flat(self) -> bool:
        return self.quantity <= ROTATION_RECONCILIATION_TOLERANCE

    def as_dict(self) -> dict[str, Any]:
        return {"subject_ref": self.subject_ref, "quantity": self.quantity}

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationResidualExposure":
        body = _rotation_mapping(
            raw,
            field_name="residual_exposure",
            expected_keys=("subject_ref", "quantity"),
        )
        return cls(subject_ref=body["subject_ref"], quantity=body["quantity"])


@dataclass(frozen=True)
class RotationGroup:
    """The durable linked-intent-group record of the frozen ``SC-09`` contract.

    The record is the *evidence* half of a rotation: ``SC-08`` decides whether a
    switch is admissible, and this record states what an admissible switch then
    did. It carries the eight frozen ``SC-09`` keys -- ``old position``,
    ``replacement thesis``, ``both-leg intent/fill IDs``, ``committed proceeds``,
    ``requalification decision``, ``residual exposure``, ``terminal state`` and
    ``reasons`` -- plus the record-level binding to the ``SC-08`` plan, common
    window and preregistered comparison it belongs to.

    The laws it enforces instead of trusting the writer:

    * both legs of one group share the group's subject, and a group has no third leg;
    * an entry leg exists only with the thesis' declared entry policy forecast, so
      a partial exit funds policy-allowed entries only;
    * the committed proceeds equal what the exit leg's committed fills released and
      split exactly into what is committed to the entry and what is released to cash;
    * the residual exposure equals the old position less what the exit actually
      committed;
    * the terminal state, the requalification decision and the recorded reasons
      must agree, so a completed switch without a requalified thesis is refused;
    * ``group_id`` is a content hash, so a forged identity fails closed.

    The record is dormant and non-authoritative: ``ROTATION_GROUP_RECORDING_ENABLED``
    is ``False``, ``authoritative`` must be ``False`` and nothing in this class
    reads a clock, a venue or a position registry.
    """

    old_position: RotationOldPosition
    replacement_thesis: RotationReplacementThesis
    legs: tuple[RotationLegReference, ...]
    committed_proceeds: RotationCommittedProceeds
    requalification: RotationRequalification
    residual_exposure: RotationResidualExposure
    terminal_state: RotationTerminalState
    reasons: tuple[RotationGroupReason, ...]
    plan_id: str
    window_identity: str
    comparison_id: str
    group_id: str = ""
    schema_version: str = ROTATION_GROUP_SCHEMA_VERSION
    authoritative: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.old_position, RotationOldPosition):
            raise RotationGroupContractError(
                "old_position must be a RotationOldPosition record"
            )
        if not isinstance(self.replacement_thesis, RotationReplacementThesis):
            raise RotationGroupContractError(
                "replacement_thesis must be a RotationReplacementThesis record"
            )
        if not isinstance(self.committed_proceeds, RotationCommittedProceeds):
            raise RotationGroupContractError(
                "committed_proceeds must be a RotationCommittedProceeds record"
            )
        if not isinstance(self.requalification, RotationRequalification):
            raise RotationGroupContractError(
                "requalification must be a RotationRequalification record"
            )
        if not isinstance(self.residual_exposure, RotationResidualExposure):
            raise RotationGroupContractError(
                "residual_exposure must be a RotationResidualExposure record"
            )

        subject = self.old_position.subject_ref
        if self.replacement_thesis.subject_ref != subject:
            raise RotationGroupContractError(
                "the replacement thesis must carry the rotation group's subject"
            )
        if self.residual_exposure.subject_ref != subject:
            raise RotationGroupContractError(
                "the residual exposure must carry the rotation group's subject"
            )

        terminal_state = _rotation_enum(
            self.terminal_state, RotationTerminalState, field_name="terminal state"
        )
        object.__setattr__(self, "terminal_state", terminal_state)

        legs = _rotation_sequence(self.legs, field_name="legs")
        if not legs:
            raise RotationGroupContractError(
                "a rotation group must carry the exit leg it links"
            )
        for index, leg in enumerate(legs):
            if not isinstance(leg, RotationLegReference):
                raise RotationGroupContractError(
                    f"legs[{index}] must be a RotationLegReference record"
                )
        if len(legs) > 2:
            raise RotationGroupContractError(
                "a rotation group links exactly two legs and has no third leg"
            )
        exit_leg = legs[0]
        if exit_leg.leg is not RotationLeg.EXIT:
            raise RotationGroupContractError(
                "the first leg of a rotation group must be the exit leg"
            )
        entry_leg = legs[1] if len(legs) == 2 else None
        if entry_leg is not None:
            if entry_leg.leg is not RotationLeg.ENTRY:
                raise RotationGroupContractError(
                    "the second leg of a rotation group must be the entry leg"
                )
            if entry_leg.intent_id == exit_leg.intent_id:
                raise RotationGroupContractError(
                    "the two legs must carry distinct intent ids"
                )
            if (
                entry_leg.policy_forecast_id
                != self.replacement_thesis.entry_policy_forecast_id
            ):
                raise RotationGroupContractError(
                    "a partial exit can only fund policy-allowed entries: the entry "
                    "leg must carry the thesis' declared entry policy forecast"
                )
        object.__setattr__(self, "legs", legs)

        reasons = _rotation_sequence(self.reasons, field_name="reasons")
        if not reasons:
            raise RotationGroupContractError(
                "a rotation group must record at least one governed reason"
            )
        resolved_reasons: list[RotationGroupReason] = []
        for reason in reasons:
            resolved = _rotation_enum(
                reason, RotationGroupReason, field_name="rotation group reason"
            )
            if resolved in resolved_reasons:
                raise RotationGroupContractError("reasons cannot repeat")
            resolved_reasons.append(resolved)
        object.__setattr__(self, "reasons", tuple(resolved_reasons))
        reason_set = frozenset(resolved_reasons)

        proceeds = self.committed_proceeds
        residual = self.residual_exposure
        requalification = self.requalification
        committed_to_entry = proceeds.committed_to_entry
        released_to_cash = proceeds.released_to_cash

        # Every committed move must be named, and no move may be claimed that the
        # record does not actually contain.
        if exit_leg.is_filled:
            if RotationGroupReason.EXIT_FILLS_COMMITTED not in reason_set:
                raise RotationGroupContractError(
                    "a group with a committed exit fill must record EXIT_FILLS_COMMITTED"
                )
        if entry_leg is not None and entry_leg.is_filled:
            if RotationGroupReason.ENTRY_FILLS_COMMITTED not in reason_set:
                raise RotationGroupContractError(
                    "a group with a committed entry fill must record ENTRY_FILLS_COMMITTED"
                )
        if committed_to_entry > ROTATION_RECONCILIATION_TOLERANCE:
            if RotationGroupReason.ENTRY_FUNDED_BY_EXIT_PROCEEDS not in reason_set:
                raise RotationGroupContractError(
                    "proceeds committed to the entry must record ENTRY_FUNDED_BY_EXIT_PROCEEDS"
                )
        if released_to_cash > ROTATION_RECONCILIATION_TOLERANCE:
            if RotationGroupReason.PROCEEDS_RELEASED_TO_CASH not in reason_set:
                raise RotationGroupContractError(
                    "proceeds released to cash must record PROCEEDS_RELEASED_TO_CASH"
                )
        partial_exit = residual.quantity > ROTATION_RECONCILIATION_TOLERANCE
        if partial_exit and exit_leg.is_filled:
            if RotationGroupReason.PARTIAL_EXIT_RESIDUAL_RETAINED not in reason_set:
                raise RotationGroupContractError(
                    "a partial exit must record PARTIAL_EXIT_RESIDUAL_RETAINED"
                )
        if partial_exit and not exit_leg.is_filled:
            if RotationGroupReason.PARTIAL_EXIT_RESIDUAL_RETAINED in reason_set:
                raise RotationGroupContractError(
                    "a group that committed no exit fill cannot claim a partial exit"
                )

        # The terminal state, the linked legs and the requalification decision must
        # tell one story; a state that claims a move the record does not contain fails.
        if terminal_state is RotationTerminalState.AWAITING_EXIT_FILL:
            if exit_leg.is_filled:
                raise RotationGroupContractError(
                    "AWAITING_EXIT_FILL cannot carry a committed exit fill"
                )
            if entry_leg is not None:
                raise RotationGroupContractError(
                    "AWAITING_EXIT_FILL cannot link an entry leg"
                )
        elif terminal_state is RotationTerminalState.ABSTAINED:
            if exit_leg.is_filled:
                raise RotationGroupContractError(
                    "ABSTAINED cannot carry a committed exit fill"
                )
            if entry_leg is not None:
                raise RotationGroupContractError("ABSTAINED cannot link an entry leg")
            if RotationGroupReason.SWITCH_ABSTAINED not in reason_set:
                raise RotationGroupContractError("ABSTAINED must record SWITCH_ABSTAINED")
        else:
            if not exit_leg.is_filled:
                raise RotationGroupContractError(
                    f"{terminal_state.value} requires a committed exit fill"
                )
            if proceeds.exit_proceeds <= ROTATION_RECONCILIATION_TOLERANCE:
                raise RotationGroupContractError(
                    f"{terminal_state.value} must record the proceeds the exit released"
                )

        if terminal_state in (
            RotationTerminalState.AWAITING_EXIT_FILL,
            RotationTerminalState.ABSTAINED,
        ) and (
            proceeds.exit_proceeds > ROTATION_RECONCILIATION_TOLERANCE
            or committed_to_entry > ROTATION_RECONCILIATION_TOLERANCE
            or released_to_cash > ROTATION_RECONCILIATION_TOLERANCE
        ):
            raise RotationGroupContractError(
                f"{terminal_state.value} cannot carry committed proceeds"
            )

        if terminal_state in (
            RotationTerminalState.EXIT_COMMITTED,
            RotationTerminalState.ABANDONED_TO_CASH,
        ):
            if entry_leg is not None:
                raise RotationGroupContractError(
                    f"{terminal_state.value} cannot link an entry leg"
                )
            if committed_to_entry > ROTATION_RECONCILIATION_TOLERANCE:
                raise RotationGroupContractError(
                    f"{terminal_state.value} cannot commit proceeds to an entry"
                )
        if terminal_state is RotationTerminalState.ABANDONED_TO_CASH:
            if RotationGroupReason.REPLACEMENT_NOT_REQUALIFIED not in reason_set:
                raise RotationGroupContractError(
                    "ABANDONED_TO_CASH must record REPLACEMENT_NOT_REQUALIFIED"
                )

        if terminal_state in (
            RotationTerminalState.ENTRY_COMMITTED,
            RotationTerminalState.COMPLETED,
        ):
            if entry_leg is None:
                raise RotationGroupContractError(
                    f"{terminal_state.value} must link the entry leg it funded"
                )
            if committed_to_entry <= ROTATION_RECONCILIATION_TOLERANCE:
                raise RotationGroupContractError(
                    f"{terminal_state.value} must commit the exit's proceeds to the entry"
                )
        if terminal_state is RotationTerminalState.COMPLETED:
            if entry_leg is None or not entry_leg.is_filled:
                raise RotationGroupContractError(
                    "COMPLETED requires the entry leg's committed fills"
                )
            if released_to_cash > ROTATION_RECONCILIATION_TOLERANCE:
                raise RotationGroupContractError(
                    "COMPLETED commits the whole exit to the entry: nothing may be released to cash"
                )
            if not residual.is_flat:
                raise RotationGroupContractError(
                    "COMPLETED must leave no residual exposure"
                )
            if RotationGroupReason.REPLACEMENT_REQUALIFIED not in reason_set:
                raise RotationGroupContractError(
                    "COMPLETED must record REPLACEMENT_REQUALIFIED"
                )

        if not rotation_requalification_is_admitted(
            terminal_state, requalification.decision
        ):
            raise RotationGroupContractError(
                "the requalification decision "
                f"{requalification.decision.value} cannot accompany the terminal "
                f"state {terminal_state.value}"
            )

        # Measured reconciliation against the committed fills, never an estimate.
        if (
            abs(proceeds.exit_proceeds - exit_leg.proceeds)
            > ROTATION_RECONCILIATION_TOLERANCE
        ):
            raise RotationGroupContractError(
                "the committed proceeds must equal what the exit leg's committed "
                "fills actually released"
            )
        expected_residual = self.old_position.quantity - exit_leg.filled_quantity
        if abs(residual.quantity - expected_residual) > ROTATION_RECONCILIATION_TOLERANCE:
            raise RotationGroupContractError(
                "the residual exposure must equal the old position less what the "
                "exit leg actually committed"
            )

        # Binding to the SC-08 decision this record documents, reusing the frozen
        # identity conventions instead of forking a second one.
        plan_id = _rotation_text(self.plan_id, field_name="plan_id")
        if not plan_id.startswith(PORTFOLIO_PLAN_ID_PREFIX):
            raise RotationGroupContractError(
                "a rotation group must name the SC-08 portfolio plan it belongs to"
            )
        comparison_id = _rotation_text(self.comparison_id, field_name="comparison_id")
        if not comparison_id.startswith(PORTFOLIO_COMPARISON_ID_PREFIX):
            raise RotationGroupContractError(
                "a rotation group must name the preregistered SC-08 comparison it is bound to"
            )
        object.__setattr__(self, "plan_id", plan_id)
        object.__setattr__(self, "comparison_id", comparison_id)
        object.__setattr__(
            self,
            "window_identity",
            _rotation_text(self.window_identity, field_name="window_identity"),
        )
        if self.schema_version != ROTATION_GROUP_SCHEMA_VERSION:
            raise RotationGroupContractError(
                f"unsupported rotation group schema version: {self.schema_version!r}"
            )
        if self.authoritative is not False:
            raise RotationGroupContractError(
                "a rotation-group record is never authoritative: it records evidence "
                "and authorizes no order"
            )

        expected_group_id = stable_hash(ROTATION_GROUP_ID_PREFIX, self._identity_payload())
        if self.group_id == "":
            object.__setattr__(self, "group_id", expected_group_id)
        elif self.group_id != expected_group_id:
            raise RotationGroupContractError(
                "group_id does not match its content; a forged rotation group fails closed"
            )

    @property
    def exit_leg(self) -> RotationLegReference:
        return self.legs[0]

    @property
    def entry_leg(self) -> RotationLegReference | None:
        return self.legs[1] if len(self.legs) == 2 else None

    @property
    def is_abstained(self) -> bool:
        """Whether the record documents an abstained switch: evidence, and no order."""

        return self.terminal_state is RotationTerminalState.ABSTAINED

    @property
    def reuses_the_old_position(self) -> bool:
        """Whether the group kept, wholly or partly, the position it started from."""

        return self.residual_exposure.quantity > ROTATION_RECONCILIATION_TOLERANCE

    def _identity_payload(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.pop("group_id", None)
        return payload

    def as_dict(self) -> dict[str, Any]:
        """The durable payload: the frozen ``SC-09`` keys, then the record context."""

        return {
            "old_position": self.old_position.as_dict(),
            "replacement_thesis": self.replacement_thesis.as_dict(),
            "legs": [leg.as_dict() for leg in self.legs],
            "committed_proceeds": self.committed_proceeds.as_dict(),
            "requalification": self.requalification.as_dict(),
            "residual_exposure": self.residual_exposure.as_dict(),
            "terminal_state": self.terminal_state.value,
            "reasons": [reason.value for reason in self.reasons],
            "plan_id": self.plan_id,
            "window_identity": self.window_identity,
            "comparison_id": self.comparison_id,
            "group_id": self.group_id,
            "schema_version": self.schema_version,
            "authoritative": self.authoritative,
        }

    def to_dict(self) -> dict[str, Any]:
        """``as_dict()`` under the ``SC-08`` record naming, so one payload shape serves both halves of R5-D."""

        return self.as_dict()

    @classmethod
    def from_dict(cls, raw: Any) -> "RotationGroup":
        """Rebuild the record from its durable payload, refusing every forgery.

        The frozen key set is checked exactly, every nested record revalidates its
        own invariants, and the identity is recomputed from content: a payload with
        a forged ``group_id``, a dropped frozen key, an extra key or a mutated leg
        fails closed rather than being loaded and trusted.
        """

        body = _rotation_mapping(
            raw, field_name="rotation_group", expected_keys=ROTATION_GROUP_PAYLOAD_KEYS
        )
        legs = _rotation_sequence(body["legs"], field_name="legs")
        group = cls(
            old_position=RotationOldPosition.from_dict(body["old_position"]),
            replacement_thesis=RotationReplacementThesis.from_dict(
                body["replacement_thesis"]
            ),
            legs=tuple(RotationLegReference.from_dict(leg) for leg in legs),
            committed_proceeds=RotationCommittedProceeds.from_dict(
                body["committed_proceeds"]
            ),
            requalification=RotationRequalification.from_dict(body["requalification"]),
            residual_exposure=RotationResidualExposure.from_dict(
                body["residual_exposure"]
            ),
            terminal_state=body["terminal_state"],
            reasons=body["reasons"],
            plan_id=body["plan_id"],
            window_identity=body["window_identity"],
            comparison_id=body["comparison_id"],
            group_id=body["group_id"],
            schema_version=body["schema_version"],
            authoritative=body["authoritative"],
        )
        if group.group_id != stable_hash(
            ROTATION_GROUP_ID_PREFIX, group._identity_payload()
        ):
            raise RotationGroupContractError(
                "group_id does not match its content; a forged rotation group fails closed"
            )
        return group


def assert_rotation_group_record_keys(group: RotationGroup) -> tuple[str, ...]:
    """Return the payload key order after proving it is exactly the frozen ``SC-09`` record."""

    if not isinstance(group, RotationGroup):
        raise RotationGroupContractError(
            "assert_rotation_group_record_keys requires a RotationGroup record"
        )
    keys = tuple(group.as_dict())
    if keys != ROTATION_GROUP_PAYLOAD_KEYS:
        raise RotationGroupContractError(
            "the rotation-group payload does not carry the frozen SC-09 keys in order"
        )
    return keys


def commit_rotation_proceeds(
    exit_leg: RotationLegReference,
    *,
    committed_to_entry: float = 0.0,
    quote_currency: str = "USD",
) -> RotationCommittedProceeds:
    """Split what an *already committed* exit leg released into entry and cash.

    This is arithmetic over committed fill evidence and nothing else: it cannot exit
    anything, cannot enter anything and cannot decide requalification. It exists so
    that every writer splits the exit the same way instead of rounding twice, and it
    fails closed when an entry commitment exceeds what the exit actually released.
    """

    if not isinstance(exit_leg, RotationLegReference):
        raise RotationGroupContractError(
            "commit_rotation_proceeds requires the group's linked leg record"
        )
    if exit_leg.leg is not RotationLeg.EXIT:
        raise RotationGroupContractError(
            "only an exit leg's committed proceeds can be committed to an entry"
        )
    available = exit_leg.proceeds
    entry_commitment = _rotation_number(
        committed_to_entry, field_name="committed_to_entry", allow_zero=True
    )
    if entry_commitment - available > ROTATION_RECONCILIATION_TOLERANCE:
        raise RotationGroupContractError(
            "an entry can only be funded by proceeds the exit actually committed"
        )
    return RotationCommittedProceeds(
        quote_currency=quote_currency,
        exit_proceeds=available,
        committed_to_entry=entry_commitment,
        released_to_cash=max(0.0, available - entry_commitment),
    )

__all__ = [
    "ENGINE_OPIP_PAPER_V2",
    "ENGINE_ROLE_BY_ENGINE",
    "EvaluationPopulation",
    "ExecutionState",
    "OPEN_OWNER_INPUTS",
    "OWNER_INPUT_STATUS",
    "PAPER_ECONOMIC_MODEL_VERSION",
    "PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION",
    "PAPER_EXECUTION_MODEL_VERSION",
    "PAPER_PROTECTION_MODEL_VERSION",
    "PaperEngineRole",
    "PaperExecutionLineage",
    "PositionState",
    "ProtectionState",
    "QualifiedOpportunityDisposition",
    "ROTATION_GROUP_CONTEXT_KEYS",
    "ROTATION_GROUP_ID_PREFIX",
    "ROTATION_GROUP_PAYLOAD_KEYS",
    "ROTATION_GROUP_RECORDING_ENABLED",
    "ROTATION_GROUP_RECORD_KEYS",
    "ROTATION_GROUP_SCHEMA_VERSION",
    "ROTATION_RECONCILIATION_TOLERANCE",
    "RotationCommittedProceeds",
    "RotationGroup",
    "RotationGroupContractError",
    "RotationGroupReason",
    "RotationLeg",
    "RotationLegFill",
    "RotationLegReference",
    "RotationOldPosition",
    "RotationReplacementThesis",
    "RotationRequalification",
    "RotationRequalificationDecision",
    "RotationResidualExposure",
    "RotationTerminalState",
    "TERMINAL_STATE_REQUALIFICATION_DECISIONS",
    "TemporalBasis",
    "TemporalEvidence",
    "TemporalPrecision",
    "TerminalReconciliationState",
    "assert_rotation_group_record_keys",
    "commit_rotation_proceeds",
    "protection_trigger_implies_exit",
    "rotation_requalification_is_admitted",
]
