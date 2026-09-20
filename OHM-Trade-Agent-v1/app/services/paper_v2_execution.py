"""B/C-3 canonical Paper v2 execution producer (increment 5).

Coordinates already-frozen canonical operations for ONE already-qualified paper
opportunity. It is deliberately not a decision engine: it does not qualify,
rank, gate, review or discover - all of that happened upstream. It coordinates
evidence and lets the canonical writer remain the authority on ancestry,
conservation and economics.

Lifecycle::

    qualified opportunity
    -> ensure canonical InstrumentVersion
    -> commit canonical decision snapshot (durable snapshot proof)
    -> ensure canonical DecisionContext (cites that proof)
    -> read PaperPortfolioState
    -> ADMIT_PAPER_OPPORTUNITY
    -> ENTRY order intent
    -> fresh public pre-trade book -> canonical quote evidence
    -> execution attempt
    -> quote-backed fill
    -> fill-derived exposure
    -> immutable protection plan

The decision snapshot is committed *before* the context on purpose. A context
must cite durable snapshot evidence, so the record it points at has to exist
first; a context that hoped a snapshot would appear later would be ancestry that
cannot be resolved.

Restart safety
--------------

Progress lives in canonical evidence, never in process memory or a sidecar store.
Before emitting each stage the producer consults the read-only progress seam and
reuses an already-committed stage rather than re-deriving it. That matters most
for admission (whose frozen request carries the portfolio version observed at the
time) and for the protection plan (whose identity is deterministic, so it must be
reused rather than rebuilt from possibly-changed configuration).

Fail-closed
-----------

Every failure stops this opportunity. Nothing falls back to Paper v1 or Freqtrade:
a fallback would recreate the dual paper authority the cutover exists to remove.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from app.opip.canonical.decision_context_bridge import (
    DecisionContextFacts,
    commit_decision_context,
    require_canonical_commit,
)
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_v2_identity import (
    paper_v2_entry_attempt_id,
    paper_v2_entry_fill_id,
    paper_v2_entry_order_intent_id,
    paper_v2_no_fill_reconciliation_id,
    paper_v2_quote_evidence_id,
)
from app.opip.contracts.paper_economics import paper_economics_for_version
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    paper_evidence_idempotency_key,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PaperAdmissionRequest,
    quote_evidence_idempotency_key,
)
from app.opip.contracts.serialization import iso_z, stable_hash
from app.opip.contracts.temporal import require_utc
from app.opip.contracts.process_identity import process_instance_id
from app.opip.decision.versioning import app_code_fingerprint
from app.services.paper_v2_activation import paper_v2_active
from app.services.paper_v2_decision_snapshot import (
    DecisionSnapshot,
    commit_decision_snapshot,
)
from app.services.paper_v2_instrument_registration import (
    InstrumentRegistrationError,
    ensure_instrument_version_registered,
)
from app.services.paper_v2_pretrade_adapter import (
    fetch_fresh_level1_observation,
    system_utc_clock,
)
from app.services.paper_v2_protection_plan import (
    ProtectionPlanSource,
    build_protection_plan_payload,
    protection_plan_idempotency_key,
)
from app.services.paper_v2_quote_evidence import (
    QuoteEvidenceUnavailableError,
    require_fresh_quote_evidence,
)

#: Stage identities now live in the shared contracts layer
#: (``app.opip.contracts.paper_v2_identity``) so the producer and the canonical
#: writer derive a given stage's identity from one definition.

#: The component name this producer stamps into its own evidence provenance. It
#: names the module that actually emits the record, so it is a fact rather than a
#: placeholder; the build and process identity are obtained by the producer itself.
PRODUCING_COMPONENT = "paper_v2_execution"

#: The only opportunity direction this slice can execute. Paper v2 models long-only
#: exposure, so an ENTRY is a BUY. A short needs a separately frozen contract, so
#: any other direction is refused rather than silently mapped onto a BUY.
OPPORTUNITY_DIRECTION_LONG = "LONG"
SUPPORTED_OPPORTUNITY_DIRECTIONS = frozenset({OPPORTUNITY_DIRECTION_LONG})

#: Terminal, non-continuing admission outcomes. Each stops this opportunity.
STOP_DISPOSITIONS = frozenset({"CAPACITY_REJECTED", "CAPITAL_REJECTED"})


class PaperV2ExecutionError(RuntimeError):
    """The opportunity could not be executed canonically. Never falls back."""


@dataclass(frozen=True)
class PaperV2Opportunity:
    """Everything the producer needs from the already-qualified opportunity.

    Plain source facts only. No qualification, ranking or gating input appears
    here because those authorities already ran.
    """

    # Canonical lineage
    #: The canonical qualification candidate identity. Increment 6B must supply
    #: this in the ``OPIPC:`` domain, derived from episode + pair + direction +
    #: market type. It must not substitute the separate signal/alert id: those are
    #: different facts, and a context that named an alert as its candidate would
    #: claim ancestry that does not describe the qualified opportunity.
    candidate_id: str
    episode_id: str
    cohort_id: str
    direction: str
    instrument_version_id: str
    #: The exact production-shaped canonical episode snapshot this decision is
    #: taken against. The producer validates it, derives its identity and content
    #: hash from it, and never accepts a free-standing id/hash pair as proof.
    snapshot_payload: Mapping[str, Any]
    evaluation_time: datetime
    evidence_cutoff: datetime
    source_record_refs: tuple[str, ...]
    #: Qualification-time policy identity, captured where the opportunity was
    #: qualified. Carried explicitly so the committed context describes the policy
    #: that actually qualified it, even if the live policy changes before this
    #: producer runs. The producer never reads the live policy.
    qualification_policy_version: str
    qualification_policy_fingerprint: str
    # Instrument (registration input)
    instrument_version: InstrumentVersion
    # Portfolio / admission
    quote_currency: str
    requested_capital: float
    requested_reservation_amount: float
    decision_time: datetime
    # Execution intent (decision-time reference; the fill uses live book)
    native_symbol: str
    requested_quantity: float
    requested_notional: float
    #: The qualified entry geometry, copied from the already-approved EntryExitPlan.
    #: Never recalculated: the fresh ask must still sit inside this band, or the
    #: opportunity that would be filled is not the one that was qualified.
    entry_low: float
    entry_high: float
    chase_limit: float
    stop_price: float
    target_prices: tuple[float, ...]


@dataclass(frozen=True)
class PaperV2ExecutionResult:
    """Terminal disposition of one opportunity. Always explicit, never silent."""

    status: str
    disposition_id: str
    paper_trade_id: str | None = None
    reservation_id: str | None = None
    entry_order_intent_id: str | None = None
    execution_attempt_id: str | None = None
    fill_id: str | None = None
    protection_plan_id: str | None = None
    filled_quantity: float = 0.0
    detail: str | None = None


def build_disposition_id(*, episode_id: str, native_symbol: str) -> str:
    """Deterministic disposition identity for one qualified opportunity.

    Derived from canonical ancestry with the repository's existing ``stable_hash``
    convention, so a retry after restart reproduces the same identity rather than
    minting a new one - which is what keeps admission, its reservation and every
    downstream event idempotent.
    """
    return stable_hash(
        "PDISP",
        {
            "episode_id": str(episode_id),
            "native_symbol": str(native_symbol).upper(),
            "engine": ENGINE_OPIP_PAPER_V2,
        },
    )


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PaperV2ExecutionError(message)


def submit_canonical_event(
    client: Any,
    *,
    event_type: str,
    key: str,
    payload: Mapping[str, Any],
    what: str,
):
    """Submit one paper event and require durable-commit proof."""
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import SCHEMA_VERSION

    intent = WriterIntent(
        schema_version=SCHEMA_VERSION,
        priority="LOW",
        idempotency_key=key,
        event_type=event_type,  # type: ignore[arg-type]
        payload=dict(payload),
    )
    ack = client.submit(intent)
    return require_canonical_commit(
        ack, idempotency_key=key, what=what
    )


def run_paper_v2_opportunity(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    now: datetime,
    clock: Callable[[], datetime] | None = None,
) -> PaperV2ExecutionResult:
    """Execute one qualified opportunity through the frozen canonical path.

    ``now`` is the *execution* opening reading and ``clock`` is the injectable
    execution clock used for subsequent readings (market-data freshness, attempt
    and fill occurrence). Neither is the qualification instant: qualification is a
    decision fact carried on the opportunity, while these are execution facts. The
    router supplies a real execution reading; tests supply a frozen one.
    """
    if not isinstance(opportunity, PaperV2Opportunity):
        raise ValueError("opportunity must be a PaperV2Opportunity")

    execution_clock = clock or system_utc_clock

    # --- 0. producer-level activation and direction gates ------------------
    # Enforced here, before any canonical write or market-data read, so a direct
    # call cannot execute while Paper v2 is inactive even if a future runtime
    # caller forgot to gate. This is defense in depth for the router that
    # Increment 6B will add; it deliberately does not replace that gate.
    if not paper_v2_active(settings):
        raise PaperV2ExecutionError(
            "Paper v2 is not active, so no paper execution may run"
        )
    # Long-only: an ENTRY is a BUY. A short requires a separately frozen contract,
    # so an unknown or unsupported direction is refused rather than reinterpreted.
    if opportunity.direction not in SUPPORTED_OPPORTUNITY_DIRECTIONS:
        raise PaperV2ExecutionError(
            f"unsupported opportunity direction: {opportunity.direction!r}"
        )

    moment = require_utc(now, field_name="now")

    disposition_id = build_disposition_id(
        episode_id=opportunity.episode_id, native_symbol=opportunity.native_symbol
    )

    # --- 0b. validate the snapshot before any canonical write --------------
    # Pure validation: the snapshot's identity, content hash and decision subject
    # are derived from the payload itself, so an opportunity cannot assert a
    # snapshot that does not prove its own contents or belongs to another episode.
    try:
        decision_snapshot = DecisionSnapshot.from_payload(
            opportunity.snapshot_payload,
            expected_episode_id=opportunity.episode_id,
            expected_cohort_id=opportunity.cohort_id,
        )
    except ValueError as exc:
        raise PaperV2ExecutionError(f"decision snapshot rejected: {exc}") from exc

    # --- 0c. bind the snapshot's decision boundary to the context cutoff ---
    # The canonical episode snapshot is the evidence available at the scan decision
    # boundary, and that boundary is the context's evidence_cutoff. They must name
    # the same instant: if the snapshot were captured at a different moment, the
    # context would cite evidence that was not what the decision consumed.
    # ``evaluation_time`` is separately allowed to be equal or later, because the
    # decision may be evaluated after the boundary is closed. This is a
    # point-in-time check, deliberately not a snapshot-age expiration: execution
    # market freshness remains the Level-1 quote-evidence responsibility.
    cutoff = require_utc(opportunity.evidence_cutoff, field_name="evidence_cutoff")
    if decision_snapshot.decision_at != cutoff:
        raise PaperV2ExecutionError(
            "decision snapshot boundary does not match the evidence cutoff"
        )

    # --- 1. canonical instrument version -----------------------------------
    try:
        registered = ensure_instrument_version_registered(
            opportunity.instrument_version, client=client
        )
    except (InstrumentRegistrationError, ValueError) as exc:
        raise PaperV2ExecutionError(f"instrument registration failed: {exc}") from exc

    # --- 1b. durable decision snapshot -------------------------------------
    # Committed before the context: the context cites this record as snapshot
    # provenance, so the record must exist first. Only proof of a durable commit
    # lets execution continue.
    try:
        snapshot_proof = commit_decision_snapshot(decision_snapshot, client=client)
    except ValueError as exc:
        raise PaperV2ExecutionError(f"decision snapshot failed: {exc}") from exc
    except RuntimeError as exc:
        raise PaperV2ExecutionError(f"decision snapshot failed: {exc}") from exc

    # --- 2. canonical decision context -------------------------------------
    # The schema-v2 context carries only facts this path can state truthfully. The
    # policy identity is the one captured when the opportunity was qualified, never
    # the live policy, so the context describes the policy that actually qualified
    # it even if thresholds changed since. The snapshot's id and content hash come
    # from the snapshot record committed above, not from the caller. The instrument
    # registration proves the instrument exists; its coordinate is deliberately NOT
    # reused as a consumed-input watermark.
    facts = DecisionContextFacts(
        candidate_id=opportunity.candidate_id,
        episode_id=opportunity.episode_id,
        instrument_version_id=opportunity.instrument_version_id,
        instrument_registration_event_id=registered.event_id,
        snapshot_record_event_id=snapshot_proof.event_id,
        snapshot_id=decision_snapshot.snapshot_id,
        snapshot_hash=decision_snapshot.snapshot_hash,
        evaluation_time=opportunity.evaluation_time,
        evidence_cutoff=opportunity.evidence_cutoff,
        policy_version=opportunity.qualification_policy_version,
        policy_fingerprint=opportunity.qualification_policy_fingerprint,
        producing_component=PRODUCING_COMPONENT,
        artifact_or_build_id=app_code_fingerprint(),
        process_instance_id=process_instance_id(),
        emitted_at=moment,
        source_record_refs=opportunity.source_record_refs,
    )
    try:
        context_id, _context_proof = commit_decision_context(facts, client=client)
    except ValueError as exc:
        raise PaperV2ExecutionError(f"decision context failed: {exc}") from exc
    except RuntimeError as exc:
        raise PaperV2ExecutionError(f"decision context failed: {exc}") from exc

    # --- 3. restart check before admitting ---------------------------------
    progress = client.get_paper_v2_execution_state(disposition_id)
    _require(
        progress.status == "OK",
        f"canonical progress unavailable: {progress.error_code or progress.status}",
    )
    paper_trade_id = progress.paper_trade_id
    assert paper_trade_id is not None

    if not progress.admitted:
        reserved = _admit(
            opportunity,
            client=client,
            disposition_id=disposition_id,
            context_id=context_id,
            moment=moment,
        )
        if reserved is not None:
            return reserved
        progress = client.get_paper_v2_execution_state(disposition_id)
        _require(progress.admitted, "admission did not commit")

    reservation_id = progress.reservation_id
    _require(bool(reservation_id), "admitted trade has no canonical reservation")

    # From here the trade owns a reservation, so every failure below must reach a
    # decided outcome. Which outcome is safe cannot be inferred from the exception
    # site: a submit may have committed before its ACK was lost, a timeout may have
    # committed, and a disconnect may have committed. Canonical state is therefore
    # re-read before anything is released.
    try:
        return _advance_admitted_trade(
            opportunity,
            client=client,
            kraken_client=kraken_client,
            settings=settings,
            execution_clock=execution_clock,
            progress=progress,
            context_id=context_id,
            disposition_id=disposition_id,
            paper_trade_id=paper_trade_id,
            reservation_id=reservation_id,
        )
    except Exception as exc:
        return recover_after_admitted_failure(
            exc,
            client=client,
            disposition_id=disposition_id,
            paper_trade_id=paper_trade_id,
            execution_clock=execution_clock,
        )


def recover_after_admitted_failure(
    error: BaseException,
    *,
    client: Any,
    disposition_id: str,
    paper_trade_id: str,
    execution_clock: Callable[[], datetime],
) -> PaperV2ExecutionResult:
    """Decide a safely committed outcome for a failed admitted trade.

    Reads canonical state rather than trusting the exception site, then classifies:

    * terminal record exists -> return it idempotently, never reopen;
    * exposure exists (a fill committed, possibly even though the ACK was lost) ->
      never release; report completion of the exposure stage;
    * a fill-capable attempt exists -> retain the reservation and require retry;
    * otherwise nothing can produce exposure -> terminalize zero-fill and release.

    The writer remains the final authority on release: it revalidates unsafe release
    independently, so a misclassification here cannot free capacity that could still
    be consumed.
    """
    reason = str(error) or type(error).__name__
    try:
        progress = client.get_paper_v2_execution_state(disposition_id)
    except Exception as read_error:  # noqa: BLE001 - unreadable state must not release
        raise PaperV2ExecutionError(
            f"{reason}; canonical recovery state unreadable "
            f"({type(read_error).__name__}) so nothing was released"
        ) from error

    if getattr(progress, "status", "") != "OK":
        raise PaperV2ExecutionError(
            f"{reason}; canonical recovery state unavailable "
            f"({getattr(progress, 'error_code', None) or progress.status}) so nothing "
            "was released"
        ) from error

    # CASE D: already terminal.
    if progress.terminal_reconciliation is not None:
        return PaperV2ExecutionResult(
            status="NO_FILL_TERMINAL",
            disposition_id=disposition_id,
            paper_trade_id=paper_trade_id,
            reservation_id=progress.reservation_id,
            entry_order_intent_id=progress.entry_order_intent_id,
            protection_plan_id=(
                str(progress.protection_plan["protection_plan_id"])
                if isinstance(progress.protection_plan, Mapping)
                else None
            ),
            detail=f"already terminally reconciled ({reason})",
        )

    # CASE C: exposure exists. Never release, never zero-fill terminalize.
    if float(progress.filled_quantity) > 0:
        raise PaperV2ExecutionError(
            f"{reason}; canonical fill already committed, so exposure exists and the "
            "trade must be reconciled rather than released"
        ) from error

    # CASE B: a fill-capable attempt exists. Retain the reservation and retry.
    if bool(getattr(progress, "entry_attempt_fill_capable", False)):
        raise PaperV2ExecutionError(
            f"{reason}; a fill-capable ENTRY attempt is outstanding, so the "
            "reservation is retained and the attempt must be resumed"
        ) from error

    # CASE A: nothing can produce exposure, so release via a truthful zero-fill
    # terminal record.
    _terminalize_zero_fill(
        client,
        progress=progress,
        reservation_id=progress.reservation_id,
        paper_trade_id=paper_trade_id,
        reason=reason,
        execution_clock=execution_clock,
    )
    raise PaperV2ExecutionError(reason) from error


def _advance_admitted_trade(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    execution_clock: Callable[[], datetime],
    progress: Any,
    context_id: str,
    disposition_id: str,
    paper_trade_id: str,
    reservation_id: str,
) -> PaperV2ExecutionResult:
    """Resume this trade from the first stage that is not yet committed.

    Every stage below is reused verbatim when it is already committed. That is what
    keeps a deterministic identity idempotent across a restart: the payload, not
    just the key, has to match, and it only does because a committed stage is never
    regenerated from mutable runtime state.
    """
    entry_order_id = paper_v2_entry_order_intent_id(paper_trade_id)
    attempt_id = paper_v2_entry_attempt_id(entry_order_id)
    fill_id = paper_v2_entry_fill_id(entry_order_id)

    # --- 3b. an already-terminal trade is returned, never reopened ---------
    # A zero-fill terminal record is a closed outcome. Advancing past it would try
    # to add execution evidence to a FINAL_VERIFIED trade, which the canonical
    # writer refuses, so the existing terminal result is returned instead. This is
    # what makes a completed-or-closed trade a pure idempotent resume.
    if progress.terminal_reconciliation is not None:
        return PaperV2ExecutionResult(
            status="NO_FILL_TERMINAL",
            disposition_id=disposition_id,
            paper_trade_id=paper_trade_id,
            reservation_id=reservation_id,
            entry_order_intent_id=progress.entry_order_intent_id,
            protection_plan_id=(
                str(progress.protection_plan["protection_plan_id"])
                if isinstance(progress.protection_plan, Mapping)
                else None
            ),
            detail="trade is already terminally reconciled with zero fills",
        )

    # --- 4. protection geometry, durably frozen BEFORE any exposure --------
    # A fill reaches canonical exposure, and a crash between that fill and plan
    # persistence would leave exposed quantity whose stop and targets existed only
    # in process memory. The frozen B/C-2 contract already allows a plan to be
    # recorded before it is activated, so the immutable plan is committed here -
    # ahead of the ENTRY intent that could become a fill.
    plan_id = _ensure_protection_plan(
        opportunity,
        client=client,
        progress=progress,
        settings=settings,
        moment=next_execution_moment(
            execution_clock,
            floor=require_utc(
                opportunity.evidence_cutoff, field_name="evidence_cutoff"
            ),
            field_name="plan_time",
            release_safe=True,
        ),
    )

    # --- 5. ENTRY order intent (reuse committed) ---------------------------
    if progress.entry_order_intent is not None:
        entry_payload = dict(progress.entry_order_intent)
    else:
        intent_moment = next_execution_moment(
            execution_clock,
            floor=require_utc(
                opportunity.evidence_cutoff, field_name="evidence_cutoff"
            ),
            field_name="intent_time",
            release_safe=True,
        )
        entry_payload = {
            "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
            "engine": ENGINE_OPIP_PAPER_V2,
            "order_intent_id": entry_order_id,
            "paper_trade_id": paper_trade_id,
            "decision_context_id": context_id,
            "intent_seq": 0,
            "intent_role": "ENTRY",
            "side": "BUY",
            "order_type": "MARKET",
            "requested_quantity": float(opportunity.requested_quantity),
            "requested_notional": float(opportunity.requested_notional),
            "reason_code": "PAPER_V2_ENTRY",
            "intent_time": {
                "precision": "EXACT",
                "basis": "SOURCE_REPORTED",
                "occurred_at": iso_z(intent_moment, field_name="intent_time"),
            },
            "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "reservation_id": reservation_id,
        }
        submit_canonical_event(
            client,
            event_type=PAPER_ORDER_INTENT_RECORDED,
            key=paper_evidence_idempotency_key(
                PAPER_ORDER_INTENT_RECORDED, entry_payload
            ),
            payload=entry_payload,
            what="ENTRY order intent",
        )

    # --- 6/7. execution quote and attempt ---------------------------------
    # An already-committed attempt is never rebound to a different quote: its
    # committed evidence reference is immutable, so a restart reuses the exact quote
    # it consumed instead of reading the market again under the same identity.
    if progress.execution_attempt is not None:
        attempt_payload = dict(progress.execution_attempt)
        committed_quote = progress.quote_evidence
        if not isinstance(committed_quote, Mapping):
            raise PaperV2ExecutionError(
                "committed attempt cites quote evidence that cannot be resolved"
            )
        quote = dict(committed_quote)
        attempt_floor = temporal_instant(
            attempt_payload["attempt_time"], field_name="attempt_time"
        )
    else:
        # No attempt yet, so the quote must satisfy the execution contract now. These
        # pre-attempt failures provably occur before any exposure exists and before
        # any fill-capable attempt, so they may be terminalized as a zero-fill trade.
        try:
            quote, received_at = commit_execution_quote(
                opportunity,
                client=client,
                kraken_client=kraken_client,
                settings=settings,
                clock=execution_clock,
            )
            # The committed ask must still be an executable qualified entry: geometry,
            # reserved capital and displayed depth. Nothing is resized, extrapolated
            # or filled partially, so a quote that cannot support the approved trade
            # produces no exposure at all.
            _require_executable_entry(opportunity, quote=quote)
        except PaperV2ExecutionError as exc:
            raise PreAttemptTerminalStop(str(exc)) from exc
        attempt_floor = received_at
        attempt_moment = next_execution_moment(
            execution_clock,
            floor=attempt_floor,
            field_name="attempt_time",
            release_safe=True,
        )
        attempt_payload = {
            "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
            "engine": ENGINE_OPIP_PAPER_V2,
            "execution_attempt_id": attempt_id,
            "order_intent_id": entry_order_id,
            "paper_trade_id": paper_trade_id,
            "attempt_seq": 0,
            "execution_state": "ACCEPTED",
            "attempt_time": {
                "precision": "EXACT",
                "basis": "SOURCE_REPORTED",
                "occurred_at": iso_z(attempt_moment, field_name="attempt_time"),
            },
            "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "accepted_quantity": float(opportunity.requested_quantity),
            "market_evidence_ref": quote["quote_evidence_id"],
        }
        submit_canonical_event(
            client,
            event_type=PAPER_EXECUTION_ATTEMPT_RECORDED,
            key=paper_evidence_idempotency_key(
                PAPER_EXECUTION_ATTEMPT_RECORDED, attempt_payload
            ),
            payload=attempt_payload,
            what="execution attempt",
        )

    # --- 8. quote-backed fill (reuse committed) ----------------------------
    if progress.fill is not None:
        fill_payload = dict(progress.fill)
        fill_quantity = float(fill_payload["quantity"])
    else:
        # Rebuilt only from committed facts: the committed accepted quantity, the
        # committed quote, and the frozen economics for this execution's model
        # version. No fresh Kraken read, and no mutable setting can change what this
        # attempt's fill costs.
        fill_quantity = float(attempt_payload["accepted_quantity"])
        if (
            abs(fill_quantity - float(opportunity.requested_quantity))
            > QUANTITY_TOLERANCE
        ):
            raise PaperV2ExecutionError(
                "committed attempt quantity does not match the approved request"
            )
        # A long ENTRY executes against the ask side of the committed book.
        executable_price = float(quote["best_ask"])
        economics = paper_economics_for_version(PAPER_ECONOMIC_MODEL_VERSION)
        cost = economics.cost_components(fill_quantity, executable_price)
        fill_moment = next_execution_moment(
            execution_clock,
            floor=attempt_floor,
            field_name="fill_time",
            # An accepted attempt already exists, so this trade can still produce
            # exposure: a regression here must be retried, never released.
            release_safe=False,
        )
        fill_payload = {
            "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
            "engine": ENGINE_OPIP_PAPER_V2,
            "fill_id": fill_id,
            "execution_attempt_id": attempt_id,
            "order_intent_id": entry_order_id,
            "paper_trade_id": paper_trade_id,
            "fill_seq": 0,
            "side": "BUY",
            "quantity": fill_quantity,
            "price": executable_price,
            "fee_cost": cost["fee_cost"],
            "spread_cost": cost["spread_cost"],
            "slippage_cost": cost["slippage_cost"],
            "other_supported_cost": cost["other_supported_cost"],
            "fill_time": {
                "precision": "EXACT",
                "basis": "SOURCE_REPORTED",
                "occurred_at": iso_z(fill_moment, field_name="fill_time"),
            },
            "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
            "market_evidence_ref": quote["quote_evidence_id"],
        }
        submit_canonical_event(
            client,
            event_type=PAPER_FILL_RECORDED,
            key=paper_evidence_idempotency_key(PAPER_FILL_RECORDED, fill_payload),
            payload=fill_payload,
            what="fill",
        )

    # --- 9. exposure from canonical fills only ----------------------------
    progress = client.get_paper_v2_execution_state(disposition_id)
    _require(
        float(progress.filled_quantity) > 0,
        "no canonical exposure after the fill; refusing to protect a hypothetical position",
    )

    return PaperV2ExecutionResult(
        status="EXECUTED",
        disposition_id=disposition_id,
        paper_trade_id=paper_trade_id,
        reservation_id=reservation_id,
        entry_order_intent_id=entry_order_id,
        execution_attempt_id=attempt_id,
        fill_id=fill_id,
        protection_plan_id=plan_id,
        filled_quantity=fill_quantity,
    )


#: Tolerance for the long-spot leverage-consistency check. The action gate rounds
#: the notional to cents, so a matched position may differ by under a cent.
_NOTIONAL_TOLERANCE_USD = 0.01

#: Quantity tolerance for the displayed Level-1 depth check.
QUANTITY_TOLERANCE = 1e-9


def _require_executable_entry(opportunity: PaperV2Opportunity, *, quote: Mapping[str, Any]) -> None:
    """Validate the fresh committed ask before it may become exposure.

    Three independent refusals, all fail-closed:

    * **Qualified geometry.** The ask must still sit inside the entry band the plan
      qualified, above the qualified stop and below the first qualified target. A
      quote outside that geometry is a different trade from the one that was
      qualified, so it must not fill.
    * **Reserved capital.** The actual notional at the committed ask must not exceed
      the approved reservation. Silently resizing the trade would change approved
      economics, so this refuses rather than rescaling.
    * **Displayed depth.** There is no approved depth model and no approved
      partial-fill model, so a top-of-book ask that cannot support the full
      requested quantity cannot be filled. Extrapolating deeper liquidity or
      fabricating a full fill is not permitted.
    """
    ask = float(quote["best_ask"])
    quantity = float(opportunity.requested_quantity)

    if ask > float(opportunity.chase_limit):
        raise PaperV2ExecutionError(
            "best ask is above the qualified chase limit for this opportunity"
        )
    if ask < float(opportunity.entry_low):
        raise PaperV2ExecutionError(
            "best ask is below the qualified entry band for this opportunity"
        )
    if ask <= float(opportunity.stop_price):
        raise PaperV2ExecutionError(
            "best ask is at or below the qualified stop for this opportunity"
        )
    if opportunity.target_prices:
        first_target = float(opportunity.target_prices[0])
        if ask >= first_target:
            raise PaperV2ExecutionError(
                "best ask is at or above the first qualified target for this opportunity"
            )

    actual_notional = quantity * ask
    reserved = float(opportunity.requested_reservation_amount)
    if actual_notional > reserved + _NOTIONAL_TOLERANCE_USD:
        raise PaperV2ExecutionError(
            "actual execution notional at the committed ask exceeds the approved "
            "reservation; refusing rather than resizing the trade"
        )

    ask_quantity = float(quote["ask_quantity"])
    if quantity > ask_quantity + QUANTITY_TOLERANCE:
        raise PaperV2ExecutionError(
            "requested quantity exceeds the displayed Level-1 ask quantity; "
            "no approved depth or partial-fill model exists"
        )


class PreAttemptTerminalStop(RuntimeError):
    """A fail-closed stop that provably occurred BEFORE any fill-capable attempt.

    Only this shape may be terminalized as a zero-fill trade, because only here does
    canonical evidence prove the trade can no longer produce exposure. Raising this
    after an accepted attempt existed would release capacity that could still be
    consumed, so the two situations are separate types rather than one.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ExecutionRetryRequired(RuntimeError):
    """The trade cannot safely continue now, but must NOT be released.

    Raised once a fill-capable attempt exists, or once exposure already does: the
    reservation is retained, the retryable state is surfaced, and a later recovery
    continues from committed canonical evidence.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def next_execution_moment(
    clock: Callable[[], datetime],
    *,
    floor: datetime,
    field_name: str,
    release_safe: bool,
) -> datetime:
    """Take an execution reading, refusing a clock that regressed.

    Canonical chronology must never go backwards: ``fill_time < attempt_time`` would
    record an execution that finished before it began. Silently rewriting the
    reading to ``floor`` would fabricate chronology, so a regression fails closed and
    the trade resumes later when the clock is valid again.

    ``release_safe`` selects the failure shape, because the safe next action differs:
    a regression before any fill-capable attempt leaves a trade that provably cannot
    produce exposure (releasable), while one afterwards leaves a trade that still can
    (retained and retried). Using one exception for both let a post-attempt
    regression reach the zero-fill release path.
    """
    moment = require_utc(clock(), field_name=field_name)
    if moment < floor:
        message = f"{field_name} regressed behind the causal floor for this execution"
        if release_safe:
            raise PreAttemptTerminalStop(message)
        raise ExecutionRetryRequired(message)
    return moment


def recover_outstanding_paper_v2_trades(
    client: Any,
    *,
    execution_clock: Callable[[], datetime] | None = None,
) -> dict:
    """Continue committed Paper-v2 trades that still need lifecycle work.

    Recovery of already-authorized work, not new trading. It is driven entirely by
    canonical evidence, so an interrupted accepted attempt is completed even if the
    originating opportunity never qualifies again - which the normal scan path could
    not guarantee.

    Invariants: it cannot admit, cannot requalify or rerank, cannot change the
    decision context, quantity, plan geometry, economics or instrument, cannot fetch
    a new quote under an existing committed attempt, cannot call legacy execution,
    and is idempotent. A trade that cannot safely proceed keeps its reservation and
    is reported as retryable rather than being released or retried unsafely.
    """
    clock = execution_clock or system_utc_clock
    summary: dict[str, Any] = {
        "status": "OK",
        "considered": 0,
        "completed": 0,
        "retryable": 0,
        "details": [],
    }
    try:
        projection = client.get_paper_v2_recoverable_executions()
    except Exception as exc:  # noqa: BLE001 - unreadable work list must be reported
        summary["status"] = "UNAVAILABLE"
        summary["details"].append(f"recoverable projection failed: {type(exc).__name__}")
        return summary
    if str(getattr(projection, "status", "") or "") != "OK":
        summary["status"] = "UNAVAILABLE"
        summary["details"].append(
            "recoverable projection unavailable: "
            f"{getattr(projection, 'error_code', None) or projection.status}"
        )
        return summary

    for entry in projection.entries:
        summary["considered"] += 1
        paper_trade_id = str(entry.get("paper_trade_id") or "")
        try:
            _complete_committed_fill(
                client, entry=entry, execution_clock=clock
            )
            summary["completed"] += 1
            summary["details"].append(f"completed fill for {paper_trade_id}")
        except Exception as exc:  # noqa: BLE001 - one trade must not stop the sweep
            summary["retryable"] += 1
            summary["details"].append(
                f"retryable {paper_trade_id}: {type(exc).__name__}: {exc}"
            )
    return summary


def _complete_committed_fill(
    client: Any,
    *,
    entry: Mapping[str, Any],
    execution_clock: Callable[[], datetime],
) -> None:
    """Finish the fill for an already-committed, fill-capable ENTRY attempt.

    Built only from committed facts - the committed accepted quantity, the committed
    quote the attempt cites, and the frozen economics for this execution's model
    version. It reads no market data and consults no mutable setting, so the fill it
    produces is the one the original execution would have produced.
    """
    attempt = entry["execution_attempt"]
    quote = entry["quote_evidence"]
    if not isinstance(attempt, Mapping) or not isinstance(quote, Mapping):
        raise PaperV2ExecutionError("recovery entry is missing committed payloads")

    entry_order_id = str(attempt["order_intent_id"])
    fill_quantity = float(attempt["accepted_quantity"])
    executable_price = float(quote["best_ask"])
    economics = paper_economics_for_version(PAPER_ECONOMIC_MODEL_VERSION)
    cost = economics.cost_components(fill_quantity, executable_price)
    attempt_floor = temporal_instant(
        attempt["attempt_time"], field_name="attempt_time"
    )
    fill_moment = next_execution_moment(
        execution_clock,
        floor=attempt_floor,
        field_name="fill_time",
        release_safe=False,
    )
    fill_payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "fill_id": paper_v2_entry_fill_id(entry_order_id),
        "execution_attempt_id": str(attempt["execution_attempt_id"]),
        "order_intent_id": entry_order_id,
        "paper_trade_id": str(attempt["paper_trade_id"]),
        "fill_seq": 0,
        "side": "BUY",
        "quantity": fill_quantity,
        "price": executable_price,
        "fee_cost": cost["fee_cost"],
        "spread_cost": cost["spread_cost"],
        "slippage_cost": cost["slippage_cost"],
        "other_supported_cost": cost["other_supported_cost"],
        "fill_time": {
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(fill_moment, field_name="fill_time"),
        },
        "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
        "market_evidence_ref": str(attempt["market_evidence_ref"]),
    }
    submit_canonical_event(
        client,
        event_type=PAPER_FILL_RECORDED,
        key=paper_evidence_idempotency_key(PAPER_FILL_RECORDED, fill_payload),
        payload=fill_payload,
        what="recovered fill",
    )


def temporal_instant(value: Any, *, field_name: str) -> datetime:
    """The instant from a committed canonical temporal-evidence object."""
    if not isinstance(value, Mapping):
        raise PaperV2ExecutionError(f"{field_name} is not canonical temporal evidence")
    occurred = value.get("occurred_at")
    if not isinstance(occurred, str) or not occurred:
        raise PaperV2ExecutionError(f"{field_name} has no exact occurrence instant")
    try:
        parsed = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PaperV2ExecutionError(
            f"{field_name} is not a parseable instant"
        ) from exc
    return require_utc(parsed, field_name=field_name)


def _terminalize_zero_fill(
    client: Any,
    *,
    progress: Any,
    reservation_id: str,
    paper_trade_id: str,
    reason: str,
    execution_clock: Callable[[], datetime],
) -> None:
    """Write the truthful terminal record for a trade that provably never filled.

    Not a success, and not a strategy rejection: the trade reached no exposure, so it
    is terminalized as a zero-fill FINAL_VERIFIED trade whose reservation the
    canonical portfolio projection can then release. The writer independently
    refuses this when canonical evidence cannot prove release is safe, so capacity is
    never freed against a trade that could still fill.

    An already-committed terminal record is left alone, so a restart does not reopen
    a closed trade.
    """
    if progress.terminal_reconciliation is not None:
        return
    if float(progress.filled_quantity) > 0:
        # Exposure exists: this is not a zero-fill trade and must be reconciled on
        # its own evidence rather than released here.
        return
    if bool(getattr(progress, "entry_attempt_fill_capable", False)):
        # A fill-capable attempt is outstanding and may still become exposure, so
        # releasing now would be unsafe. It is resumed instead.
        return

    moment = require_utc(execution_clock(), field_name="reconciled_time")
    payload = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "reconciliation_id": paper_v2_no_fill_reconciliation_id(paper_trade_id),
        "paper_trade_id": paper_trade_id,
        "reconciliation_seq": 0,
        # The trade never held a position, so its truthful terminal state is that
        # there is no position rather than one that was closed.
        "position_state": "NO_POSITION",
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "filled_entry_quantity": 0.0,
        "filled_exit_quantity": 0.0,
        "remaining_quantity": 0.0,
        "reserved_capital": float(progress.requested_reservation_amount or 0.0),
        "realized_gross_pnl": 0.0,
        "recorded_execution_costs": 0.0,
        "realized_net_pnl": 0.0,
        "reconciled_time": {
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(moment, field_name="reconciled_time"),
        },
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
    }
    try:
        submit_canonical_event(
            client,
            event_type=PAPER_RECONCILIATION_RECORDED,
            key=paper_evidence_idempotency_key(
                PAPER_RECONCILIATION_RECORDED, payload
            ),
            payload=payload,
            what=f"zero-fill terminal reconciliation ({reason})",
        )
    except (ValueError, RuntimeError):
        # A refused release is not a reason to hide the original failure: the
        # reservation stays active, which is the fail-closed outcome.
        return


def _admit(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    disposition_id: str,
    context_id: str,
    moment: datetime,
) -> PaperV2ExecutionResult | None:
    """Read the authoritative version, then admit. Returns a result to stop on."""
    portfolio = client.get_paper_portfolio_state(opportunity.quote_currency)
    if portfolio.status != "OK" or portfolio.portfolio_version is None:
        raise PaperV2ExecutionError(
            "canonical portfolio state unavailable: "
            f"{portfolio.error_code or portfolio.status}"
        )

    from app.opip.contracts.paper_execution_runtime import resolve_capital_policy

    policy = resolve_capital_policy("paper-capital-v1")
    request = PaperAdmissionRequest(
        disposition_id=disposition_id,
        decision_context_id=context_id,
        disposition_seq=0,
        quote_currency=opportunity.quote_currency,
        requested_capital=float(opportunity.requested_capital),
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(opportunity.decision_time, field_name="decision_time"),
        },
        # The exact authoritative version, read immediately before constructing
        # the frozen request. Never derived from local counts.
        expected_portfolio_version=int(portfolio.portfolio_version),
        capital_policy_version=policy.policy_version,
        portfolio_equity_limit=policy.portfolio_equity_limit,
        portfolio_position_limit=policy.portfolio_position_limit,
        requested_reservation_amount=float(opportunity.requested_reservation_amount),
    )
    ack = client.admit_paper_opportunity(request)
    status = str(getattr(ack, "status", "") or "")
    disposition = str(getattr(ack, "disposition", "") or "")

    if status == "OK" and disposition == "ADMITTED":
        return None
    if status == "DUPLICATE_OK" and disposition == "ADMITTED":
        return None
    if disposition in STOP_DISPOSITIONS:
        return PaperV2ExecutionResult(
            status=disposition,
            disposition_id=disposition_id,
            detail=f"admission stopped the opportunity: {disposition}",
        )
    if getattr(ack, "error_code", None) == "STALE_PORTFOLIO_VERSION" or (
        disposition == "STALE_PORTFOLIO_VERSION"
    ):
        # Fail closed. No retry under a mutated request, no new disposition id,
        # no legacy fallback: the stale request remains evidence of the lost race.
        raise PaperV2ExecutionError(
            "stale portfolio version; failing closed for this disposition"
        )
    raise PaperV2ExecutionError(
        "admission failed: "
        f"status={status or 'UNKNOWN'} disposition={disposition or 'NONE'} "
        f"error={getattr(ack, 'error_code', None)}"
    )


def commit_execution_quote(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    kraken_client: Any,
    settings: Any,
    clock: Callable[[], datetime],
) -> tuple[dict, datetime]:
    """Fetch a fresh public pre-trade book and commit canonical quote evidence.

    Returns the committed payload and the receipt instant the book was validated
    against, so the caller reuses one execution reading instead of consulting a
    clock that may have moved.
    """
    max_age = int(getattr(settings, "paper_v2_quote_max_age_seconds", 15))
    try:
        observation, received_at = fetch_fresh_level1_observation(
            kraken_client,
            symbol=opportunity.native_symbol,
            instrument_version_id=opportunity.instrument_version_id,
            native_symbol=opportunity.native_symbol,
            quote_currency=opportunity.quote_currency,
            max_age_seconds=max_age,
            clock=clock,
        )
        payload = require_fresh_quote_evidence(
            observation,
            quote_evidence_id=paper_v2_quote_evidence_id(
                instrument_version_id=opportunity.instrument_version_id,
                native_symbol=opportunity.native_symbol,
                observed_at=observation.observed_at,
            ),
            now=received_at,
            max_age_seconds=max_age,
        )
    except QuoteEvidenceUnavailableError as exc:
        raise PaperV2ExecutionError(f"execution quote unavailable: {exc}") from exc

    submit_canonical_event(
        client,
        event_type=PAPER_QUOTE_EVIDENCE_RECORDED,
        key=quote_evidence_idempotency_key(payload),
        payload=payload,
        what="quote evidence",
    )
    return payload, received_at


def _ensure_protection_plan(
    opportunity: PaperV2Opportunity,
    *,
    client: Any,
    progress: Any,
    settings: Any,
    moment: datetime,
) -> str | None:
    """Reuse a committed plan; otherwise build the deterministic one.

    Reuse is mandatory, not an optimisation: the plan identity is deterministic
    from trade and sequence, so rebuilding it after a settings change would
    silently rewrite committed economics under the same identity.
    """
    plan = progress.protection_plan
    if isinstance(plan, Mapping):
        # Reuse is mandatory, not an optimization: the plan identity is
        # deterministic from trade and sequence, so rebuilding it after a settings
        # change would silently rewrite committed economics under the same identity.
        existing = dict(plan)
        return str(existing.get("protection_plan_id") or "") or None

    payload = build_protection_plan_payload(
        ProtectionPlanSource(
            paper_trade_id=str(progress.paper_trade_id),
            plan_seq=0,
            stop_price=float(opportunity.stop_price),
            target_prices=tuple(float(p) for p in opportunity.target_prices),
            plan_time=moment,
        ),
        tp1_fraction=float(getattr(settings, "paper_v2_tp1_fraction", 0.5)),
        max_hold_seconds=int(getattr(settings, "paper_v2_max_hold_seconds", 86_400)),
    )
    submit_canonical_event(
        client,
        event_type="paper_protection.plan.recorded",
        key=protection_plan_idempotency_key(payload),
        payload=payload,
        what="protection plan",
    )
    return str(payload["protection_plan_id"])


__all__ = [
    "ExecutionRetryRequired",
    "PaperV2ExecutionError",
    "PaperV2ExecutionResult",
    "PaperV2Opportunity",
    "PreAttemptTerminalStop",
    "QUANTITY_TOLERANCE",
    "build_disposition_id",
    "commit_execution_quote",
    "next_execution_moment",
    "recover_after_admitted_failure",
    "recover_outstanding_paper_v2_trades",
    "run_paper_v2_opportunity",
    "submit_canonical_event",
    "temporal_instant",
]
