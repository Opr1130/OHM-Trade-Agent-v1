"""R3 F4 Opportunity Lifecycle persistence: acceptance and adversarial tests.

These tests satisfy the increment ``ATDD-R3-F4-opportunity-lifecycle-persistence``
by proving that one real lifecycle transition becomes exactly one durable
canonical record through the existing single ``CanonicalWriter`` and its generic
``events``/``idempotency_keys``/``watermarks`` store, that exact replays are
idempotent, that same-key/different-payload submissions fail closed, and that a
restarted writer rehydrates and validates the persisted history without repair.

Every fixture here is a deterministic literal. The tests read no network, no
wall clock, no random value and no production data.
"""

from __future__ import annotations

import ast
import copy
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.opip import opportunity_lifecycle as lifecycle  # noqa: E402
from app.opip import opportunity_persistence as producer  # noqa: E402
from app.opip.canonical.models import WriterAck, WriterIntent  # noqa: E402
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION  # noqa: E402
from app.opip.canonical.writer import (  # noqa: E402
    ACCEPTED_EVENT_TYPES,
    IDEMPOTENT_PAYLOAD_EVENT_TYPES,
    MAX_PAYLOAD_BYTES,
    CanonicalWriter,
)
from app.opip.contracts import opportunity_persistence as persistence  # noqa: E402
from app.opip.contracts.detector import (  # noqa: E402
    IGNITION_DETECTOR_VERSION,
    IGNITION_POLICY_VERSION,
    DetectorClaim,
    DetectorContractError,
)
from app.opip.contracts.opportunity import (  # noqa: E402
    OpportunityContractError,
    OpportunityDeferral,
    OpportunityEpisode,
    OpportunityLifecycleEvent,
    OpportunityLifecyclePolicy,
    OpportunityLifecycleResult,
    OpportunityLifecycleState,
    OpportunityTerminalReason,
)

RECORDED = persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_RECORDED

# ---------------------------------------------------------------------------
# Deterministic fixtures
# ---------------------------------------------------------------------------

CUTOFF = datetime(2026, 9, 11, 15, 1, tzinfo=timezone.utc)
DEFER_DEADLINE = datetime(2026, 9, 11, 15, 31, tzinfo=timezone.utc)
VALIDITY_DEADLINE = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
AFTER_DEADLINE = DEFER_DEADLINE + timedelta(minutes=1)
OTHER_SNAPSHOT_ID = "SNAP:r3f4p-fixture-other"
OUR_SNAPSHOT_ID = "SNAP:r3f4p-fixture"
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
VENUE_INSTRUMENT_ID = "SOLUSD"

POLICY = OpportunityLifecyclePolicy()


def build_claim(
    *,
    cutoff: datetime = CUTOFF,
    snapshot_id: str = OUR_SNAPSHOT_ID,
) -> DetectorClaim:
    return DetectorClaim.create(
        detector_version=IGNITION_DETECTOR_VERSION,
        policy_version=IGNITION_POLICY_VERSION,
        instrument_version_id=INSTRUMENT_VERSION_ID,
        venue_instrument_id=VENUE_INSTRUMENT_ID,
        snapshot_id=snapshot_id,
        detector_input_fingerprint="DETIN:r3f4p-fixture",
        evaluation_cutoff=cutoff,
    )


def deferral(
    *,
    defer: datetime = DEFER_DEADLINE,
    validity: datetime = VALIDITY_DEADLINE,
) -> OpportunityDeferral:
    return OpportunityDeferral(defer_deadline=defer, validity_deadline=validity)


def active_result(claim: DetectorClaim | None = None) -> OpportunityLifecycleResult:
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(chosen, None, chosen.evaluation_cutoff, POLICY)


def deferred_result(claim: DetectorClaim | None = None) -> OpportunityLifecycleResult:
    chosen = claim if claim is not None else build_claim()
    return lifecycle.apply_claim(
        chosen, None, chosen.evaluation_cutoff, POLICY, deferral_request=deferral()
    )


def expired_result(claim: DetectorClaim | None = None) -> OpportunityLifecycleResult:
    deferred = deferred_result(claim)
    return lifecycle.evaluate_time(deferred.episode, DEFER_DEADLINE, POLICY)


def payload_for(result: OpportunityLifecycleResult) -> dict[str, Any]:
    return persistence.build_opportunity_transition_payload(
        result.episode, result.events[0]
    )


@pytest.fixture
def canonical_store(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))
    db = tmp_path / "canonical" / "opip_canonical_v1.sqlite3"
    db.parent.mkdir(parents=True, exist_ok=True)
    return db


def open_writer(db: Path) -> CanonicalWriter:
    return CanonicalWriter(db)


def _connect(db: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db))
    connection.row_factory = sqlite3.Row
    return connection


def f4_rows(db: Path) -> list[dict[str, Any]]:
    connection = _connect(db)
    try:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT event_id, event_time, causation_id, correlation_id, "
                "idempotency_key, history_epoch, local_sequence, payload_json "
                "FROM events WHERE event_type = ? "
                "ORDER BY history_epoch ASC, local_sequence ASC",
                (RECORDED,),
            ).fetchall()
        ]
    finally:
        connection.close()


def f4_count(db: Path) -> int:
    return len(f4_rows(db))


