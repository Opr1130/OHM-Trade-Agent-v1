"""ATDD-R5-E portfolio lifecycle closure: the connected research-paper chain.

Increment: `ATDD-R5-E-portfolio-lifecycle-closure`. Every test cites its own
criterion in its docstring, and the contract
`docs/atdd/scope-contracts/ATDD-R5-E-portfolio-lifecycle-closure.md` is the
authority for what each one means.

Two things are proven here, and nothing else is added.

* `AC-001` closes the fail-closed gap found when reviewing the delivered seam:
  the seam compared the committed admission's quote currency and requested
  reservation amount with the execution intent only when the committed
  projection declared them, so an undeclared committed value was dispatched as
  if it described any intent. An undeclared value is now refused before dispatch
  under the existing closed reason `ADMISSION_INTENT_DIVERGENCE`.
* `AC-002` to `AC-006` connect the components that already exist - the R5-C/R5-D
  capital authority, the canonical writer, the F8 producer and the F11
  protection runtime - and prove the lifecycle behaviour where it is real,
  while recording rotation from committed proceeds as unimplemented.

`AC-007` keeps the increment honest: same posture, no authority, no runtime
caller, and no second engine or ledger.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from datetime import timedelta
from typing import Any

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.exchanges.kraken import KrakenClient, KrakenTransportError  # noqa: E402
from app.opip.canonical.client import InProcessWriterClient  # noqa: E402
from app.opip.canonical.decision_context_bridge import (  # noqa: E402
    DecisionContextFacts,
    commit_decision_context,
)
from app.opip.canonical.models import (  # noqa: E402
    BOUND_RESERVATION_CONSTRAINTS,
    PaperV2ExecutionState,
)
from app.opip.canonical.server import CanonicalWriterServer  # noqa: E402
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (  # noqa: E402
    PAPER_ADMISSION_REQUEST_RECORDED,
    PAPER_CAPITAL_POLICY_VERSION,
    PAPER_QUOTE_EVIDENCE_RECORDED,
)
from app.opip.contracts.process_identity import process_instance_id  # noqa: E402
from app.opip.decision.versioning import app_code_fingerprint  # noqa: E402
from app.services import paper_v2_capital_admission_execution as seam  # noqa: E402
from app.services.capital_admission_integration import (  # noqa: E402
    CAPITAL_ADMISSION_ADMITTED,
    admit_planned_reservation,
)
from app.services.paper_v2_capital_admission_execution import (  # noqa: E402
    OUTCOME_BLOCKED,
    OUTCOME_EXECUTED,
    OUTCOME_FAILED,
    REASON_ADMISSION_INTENT_DIVERGENCE,
    REASON_PRODUCER_FAILED,
    execute_admitted_research_paper_opportunity,
)
from app.services.paper_v2_execution import (  # noqa: E402
    PRODUCING_COMPONENT,
    build_disposition_id,
)
from app.services.paper_v2_decision_snapshot import (  # noqa: E402
    DecisionSnapshot,
    commit_decision_snapshot,
)
from app.services.paper_v2_instrument_registration import (  # noqa: E402
    ensure_instrument_version_registered,
)
from app.services.paper_v2_protection_runtime import run_protection_sweep  # noqa: E402
from tests.test_opip_paper_v2_execution_bc3 import (  # noqa: E402
    NOW,
    _Settings,
    _kraken,
    _opportunity,
    _rows,
    _run,
)
from tests.test_opip_paper_v2_protection_runtime_bc3 import (  # noqa: E402
    QUALIFICATION_TIME,
    _BookTransport,
    _Clock,
    _Env as _PrtEnv,
    _Settings as _ProtectionSettings,
    _item as _protection_item,
    _next_scan as _next_scan,
    _opportunity as _prt_opportunity,
    _rows as _protection_rows,
    _sweep as _protection_sweep,
    _transport as _protection_transport,
)
from tests.test_opip_r5_e_capital_admission_integration import (  # noqa: E402
    CANDIDATE,
    _book as _capital_book,
    _store_fingerprint as _store_fingerprint,
    capital_snapshot as _capital_snapshot,
    reservation_plan as _reservation_plan,
)
from tests.test_opip_r5_e_research_paper_execution import (  # noqa: E402
    _RefusingTransport,
    _StubClient,
    _counts,
    _portfolio,
    _projection,
    _seed_committed_admission,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-E-portfolio-lifecycle-closure"
SEAM_PATH = pathlib.Path(seam.__file__).resolve()


class _SkewedWriterClient:
    """A writer client whose projection omits one field, as an older revision does.

    The canonical store is untouched: this reproduces exactly what
    ``WriterClient.get_paper_v2_execution_state`` returns when the writer on the
    other end of the socket does not emit a field, because the transport
    deserializes the response with ``PaperV2ExecutionState.from_dict``, whose
    fields are optional. It is the seam's real input shape, not a fabricated
    canonical record.
    """

    def __init__(self, client: Any, *, omit: str) -> None:
        self.client = client
        self.omit = omit
        self.reads: list[str] = []

    def get_paper_v2_execution_state(
        self, disposition_id: str
    ) -> PaperV2ExecutionState:
        self.reads.append(disposition_id)
        state = self.client.get_paper_v2_execution_state(disposition_id)
        raw = state.to_dict()
        raw.pop(self.omit, None)
        return PaperV2ExecutionState.from_dict(raw)

    def __getattr__(self, name: str) -> Any:  # pragma: no cover - delegation only
        return getattr(self.client, name)


@pytest.fixture(autouse=True)
def _frozen_producer_clock(monkeypatch):
    """Freeze the producer's execution clock to this module's NOW (BC3 harness)."""

    monkeypatch.setattr(
        "app.services.paper_v2_execution.system_utc_clock", lambda: NOW
    )


@pytest.fixture
def env(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


def _dispatch(
    env, *, kraken=None, opportunity=None, client=None, now=NOW, settings=None
):
    _server, live_client = env
    return execute_admitted_research_paper_opportunity(
        opportunity or _opportunity(),
        client=client if client is not None else live_client,
        kraken_client=kraken or _kraken(),
        settings=settings or _Settings(),
        now=now,
    )


# ---------------------------------------------------------------------------
# AC-002: the planned reservation the capital authority committed is executed
# by the seam, once, as one entry
# ---------------------------------------------------------------------------

LIFECYCLE_SYMBOL = "SOLUSD"


@pytest.fixture
def lifecycle(tmp_path):
    """One real canonical store with a restart seam, shared by the lifecycle tests."""

    holder = _PrtEnv(tmp_path=tmp_path)
    try:
        yield holder
    finally:
        holder.server.stop()


def _commit_execution_ancestry(holder, opportunity, *, moment) -> str:
    """Commit the canonical ancestry the execution path cites, and return its context.

    The canonical writer refuses an ENTRY order intent whose decision context does
    not match the committed reservation's, so the admission and the execution must
    cite one and the same context. This helper commits exactly what the execution
    path commits - registered instrument version, durable decision snapshot, and
    the schema-v2 context built from the same facts and the same producing
    component - through the same public helpers, so the identity returned here is
    the execution path's own and the admission can be told about it.
    """

    registered = ensure_instrument_version_registered(
        opportunity.instrument_version, client=holder.client
    )
    snapshot = DecisionSnapshot.from_payload(
        opportunity.snapshot_payload,
        expected_episode_id=opportunity.episode_id,
        expected_cohort_id=opportunity.cohort_id,
    )
    snapshot_proof = commit_decision_snapshot(snapshot, client=holder.client)
    facts = DecisionContextFacts(
        candidate_id=opportunity.candidate_id,
        episode_id=opportunity.episode_id,
        instrument_version_id=opportunity.instrument_version_id,
        instrument_registration_event_id=registered.event_id,
        snapshot_record_event_id=snapshot_proof.event_id,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
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
    context_id, _proof = commit_decision_context(facts, client=holder.client)
    return context_id


def _commit_planned_admission(
    holder,
    opportunity,
    *,
    moment: datetime,
    context_id: str | None = None,
    expected_version: int | None = 0,
):
    """Commit exactly one canonical admission through the R5-C/R5-D authority.

    This is the upstream half of the chain: the planned reservation of the R5-D
    plan, bound to the R5-C capital snapshot, admitted by the canonical writer
    under the disposition identity the execution producer derives. Nothing is
    executed by it.
    """

    context = context_id or _commit_execution_ancestry(
        holder, opportunity, moment=moment
    )
    return (
        context,
        admit_planned_reservation(
            _reservation_plan(),
            client=holder.client,
            snapshot=_capital_snapshot(),
            disposition_id=build_disposition_id(
                episode_id=opportunity.episode_id,
                native_symbol=opportunity.native_symbol,
                direction=str(opportunity.direction),
            ),
            decision_context_id=context,
            disposition_time=moment,
            candidate_id=CANDIDATE,
            binding_constraints=BOUND_RESERVATION_CONSTRAINTS,
            capital_policy_version=PAPER_CAPITAL_POLICY_VERSION,
            expected_portfolio_version=expected_version,
        ),
    )


def _dispatch_once(
    holder,
    opportunity,
    *,
    clock,
    now=QUALIFICATION_TIME,
    bid=99.9,
    ask=100.0,
    bid_qty=10.0,
    kraken_client=None,
):
    """Dispatch the seam against a real public book."""

    transport = _BookTransport(
        clock=clock, bid=bid, ask=ask, bid_qty=bid_qty, ask_qty=bid_qty
    )
    return (
        execute_admitted_research_paper_opportunity(
            opportunity,
            client=holder.client,
            kraken_client=kraken_client or KrakenClient(transport=transport),
            settings=_ProtectionSettings(),
            now=now,
            clock=clock,
        ),
        transport,
    )


def _stage_counts(holder, trade_id: str) -> dict[str, int]:
    """Committed stages of one trade, counted directly in the canonical store."""

    return {
        "order_intent": len(_protection_rows(holder, PAPER_ORDER_INTENT_RECORDED, trade_id)),
        "quote_evidence": len(_protection_rows(holder, PAPER_QUOTE_EVIDENCE_RECORDED)),
        "execution_attempt": len(
            _protection_rows(holder, PAPER_EXECUTION_ATTEMPT_RECORDED, trade_id)
        ),
        "fill": len(_protection_rows(holder, PAPER_FILL_RECORDED, trade_id)),
        "protection_plan": len(
            _protection_rows(holder, PAPER_PROTECTION_PLAN_RECORDED, trade_id)
        ),
        "reconciliation": len(
            _protection_rows(holder, PAPER_RECONCILIATION_RECORDED, trade_id)
        ),
    }


def test_ac_002_a_planned_reservation_is_executed_by_the_seam_as_one_entry(lifecycle):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-002: the seam executes the admitted entry."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    disposition_id = admitted.disposition_id

    assert admitted.status == CAPITAL_ADMISSION_ADMITTED
    assert admitted.reservation_id
    assert admitted.paper_trade_id

    before = lifecycle.client.get_paper_v2_execution_state(disposition_id)
    assert before.status == "OK"
    assert before.admitted is True
    assert before.reservation_id == admitted.reservation_id
    assert before.entry_order_intent_id is None
    assert before.fill_id is None
    # Nothing is executed yet: the admission is committed, the entry is not.
    assert len(_protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED)) == 1
    assert len(_protection_rows(lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1
    assert _stage_counts(lifecycle, admitted.paper_trade_id) == {
        "order_intent": 0,
        "quote_evidence": 0,
        "execution_attempt": 0,
        "fill": 0,
        "protection_plan": 0,
        "reconciliation": 0,
    }
    fingerprint_before = _store_fingerprint(lifecycle.server.writer)

    clock = _Clock(QUALIFICATION_TIME)
    outcome, transport = _dispatch_once(lifecycle, opportunity, clock=clock)

    assert outcome.status == OUTCOME_EXECUTED, outcome.detail
    assert outcome.reason_code == outcome.execution.status
    assert outcome.admission_consumed is True
    assert outcome.admission_consumed is True
    assert outcome.execution.paper_trade_id == admitted.paper_trade_id
    assert outcome.execution.reservation_id == admitted.reservation_id
    assert outcome.execution.entry_order_intent_id
    assert outcome.execution.fill_id
    assert transport.requests

    # Exactly one of each stage, and the entry really is filled.
    assert _stage_counts(lifecycle, admitted.paper_trade_id) == {
        "order_intent": 1,
        "quote_evidence": 1,
        "execution_attempt": 1,
        "fill": 1,
        "protection_plan": 1,
        "reconciliation": 0,
    }
    state = lifecycle.client.get_paper_v2_execution_state(disposition_id)
    assert state.decision_context_id == context_id
    assert state.filled_quantity > 0
    assert state.requested_reservation_amount == admitted.requested_reservation_amount

    # One admission, one disposition, one active reservation. The dispatch added
    # no canonical admission of its own.
    assert len(_protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED)) == 1
    assert len(_protection_rows(lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)) == 1
    status, version, reserved, active, reservations = _capital_book(lifecycle.server.writer)
    assert (status, active) == ("OK", 1)
    assert reserved == admitted.requested_reservation_amount
    assert [row[3] for row in reservations] == ["ACTIVE"]
    assert version == admitted.canonical_portfolio_version


def test_ac_002_the_dispatch_keeps_the_committed_admission_and_reservation_intact(
    lifecycle,
):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-002: the committed admission is not rewritten."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    disposition_before = _protection_rows(
        lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED
    )
    request_before = _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED)

    clock = _Clock(QUALIFICATION_TIME)
    outcome, _transport_ = _dispatch_once(lifecycle, opportunity, clock=clock)
    assert outcome.status == OUTCOME_EXECUTED, outcome.detail

    trade_id = outcome.execution.paper_trade_id
    # The committed admission evidence is byte-identical: the dispatch consumed it
    # and minted no second admission, disposition or reservation of its own.
    assert _protection_rows(
        lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED
    ) == disposition_before
    assert _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED) == request_before
    assert len(request_before) == 1

    # The canonical reservation ledger still holds exactly the committed
    # reservation: same identity, same amount, still ACTIVE for the open position.
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert (status, active) == ("OK", 1)
    assert reserved == admitted.requested_reservation_amount
    assert [row[0] for row in reservations] == [admitted.reservation_id]
    assert [row[1] for row in reservations] == [admitted.paper_trade_id]
    assert [row[3] for row in reservations] == ["ACTIVE"]
    assert [row[4] for row in reservations] == [admitted.requested_reservation_amount]

    # The entry intent declares the committed ancestry, not an invented one.
    entry_intents = [
        row
        for row in _protection_rows(lifecycle, PAPER_ORDER_INTENT_RECORDED, trade_id)
        if row["intent_role"] == "ENTRY"
    ]
    assert len(entry_intents) == 1
    assert entry_intents[0]["decision_context_id"] == context_id
    assert entry_intents[0]["reservation_id"] == admitted.reservation_id
    assert entry_intents[0]["paper_trade_id"] == admitted.paper_trade_id


# ---------------------------------------------------------------------------
# AC-003: approved exits, residual exposure and the released reservation
# ---------------------------------------------------------------------------


def _entry_then_arm(lifecycle, *, bid=99.9, ask=100.0, bid_qty=10.0):
    """Run the seam's entry and arm the committed plan with the real runtime."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    _context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    clock = _Clock(QUALIFICATION_TIME)
    outcome, _transport_ = _dispatch_once(
        lifecycle, opportunity, clock=clock, bid=bid, ask=ask, bid_qty=bid_qty
    )
    assert outcome.status == OUTCOME_EXECUTED, outcome.detail
    armed = _protection_sweep(lifecycle, clock=clock)
    assert armed.activated == 1, armed.details
    return opportunity, admitted, outcome, clock


