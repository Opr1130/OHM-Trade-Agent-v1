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
    return tmp_path / "telegram_delivery_state.json", tmp_path / "events.jsonl"


def _record(tmp_path, *, status="SUPPRESSED", reason=REASON, fingerprint="fp", at=T0, failure=None,
            family="QUALIFIED_OPPORTUNITY"):
    state, events = _paths(tmp_path)
    return delivery._record(  # noqa: SLF001 - clock-controlled access to the ledger writer
        identity=f"{family}:T1",
        alert_family=family,
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


def test_ac_004_other_alert_families_keep_one_row_per_occurrence(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-004: incident alerts keep their auditable per-occurrence rows."""
    for i in range(5):
        _record(tmp_path, family="SYSTEM_HEALTH", reason="INCIDENT_ALREADY_NOTIFIED", at=T0 + timedelta(seconds=i))
    rows = _rows(tmp_path)
    assert len(rows) == 5 and all("suppressed_since_last_row" not in row for row in rows)
    assert delivery.BOUNDED_SUPPRESSION_FAMILIES == frozenset({"QUALIFIED_OPPORTUNITY"})


def test_ac_005_summary_counts_collapsed_repeats_under_the_verdict_they_repeated(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-005: the summary does not undercount collapsed suppressions."""
    other = "ACTIONABLE_GATE:SPREAD_TOO_WIDE"
    for i in range(12):  # one row, eleven collapsed repeats of REASON
        _record(tmp_path, at=T0 + timedelta(minutes=5 * i))
    _record(tmp_path, reason=other, at=T0 + timedelta(minutes=60))  # verdict changes: a row carries the 11
    rows = _rows(tmp_path)
    assert len(rows) == 2
    assert rows[1]["suppressed_since_last_row"] == 11
    assert rows[1]["suppressed_since_last_row_reason"] == REASON, "carried repeats keep their own verdict"
    assert rows[1]["suppression_reason"] == other
    summary = delivery.build_delivery_summary(path=_paths(tmp_path)[1])
    assert summary["events"] == 2
    assert summary["suppressed"] == 13 and summary["collapsed_suppression_repeats"] == 11
    assert summary["by_suppression_reason"] == {REASON: 12, other: 1}
    assert summary["by_status"] == {"SUPPRESSED": 13}
    for i in range(1, 4):  # three more repeats of the new verdict stay pending in state
        _record(tmp_path, reason=other, at=T0 + timedelta(minutes=60 + 5 * i))
    pending = delivery.build_delivery_summary(path=_paths(tmp_path)[1])
    assert len(_rows(tmp_path)) == 2
    assert pending["suppressed"] == 16 and pending["collapsed_suppression_repeats"] == 14
    assert pending["by_suppression_reason"] == {REASON: 12, other: 4}
    _record(tmp_path, reason=other, at=T0 + timedelta(minutes=125))  # window elapsed: flushed to a row
    flushed = delivery.build_delivery_summary(path=_paths(tmp_path)[1])
    assert flushed["suppressed"] == 17, "flushing must not count a repeat twice"
    assert flushed["by_suppression_reason"] == {REASON: 12, other: 5}


def _tree(path):
    return {p.name: p.read_bytes() for p in path.iterdir()}


@pytest.mark.parametrize("damage", [b'{"alerts": {', b"[1, 2]", b'{"alerts": {"a": {"suppressed_repeat_count": NaN}}}'])
def test_ac_006_summary_never_moves_or_rewrites_a_malformed_state_file(tmp_path, damage):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-006: a read-only summary leaves malformed state untouched."""
    for i in range(3):
        _record(tmp_path, at=T0 + timedelta(minutes=5 * i))
    state, events = _paths(tmp_path)
    state.write_bytes(damage)
    before = _tree(tmp_path)
    for scope in ("all", "today"):
        summary = delivery.build_delivery_summary(scope=scope, path=events, now=T0)
        assert summary["collapsed_repeats_state_readable"] is False
        assert summary["events"] >= 0
    assert _tree(tmp_path) == before, "no rename, quarantine copy, lock file or rewrite"
    assert not list(tmp_path.glob("*corrupt*"))
    missing = tmp_path / "empty"
    missing.mkdir()
    assert delivery.build_delivery_summary(path=missing / "events.jsonl")["collapsed_repeats_state_readable"] is True
    assert list(missing.iterdir()) == []


def test_ac_007_a_day_scoped_summary_never_credits_a_run_that_began_yesterday(tmp_path):
    """ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1/AC-007: collapsed runs are attributed by when they began."""
    day = datetime(2026, 11, 12, tzinfo=timezone.utc)
    other = "ACTIONABLE_GATE:SPREAD_TOO_WIDE"
    _record(tmp_path, at=day.replace(hour=23, minute=30))  # run begins on day D
    for minute in (35, 40, 45):
        _record(tmp_path, at=day.replace(hour=23, minute=minute))  # three repeats on day D
    tomorrow = day + timedelta(days=1)
    _record(tmp_path, reason=other, at=tomorrow.replace(minute=5))  # day D+1 row carries the 3
    _record(tmp_path, reason=other, at=tomorrow.replace(minute=10))  # a repeat that begins today: pending
    noon = tomorrow.replace(hour=12)
    today = delivery.build_delivery_summary(scope="today", path=_paths(tmp_path)[1], now=noon)
    assert today["events"] == 1
    assert today["collapsed_repeats_unattributed_to_day"] == 3, "yesterday's run is reported, not credited"
    assert today["collapsed_suppression_repeats"] == 1 and today["suppressed"] == 2
    assert today["by_suppression_reason"] == {other: 2}
    everything = delivery.build_delivery_summary(scope="all", path=_paths(tmp_path)[1], now=noon)
    assert everything["collapsed_repeats_unattributed_to_day"] == 0
    assert everything["events"] == 2 and everything["suppressed"] == 6  # 2 rows + 3 carried + 1 pending
    assert everything["by_suppression_reason"][REASON] == 1 + 3
    assert everything["by_suppression_reason"][other] == 2
    yesterday = delivery.build_delivery_summary(scope="today", path=_paths(tmp_path)[1], now=day.replace(hour=23, minute=59))
    assert yesterday["events"] == 1 and yesterday["by_suppression_reason"] == {REASON: 1}
    assert yesterday["collapsed_repeats_unattributed_to_day"] == 0
