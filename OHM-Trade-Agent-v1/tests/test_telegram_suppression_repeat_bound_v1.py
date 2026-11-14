"""ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1: an unchanged suppression is not re-written every scan."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.services import telegram_delivery as delivery

pytestmark = pytest.mark.acceptance

T0 = datetime(2026, 11, 12, 0, 0, tzinfo=timezone.utc)
REASON = "ACTIONABLE_GATE:POSITION_CAPACITY_INSUFFICIENT"


def _paths(tmp_path):
    return tmp_path / "state.json", tmp_path / "events.jsonl"


def _record(tmp_path, *, status="SUPPRESSED", reason=REASON, fingerprint="fp", at=T0, failure=None):
    state, events = _paths(tmp_path)
    return delivery._record(  # noqa: SLF001 - clock-controlled access to the ledger writer
        identity="QUALIFIED_OPPORTUNITY:T1",
        alert_family="QUALIFIED_OPPORTUNITY",
        event_type="NEW_TRADE",
        fingerprint=fingerprint,
        status=status,
        symbol="SOL",
        pair=None,
        message_id=None,
        journey_id="J1",
        signal_id="S1",
        trade_id="T1",
        suppression_reason=reason if status in {"SUPPRESSED", "NOT_ELIGIBLE"} else None,
        failure_reason=failure,
        generated_at=at,
        attempted_at=at,
        latency_ms=0,
        state_file=state,
        event_file=events,
    )


def _rows(tmp_path):
    return [json.loads(line) for line in _paths(tmp_path)[1].read_text().splitlines()]


def _alert_state(tmp_path):
    return next(iter(json.loads(_paths(tmp_path)[0].read_text())["alerts"].values()))


def test_ac_001_a_unchanged_suppression_is_bounded_to_one_row_per_window(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-001: two days of five-minute rescans stay bounded."""
    scans = 2 * 24 * 12
    for i in range(scans):
        _record(tmp_path, at=T0 + timedelta(minutes=5 * i))
    rows = _rows(tmp_path)
    assert len(rows) == 2 * 24  # one row per hourly window instead of one per scan (576)
    first, second = rows[0], rows[1]
    assert first["status"] == "SUPPRESSED" and first["suppression_reason"] == REASON
    assert "suppressed_since_last_row" not in first
    assert second["suppressed_since_last_row"] == 11  # the 11 five-minute repeats since the first row
    assert second["attempted_at"] > first["attempted_at"]
    state = _alert_state(tmp_path)
    assert state["attempts"] == 0 and state["last_status"] == "SUPPRESSED"


def test_ac_001_b_every_change_is_written(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-001: a new reason, plan or status is always auditable."""
    _record(tmp_path, at=T0)
    _record(tmp_path, at=T0 + timedelta(minutes=5))  # unchanged -> skipped
    _record(tmp_path, reason="ACTIONABLE_GATE:NET_REWARD_TO_RISK_BELOW_POLICY", at=T0 + timedelta(minutes=6))
    _record(tmp_path, fingerprint="fp2", at=T0 + timedelta(minutes=7))
    _record(tmp_path, status="NOT_ELIGIBLE", reason="PORTFOLIO_OR_ALLOCATION_GUARDRAIL", at=T0 + timedelta(minutes=8))
    assert [r["status"] for r in _rows(tmp_path)] == [
        "SUPPRESSED", "SUPPRESSED", "SUPPRESSED", "NOT_ELIGIBLE"
    ]
    # the changed reason on the same alert is a new row even inside the window
    assert _rows(tmp_path)[1]["suppression_reason"].endswith("NET_REWARD_TO_RISK_BELOW_POLICY")


def test_ac_002_send_attempts_are_never_collapsed(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-002: transient send failures keep every attempt row."""
    for i in range(5):
        result = _record(tmp_path, status="SEND_FAILED", failure="TimeoutError", at=T0 + timedelta(seconds=i))
    rows = _rows(tmp_path)
    assert len(rows) == 5 and [r["attempt"] for r in rows] == [1, 2, 3, 4, 5]
    assert result.attempt == 5 and result.retry_count == 4
    assert all(r["failure_reason"] == "TimeoutError" for r in rows)


def test_ac_002_a_a_send_between_suppressions_resets_the_run(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-002: a delivery between two identical suppressions is not hidden."""
    _record(tmp_path, at=T0)
    _record(tmp_path, at=T0 + timedelta(minutes=1))  # skipped
    _record(tmp_path, status="DELIVERED", at=T0 + timedelta(minutes=2))
    _record(tmp_path, at=T0 + timedelta(minutes=3))  # previous status differs -> written
    assert [r["status"] for r in _rows(tmp_path)] == ["SUPPRESSED", "DELIVERED", "SUPPRESSED"]


def test_ac_003_window_expiry_and_clock_skew_write_the_row(tmp_path, monkeypatch):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-003: window expiry and clock skew always write the row."""
    _record(tmp_path, at=T0)
    _record(tmp_path, at=T0 + timedelta(seconds=delivery.SUPPRESSION_REPEAT_WINDOW_SECONDS - 1))  # skipped
    assert len(_rows(tmp_path)) == 1
    _record(tmp_path, at=T0 + timedelta(seconds=delivery.SUPPRESSION_REPEAT_WINDOW_SECONDS))  # window over
    assert len(_rows(tmp_path)) == 2
    _record(tmp_path, at=T0 - timedelta(hours=5))  # clock went backwards
    assert len(_rows(tmp_path)) == 3
    monkeypatch.setattr(delivery, "SUPPRESSION_REPEAT_WINDOW_SECONDS", 0)
    _record(tmp_path, at=T0 + timedelta(hours=9, seconds=1))
    _record(tmp_path, at=T0 + timedelta(hours=9, seconds=2))
    assert len(_rows(tmp_path)) == 5  # a zero window disables the bound entirely