def _cross_and_fill_stop(lifecycle, clock, *, bid: float, ask: float):
    """Cross the armed stop, then fill its committed EXIT order at the bid."""

    _next_scan(clock)
    triggered = _protection_sweep(
        lifecycle, clock=clock, transport=_protection_transport(clock, bid=bid, ask=ask)
    )
    _next_scan(clock)
    executed = _protection_sweep(
        lifecycle, clock=clock, transport=_protection_transport(clock, bid=bid, ask=ask)
    )
    return triggered, executed


def test_ac_003_an_approved_stop_exit_releases_the_reservation_and_reconciles_the_book(
    lifecycle,
):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-003: an approved stop exit reconciles and releases."""

    _opportunity_, admitted, entry, clock = _entry_then_arm(lifecycle)
    trade_id = entry.execution.paper_trade_id
    amount = admitted.requested_reservation_amount

    # The approved stop is crossed and its committed EXIT order fills into the bid.
    triggered, executed = _cross_and_fill_stop(lifecycle, clock, bid=90.0, ask=90.5)
    assert triggered.triggered == 1, triggered.details
    assert executed.exit_attempted == 1, executed.details

    fills = _protection_rows(lifecycle, PAPER_FILL_RECORDED, trade_id)
    exits = [fill for fill in fills if fill["side"] == "SELL"]
    assert len(exits) == 1
    # A long exit sells into the executable bid, and the whole entry leaves.
    assert float(exits[0]["price"]) == 90.0
    assert float(exits[0]["quantity"]) == float(entry.execution.filled_quantity)

    # Reserved capital is still held while the trade is merely flat.
    assert _capital_book(lifecycle.server.writer)[3] == 1

    flat = _protection_sweep(lifecycle, clock=clock)
    assert flat.terminalized == 1, flat.details
    verified = _protection_sweep(lifecycle, clock=clock)
    assert verified.terminalized == 1, verified.details
    assert _protection_item(lifecycle, trade_id).final_verified is True

    # Released exactly once, and the reconciliation states the canonical totals.
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert status == "OK"
    assert (active, reserved) == (0, 0.0)
    assert [row[3] for row in reservations] == ["RELEASED"]

    reconciliations = _protection_rows(
        lifecycle, PAPER_RECONCILIATION_RECORDED, trade_id
    )
    assert [row["terminal_reconciliation_state"] for row in reconciliations] == [
        "FLAT_AWAITING_RECONCILIATION",
        "FINAL_VERIFIED",
    ]
    final = reconciliations[-1]
    assert final["position_state"] == "FLAT"
    assert float(final["reserved_capital"]) == amount
    assert float(final["filled_exit_quantity"]) == float(entry.execution.filled_quantity)
    assert float(final["remaining_quantity"]) == 0.0

    # Costs are counted once, from the committed fills, and the net is their net.
    gross = sum(
        float(fill["quantity"])
        * float(fill["price"])
        * (1.0 if fill["side"] == "SELL" else -1.0)
        for fill in fills
    )
    costs = sum(
        float(fill["fee_cost"])
        + float(fill["spread_cost"])
        + float(fill["slippage_cost"])
        + float(fill["other_supported_cost"])
        for fill in fills
    )
    assert float(final["realized_gross_pnl"]) == gross
    assert float(final["recorded_execution_costs"]) == costs
    assert float(final["realized_net_pnl"]) == gross - costs

    # Capacity is released once: a further sweep writes nothing at all.
    again = _protection_sweep(lifecycle, clock=clock)
    assert (again.triggered, again.exit_attempted, again.terminalized) == (0, 0, 0)
    assert (
        _protection_rows(lifecycle, PAPER_RECONCILIATION_RECORDED, trade_id)
        == reconciliations
    )


def test_ac_003_a_partial_target_exit_retains_the_residual_exposure_once(lifecycle):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-003: a partial exit keeps the residual, counted once."""

    _opportunity_, admitted, entry, clock = _entry_then_arm(lifecycle)
    trade_id = entry.execution.paper_trade_id
    amount = admitted.requested_reservation_amount
    entry_quantity = float(entry.execution.filled_quantity)

    # The approved first target is crossed and its configured fraction leaves.
    _next_scan(clock)
    triggered = _protection_sweep(
        lifecycle,
        clock=clock,
        transport=_protection_transport(clock, bid=110.0, ask=110.5),
    )
    assert triggered.triggered == 1, triggered.details
    _next_scan(clock)
    executed = _protection_sweep(
        lifecycle,
        clock=clock,
        transport=_protection_transport(clock, bid=110.0, ask=110.5),
    )
    assert executed.exit_attempted == 1, executed.details
    _next_scan(clock)
    residual = _protection_sweep(lifecycle, clock=clock)
    assert residual.partially_exited == 1, residual.details

    exits = [
        fill
        for fill in _protection_rows(lifecycle, PAPER_FILL_RECORDED, trade_id)
        if fill["side"] == "SELL"
    ]
    assert len(exits) == 1
    assert float(exits[0]["quantity"]) == entry_quantity / 2

    # The residual stays exposed, still protected, and occupancy is counted once:
    # one reservation of the committed amount, and a residual plan that carries the
    # original stop forward without altering the original plan.
    item = _protection_item(lifecycle, trade_id)
    assert float(item.remaining_quantity) == entry_quantity / 2
    plans = _protection_rows(lifecycle, PAPER_PROTECTION_PLAN_RECORDED, trade_id)
    assert [int(plan["plan_seq"]) for plan in plans] == [0, 1]
    assert float(plans[1]["stop_price"]) == float(plans[0]["stop_price"])
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert (status, active) == ("OK", 1)
    assert reserved == amount
    assert [row[4] for row in reservations] == [amount]

    # The residual's approved stop closes it, and only then is capital released.
    triggered_stop, closed = _cross_and_fill_stop(lifecycle, clock, bid=89.0, ask=89.5)
    assert triggered_stop.triggered == 1, triggered_stop.details
    assert closed.exit_attempted == 1, closed.details
    for _ in range(3):
        _protection_sweep(lifecycle, clock=clock)
    assert _protection_item(lifecycle, trade_id).final_verified is True

    exits = [
        fill
        for fill in _protection_rows(lifecycle, PAPER_FILL_RECORDED, trade_id)
        if fill["side"] == "SELL"
    ]
    assert len(exits) == 2
    assert sum(float(fill["quantity"]) for fill in exits) == entry_quantity
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert (status, active, reserved) == ("OK", 0, 0.0)
    assert [row[3] for row in reservations] == ["RELEASED"]
    final = _protection_rows(lifecycle, PAPER_RECONCILIATION_RECORDED, trade_id)[-1]
    assert final["terminal_reconciliation_state"] == "FINAL_VERIFIED"
    assert float(final["reserved_capital"]) == amount
    assert float(final["filled_exit_quantity"]) == entry_quantity


