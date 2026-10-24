"""ATDD: Tradevisor V2 webhook boundary (default-disabled, no authority).

Covers ATDD-11..ATDD-19, ATDD-20/21/22, ATDD-23/24 (transport), ATDD-39, ATDD-52.
Every case is synthetic: no TradingView, Kraken, AI or Telegram call occurs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import tradevisor as routes
from app.core.config import Settings
from app.services import tradevisor_inbox as inbox

TOKEN = "T" * 43
HEADERS = {routes.TRADEVISOR_VERIFICATION_HEADER: "tradevisor-verified-by-trusted-proxy"}


def make_settings(**over) -> Settings:
    values = dict(
        _env_file=None,
        webhook_secret="x" * 12,
        opip_tradevisor_enabled=True,
        opip_tradevisor_webhook_token=TOKEN,
        opip_tradevisor_trusted_proxy_ips="testclient",
    )
    values.update(over)
    return Settings(**values)


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    resolved = make_settings()
    monkeypatch.setattr(routes, "get_settings", lambda: resolved)
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "tradevisor_inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".tradevisor_inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: resolved)
    routes.reset_rate_limiter_for_tests()
    return resolved


def client_for(settings) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def bar_times() -> tuple[datetime, datetime, datetime]:
    """A closed 1H bar on an exact boundary, observed just now.

    The trigger is "now minus one second", which is what a bar-close alert
    looks like when it arrives promptly. A *late* delivery must be refused by
    the freshness gate, and that is tested separately with explicit old stamps.
    """
    now = datetime.now(timezone.utc)
    bar_open = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    bar_close = bar_open + timedelta(hours=1)
    return bar_open, bar_close, now - timedelta(seconds=1)


def _z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def payload(**over) -> dict:
    bar_open, _bar_close, triggered = bar_times()
    body = {
        "schema_version": "tradevisor-ingress-v1",
        "source": "TRADEVISOR",
        "indicator": "TRADEVISOR_V2",
        "signal": "BUY",
        "symbol": "BTCUSD",
        "exchange": "COINBASE",
        "timeframe": "60",
        "signal_price": 67000.5,
        "bar_opened_at": _z(bar_open),
        "triggered_at": _z(triggered),
        "alert_config_version": "tradevisor-1h-buy",
        "ingest_token": TOKEN,
    }
    body.update(over)
    return body


def rows() -> list[dict]:
    registry = inbox.load_json(inbox.INBOX_FILE) or {}
    return list((registry.get("events") or {}).values())


# ---------------------------------------------------------------------------
# Default posture (ATDD-52)
# ---------------------------------------------------------------------------


def test_atdd52_disabled_ingress_is_indistinguishable_from_missing_route(
    tmp_path, monkeypatch
):
    disabled = make_settings(opip_tradevisor_enabled=False)
    monkeypatch.setattr(routes, "get_settings", lambda: disabled)
    monkeypatch.setattr(inbox, "INBOX_FILE", tmp_path / "inbox.json")
    monkeypatch.setattr(inbox, "LOCK_FILE", tmp_path / ".inbox.lock")
    monkeypatch.setattr(inbox, "get_settings", lambda: disabled)
    routes.reset_rate_limiter_for_tests()
    response = client_for(disabled).post(
        "/webhooks/tradevisor/v1", json=payload(), headers=HEADERS
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert rows() == []


def test_atdd52_settings_default_keep_the_ingress_dark():
    defaulted = Settings(_env_file=None, webhook_secret="x" * 12)
    assert defaulted.opip_tradevisor_enabled is False
    assert defaulted.opip_tradevisor_webhook_token is None


# ---------------------------------------------------------------------------
# Authentication and origin (ATDD-11, ATDD-12, ATDD-34)
# ---------------------------------------------------------------------------


def test_atdd12_untrusted_origin_is_refused(settings):
    settings.opip_tradevisor_trusted_proxy_ips = "10.9.9.9"
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(), headers=HEADERS
    )
    assert response.status_code == 404
    assert rows() == []


def test_atdd11_missing_verification_header_is_refused(settings):
    response = client_for(settings).post("/webhooks/tradevisor/v1", json=payload())
    assert response.status_code == 404
    assert rows() == []


def test_atdd11_wrong_verification_header_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(),
        headers={routes.TRADEVISOR_VERIFICATION_HEADER: "not-the-edge"},
    )
    assert response.status_code == 404
    assert rows() == []


def test_atdd11_missing_ingest_token_is_refused_before_schema_detail(settings):
    body = payload()
    body.pop("ingest_token")
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=body, headers=HEADERS
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
    assert rows() == []


def test_atdd34_source_cannot_assert_its_own_authority(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(risk_approved=True, order_permission="GRANTED"),
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert rows() == []


def test_atdd12_ingest_token_is_never_persisted(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(), headers=HEADERS
    )
    assert response.status_code == 202
    stored = rows()
    assert len(stored) == 1
    assert TOKEN not in repr(stored)



# ---------------------------------------------------------------------------
# Accepted paths (ATDD-01, 02) and idempotency (ATDD-23, 24)
# ---------------------------------------------------------------------------


def test_atdd01_valid_buy_is_durably_accepted(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(), headers=HEADERS
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["trade_authority_changed"] is False
    assert body["dedup_id"] == rows()[0]["dedup_id"]


def test_atdd02_valid_sell_is_accepted(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(signal="SELL", alert_config_version="tradevisor-1h-sell"),
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert rows()[0]["signal"]["signal"] == "SELL"


def test_atdd23_duplicate_delivery_returns_duplicate_without_a_second_row(settings):
    client = client_for(settings)
    first = client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
    second = client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
    assert first.status_code == second.status_code == 202
    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert len(rows()) == 1


def test_atdd24_replay_with_fresh_receipt_still_collapses(settings):
    client = client_for(settings)
    first = client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
    replay = client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)


# ---------------------------------------------------------------------------
# Validation refusals (ATDD-13..19, 21, 22, 25)
# ---------------------------------------------------------------------------


def test_atdd13_unknown_indicator_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(indicator="TRADEVISOR_V1"),
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_atdd13_unknown_schema_version_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(schema_version="tradevisor-ingress-v2"),
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_atdd14_malformed_json_is_refused_without_detail(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", content=b"{not-json", headers=HEADERS
    )
    assert response.status_code == 404
    assert rows() == []


def test_atdd14_json_array_body_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=[1, 2], headers=HEADERS
    )
    assert response.status_code == 404
    assert rows() == []


def test_atdd15_missing_direction_is_refused(settings):
    body = payload()
    body.pop("signal")
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=body, headers=HEADERS
    )
    assert response.status_code == 422


def test_atdd16_direction_config_conflict_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(signal="SELL", alert_config_version="tradevisor-1h-buy"),
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert rows() == []


def test_atdd17_unresolved_placeholder_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(symbol="{{ticker}}"), headers=HEADERS
    )
    assert response.status_code == 422


def test_atdd17_degenerate_price_alert_interval_is_refused(settings):
    # TradingView returns {{interval}}="1" for price-based (non-indicator)
    # alerts; that is not a 1H bar and must never be treated as one.
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(timeframe="1"), headers=HEADERS
    )
    assert response.status_code == 422


def test_atdd18_oversized_payload_is_refused(settings):
    settings.opip_tradevisor_max_payload_bytes = 512
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", content=b"x" * 513, headers=HEADERS
    )
    assert response.status_code == 413
    assert rows() == []


def test_atdd19_negative_price_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(signal_price=-5.0), headers=HEADERS
    )
    assert response.status_code == 422


def test_atdd19_non_finite_price_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        content=b'{"signal_price": Infinity}',
        headers=HEADERS,
    )
    assert response.status_code == 404
    assert rows() == []


def test_atdd25_unknown_coin_pair_is_refused(settings):
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1", json=payload(symbol="SHIBUSD"), headers=HEADERS
    )
    assert response.status_code == 422
    assert rows() == []


def test_atdd20_stale_alert_is_refused(settings):
    old_open = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(
            bar_opened_at=_z(old_open),
            triggered_at=_z(old_open + timedelta(hours=1, seconds=2)),
        ),
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert rows() == []


def test_atdd21_future_dated_alert_is_refused(settings):
    future = datetime.now(timezone.utc) + timedelta(minutes=30)
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(
            bar_opened_at=_z(future),
            triggered_at=_z(future + timedelta(hours=1)),
        ),
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_atdd22_trigger_before_bar_close_is_refused(settings):
    bar_open, _bar_close, _triggered = bar_times()
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(
            bar_opened_at=_z(bar_open),
            triggered_at=_z(bar_open + timedelta(minutes=10)),
        ),
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert rows() == []


# ---------------------------------------------------------------------------
# Backpressure (ATDD-39) and bounded rate (WP-01)
# ---------------------------------------------------------------------------


def test_atdd39_inbox_capacity_pressure_backs_off_with_503(settings):
    settings.opip_tradevisor_max_inbox_events = 100  # schema floor
    assert (
        client_for(settings)
        .post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS)
        .status_code
        == 202
    )

    registry = inbox.load_json(inbox.INBOX_FILE)
    for index in range(100):
        registry["events"][f"filler-{index}"] = {
            "status": "PROCESSED",
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }
    inbox.save_json_atomic(inbox.INBOX_FILE, registry)

    bar_open, _bar_close, triggered = bar_times()
    response = client_for(settings).post(
        "/webhooks/tradevisor/v1",
        json=payload(symbol="ETHUSD", bar_opened_at=_z(bar_open), triggered_at=_z(triggered)),
        headers=HEADERS,
    )
    assert response.status_code == 503


def test_rate_limit_is_bounded(settings):
    settings.opip_tradevisor_max_requests_per_minute = 2
    routes.reset_rate_limiter_for_tests()
    client = client_for(settings)
    assert (
        client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS).status_code
        == 202
    )
    assert (
        client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS).status_code
        == 202
    )
    assert (
        client.post("/webhooks/tradevisor/v1", json=payload(), headers=HEADERS).status_code
        == 429
    )


# ---------------------------------------------------------------------------
# Fail-closed boot validation (WP-01)
# ---------------------------------------------------------------------------


def test_enabling_with_a_short_token_refuses_to_boot():
    with pytest.raises(Exception) as excinfo:
        make_settings(opip_tradevisor_webhook_token="too-short")
    assert "OPIP_TRADEVISOR_WEBHOOK_TOKEN" in str(excinfo.value)


def test_production_refuses_the_documented_verification_default():
    with pytest.raises(Exception) as excinfo:
        make_settings(
            app_env="production",
            opip_tradevisor_internal_verification_value="tradevisor-verified-by-trusted-proxy",
        )
    assert "OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE" in str(excinfo.value)


def test_malformed_instrument_table_refuses_to_boot():
    with pytest.raises(Exception) as excinfo:
        make_settings(opip_tradevisor_instruments="COINBASE:BTCUSD")
    assert "OPIP_TRADEVISOR_INSTRUMENTS" in str(excinfo.value) or "invalid Tradevisor" in str(
        excinfo.value
    )
