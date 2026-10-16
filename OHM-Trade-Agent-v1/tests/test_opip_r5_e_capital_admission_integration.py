"""ATDD acceptance tests for the R5-E capital admission integration slice.

Increment: `ATDD-R5-E-capital-admission-integration`. Every test cites its
criterion in the docstring as `INCREMENT/AC-NNN`, and the module is marked
`pytest.mark.acceptance`, so both the scope checker and `pytest -m acceptance`
read the same citations.

The tested surface is the seam `app/services/capital_admission_integration.py`:
`admit_planned_reservation` translates exactly one active R5-D planned
reservation into exactly one frozen `PaperAdmissionRequest`, calls the existing
canonical authority `WriterClient.admit_paper_opportunity` exactly once and
reports the canonical writer's own decision verbatim.

The increment adds no write path, no table, no event type, no `WriterIntent`, no
allocator, no scheduler, no environment read and no order or exchange surface: it
is dormant (`OPIP_PAPER_V2_MODE=off`, no runtime importer) and every record
refuses to grant execution authority. The tests are hermetic - no clock, no
network, no environment read - and they drive the real `CanonicalWriter` against
a `tmp_path` store, so every canonical token asserted here is the writer's own.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from dataclasses import replace as _dc_replace
from app.services import capital_admission_integration as _seam
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.canonical.client import InProcessWriterClient  # noqa: E402
from app.opip.canonical.models import (  # noqa: E402
    BOUND_RESERVATION_CONSTRAINTS,
    UNBOUND_RESERVATION_CONSTRAINTS,
)
from app.opip.canonical.server import CanonicalWriterServer  # noqa: E402
from app.opip.canonical.writer import CanonicalWriter  # noqa: E402
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (  # noqa: E402
    PAPER_CAPITAL_POLICY_VERSION,
    resolve_capital_policy,
)
from app.opip.contracts.portfolio import (  # noqa: E402
    PortfolioDirection,
    PortfolioReservation,
    PortfolioReservationPlan,
)
from app.services.capital_admission_integration import (  # noqa: E402
    CAPITAL_ADMISSION_ABSTAINED,
    CAPITAL_ADMISSION_ACK_UNUSABLE,
    CAPITAL_ADMISSION_ADMITTED,
    CAPITAL_ADMISSION_CANONICAL_ACCEPTED,
    CAPITAL_ADMISSION_CANONICAL_REFUSAL,
    CAPITAL_ADMISSION_CANONICAL_UNKNOWN,
    CAPITAL_ADMISSION_CONSTRAINT_UNBOUND,
    CAPITAL_ADMISSION_CURRENCY_REFUSED,
    CAPITAL_ADMISSION_DEFAULT_CONSTRAINTS,
    CAPITAL_ADMISSION_ENVIRONMENT_REFUSED,
    CAPITAL_ADMISSION_INDETERMINATE,
    CAPITAL_ADMISSION_INPUT_INVALID,
    CAPITAL_ADMISSION_PLAN_STALE,
    CAPITAL_ADMISSION_POLICY_UNRESOLVED,
    CAPITAL_ADMISSION_REFUSED,
    CAPITAL_ADMISSION_SCHEMA_VERSION,
    CAPITAL_ADMISSION_STATUSES,
    CapitalAdmissionDisposition,
    admit_planned_reservation,
)
from app.services.capital_rotation_intelligence import (  # noqa: E402
    CapitalAccountingState,
    CapitalEnvironment,
    CapitalFeeState,
    CapitalFreshness,
    CapitalLimitKind,
    CapitalLimitUsage,
    CapitalMarkState,
    CapitalPartition,
    CapitalPosition,
    CapitalReconciliation,
    CapitalReconciliationStatus,
    CapitalSnapshot,
    CapitalSourceWatermark,
    CashPartitionLedger,
    SleeveUsage,
)
from app.services.release_profiles import (  # noqa: E402
    get_release_profiles,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-E-capital-admission-integration"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
SEAM_MODULE = APP_ROOT / "app" / "services" / "capital_admission_integration.py"

NOW = datetime(2026, 9, 19, 11, 10, 0, tzinfo=timezone.utc)
STAMPED = NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
T0 = datetime(2026, 9, 19, 11, 0, 0, tzinfo=timezone.utc)

CAP_VERSION = "pv-r5e2i-0001"
CAP_ACCOUNT = "PAPER-LEDGER-R5E2I"
CAP_ENGINE = "opip-paper-v2"
CAP_CURRENCY = "USD"
WINDOW_IDENTITY = "window-r5e2i-0001"
SYMBOL = "SOLUSD"
AMOUNT = 500.0


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def server(tmp_path) -> CanonicalWriterServer:
    """The real in-process canonical writer server over a throwaway store."""

    instance = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield instance
    finally:
        instance.stop()


@pytest.fixture
def writer(server) -> CanonicalWriter:
    """The server's own writer, for read-only inspection and context seeding."""

    return server.writer


@pytest.fixture
def client(server) -> InProcessWriterClient:
    """The ``WriterClient`` protocol the seam is written against.

    The seam reads the canonical book through ``get_paper_portfolio_state`` and
    writes through ``admit_paper_opportunity``: those are the protocol's names, so
    the seam is driven here through the real client/server dispatch path rather
    than against the writer object's own ``paper_portfolio_state`` reader.
    """

    return InProcessWriterClient(server)


CANDIDATE = "PCAND:R5E2I:1"
CANDIDATE_TWO = "PCAND:R5E2I:2"


def _seed_context(writer: CanonicalWriter, *, suffix: str = "r5e2i") -> str:
    """A minimal eligible paper decision context, committed canonically."""

    from app.opip.canonical.decision_context_bridge import (
        DecisionContextFacts,
        build_decision_context_payload,
        submit_decision_context,
    )
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    facts = DecisionContextFacts(
        candidate_id=f"candidate-{suffix}",
        episode_id=f"episode-{suffix}",
        instrument_version_id="INSTR:kraken:SOL:USD:1",
        instrument_registration_event_id="EVT:seed-proof",
        snapshot_record_event_id="EVT:seed-snapshot-proof",
        snapshot_id=f"snapshot-{suffix}",
        snapshot_hash=f"snapshot-hash-{suffix}",
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        policy_version=GATE_POLICY_VERSION,
        policy_fingerprint=gate_policy_fingerprint(),
        producing_component="r5e2i-test",
        artifact_or_build_id="build-r5e2i",
        process_instance_id="proc-r5e2i",
        emitted_at=NOW,
        source_record_refs=("source:r5e2i",),
    )
    payload = build_decision_context_payload(facts)
    submit_decision_context(payload, client=writer)
    return str(payload["context_id"])


def _capital_cash(
    *,
    free: str = "10000.00",
    reserves: str = "0.00",
    safety: str = "500.00",
    blocked: str = "0.00",
) -> CashPartitionLedger:
    partitions = {
        CapitalPartition.FREE_CASH: free,
        CapitalPartition.ORDER_RESERVES: reserves,
        CapitalPartition.SAFETY_RESERVE: safety,
        CapitalPartition.OTHER_BLOCKED_CASH: blocked,
    }
    total = f"{sum((Decimal(value) for value in partitions.values()), Decimal('0.00')):.2f}"
    return CashPartitionLedger(partitions=partitions, cash_total=total)