# ---------------------------------------------------------------------------
# AC-005: no fabricated fill, and no seam-authored repair
# ---------------------------------------------------------------------------


def test_ac_005_a_book_that_cannot_support_the_entry_fabricates_no_fill(lifecycle):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-005: an unsupportable entry fabricates no fill."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    _context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    disposition_before = _protection_rows(
        lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED
    )
    request_before = _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED)
    amount = admitted.requested_reservation_amount

    clock = _Clock(QUALIFICATION_TIME)
    # Top-of-book depth of 1.0 cannot support the approved 5.0 entry size.
    outcome, transport = _dispatch_once(
        lifecycle, opportunity, clock=clock, bid_qty=1.0
    )

    # A real dispatch happened, and the entry did not become a fabricated success.
    assert transport.requests
    assert outcome.status != OUTCOME_EXECUTED
    assert outcome.execution is None
    assert outcome.reason_code in {
        "RETRY_REQUIRED",
        "NO_FILL_TERMINAL",
        REASON_PRODUCER_FAILED,
    }, outcome.reason_code

    trade_id = admitted.paper_trade_id
    assert _protection_rows(lifecycle, PAPER_FILL_RECORDED, trade_id) == []
    state = lifecycle.client.get_paper_v2_execution_state(admitted.disposition_id)
    assert state.fill_id is None
    assert float(state.filled_quantity) == 0.0

    # The seam wrote no repair and no release of its own: the admission evidence is
    # byte-identical, and any terminal evidence is the producer's own, stating the
    # canonical reservation exactly.
    assert (
        _protection_rows(lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)
        == disposition_before
    )
    assert _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED) == request_before
    terminal = _protection_rows(lifecycle, PAPER_RECONCILIATION_RECORDED, trade_id)
    for reconciliation in terminal:
        assert float(reconciliation["reserved_capital"]) == amount
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert status == "OK"
    if terminal:
        # The producer decided a terminal outcome, so the reservation is terminal
        # for its own stated reason and never for a seam-authored one.
        assert (active, reserved) == (0, 0.0)
        assert [row[3] for row in reservations] == ["RELEASED"]
    else:
        assert (active, reserved) == (1, amount)
        assert [row[3] for row in reservations] == ["ACTIVE"]


