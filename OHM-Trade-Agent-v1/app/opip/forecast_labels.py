"""Prospective F6 label projection over committed canonical paper evidence.

This module is the missing bridge between the ONE canonical outcome authority
(the committed ``paper_execution`` / ``paper_protection`` / ``paper_outcome``
canonical records) and the F6 Forecast Engine's two label families:

* the entry-execution family ``NO_FILL`` / ``PARTIAL_FILL`` / ``FULL_FILL``;
* the conditional post-fill path family ``TARGET`` / ``STOP`` / ``TIMEOUT`` /
  ``RISK_EXIT``.

What this module is
-------------------
A pure, deterministic projection. Given the canonical evidence for ONE economic
opportunity it derives the exact F6 labels (or an explicit non-label state) under
a frozen policy, so prospective canonical outcomes can become F6 evidence without
anyone re-deciding the vocabulary after seeing outcomes.

What this module is NOT
-----------------------
It is a projection, never an authority. It reads no clock, environment,
filesystem, database or network. It writes nothing, creates no store, no table,
no JSONL stream and no canonical event type. It holds no trading, admission,
reservation, execution or exchange authority, allocates no capital, ranks
nothing, sizes nothing and trains nothing. The canonical writer remains the
single outcome authority; this module only re-expresses what the canonical writer
already committed.

Label law (frozen, never chosen after viewing outcomes)
-------------------------------------------------------
* ``NO_FILL`` has no post-fill path label: its path state is
  ``INSUFFICIENT_EVIDENCE`` and its path outcome is ``None``.
* ``TIMEOUT`` is a fully observed horizon expiry scored from its recorded
  realized return; it is never equated with a negative return.
* ``INCOMPLETE_COVERAGE`` (an ambiguous within-bar path, e.g. an OHLC gap) is
  never a negative and never a clean exact path label.
* A non-admitted or unfilled opportunity deploys no capital, so its realized net
  return is exactly zero; it is never recorded as a loss.
* MISSING EVIDENCE IS NEVER FAVORABLE: any token, state or quantity the committed
  canonical vocabulary does not unambiguously support fails closed to
  ``UNRESOLVED`` / ``INCOMPLETE_COVERAGE`` and is never repaired or made
  favorable.

The two families are never mixed, and no ordinal score, confidence or Committee
rubric is ever read here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.contracts.forecast import (
    EntryExecutionOutcome,
    ForecastFidelityGrade,
    ForecastLabelState,
    PostFillPathOutcome,
)
from app.opip.contracts.paper_outcome import LINEAGE_COMPLETE, TERMINAL_STATUSES

#: Semantic version of the frozen prospective F6 label-projection policy.
FORECAST_LABEL_PROJECTION_VERSION = "forecast-label-projection-v1"

#: The single v1 post-fill path anchor, mirroring ``ForecastHorizon.FIRST_FILL``.
POST_FILL_PATH_ANCHOR = "FIRST_FILL"


class ForecastLabelProjectionError(ValueError):
    """A structural violation of the frozen label-projection contract."""


class ForecastEvidencePopulation(str, Enum):
    """The frozen prospective F6 evidence population classes.

    Every eligible prospective opportunity lands in exactly one class, so the
    full population (including no-trade, no-fill, unresolved and incomplete
    cases) stays countable and no case is silently dropped.
    """

    CASH_NO_TRADE = "CASH_NO_TRADE"
    NO_FILL = "NO_FILL"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    UNRESOLVED = "UNRESOLVED"
    INCOMPLETE_COVERAGE = "INCOMPLETE_COVERAGE"


#: Canonical ``QualifiedOpportunityDisposition`` tokens that mean the opportunity
#: was deliberately never admitted as an entry. These are no-trade decisions and
#: are retained in the cash/no-trade population, never scored as an F6 fill.
NON_ADMISSION_DISPOSITIONS: frozenset[str] = frozenset(
    {
        "CAPACITY_REJECTED",
        "ALREADY_TRACKED",
        "CAPITAL_REJECTED",
        "NOT_ACTIONABLE",
        "DO_NOT_CHASE",
        "CANCELLED",
        "UNSUPPORTED",
        "EVIDENCE_INCOMPLETE",
        "DISABLED",
        "UNRESOLVED",
    }
)

#: Canonical ``ExecutionState`` tokens that are terminal with no fill.
NO_FILL_EXECUTION_STATES: frozenset[str] = frozenset(
    {"REJECTED", "CANCELLED", "EXPIRED"}
)

#: Canonical ``ExecutionState`` tokens that are not yet terminal.
NON_TERMINAL_EXECUTION_STATES: frozenset[str] = frozenset(
    {"INTENT_RECORDED", "ACCEPTED", "WORKING"}
)

#: Frozen canonical ``exit_reason`` -> (path outcome, path label state) map.
#: A ``None`` outcome means the reason is not a clean exact path label.
FORECAST_EXIT_REASON_PATH_MAP: Mapping[
    str, tuple[PostFillPathOutcome | None, ForecastLabelState]
] = MappingProxyType(
    {
        # A target was reached: a clean, resolved path outcome. ``TARGET_2`` is
        # the canonical second-target token.
        "TARGET_2": (PostFillPathOutcome.TARGET, ForecastLabelState.RESOLVED),
        # A stop was reached: a clean, resolved path outcome.
        "STOP": (PostFillPathOutcome.STOP, ForecastLabelState.RESOLVED),
        # A stop during the entry candle is still a stop path.
        "ENTRY_CANDLE_STOP": (PostFillPathOutcome.STOP, ForecastLabelState.RESOLVED),
        # A fully observed horizon expiry scored from its recorded return.
        "TIME_EXIT": (PostFillPathOutcome.TIMEOUT, ForecastLabelState.RESOLVED),
        # An ambiguous within-bar path (OHLC gap): never a clean exact label and
        # never a negative.
        "OHLC_GAP": (None, ForecastLabelState.INCOMPLETE_COVERAGE),
        # Non-market terminations and genuinely uncategorizable outcomes are not a
        # path label at all.
        "OPERATOR_OFF": (None, ForecastLabelState.UNRESOLVED),
        "UNRESOLVED": (None, ForecastLabelState.UNRESOLVED),
        # These mean the entry never filled, so they carry no post-fill path.
        "PENDING_TTL_EXPIRED": (None, ForecastLabelState.INSUFFICIENT_EVIDENCE),
        "DO_NOT_CHASE": (None, ForecastLabelState.INSUFFICIENT_EVIDENCE),
    }
)

#: Canonical protection ``trigger_type`` tokens that are an independently
#: triggered risk exit.
RISK_EXIT_TRIGGER_TYPES: frozenset[str] = frozenset({"EMERGENCY"})


def _clean_token(value: Any, *, field_name: str) -> tuple[str | None, str | None]:
    """Return ``(token, error_reason)`` for one canonical text token.

    ``None`` means the token was absent. A token that is present but is not a
    non-empty, whitespace-free string is malformed: this projection fails closed
    on it rather than raising, so a single bad field cannot abort a whole evidence
    batch. It never strips or repairs the token.
    """
    if value is None:
        return (None, None)
    if not isinstance(value, str) or value == "" or value != value.strip():
        return (None, f"MALFORMED_TOKEN:{field_name}")
    return (value, None)


def _optional_finite_number(value: Any) -> float | None:
    """A finite float, or ``None`` for bools, non-numbers and non-finite values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _optional_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


