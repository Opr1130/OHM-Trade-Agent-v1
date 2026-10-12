"""ATDD acceptance tests for the R5-E canonical capital reservations slice.

Increment: `ATDD-R5-E-canonical-reservations`. Every test cites its criterion in
the docstring as `INCREMENT/AC-NNN`, and the module is marked
`pytest.mark.acceptance`, so both the scope checker and `pytest -m acceptance`
read the same citations.

The increment makes the durable paper capital reservation owned by exactly one
canonical writer, replayable, single-release and fail-closed. It adds no write
path: reservations are still created only by the existing atomic admission RPC
and released only by the existing committed terminal-reconciliation evidence.
What these tests prove is the reservation lifecycle the admission path had no
projection for, and the abstention rule for constraints the frozen capital policy
cannot resolve.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip.canonical.models import (  # noqa: E402
    BOUND_RESERVATION_CONSTRAINTS,
    RESERVATION_STATES,
    UNBOUND_RESERVATION_CONSTRAINTS,
)
from app.opip.canonical.paths import SCHEMA_VERSION  # noqa: E402
from app.opip.canonical.schema import CanonicalStoreBusyError  # noqa: E402
from app.opip.canonical.writer import CanonicalWriter  # noqa: E402
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_OPPORTUNITY_DISPOSITION_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (  # noqa: E402
    PAPER_CAPITAL_POLICY_VERSION,
    PaperAdmissionRequest,
)
from app.opip.contracts.serialization import iso_z  # noqa: E402
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

INCREMENT = "ATDD-R5-E-canonical-reservations"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
WRITER_MODULE = APP_ROOT / "app" / "opip" / "canonical" / "writer.py"

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def writer(tmp_path) -> CanonicalWriter:
    instance = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _admission(
    *,
    disposition_id: str,
    decision_context_id: str,
    quote_currency: str = "USD",
    expected_version: int = 0,
    reservation_amount: float = 500.0,
    capital_policy_version: str = PAPER_CAPITAL_POLICY_VERSION,
) -> PaperAdmissionRequest:
    return PaperAdmissionRequest(
        disposition_id=disposition_id,
        decision_context_id=decision_context_id,
        disposition_seq=0,
        quote_currency=quote_currency,
        requested_capital=reservation_amount,
        disposition_time={
            "precision": "EXACT",
            "basis": "SOURCE_REPORTED",
            "occurred_at": iso_z(NOW, field_name="disposition_time"),
        },
        expected_portfolio_version=expected_version,
        capital_policy_version=capital_policy_version,
        portfolio_equity_limit=10_000.0,
        portfolio_position_limit=3,
        requested_reservation_amount=reservation_amount,
        direction="LONG",
    )


def _seed_context(writer: CanonicalWriter) -> str:
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
        candidate_id="candidate-r5e2",
        episode_id="episode-r5e2",
        instrument_version_id="INSTR:kraken:SOL:USD:1",
        instrument_registration_event_id="EVT:seed-proof",
        snapshot_record_event_id="EVT:seed-snapshot-proof",
        snapshot_id="snapshot-r5e2",
        snapshot_hash="snapshot-hash-r5e2",
        evaluation_time=NOW,
        evidence_cutoff=NOW,
        policy_version=GATE_POLICY_VERSION,
        policy_fingerprint=gate_policy_fingerprint(),
        producing_component="r5e2-test",
        artifact_or_build_id="build-r5e2",
        process_instance_id="proc-r5e2",
        emitted_at=NOW,
        source_record_refs=("source:r5e2",),
    )
    payload = build_decision_context_payload(facts)
    submit_decision_context(payload, client=writer)
    return str(payload["context_id"])


def _snapshot(writer: CanonicalWriter) -> dict:
    """Canonical state a read or a refusal must not mutate."""
    events = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]
    idempotency_rows = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM idempotency_keys"
    ).fetchone()[0]
    tables = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    meta = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT schema_version, history_epoch, next_local_sequence FROM meta WHERE id = 1"
    ).fetchone()
    watermarks = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT stream, history_epoch, local_sequence FROM watermarks ORDER BY stream"
    ).fetchall()
    return {
        "events": int(events),
        "idempotency_rows": int(idempotency_rows),
        "tables": [str(row["name"]) for row in tables],
        "schema_version": int(meta["schema_version"]),
        "history_epoch": int(meta["history_epoch"]),
        "next_local_sequence": int(meta["next_local_sequence"]),
        "watermarks": [
            (str(row["stream"]), int(row["history_epoch"]), int(row["local_sequence"]))
            for row in watermarks
        ],
    }


def _reservation_state(writer: CanonicalWriter, quote_currency: str = "USD") -> tuple:
    """The capital-bearing state a refusal must not alter."""
    state = writer.paper_portfolio_state(quote_currency)
    ledger = writer.paper_reservation_ledger(quote_currency)
    return (
        state.portfolio_version,
        state.reserved_capital,
        state.active_reservations,
        ledger.portfolio_version,
        ledger.reserved_capital,
        ledger.active_reservations,
        tuple(
            (record.reservation_id, record.state) for record in ledger.reservations
        ),
    )


def _last_disposition_payload(writer: CanonicalWriter) -> dict:
    """The most recently committed disposition payload, for its reason code."""
    row = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        """
        SELECT payload_json FROM events
        WHERE event_type = ?
        ORDER BY history_epoch DESC, local_sequence DESC
        LIMIT 1
        """,
        (PAPER_OPPORTUNITY_DISPOSITION_RECORDED,),
    ).fetchone()
    return json.loads(str(row["payload_json"]))


# ---------------------------------------------------------------------------
# AC-001 A reservation commits exactly once and replays canonically
# ---------------------------------------------------------------------------


def test_ac_001_a_valid_reservation_commits_exactly_once(writer):
    """ATDD-R5-E-canonical-reservations/AC-001: one reservation, one debit.

    GIVEN one eligible paper decision context and one admission identity
    WHEN a valid reservation is admitted
    THEN exactly one reservation exists, it is ACTIVE, it binds the canonical
    disposition identity and amount, and the existing portfolio projection
    reports the same reserved capital and version
    """

    context_id = _seed_context(writer)
    ack = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-once",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    assert ack.status == "OK"
    assert ack.disposition == "ADMITTED"
    assert ack.reservation_id is not None
    assert ack.paper_trade_id is not None

    ledger = writer.paper_reservation_ledger("USD")
    assert ledger.status == "OK"
    assert len(ledger.reservations) == 1
    record = ledger.reservations[0]
    assert record.reservation_id == ack.reservation_id
    assert record.paper_trade_id == ack.paper_trade_id
    assert record.state == "ACTIVE"
    assert record.release_basis is None
    assert record.amount == 500.0
    assert record.quote_currency == "USD"
    assert record.portfolio_version == 0

    state = writer.paper_portfolio_state("USD")
    assert state.status == "OK"
    assert state.reserved_capital == ledger.reserved_capital == 500.0
    assert state.active_reservations == ledger.active_reservations == 1
    assert state.portfolio_version == ledger.portfolio_version == 1


def test_ac_001_a_retry_with_the_same_identity_returns_the_canonical_result(writer):
    """ATDD-R5-E-canonical-reservations/AC-001: the retry debits nothing twice.

    GIVEN a reservation that is already committed
    WHEN the identical admission is retried with the same identity
    THEN the canonical result is returned, no second reservation or debit is
    created, the version does not advance a second time and the ledger still
    holds exactly one reservation for that identity
    """

    context_id = _seed_context(writer)
    request = _admission(
        disposition_id="DISP:r5e2-retry",
        decision_context_id=context_id,
        reservation_amount=500.0,
    )
    first = writer.admit_paper_opportunity(request)
    assert first.status == "OK"

    retry = writer.admit_paper_opportunity(request)
    assert retry.status == "DUPLICATE_OK"
    assert retry.disposition == first.disposition
    assert retry.reservation_id == first.reservation_id
    assert retry.paper_trade_id == first.paper_trade_id
    assert retry.portfolio_version == first.portfolio_version

    ledger = writer.paper_reservation_ledger("USD")
    assert len(ledger.reservations) == 1
    assert ledger.reserved_capital == 500.0
    assert ledger.active_reservations == 1
    assert ledger.portfolio_version == 1


# ---------------------------------------------------------------------------
# AC-002 An idempotency conflict fails closed and writes nothing
# ---------------------------------------------------------------------------


def test_ac_002_the_same_key_with_a_different_payload_is_rejected(writer):
    """ATDD-R5-E-canonical-reservations/AC-002: identity binds one payload.

    GIVEN a reservation identity that is already committed with one amount
    WHEN the same identity is presented with a different economic payload
    THEN the attempt is rejected with `IDEMPOTENCY_PAYLOAD_CONFLICT` and the
    committed reservation keeps its original amount and identity
    """

    context_id = _seed_context(writer)
    first = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-conflict",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    assert first.status == "OK"

    conflict = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-conflict",
            decision_context_id=context_id,
            reservation_amount=750.0,
        )
    )
    assert conflict.status == "REJECTED"
    assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
    assert conflict.reservation_id is None

    ledger = writer.paper_reservation_ledger("USD")
    assert len(ledger.reservations) == 1
    assert ledger.reservations[0].amount == 500.0
    assert ledger.reservations[0].reservation_id == first.reservation_id


def test_ac_002_a_rejected_conflict_writes_nothing(writer):
    """ATDD-R5-E-canonical-reservations/AC-002: the conflict is a pure refusal.

    GIVEN a committed reservation and its canonical state
    WHEN the conflicting payload is rejected
    THEN not a single canonical row, idempotency key, sequence, watermark or
    history epoch changes, and the reservation state is identical
    """

    context_id = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-conflict-pure",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    before = _snapshot(writer)
    state_before = _reservation_state(writer)

    rejected = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-conflict-pure",
            decision_context_id=context_id,
            reservation_amount=900.0,
        )
    )
    assert rejected.status == "REJECTED"

    assert _snapshot(writer) == before
    assert _reservation_state(writer) == state_before


# ---------------------------------------------------------------------------
# AC-003 One writer owns the store, and a refusal mutates no capital
# ---------------------------------------------------------------------------


def test_ac_003_a_second_writer_cannot_own_the_store(tmp_path):
    """ATDD-R5-E-canonical-reservations/AC-003: exclusivity is store-level.

    GIVEN a canonical store with a live writer
    WHEN a second writer opens the same store
    THEN it is refused with `CanonicalStoreBusyError`, the incumbent still serves
    reads, and after a clean handoff exactly one writer owns the store again
    """

    path = tmp_path / "canonical.sqlite3"
    first = CanonicalWriter(path)
    try:
        with pytest.raises(CanonicalStoreBusyError):
            CanonicalWriter(path)
        assert first.paper_reservation_ledger("USD").status == "OK"
    finally:
        first.close()

    handoff = CanonicalWriter(path)
    try:
        ledger = handoff.paper_reservation_ledger("USD")
        assert ledger.status == "OK"
        assert ledger.reservations == ()
        assert ledger.reserved_capital == 0.0
    finally:
        handoff.close()


def test_ac_003_a_stale_portfolio_version_is_refused_without_a_partial_write(writer):
    """ATDD-R5-E-canonical-reservations/AC-003: a superseded plan cannot reserve.

    GIVEN a reservation that already advanced the portfolio version
    WHEN a competing plan reserves against the superseded version
    THEN it is refused with `STALE_PORTFOLIO_VERSION`, no reservation is created,
    the reserved capital, the active count and the version are unchanged, and the
    only committed consequence is the canonical admission-request evidence
    """

    context_id = _seed_context(writer)
    first = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-stale-a",
            decision_context_id=context_id,
            expected_version=0,
            reservation_amount=500.0,
        )
    )
    assert first.status == "OK"
    state_before = _reservation_state(writer)

    stale = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-stale-b",
            decision_context_id=context_id,
            expected_version=0,
            reservation_amount=500.0,
        )
    )
    assert stale.status == "REJECTED"
    assert stale.error_code == "STALE_PORTFOLIO_VERSION"
    assert stale.portfolio_version == 1

    assert _reservation_state(writer) == state_before
    assert _last_disposition_payload(writer)["disposition"] == "ADMITTED"
    ledger = writer.paper_reservation_ledger("USD")
    assert len(ledger.reservations) == 1
    assert ledger.reserved_capital == 500.0
    assert ledger.portfolio_version == 1


def test_ac_003_an_unknown_reservation_identity_resolves_to_nothing(writer):
    """ATDD-R5-E-canonical-reservations/AC-003: identity is never guessed.

    GIVEN a reservation committed in one lineage and an invented identity
    WHEN the ledger and capacity projections are queried
    THEN neither a foreign nor an invented identity resolves to a reservation, no
    capacity is attributed to it, and every reported state is inside the closed
    vocabulary
    """

    context_id = _seed_context(writer)
    ack = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-usdt-only",
            decision_context_id=context_id,
            quote_currency="USDT",
            reservation_amount=700.0,
        )
    )
    assert ack.status == "OK"
    foreign_reservation = str(ack.reservation_id)

    usd_ledger = writer.paper_reservation_ledger("USD")
    assert usd_ledger.status == "OK"
    assert usd_ledger.reservations == ()
    assert usd_ledger.reserved_capital == 0.0
    assert not any(
        record.reservation_id == foreign_reservation
        for record in usd_ledger.reservations
    )

    invented = "RSV2:" + "0" * 64
    usdt_ledger = writer.paper_reservation_ledger("USDT")
    assert {record.reservation_id for record in usdt_ledger.reservations} == {
        foreign_reservation
    }
    assert invented not in {
        record.reservation_id for record in usdt_ledger.reservations
    }
    assert all(
        record.state in RESERVATION_STATES for record in usdt_ledger.reservations
    )
    assert writer.paper_reservation_capacity("USD", constraint="EQUITY").status == "OK"


# ---------------------------------------------------------------------------
# AC-004 The capital and capacity bounds bind at the atomic decision
# ---------------------------------------------------------------------------


def test_ac_004_a_reservation_beyond_the_equity_limit_is_refused_atomically(writer):
    """ATDD-R5-E-canonical-reservations/AC-004: free cash alone is not deployable.

    GIVEN 6,000 already reserved against a 10,000 equity limit
    WHEN a further 5,000 reservation is attempted
    THEN the atomic decision refuses it with the capital-limit reason code, no
    reservation is created, the version does not advance, and the ledger still
    reconciles to the committed 6,000
    """

    context_id = _seed_context(writer)
    first = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-capital-a",
            decision_context_id=context_id,
            expected_version=0,
            reservation_amount=6_000.0,
        )
    )
    assert first.status == "OK"
    assert first.disposition == "ADMITTED"
    state_before = _reservation_state(writer)

    refused = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-capital-b",
            decision_context_id=context_id,
            expected_version=1,
            reservation_amount=5_000.0,
        )
    )
    assert refused.status == "OK"
    assert refused.disposition == "CAPITAL_REJECTED"
    assert refused.reservation_id is None
    assert refused.paper_trade_id is None

    payload = _last_disposition_payload(writer)
    assert payload["disposition"] == "CAPITAL_REJECTED"
    assert payload["reason_code"] == "PORTFOLIO_CAPITAL_LIMIT"

    assert _reservation_state(writer) == state_before
    ledger = writer.paper_reservation_ledger("USD")
    assert len(ledger.reservations) == 1
    assert ledger.reserved_capital == 6_000.0
    assert ledger.portfolio_version == 1
    capacity = writer.paper_reservation_capacity("USD", constraint="EQUITY")
    assert capacity.status == "OK"
    assert capacity.remaining_capacity == 4_000.0


def test_ac_004_a_reservation_beyond_the_position_limit_is_refused(writer):
    """ATDD-R5-E-canonical-reservations/AC-004: the position limit is binding too.

    GIVEN three active reservations against a position limit of three
    WHEN a fourth reservation is attempted with ample free cash
    THEN it is refused with the capacity-limit reason code, the active count stays
    three, and no fourth reservation is created
    """

    context_id = _seed_context(writer)
    for index in range(3):
        ack = writer.admit_paper_opportunity(
            _admission(
                disposition_id=f"DISP:r5e2-capacity-{index}",
                decision_context_id=context_id,
                expected_version=index,
                reservation_amount=500.0,
            )
        )
        assert ack.status == "OK"
        assert ack.disposition == "ADMITTED"

    refused = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-capacity-3",
            decision_context_id=context_id,
            expected_version=3,
            reservation_amount=500.0,
        )
    )
    assert refused.status == "OK"
    assert refused.disposition == "CAPACITY_REJECTED"
    assert refused.reservation_id is None

    payload = _last_disposition_payload(writer)
    assert payload["reason_code"] == "PORTFOLIO_POSITION_LIMIT"

    ledger = writer.paper_reservation_ledger("USD")
    assert len(ledger.reservations) == 3
    assert ledger.active_reservations == 3
    assert ledger.reserved_capital == 1_500.0
    assert ledger.portfolio_version == 3
    assert (
        writer.paper_reservation_capacity(
            "USD", constraint="POSITION_COUNT"
        ).remaining_capacity
        == 0.0
    )


# ---------------------------------------------------------------------------
# AC-005 An unresolved policy abstains instead of defaulting
# ---------------------------------------------------------------------------


def test_ac_005_an_unbound_constraint_abstains_instead_of_becoming_unlimited(writer):
    """ATDD-R5-E-canonical-reservations/AC-005: absence of policy is not a limit.

    GIVEN committed reservations and a frozen policy that binds only equity and
    position count
    WHEN capacity is queried for risk, concentration, liquidity or sleeve
    THEN each abstains with `POLICY_UNAVAILABLE` and reports no figure at all,
    while the equity question is still answered exactly and an unknown constraint
    name is refused
    """

    context_id = _seed_context(writer)
    admitted = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-policy-a",
            decision_context_id=context_id,
            reservation_amount=6_000.0,
        )
    )
    assert admitted.status == "OK"

    equity = writer.paper_reservation_capacity("USD", constraint="EQUITY")
    assert equity.status == "OK"
    assert equity.remaining_capacity == 4_000.0
    assert equity.binding_policy_version == PAPER_CAPITAL_POLICY_VERSION

    for name in UNBOUND_RESERVATION_CONSTRAINTS:
        answer = writer.paper_reservation_capacity("USD", constraint=name)
        assert answer.status == "ABSTAINED", name
        assert answer.error_code == "POLICY_UNAVAILABLE", name
        assert answer.constraint == name
        assert answer.remaining_capacity is None, name
        assert answer.unbound_constraints == UNBOUND_RESERVATION_CONSTRAINTS
        # The untouched free cash is never offered as this constraint's capacity.
        assert answer.remaining_capacity != equity.remaining_capacity

    unknown = writer.paper_reservation_capacity("USD", constraint="TURNOVER")
    assert unknown.status == "REJECTED"
    assert unknown.error_code == "UNKNOWN_RESERVATION_CONSTRAINT"
    assert unknown.remaining_capacity is None

    ledger = writer.paper_reservation_ledger("USD")
    assert ledger.policy_binding_complete is False
    assert ledger.bound_constraints == BOUND_RESERVATION_CONSTRAINTS
    assert ledger.unbound_constraints == UNBOUND_RESERVATION_CONSTRAINTS


def test_ac_005_an_unresolvable_policy_version_abstains_and_admission_stays_refused(
    writer,
):
    """ATDD-R5-E-canonical-reservations/AC-005: no unresolvable version is honored.

    GIVEN an unknown capital policy version
    WHEN admission is attempted with it and capacity is then queried
    THEN admission stays refused and writes nothing, the capacity answer names the
    one frozen authoritative policy version rather than the caller's, and any
    unbound constraint still abstains
    """

    context_id = _seed_context(writer)
    before = _snapshot(writer)

    ack = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-bad-policy",
            decision_context_id=context_id,
            capital_policy_version="paper-capital-v99",
        )
    )
    assert ack.status == "REJECTED"
    assert ack.error_code == "UNSUPPORTED_CAPITAL_POLICY_VERSION"
    assert ack.reservation_id is None
    assert _snapshot(writer) == before

    equity = writer.paper_reservation_capacity("USD", constraint="EQUITY")
    assert equity.status == "OK"
    assert equity.binding_policy_version == PAPER_CAPITAL_POLICY_VERSION
    assert equity.remaining_capacity == 10_000.0

    risk = writer.paper_reservation_capacity("USD", constraint="RISK")
    assert risk.status == "ABSTAINED"
    assert risk.error_code == "POLICY_UNAVAILABLE"
    assert risk.remaining_capacity is None

    ledger = writer.paper_reservation_ledger("USD")
    assert ledger.reservations == ()
    assert ledger.reserved_capital == 0.0
    assert ledger.portfolio_version == 0


# ---------------------------------------------------------------------------
# AC-006 The ledger reconciles exactly and never double counts
# ---------------------------------------------------------------------------


def test_ac_006_the_ledger_reconciles_with_the_existing_projection(writer):
    """ATDD-R5-E-canonical-reservations/AC-006: two readers, one answer.

    GIVEN two committed reservations in one currency
    WHEN the ledger and the existing portfolio projection are compared
    THEN version, reserved capital and active count agree exactly, every
    reservation appears once in canonical order, and the active amounts sum to
    the reserved capital
    """

    context_id = _seed_context(writer)
    first = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-recon-a",
            decision_context_id=context_id,
            expected_version=0,
            reservation_amount=500.0,
        )
    )
    second = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-recon-b",
            decision_context_id=context_id,
            expected_version=1,
            reservation_amount=750.0,
        )
    )
    assert first.status == "OK"
    assert second.status == "OK"

    ledger = writer.paper_reservation_ledger("USD")
    state = writer.paper_portfolio_state("USD")
    assert ledger.status == "OK"
    assert ledger.portfolio_version == state.portfolio_version == 2
    assert ledger.reserved_capital == state.reserved_capital == 1_250.0
    assert ledger.active_reservations == state.active_reservations == 2
    assert len(ledger.reservations) == ledger.portfolio_version == 2
    assert sum(
        record.amount for record in ledger.reservations if record.is_active
    ) == ledger.reserved_capital
    assert {record.reservation_id for record in ledger.reservations} == {
        str(first.reservation_id),
        str(second.reservation_id),
    }
    sequences = [record.local_sequence for record in ledger.reservations]
    assert sequences == sorted(sequences)
    assert all(record.state == "ACTIVE" for record in ledger.reservations)


def test_ac_006_two_reads_at_the_same_watermark_are_identical(writer):
    """ATDD-R5-E-canonical-reservations/AC-006: the read is point-in-time.

    GIVEN a committed reservation and its canonical watermark
    WHEN the ledger is read twice with no write in between
    THEN both reads are byte-identical at the same watermark, and a later
    reservation advances the watermark and the reserved capital together
    """

    context_id = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-watermark-a",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    first = writer.paper_reservation_ledger("USD")
    second = writer.paper_reservation_ledger("USD")
    assert first.to_dict() == second.to_dict()
    assert (first.history_epoch, first.local_sequence) == (
        second.history_epoch,
        second.local_sequence,
    )
    assert first.local_sequence is not None
    assert first.local_sequence >= 0

    writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-watermark-b",
            decision_context_id=context_id,
            expected_version=1,
            reservation_amount=250.0,
        )
    )
    third = writer.paper_reservation_ledger("USD")
    assert third.local_sequence > first.local_sequence
    assert third.reserved_capital == 750.0
    assert (
        third.reservations[-1].local_sequence > first.reservations[-1].local_sequence
    )


def test_ac_006_a_fill_that_is_not_terminal_keeps_the_reservation_active(writer):
    """ATDD-R5-E-canonical-reservations/AC-006: release needs terminal evidence.

    GIVEN an admitted reservation whose trade has no committed terminal
    reconciliation
    WHEN the release projection and the ledger are read
    THEN the reservation is still ACTIVE with no release basis, the release
    derivation is empty because no terminal evidence exists, and a reservation
    with no filled exposure is not reported as exposure
    """

    context_id = _seed_context(writer)
    ack = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-open",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    assert ack.status == "OK"
    assert ack.disposition == "ADMITTED"

    ledger = writer.paper_reservation_ledger("USD")
    record = ledger.reservations[0]
    assert record.state == "ACTIVE"
    assert record.is_active is True
    assert record.release_basis is None

    # Release is derived from committed terminal reconciliation evidence only, so
    # an admitted trade with no verified terminal state releases nothing: no
    # partial fill, attempt, trigger or expiry can free capital.
    assert writer._releasable_paper_trades("USD") == {}  # noqa: SLF001
    assert writer._verified_paper_trade_ids("USD") == set()  # noqa: SLF001

    exposures = writer.paper_v2_active_exposures()
    assert exposures.status == "OK"
    assert exposures.exposures == []
    assert ledger.reserved_capital == 500.0
    assert ledger.active_reservations == 1


# ---------------------------------------------------------------------------
# AC-007 Isolation: one lineage, one currency, no foreign authority
# ---------------------------------------------------------------------------


def test_ac_007_each_currency_reports_only_its_own_reservations(writer):
    """ATDD-R5-E-canonical-reservations/AC-007: currency stays a hard boundary.

    GIVEN reservations committed in two quote currencies
    WHEN each ledger is read
    THEN each reports only its own reservations, amounts and version, and the two
    reservation identities differ
    """

    context_id = _seed_context(writer)
    usd = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-usd",
            decision_context_id=context_id,
            quote_currency="USD",
            expected_version=0,
            reservation_amount=500.0,
        )
    )
    usdt = writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-usdt",
            decision_context_id=context_id,
            quote_currency="USDT",
            expected_version=0,
            reservation_amount=700.0,
        )
    )
    assert usd.status == "OK"
    assert usdt.status == "OK"

    usd_ledger = writer.paper_reservation_ledger("USD")
    usdt_ledger = writer.paper_reservation_ledger("USDT")
    assert {record.quote_currency for record in usd_ledger.reservations} == {"USD"}
    assert {record.quote_currency for record in usdt_ledger.reservations} == {"USDT"}
    assert usd_ledger.reserved_capital == 500.0
    assert usdt_ledger.reserved_capital == 700.0
    assert usd_ledger.portfolio_version == usdt_ledger.portfolio_version == 1
    assert usd_ledger.active_reservations == usdt_ledger.active_reservations == 1
    assert (
        usd_ledger.reservations[0].reservation_id
        != usdt_ledger.reservations[0].reservation_id
    )


def test_ac_007_an_unsupported_currency_fails_closed(writer):
    """ATDD-R5-E-canonical-reservations/AC-007: a bad currency is never a default.

    GIVEN a malformed, lower-cased and unsupported currency
    WHEN the ledger and capacity projections are queried
    THEN each is refused with the existing canonical currency error, reports no
    reservations and no capacity figure, and writes nothing at all
    """

    before = _snapshot(writer)
    for bad in ("XYZ", " usd", "usd", ""):
        ledger = writer.paper_reservation_ledger(bad)
        assert ledger.status == "REJECTED", bad
        assert ledger.error_code in {
            "MALFORMED_QUOTE_CURRENCY",
            "UNSUPPORTED_QUOTE_CURRENCY",
        }, bad
        assert ledger.reservations == ()
        assert ledger.reserved_capital is None
        assert ledger.portfolio_version is None

        answer = writer.paper_reservation_capacity(bad, constraint="EQUITY")
        assert answer.status == "REJECTED", bad
        assert answer.remaining_capacity is None, bad

    assert _snapshot(writer) == before


# ---------------------------------------------------------------------------
# AC-008 Read purity, compatibility and unchanged posture
# ---------------------------------------------------------------------------


def module_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_ac_008_the_reservation_reads_write_nothing_at_all(writer):
    """ATDD-R5-E-canonical-reservations/AC-008: the surfaces are strict reads.

    GIVEN committed reservations and their canonical state
    WHEN the reservation, portfolio, capacity and exposure projections are read
    repeatedly
    THEN no event, idempotency key, sequence, watermark, history epoch or table is
    created or advanced, and the store is byte-identical
    """

    context_id = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-read-pure",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    before = _snapshot(writer)
    for _ in range(3):
        assert writer.paper_reservation_ledger("USD").status == "OK"
        assert writer.paper_portfolio_state("USD").status == "OK"
        assert (
            writer.paper_reservation_capacity("USD", constraint="EQUITY").status
            == "OK"
        )
        assert (
            writer.paper_reservation_capacity("USD", constraint="SLEEVE").status
            == "ABSTAINED"
        )
        assert writer.paper_v2_active_exposures().status == "OK"
    assert _snapshot(writer) == before


def test_ac_008_existing_readers_and_schema_are_unchanged(writer):
    """ATDD-R5-E-canonical-reservations/AC-008: nothing existing changed shape.

    GIVEN the schema version, the existing readers and the writer module
    WHEN the increment's surfaces exist alongside them
    THEN the persisted schema version is unchanged, the existing readers keep their
    behaviour and payload, no new event type, writer intent kind or table is
    introduced, and the new records declare themselves non-authoritative
    """

    context_id = _seed_context(writer)
    writer.admit_paper_opportunity(
        _admission(
            disposition_id="DISP:r5e2-compat",
            decision_context_id=context_id,
            reservation_amount=500.0,
        )
    )
    snapshot = _snapshot(writer)
    assert snapshot["schema_version"] == SCHEMA_VERSION

    state = writer.paper_portfolio_state("USD")
    assert state.status == "OK"
    assert (state.quote_currency, state.portfolio_version) == ("USD", 1)
    assert state.reserved_capital == 500.0
    assert state.active_reservations == 1

    exposures = writer.paper_v2_active_exposures()
    assert exposures.status == "OK"
    assert exposures.exposures == []

    ledger = writer.paper_reservation_ledger("USD")
    assert ledger.grants_authority is False
    assert ledger.policy_binding_complete is False
    assert ledger.bound_constraints == BOUND_RESERVATION_CONSTRAINTS

    source = module_source(WRITER_MODULE)
    assert "CREATE TABLE" not in source, "the reservation surfaces add no table"
    assert "reservation.recorded" not in source, "no new event type is introduced"
    assert "class WriterIntent" not in source, "no new writer intent kind exists"
    assert not any(name.startswith("reservation") for name in snapshot["tables"])


def test_ac_008_the_increment_contract_and_pointer_are_self_consistent():
    """ATDD-R5-E-canonical-reservations/AC-008: the contract traces to its tests.

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

    pointer_name = read_increment_pointer(POINTER)
    assert pointer_name in {item.increment for item in contracts}

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


def test_ac_008_release_profile_and_posture_are_unchanged(writer):
    """ATDD-R5-E-canonical-reservations/AC-008: the posture is preserved.

    GIVEN the repository release-profile contract and the Compose literals
    WHEN they are inspected
    THEN `EVIDENCE_SHADOW` remains the active shadow profile with `TARGET_PAPER`
    blocked, paper-v2 stays off, this increment adds no mode key, and the
    reservation surfaces still declare themselves non-authoritative with an
    incomplete binding set
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
    assert not any(key.startswith("OPIP_R5") for key in allowed)

    compose = yaml.safe_load(
        (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    environment = compose["services"]["ohm-trade-agent"]["environment"]
    assert environment["OPIP_PAPER_V2_MODE"] == "off"
    assert environment["OPIP_FEATURE_BUS_MODE"] == "shadow"

    ledger = writer.paper_reservation_ledger("USD")
    assert ledger.grants_authority is False
    assert ledger.policy_binding_complete is False
    assert ledger.unbound_constraints == UNBOUND_RESERVATION_CONSTRAINTS