def capital_snapshot(
    *,
    version: str = CAP_VERSION,
    currency: str = CAP_CURRENCY,
    environment: CapitalEnvironment = CapitalEnvironment.PAPER_LEDGER,
) -> CapitalSnapshot:
    """The existing `SC-06` capital book, built only from declared evidence."""

    return CapitalSnapshot(
        portfolio_version=version,
        account=CAP_ACCOUNT,
        environment=environment,
        engine=CAP_ENGINE,
        currency=currency,
        accounting_state=CapitalAccountingState.SUPPORTED,
        cash=_capital_cash(),
        positions=(
            CapitalPosition(
                symbol=SYMBOL,
                quantity=Decimal("0.1"),
                basis_capital="3000.00",
                marked_value="3120.00",
                marked_at=T0,
                mark_reference=f"MARK:{SYMBOL}:1",
            ),
        ),
        marks=CapitalMarkState(
            marked_at=T0,
            mark_source="paper-ledger-mark-v1",
            marked_position_value="3120.00",
        ),
        sleeve_usage=(SleeveUsage(sleeve="TACTICAL", used_capital="1000.00", as_of=T0),),
        limit_usage=(
            CapitalLimitUsage(kind=CapitalLimitKind.RISK, used_capital="900.00", as_of=T0),
            CapitalLimitUsage(
                kind=CapitalLimitKind.CONCENTRATION, used_capital="900.00", as_of=T0
            ),
            CapitalLimitUsage(
                kind=CapitalLimitKind.LIQUIDITY, used_capital="900.00", as_of=T0
            ),
        ),
        fee_state=CapitalFeeState(
            fee_model_version="fee-model-v1",
            fees_settled="0.00",
            fee_watermark=CapitalSourceWatermark(
                ledger_sequence=7, observed_at=T0, state_is_stale=False
            ),
        ),
        source_watermark=CapitalSourceWatermark(
            ledger_sequence=7, observed_at=T0, state_is_stale=False
        ),
        reconciliation=CapitalReconciliation(
            status=CapitalReconciliationStatus.RECONCILED,
            observed_at=T0,
            reference="RECON:R5E2I:1",
            variance="0.00",
        ),
        freshness=CapitalFreshness(
            as_of=T0,
            validity_deadline=T0 + timedelta(seconds=900),
            evidence_complete=True,
        ),
    )



def _fresh_snapshot(deadline_hours: float = 2.0):
    """A snapshot whose freshness window covers the temporal cases under test."""

    base = capital_snapshot()
    return _dc_replace(
        base,
        freshness=_dc_replace(
            base.freshness,
            as_of=T0,
            validity_deadline=T0 + timedelta(hours=deadline_hours),
        ),
    )


def _short_window_plan() -> PortfolioReservationPlan:
    """The same book with a reservation window that closes inside freshness."""

    return PortfolioReservationPlan(
        portfolio_version=CAP_VERSION,
        window_identity=WINDOW_IDENTITY,
        created_at=T0,
        reservations=(
            PortfolioReservation(
                candidate_id=CANDIDATE,
                symbol=SYMBOL,
                direction=PortfolioDirection.LONG,
                reserved_capital=AMOUNT,
                portfolio_version=CAP_VERSION,
                created_at=T0,
                expires_at=T0 + timedelta(seconds=300),
            ),
        ),
    )

def reservation_plan(
    *,
    entries: tuple[tuple[str, float], ...] = ((CANDIDATE, AMOUNT),),
    version: str = CAP_VERSION,
) -> PortfolioReservationPlan:
    """One deterministic R5-D plan of this book, one reservation per entry."""

    return PortfolioReservationPlan(
        portfolio_version=version,
        window_identity=WINDOW_IDENTITY,
        created_at=T0,
        reservations=tuple(
            PortfolioReservation(
                candidate_id=candidate_id,
                symbol=SYMBOL,
                direction=PortfolioDirection.LONG,
                reserved_capital=amount,
                portfolio_version=version,
                created_at=T0,
                expires_at=T0 + timedelta(hours=1),
            )
            for candidate_id, amount in entries
        ),
    )


class RecordingClient:
    """A client that records every call and answers nothing of its own."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get_paper_portfolio_state(self, quote_currency: str):
        self.calls.append(("read", quote_currency))
        raise AssertionError("this attempt must not read the canonical book")

    def admit_paper_opportunity(self, request):
        self.calls.append(("write", request))
        raise AssertionError("this attempt must not write to the canonical authority")


class CountingClient:
    """A ``WriterClient`` whose every RPC is counted."""

    def __init__(self, client) -> None:
        self.client = client
        self.reads = 0
        self.writes = 0
        self.requests: list[object] = []

    def get_paper_portfolio_state(self, quote_currency: str):
        self.reads += 1
        return self.client.get_paper_portfolio_state(quote_currency)

    def admit_paper_opportunity(self, request):
        self.writes += 1
        self.requests.append(request)
        return self.client.admit_paper_opportunity(request)


class LostAckClient:
    """A ``WriterClient`` whose acknowledgement is lost after the commit."""

    def __init__(self, client) -> None:
        self.client = client
        self.commits = 0
        self.last_ack = None

    def get_paper_portfolio_state(self, quote_currency: str):
        return self.client.get_paper_portfolio_state(quote_currency)

    def admit_paper_opportunity(self, request):
        ack = self.client.admit_paper_opportunity(request)
        self.last_ack = ack
        self.commits += 1
        raise RuntimeError(f"simulated lost acknowledgement after {ack.status}")


def _admit(
    client,
    *,
    disposition_id: str,
    context_id: str,
    plan: PortfolioReservationPlan | None = None,
    snapshot: CapitalSnapshot | None = None,
    candidate_id: str | None = None,
    constraints: tuple[str, ...] | None = BOUND_RESERVATION_CONSTRAINTS,
    policy_version: str = PAPER_CAPITAL_POLICY_VERSION,
    expected_version: int | None = 0,
    disposition_time: datetime = NOW,
):
    """One seam attempt with the increment's explicit inputs."""

    return admit_planned_reservation(
        reservation_plan() if plan is None else plan,
        client=client,
        snapshot=capital_snapshot() if snapshot is None else snapshot,
        disposition_id=disposition_id,
        decision_context_id=context_id,
        disposition_time=disposition_time,
        candidate_id=candidate_id,
        binding_constraints=constraints,
        capital_policy_version=policy_version,
        expected_portfolio_version=expected_version,
    )


def _book(writer: CanonicalWriter, quote_currency: str = CAP_CURRENCY) -> tuple:
    """The capital-bearing canonical state: version, amount and identities."""

    state = writer.paper_portfolio_state(quote_currency)
    ledger = writer.paper_reservation_ledger(quote_currency)
    return (
        state.status,
        state.portfolio_version,
        state.reserved_capital,
        state.active_reservations,
        tuple(
            (
                record.reservation_id,
                record.paper_trade_id,
                record.disposition_id,
                record.state,
                record.amount,
            )
            for record in ledger.reservations
        ),
    )


