from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_zero_signal_runtime_diagnostics_are_bounded_and_read_only():
    diagnostics = (ROOT / "deploy/remote/diagnose-opip-learning.sh").read_text(
        encoding="utf-8"
    )

    marker = "# Read-only production runtime evidence for diagnosing a zero-signal state."
    assert marker in diagnostics
    runtime = diagnostics[diagnostics.index(marker) :]

    assert "production_runtime_data=" in runtime
    assert "load_json(STATE_FILE)" in runtime
    assert "get_active_trades()" in runtime
    assert "get_pending_setups()" in runtime
    assert "get_live_order_intents()" in runtime
    assert "SCAN_ACTIVITY_FILE" in runtime
    assert "bounded_scan_tail" in runtime
    assert "max_bytes=1048576" in runtime
    assert 'handle.read(max_bytes)' in runtime
    assert 'timedelta(hours=24)' in runtime

    for field in (
        '"override_mode"',
        '"effective_mode"',
        '"reason"',
        '"quiet_hours"',
        '"search_allowed"',
        '"search_due"',
        '"search_interval_seconds"',
        '"occupied_slots"',
        '"active_trades"',
        '"pending_setups"',
        '"live_order_intents"',
        '"cooldown_until"',
        '"last_search_started_at"',
        '"scan_activity_tail_rows"',
        '"scan_activity_rows_24h"',
        '"scan_activity_read_limit_bytes"',
        '"last_broad_scan_utc"',
        '"last_broad_scan_age_seconds"',
        '"last_broad_scan_requested"',
        '"last_broad_scan_analyzed"',
        '"last_broad_scan_technical_shortlist"',
        '"last_broad_scan_qualified_survivors"',
        '"last_broad_scan_notifications_sent"',
    ):
        assert field in runtime

    # The added block must stay diagnostic-only. In particular it must never
    # evaluate through a state-mutating status path, change the operator mode,
    # run a scanner, send an alert, or mutate paper/trade/exchange state.
    for forbidden in (
        "status_payload(",
        "get_operator_decision(",
        "mark_search_started(",
        "set_override_mode(",
        "save_json_atomic(",
        "_read_jsonl(",
        "scan_market(",
        "scan_main(",
        "send_trade_plan(",
        "send_tracked_telegram(",
        "enroll_paper_opportunity(",
        "publish_qualified_long(",
        "close_trade(",
        "register_order_intent(",
        "docker stop",
        "docker rm",
    ):
        assert forbidden not in runtime


def test_runtime_diagnostics_do_not_dump_environment_or_credentials():
    diagnostics = (ROOT / "deploy/remote/diagnose-opip-learning.sh").read_text(
        encoding="utf-8"
    )
    marker = "# Read-only production runtime evidence for diagnosing a zero-signal state."
    runtime = diagnostics[diagnostics.index(marker) :]

    for forbidden in (
        "os.environ",
        "printenv",
        "env |",
        "OPENAI_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "WEBHOOK_SECRET",
        "KRAKEN_API_KEY",
        "KRAKEN_API_SECRET",
    ):
        assert forbidden not in runtime