@dataclass(frozen=True)
class ForecastEvidenceLabel:
    """One projection result: the frozen F6 labels for one canonical opportunity."""

    population: ForecastEvidencePopulation
    entry_outcome: EntryExecutionOutcome | None
    entry_label_state: ForecastLabelState
    post_fill_outcome: PostFillPathOutcome | None
    post_fill_label_state: ForecastLabelState
    realized_net_return: float | None
    realized_return_label_state: ForecastLabelState
    fidelity: ForecastFidelityGrade
    label_available_at: datetime | None
    exit_reason: str | None
    entry_execution_state: str | None
    projection_version: str = FORECAST_LABEL_PROJECTION_VERSION
    reason: str = ""

    @property
    def entry_is_calibration_eligible(self) -> bool:
        """True only for a cleanly resolved entry-execution label."""
        return (
            self.entry_label_state is ForecastLabelState.RESOLVED
            and self.entry_outcome is not None
        )

    @property
    def path_is_calibration_eligible(self) -> bool:
        """True only for a cleanly resolved post-fill path label."""
        return (
            self.post_fill_label_state is ForecastLabelState.RESOLVED
            and self.post_fill_outcome is not None
        )


def _derive_entry(
    *,
    disposition: str | None,
    entry_execution_state: str | None,
    intended_quantity: float | None,
    accepted_quantity: float | None,
) -> tuple[ForecastEvidencePopulation, EntryExecutionOutcome | None,
           ForecastLabelState, str]:
    """Derive the entry-execution label. Fails closed; never fabricates a fill."""
    if disposition is not None and disposition in NON_ADMISSION_DISPOSITIONS:
        # Deliberately never admitted: a no-trade decision, retained as cash.
        return (
            ForecastEvidencePopulation.CASH_NO_TRADE,
            None,
            ForecastLabelState.RESOLVED,
            f"NOT_ADMITTED:{disposition}",
        )
    if disposition == "NO_FILL_EXPIRED":
        if accepted_quantity is None or accepted_quantity == 0.0:
            return (
                ForecastEvidencePopulation.NO_FILL,
                EntryExecutionOutcome.NO_FILL,
                ForecastLabelState.RESOLVED,
                "NO_FILL_EXPIRED",
            )
        # An admission that expired cannot also have accepted quantity; ambiguous.
        return (
            ForecastEvidencePopulation.UNRESOLVED,
            None,
            ForecastLabelState.UNRESOLVED,
            "NO_FILL_EXPIRED_WITH_ACCEPTED_QUANTITY",
        )

    state = entry_execution_state
    if state is None:
        return (
            ForecastEvidencePopulation.UNRESOLVED,
            None,
            ForecastLabelState.UNRESOLVED,
            "NO_ENTRY_EVIDENCE",
        )
    if state in NON_TERMINAL_EXECUTION_STATES:
        return (
            ForecastEvidencePopulation.UNRESOLVED,
            None,
            ForecastLabelState.UNRESOLVED,
            f"NON_TERMINAL_ENTRY:{state}",
        )
    if state == "FILLED":
        if intended_quantity is None or intended_quantity <= 0:
            return (
                ForecastEvidencePopulation.UNRESOLVED,
                None,
                ForecastLabelState.UNRESOLVED,
                "FILLED_WITHOUT_INTENDED_QUANTITY",
            )
        if accepted_quantity is None:
            return (
                ForecastEvidencePopulation.UNRESOLVED,
                None,
                ForecastLabelState.UNRESOLVED,
                "FILLED_WITHOUT_ACCEPTED_QUANTITY",
            )
        if accepted_quantity >= intended_quantity:
            return (
                ForecastEvidencePopulation.FULL_FILL,
                EntryExecutionOutcome.FULL_FILL,
                ForecastLabelState.RESOLVED,
                "FILLED",
            )
        if 0.0 < accepted_quantity < intended_quantity:
            return (
                ForecastEvidencePopulation.PARTIAL_FILL,
                EntryExecutionOutcome.PARTIAL_FILL,
                ForecastLabelState.RESOLVED,
                "FILLED_BELOW_INTENDED",
            )
        return (
            ForecastEvidencePopulation.UNRESOLVED,
            None,
            ForecastLabelState.UNRESOLVED,
            "FILLED_WITH_NONPOSITIVE_ACCEPTED_QUANTITY",
        )
    if state == "PARTIALLY_FILLED":
        if accepted_quantity is None or accepted_quantity <= 0.0:
            return (
                ForecastEvidencePopulation.UNRESOLVED,
                None,
                ForecastLabelState.UNRESOLVED,
                "PARTIAL_WITHOUT_ACCEPTED_QUANTITY",
            )
        if intended_quantity is not None and accepted_quantity >= intended_quantity:
            return (
                ForecastEvidencePopulation.UNRESOLVED,
                None,
                ForecastLabelState.UNRESOLVED,
                "PARTIAL_NOT_BELOW_INTENDED",
            )
        return (
            ForecastEvidencePopulation.PARTIAL_FILL,
            EntryExecutionOutcome.PARTIAL_FILL,
            ForecastLabelState.RESOLVED,
            "PARTIALLY_FILLED",
        )
    if state in NO_FILL_EXECUTION_STATES:
        if accepted_quantity not in (None, 0.0):
            # Cancelled/expired/rejected but quantity was accepted: ambiguous.
            return (
                ForecastEvidencePopulation.UNRESOLVED,
                None,
                ForecastLabelState.UNRESOLVED,
                f"{state}_WITH_ACCEPTED_QUANTITY",
            )
        return (
            ForecastEvidencePopulation.NO_FILL,
            EntryExecutionOutcome.NO_FILL,
            ForecastLabelState.RESOLVED,
            state,
        )
    return (
        ForecastEvidencePopulation.UNRESOLVED,
        None,
        ForecastLabelState.UNRESOLVED,
        f"UNKNOWN_ENTRY_EXECUTION_STATE:{state}",
    )