def test_ac_005_a_producer_failure_is_reported_without_a_seam_authored_release(lifecycle):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-005: a producer failure is reported, not repaired."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    _context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    disposition_before = _protection_rows(
        lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED
    )
    request_before = _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED)
    amount = admitted.requested_reservation_amount

    clock = _Clock(QUALIFICATION_TIME)
    transport = _RefusingTransport(error=KrakenTransportError("market data down"))
    outcome, _unused = _dispatch_once(
        lifecycle,
        opportunity,
        clock=clock,
        kraken_client=KrakenClient(transport=transport),
    )

    assert outcome.status == OUTCOME_FAILED
    assert outcome.reason_code == REASON_PRODUCER_FAILED
    assert outcome.execution is None
    assert transport.calls
    trade_id = admitted.paper_trade_id
    assert _protection_rows(lifecycle, PAPER_FILL_RECORDED, trade_id) == []
    state = lifecycle.client.get_paper_v2_execution_state(admitted.disposition_id)
    assert state.fill_id is None

    # No seam-authored repair or release: the committed admission evidence is
    # byte-identical, and every terminal fact carries the canonical reservation.
    assert (
        _protection_rows(lifecycle, PAPER_OPPORTUNITY_DISPOSITION_RECORDED)
        == disposition_before
    )
    assert _protection_rows(lifecycle, PAPER_ADMISSION_REQUEST_RECORDED) == request_before
    for reconciliation in _protection_rows(
        lifecycle, PAPER_RECONCILIATION_RECORDED, trade_id
    ):
        assert float(reconciliation["reserved_capital"]) == amount
    status, _version, reserved, active, reservations = _capital_book(
        lifecycle.server.writer
    )
    assert status == "OK"
    assert [row[3] for row in reservations] in (["ACTIVE"], ["RELEASED"])
    if reservations == []:  # pragma: no cover - defensive, the trade is reserved
        raise AssertionError("the admitted trade lost its canonical reservation")
    assert active in (0, 1)
    assert reserved in (0.0, amount)



