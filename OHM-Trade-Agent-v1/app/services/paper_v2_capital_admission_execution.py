"""R5-E research-paper execution: committed capital admission -> F8 execution.

This module is the composition seam that connects *committed canonical capital
admission* to the single Paper-v2 execution producer. It exists because the two
authorities already exist and must not be duplicated:

* capital is decided and reserved by exactly one authority: the canonical
  admission RPC ``WriterClient.admit_paper_opportunity``, consumed by the parent
  R5-E capital-admission increment, whose committed evidence this seam only
  reads;
* paper execution is produced by exactly one producer,
  ``app.services.paper_v2_execution.run_paper_v2_opportunity``, which reuses a
  committed admission instead of re-deciding capital.

The seam therefore *consumes* committed admission evidence and dispatches one
execution. It does not admit, reserve, release, widen, retry or decide anything
about capital, and it writes no canonical record of its own:

1. read the committed Paper-v2 progress for this opportunity's deterministic
   disposition identity through the existing ``get_paper_v2_execution_state``
   RPC (the identity the producer derives, so no new identity is minted);
2. refuse, without executing, when that state is unusable, is not admitted, has
   no committed reservation, or its committed admission does not describe the
   execution intent this opportunity asks for (currency and requested
   reservation amount are compared exactly against the committed values);
3. dispatch exactly one F8 execution and report the producer's own canonical
   outcome verbatim, including the producer's zero-fill and terminal statuses.

Every refusal happens before the producer is called, so an unprovable execution
never reaches a market-data read or a canonical write. Nothing here reads the
environment, a secret or a clock: the caller supplies ``now`` as it does for the
producer, and no economic, fee, slippage, latency or spread number is accepted
or invented - the only economics are the ones the producer commits as evidence.

Authority is zero. Every outcome carries ``grants_execution_authority=False``
and the record refuses to be constructed with it ``True``. This module is
dormant: no runtime root imports it, it is not wired into the router, and paper
trading stays isolated from production authority under ``OPIP_PAPER_V2_MODE=off``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.paper_v2_execution import (
    PaperV2ExecutionError,
    PaperV2ExecutionResult,
    PaperV2Opportunity,
    build_disposition_id,
    run_paper_v2_opportunity,
)

#: Closed refusal vocabulary. The seam adds transport-level blocking codes only;
#: every executed outcome carries the producer's own status verbatim.
OUTCOME_EXECUTED = "EXECUTED"
OUTCOME_BLOCKED = "BLOCKED"
OUTCOME_FAILED = "FAILED"

REASON_UNUSABLE_CANONICAL_STATE = "UNUSABLE_CANONICAL_STATE"
REASON_NO_COMMITTED_ADMISSION = "NO_COMMITTED_ADMISSION"
REASON_UNCOMMITTED_RESERVATION = "UNCOMMITTED_RESERVATION"
REASON_ADMISSION_INTENT_DIVERGENCE = "ADMISSION_INTENT_DIVERGENCE"
REASON_PRODUCER_FAILED = "PRODUCER_FAILED"

ADMISSION_SOURCE = "COMMITTED_CANONICAL_ADMISSION"


@dataclass(frozen=True)
class CommittedAdmissionEvidence:
    """The committed canonical admission this execution consumed, verbatim.

    Every field is read from the committed canonical projection or derived from
    the deterministic disposition identity. Nothing here is re-decided, and a
    field the projection omits stays ``None`` rather than being assumed.
    """

    disposition_id: str
    source: str
    admitted: bool
    reservation_id: str | None = None
    paper_trade_id: str | None = None
    quote_currency: str | None = None
    requested_reservation_amount: float | None = None
    decision_context_id: str | None = None
    disposition: str | None = None


@dataclass(frozen=True)
class ResearchPaperExecutionOutcome:
    """The seam's report: what was admitted, whether it executed, and why not.

    ``reason_code`` is the producer's own canonical status for an executed
    outcome; for a refusal it is one of this module's frozen blocking codes.
    """

    status: str
    reason_code: str
    disposition_id: str
    admission: CommittedAdmissionEvidence | None = None
    execution: PaperV2ExecutionResult | None = None
    admission_consumed: bool = False
    detail: str | None = None
    grants_execution_authority: bool = False

    def __post_init__(self) -> None:
        if self.grants_execution_authority:
            raise ValueError(
                "the research-paper execution seam never grants execution authority"
            )


def _optional_text(progress: Any, name: str) -> str | None:
    value = getattr(progress, name, None)
    if isinstance(value, str) and value:
        return value
    return None


def _optional_amount(progress: Any, name: str) -> float | None:
    value = getattr(progress, name, None)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def read_committed_admission(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
) -> tuple[Any, CommittedAdmissionEvidence]:
    """Read the committed admission evidence for this opportunity's identity.

    Read-only: the same single RPC the producer uses, addressed by the identity
    the producer derives, so the seam mints no identity of its own. The
    projection is returned to the caller rather than cached here.
    """
    disposition_id = build_disposition_id(
        episode_id=opportunity.episode_id,
        native_symbol=opportunity.native_symbol,
        direction=str(opportunity.direction),
    )
    progress = client.get_paper_v2_execution_state(disposition_id)
    return progress, CommittedAdmissionEvidence(
        disposition_id=disposition_id,
        source=ADMISSION_SOURCE,
        admitted=bool(getattr(progress, "admitted", False)),
        reservation_id=_optional_text(progress, "reservation_id"),
        paper_trade_id=_optional_text(progress, "paper_trade_id"),
        quote_currency=_optional_text(progress, "quote_currency"),
        requested_reservation_amount=_optional_amount(
            progress, "requested_reservation_amount"
        ),
        decision_context_id=_optional_text(progress, "decision_context_id"),
        disposition=_optional_text(progress, "disposition"),
    )



def execute_admitted_research_paper_opportunity(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    now: datetime,
    clock: Any = None,
) -> ResearchPaperExecutionOutcome:
    """Dispatch one F8 research-paper execution behind a committed admission.

    Refusals are decided before dispatch, so a blocked opportunity performs no
    market-data read, no canonical write and no reservation. ``now`` and
    ``clock`` are supplied by the caller exactly as the producer requires them;
    the seam owns no clock of its own and no policy of its own.
    """
    if not isinstance(opportunity, PaperV2Opportunity):
        raise ValueError("opportunity must be a PaperV2Opportunity")

    try:
        progress, evidence = read_committed_admission(opportunity, client=client)
    except (OSError, TimeoutError) as exc:
        # Transport/availability failure of the canonical read fails closed:
        # no synthetic success, no stale fallback, no execution, no canonical write.
        # Programming errors (ValueError/KeyError/TypeError) stay visible.
        disposition_id = build_disposition_id(
            episode_id=opportunity.episode_id,
            native_symbol=opportunity.native_symbol,
            direction=str(opportunity.direction),
        )
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_UNUSABLE_CANONICAL_STATE,
            disposition_id=disposition_id,
            detail=f"canonical read transport failed: {exc}",
        )

    if getattr(progress, "status", "") != "OK":
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_UNUSABLE_CANONICAL_STATE,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            detail=str(getattr(progress, "error_code", None) or progress.status),
        )

    if not evidence.admitted:
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_NO_COMMITTED_ADMISSION,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            detail=(
                "no committed canonical admission exists for this disposition "
                "identity, so the seam does not admit and does not execute"
            ),
        )

    if not evidence.reservation_id:
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_UNCOMMITTED_RESERVATION,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            detail="the admitted trade has no committed canonical reservation",
        )

    divergence = _intent_divergence(opportunity, evidence)
    if divergence is not None:
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_ADMISSION_INTENT_DIVERGENCE,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            detail=divergence,
        )

    if not evidence.decision_context_id:
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_BLOCKED,
            reason_code=REASON_UNUSABLE_CANONICAL_STATE,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            detail=(
                "the committed admission carries no authoritative decision "
                "context identity, so the execution cannot reuse it"
            ),
        )

    try:
        result = run_paper_v2_opportunity(
            opportunity,
            client=client,
            kraken_client=kraken_client,
            settings=settings,
            now=now,
            clock=clock,
            committed_decision_context_id=evidence.decision_context_id,
        )
    except PaperV2ExecutionError as exc:
        return ResearchPaperExecutionOutcome(
            status=OUTCOME_FAILED,
            reason_code=REASON_PRODUCER_FAILED,
            disposition_id=evidence.disposition_id,
            admission=evidence,
            admission_consumed=True,
            detail=str(exc),
        )

    return ResearchPaperExecutionOutcome(
        status=OUTCOME_EXECUTED,
        reason_code=str(result.status),
        disposition_id=evidence.disposition_id,
        admission=evidence,
        execution=result,
        admission_consumed=True,
    )


def _intent_divergence(
    opportunity: PaperV2Opportunity,
    evidence: CommittedAdmissionEvidence,
) -> str | None:
    """Refuse an execution intent the committed admission does not describe.

    Only values the admission actually committed are compared, and they are
    compared exactly: no tolerance, no default and no re-derivation. Direction is
    bound by construction, because the committed disposition is addressed by the
    identity that carries episode, instrument symbol and direction.
    """
    if (
        evidence.quote_currency is not None
        and str(evidence.quote_currency) != str(opportunity.quote_currency)
    ):
        return (
            "committed admission quote currency "
            f"{evidence.quote_currency!r} does not describe execution intent "
            f"{opportunity.quote_currency!r}"
        )
    if evidence.requested_reservation_amount is None:
        return None
    if float(evidence.requested_reservation_amount) != float(
        opportunity.requested_reservation_amount
    ):
        return (
            "committed reservation amount "
            f"{evidence.requested_reservation_amount!r} does not describe execution "
            f"intent {opportunity.requested_reservation_amount!r}"
        )
    return None
