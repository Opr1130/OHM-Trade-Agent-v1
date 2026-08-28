from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from types import SimpleNamespace

from app.api.dashboard import DASHBOARD_HTML
from app.services import evolution_dashboard as dashboard
from app.services.intelligence_journey import record_watch_observation


NOW = datetime(2026, 8, 28, 1, 0, tzinfo=timezone.utc)


def _event(
    *,
    kind: str,
    journey: str,
    at: datetime,
    signal: str | None = None,
    symbol: str = "SOLUSD",
    payload: dict | None = None,
    delivered: bool = False,
    admitted: bool | None = None,
    reason: str | None = None,
) -> dict:
    row = {
        "event_type": kind,
        "journey_id": journey,
        "symbol": symbol,
        "observed_at": at.isoformat(),
        "payload": payload or {},
    }
    if signal:
        row["signal_id"] = signal
    if kind in {"EARLY_WATCH", "EARLY_MOVER", "BROAD_WATCH"}:
        row["delivered"] = delivered
    if kind == "PAPER_ADMISSION":
        row["admitted"] = bool(admitted)
        row["reason"] = reason or "UNKNOWN"
    return row


def _journey(
    *,
    journey: str,
    signal: str,
    at: datetime,
    pnl: float | None = None,
    paper_requested: bool = True,
) -> list[dict]:
    rows = [
        _event(
            kind="EARLY_WATCH",
            journey=journey,
            signal=None,
            at=at,
            payload={
                "stage": "EARLY_BUILDING",
                "pattern": "COMPRESSION_RELEASE",
            },
            delivered=True,
        ),
        _event(
            kind="QUALIFIED_SIGNAL",
            journey=journey,
            signal=signal,
            at=at + timedelta(minutes=30),
            payload={"paper_requested": paper_requested},
        ),
        _event(
            kind="PAPER_ADMISSION",
            journey=journey,
            signal=signal,
            at=at + timedelta(minutes=31),
            admitted=True,
            reason="ADMITTED",
        ),
    ]
    if pnl is not None:
        rows.append(
            _event(
                kind="PAPER_OUTCOME",
                journey=journey,
                signal=signal,
                at=at + timedelta(hours=2),
                payload={
                    "net_pnl": pnl,
                    "pnl_currency": "USD",
                    "close_profit_ratio": pnl / 1000.0,
                    "pair": "SOL/USD",
                    "exit_reason": "ohm_tp2" if pnl > 0 else "stop_loss",
                },
            )
        )
    return rows


def test_funnel_tracks_early_signal_admission_and_profitability():
    events = _journey(
        journey="JOURNEY:1",
        signal="OHM:1",
        at=NOW - timedelta(days=1),
        pnl=25.0,
    )
    result = dashboard._funnel(events)

    assert result["early_detected"] == 1
    assert result["early_delivered"] == 1
    assert result["qualified_signals"] == 1
    assert result["paper_requested"] == 1
    assert result["paper_admitted"] == 1
    assert result["paper_closed"] == 1
    assert result["paper_profitable"] == 1
    assert result["early_to_signal_pct"] == 100.0
    assert result["requested_to_closed_pct"] == 100.0
    assert result["closed_win_rate_pct"] == 100.0


def test_failure_eradication_distinguishes_new_recurring_and_eradicated():
    prior_at = NOW - timedelta(days=10)
    recent_at = NOW - timedelta(days=2)
    all_events = [
        _event(
            kind="PAPER_ADMISSION",
            journey="J:old-only",
            signal="S:old-only",
            at=prior_at,
            admitted=False,
            reason="GLOBAL_CAPITAL_CAPACITY",
        ),
        _event(
            kind="PAPER_OUTCOME",
            journey="J:repeat-old",
            signal="S:repeat-old",
            at=prior_at,
            payload={
                "net_pnl": -10.0,
                "close_profit_ratio": -0.01,
                "exit_reason": "stop_loss",
            },
        ),
        _event(
            kind="PAPER_OUTCOME",
            journey="J:repeat-new",
            signal="S:repeat-new",
            at=recent_at,
            payload={
                "net_pnl": -5.0,
                "close_profit_ratio": -0.005,
                "exit_reason": "stop_loss",
            },
        ),
        _event(
            kind="PAPER_ADMISSION",
            journey="J:new",
            signal="S:new",
            at=recent_at,
            admitted=False,
            reason="AUTHORITATIVE_CAPACITY_UNAVAILABLE",
        ),
    ]

    result = dashboard._failure_summary(all_events, all_events, now=NOW)
    by_family = {
        row["family"]: row
        for row in result["families_detail"]
    }

    assert by_family[
        "ADMISSION · GLOBAL_CAPITAL_CAPACITY"
    ]["status"] == "ERADICATED"
    assert by_family["TRADE · STOP_LOSS"]["status"] == "RECURRING"
    assert by_family[
        "ADMISSION · AUTHORITATIVE_CAPACITY_UNAVAILABLE"
    ]["status"] == "NEW"
    assert result["repeated_failure_rate_pct"] is not None