# ---------------------------------------------------------------------------
# AC-004: a restart and a replay consume the committed admission exactly once
# ---------------------------------------------------------------------------



def test_ac_004_a_restart_replay_reports_the_same_outcome_without_new_evidence(
    lifecycle,
):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-004: a restart replay adds no evidence."""

    opportunity = _prt_opportunity(LIFECYCLE_SYMBOL)
    context_id, admitted = _commit_planned_admission(
        lifecycle, opportunity, moment=QUALIFICATION_TIME
    )
    clock = _Clock(QUALIFICATION_TIME)
    first, _transport_ = _dispatch_once(lifecycle, opportunity, clock=clock)
    assert first.status == OUTCOME_EXECUTED, first.detail
    trade_id = first.execution.paper_trade_id
    counts_after_first = _stage_counts(lifecycle, trade_id)
    book_after_first = _capital_book(lifecycle.server.writer)
    fingerprint_after_first = _store_fingerprint(lifecycle.server.writer)

    # A replay in the same process changes nothing.
    replay, _transport_ = _dispatch_once(lifecycle, opportunity, clock=clock)
    assert replay.status == OUTCOME_EXECUTED
    assert replay.admission_consumed is True
    assert replay.execution.paper_trade_id == first.execution.paper_trade_id
    assert replay.execution.reservation_id == first.execution.reservation_id
    assert replay.execution.entry_order_intent_id == first.execution.entry_order_intent_id
    assert replay.execution.fill_id == first.execution.fill_id
    assert _store_fingerprint(lifecycle.server.writer) == fingerprint_after_first

    # A restart reads the same committed state and writes nothing new.
    lifecycle.restart()
    after_restart, _transport_ = _dispatch_once(lifecycle, opportunity, clock=clock)

    assert after_restart.status == OUTCOME_EXECUTED
    assert after_restart.admission_consumed is True
    assert after_restart.execution.paper_trade_id == first.execution.paper_trade_id
    assert after_restart.execution.fill_id == first.execution.fill_id
    assert _stage_counts(lifecycle, trade_id) == counts_after_first
    assert _capital_book(lifecycle.server.writer) == book_after_first
    assert _store_fingerprint(lifecycle.server.writer) == fingerprint_after_first
    state = lifecycle.client.get_paper_v2_execution_state(admitted.disposition_id)
    assert state.decision_context_id == context_id
    assert state.reservation_id == admitted.reservation_id


# ---------------------------------------------------------------------------
# AC-001: an undeclared committed value is refused, not assumed
# ---------------------------------------------------------------------------


def test_ac_001_a_projection_without_a_quote_currency_is_refused_before_dispatch(env):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-001: no committed currency is refused."""

    server, _client = env
    opportunity = _opportunity()
    stub = _StubClient(
        lambda disposition_id: _projection(disposition_id, quote_currency=None)
    )
    transport = _RefusingTransport()

    outcome = _dispatch(
        env,
        client=stub,
        kraken=KrakenClient(transport=transport),
        opportunity=opportunity,
    )

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert outcome.execution is None
    assert outcome.admission_consumed is False
    assert "quote currency" in outcome.detail
    assert repr(opportunity.quote_currency) in outcome.detail
    assert transport.calls == []
    assert all(value == 0 for value in _counts(server.writer).values())


