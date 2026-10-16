"""R5-E integration: one planned capital reservation becomes canonical evidence.

The R5-C capital engine measures one book and the R5-D selector plans a
reservation against one explicit capital version. Until this slice those objects
had no durable home: a planned reservation was a pure value and the R5-C
reservation *commit* witness is process-scoped
(``PROCESS_SCOPED_IN_MEMORY_WITNESS``). This module is the seam that gives one
planned reservation exactly one durable owner - the canonical writer's existing
atomic admission (``SC-07``) - and nothing else.

What this seam deliberately is not
----------------------------------

* **No second admission authority.** Every write is one call to the existing
  ``WriterClient.admit_paper_opportunity`` RPC carrying a frozen
  ``PaperAdmissionRequest``; every read is the existing
  ``get_paper_portfolio_state`` projection. There is no allocator, cash ledger,
  event store, table, scheduler or writer intent here, and the local outcome is
  *reported* from the canonical ack rather than decided.
* **No trading, order, exchange or funded authority.** ``grants_execution_authority``
  is hard-coded ``False``; nothing is placed, modified or cancelled, no exchange
  client is imported, no secret is read.
* **No wall clock and no environment variable.** The caller supplies the exact
  ``datetime`` the disposition is stamped with, the caller supplies the
  disposition identity, and the capital limits come from the frozen writer-owned
  policy resolved here - never from the plan, the snapshot or the caller.
* **No silent default admission.** The R5-C binding set names risk,
  concentration, liquidity and sleeve, and the canonical reservation surfaces
  still declare all four *unbound*. So when the caller does not explicitly
  declare a binding the frozen policy can resolve, this seam abstains before it
  touches the client at all.

Outcomes
--------

``ADMITTED``       the canonical writer committed exactly one reservation for
                   this disposition identity and the canonical book was read
                   back.
``ABSTAINED``      the attempt cannot be proven safe with the frozen canonical
                   surfaces, so nothing was written: an unresolvable declared
                   constraint or capital policy is never treated as unlimited, and
                   an unreadable canonical book is never treated as an empty one.
``REFUSED``        the canonical authority, the identity binding or the plan
                   freshness refused this attempt. The canonical tokens are
                   surfaced verbatim and are never reinterpreted.
``INDETERMINATE``  the canonical outcome could not be read back (for example a
                   lost ack). The frozen request identity in the returned record
                   is what a re-attempt must reuse; a re-attempt that mutates the
                   request under the same identity is refused by the writer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.opip.canonical.client import WriterClient
from app.opip.canonical.models import (
    BOUND_RESERVATION_CONSTRAINTS,
    RESERVATION_CONSTRAINTS,
    UNBOUND_RESERVATION_CONSTRAINTS,
)
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    QualifiedOpportunityDisposition,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_CAPITAL_POLICY_VERSION,
    PaperAdmissionAck,
    PaperAdmissionRequest,
    resolve_capital_policy,
    validate_admission_request,
)
from app.opip.contracts.paper_outcome import QUOTE_CURRENCIES
from app.opip.contracts.portfolio import (
    PortfolioContractError,
    PortfolioReservation,
    PortfolioReservationPlan,
    assert_reservation_plan_current,
)
from app.opip.contracts.serialization import iso_z
from app.opip.contracts.temporal import require_utc
from app.services.capital_rotation_intelligence import (
    CapitalEnvironment,
    CapitalSnapshot,
)

#: Schema version of this seam's disposition record.
CAPITAL_ADMISSION_SCHEMA_VERSION = "capital-admission-v1"

#: The outcomes. Each is a distinct disposition; none aliases another.
CAPITAL_ADMISSION_ADMITTED = "ADMITTED"
CAPITAL_ADMISSION_ABSTAINED = "ABSTAINED"
CAPITAL_ADMISSION_REFUSED = "REFUSED"
CAPITAL_ADMISSION_INDETERMINATE = "INDETERMINATE"
CAPITAL_ADMISSION_STATUSES: frozenset[str] = frozenset(
    {
        CAPITAL_ADMISSION_ADMITTED,
        CAPITAL_ADMISSION_ABSTAINED,
        CAPITAL_ADMISSION_REFUSED,
        CAPITAL_ADMISSION_INDETERMINATE,
    }
)

#: Deterministic reasons. A reason never replaces the canonical tokens, which are
#: surfaced verbatim alongside it: it names either a committed reservation
#: (``CAPITAL_ADMISSION_CANONICAL_ACCEPTED``) or why this seam did not reserve.
CAPITAL_ADMISSION_CANONICAL_ACCEPTED = "CAPITAL_CANONICAL_ACCEPTED"
CAPITAL_ADMISSION_INPUT_INVALID = "CAPITAL_ADMISSION_INPUT_INVALID"
CAPITAL_ADMISSION_ENVIRONMENT_REFUSED = "CAPITAL_ENVIRONMENT_NOT_PAPER_LEDGER"
CAPITAL_ADMISSION_CURRENCY_REFUSED = "CAPITAL_QUOTE_CURRENCY_UNSUPPORTED"
CAPITAL_ADMISSION_PLAN_STALE = "CAPITAL_PLAN_PORTFOLIO_VERSION_STALE"
CAPITAL_ADMISSION_RESERVATION_INACTIVE = "CAPITAL_RESERVATION_NOT_ACTIVE"
CAPITAL_ADMISSION_CONSTRAINT_UNBOUND = "CAPITAL_CONSTRAINT_UNBOUND"
CAPITAL_ADMISSION_POLICY_UNRESOLVED = "CAPITAL_POLICY_VERSION_UNRESOLVED"
CAPITAL_ADMISSION_PORTFOLIO_UNAVAILABLE = "CAPITAL_PORTFOLIO_STATE_UNAVAILABLE"
CAPITAL_ADMISSION_CANONICAL_REFUSAL = "CAPITAL_CANONICAL_REFUSAL"
CAPITAL_ADMISSION_CANONICAL_UNKNOWN = "CAPITAL_CANONICAL_OUTCOME_UNKNOWN"
CAPITAL_ADMISSION_ACK_UNUSABLE = "CAPITAL_CANONICAL_ACK_UNUSABLE"

#: The caller's default binding: every constraint the R5-C capital engine
#: measures. It is deliberately the *conservative* default, because the canonical
#: reservation surfaces still declare risk, concentration, liquidity and sleeve
#: unbound - so the default outcome of this seam is an abstention, not an
#: admission. A caller that can only be bound by the frozen writer policy passes
#: ``BOUND_RESERVATION_CONSTRAINTS`` explicitly, which narrows the claim and can
#: never widen it.
CAPITAL_ADMISSION_DEFAULT_CONSTRAINTS: tuple[str, ...] = tuple(
    BOUND_RESERVATION_CONSTRAINTS + UNBOUND_RESERVATION_CONSTRAINTS
)

#: Canonical ack tokens this seam reads. They are the writer's vocabulary and are
#: only ever compared, never invented or renamed here.
_CANONICAL_STATUS_DUPLICATE_OK = "DUPLICATE_OK"
_ADMITTED_DISPOSITION = QualifiedOpportunityDisposition.ADMITTED.value

#: The failures a transport read or write may raise. They fail closed into a
#: disposition record instead of escaping as an exception.
_TRANSPORT_FAILURES = (OSError, RuntimeError, ValueError)


@dataclass(frozen=True)
class CapitalAdmissionDisposition:
    """What happened to one planned reservation's canonical admission.

    Durable evidence of one attempt, and nothing more: it reports the canonical
    identity that owns the reservation and the canonical tokens that decided it.
    It cannot reserve, release, widen or authorize anything, and
    ``grants_execution_authority`` is always ``False``.
    """

    status: str
    reason: str
    detail: str
    disposition_id: str
    #: The frozen request identity a re-attempt must reuse byte-for-byte. The
    #: writer proves idempotency by comparing the whole frozen payload, so these
    #: two values are the caller's only honest retry inputs.
    expected_portfolio_version: int | None = None
    disposition_time: str | None = None
    #: The plan's own identity and book. Reported, never inferred downstream.
    candidate_id: str | None = None
    plan_id: str | None = None
    plan_portfolio_version: str | None = None
    account: str | None = None
    capital_engine: str | None = None
    canonical_engine: str = ENGINE_OPIP_PAPER_V2
    quote_currency: str | None = None
    direction: str | None = None
    requested_reservation_amount: float | None = None
    #: The canonical ack, surfaced verbatim.
    canonical_status: str | None = None
    canonical_disposition: str | None = None
    canonical_error_code: str | None = None
    canonical_detail: str | None = None
    canonical_portfolio_version: int | None = None
    reservation_id: str | None = None
    paper_trade_id: str | None = None
    #: The canonical book after the attempt, read back through the existing
    #: portfolio projection. ``None`` when the read back was not possible.
    active_reservations: int | None = None
    reserved_capital: float | None = None
    reservation_committed: bool = False
    replay: bool = False
    #: True only when a reservation was committed *and* every constraint the
    #: caller declared resolvable is canonical-bound. It is never proof that the
    #: declared-unbound R5-C constraints were enforced.
    policy_binding_complete: bool = False
    bound_constraints: tuple[str, ...] = ()
    unbound_constraints: tuple[str, ...] = ()
    schema_version: str = CAPITAL_ADMISSION_SCHEMA_VERSION
    #: A reservation ledger read-back never confers trading or execution authority.
    grants_execution_authority: bool = False
    #: The committed decision-context identity this disposition belongs to, so a
    #: lost acknowledgement retries against the same canonical commitment.
    decision_context_id: str | None = None

    def __post_init__(self) -> None:
        if self.status not in CAPITAL_ADMISSION_STATUSES:
            raise ValueError(f"unknown capital admission status: {self.status!r}")
        if self.grants_execution_authority:
            raise ValueError("a capital admission disposition grants no authority")

    def to_dict(self) -> dict[str, Any]:
        """The durable payload: every declared fact, and no authority."""

        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
            "disposition_id": self.disposition_id,
            "expected_portfolio_version": self.expected_portfolio_version,
            "disposition_time": self.disposition_time,
            "candidate_id": self.candidate_id,
            "plan_id": self.plan_id,
            "decision_context_id": self.decision_context_id,
            "plan_portfolio_version": self.plan_portfolio_version,
            "account": self.account,
            "capital_engine": self.capital_engine,
            "canonical_engine": self.canonical_engine,
            "quote_currency": self.quote_currency,
            "direction": self.direction,
            "requested_reservation_amount": self.requested_reservation_amount,
            "canonical_status": self.canonical_status,
            "canonical_disposition": self.canonical_disposition,
            "canonical_error_code": self.canonical_error_code,
            "canonical_detail": self.canonical_detail,
            "canonical_portfolio_version": self.canonical_portfolio_version,
            "reservation_id": self.reservation_id,
            "paper_trade_id": self.paper_trade_id,
            "active_reservations": self.active_reservations,
            "reserved_capital": self.reserved_capital,
            "reservation_committed": self.reservation_committed,
            "replay": self.replay,
            "policy_binding_complete": self.policy_binding_complete,
            "bound_constraints": list(self.bound_constraints),
            "unbound_constraints": list(self.unbound_constraints),
            "grants_execution_authority": self.grants_execution_authority,
        }


# ---------------------------------------------------------------------------
# Canonical acknowledgement tokens this seam reads
# ---------------------------------------------------------------------------

_CANONICAL_STATUS_OK = "OK"
_CANONICAL_STATUS_REJECTED = "REJECTED"
_CANONICAL_STATUS_RETRYABLE = "RETRYABLE"
_PORTFOLIO_STATE_OK = "OK"

#: The dispositions the writer reports on an ``OK`` status when it declined to
#: reserve. Both are refusals of *this* attempt, so both are reported as REFUSED
#: with the canonical tokens verbatim.
_REFUSING_DISPOSITIONS: frozenset[str] = frozenset(
    {
        QualifiedOpportunityDisposition.CAPACITY_REJECTED.value,
        QualifiedOpportunityDisposition.CAPITAL_REJECTED.value,
    }
)


# ---------------------------------------------------------------------------
# Local helpers: normalization and reporting
# ---------------------------------------------------------------------------


def _text(value: Any, *, field_name: str) -> str:
    """Require exact canonical text. Whitespace-only is not text."""
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be text")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} is required")
    return stripped


def _declared_constraints(
    binding_constraints: Iterable[str] | None,
) -> tuple[str, ...]:
    """Normalize the caller's declared binding, refusing unknown names.

    Omission means the conservative R5-C default - every constraint the capital
    engine measures - so the default outcome here is an abstention unless the
    caller explicitly narrows the claim to a constraint the frozen policy
    resolves. Narrowing can never widen the claim.
    """

    if binding_constraints is None:
        return CAPITAL_ADMISSION_DEFAULT_CONSTRAINTS
    if isinstance(binding_constraints, (str, bytes)):
        raise TypeError(
            "binding_constraints must be an iterable of constraint names, not text"
        )
    declared: list[str] = []
    for item in binding_constraints:
        name = _text(item, field_name="binding_constraints")
        if name not in RESERVATION_CONSTRAINTS:
            raise ValueError(f"unknown reservation constraint: {name!r}")
        if name not in declared:
            declared.append(name)
    if not declared:
        raise ValueError("binding_constraints must declare at least one constraint")
    return tuple(declared)


def _split_constraints(
    declared: tuple[str, ...],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split a declaration into the canonical-bound and canonical-unbound part."""

    bound = tuple(name for name in declared if name in BOUND_RESERVATION_CONSTRAINTS)
    unbound = tuple(name for name in declared if name in UNBOUND_RESERVATION_CONSTRAINTS)
    return bound, unbound


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _record(
    *,
    status: str,
    reason: str,
    detail: str,
    disposition_id: str,
    plan: PortfolioReservationPlan | None = None,
    reservation: PortfolioReservation | None = None,
    snapshot: CapitalSnapshot | None = None,
    expected_portfolio_version: int | None = None,
    disposition_time: str | None = None,
    declared: tuple[str, ...] = (),
    **overrides: Any,
) -> CapitalAdmissionDisposition:
    """Build one disposition record. Reporting only: it decides nothing.

    ``overrides`` carries the canonical ack facts and the retry bookkeeping of one
    branch. The dataclass constructor remains the only validator, so an unknown key
    is a programming error rather than a silently reported fact.
    """

    bound, unbound = _split_constraints(declared)
    fields: dict[str, Any] = {
        "status": status,
        "reason": reason,
        "detail": detail,
        "disposition_id": disposition_id,
        "expected_portfolio_version": expected_portfolio_version,
        "disposition_time": disposition_time,
        "candidate_id": None if reservation is None else reservation.candidate_id,
        "plan_id": None if plan is None else plan.plan_id,
        "plan_portfolio_version": None if plan is None else plan.portfolio_version,
        "account": None if snapshot is None else snapshot.account,
        "capital_engine": None if snapshot is None else snapshot.engine,
        "quote_currency": None if snapshot is None else snapshot.currency,
        "direction": None if reservation is None else str(reservation.direction.value),
        "requested_reservation_amount": (
            None if reservation is None else float(reservation.reserved_capital)
        ),
        "bound_constraints": bound,
        "unbound_constraints": unbound,
        "policy_binding_complete": bool(
            status == CAPITAL_ADMISSION_ADMITTED and declared and not unbound
        ),
    }
    fields.update(overrides)
    return CapitalAdmissionDisposition(**fields)




