"""PR-A: a lost paper evidence append must never be silent.

The terminal paper event is the only durable record that a lifecycle reached a
terminal state, and it is the shipper's source for analytics. Before PR-A the
append path caught its own I/O error and returned, leaving operational state
complete (CLOSED) while evidence was missing with nothing to repair from.

These tests pin the replacement contract: the failure is reported to the
caller, recorded durably, and visible as an incomplete evidence window - while
still never raising into the trading path.
"""
from __future__ import annotations

from app.opip.canonical.gap_spool import evidence_window_incomplete, load_gap_spool
from app.opip.canonical.paths import gap_spool_path
from app.services import paper_trade_registry
from app.services.paper_trade_models import PaperTradeLifecycle

PAPER_ID = "PAPER:" + "a" * 20


def _lifecycle(**overrides) -> PaperTradeLifecycle:
    values = {
        "paper_trade_id": PAPER_ID,
        "episode_id": "EP:test-1",
        "cohort_id": "COH:test-1",
        "symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_action": "MARKET_DECISION_TIME",
        "signal_at": "2026-09-16T12:00:00+00:00",
        "created_at": "2026-09-16T12:00:00+00:00",
        "updated_at": "2026-09-16T13:00:00+00:00",
        "entry_low": 100.0,
        "entry_high": 101.0,
        "entry_limit": 100.5,
        "chase_limit": 102.0,
        "stop_price": 98.0,
        "target_1": 104.0,
        "target_2": 108.0,
        "risk_level": "medium",
        "confidence": 70,
        "profit_rank": 1,
        "profit_rank_score": 80.0,
        "capital": 1000.0,
        "fee_rate": 0.004,
        "slippage_bps": 10.0,
        "tp1_fraction": 0.5,
        "pending_ttl_hours": 24,
        "max_hold_hours": 24,
        "reference_price": 100.5,
        "exit_reason": "STOP",
        "exit_price": 98.0,
        "gross_pnl": -20.0,
        "net_pnl": -24.0,
        "net_pnl_pct": -2.4,
    }
    values.update(overrides)
    return PaperTradeLifecycle(**values)


def _unwritable_events(tmp_path):
    """A directory where the event file should be, so the append must fail."""
    events = tmp_path / "events.jsonl"
    events.mkdir()
    return events


def test_successful_append_reports_success_and_records_no_gap(tmp_path):
    events = tmp_path / "events.jsonl"
    spool = tmp_path / "evidence_gap_spool.json"

    appended = paper_trade_registry._append_event(
        _lifecycle(), "CLOSED_STOP", event_file=events, gap_spool_file=spool
    )

    assert appended is True
    assert events.read_text(encoding="utf-8").strip()
    assert load_gap_spool(spool)["unresolved"] == []
    assert evidence_window_incomplete(spool) is False


def test_lost_append_reports_failure_and_records_durable_disposition(tmp_path):
    spool = tmp_path / "evidence_gap_spool.json"

    appended = paper_trade_registry._append_event(
        _lifecycle(), "CLOSED_STOP", event_file=_unwritable_events(tmp_path), gap_spool_file=spool
    )

    assert appended is False
    unresolved = load_gap_spool(spool)["unresolved"]
    assert len(unresolved) == 1
    entry = unresolved[0]
    assert entry["identity"] == PAPER_ID
    assert entry["intended_event_type"] == "CLOSED_STOP"
    assert entry["error_code"] == paper_trade_registry.PAPER_EVENT_APPEND_FAILED
    assert entry["retry_count"] == 0


def test_lost_append_marks_evidence_window_incomplete(tmp_path):
    """A CLOSED lifecycle with a gap means evidence is incomplete, not complete."""
    spool = tmp_path / "evidence_gap_spool.json"

    paper_trade_registry._append_event(
        _lifecycle(), "CLOSED_STOP", event_file=_unwritable_events(tmp_path), gap_spool_file=spool
    )

    assert evidence_window_incomplete(spool) is True


def test_disposition_failure_never_propagates_into_the_trading_path(tmp_path, monkeypatch):
    """Even an unavailable spool must not raise out of the paper path."""
    def _boom(**kwargs):
        raise OSError("spool unavailable")

    monkeypatch.setattr(paper_trade_registry, "append_capture_gap", _boom)

    appended = paper_trade_registry._append_event(
        _lifecycle(),
        "CLOSED_STOP",
        event_file=_unwritable_events(tmp_path),
        gap_spool_file=tmp_path / "evidence_gap_spool.json",
    )

    assert appended is False


def test_repeated_losses_never_evict_earlier_gaps(tmp_path):
    """Gaps must accumulate; pruning unresolved evidence is not allowed."""
    spool = tmp_path / "evidence_gap_spool.json"
    events = _unwritable_events(tmp_path)

    for _ in range(3):
        paper_trade_registry._append_event(
            _lifecycle(), "CLOSED_STOP", event_file=events, gap_spool_file=spool
        )

    assert len(load_gap_spool(spool)["unresolved"]) == 3


def test_paper_gap_spool_is_separate_from_the_canonical_spool(tmp_path):
    """A paper evidence failure must not mark the alert governor's window incomplete."""
    assert paper_trade_registry.EVIDENCE_GAP_SPOOL_FILE != gap_spool_path()
    assert "paper_trading" in str(paper_trade_registry.EVIDENCE_GAP_SPOOL_FILE)


def test_disposition_does_not_carry_payload_or_secret_fields(tmp_path):
    """The spool stores a descriptor only - never the event body."""
    spool = tmp_path / "evidence_gap_spool.json"

    paper_trade_registry._append_event(
        _lifecycle(), "CLOSED_STOP", event_file=_unwritable_events(tmp_path), gap_spool_file=spool
    )

    entry = load_gap_spool(spool)["unresolved"][0]
    allowed = {
        "gap_id",
        "idempotency_key",
        "scan_id",
        "identity",
        "intended_event_type",
        "observed_at",
        "error_code",
        "retry_count",
    }
    assert set(entry) == allowed