def test_dashboard_builder_is_read_only_and_source_failures_are_visible(
    monkeypatch,
    tmp_path,
):
    event_file = tmp_path / "events.jsonl"
    rows = _journey(
        journey="JOURNEY:1",
        signal="OHM:1",
        at=NOW - timedelta(days=1),
        pnl=20.0,
    )
    rows[-1]["measurement_versions"] = {
        "intelligence_stack": "TEST_BASELINE",
    }
    event_file.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(dashboard, "EVENT_FILE", event_file)
    monkeypatch.setattr(dashboard, "_now", lambda: NOW)
    monkeypatch.setattr(
        dashboard,
        "build_operations_summary",
        lambda scope: {
            "market": {
                "pairs_analyzed": 200,
                "technical_shortlist": 8,
                "latest_regime": "RISK_ON",
                "last_scan_utc": (NOW - timedelta(minutes=5)).isoformat(),
                "movement_watch": 3,
                "movement_ready": 2,
                "movement_confirmed": 1,
            },
            "ai": {
                "chief_calls": 3,
                "candidates_reviewed": 7,
                "total_tokens": 1200,
            },
            "current": {},
        },
    )
    monkeypatch.setattr(
        dashboard,
        "freqtrade_dry_run_status",
        lambda: {
            "status": "OK",
            "open_trades": 1,
            "closed_trades": 4,
            "realized_pnl_by_currency": {"USD": 20.0, "USDT": 0.0},
            "active_stake_by_currency": {"USD": 1000.0, "USDT": 0.0},
            "workers": {
                "USD": {"status": "OK"},
                "USDT": {"status": "OK"},
            },
        },
    )
    monkeypatch.setattr(
        dashboard,
        "get_paper_trade_control",
        lambda: SimpleNamespace(
            enabled=True,
            status="OK",
            updated_at=NOW.isoformat(),
            updated_by="TEST",
        ),
    )
    monkeypatch.setattr(
        dashboard,
        "load_tradingview_evidence_diagnostics",
        lambda: {
            "coverage_status": "OK",
            "queue_depth": 0,
        },
    )
    monkeypatch.setattr(
        dashboard,
        "outbox_health",
        lambda: {
            "status": "OK",
            "backlog_rows": 0,
            "processed_through_line": 50,
            "total_rows": 50,
        },
    )
    monkeypatch.setattr(dashboard, "_dead_letter_count", lambda: 0)
    monkeypatch.setattr(
        dashboard,
        "_paper_trade_rows",
        lambda **kwargs: {"open": [], "closed": []},
    )

    result = dashboard.build_evolution_dashboard("30d")

    assert result["read_only"] is True
    assert result["trade_authority_changed"] is False
    assert result["executive"]["paper_status"] == "OK"
    assert result["executive"]["paper_win_rate_pct"] == 100.0
    assert result["funnel"]["early_to_signal_pct"] == 100.0
    assert result["learning_health"]["freqtrade_dedup"] == "SQLITE_PRIMARY_KEY"
    assert result["evolution"]["version_attribution"]["tagged_events"] == 1


def test_measurement_version_metadata_is_written_to_new_journey_events(tmp_path):
    state = tmp_path / "journeys.json"
    events = tmp_path / "events.jsonl"

    record_watch_observation(
        symbol="SOLUSD",
        observed_at=NOW,
        watch_type="EARLY_WATCH",
        payload={"stage": "EARLY_BUILDING"},
        delivery_action="CREATED",
        delivered=True,
        state_file=state,
        event_file=events,
    )

    row = json.loads(events.read_text(encoding="utf-8").splitlines()[0])
    assert row["measurement_versions"]["intelligence_stack"].startswith(
        "OHM_EVOLUTION_BASELINE"
    )
    assert row["measurement_versions"]["failure_taxonomy"] == "FAILURE_TAXONOMY_V1"
    assert row["measurement_only"] is True
    assert row["affects_trade_authority"] is False


def test_dashboard_html_has_graph_first_information_architecture():
    required = [
        "Executive overview",
        "Evolution",
        "Intelligence Monitor",
        "Signal Funnel",
        "Paper Trades",
        "Signal Intelligence",
        "Journeys",
        "Learning Health",
        'id="trendChart"',
        'id="equityChart"',
        'id="overviewFunnel"',
        'class="tip"',
        "/api/analytics/evolution",
        "Read-only analytics",
    ]
    for value in required:
        assert value in DASHBOARD_HTML