def _derive_path(
    *,
    entry_outcome: EntryExecutionOutcome | None,
    exit_reason: str | None,
    protection_trigger_type: str | None,
) -> tuple[PostFillPathOutcome | None, ForecastLabelState, str]:
    """Derive the conditional post-fill path label. Never labels a NO_FILL path."""
    if entry_outcome is EntryExecutionOutcome.NO_FILL or entry_outcome is None:
        return (None, ForecastLabelState.INSUFFICIENT_EVIDENCE, "NO_FILL_NO_PATH")
    if protection_trigger_type is not None and protection_trigger_type in (
        RISK_EXIT_TRIGGER_TYPES
    ):
        return (
            PostFillPathOutcome.RISK_EXIT,
            ForecastLabelState.RESOLVED,
            "RISK_EXIT_TRIGGER",
        )
    if exit_reason is None:
        return (None, ForecastLabelState.UNRESOLVED, "NO_EXIT_REASON")
    mapped = FORECAST_EXIT_REASON_PATH_MAP.get(exit_reason)
    if mapped is None:
        return (
            None,
            ForecastLabelState.UNRESOLVED,
            f"UNMAPPED_EXIT_REASON:{exit_reason}",
        )
    return (mapped[0], mapped[1], f"EXIT_REASON:{exit_reason}")


