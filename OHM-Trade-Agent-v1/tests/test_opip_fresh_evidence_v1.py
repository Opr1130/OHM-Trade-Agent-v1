"""Focused evidence-only acceptance checks."""
from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "opip_fresh_evidence.py"
SPEC = importlib.util.spec_from_file_location("opip_fresh_evidence", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collector)
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.mark.acceptance
def test_fresh_outcomes_are_immutable_observations_not_realized_fills(tmp_path):
    # Activation is prospective: no pre-activation rows are imported.
    collector.capture(tmp_path, now=NOW)
    _json(tmp_path / "trade_outcomes.json", {
        "xrp": {
            "record_key": "xrp", "symbol": "XRPUSD", "direction": "SHORT",
            "recommendation_timestamp": (NOW + timedelta(seconds=3)).isoformat(),
            "target_1_observed": True, "entry_price": 1.08,
            "terminal_status": "TARGET_1", "net_profit": 1000,
            "credential": "NEVER_COPY",
        }
    })
    later = collector.capture(tmp_path, now=NOW + timedelta(seconds=4))
    assert later["new_rows"] == 1
    item = json.loads((tmp_path / "opip/fresh_evidence/events.jsonl").read_text())
    assert item["evidence"]["realized_net_profit_usd"] is None
    assert item["evidence"]["fill_verification"] == "NOT_CONNECTED"
    assert "NEVER_COPY" not in json.dumps(item)
    assert "net_profit" not in item["evidence"]
    assert item["trade_authority_changed"] is False


@pytest.mark.acceptance
def test_checkpoint_dedup_and_updated_outcome(tmp_path):
    _json(tmp_path / "trade_outcomes.json", {})
    collector.capture(tmp_path, now=NOW)
    row = {"record_key": "one", "symbol": "BTCUSD", "direction": "LONG",
           "recommendation_timestamp": (NOW + timedelta(minutes=1)).isoformat()}
    _json(tmp_path / "trade_outcomes.json", {"one": row})
    assert collector.capture(tmp_path, now=NOW + timedelta(minutes=2))["new_rows"] == 1
    assert collector.capture(tmp_path, now=NOW + timedelta(minutes=3))["new_rows"] == 0
    row["target_1_observed"] = True
    row["terminal_timestamp"] = (NOW + timedelta(minutes=3)).isoformat()
    _json(tmp_path / "trade_outcomes.json", {"one": row})
    assert collector.capture(tmp_path, now=NOW + timedelta(minutes=4))["new_rows"] == 1
    assert len((tmp_path / "opip/fresh_evidence/events.jsonl").read_text().splitlines()) == 2


@pytest.mark.acceptance
def test_missing_sources_and_unknown_manual_pnl(tmp_path):
    result = collector.capture(tmp_path, now=NOW)
    assert all(v == "MISSING" for v in result["coverage"].values())
    assert result["training"] == "OFF" and result["execution"] == "OFF"
    assert not (tmp_path / "opip/fresh_evidence/events.jsonl").exists()


@pytest.mark.acceptance
def test_refuses_oversized_sources_without_checkpoint_advancement(tmp_path):
    collector.capture(tmp_path, now=NOW)
    before = (tmp_path / "opip/fresh_evidence/state.json").read_bytes()
    (tmp_path / "trade_outcomes.json").write_text("x" * 1200)
    with pytest.raises(ValueError, match="bounded"):
        collector.capture(tmp_path, now=NOW + timedelta(minutes=2), max_bytes=1024)
    assert (tmp_path / "opip/fresh_evidence/state.json").read_bytes() == before


@pytest.mark.acceptance
def test_detects_telegram_delivery_and_tradevisor_receipt_without_secrets(tmp_path):
    collector.capture(tmp_path, now=NOW)
    _json(tmp_path / "tradevisor_v1_inbox.json", {"events": {
        "r1": {"dedup_id": "r1", "received_at": (NOW + timedelta(minutes=1)).isoformat(),
            "decision": "ENTER_NOW", "signal": {"market": "XRPUSD", "signal": "SELL",
            "triggered_at": NOW.isoformat(), "api_key": "REDACT"}}
    }})
    (tmp_path / "telegram_delivery_events.jsonl").write_text(json.dumps({
        "alert_id": "a1", "attempt": 1, "pair": "XRPUSD", "status": "DELIVERED",
        "attempted_at": (NOW + timedelta(minutes=1)).isoformat(),
        "bot_token": "REDACT"
    }) + "\n")
    result = collector.capture(tmp_path, now=NOW + timedelta(minutes=2))
    assert result["new_rows"] == 2
    text = (tmp_path / "opip/fresh_evidence/events.jsonl").read_text()
    assert "REDACT" not in text
    assert "XRPUSD" in text


@pytest.mark.acceptance
def test_rejects_malformed_source_without_advancing_state(tmp_path):
    collector.capture(tmp_path, now=NOW)
    before = (tmp_path / "opip/fresh_evidence/state.json").read_bytes()
    (tmp_path / "telegram_delivery_events.jsonl").write_text('{"broken":')
    with pytest.raises(json.JSONDecodeError):
        collector.capture(tmp_path, now=NOW + timedelta(minutes=1))
    assert (tmp_path / "opip/fresh_evidence/state.json").read_bytes() == before