def test_ac_001_a_projection_without_a_reservation_amount_is_refused_before_dispatch(
    env,
):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-001: no committed amount is refused."""

    server, _client = env
    opportunity = _opportunity()
    stub = _StubClient(
        lambda disposition_id: _projection(
            disposition_id, requested_reservation_amount=None
        )
    )
    transport = _RefusingTransport()

    outcome = _dispatch(
        env,
        client=stub,
        kraken=KrakenClient(transport=transport),
        opportunity=opportunity,
    )

    assert outcome.status == OUTCOME_BLOCKED
    assert outcome.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert outcome.execution is None
    assert outcome.admission_consumed is False
    assert "requested reservation amount" in outcome.detail
    assert repr(float(opportunity.requested_reservation_amount)) in outcome.detail
    assert transport.calls == []
    assert all(value == 0 for value in _counts(server.writer).values())


def test_ac_001_a_real_committed_admission_without_a_declared_amount_is_refused(env):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-001: a real admission read without its amount is refused."""

    server, live_client = env
    seeded = _seed_committed_admission(env)
    assert seeded.status == OUTCOME_EXECUTED

    honest = _dispatch(env)
    assert honest.status == OUTCOME_EXECUTED
    counts = _counts(server.writer)
    portfolio = _portfolio(server.writer)

    skewed = _SkewedWriterClient(live_client, omit="requested_reservation_amount")
    transport = _RefusingTransport()
    refused = _dispatch(env, client=skewed, kraken=KrakenClient(transport=transport))

    assert refused.status == OUTCOME_BLOCKED
    assert refused.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert refused.admission_consumed is False
    assert refused.admission.admitted is True
    assert refused.admission.reservation_id == seeded.reservation_id
    assert "requested reservation amount" in refused.detail
    assert transport.calls == []
    assert _counts(server.writer) == counts
    assert _portfolio(server.writer) == portfolio