def _store_fingerprint(writer: CanonicalWriter) -> tuple:
    """Every canonical surface a write would move. Read-only inspection."""

    events = writer._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]  # noqa: SLF001
    idempotency = writer._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM idempotency_keys"
    ).fetchone()[0]
    meta = writer._conn.execute(  # noqa: SLF001
        "SELECT schema_version, history_epoch, next_local_sequence FROM meta WHERE id = 1"
    ).fetchone()
    return (
        int(events),
        int(idempotency),
        int(meta["schema_version"]),
        int(meta["history_epoch"]),
        int(meta["next_local_sequence"]),
    )


def _last_disposition_payload(writer: CanonicalWriter) -> dict:
    """The writer's own last committed disposition evidence, decoded."""

    row = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        """
        SELECT payload_json FROM events
        WHERE event_type = ?
        ORDER BY local_sequence DESC, rowid DESC
        LIMIT 1
        """,
        (PAPER_OPPORTUNITY_DISPOSITION_RECORDED,),
    ).fetchone()
    return json.loads(str(row["payload_json"]))


# ---------------------------------------------------------------------------
# AC-001 One planned reservation, exactly one canonical reservation
# ---------------------------------------------------------------------------


def test_ac_001_a_planned_reservation_becomes_exactly_one_canonical_reservation(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-001: the plan gets one owner.

    GIVEN one committed eligible paper decision context, one fresh canonical
    paper book, its capital snapshot and a plan with one active reservation
    WHEN the seam admits that reservation under one disposition identity
    THEN exactly one canonical reservation is committed, the record reports the
    canonical identities and the read-back book, the frozen request is the plan
    translated once with the frozen policy limits, and no authority is granted
    """

    context_id = _seed_context(writer)
    client = CountingClient(client)
    plan = reservation_plan()
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:1",
        context_id=context_id,
        plan=plan,
    )

    assert record.status == CAPITAL_ADMISSION_ADMITTED
    assert record.reason == CAPITAL_ADMISSION_CANONICAL_ACCEPTED
    assert record.schema_version == CAPITAL_ADMISSION_SCHEMA_VERSION
    assert record.replay is False
    assert record.reservation_committed is True
    assert record.grants_execution_authority is False
    assert record.policy_binding_complete is True
    assert record.bound_constraints == BOUND_RESERVATION_CONSTRAINTS
    assert record.unbound_constraints == ()

    # The record reports the plan's own identity and book, and never invents one.
    assert record.disposition_id == "DISP:R5E2I:1"
    assert record.disposition_time == STAMPED
    assert record.expected_portfolio_version == 0
    assert (record.candidate_id, record.plan_id) == (CANDIDATE, plan.plan_id)
    assert record.plan_portfolio_version == CAP_VERSION
    assert (record.account, record.capital_engine) == (CAP_ACCOUNT, CAP_ENGINE)
    assert (record.quote_currency, record.direction) == (CAP_CURRENCY, "LONG")
    assert record.requested_reservation_amount == AMOUNT

    # One write, one read back, and the frozen request is the plan translated once.
    assert client.writes == 1
    assert client.reads == 1
    payload = client.requests[0].as_dict()
    assert payload["disposition_id"] == "DISP:R5E2I:1"
    assert payload["decision_context_id"] == context_id
    assert payload["disposition_seq"] == 0
    assert payload["quote_currency"] == CAP_CURRENCY
    assert payload["requested_capital"] == AMOUNT
    assert payload["requested_reservation_amount"] == AMOUNT
    assert payload["expected_portfolio_version"] == 0
    assert payload["direction"] == "LONG"
    assert payload["disposition_time"]["occurred_at"] == STAMPED
    # The limits come from the frozen writer policy, never from plan or caller.
    assert payload["capital_policy_version"] == policy.policy_version
    assert payload["portfolio_equity_limit"] == policy.portfolio_equity_limit
    assert payload["portfolio_position_limit"] == policy.portfolio_position_limit

    # The canonical ack is surfaced verbatim, including the identities it minted.
    assert record.canonical_status == "OK"
    assert record.canonical_disposition == "ADMITTED"
    assert record.canonical_error_code is None
    assert record.canonical_portfolio_version == 1
    assert record.reservation_id and record.paper_trade_id
    assert record.active_reservations == 1
    assert record.reserved_capital == AMOUNT

    # And the canonical book agrees: one ACTIVE reservation under that identity.
    status, version, reserved, active, rows = _book(writer)
    assert (status, version, reserved, active) == ("OK", 1, AMOUNT, 1)
    assert rows == (
        (
            record.reservation_id,
            record.paper_trade_id,
            "DISP:R5E2I:1",
            "ACTIVE",
            AMOUNT,
        ),
    )
    ledger = writer.paper_reservation_ledger(CAP_CURRENCY)
    assert ledger.grants_authority is False
    assert ledger.policy_binding_complete is False
    assert record.to_dict()["grants_execution_authority"] is False


def test_ac_001_each_candidate_keeps_its_own_reservation_identity(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-001: identity is per reserve.

    GIVEN one plan holding two active reservations of the same book
    WHEN the seam admits each candidate under its own disposition identity
    THEN each admission commits one reservation of its own amount, the identities
    differ, and the book counts both without either being reinterpreted
    """

    context_id = _seed_context(writer)
    plan = reservation_plan(entries=((CANDIDATE, AMOUNT), (CANDIDATE_TWO, 700.0)))

    first = _admit(
        client,
        disposition_id="DISP:R5E2I:1",
        context_id=context_id,
        plan=plan,
        candidate_id=CANDIDATE,
        expected_version=0,
    )
    second = _admit(
        client,
        disposition_id="DISP:R5E2I:2",
        context_id=context_id,
        plan=plan,
        candidate_id=CANDIDATE_TWO,
        expected_version=1,
    )

    assert (first.status, second.status) == (
        CAPITAL_ADMISSION_ADMITTED,
        CAPITAL_ADMISSION_ADMITTED,
    )
    assert first.candidate_id == CANDIDATE
    assert second.candidate_id == CANDIDATE_TWO
    assert first.requested_reservation_amount == AMOUNT
    assert second.requested_reservation_amount == 700.0
    assert first.reservation_id != second.reservation_id
    assert first.paper_trade_id != second.paper_trade_id
    assert first.expected_portfolio_version == 0
    assert second.expected_portfolio_version == 1

    status, version, reserved, active, rows = _book(writer)
    assert (status, version, active) == ("OK", 2, 2)
    assert reserved == AMOUNT + 700.0
    assert [row[3] for row in rows] == ["ACTIVE", "ACTIVE"]
    assert sorted(row[4] for row in rows) == [AMOUNT, 700.0]


# ---------------------------------------------------------------------------
# AC-002 An exact re-attempt replays as one reservation
# ---------------------------------------------------------------------------


def test_ac_002_an_exact_re_attempt_replays_as_one_reservation(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-002: a replay is not a reserve.

    GIVEN an already committed reservation of one disposition identity
    WHEN the identical request is attempted again through the seam
    THEN it is reported ADMITTED with replay True and DUPLICATE_OK, carries the
    same canonical identities, and the book still holds exactly one reservation
    """

    context_id = _seed_context(writer)
    client = CountingClient(client)
    first = _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)
    assert first.status == CAPITAL_ADMISSION_ADMITTED
    book_after_first = _book(writer)

    second = _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)

    assert second.status == CAPITAL_ADMISSION_ADMITTED
    assert second.reason == CAPITAL_ADMISSION_CANONICAL_ACCEPTED
    assert second.replay is True
    assert second.reservation_committed is True
    assert second.canonical_status == "DUPLICATE_OK"
    assert second.canonical_disposition == "ADMITTED"
    assert second.canonical_portfolio_version == 1
    assert second.disposition_id == first.disposition_id
    assert second.reservation_id == first.reservation_id
    assert second.paper_trade_id == first.paper_trade_id
    assert second.expected_portfolio_version == first.expected_portfolio_version == 0

    # Two attempts, one commit: the writer is the only deduplicating authority.
    assert client.writes == 2
    assert _book(writer) == book_after_first
    assert len(_book(writer)[4]) == 1


def test_ac_002_a_replay_writes_nothing_new_to_the_canonical_store(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-002: a replay is read-only.

    GIVEN an already committed reservation
    WHEN the identical request is attempted again
    THEN no event, no idempotency row, no sequence and no watermark move, so the
    canonical store is byte-identical after the re-attempt
    """

    context_id = _seed_context(writer)
    _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)
    fingerprint = _store_fingerprint(writer)

    replay = _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)

    assert replay.canonical_status == "DUPLICATE_OK"
    assert _store_fingerprint(writer) == fingerprint