def _derive_realized_return(
    *,
    entry_outcome: EntryExecutionOutcome | None,
    net_pnl: float | None,
    capital_committed: float | None,
) -> tuple[float | None, ForecastLabelState, str]:
    """Derive the dimensionless realized net return.

    A no-trade or unfilled opportunity deploys no capital, so its realized net
    return is exactly zero and is never recorded as a loss. Otherwise the return
    is ``net_pnl / capital_committed`` (dimensionless, matching the F6 convention
    where ``0.012`` means plus one point two percent); if either figure is absent
    or non-finite the return is ``UNRESOLVED`` rather than guessed.
    """
    if entry_outcome is EntryExecutionOutcome.NO_FILL or entry_outcome is None:
        return (0.0, ForecastLabelState.RESOLVED, "NO_CAPITAL_DEPLOYED")
    if net_pnl is None or capital_committed is None or capital_committed <= 0.0:
        return (None, ForecastLabelState.UNRESOLVED, "RETURN_FIGURES_UNAVAILABLE")
    return (net_pnl / capital_committed, ForecastLabelState.RESOLVED, "NET_OVER_CAPITAL")


def _derive_fidelity(
    *,
    path_state: ForecastLabelState,
    lineage_completeness: str | None,
) -> ForecastFidelityGrade:
    """Derive the simulation-fidelity grade, never inferring grade A.

    The native paper path is grade B; an ambiguous path or incomplete lineage is
    grade C. Grade A is an exact-replay fidelity that no current canonical record
    attests, so it is never inferred from absence of a defect. Lineage is proven
    complete only by the exact canonical ``COMPLETE`` token: any other value,
    including an absent or unknown one, grades ``C`` rather than silently grading
    ``B`` for a value the canonical vocabulary does not attest.
    """
    if path_state is ForecastLabelState.INCOMPLETE_COVERAGE:
        return ForecastFidelityGrade.C
    if lineage_completeness != LINEAGE_COMPLETE:
        return ForecastFidelityGrade.C
    return ForecastFidelityGrade.B