def table_names(db: Path) -> set[str]:
    connection = _connect(db)
    try:
        return {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        connection.close()


def next_local_sequence(db: Path) -> int:
    connection = _connect(db)
    try:
        row = connection.execute(
            "SELECT next_local_sequence FROM meta WHERE id = 1"
        ).fetchone()
        return int(row["next_local_sequence"])
    finally:
        connection.close()


def watermark_rows(db: Path) -> dict[str, tuple[int, int]]:
    connection = _connect(db)
    try:
        return {
            str(row["stream"]): (
                int(row["history_epoch"]),
                int(row["local_sequence"]),
            )
            for row in connection.execute(
                "SELECT stream, history_epoch, local_sequence FROM watermarks"
            ).fetchall()
        }
    finally:
        connection.close()


def column_count(db: Path, table: str) -> int:
    connection = _connect(db)
    try:
        row = connection.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
    finally:
        connection.close()


def imported_module_names(source: str) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def canonical_intent(payload: dict[str, Any]) -> WriterIntent:
    return WriterIntent(
        schema_version=EVENT_SCHEMA_VERSION,
        priority=persistence.OPPORTUNITY_LIFECYCLE_PRIORITY,
        idempotency_key=persistence.opportunity_transition_idempotency_key(payload),
        event_type=RECORDED,
        payload=payload,
        event_time=persistence.opportunity_transition_event_time(payload),
        correlation_id=persistence.opportunity_transition_correlation_id(payload),
        causation_id=persistence.opportunity_transition_causation_id(payload),
        ops_handoff=None,
    )


class RecordingClient:
    """Minimal writer-client double that records submitted intents."""

    def __init__(self) -> None:
        self.intents: list[WriterIntent] = []

    def submit(self, intent: WriterIntent) -> WriterAck:
        self.intents.append(intent)
        return WriterAck(
            status="OK", event_id=f"EVT:{len(self.intents)}", history_epoch=1,
            local_sequence=len(self.intents),
        )


# ---------------------------------------------------------------------------
# AC-001 .. AC-022
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_001_event_type_stream_and_schema_are_uniquely_registered() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-001: the F4 durable event type, stream and schema token are uniquely registered in the single canonical writer."""
    assert RECORDED == "opportunity_lifecycle.transition.recorded"
    assert persistence.OPPORTUNITY_LIFECYCLE_EVENT_TYPES == frozenset({RECORDED})
    assert persistence.OPPORTUNITY_LIFECYCLE_STREAM == "opportunity_lifecycle"
    assert persistence.OPPORTUNITY_LIFECYCLE_PRIORITY == "LOW"
    assert (
        persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN
        == "opportunity-lifecycle-transition-v1"
    )
    assert RECORDED in ACCEPTED_EVENT_TYPES
    assert RECORDED in IDEMPOTENT_PAYLOAD_EVENT_TYPES
    assert CanonicalWriter._stream_for_event(RECORDED) == (
        persistence.OPPORTUNITY_LIFECYCLE_STREAM
    )
    assert CanonicalWriter._requires_ops_handoff(RECORDED) is False


@pytest.mark.acceptance
def test_ac_002_changed_result_yields_exactly_one_writer_intent() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-002: a changed lifecycle result yields exactly one writer intent."""
    client = RecordingClient()
    result = deferred_result()
    assert result.changed is True
    assert len(result.events) == 1
    ack = producer.persist_opportunity_result(client, result)
    assert ack is not None and ack.status == "OK"
    assert len(client.intents) == 1
    intent = client.intents[0]
    assert intent.event_type == RECORDED
    assert intent.priority == "LOW"
    assert intent.ops_handoff is None
    assert intent.idempotency_key == (
        persistence.opportunity_lifecycle_transition_idempotency_key(
            result.events[0].event_id
        )
    )
    assert intent.correlation_id == result.episode.episode_id


@pytest.mark.acceptance
def test_ac_003_unchanged_result_yields_no_write(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-003: an unchanged result with zero events writes nothing."""
    writer = open_writer(canonical_store)
    try:
        deferred = deferred_result()
        assert producer.persist_opportunity_result(writer, deferred).status == "OK"
        assert f4_count(canonical_store) == 1

        duplicate = lifecycle.apply_claim(
            build_claim(), deferred.episode, DEFER_DEADLINE, POLICY
        )
        assert duplicate.changed is False and duplicate.events == ()
        assert producer.persist_opportunity_result(writer, duplicate) is None
        assert f4_count(canonical_store) == 1
    finally:
        writer.close()

    client = RecordingClient()
    assert producer.persist_opportunity_result(client, duplicate) is None
    assert client.intents == []


@pytest.mark.acceptance
def test_ac_004_payload_round_trip_reconstructs_exact_episode_and_event() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-004: the durable payload round-trips to the exact episode and event."""
    for result in (active_result(), deferred_result(), expired_result()):
        payload = payload_for(result)
        assert payload["record_type"] == persistence.OPPORTUNITY_LIFECYCLE_RECORD_TYPE
        assert payload["schema_version"] == (
            persistence.OPPORTUNITY_LIFECYCLE_TRANSITION_SCHEMA_TOKEN
        )
        episode = OpportunityEpisode.from_dict(payload["episode"])
        event = OpportunityLifecycleEvent.from_dict(payload["event"])
        assert episode == result.episode
        assert event == result.events[0]
        # Strict convenience round-trips too.
        assert OpportunityEpisode.from_dict(result.episode.to_dict()) == result.episode
        assert (
            OpportunityLifecycleEvent.from_dict(result.events[0].to_dict())
            == result.events[0]
        )


@pytest.mark.acceptance
def test_ac_005_idempotency_derives_only_from_lifecycle_event_identity() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-005: the idempotency key is derived only from the deterministic lifecycle event identity."""
    event = deferred_result().events[0]
    key = persistence.opportunity_lifecycle_transition_idempotency_key(event.event_id)
    assert key == f"OPLT:{event.event_id}"
    # Deterministic across calls and independent of receipt time.
    assert key == persistence.opportunity_lifecycle_transition_idempotency_key(
        event.event_id
    )
    other = active_result(build_claim(snapshot_id=OTHER_SNAPSHOT_ID)).events[0]
    assert persistence.opportunity_lifecycle_transition_idempotency_key(
        other.event_id
    ) != key
    for bad in ("", "OPEV:", "OPEP:" + "a" * 32, "OPEV: ", 5, True, None):
        with pytest.raises(OpportunityContractError):
            persistence.opportunity_lifecycle_transition_idempotency_key(bad)  # type: ignore[arg-type]


@pytest.mark.acceptance
def test_ac_006_exact_replay_is_duplicate_ok_with_one_row(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-006: an exact replay reaches DUPLICATE_OK and leaves exactly one canonical row."""
    writer = open_writer(canonical_store)
    try:
        deferred = deferred_result()
        first = producer.persist_opportunity_result(writer, deferred)
        again = producer.persist_opportunity_result(writer, deferred)
        assert first.status == "OK"
        assert again.status == "DUPLICATE_OK"
        assert again.event_id == first.event_id
        assert f4_count(canonical_store) == 1
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_007_same_key_different_payload_fails_closed(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-007: the same idempotency key with a semantically different payload fails closed."""
    writer = open_writer(canonical_store)
    try:
        first = deferred_result()
        assert producer.persist_opportunity_result(writer, first).status == "OK"
        first_event_id = first.events[0].event_id

        # Same claim/cutoff/evaluation -> same episode id and same event id, but a
        # different validity deadline, so the payload differs semantically.
        altered = lifecycle.apply_claim(
            build_claim(),
            None,
            CUTOFF,
            POLICY,
            deferral_request=deferral(
                validity=VALIDITY_DEADLINE + timedelta(hours=1)
            ),
        )
        assert altered.events[0].event_id == first_event_id
        conflict = writer.submit(canonical_intent(payload_for(altered)))
        assert conflict.status == "REJECTED"
        assert conflict.error_code == "IDEMPOTENCY_PAYLOAD_CONFLICT"
        assert f4_count(canonical_store) == 1
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_008_opened_to_active_is_valid(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-008: an OPENED transition into ACTIVE persists and projects."""
    writer = open_writer(canonical_store)
    try:
        result = active_result()
        assert producer.persist_opportunity_result(writer, result).status == "OK"
        projection = writer.opportunity_episode_projection(result.episode.episode_id)
        assert projection.status == "OK"
        assert projection.lifecycle_state == "ACTIVE"
        assert projection.episode == result.episode
        assert projection.event_count == 1
        assert f4_count(canonical_store) == 1
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_009_deferred_to_deferred_is_valid(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-009: a DEFERRED creation persists and projects with its deadlines."""
    writer = open_writer(canonical_store)
    try:
        result = deferred_result()
        assert producer.persist_opportunity_result(writer, result).status == "OK"
        projection = writer.opportunity_episode_projection(result.episode.episode_id)
        assert projection.status == "OK"
        assert projection.lifecycle_state == "DEFERRED"
        assert projection.episode.defer_deadline == DEFER_DEADLINE
        assert projection.episode.validity_deadline == VALIDITY_DEADLINE
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_010_deferred_to_expired_terminal_is_valid(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-010: DEFERRED then EXPIRED persists to TERMINAL/EXPIRED with two rows."""
    writer = open_writer(canonical_store)
    try:
        deferred = deferred_result()
        expired = lifecycle.evaluate_time(deferred.episode, DEFER_DEADLINE, POLICY)
        assert producer.persist_opportunity_result(writer, deferred).status == "OK"
        assert producer.persist_opportunity_result(writer, expired).status == "OK"
        projection = writer.opportunity_episode_projection(deferred.episode.episode_id)
        assert projection.status == "OK"
        assert projection.lifecycle_state == "TERMINAL"
        assert projection.episode.terminal_reason is OpportunityTerminalReason.EXPIRED
        assert projection.event_count == 2
        assert f4_count(canonical_store) == 2
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_011_expired_without_deferred_fails_closed(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-011: an EXPIRED transition without a preceding DEFERRED fails closed."""
    expired = expired_result()
    payload = payload_for(expired)
    # The pure durable-history fold refuses an expiry-first history.
    with pytest.raises(OpportunityContractError):
        persistence.reconstruct_opportunity_transition_history([payload])

    writer = open_writer(canonical_store)
    try:
        ack = writer.submit(canonical_intent(payload))
        assert ack.status == "REJECTED"
        assert f4_count(canonical_store) == 0
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_012_terminal_cannot_advance(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-012: a terminal episode cannot advance and duplicates do not extend the sequence."""
    deferred_payload = payload_for(deferred_result())
    expired_payload = payload_for(expired_result())
    terminal = persistence.reconstruct_opportunity_transition_history(
        [deferred_payload, expired_payload]
    )
    for later in (deferred_payload, expired_payload):
        with pytest.raises(OpportunityContractError):
            persistence.apply_opportunity_transition_record(terminal, later)
    with pytest.raises(OpportunityContractError):
        persistence.reconstruct_opportunity_transition_history(
            [deferred_payload, expired_payload, expired_payload]
        )

    writer = open_writer(canonical_store)
    try:
        deferred = deferred_result()
        expired = lifecycle.evaluate_time(deferred.episode, DEFER_DEADLINE, POLICY)
        producer.persist_opportunity_result(writer, deferred)
        producer.persist_opportunity_result(writer, expired)
        before = next_local_sequence(canonical_store)
        replay = producer.persist_opportunity_result(writer, expired)
        assert replay.status == "DUPLICATE_OK"
        assert next_local_sequence(canonical_store) == before
        assert f4_count(canonical_store) == 2
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_013_restart_rehydrates_active(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-013: a restarted writer rehydrates an ACTIVE episode identically and replays safely."""
    writer = open_writer(canonical_store)
    result = active_result()
    original = result.episode
    assert producer.persist_opportunity_result(writer, result).status == "OK"
    writer.close()

    reopened = open_writer(canonical_store)
    try:
        recovered = reopened.opportunity_episode_projection(original.episode_id)
        assert recovered.status == "OK"
        assert recovered.episode == original
        replay = producer.persist_opportunity_result(reopened, result)
        assert replay.status == "DUPLICATE_OK"
        assert f4_count(canonical_store) == 1
    finally:
        reopened.close()


@pytest.mark.acceptance
def test_ac_014_restart_rehydrates_deferred_preserving_deadlines(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-014: a restarted writer rehydrates DEFERRED and preserves its deadlines."""
    writer = open_writer(canonical_store)
    result = deferred_result()
    original = result.episode
    assert producer.persist_opportunity_result(writer, result).status == "OK"
    writer.close()

    reopened = open_writer(canonical_store)
    try:
        recovered = reopened.opportunity_episode_projection(original.episode_id)
        assert recovered.status == "OK"
        assert recovered.lifecycle_state == "DEFERRED"
        assert recovered.episode == original
        assert recovered.episode.defer_deadline == DEFER_DEADLINE
        assert recovered.episode.validity_deadline == VALIDITY_DEADLINE

        # The recovered episode can be expired purely from its persisted deadline.
        expired = lifecycle.evaluate_time(recovered.episode, DEFER_DEADLINE, POLICY)
        assert producer.persist_opportunity_result(reopened, expired).status == "OK"
    finally:
        reopened.close()


@pytest.mark.acceptance
def test_ac_015_restart_rehydrates_terminal_expired(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-015: a restarted writer rehydrates TERMINAL/EXPIRED and replays the expiry safely."""
    writer = open_writer(canonical_store)
    deferred = deferred_result()
    expired = lifecycle.evaluate_time(deferred.episode, DEFER_DEADLINE, POLICY)
    producer.persist_opportunity_result(writer, deferred)
    producer.persist_opportunity_result(writer, expired)
    original = expired.episode
    writer.close()

    reopened = open_writer(canonical_store)
    try:
        recovered = reopened.opportunity_episode_projection(original.episode_id)
        assert recovered.status == "OK"
        assert recovered.lifecycle_state == "TERMINAL"
        assert recovered.episode == original
        assert recovered.event_count == 2
        replay = producer.persist_opportunity_result(reopened, expired)
        assert replay.status == "DUPLICATE_OK"
        assert f4_count(canonical_store) == 2
    finally:
        reopened.close()


@pytest.mark.acceptance
def test_ac_016_forged_identity_or_lineage_durable_payload_is_rejected(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-016: a forged identity or lineage durable payload is rejected with no row."""
    writer = open_writer(canonical_store)
    try:
        payload = payload_for(deferred_result())
        tampered = copy.deepcopy(payload)
        tampered["episode"]["episode_id"] = "OPEP:" + "0" * 32
        with pytest.raises(OpportunityContractError):
            persistence.validate_opportunity_transition_record(RECORDED, tampered)
        ack = writer.submit(
            WriterIntent(
                schema_version=EVENT_SCHEMA_VERSION,
                priority="LOW",
                idempotency_key="OPLT:OPEV:" + "0" * 32,
                event_type=RECORDED,
                payload=tampered,
                event_time=None,
                correlation_id=None,
                causation_id=None,
                ops_handoff=None,
            )
        )
        assert ack.status == "REJECTED"
        assert f4_count(canonical_store) == 0

        lineage = copy.deepcopy(payload)
        lineage["episode"]["snapshot_id"] = "SNAP:forged"
        writer.submit(
            WriterIntent(
                schema_version=EVENT_SCHEMA_VERSION,
                priority="LOW",
                idempotency_key="OPLT:OPEV:" + "3" * 32,
                event_type=RECORDED,
                payload=lineage,
                event_time=None,
                correlation_id=None,
                causation_id=None,
                ops_handoff=None,
            )
        )
        assert f4_count(canonical_store) == 0
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_017_projection_cannot_lead_durable_transaction_state(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-017: the in-memory projection never leads durable state; a failed commit, duplicate or rejection does not advance it."""
    writer = open_writer(canonical_store)
    try:
        deferred = deferred_result()
        assert producer.persist_opportunity_result(writer, deferred).status == "OK"
        # A duplicate must not advance the projection twice.
        producer.persist_opportunity_result(writer, deferred)
        projection = writer.opportunity_episode_projection(deferred.episode.episode_id)
        assert projection.event_count == 1

        # A rejected write must not mutate the projection.
        bad = copy.deepcopy(payload_for(deferred))
        bad["event"]["event_id"] = "OPEV:" + "1" * 32
        writer.submit(
            WriterIntent(
                schema_version=EVENT_SCHEMA_VERSION,
                priority="LOW",
                idempotency_key="OPLT:OPEV:" + "1" * 32,
                event_type=RECORDED,
                payload=bad,
                event_time=None,
                correlation_id=None,
                causation_id=None,
                ops_handoff=None,
            )
        )
        assert writer.opportunity_episode_projection(
            deferred.episode.episode_id
        ).event_count == 1

        # A failed SQLite commit must not advance the in-memory projection.
        expired = lifecycle.evaluate_time(deferred.episode, DEFER_DEADLINE, POLICY)

        class _FailingCommit:
            def __init__(self, real: Any) -> None:
                self._real = real

            def commit(self) -> None:
                raise sqlite3.OperationalError("forced commit failure")

            def __getattr__(self, name: str) -> Any:
                return getattr(self._real, name)

        real_conn = writer._conn
        writer._conn = _FailingCommit(real_conn)
        try:
            ack = producer.persist_opportunity_result(writer, expired)
            assert ack.status == "RETRYABLE"
        finally:
            writer._conn = real_conn
        assert f4_count(canonical_store) == 1
        assert writer.opportunity_episode_projection(
            deferred.episode.episode_id
        ).lifecycle_state == "DEFERRED"
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_018_uses_existing_canonical_writer_and_generic_store(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-018: persistence reuses the existing CanonicalWriter and generic tables, with no second DB/table/writer."""
    contract_source = (
        APP_ROOT / "app" / "opip" / "contracts" / "opportunity_persistence.py"
    ).read_text(encoding="utf-8")
    producer_source = (
        APP_ROOT / "app" / "opip" / "opportunity_persistence.py"
    ).read_text(encoding="utf-8")
    schema_source = (
        APP_ROOT / "app" / "opip" / "canonical" / "schema.py"
    ).read_text(encoding="utf-8")
    assert "sqlite3" not in contract_source
    assert "import sqlite3" not in producer_source
    assert "CanonicalWriter(" not in producer_source
    assert "opportunity" not in schema_source.lower()
    for forbidden in ("opportunity.sqlite", "jsonl", "jsonlines"):
        assert forbidden not in contract_source
        assert forbidden not in producer_source

    writer = open_writer(canonical_store)
    try:
        assert producer.persist_opportunity_result(writer, deferred_result()).status == "OK"
        tables = table_names(canonical_store)
        assert "events" in tables
        assert "idempotency_keys" in tables
        assert "watermarks" in tables
        assert "opportunity_episode" not in tables
        assert "opportunity_lifecycle_event" not in tables
        assert persistence.OPPORTUNITY_LIFECYCLE_STREAM in watermark_streams(
            canonical_store
        )
    finally:
        writer.close()


def watermark_streams(db: Path) -> set[str]:
    return set(watermark_rows(db))


@pytest.mark.acceptance
def test_ac_019_no_ops_handoff_and_no_alert_mutation(canonical_store) -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-019: F4 persistence creates no ops-handoff and mutates no alert projection."""
    writer = open_writer(canonical_store)
    try:
        assert producer.persist_opportunity_result(writer, deferred_result()).status == "OK"
        assert writer.list_pending_handoffs() == []
        assert column_count(canonical_store, "alert_ops_handoffs") == 0
        assert column_count(canonical_store, "alert_identity_projection") == 0
    finally:
        writer.close()


@pytest.mark.acceptance
def test_ac_020_no_runtime_caller_no_feature_bus_activation_no_consumer_migration() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-020: no production runtime caller, Feature Bus stays OFF, no consumer migration."""
    tracked = (
        APP_ROOT / "app" / "jobs" / "run_cycle.py",
        APP_ROOT / "app" / "jobs" / "scan_opportunities.py",
        APP_ROOT / "app" / "services" / "entry_watch_queue.py",
        APP_ROOT / "app" / "services" / "price_movement_radar.py",
        APP_ROOT / "app" / "jobs" / "monitor_pending_setups.py",
        APP_ROOT / "app" / "services" / "signal_quality_phase2.py",
    )
    for path in tracked:
        text = path.read_text(encoding="utf-8")
        assert "opportunity_persistence" not in text, path
        assert "opportunity_lifecycle" not in text, path
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'OPIP_FEATURE_BUS_MODE: "off"' in compose
    # The producer seam is dormant: nothing outside the authorized persistence
    # files plus the canonical writer's IPC vocabulary imports it.
    authorized = {
        "opportunity_persistence.py",
        "models.py",
    }
    for path in sorted((APP_ROOT / "app").rglob("*.py")):
        if path.name in authorized:
            continue
        if path.parent.name == "canonical" and path.name in {"writer.py", "client.py", "server.py"}:
            continue
        if path.parent.name == "contracts":
            continue
        assert "opip.opportunity_persistence" not in path.read_text(encoding="utf-8"), path


@pytest.mark.acceptance
def test_ac_021_f3_and_pure_f4_semantics_unchanged() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-021: F3 and the pure F4 lifecycle semantics are unchanged; the pure isolation guard was widened only."""
    lifecycle_source = (
        APP_ROOT / "app" / "opip" / "opportunity_lifecycle.py"
    ).read_text(encoding="utf-8")
    lifecycle_modules = imported_module_names(lifecycle_source)
    assert "sqlite3" not in lifecycle_modules
    assert not any(module.startswith("app.opip.canonical") for module in lifecycle_modules)
    assert not any("persistence" in module for module in lifecycle_modules)

    # F3 still refuses a forged claim and still forbids an episode identity.
    detector_source = (
        APP_ROOT / "app" / "opip" / "contracts" / "detector.py"
    ).read_text(encoding="utf-8")
    assert "episode_id is not None" in detector_source
    with pytest.raises(DetectorContractError):
        replace(build_claim(), claim_id="DCLM:forged")

    # The pure lifecycle still produces identical results.
    first = expired_result().to_dict()
    second = expired_result().to_dict()
    assert first == second

    guard = (
        APP_ROOT / "tests" / "test_opip_r3_f4_opportunity_lifecycle.py"
    ).read_text(encoding="utf-8")
    for assertion in (
        '"opip.opportunity_lifecycle" not in text',
        '"opportunity_lifecycle" not in text',
        '"contracts.opportunity" not in text',
        '"apply_claim" not in text',
    ):
        assert assertion in guard, assertion


@pytest.mark.acceptance
def test_ac_022_f5_plus_absent() -> None:
    """ATDD-R3-F4-opportunity-lifecycle-persistence/AC-022: F5/F6/F7, Paper-v2 and funded authority are absent from this increment."""
    allowed_modules = {
        "__future__",
        "dataclasses",
        "typing",
        "app.opip.contracts",
        "app.opip.contracts.opportunity",
        "app.opip.contracts.opportunity_persistence",
        "app.opip.contracts.serialization",
        "app.opip.canonical.models",
        "app.opip.canonical.paths",
    }
    for name in (
        "contracts/opportunity_persistence.py",
        "opportunity_persistence.py",
    ):
        source = (APP_ROOT / "app" / "opip" / name).read_text(encoding="utf-8")
        modules = imported_module_names(source)
        assert modules <= allowed_modules, (name, modules - allowed_modules)
        assert "funded" not in source.lower()
    # No second architecture spine and no F5+ package is created.
    assert not (APP_ROOT / "app" / "opip" / "opportunity").exists()
    for absent in ("f5", "f6", "f7", "forecast", "selector", "feasibility"):
        assert not (APP_ROOT / "app" / "opip" / absent).exists()


# ---------------------------------------------------------------------------
# Required proofs
# ---------------------------------------------------------------------------


def test_replay_and_restart_proof_active(canonical_store) -> None:
    """The ACTIVE replay+restart proof: persist, restart, recover, replay, one row."""
    writer = open_writer(canonical_store)
    result = active_result()
    assert producer.persist_opportunity_result(writer, result).status == "OK"
    writer.close()

    reopened = open_writer(canonical_store)
    try:
        recovered = reopened.opportunity_episode_projection(result.episode.episode_id)
        assert recovered.episode == result.episode
        assert producer.persist_opportunity_result(reopened, result).status == "DUPLICATE_OK"
        assert f4_count(canonical_store) == 1
    finally:
        reopened.close()


def test_deferred_expiry_restart_proof(canonical_store) -> None:
    """The DEFERRED->EXPIRED restart proof: two rows, terminal replay is DUPLICATE_OK."""
    writer = open_writer(canonical_store)
    deferred = deferred_result()
    assert producer.persist_opportunity_result(writer, deferred).status == "OK"
    writer.close()

    reopened = open_writer(canonical_store)
    recovered = reopened.opportunity_episode_projection(deferred.episode.episode_id)
    expired = lifecycle.evaluate_time(recovered.episode, DEFER_DEADLINE, POLICY)
    assert producer.persist_opportunity_result(reopened, expired).status == "OK"
    reopened.close()

    final = open_writer(canonical_store)
    try:
        terminal = final.opportunity_episode_projection(deferred.episode.episode_id)
        assert terminal.lifecycle_state == "TERMINAL"
        assert terminal.event_count == 2
        assert producer.persist_opportunity_result(final, expired).status == "DUPLICATE_OK"
        assert f4_count(canonical_store) == 2
    finally:
        final.close()


# ---------------------------------------------------------------------------
# Adversarial matrix: payload validation
# ---------------------------------------------------------------------------


def _mutations() -> list[tuple[str, Any]]:
    def naive(payload: dict[str, Any]) -> None:
        payload["episode"]["last_evaluation_time"] = "2026-09-11T15:01:00"
        payload["event"]["evaluation_time"] = "2026-09-11T15:01:00"

    return [
        ("schema_token", lambda p: p.update(schema_version="v2")),
        ("record_type", lambda p: p.update(record_type="other")),
        ("missing_episode", lambda p: p.pop("episode")),
        ("missing_event", lambda p: p.pop("event")),
        ("non_mapping_episode", lambda p: p.update(episode="x")),
        ("unknown_key", lambda p: p.update(extra="x")),
        ("forged_episode_id", lambda p: p["episode"].update(episode_id="OPEP:" + "0" * 32)),
        ("forged_event_id", lambda p: p["event"].update(event_id="OPEV:" + "0" * 32)),
        ("event_episode_mismatch", lambda p: p["event"].update(episode_id="OPEP:other")),
        ("policy_version", lambda p: p["episode"].update(policy_version="opportunity-shadow-policy-v2")),
        ("lifecycle_version", lambda p: p["event"].update(lifecycle_version="opportunity-lifecycle-v2")),
        ("unsupported_event_token", lambda p: p["event"].update(event_type="REOPENED")),
        ("bad_state_token", lambda p: p["episode"].update(lifecycle_state="active")),
        ("bad_terminal_reason", lambda p: p["episode"].update(terminal_reason="TIMEOUT")),
        ("altered_lineage", lambda p: p["episode"].update(snapshot_id="SNAP:forged")),
        ("naive_datetime", naive),
    ]


@pytest.mark.parametrize(
    "mutate", [mutation for _name, mutation in _mutations()],
    ids=[name for name, _mutation in _mutations()],
)
def test_payload_validation_rejects_malformed_record(mutate) -> None:
    payload = copy.deepcopy(payload_for(deferred_result()))
    mutate(payload)
    with pytest.raises(OpportunityContractError):
        persistence.validate_opportunity_transition_record(RECORDED, payload)


def test_payload_validation_rejects_non_mapping_payload() -> None:
    for bad in ([], "x", 5, None):
        with pytest.raises(OpportunityContractError):
            persistence.validate_opportunity_transition_record(RECORDED, bad)


def test_payload_validation_rejects_unsupported_event_type() -> None:
    with pytest.raises(OpportunityContractError):
        persistence.validate_opportunity_transition_record(
            "opportunity_lifecycle.other.recorded", payload_for(deferred_result())
        )


def test_wrong_event_state_pairing_is_refused() -> None:
    opened = active_result()
    deferred = deferred_result()
    with pytest.raises(OpportunityContractError):
        persistence.build_opportunity_transition_payload(
            deferred.episode, opened.events[0]
        )
    with pytest.raises(OpportunityContractError):
        persistence.build_opportunity_transition_payload(
            opened.episode, deferred.events[0]
        )


# ---------------------------------------------------------------------------
# Adversarial matrix: durable-history reconstruction / corruption
# ---------------------------------------------------------------------------


def test_history_reconstruction_accepts_only_valid_histories() -> None:
    opened = payload_for(active_result())
    deferred = payload_for(deferred_result())
    expired = payload_for(expired_result())
    assert (
        persistence.reconstruct_opportunity_transition_history([opened]).lifecycle_state
        is OpportunityLifecycleState.ACTIVE
    )
    assert (
        persistence.reconstruct_opportunity_transition_history([deferred]).lifecycle_state
        is OpportunityLifecycleState.DEFERRED
    )
    terminal = persistence.reconstruct_opportunity_transition_history(
        [deferred, expired]
    )
    assert terminal.lifecycle_state is OpportunityLifecycleState.TERMINAL


def test_history_reconstruction_refuses_every_impossible_ordering() -> None:
    opened = payload_for(active_result())
    deferred = payload_for(deferred_result())
    expired = payload_for(expired_result())
    bad_histories = (
        [expired],  # EXPIRED first
        [opened, opened],  # second OPENED
        [deferred, deferred],  # second DEFERRED
        [opened, expired],  # OPENED -> EXPIRED
        [deferred, expired, expired],  # transition after terminal
        [deferred, expired, deferred],  # terminal reopening
        [],
    )
    for history in bad_histories:
        with pytest.raises(OpportunityContractError):
            persistence.reconstruct_opportunity_transition_history(history)


def test_history_reconstruction_refuses_altered_deadlines() -> None:
    deferred = payload_for(deferred_result())
    expired = payload_for(expired_result())
    tampered = copy.deepcopy(expired)
    tampered["episode"]["validity_deadline"] = (
        VALIDITY_DEADLINE + timedelta(hours=3)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with pytest.raises(OpportunityContractError):
        persistence.reconstruct_opportunity_transition_history([deferred, tampered])


def test_history_reconstruction_refuses_version_change_mid_episode() -> None:
    deferred_payload = payload_for(deferred_result())
    expired_payload = payload_for(expired_result())
    prior = persistence.apply_opportunity_transition_record(None, deferred_payload)
    mutated = replace(prior)
    object.__setattr__(mutated, "policy_version", "opportunity-shadow-policy-v2")
    with pytest.raises(OpportunityContractError):
        persistence.apply_opportunity_transition_record(mutated, expired_payload)


# ---------------------------------------------------------------------------
# Adversarial matrix: writer envelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("priority", "HIGH", "REJECTED"),
        ("priority", "NORMAL", "REJECTED"),
        ("ops_handoff", {"operation": "RECORD", "state_family": "early_watch"}, "REJECTED"),
        ("correlation_id", "OPEP:wrong", "REJECTED"),
        ("causation_id", "DCLM:wrong", "REJECTED"),
        ("event_time", "2000-01-01T00:00:00Z", "REJECTED"),
        ("idempotency_key", "OPLT:OPEV:" + "9" * 32, "REJECTED"),
    ],
)
def test_writer_rejects_decoupled_envelope(canonical_store, field, value, expected) -> None:
    writer = open_writer(canonical_store)
    try:
        payload = payload_for(deferred_result())
        intent = canonical_intent(payload)
        ack = writer.submit(replace(intent, **{field: value}))
        assert ack.status == expected
        assert f4_count(canonical_store) == 0
    finally:
        writer.close()


def test_writer_rejects_unregistered_event_type(canonical_store) -> None:
    writer = open_writer(canonical_store)
    try:
        payload = payload_for(deferred_result())
        intent = replace(canonical_intent(payload), event_type="opportunity.other.recorded")
        ack = writer.submit(intent)
        assert ack.status == "REJECTED"
        assert f4_count(canonical_store) == 0
    finally:
        writer.close()


def test_low_priority_is_accepted(canonical_store) -> None:
    writer = open_writer(canonical_store)
    try:
        assert producer.persist_opportunity_result(writer, deferred_result()).status == "OK"
    finally:
        writer.close()


def test_rejected_write_does_not_advance_watermark(canonical_store) -> None:
    writer = open_writer(canonical_store)
    try:
        bad = copy.deepcopy(payload_for(deferred_result()))
        bad["event"]["event_id"] = "OPEV:" + "2" * 32
        writer.submit(
            WriterIntent(
                schema_version=EVENT_SCHEMA_VERSION,
                priority="LOW",
                idempotency_key="OPLT:OPEV:" + "2" * 32,
                event_type=RECORDED,
                payload=bad,
                event_time=None,
                correlation_id=None,
                causation_id=None,
                ops_handoff=None,
            )
        )
        assert RECORDED not in watermark_streams(canonical_store)
    finally:
        writer.close()


def test_duplicate_does_not_change_sequence_or_projection(canonical_store) -> None:
    writer = open_writer(canonical_store)
    try:
        result = deferred_result()
        producer.persist_opportunity_result(writer, result)
        sequence_before = next_local_sequence(canonical_store)
        projection_before = writer.opportunity_episode_projection(
            result.episode.episode_id
        )
        producer.persist_opportunity_result(writer, result)
        assert next_local_sequence(canonical_store) == sequence_before
        assert writer.opportunity_episode_projection(
            result.episode.episode_id
        ) == projection_before
    finally:
        writer.close()


def test_read_api_performs_no_transition_and_reports_unknown(canonical_store) -> None:
    writer = open_writer(canonical_store)
    try:
        unknown = writer.opportunity_episode_projection("OPEP:" + "a" * 32)
        assert unknown.status == "NOT_FOUND"
        assert unknown.episode is None
        assert "OPEP" in unknown.episode_id
        malformed = writer.opportunity_episode_projection("not-an-episode")
        assert malformed.status == "REJECTED"
        assert malformed.error_code == "MALFORMED_EPISODE_ID"

        result = active_result()
        producer.persist_opportunity_result(writer, result)
        before = f4_count(canonical_store)
        first = writer.opportunity_episode_projection(result.episode.episode_id)
        second = writer.opportunity_episode_projection(result.episode.episode_id)
        assert first == second
        assert f4_count(canonical_store) == before
    finally:
        writer.close()


def test_payload_is_well_under_the_writer_limit() -> None:
    for result in (active_result(), deferred_result(), expired_result()):
        payload = payload_for(result)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        assert len(encoded.encode("utf-8")) < MAX_PAYLOAD_BYTES


def test_reopen_refuses_corrupt_persisted_history(canonical_store) -> None:
    """Fail closed on an impossible persisted history, with no repair heuristic."""
    writer = open_writer(canonical_store)
    writer.close()
    forged = payload_for(expired_result())  # an expiry-first history is impossible
    connection = _connect(canonical_store)
    try:
        connection.execute(
            """
            INSERT INTO events (
                event_id, schema_version, event_type, history_epoch, local_sequence,
                recorded_at, event_time, causation_id, correlation_id,
                idempotency_key, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "EVT:forged",
                EVENT_SCHEMA_VERSION,
                RECORDED,
                1,
                1,
                "2026-09-11T15:00:00Z",
                None,
                None,
                None,
                "OPLT:forged",
                json.dumps(forged, separators=(",", ":"), sort_keys=True),
            ),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises((ValueError, sqlite3.Error)):
        CanonicalWriter(canonical_store)


def test_contract_module_is_pure_of_persistence_io() -> None:
    source = (
        APP_ROOT / "app" / "opip" / "contracts" / "opportunity_persistence.py"
    ).read_text(encoding="utf-8")
    roots = {
        node.module.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".", 1)[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert roots.isdisjoint(
        {"sqlite3", "socket", "requests", "urllib", "time", "random", "uuid", "os", "sys"}
    )
