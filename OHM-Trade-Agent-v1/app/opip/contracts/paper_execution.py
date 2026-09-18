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

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from app.opip.contracts.serialization import iso_z
from app.opip.contracts.temporal import require_utc
from app.opip.contracts.paper_outcome import (
    ENGINE_FREQTRADE_DRY_RUN,
    ENGINE_OHM_PAPER_SIM,
    QUOTE_CURRENCIES,
)


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


__all__ = [
    "ENGINE_OPIP_PAPER_V2",
    "ENGINE_ROLE_BY_ENGINE",
    "EvaluationPopulation",
    "ExecutionState",
    "PAPER_ECONOMIC_MODEL_VERSION",
    "PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION",
    "PAPER_EXECUTION_MODEL_VERSION",
    "PAPER_PROTECTION_MODEL_VERSION",
    "PaperEngineRole",
    "PaperExecutionLineage",
    "PositionState",
    "ProtectionState",
    "QualifiedOpportunityDisposition",
    "TemporalBasis",
    "TemporalEvidence",
    "TemporalPrecision",
    "TerminalReconciliationState",
    "protection_trigger_implies_exit",
]