def test_ac_001_a_real_committed_admission_without_a_declared_currency_is_refused(env):
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-001: a real admission read without its currency is refused."""

    server, live_client = env
    seeded = _seed_committed_admission(env)
    assert seeded.status == OUTCOME_EXECUTED
    counts = _counts(server.writer)

    skewed = _SkewedWriterClient(live_client, omit="quote_currency")
    transport = _RefusingTransport()
    refused = _dispatch(env, client=skewed, kraken=KrakenClient(transport=transport))

    assert refused.status == OUTCOME_BLOCKED
    assert refused.reason_code == REASON_ADMISSION_INTENT_DIVERGENCE
    assert refused.admission_consumed is False
    assert refused.admission.reservation_id == seeded.reservation_id
    assert "quote currency" in refused.detail
    assert transport.calls == []
    assert _counts(server.writer) == counts


# ---------------------------------------------------------------------------
# AC-006: rotation from committed proceeds stays unratified and unimplemented
# ---------------------------------------------------------------------------


def test_ac_006_an_unratified_rotation_policy_refuses_deterministically():
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-006: an unratified rotation policy refuses."""

    from app.opip.contracts.portfolio import (  # noqa: PLC0415 - scoped to this proof
        OPEN_OWNER_INPUTS,
        PortfolioPlanAbstentionReason,
        RotationSwitchStatus,
        assess_rotation_switch,
        unratified_plan_constraints,
    )
    from tests.test_opip_r5_d_portfolio_planning import (  # noqa: PLC0415
        T0 as R5D_T0,
        plan as r5d_plan,
    )

    # `OQ-05` and `OQ-06` are owner inputs: no advantage, cooldown, uncertainty
    # treatment or turnover budget is invented anywhere.
    constraints = unratified_plan_constraints()
    assert constraints.is_ratified is False
    assert constraints.open_owner_inputs == OPEN_OWNER_INPUTS == ("OQ-05", "OQ-06")
    fields = constraints.to_dict()
    assert fields["min_switch_advantage_dollars"] is None
    assert fields["uncertainty_treatment"] is None
    assert fields["turnover_budget_dollars"] is None
    assert fields["cooldown_hours"] is None

    # The shipped plan binds that unratified policy, so the frozen switch gate
    # refuses instead of quantifying a switch, and it refuses the same way twice.
    shipped = r5d_plan()
    assessment = assess_rotation_switch(shipped, now=R5D_T0 + timedelta(hours=1))
    assert assessment.status is RotationSwitchStatus.ABSTAINED
    assert assessment.abstention_reason is PortfolioPlanAbstentionReason.POLICY_NOT_BOUND
    assert assessment.advantage_dollars is None
    assert assessment.minimum_advantage_dollars is None
    assert assessment.uncertainty_treatment is None
    repeat = assess_rotation_switch(shipped, now=R5D_T0 + timedelta(hours=1))
    assert repeat.to_dict() == assessment.to_dict()