# ---------------------------------------------------------------------------
# AC-003 A mutated re-attempt under one identity is refused
# ---------------------------------------------------------------------------


def test_ac_003_a_mutated_re_attempt_under_one_identity_is_refused(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-003: one identity, one payload.

    GIVEN an already committed reservation of one disposition identity
    WHEN a mutated attempt reuses that identity with another amount
    THEN the writer's own IDEMPOTENCY_PAYLOAD_CONFLICT is surfaced verbatim as a
    refusal with no canonical identity and no committed-reservation claim
    """

    context_id = _seed_context(writer)
    first = _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)
    assert first.status == CAPITAL_ADMISSION_ADMITTED

    mutated = _admit(
        client,
        disposition_id="DISP:R5E2I:1",
        context_id=context_id,
        plan=reservation_plan(entries=((CANDIDATE, 750.0),)),
    )

    assert mutated.status == CAPITAL_ADMISSION_REFUSED
    assert mutated.reason == CAPITAL_ADMISSION_CANONICAL_REFUSAL
    assert mutated.canonical_status == "REJECTED"
    assert mutated.canonical_disposition is None
    assert mutated.canonical_error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert mutated.canonical_detail
    assert mutated.reservation_id is None
    assert mutated.paper_trade_id is None
    assert mutated.reservation_committed is False
    assert mutated.policy_binding_complete is False
    assert mutated.active_reservations is None
    assert mutated.reserved_capital is None
    # The refusal is reported under the identity the caller tried to reuse.
    assert mutated.disposition_id == "DISP:R5E2I:1"
    assert mutated.requested_reservation_amount == 750.0


def test_ac_003_the_conflicting_mutation_leaves_the_reserved_book_unchanged(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-003: the commit is untouched.

    GIVEN a committed reservation and a refused mutated re-attempt under it
    WHEN the canonical store and the reservation ledger are inspected
    THEN nothing was written and the committed reservation keeps its identity,
    state and amount
    """

    context_id = _seed_context(writer)
    first = _admit(client, disposition_id="DISP:R5E2I:1", context_id=context_id)
    book = _book(writer)
    fingerprint = _store_fingerprint(writer)

    refused = _admit(
        client,
        disposition_id="DISP:R5E2I:1",
        context_id=context_id,
        plan=reservation_plan(entries=((CANDIDATE, 750.0),)),
    )

    assert refused.status == CAPITAL_ADMISSION_REFUSED
    assert _book(writer) == book
    assert _store_fingerprint(writer) == fingerprint
    ledger = writer.paper_reservation_ledger(CAP_CURRENCY)
    assert len(ledger.reservations) == 1
    row = ledger.reservations[0]
    assert row.reservation_id == first.reservation_id
    assert row.state == "ACTIVE"
    assert row.amount == AMOUNT
    assert row.release_basis is None


# ---------------------------------------------------------------------------
# AC-004 A stale expected version is refused atomically
# ---------------------------------------------------------------------------


def test_ac_004_a_stale_expected_version_is_refused_atomically(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-004: the guard fails closed.

    GIVEN a fresh canonical paper book at portfolio version 0
    WHEN the seam is asked to admit against a version the book never reached
    THEN the writer's STALE_PORTFOLIO_VERSION is surfaced verbatim, no canonical
    identity is reported, and the book is unchanged: no partial reservation, no
    release and no version advance exist
    """

    context_id = _seed_context(writer)
    client = CountingClient(client)

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:stale",
        context_id=context_id,
        expected_version=7,
    )

    assert record.status == CAPITAL_ADMISSION_REFUSED
    assert record.reason == CAPITAL_ADMISSION_CANONICAL_REFUSAL
    assert record.canonical_status == "REJECTED"
    assert record.canonical_error_code == "STALE_PORTFOLIO_VERSION"
    assert record.canonical_portfolio_version == 0
    assert "expected portfolio version 7" in record.canonical_detail
    assert "observed 0" in record.canonical_detail
    assert record.expected_portfolio_version == 7
    assert record.reservation_id is None
    assert record.paper_trade_id is None
    assert record.reservation_committed is False
    assert record.active_reservations is None
    assert record.reserved_capital is None

    # The refusal is atomic: the reservation book never moved.
    status, version, reserved, active, rows = _book(writer)
    assert (status, version, reserved, active) == ("OK", 0, 0.0, 0)
    assert rows == ()
    assert client.writes == 1
    # The refusal path is never read back: the seam assumes nothing either way.
    assert client.reads == 0


# ---------------------------------------------------------------------------
# AC-005 The writer's own limits decide, and its tokens are surfaced verbatim
# ---------------------------------------------------------------------------


def test_ac_005_a_capital_limit_refusal_surfaces_only_the_canonical_tokens(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-005: no reinterpretation.

    GIVEN one eligible reservation larger than the frozen equity limit
    WHEN the seam attempts it
    THEN the writer's own CAPITAL_REJECTED decision is reported as a refusal with
    the writer's reason code, no canonical identity, and an unchanged book
    """

    context_id = _seed_context(writer)
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    over = policy.portfolio_equity_limit + 1.0

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:over",
        context_id=context_id,
        plan=reservation_plan(entries=((CANDIDATE, over),)),
    )

    assert record.status == CAPITAL_ADMISSION_REFUSED
    assert record.reason == CAPITAL_ADMISSION_CANONICAL_REFUSAL
    # The canonical tokens are the writer's vocabulary, reported verbatim: the
    # writer acknowledged the request as OK and declined to reserve.
    assert record.canonical_status == "OK"
    assert record.canonical_disposition == "CAPITAL_REJECTED"
    assert record.canonical_error_code is None
    assert record.reservation_id is None
    assert record.paper_trade_id is None
    assert record.reservation_committed is False
    assert record.policy_binding_complete is False
    assert record.expected_portfolio_version == 0
    assert record.requested_reservation_amount == over

    # The writer's own committed evidence names the limit that decided it.
    evidence = _last_disposition_payload(writer)
    assert evidence["disposition_id"] == "DISP:R5E2I:over"
    assert evidence["disposition"] == "CAPITAL_REJECTED"
    assert evidence["reason_code"] == "PORTFOLIO_CAPITAL_LIMIT"

    # A refused admission reserves nothing and does not advance the book.
    status, version, reserved, active, rows = _book(writer)
    assert (status, version, reserved, active) == ("OK", 0, 0.0, 0)
    assert rows == ()


def test_ac_005_a_capacity_limit_refusal_surfaces_only_the_canonical_tokens(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-005: capacity is the writer's.

    GIVEN a book that already holds the frozen policy's maximum of reservations
    WHEN the seam attempts one more admissible reservation
    THEN the writer's CAPACITY_REJECTED decision is reported as a refusal with its
    own reason code, and the existing reservations are untouched
    """

    context_id = _seed_context(writer)
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)
    for index in range(policy.portfolio_position_limit):
        seeded = _admit(
            client,
            disposition_id=f"DISP:R5E2I:seed{index}",
            context_id=context_id,
            expected_version=index,
        )
        assert seeded.status == CAPITAL_ADMISSION_ADMITTED
    book = _book(writer)
    assert (book[1], book[3]) == (policy.portfolio_position_limit, policy.portfolio_position_limit)

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:full",
        context_id=context_id,
        expected_version=policy.portfolio_position_limit,
    )

    assert record.status == CAPITAL_ADMISSION_REFUSED
    assert record.reason == CAPITAL_ADMISSION_CANONICAL_REFUSAL
    assert record.canonical_status == "OK"
    assert record.canonical_disposition == "CAPACITY_REJECTED"
    assert record.reservation_id is None
    assert record.reservation_committed is False
    evidence = _last_disposition_payload(writer)
    assert evidence["disposition_id"] == "DISP:R5E2I:full"
    assert evidence["disposition"] == "CAPACITY_REJECTED"
    assert evidence["reason_code"] == "PORTFOLIO_POSITION_LIMIT"

    # The refusal is not a release: the existing reservations keep their version.
    assert _book(writer) == book


def test_ac_005_the_boundary_is_the_writers_frozen_policy(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-005: no invented limit.

    GIVEN the frozen equity limit of the canonical capital policy
    WHEN the seam admits exactly that amount and then one cent more
    THEN exactly the frozen limit is admitted, one cent above it is refused, and
    the seam itself carries no limit literal at all
    """

    context_id = _seed_context(writer)
    policy = resolve_capital_policy(PAPER_CAPITAL_POLICY_VERSION)

    exact = _admit(
        client,
        disposition_id="DISP:R5E2I:exact",
        context_id=context_id,
        plan=reservation_plan(entries=((CANDIDATE, policy.portfolio_equity_limit),)),
        expected_version=0,
    )
    assert (exact.status, exact.canonical_disposition) == (
        CAPITAL_ADMISSION_ADMITTED,
        "ADMITTED",
    )
    assert exact.reserved_capital == policy.portfolio_equity_limit

    above = _admit(
        client,
        disposition_id="DISP:R5E2I:above",
        context_id=context_id,
        plan=reservation_plan(
            entries=((CANDIDATE, policy.portfolio_equity_limit + 0.01),)
        ),
        expected_version=1,
    )
    assert (above.status, above.canonical_disposition) == (
        CAPITAL_ADMISSION_REFUSED,
        "CAPITAL_REJECTED",
    )
    assert _last_disposition_payload(writer)["reason_code"] == "PORTFOLIO_CAPITAL_LIMIT"
    status, version, reserved, active, rows = _book(writer)
    assert (status, version, reserved, active) == (
        "OK",
        1,
        policy.portfolio_equity_limit,
        1,
    )
    assert len(rows) == 1

    # The seam holds no limit of its own: the numbers come from the policy.
    source = module_source(SEAM_MODULE)
    assert "10000" not in source and "10_000" not in source


# ---------------------------------------------------------------------------
# AC-006 An unprovable attempt abstains or refuses before touching anything
# ---------------------------------------------------------------------------


def test_ac_006_an_unbound_declared_constraint_abstains_before_touching_the_client(
    writer,
    client,
):
    """ATDD-R5-E-capital-admission-integration/AC-006: no silent default admission.

    GIVEN the conservative default binding, which includes the four canonical
    unbound constraints, and a client that records every call
    WHEN the seam attempts the reservation
    THEN it abstains with CAPITAL_CONSTRAINT_UNBOUND, declares the unbound set, and
    never called the client at all, so nothing was read and nothing was written
    """

    context_id = _seed_context(writer)
    client = RecordingClient()
    fingerprint = _store_fingerprint(writer)

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:unbound",
        context_id=context_id,
        constraints=None,
        expected_version=None,
    )

    assert CAPITAL_ADMISSION_DEFAULT_CONSTRAINTS == tuple(
        BOUND_RESERVATION_CONSTRAINTS + UNBOUND_RESERVATION_CONSTRAINTS
    )
    assert record.status == CAPITAL_ADMISSION_ABSTAINED
    assert record.reason == CAPITAL_ADMISSION_CONSTRAINT_UNBOUND
    assert record.bound_constraints == BOUND_RESERVATION_CONSTRAINTS
    assert record.unbound_constraints == UNBOUND_RESERVATION_CONSTRAINTS
    assert record.policy_binding_complete is False
    assert record.reservation_committed is False
    assert record.canonical_status is None
    assert record.canonical_disposition is None
    assert record.reservation_id is None
    assert record.paper_trade_id is None
    assert record.expected_portfolio_version is None
    assert record.active_reservations is None

    assert client.calls == []
    assert _store_fingerprint(writer) == fingerprint
    assert _book(writer)[3] == 0


def test_ac_006_an_unresolved_policy_abstains_before_reading_or_writing(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-006: unresolved is never unlimited.

    GIVEN a capital policy version the frozen registry does not resolve and no
    caller-supplied portfolio version
    WHEN the seam attempts the reservation
    THEN it abstains with CAPITAL_POLICY_VERSION_UNRESOLVED without even reading
    the canonical book, so an unresolved policy can never be treated as no limits
    """

    context_id = _seed_context(writer)
    client = RecordingClient()
    fingerprint = _store_fingerprint(writer)

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:policy",
        context_id=context_id,
        policy_version="paper-capital-v9-unknown",
        expected_version=None,
    )

    assert record.status == CAPITAL_ADMISSION_ABSTAINED
    assert record.reason == CAPITAL_ADMISSION_POLICY_UNRESOLVED
    assert "does not resolve" in record.detail
    assert record.policy_binding_complete is False
    assert record.reservation_committed is False
    assert record.canonical_status is None
    assert record.expected_portfolio_version is None
    assert client.calls == []
    assert _store_fingerprint(writer) == fingerprint


def test_ac_006_local_refusals_write_nothing_and_touch_no_client(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-006: local first, canonical second.

    GIVEN a snapshot of another environment, a snapshot of an unsupported quote
    currency, a plan that is not the plan of the book, and a blank disposition
    identity, each with a client that records every call
    WHEN the seam attempts each of them
    THEN each is refused with its own deterministic reason before any canonical
    surface is touched, and the canonical store is byte-identical
    """

    context_id = _seed_context(writer)
    fingerprint = _store_fingerprint(writer)
    book = _book(writer)

    environment = _admit(
        RecordingClient(),
        disposition_id="DISP:R5E2I:env",
        context_id=context_id,
        snapshot=capital_snapshot(environment=CapitalEnvironment.SHADOW_EVIDENCE),
    )
    assert environment.status == CAPITAL_ADMISSION_REFUSED
    assert environment.reason == CAPITAL_ADMISSION_ENVIRONMENT_REFUSED
    assert environment.reservation_committed is False
    assert environment.canonical_status is None

    currency = _admit(
        RecordingClient(),
        disposition_id="DISP:R5E2I:currency",
        context_id=context_id,
        snapshot=capital_snapshot(currency="EUR"),
    )
    assert currency.status == CAPITAL_ADMISSION_REFUSED
    assert currency.reason == CAPITAL_ADMISSION_CURRENCY_REFUSED
    assert currency.canonical_status is None

    stale_plan = _admit(
        RecordingClient(),
        disposition_id="DISP:R5E2I:plan",
        context_id=context_id,
        plan=reservation_plan(version="pv-r5e2i-9999"),
    )
    assert stale_plan.status == CAPITAL_ADMISSION_REFUSED
    assert stale_plan.reason == CAPITAL_ADMISSION_PLAN_STALE
    assert stale_plan.canonical_status is None
    # The plan's own book version is reported, so the mismatch is visible.
    assert stale_plan.plan_portfolio_version == "pv-r5e2i-9999"

    blank_identity = _admit(
        RecordingClient(),
        disposition_id="   ",
        context_id=context_id,
    )
    assert blank_identity.status == CAPITAL_ADMISSION_REFUSED
    assert blank_identity.reason == CAPITAL_ADMISSION_INPUT_INVALID
    assert blank_identity.canonical_status is None

    for record in (environment, currency, stale_plan, blank_identity):
        assert record.canonical_disposition is None
        assert record.reservation_id is None
        assert record.paper_trade_id is None
        assert record.reservation_committed is False
        assert record.policy_binding_complete is False

    assert _store_fingerprint(writer) == fingerprint
    assert _book(writer) == book


# ---------------------------------------------------------------------------
# AC-007 A lost acknowledgement is indeterminate and reserves once
# ---------------------------------------------------------------------------


def test_ac_007_a_lost_acknowledgement_is_indeterminate_and_reserves_once(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-007: never assume an outcome.

    GIVEN a real writer whose acknowledgement is lost after the commit
    WHEN the seam attempts the reservation and then re-attempts it honestly
    THEN the first attempt is INDETERMINATE with no claimed outcome while the
    writer did commit exactly once, and the honest re-attempt is a replay rather
    than a second reservation
    """

    context_id = _seed_context(writer)
    lost = LostAckClient(client)

    record = _admit(
        lost,
        disposition_id="DISP:R5E2I:lost",
        context_id=context_id,
        expected_version=0,
    )

    assert record.status == CAPITAL_ADMISSION_INDETERMINATE
    assert record.reason == CAPITAL_ADMISSION_CANONICAL_UNKNOWN
    assert "did not" in record.detail and "nothing is assumed" in record.detail
    assert record.expected_portfolio_version == 0
    assert record.disposition_id == "DISP:R5E2I:lost"
    assert record.disposition_time == STAMPED
    assert record.canonical_status is None
    assert record.canonical_disposition is None
    assert record.reservation_id is None
    assert record.paper_trade_id is None
    assert record.reservation_committed is False
    assert record.active_reservations is None

    # The writer committed before the acknowledgement was lost: exactly once.
    assert lost.commits == 1
    assert lost.last_ack.status == "OK"
    assert lost.last_ack.disposition == "ADMITTED"
    status, version, reserved, active, rows = _book(writer)
    assert (status, version, reserved, active) == ("OK", 1, AMOUNT, 1)
    assert rows[0][0] == lost.last_ack.reservation_id

    # The honest re-attempt reuses the frozen identity and replays.
    retry = _admit(
        client,
        disposition_id="DISP:R5E2I:lost",
        context_id=context_id,
        expected_version=record.expected_portfolio_version,
    )
    assert retry.status == CAPITAL_ADMISSION_ADMITTED
    assert retry.replay is True
    assert retry.canonical_status == "DUPLICATE_OK"
    assert retry.reservation_id == lost.last_ack.reservation_id
    assert _book(writer) == (status, version, reserved, active, rows)


def test_ac_007_a_retry_must_reuse_the_recorded_version_not_a_fresh_one(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-007: the retry input is frozen.

    GIVEN an indeterminate attempt whose reservation did commit
    WHEN a re-attempt substitutes the book's fresh portfolio version instead of
    the version the indeterminate record echoed
    THEN the writer refuses it as an identity conflict and reserves nothing,
    while the recorded version replays as the same single reservation
    """

    context_id = _seed_context(writer)
    lost = LostAckClient(client)
    indeterminate = _admit(
        lost,
        disposition_id="DISP:R5E2I:lost",
        context_id=context_id,
        expected_version=0,
    )
    assert indeterminate.status == CAPITAL_ADMISSION_INDETERMINATE
    book = _book(writer)
    assert (book[1], book[3]) == (1, 1)

    substituted = _admit(
        client,
        disposition_id="DISP:R5E2I:lost",
        context_id=context_id,
        expected_version=1,
    )
    assert substituted.status == CAPITAL_ADMISSION_REFUSED
    assert substituted.canonical_status == "REJECTED"
    assert substituted.canonical_error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert substituted.reservation_id is None
    assert substituted.reservation_committed is False
    assert _book(writer) == book

    recorded = _admit(
        client,
        disposition_id="DISP:R5E2I:lost",
        context_id=context_id,
        expected_version=0,
    )
    assert recorded.status == CAPITAL_ADMISSION_ADMITTED
    assert recorded.replay is True
    assert recorded.canonical_status == "DUPLICATE_OK"
    assert recorded.reservation_id == lost.last_ack.reservation_id
    assert _book(writer) == book


def test_ac_007_an_unsettled_acknowledgement_is_never_reported_as_a_refusal(
    writer, client, server
):
    """ATDD-R5-E-capital-admission-integration/AC-007: unsettled is not refused.

    GIVEN a canonical server whose worker is unhealthy, so its own admission reply
    is the fail-closed RETRYABLE/WORKER_UNHEALTHY acknowledgement
    WHEN the seam attempts the reservation
    THEN the outcome is INDETERMINATE with the canonical tokens surfaced verbatim
    and nothing assumed, not a refusal and not an admission, and nothing was
    reserved
    """

    context_id = _seed_context(writer)
    fingerprint = _store_fingerprint(writer)
    server.mark_worker_unhealthy_for_tests()

    record = _admit(
        client,
        disposition_id="DISP:R5E2I:unsettled",
        context_id=context_id,
        expected_version=0,
    )

    assert record.status == CAPITAL_ADMISSION_INDETERMINATE
    assert record.reason == CAPITAL_ADMISSION_ACK_UNUSABLE
    assert record.canonical_status == "RETRYABLE"
    assert record.canonical_error_code == "WORKER_UNHEALTHY"
    assert record.canonical_disposition is None
    assert record.canonical_portfolio_version is None
    assert record.reservation_id is None
    assert record.paper_trade_id is None
    assert record.reservation_committed is False
    assert record.active_reservations is None
    assert record.reserved_capital is None
    assert record.expected_portfolio_version == 0
    assert _store_fingerprint(writer) == fingerprint
    assert _book(writer)[3] == 0


# ---------------------------------------------------------------------------
# AC-008 A restart replays the same reservation once
# ---------------------------------------------------------------------------


def test_ac_008_a_restart_replays_the_same_reservation_once(tmp_path):
    """ATDD-R5-E-capital-admission-integration/AC-008: durable, not process-scoped.

    GIVEN an admitted reservation and a canonical store that is closed and reopened
    WHEN the same disposition identity is re-attempted against the reopened store
    THEN the commit survived with the same identity and amount, and the re-attempt
    is a replay instead of a second reservation
    """

    path = tmp_path / "canonical.sqlite3"
    first = CanonicalWriterServer(
        db_path=path, socket_path=tmp_path / "canonical-first.sock"
    )
    try:
        context_id = _seed_context(first.writer)
        committed = _admit(
            InProcessWriterClient(first),
            disposition_id="DISP:R5E2I:restart",
            context_id=context_id,
            expected_version=0,
        )
        assert committed.status == CAPITAL_ADMISSION_ADMITTED
        before = _book(first.writer)
    finally:
        first.stop()

    reopened = CanonicalWriterServer(
        db_path=path, socket_path=tmp_path / "canonical-second.sock"
    )
    try:
        status, version, reserved, active, rows = _book(reopened.writer)
        assert (status, version, reserved, active) == ("OK", 1, AMOUNT, 1)
        assert rows[0][0] == committed.reservation_id
        assert (rows[0][3], rows[0][4]) == ("ACTIVE", AMOUNT)

        replay = _admit(
            InProcessWriterClient(reopened),
            disposition_id="DISP:R5E2I:restart",
            context_id=context_id,
            expected_version=0,
        )
        assert replay.status == CAPITAL_ADMISSION_ADMITTED
        assert replay.replay is True
        assert replay.canonical_status == "DUPLICATE_OK"
        assert replay.reservation_id == committed.reservation_id
        assert _book(reopened.writer) == before
    finally:
        reopened.stop()


# ---------------------------------------------------------------------------
# AC-009 A reservation without terminal evidence stays active
# ---------------------------------------------------------------------------


def test_ac_009_a_reservation_without_terminal_evidence_stays_active(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-009: release needs evidence.

    GIVEN a reservation committed through the seam whose trade has no committed
    terminal reconciliation evidence
    WHEN the canonical release projection, the ledger and the exposure read are
    inspected
    THEN the reservation is still ACTIVE with no release basis, the seam's own
    read back counts it once, and nothing can free the reserved capital
    """

    context_id = _seed_context(writer)
    record = _admit(client, disposition_id="DISP:R5E2I:open", context_id=context_id)
    assert record.status == CAPITAL_ADMISSION_ADMITTED

    ledger = writer.paper_reservation_ledger(CAP_CURRENCY)
    assert len(ledger.reservations) == 1
    row = ledger.reservations[0]
    assert row.state == "ACTIVE"
    assert row.is_active is True
    assert row.release_basis is None
    assert row.amount == AMOUNT

    # Release is derived from committed terminal reconciliation evidence only.
    assert writer._releasable_paper_trades(CAP_CURRENCY) == {}  # noqa: SLF001
    assert writer._verified_paper_trade_ids(CAP_CURRENCY) == set()  # noqa: SLF001
    exposures = writer.paper_v2_active_exposures()
    assert exposures.status == "OK"
    assert exposures.exposures == []

    # The book, the ledger and the seam's read back all count it exactly once.
    assert (ledger.reserved_capital, ledger.active_reservations) == (AMOUNT, 1)
    assert (record.reserved_capital, record.active_reservations) == (AMOUNT, 1)
    assert (record.reservation_id, record.paper_trade_id) == (
        row.reservation_id,
        row.paper_trade_id,
    )
    assert record.reservation_committed is True


# ---------------------------------------------------------------------------
# AC-010 The seam is dormant and grants no authority
# ---------------------------------------------------------------------------


def test_ac_010_the_seam_is_dormant_and_grants_no_authority(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-010: reuse, never widen.

    GIVEN the seam module and the canonical schema
    WHEN both are inspected
    THEN the seam adds no table, no event type and no writer intent kind, reads no
    environment variable and no secret and imports no exchange or order surface,
    no runtime module imports it, and no record can grant execution authority
    """

    source = module_source(SEAM_MODULE)
    assert "CREATE TABLE" not in source, "the seam adds no table"
    assert "class WriterIntent" not in source, "no new writer intent kind exists"
    assert "os.environ" not in source and "getenv" not in source
    for forbidden in (
        "kraken",
        "ccxt",
        "place_order",
        "cancel_order",
        "api_key",
        "private_key",
    ):
        assert forbidden not in source.lower(), forbidden

    # Dormant: no runtime module imports the seam at all.
    importers = [
        path.relative_to(APP_ROOT).as_posix()
        for path in (APP_ROOT / "app").rglob("*.py")
        if "capital_admission_integration" in path.read_text(encoding="utf-8")
    ]
    assert importers == []

    # The outcome vocabulary is closed and each outcome is its own disposition.
    assert CAPITAL_ADMISSION_STATUSES == {
        CAPITAL_ADMISSION_ADMITTED,
        CAPITAL_ADMISSION_ABSTAINED,
        CAPITAL_ADMISSION_REFUSED,
        CAPITAL_ADMISSION_INDETERMINATE,
    }

    # No record can be built that grants authority, by either route.
    manual = CapitalAdmissionDisposition(
        status=CAPITAL_ADMISSION_ADMITTED,
        reason=CAPITAL_ADMISSION_CANONICAL_ACCEPTED,
        detail="constructed directly to prove the record is inert",
        disposition_id="DISP:R5E2I:manual",
    )
    assert manual.grants_execution_authority is False
    with pytest.raises(ValueError):
        replace(manual, grants_execution_authority=True)
    with pytest.raises(ValueError):
        CapitalAdmissionDisposition(
            status="NOT_AN_OUTCOME",
            reason="X",
            detail="Y",
            disposition_id="DISP:R5E2I:manual",
        )

    # And the seam's own committed record grants none either.
    context_id = _seed_context(writer)
    record = _admit(
        client, disposition_id="DISP:R5E2I:authority", context_id=context_id
    )
    assert record.status == CAPITAL_ADMISSION_ADMITTED
    assert record.grants_execution_authority is False
    assert record.to_dict()["grants_execution_authority"] is False


def test_ac_010_the_increment_contract_and_pointer_are_self_consistent():
    """ATDD-R5-E-capital-admission-integration/AC-010: the contract traces itself.

    GIVEN this increment, its scope contract and the active pointer
    WHEN the contract is parsed and checked against the acceptance index
    THEN the pointer resolves to an existing scope contract, every criterion is
    traced to a matching acceptance test and mapped to a changed file, no
    unapproved scope change is declared, and this module pins no global pointer
    """

    contracts = load_scope_contracts(APP_ROOT / "docs" / "atdd" / "scope-contracts")
    ours = [contract for contract in contracts if contract.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert CONTRACT.name == f"{INCREMENT}.md"
    assert contract.sections["INCREMENT"] == INCREMENT
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert list(contract.acceptance_criteria) == [
        f"AC-{index:03d}" for index in range(1, 14)
    ]

    pointer_name = read_increment_pointer(POINTER)
    assert pointer_name in {item.increment for item in contracts}
    assert (
        APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{pointer_name}.md"
    ).is_file()

    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id
    assert set(contract.sections) == {
        "INCREMENT",
        "OWNER-APPROVED INTENT",
        "ARCHITECTURE REFERENCES",
        "APPROVED ACCEPTANCE CRITERIA",
        "EXPLICITLY OUT OF SCOPE",
        "FROZEN BOUNDARIES",
        "ACCEPTANCE TEST TRACEABILITY",
        "IMPLEMENTATION MAP",
        "DEFERRED DISCOVERIES",
        "UNAPPROVED SCOPE CHANGES",
    }

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons
    assert global_pointer_pin_violations(module_source(Path(__file__))) == []


def test_ac_010_the_release_profile_and_posture_are_unchanged(writer, client):
    """ATDD-R5-E-capital-admission-integration/AC-010: the posture is preserved.

    GIVEN the repository release-profile contract and the Compose literals
    WHEN they are inspected
    THEN EVIDENCE_SHADOW remains the active shadow profile with TARGET_PAPER
    blocked, paper-v2 stays off and this increment adds no mode key, while the
    canonical reservation surfaces still declare themselves non-authoritative
    """

    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert validate_profile_contract("EVIDENCE_SHADOW") == (True, [])
    valid, reasons = validate_profile_contract("TARGET_PAPER")
    assert valid is False
    assert reasons and "blocked" in reasons[0]

    allowed = profiles["EVIDENCE_SHADOW"]["allowed_modes"]
    assert allowed["OPIP_FEATURE_BUS_MODE"] == "shadow"
    assert allowed["OPIP_CANONICAL_WRITER_MODE"] == "shadow"
    assert allowed["OPIP_TARGET_SPINE_MODE"] == "shadow"
    assert allowed["OPIP_PAPER_V2_MODE"] == "off"
    # The seam adds no mode key at all: it is not activated by configuration.
    assert not any(key.startswith("OPIP_R5") for key in allowed)

    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"

    ledger = writer.paper_reservation_ledger(CAP_CURRENCY)
    assert ledger.grants_authority is False
    assert ledger.policy_binding_complete is False
    assert ledger.unbound_constraints == UNBOUND_RESERVATION_CONSTRAINTS


def test_ac_010_the_ratified_consumer_and_caller_lists_name_this_seam():
    """ATDD-R5-E-capital-admission-integration/AC-010: ratified, never smuggled.

    GIVEN the guards that own the R5-C capital-record isolation and the F11
    target-domain mutating-authority list
    WHEN each is inspected alongside this seam
    THEN each names this seam explicitly as a declared reader, consumer or caller
    instead of being weakened or silenced, and the seam itself holds exactly one
    canonical admission call site and no protected-action call at all
    """

    from tests.test_opip_f11_precutover_protection import (
        DECLARED_TARGET_MUTATOR_CALLERS,
    )
    from tests.test_opip_r5_c_capital_intelligence import (
        DECLARED_RECORD_CONSUMERS,
        DECLARED_SNAPSHOT_READERS,
    )

    seam = "app/services/capital_admission_integration.py"
    assert seam in DECLARED_TARGET_MUTATOR_CALLERS
    assert DECLARED_SNAPSHOT_READERS == {seam}
    assert DECLARED_RECORD_CONSUMERS == {seam}
    for declared in (
        *DECLARED_TARGET_MUTATOR_CALLERS,
        *DECLARED_SNAPSHOT_READERS,
        *DECLARED_RECORD_CONSUMERS,
    ):
        assert (APP_ROOT / declared).is_file(), declared

    # The seam calls exactly one canonical mutator, once, and never the other one.
    tree = ast.parse(module_source(SEAM_MODULE))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    attributes = {node.func.attr for node in calls}
    assert "admit_paper_opportunity" in attributes
    assert "trigger_paper_protection_action" not in attributes
    assert len([node for node in calls if node.func.attr == "admit_paper_opportunity"]) == 1

    # And the ratified reader must not declare a second book of its own.
    source = module_source(SEAM_MODULE)
    assert "class CapitalSnapshot" not in source
    assert "CashPartitionLedger" not in source














@pytest.mark.parametrize(
    ("offset", "short_window", "expected_reason"),
    [
        (timedelta(seconds=-1), False, _seam.CAPITAL_ADMISSION_INPUT_INVALID),
        (timedelta(seconds=300), True, _seam.CAPITAL_ADMISSION_RESERVATION_INACTIVE),
        (timedelta(seconds=301), True, _seam.CAPITAL_ADMISSION_RESERVATION_INACTIVE),
    ],
)
def test_a1_admission_refuses_outside_the_plan_reservation_window(
    offset, short_window, expected_reason
):
    """ATDD-R5-E-capital-admission-integration/AC-013: invalid timing refuses before any canonical read or write."""

    recorder = RecordingClient()
    result = _admit(
        recorder,
        disposition_id="a1-outside",
        context_id="ctx-a1",
        plan=_short_window_plan() if short_window else reservation_plan(),
        snapshot=capital_snapshot(),
        disposition_time=T0 + offset,
    )
    assert result.status == _seam.CAPITAL_ADMISSION_REFUSED, (result.reason, result.detail)
    assert result.reason == expected_reason, (result.reason, result.detail)
    assert recorder.calls == []


def test_a1_admission_still_admits_inside_the_window(client):
    """ATDD-R5-E-capital-admission-integration/AC-013: the window guard must not disturb a legitimate in-window admission."""

    result = _admit(
        client,
        disposition_id="a1-inside",
        context_id="ctx-a1-inside",
        disposition_time=T0 + timedelta(seconds=30),
    )
    assert result.reason not in (
        _seam.CAPITAL_ADMISSION_INPUT_INVALID,
        _seam.CAPITAL_ADMISSION_RESERVATION_INACTIVE,
    ), (result.reason, result.detail)
