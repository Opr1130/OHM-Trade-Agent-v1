"""B/C-3 narrow instrument-version registration tests.

Proves Paper v2 can ensure its canonical instrument version without Feature Bus
capture enabled, without emitting any other Feature Bus event, and failing closed
at every step.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.canonical.models import WriterAck
from app.opip.canonical.writer import ACCEPTED_EVENT_TYPES, CanonicalWriter
from app.opip.contracts.events import (
    FEATURE_BUS_EVENT_TYPES,
    MARKET_INSTRUMENT_VERSION_RECORDED,
    MARKET_OBSERVATION_RECORDED,
)
from app.opip.contracts.identity import InstrumentVersion
from app.opip.features.publisher import (
    FeatureBusPublisher,
    feature_bus_capture_enabled,
    instrument_version_intent,
)
from app.services.paper_v2_instrument_registration import (
    COMMITTED_ACK_STATUSES,
    InstrumentRegistrationError,
    RegisteredInstrument,
    ensure_instrument_version_registered,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"


def _version(**overrides) -> InstrumentVersion:
    fields = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOL/USD",
        "version": 1,
        "reference_data_version": "kraken-ref-1",
        "observed_at_utc": NOW - timedelta(minutes=5),
    }
    fields.update(overrides)
    return InstrumentVersion(**fields)


@pytest.fixture
def writer(tmp_path) -> CanonicalWriter:
    instance = CanonicalWriter(tmp_path / "canonical.sqlite3")
    try:
        yield instance
    finally:
        instance.close()


def _rows(writer: CanonicalWriter, event_type: str) -> list[dict]:
    import json

    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _other_feature_bus_rows(writer: CanonicalWriter) -> dict[str, int]:
    """Counts of Feature Bus event types other than the instrument version."""
    counts: dict[str, int] = {}
    for event_type in sorted(FEATURE_BUS_EVENT_TYPES):
        if event_type == MARKET_INSTRUMENT_VERSION_RECORDED:
            continue
        count = len(_rows(writer, event_type))
        if count:
            counts[event_type] = count
    return counts


# ---------------------------------------------------------------------------
# Registration and proof
# ---------------------------------------------------------------------------


def test_instrument_version_is_registered_and_proven(writer):
    ref = ensure_instrument_version_registered(_version(), client=writer)
    assert isinstance(ref, RegisteredInstrument)
    assert ref.status in COMMITTED_ACK_STATUSES
    assert ref.instrument_version_id == INSTRUMENT_VERSION_ID
    assert ref.event_id
    assert ref.history_epoch >= 0 and ref.local_sequence >= 0
    assert len(_rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED)) == 1


def test_registration_uses_the_existing_canonical_intent(writer):
    """The narrow path reuses the existing intent, so there is no parallel format."""
    version = _version()
    ref = ensure_instrument_version_registered(version, client=writer)
    assert ref.idempotency_key == instrument_version_intent(version).idempotency_key


def test_registration_watermark_is_the_canonical_position(writer):
    ref = ensure_instrument_version_registered(_version(), client=writer)
    assert ref.watermark.history_epoch == ref.history_epoch
    assert ref.watermark.local_sequence == ref.local_sequence


def test_persisted_payload_verifies_against_the_canonical_reader(writer):
    """The stored record is a valid canonical instrument version, not a loose copy."""
    from app.opip.market.instrument_version_store import instrument_version_from_payload

    ensure_instrument_version_registered(_version(), client=writer)
    stored = _rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED)[0]
    rebuilt = instrument_version_from_payload(stored)
    assert rebuilt.instrument_version_id == INSTRUMENT_VERSION_ID


def test_registered_instrument_is_resolvable_by_the_writer(writer):
    """B/C-1's instrument lookup succeeds for the version we registered."""
    ensure_instrument_version_registered(_version(), client=writer)
    resolved = writer._load_instrument_version_by_id(INSTRUMENT_VERSION_ID)  # noqa: SLF001
    assert resolved["instrument_version_id"] == INSTRUMENT_VERSION_ID
    assert resolved["venue"] == "kraken"
    assert resolved["venue_instrument_id"] == "SOL/USD"


def test_an_unregistered_instrument_still_fails_closed_in_the_writer(writer):
    """Control: registration is what makes the lookup succeed."""
    with pytest.raises(ValueError, match="not a registered canonical instrument version"):
        writer._load_instrument_version_by_id("INSTR:kraken:ETH:USD:1")  # noqa: SLF001


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_repeat_registration_is_idempotent(writer):
    version = _version()
    first = ensure_instrument_version_registered(version, client=writer)
    retry = ensure_instrument_version_registered(version, client=writer)
    assert first.status == "OK"
    assert retry.status == "DUPLICATE_OK"
    assert retry.event_id == first.event_id
    assert retry.history_epoch == first.history_epoch
    assert retry.local_sequence == first.local_sequence
    assert len(_rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED)) == 1


