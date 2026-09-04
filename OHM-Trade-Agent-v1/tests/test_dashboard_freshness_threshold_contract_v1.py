from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.opip.data_platform.freshness import (
    DEGRADED_MAX_AGE_SECONDS,
    DEFAULT_MAX_AGE_SECONDS,
    LIVE_MAX_AGE_SECONDS,
    MaintenanceInput,
    StreamInput,
    classify_freshness,
)


def _stream(now: datetime, age_seconds: int, *, required: bool = True) -> StreamInput:
    stamp = now - timedelta(seconds=age_seconds)
    return StreamInput(
        stream_name="threshold_test",
        required=required,
        requires_typed_projection=False,
        threshold_seconds=None if required else 86400,
        source_updated_at=stamp,
        last_ingested_at=stamp,
        typed_watermark_at=None,
        last_polled_at=stamp,
        unresolved_dead_letters=0,
        last_reconciliation_status="CLEAN" if required else None,
        last_reconciled_at=now if required else None,
    )


def _result(age_seconds: int) -> dict:
    now = datetime.now(timezone.utc)
    return classify_freshness(
        [_stream(now, age_seconds)],
        MaintenanceInput("SUCCESS", now, False),
        now=now,
    )


@pytest.mark.parametrize(
    ("age_seconds", "status", "reason", "ready"),
    [
        (LIVE_MAX_AGE_SECONDS - 1, "LIVE", None, True),
        (LIVE_MAX_AGE_SECONDS + 1, "DEGRADED", "DATA_DELAYED", False),
        (DEGRADED_MAX_AGE_SECONDS + 1, "STALE", "STALE_DATA", False),
        (
            DEFAULT_MAX_AGE_SECONDS + 1,
            "UNAVAILABLE",
            "PER_STREAM_THRESHOLD_EXCEEDED",
            False,
        ),
    ],
)
def test_required_stream_threshold_contract(age_seconds, status, reason, ready):
    result = _result(age_seconds)
    assessment = result["streams"]["threshold_test"]
    assert assessment.status == status
    assert assessment.reason == reason
    assert result["status"] == status
    assert result["ready"] is ready


def test_optional_stale_stream_does_not_gate_required_live_stream():
    now = datetime.now(timezone.utc)
    required = _stream(now, 1, required=True)
    optional = StreamInput(
        stream_name="optional_test",
        required=False,
        requires_typed_projection=False,
        threshold_seconds=86400,
        source_updated_at=now - timedelta(days=2),
        last_ingested_at=now - timedelta(days=2),
        typed_watermark_at=None,
        last_polled_at=now - timedelta(days=2),
        unresolved_dead_letters=0,
        last_reconciliation_status=None,
        last_reconciled_at=None,
    )
    result = classify_freshness(
        [required, optional],
        MaintenanceInput("SUCCESS", now, False),
        now=now,
    )
    assert result["streams"]["optional_test"].status == "STALE"
    assert result["status"] == "LIVE"
    assert result["ready"] is True


@pytest.mark.parametrize(
    ("age_seconds", "status", "reason"),
    [
        (LIVE_MAX_AGE_SECONDS - 1, "LIVE", None),
        (LIVE_MAX_AGE_SECONDS + 1, "DEGRADED", "MAINTENANCE_DELAYED"),
        (DEGRADED_MAX_AGE_SECONDS + 1, "STALE", "MAINTENANCE_STALE"),
        (DEFAULT_MAX_AGE_SECONDS + 1, "UNAVAILABLE", "MAINTENANCE_STALE"),
    ],
)
def test_maintenance_threshold_contract(age_seconds, status, reason):
    now = datetime.now(timezone.utc)
    result = classify_freshness(
        [_stream(now, 1)],
        MaintenanceInput(
            "SUCCESS",
            now - timedelta(seconds=age_seconds),
            False,
        ),
        now=now,
    )
    assert result["maintenance"].status == status
    assert result["maintenance"].reason == reason
    assert result["status"] == status
    assert result["ready"] is (status == "LIVE")
