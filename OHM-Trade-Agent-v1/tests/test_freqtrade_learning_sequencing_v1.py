from __future__ import annotations

import inspect

from app.jobs import scan_movers, scan_opportunities


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
    last_send = source.rfind("send_telegram_message_with_id(", 0, journey)
    last_edit = source.rfind("edit_telegram_message(", 0, journey)
    assert last_send >= 0
    assert last_edit >= 0
    assert last_send < journey
    assert last_edit < journey


def test_journey_capture_is_fail_soft_and_measurement_only():
    source = inspect.getsource(scan_movers.main)
    assert "Intelligence journey watch capture: fail-soft" in source
    helper_source = inspect.getsource(scan_opportunities._publish_freqtrade_paper_opportunities)
    assert "production unaffected" in helper_source
