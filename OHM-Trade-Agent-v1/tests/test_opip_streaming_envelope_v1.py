"""BUILD 4.1 — canonical streaming envelope contract."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.events.contract import MappingStatus
from app.opip.streaming.contract import STREAMING_SCHEMA_VERSION, SequenceStatus, StreamProvider, StreamType
from app.opip.streaming.envelope import StreamEnvelope


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _envelope(**overrides) -> StreamEnvelope:
    fields = dict(
        provider=StreamProvider.BINANCE,
        stream_type=StreamType.AGG_TRADE,
        provider_symbol="BTCUSDT",
        provider_timestamp_utc=NOW,
        ingest_timestamp_utc=NOW + timedelta(milliseconds=20),
        connection_id="conn-1",
        reconnect_epoch=0,
        sequence_status=SequenceStatus.FIRST,
        is_aggregate=True,
    )
    fields.update(overrides)
    return StreamEnvelope(**fields)


def test_schema_round_trip():
    env = _envelope(
        sequence_status=SequenceStatus.GAP,
        gap_before=True,
        identity_status=MappingStatus.UNIQUE,
        canonical_asset_id="bitcoin",
        payload={"price": "60000.5"},
        quality={"note": "ok"},
    )
    restored = StreamEnvelope.from_dict(env.to_dict())
    assert restored == env


def test_deterministic_serialization_is_stable():
    env = _envelope()
    assert env.canonical_bytes() == env.canonical_bytes()
    restored = StreamEnvelope.from_dict(env.to_dict())
    assert restored.canonical_bytes() == env.canonical_bytes()


def test_utc_enforcement_rejects_naive_timestamps():
    with pytest.raises(ValueError):
        _envelope(provider_timestamp_utc=datetime(2026, 8, 29, 12, 0))
    with pytest.raises(ValueError):
        _envelope(ingest_timestamp_utc=datetime(2026, 8, 29, 12, 0))


def test_missing_identity_defaults_to_unknown_with_no_canonical_id():
    env = _envelope()
    assert env.identity_status == MappingStatus.UNKNOWN
    assert env.canonical_asset_id is None
    assert env.has_unique_identity is False


def test_canonical_id_requires_unique_identity_status():
    with pytest.raises(ValueError):
        _envelope(identity_status=MappingStatus.AMBIGUOUS, canonical_asset_id="bitcoin")
    with pytest.raises(ValueError):
        _envelope(identity_status=MappingStatus.UNKNOWN, canonical_asset_id="bitcoin")


def test_unique_identity_requires_a_canonical_id():
    with pytest.raises(ValueError):
        _envelope(identity_status=MappingStatus.UNIQUE, canonical_asset_id=None)


def test_malformed_provider_timestamp_rejected_on_deserialize():
    payload = _envelope().to_dict()
    payload["provider_timestamp_utc"] = "not-a-timestamp"
    with pytest.raises(ValueError):
        StreamEnvelope.from_dict(payload)


def test_reconnect_epoch_must_be_non_negative():
    with pytest.raises(ValueError):
        _envelope(reconnect_epoch=-1)


@pytest.mark.parametrize(
    "status,flags",
    [
        (SequenceStatus.FIRST, dict(gap_before=False, out_of_order=False, duplicate=False)),
        (SequenceStatus.CONTIGUOUS, dict(gap_before=False, out_of_order=False, duplicate=False)),
        (SequenceStatus.DUPLICATE, dict(gap_before=False, out_of_order=False, duplicate=True)),
        (SequenceStatus.GAP, dict(gap_before=True, out_of_order=False, duplicate=False)),
        (SequenceStatus.OUT_OF_ORDER, dict(gap_before=False, out_of_order=True, duplicate=False)),
    ],
)
def test_flags_must_match_sequence_status(status, flags):
    # Correct combination succeeds.
    _envelope(sequence_status=status, **flags)
    # Any single flipped flag is rejected as an inconsistent record.
    for key in flags:
        bad = dict(flags)
        bad[key] = not bad[key]
        with pytest.raises(ValueError):
            _envelope(sequence_status=status, **bad)


def test_empty_provider_symbol_rejected():
    with pytest.raises(ValueError):
        _envelope(provider_symbol="   ")


def test_empty_connection_id_rejected():
    with pytest.raises(ValueError):
        _envelope(connection_id="")


def test_unsupported_schema_version_rejected():
    with pytest.raises(ValueError):
        _envelope(schema_version=STREAMING_SCHEMA_VERSION + 1)


def test_payload_and_quality_defaults_are_not_shared_between_instances():
    a = _envelope()
    b = _envelope()
    assert a.payload is not b.payload
    assert a.quality is not b.quality