def project_forecast_labels(
    *,
    disposition: str | None = None,
    entry_execution_state: str | None = None,
    intended_quantity: float | None = None,
    accepted_quantity: float | None = None,
    exit_reason: str | None = None,
    protection_trigger_type: str | None = None,
    terminal_status: str | None = None,
    net_pnl: float | None = None,
    capital_committed: float | None = None,
    lineage_completeness: str | None = None,
    exit_timestamp: datetime | str | None = None,
) -> ForecastEvidenceLabel:
    """Project committed canonical evidence onto the frozen F6 label contract.

    Pure and deterministic: identical inputs always yield an identical label, and
    the function reads no clock, environment, filesystem, database or network. Any
    input the canonical vocabulary does not unambiguously support fails closed.
    """
    malformed: list[str] = []
    disposition, err = _clean_token(disposition, field_name="disposition")
    malformed += [err] if err else []
    entry_execution_state, err = _clean_token(
        entry_execution_state, field_name="entry_execution_state"
    )
    malformed += [err] if err else []
    exit_reason, err = _clean_token(exit_reason, field_name="exit_reason")
    malformed += [err] if err else []
    protection_trigger_type, err = _clean_token(
        protection_trigger_type, field_name="protection_trigger_type"
    )
    malformed += [err] if err else []
    terminal_status, err = _clean_token(terminal_status, field_name="terminal_status")
    malformed += [err] if err else []

    if malformed:
        # A malformed canonical token is not evidence; fail closed, never guess.
        return ForecastEvidenceLabel(
            population=ForecastEvidencePopulation.UNRESOLVED,
            entry_outcome=None,
            entry_label_state=ForecastLabelState.UNRESOLVED,
            post_fill_outcome=None,
            post_fill_label_state=ForecastLabelState.UNRESOLVED,
            realized_net_return=None,
            realized_return_label_state=ForecastLabelState.UNRESOLVED,
            fidelity=ForecastFidelityGrade.C,
            label_available_at=None,
            exit_reason=None,
            entry_execution_state=None,
            reason="|".join(malformed),
        )

    # A terminal status outside the frozen canonical set is not evidence.
    if terminal_status is not None and terminal_status not in TERMINAL_STATUSES:
        return ForecastEvidenceLabel(
            population=ForecastEvidencePopulation.UNRESOLVED,
            entry_outcome=None,
            entry_label_state=ForecastLabelState.UNRESOLVED,
            post_fill_outcome=None,
            post_fill_label_state=ForecastLabelState.UNRESOLVED,
            realized_net_return=None,
            realized_return_label_state=ForecastLabelState.UNRESOLVED,
            fidelity=ForecastFidelityGrade.C,
            label_available_at=None,
            exit_reason=exit_reason,
            entry_execution_state=entry_execution_state,
            reason=f"UNKNOWN_TERMINAL_STATUS:{terminal_status}",
        )

    clean_net_pnl = _optional_finite_number(net_pnl)
    clean_capital = _optional_finite_number(capital_committed)
    clean_intended = _optional_finite_number(intended_quantity)
    clean_accepted = _optional_finite_number(accepted_quantity)

    population, entry_outcome, entry_state, entry_reason = _derive_entry(
        disposition=disposition,
        entry_execution_state=entry_execution_state,
        intended_quantity=clean_intended,
        accepted_quantity=clean_accepted,
    )

    # A terminal ``UNRESOLVED`` status asserts nothing, so no path or return label
    # may be claimed from it.
    if terminal_status == "UNRESOLVED":
        return ForecastEvidenceLabel(
            population=ForecastEvidencePopulation.UNRESOLVED,
            entry_outcome=None,
            entry_label_state=ForecastLabelState.UNRESOLVED,
            post_fill_outcome=None,
            post_fill_label_state=ForecastLabelState.UNRESOLVED,
            realized_net_return=None,
            realized_return_label_state=ForecastLabelState.UNRESOLVED,
            fidelity=ForecastFidelityGrade.C,
            label_available_at=None,
            exit_reason=exit_reason,
            entry_execution_state=entry_execution_state,
            reason="TERMINAL_STATUS_UNRESOLVED",
        )

    path_outcome, path_state, path_reason = _derive_path(
        entry_outcome=entry_outcome,
        exit_reason=exit_reason,
        protection_trigger_type=protection_trigger_type,
    )
    realized, return_state, return_reason = _derive_realized_return(
        entry_outcome=entry_outcome,
        net_pnl=clean_net_pnl,
        capital_committed=clean_capital,
    )
    fidelity = _derive_fidelity(
        path_state=path_state, lineage_completeness=lineage_completeness
    )

    # If the entry itself is unresolved, nothing downstream may be claimed either.
    if entry_state is not ForecastLabelState.RESOLVED:
        return ForecastEvidenceLabel(
            population=ForecastEvidencePopulation.UNRESOLVED,
            entry_outcome=None,
            entry_label_state=ForecastLabelState.UNRESOLVED,
            post_fill_outcome=None,
            post_fill_label_state=ForecastLabelState.UNRESOLVED,
            realized_net_return=None,
            realized_return_label_state=ForecastLabelState.UNRESOLVED,
            fidelity=ForecastFidelityGrade.C,
            label_available_at=None,
            exit_reason=exit_reason,
            entry_execution_state=entry_execution_state,
            reason=entry_reason,
        )

    population = _population_from_labels(
        entry_outcome=entry_outcome, path_state=path_state, population=population
    )

    return ForecastEvidenceLabel(
        population=population,
        entry_outcome=entry_outcome,
        entry_label_state=entry_state,
        post_fill_outcome=path_outcome,
        post_fill_label_state=path_state,
        realized_net_return=realized,
        realized_return_label_state=return_state,
        fidelity=fidelity,
        label_available_at=_optional_utc(exit_timestamp),
        exit_reason=exit_reason,
        entry_execution_state=entry_execution_state,
        reason=f"{entry_reason}|{path_reason}|{return_reason}",
    )