def test_restart_repeat_registration_is_idempotent(tmp_path):
    db_path = tmp_path / "canonical.sqlite3"
    first_writer = CanonicalWriter(db_path)
    try:
        first = ensure_instrument_version_registered(_version(), client=first_writer)
    finally:
        first_writer.close()

    reopened = CanonicalWriter(db_path)
    try:
        retry = ensure_instrument_version_registered(_version(), client=reopened)
        assert retry.status == "DUPLICATE_OK"
        assert retry.event_id == first.event_id
        assert len(_rows(reopened, MARKET_INSTRUMENT_VERSION_RECORDED)) == 1
    finally:
        reopened.close()


def test_a_different_version_is_a_distinct_registration(writer):
    ensure_instrument_version_registered(_version(), client=writer)
    second = ensure_instrument_version_registered(_version(version=2), client=writer)
    assert second.instrument_version_id == "INSTR:kraken:SOL:USD:2"
    assert len(_rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED)) == 2


# ---------------------------------------------------------------------------
# Feature Bus stays independent and off
# ---------------------------------------------------------------------------


def test_registration_works_with_feature_bus_off(writer):
    """Registration must not require Feature Bus capture to be enabled."""
    assert feature_bus_capture_enabled() is False
    ref = ensure_instrument_version_registered(_version(), client=writer)
    assert ref.status in COMMITTED_ACK_STATUSES


def test_registration_emits_no_other_feature_bus_event(writer):
    """Only the instrument version is written; no capture plane is touched."""
    ensure_instrument_version_registered(_version(), client=writer)
    assert _other_feature_bus_rows(writer) == {}
    assert _rows(writer, MARKET_OBSERVATION_RECORDED) == []
    assert len(_rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED)) == 1


def test_registration_does_not_change_feature_bus_state(writer):
    before = feature_bus_capture_enabled()
    ensure_instrument_version_registered(_version(), client=writer)
    assert feature_bus_capture_enabled() is before is False


def test_feature_bus_publisher_remains_disabled(writer):
    """The mode-gated publisher is untouched by the Paper v2 path.

    This pins why the narrow path exists: going through the publisher would be a
    no-op (DISABLED) while Feature Bus capture is off.
    """
    publisher = FeatureBusPublisher()
    outcome = publisher.publish_instrument_version(_version())
    assert outcome.status == "DISABLED"
    assert _rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED) == []


# ---------------------------------------------------------------------------
# Fail-closed
# ---------------------------------------------------------------------------


def test_non_instrument_version_fails_closed(writer):
    with pytest.raises(ValueError, match="canonical InstrumentVersion"):
        ensure_instrument_version_registered("not-a-version", client=writer)


@pytest.mark.parametrize("status", ["REJECTED", "RETRYABLE", "SPOOLED", "DISABLED", ""])
def test_non_committed_ack_fails_closed(writer, status):
    class _Client:
        def submit(self, intent):
            return WriterAck(status=status or "REJECTED", error_code="NOT_COMMITTED")

    with pytest.raises(InstrumentRegistrationError, match="not proven committed"):
        ensure_instrument_version_registered(_version(), client=_Client())


def test_ack_without_canonical_identity_fails_closed(writer):
    class _BareClient:
        def submit(self, intent):
            return WriterAck(status="OK", event_id=None)

    with pytest.raises(InstrumentRegistrationError, match="missing canonical identity"):
        ensure_instrument_version_registered(_version(), client=_BareClient())


def test_missing_ack_fails_closed(writer):
    class _SilentClient:
        def submit(self, intent):
            return None

    with pytest.raises(InstrumentRegistrationError, match="not acknowledged"):
        ensure_instrument_version_registered(_version(), client=_SilentClient())


def test_unavailable_writer_propagates_failure(writer):
    class _UnavailableClient:
        def submit(self, intent):
            raise RuntimeError("canonical writer unavailable")

    with pytest.raises(RuntimeError, match="unavailable"):
        ensure_instrument_version_registered(_version(), client=_UnavailableClient())


def test_registered_instrument_id_is_never_invented(writer):
    """A rejection never yields a pseudo-proof object."""
    class _RejectingClient:
        def submit(self, intent):
            return WriterAck(status="REJECTED", error_code="INVALID_INTENT")

    with pytest.raises(InstrumentRegistrationError):
        ensure_instrument_version_registered(_version(), client=_RejectingClient())
    assert _rows(writer, MARKET_INSTRUMENT_VERSION_RECORDED) == []


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------


def test_only_the_frozen_registration_event_type_is_used():
    assert MARKET_INSTRUMENT_VERSION_RECORDED in ACCEPTED_EVENT_TYPES


def test_module_does_not_reach_di_exchange_or_order_surfaces():
    """The registration path stays inside the frozen runtime import boundary.

    It must not import the decision-intelligence plane (which the runtime is
    forbidden from importing) nor any exchange or order-placement surface.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_instrument_registration.py"
    )
    roots: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)

    for forbidden in (
        "app.opip.decision_intelligence",
        "app.exchanges",
        "ccxt",
        "krakenex",
    ):
        assert not any(
            root == forbidden or root.startswith(forbidden + ".") for root in roots
        ), f"instrument registration must not import {forbidden}"

    source = path.read_text(encoding="utf-8").lower()
    for token in ("place_order", "create_order", "submit_order", "kraken_private"):
        assert token not in source