def test_ac_006_the_seam_exposes_no_rotation_release_or_second_leg_surface():
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-006: the seam exposes no rotation or release surface."""

    source = SEAM_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", None)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    for forbidden in (
        "admit_paper_opportunity",
        "admit_planned_reservation",
        "release_reservation",
        "expire_reservations",
        "adjust_reservation_for_fill",
        "assess_rotation_switch",
        "compose_rotation_replacement",
        "rotation_comparison",
        "run_protection_sweep",
    ):
        assert forbidden not in called, forbidden
    # The one producer the seam is allowed to dispatch is dispatched.
    assert "run_paper_v2_opportunity" in called

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    # This increment added no import and no rotation surface to the seam.
    assert imported == {
        "__future__",
        "dataclasses",
        "datetime",
        "typing",
        "app.services.paper_v2_execution",
    }, sorted(imported)
    assert "rotation" not in source.lower()
    # No release verb is called: the seam names the producer's refusal vocabulary
    # only in prose, and every capital transition stays the writer's own.
    assert "release(" not in source
    assert "release_reservation" not in called


# ---------------------------------------------------------------------------
# AC-007: posture, authority and this increment's own bookkeeping
# ---------------------------------------------------------------------------


def test_ac_007_the_contract_pointer_and_parent_increment_are_self_consistent():
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-007: this contract, its pointer and the parent agree."""

    from tests.atdd_scope import (  # noqa: PLC0415 - scoped to this proof
        check_scope,
        load_acceptance_index,
        load_scope_contracts,
        parse_scope_contract,
        read_increment_pointer,
    )

    contract_dir = APP_ROOT / "docs" / "atdd" / "scope-contracts"
    contract_path = contract_dir / f"{INCREMENT}.md"
    assert contract_path.is_file(), contract_path

    contract = parse_scope_contract(contract_path.read_text(encoding="utf-8"))
    assert contract.increment == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert set(contract.traceability) == set(contract.acceptance_criteria)
    assert set(contract.implementation_map) == set(contract.acceptance_criteria)
    for ac_id, body in contract.acceptance_criteria.items():
        for label in ("GIVEN:", "WHEN:", "THEN:"):
            assert label in body, (ac_id, label)

    contracts = load_scope_contracts(contract_dir)
    pointer = read_increment_pointer()
    assert pointer == INCREMENT
    assert len([c for c in contracts if c.increment == pointer]) == 1, pointer
    # The parent increment this one closes is still a declared contract, so its
    # own pointer-based scope control and tests keep working.
    assert any(
        c.increment == "ATDD-R5-E-research-paper-execution" for c in contracts
    )

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.passed, result.reasons
    for ac_id, nodes in contract.traceability.items():
        assert set(nodes) == set(index.by_ac.get((INCREMENT, ac_id), frozenset())), ac_id
    assert not [
        node
        for node in index.unscoped_tests
        if node.startswith("tests/test_opip_r5_e_portfolio_lifecycle_closure.py")
    ]
    for paths in contract.implementation_map.values():
        for path in paths:
            assert (APP_ROOT.parent / path).is_file(), path


def test_ac_007_the_posture_and_release_surface_are_unchanged():
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-007: the release posture is unchanged."""

    from app.services.release_profiles import RELEASE_PROFILES  # noqa: PLC0415

    assert {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"} <= set(RELEASE_PROFILES)
    shadow = RELEASE_PROFILES["EVIDENCE_SHADOW"]
    assert shadow["status"] == "ACTIVE"
    assert shadow["allowed_modes"] == {
        "OPIP_FEATURE_BUS_MODE": "shadow",
        "OPIP_CANONICAL_WRITER_MODE": "shadow",
        "OPIP_TARGET_SPINE_MODE": "shadow",
        "OPIP_PAPER_V2_MODE": "off",
        "OPIP_COMMITTEE_MODE": "off",
        "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD": "1000.0",
    }
    assert RELEASE_PROFILES["TARGET_PAPER"]["status"] == "BLOCKED"
    for name, profile in RELEASE_PROFILES.items():
        for key in profile["allowed_modes"]:
            assert not key.startswith("OPIP_R5"), (name, key)

    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_RELEASE_PROFILE: "EVIDENCE_SHADOW"' in compose
    assert 'OPIP_PAPER_V2_MODE: "off"' in compose


def test_ac_007_the_seam_grants_no_authority_and_no_application_module_imports_it():
    """ATDD-R5-E-portfolio-lifecycle-closure/AC-007: no authority, and the seam stays dormant."""

    outcome = seam.ResearchPaperExecutionOutcome(
        status=OUTCOME_BLOCKED,
        reason_code=REASON_ADMISSION_INTENT_DIVERGENCE,
        disposition_id="disposition-x",
    )
    assert outcome.grants_execution_authority is False
    with pytest.raises(ValueError):
        seam.ResearchPaperExecutionOutcome(
            status=OUTCOME_EXECUTED,
            reason_code="EXECUTED",
            disposition_id="disposition-x",
            grants_execution_authority=True,
        )

    offenders = [
        path.relative_to(APP_ROOT).as_posix()
        for path in sorted((APP_ROOT / "app").rglob("*.py"))
        if "__pycache__" not in path.parts
        and path.resolve() != SEAM_PATH
        and "paper_v2_capital_admission_execution" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []

    candidates = [
        APP_ROOT / "app",
        APP_ROOT / "deploy",
        APP_ROOT.parent / ".github",
    ]
    suffixes = {".py", ".yml", ".yaml", ".sh", ".json", ".toml", ".cfg", ".env"}
    files = [
        path
        for root in candidates
        if root.is_dir()
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix in suffixes
    ]
    files.extend(sorted(APP_ROOT.glob("docker-compose*.yml")))
    named = [
        path.relative_to(APP_ROOT.parent).as_posix()
        for path in files
        if "paper_v2_capital_admission_execution"
        in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert named == [], named