def _explicit_version(value: Any) -> int | None:
    """The caller's declared canonical portfolio version, or ``None``."""

    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("expected_portfolio_version must be an integer")
    if value < 0:
        raise ValueError("expected_portfolio_version must not be negative")
    return int(value)


def _read_back(
    client: WriterClient,
    record: CapitalAdmissionDisposition,
    quote_currency: str,
) -> CapitalAdmissionDisposition:
    """Report the canonical book after a proven commit. Supplementary only.

    The commit is already proven by the writer's own acknowledgement, so a
    degraded read back leaves the counted facts unset instead of downgrading a
    committed reservation into an unknown outcome.
    """

    try:
        state = client.get_paper_portfolio_state(quote_currency)
    except _TRANSPORT_FAILURES:
        return record
    if str(getattr(state, "status", "") or "") != _PORTFOLIO_STATE_OK:
        return record
    return replace(
        record,
        active_reservations=_optional_int(getattr(state, "active_reservations", None)),
        reserved_capital=_optional_float(getattr(state, "reserved_capital", None)),
    )


def admit_planned_reservation(
    plan: PortfolioReservationPlan,
    *,
    client: WriterClient,
    snapshot: CapitalSnapshot,
    disposition_id: str,
    decision_context_id: str,
    disposition_time: datetime,
    candidate_id: str | None = None,
    binding_constraints: Iterable[str] | None = None,
    capital_policy_version: str = PAPER_CAPITAL_POLICY_VERSION,
    expected_portfolio_version: int | None = None,
) -> CapitalAdmissionDisposition:
    """Attempt the canonical admission of exactly one planned reservation.

    The frozen attempt order is local first and canonical second:

    1. normalize the identity inputs and the declared binding;
    2. refuse a snapshot that is not a paper-ledger book of a canonical quote
       currency, or a plan that is not the plan of that book;
    3. abstain when a declared constraint is canonical-unbound or when the frozen
       capital policy does not resolve - nothing has been read or written yet;
    4. select exactly one active planned reservation;
    5. resolve the canonical freshness guard: a caller-supplied
       ``expected_portfolio_version`` is used verbatim, otherwise the canonical
       book is read once, here, and that exact version is the guard;
    6. call ``CanonicalWriter.admit_paper_opportunity`` exactly once - never twice,
       and never with a locally invented decision; and
    7. report the canonical book back after a proven commit.

    ``expected_portfolio_version`` is read *fresh* when it is omitted and reused
    byte-for-byte when it is supplied, because the writer proves an idempotent
    replay by comparing the whole frozen request payload: a re-attempt of a
    recorded disposition identity has to reproduce the recorded version exactly. A
    supplied version the canonical book has already moved past is refused by the
    writer itself, atomically, as ``STALE_PORTFOLIO_VERSION``.

    The seam never reserves, releases, widens or authorizes anything of its own: it
    translates one planned reservation into the existing frozen admission request
    and reports what the canonical writer decided. Nothing is retried inside it, so
    an unreadable canonical outcome is reported as ``INDETERMINATE`` together with
    the exact identity a re-attempt must reuse.
    """

    try:
        resolved_disposition_id = _text(disposition_id, field_name="disposition_id")
        resolved_context_id = _text(
            decision_context_id, field_name="decision_context_id"
        )
        instant = require_utc(disposition_time, field_name="disposition_time")
        stamped = iso_z(instant, field_name="disposition_time")
        declared = _declared_constraints(binding_constraints)
        resolved_candidate_id = (
            None
            if candidate_id is None
            else _text(candidate_id, field_name="candidate_id")
        )
        explicit_version = _explicit_version(expected_portfolio_version)
    except (TypeError, ValueError) as exc:
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=CAPITAL_ADMISSION_INPUT_INVALID,
            detail=f"{type(exc).__name__}: {exc}",
            disposition_id=disposition_id if isinstance(disposition_id, str) else "",
        )

    if not isinstance(plan, PortfolioReservationPlan):
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=CAPITAL_ADMISSION_INPUT_INVALID,
            detail="plan must be a PortfolioReservationPlan",
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
        )
    if not isinstance(snapshot, CapitalSnapshot):
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=CAPITAL_ADMISSION_INPUT_INVALID,
            detail="snapshot must be a CapitalSnapshot",
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            plan=plan,
        )

    def _refuse(reason: str, detail: str, **extra: Any) -> CapitalAdmissionDisposition:
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=reason,
            detail=detail,
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            **extra,
        )

    def _abstain(reason: str, detail: str, **extra: Any) -> CapitalAdmissionDisposition:
        return _record(
            status=CAPITAL_ADMISSION_ABSTAINED,
            reason=reason,
            detail=detail,
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            **extra,
        )

    def _unknown(reason: str, detail: str, **extra: Any) -> CapitalAdmissionDisposition:
        return _record(
            status=CAPITAL_ADMISSION_INDETERMINATE,
            reason=reason,
            detail=detail,
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            **extra,
        )

    book: dict[str, Any] = {"plan": plan, "snapshot": snapshot}

    environment = getattr(snapshot.environment, "value", snapshot.environment)
    if environment != CapitalEnvironment.PAPER_LEDGER.value:
        return _refuse(
            CAPITAL_ADMISSION_ENVIRONMENT_REFUSED,
            "the capital snapshot is not a paper-ledger book, so no canonical paper "
            f"admission may be attempted (environment={environment!r})",
            **book,
        )
    if snapshot.currency not in QUOTE_CURRENCIES:
        return _refuse(
            CAPITAL_ADMISSION_CURRENCY_REFUSED,
            "the capital snapshot quote currency is not a canonical paper quote "
            f"currency (quote_currency={snapshot.currency!r})",
            **book,
        )
    try:
        assert_reservation_plan_current(
            plan, current_portfolio_version=snapshot.portfolio_version
        )
    except (PortfolioContractError, TypeError, ValueError) as exc:
        return _refuse(
            CAPITAL_ADMISSION_PLAN_STALE,
            f"the reservation plan is not the plan of this book: {exc}",
            **book,
        )

    bound, unbound = _split_constraints(declared)
    if unbound:
        return _abstain(
            CAPITAL_ADMISSION_CONSTRAINT_UNBOUND,
            "a declared constraint cannot be resolved by the frozen canonical "
            "reservation surfaces, so no admission may be attempted "
            f"(unbound={list(unbound)} bound={list(bound)})",
            **book,
        )
    try:
        policy = resolve_capital_policy(capital_policy_version)
    except (TypeError, ValueError) as exc:
        return _abstain(
            CAPITAL_ADMISSION_POLICY_UNRESOLVED,
            f"capital policy {capital_policy_version!r} does not resolve, so no "
            f"canonical limit can be proven and nothing was attempted: {exc}",
            **book,
        )

    active = [item for item in plan.reservations if item.is_active]
    if resolved_candidate_id is None:
        if len(active) != 1:
            return _refuse(
                CAPITAL_ADMISSION_INPUT_INVALID,
                f"the plan holds {len(active)} active reservations; candidate_id must "
                "name exactly one",
                **book,
            )
        reservation = active[0]
    else:
        selected = [
            item for item in active if item.candidate_id == resolved_candidate_id
        ]
        if not selected:
            return _refuse(
                CAPITAL_ADMISSION_RESERVATION_INACTIVE,
                "the plan holds no active reservation for candidate "
                f"{resolved_candidate_id!r}",
                **book,
            )
        if len(selected) > 1:
            return _refuse(
                CAPITAL_ADMISSION_INPUT_INVALID,
                f"the plan holds {len(selected)} active reservations for candidate "
                f"{resolved_candidate_id!r}; a plan holds one reservation per candidate",
                **book,
            )
        reservation = selected[0]

    booked: dict[str, Any] = {**book, "reservation": reservation}

    if explicit_version is not None:
        expected = explicit_version
    else:
        try:
            state = client.get_paper_portfolio_state(snapshot.currency)
        except _TRANSPORT_FAILURES as exc:
            return _abstain(
                CAPITAL_ADMISSION_PORTFOLIO_UNAVAILABLE,
                "the canonical paper book could not be read "
                f"({type(exc).__name__}), so the freshness guard cannot be proven and "
                "nothing was attempted",
                **booked,
            )
        status = str(getattr(state, "status", "") or "")
        version = getattr(state, "portfolio_version", None)
        if status != _PORTFOLIO_STATE_OK or version is None:
            return _abstain(
                CAPITAL_ADMISSION_PORTFOLIO_UNAVAILABLE,
                "the canonical paper book read is not usable "
                f"(status={status or 'UNKNOWN'} portfolio_version={version!r}), so the "
                "freshness guard cannot be proven and nothing was attempted",
                **booked,
            )
        expected = int(version)

    request = PaperAdmissionRequest(
        disposition_id=resolved_disposition_id,
        decision_context_id=resolved_context_id,
        # One seam attempt per disposition identity: the sequence is part of the
        # frozen payload, so a re-attempt reproduces it exactly instead of
        # inventing a new position in a sequence this seam does not own.
        disposition_seq=0,
        quote_currency=snapshot.currency,
        requested_capital=float(reservation.reserved_capital),
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": stamped,
        },
        expected_portfolio_version=expected,
        capital_policy_version=policy.policy_version,
        portfolio_equity_limit=policy.portfolio_equity_limit,
        portfolio_position_limit=policy.portfolio_position_limit,
        requested_reservation_amount=float(reservation.reserved_capital),
        direction=str(reservation.direction.value),
    )

    try:
        validate_admission_request(request)
    except (TypeError, ValueError) as exc:
        return _refuse(
            CAPITAL_ADMISSION_INPUT_INVALID,
            "the planned reservation cannot be translated into an admissible "
            f"canonical request, so nothing was sent: {exc}",
            **booked,
        )

    try:
        ack = client.admit_paper_opportunity(request)
    except _TRANSPORT_FAILURES as exc:
        return _unknown(
            CAPITAL_ADMISSION_CANONICAL_UNKNOWN,
            "the canonical admission outcome is unknown because the call did not "
            f"complete ({type(exc).__name__}: {exc}); nothing is assumed about "
            "whether a reservation was committed, and a re-attempt must reuse exactly "
            "the reported disposition identity, disposition_time and expected "
            "portfolio version",
            expected_portfolio_version=expected,
            **booked,
        )

    if not isinstance(ack, PaperAdmissionAck):
        return _unknown(
            CAPITAL_ADMISSION_ACK_UNUSABLE,
            "the canonical writer did not return a PaperAdmissionAck "
            f"({type(ack).__name__}), so nothing is assumed about whether a "
            "reservation was committed",
            expected_portfolio_version=expected,
            **booked,
        )

    canonical_status = str(getattr(ack.status, "value", ack.status))
    ack_disposition = getattr(ack, "disposition", None)
    canonical_disposition = (
        None
        if ack_disposition is None
        else str(getattr(ack_disposition, "value", ack_disposition))
    )
    canonical_facts: dict[str, Any] = {
        "canonical_status": canonical_status,
        "canonical_disposition": canonical_disposition,
        "canonical_error_code": _optional_text(getattr(ack, "error_code", None)),
        "canonical_detail": _optional_text(getattr(ack, "detail", None)),
        "canonical_portfolio_version": _optional_int(
            getattr(ack, "portfolio_version", None)
        ),
        "reservation_id": _optional_text(getattr(ack, "reservation_id", None)),
        "paper_trade_id": _optional_text(getattr(ack, "paper_trade_id", None)),
    }

    # Only the writer's own refusal status is a refusal. ``DUPLICATE_OK`` is proof
    # of the opposite - the writer reports the reservation this disposition
    # identity already holds - and every other status it may return for an
    # admission (``RETRYABLE``, for example ``WORKER_UNHEALTHY`` or
    # ``SQLITE_ERROR``) is explicitly *unsettled*: it is not proof that no
    # reservation was committed, so it may never be reported as a refusal.
    if canonical_status == _CANONICAL_STATUS_REJECTED:
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=CAPITAL_ADMISSION_CANONICAL_REFUSAL,
            detail=(
                "the canonical writer recorded no admission for this disposition and "
                f"left the reservation book unchanged (status={canonical_status}); a "
                "re-attempt must reuse exactly this request"
            ),
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            expected_portfolio_version=expected,
            **canonical_facts,
            **booked,
        )

    if canonical_status not in (
        _CANONICAL_STATUS_OK,
        _CANONICAL_STATUS_DUPLICATE_OK,
    ):
        return _unknown(
            CAPITAL_ADMISSION_ACK_UNUSABLE,
            "the canonical writer answered this admission with a status it does not "
            f"settle ({canonical_status!r}), so nothing is assumed about whether a "
            "reservation was committed and a re-attempt must reuse exactly this "
            "request",
            expected_portfolio_version=expected,
            **canonical_facts,
            **booked,
        )

    if canonical_disposition in _REFUSING_DISPOSITIONS:
        return _record(
            status=CAPITAL_ADMISSION_REFUSED,
            reason=CAPITAL_ADMISSION_CANONICAL_REFUSAL,
            detail=(
                "the canonical writer evaluated this request against its own "
                "reservation book and declined to reserve "
                f"(status={canonical_status} disposition={canonical_disposition}); "
                "the canonical tokens are surfaced verbatim"
            ),
            disposition_id=resolved_disposition_id,
            disposition_time=stamped,
            declared=declared,
            expected_portfolio_version=expected,
            **canonical_facts,
            **booked,
        )

    if canonical_disposition != _ADMITTED_DISPOSITION:
        return _unknown(
            CAPITAL_ADMISSION_ACK_UNUSABLE,
            "the canonical writer acknowledged this request with an admission "
            f"disposition this seam does not define ({canonical_disposition!r}), so "
            "the reservation state is not assumed either way",
            expected_portfolio_version=expected,
            **canonical_facts,
            **booked,
        )

    replayed = canonical_status == _CANONICAL_STATUS_DUPLICATE_OK
    committed = _record(
        status=CAPITAL_ADMISSION_ADMITTED,
        reason=CAPITAL_ADMISSION_CANONICAL_ACCEPTED,
        detail=(
            "the canonical writer committed this reservation under this disposition "
            f"identity (status={canonical_status} replay={replayed})"
        ),
        disposition_id=resolved_disposition_id,
        disposition_time=stamped,
        declared=declared,
        expected_portfolio_version=expected,
        replay=replayed,
        # The canonical writer's own acknowledgement is the proof of the commit, so
        # the record says so. A degraded read back leaves the counted facts unset
        # instead of downgrading a committed reservation into an unknown outcome.
        reservation_committed=True,
        **canonical_facts,
        **booked,
    )
    return _read_back(client, committed, snapshot.currency)

