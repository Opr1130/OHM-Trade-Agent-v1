from __future__ import annotations

import inspect

from types import SimpleNamespace

from app.jobs import run_cycle, scan_movers, scan_opportunities



def test_qualified_signal_lineage_is_created_before_telegram_delivery():
    source = inspect.getsource(scan_opportunities.main)
    lineage = source.index("_prepare_qualified_lineage(")
    send = source.index("send_trade_plan(", lineage)
    publication = source.rfind("_publish_freqtrade_paper_opportunities(")
    assert lineage < send < publication


def test_freqtrade_signal_publication_remains_post_telegram():
    source = inspect.getsource(scan_opportunities.main)
    publication = source.rfind("_publish_freqtrade_paper_opportunities(")
    assert publication > 0
    assert source.rfind("send_trade_plan(", 0, publication) >= 0
    assert source.rfind("Price movement notifications sent:", 0, publication) >= 0


def test_shadow_simulator_is_secondary_to_authoritative_freqtrade_bridge():
    source = inspect.getsource(scan_opportunities.main)
    authoritative = source.rfind("_publish_freqtrade_paper_opportunities(")
    shadow = source.rfind("_maybe_enroll_paper_opportunities(")
    assert authoritative > 0
    assert shadow > authoritative


def test_early_watch_journey_capture_occurs_after_telegram_delivery_loop():
    source = inspect.getsource(scan_movers.main)
    journey = source.index("record_watch_observation(")
    last_send = source.rfind("send_tracked_telegram(", 0, journey)
    last_update = source.rfind("_deliver_existing_card_update(", 0, journey)
    assert last_send >= 0
    assert last_update >= 0
    assert last_send < journey
    assert last_update < journey

    helper_source = inspect.getsource(scan_movers._deliver_existing_card_update)
    assert "send_tracked_telegram(" in helper_source
    assert "edit_tracked_telegram(" in helper_source


def test_journey_capture_is_fail_soft_and_measurement_only():
    source = inspect.getsource(scan_movers.main)
    assert "Intelligence journey watch capture: fail-soft" in source
    helper_source = inspect.getsource(scan_opportunities._publish_freqtrade_paper_opportunities)
    assert "production unaffected" in helper_source



def test_unified_cycle_orders_real_risk_before_early_watch_and_paper():
    source = inspect.getsource(run_cycle._run_cycle_once)
    active = source.index("monitor_active_main()")
    pending = source.index("monitor_pending_main()")
    early = source.index("_run_early_watch_if_due(")
    paper = source.index("_run_paper_monitor_fail_open()")
    broad = source.index("run_scan_with_telemetry(scan_main)")
    assert active < pending < early < paper < broad


def test_early_watch_cadence_runs_once_then_waits(tmp_path, monkeypatch):
    state = tmp_path / "early.json"
    lock = tmp_path / ".early.lock"
    calls = []
    monkeypatch.setattr(run_cycle, "EARLY_WATCH_STATE_FILE", state)
    monkeypatch.setattr(run_cycle, "EARLY_WATCH_LOCK_FILE", lock)
    monkeypatch.setattr(run_cycle, "scan_movers_main", lambda: calls.append(True))
    monkeypatch.setattr(
        run_cycle,
        "datetime",
        SimpleNamespace(
            now=lambda tz: __import__("datetime").datetime(
                2026, 8, 27, 12, 0, tzinfo=__import__("datetime").timezone.utc
            ),
            fromisoformat=__import__("datetime").datetime.fromisoformat,
        ),
    )
    settings = SimpleNamespace(signal_quality_scan_interval_seconds=600)

    run_cycle._run_early_watch_if_due(settings=settings, quiet_hours=False)
    run_cycle._run_early_watch_if_due(settings=settings, quiet_hours=False)
    assert calls == [True]


def test_early_watch_does_not_create_new_quiet_hour_alert_path(tmp_path, monkeypatch):
    monkeypatch.setattr(run_cycle, "EARLY_WATCH_STATE_FILE", tmp_path / "early_watch_scheduler_state.json")
    monkeypatch.setattr(run_cycle, "EARLY_WATCH_LOCK_FILE", tmp_path / ".early_watch_scheduler.lock")
    calls = []
    monkeypatch.setattr(run_cycle, "scan_movers_main", lambda: calls.append(True))

    # Quiet hours must not create an overnight blind spot: the first call is
    # cadence-due and must still run Early Watch.
    run_cycle._run_early_watch_if_due(
        settings=SimpleNamespace(signal_quality_scan_interval_seconds=600),
        quiet_hours=True,
    )
    assert calls == [True]

    # A rapid repeat invocation within the cadence window must not create a
    # second, duplicate scan path. This must hold deterministically on a
    # fresh checkout, not merely when another test happened to run first.
    calls.clear()
    run_cycle._run_early_watch_if_due(
        settings=SimpleNamespace(signal_quality_scan_interval_seconds=600),
        quiet_hours=True,
    )
    assert calls == []