def _population_from_labels(
    *,
    entry_outcome: EntryExecutionOutcome | None,
    path_state: ForecastLabelState,
    population: ForecastEvidencePopulation,
) -> ForecastEvidencePopulation:
    """Refine the reported population class from the resolved labels."""
    if population in (
        ForecastEvidencePopulation.CASH_NO_TRADE,
        ForecastEvidencePopulation.NO_FILL,
    ):
        return population
    if entry_outcome is EntryExecutionOutcome.FULL_FILL:
        if path_state is ForecastLabelState.INCOMPLETE_COVERAGE:
            return ForecastEvidencePopulation.INCOMPLETE_COVERAGE
        if path_state is ForecastLabelState.RESOLVED:
            return ForecastEvidencePopulation.FULL_FILL
        return ForecastEvidencePopulation.UNRESOLVED
    if entry_outcome is EntryExecutionOutcome.PARTIAL_FILL:
        if path_state is ForecastLabelState.INCOMPLETE_COVERAGE:
            return ForecastEvidencePopulation.INCOMPLETE_COVERAGE
        if path_state is ForecastLabelState.RESOLVED:
            return ForecastEvidencePopulation.PARTIAL_FILL
        return ForecastEvidencePopulation.UNRESOLVED
    return ForecastEvidencePopulation.UNRESOLVED


__all__ = [
    "FORECAST_EXIT_REASON_PATH_MAP",
    "FORECAST_LABEL_PROJECTION_VERSION",
    "NON_ADMISSION_DISPOSITIONS",
    "NO_FILL_EXECUTION_STATES",
    "NON_TERMINAL_EXECUTION_STATES",
    "POST_FILL_PATH_ANCHOR",
    "RISK_EXIT_TRIGGER_TYPES",
    "ForecastEvidenceLabel",
    "ForecastEvidencePopulation",
    "ForecastLabelProjectionError",
    "project_forecast_labels",
]
