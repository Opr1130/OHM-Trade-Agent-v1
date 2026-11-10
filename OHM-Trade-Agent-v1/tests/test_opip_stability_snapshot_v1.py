"""Focused, offline checks for read-only stability metrics; no production access."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "tools" / "opip_stability_snapshot.py"
_spec = importlib.util.spec_from_file_location("opip_stability_snapshot", SCRIPT)
assert _spec is not None and _spec.loader is not None
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def _put(root: Path, filename: str, rows: list[dict]) -> None:
    (root / filename).write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_counts_are_aggregates_not_raw_records(tmp_path: Path):
    now = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
    stamp = (now - timedelta(hours=1)).isoformat()
    old = (now - timedelta(hours=25)).isoformat()
    _put(tmp_path, "scan_activity.jsonl", [
        {"timestamp_utc": stamp, "outcome": "COMPLETED", "symbol": "PRIVATE"},
        {"timestamp_utc": old, "outcome": "FAILED"},
    ])
    _put(tmp_path, "openai_usage.jsonl", [
        {"timestamp_utc": stamp, "model": "example-model", "total_tokens": 90, "input_tokens": 70, "output_tokens": 20},
    ])
    _put(tmp_path, "opip_ai_router_usage.jsonl", [
        {"timestamp_utc": stamp, "provider": "openai", "status": "FAILED", "error_code": "PROVIDER_TIMEOUT"},
    ])
    _put(tmp_path, "telegram_delivery_events.jsonl", [
        {"record_type": "TELEGRAM_DELIVERY_EVENT", "attempted_at": stamp, "status": "SUPPRESSED", "alert_family": "WATCH", "suppression_reason": "COOLDOWN", "message": "PRIVATE"},
    ])
    output = module.snapshot(tmp_path, now=now)
    assert output["metrics"]["scans"] == {"count": 1, "by_outcome": {"COMPLETED": 1}}
    assert output["metrics"]["chief_openai"]["total_tokens"] == 90
    assert output["metrics"]["chief_openai"]["usd_spend"] is None
    assert output["metrics"]["ai_router"]["by_error_code"] == {"PROVIDER_TIMEOUT": 1}
    assert output["metrics"]["telegram"]["by_suppression_reason"] == {"COOLDOWN": 1}
    assert "PRIVATE" not in json.dumps(output)


def test_missing_ledgers_are_unknown_not_zero_coverage(tmp_path: Path):
    output = module.snapshot(tmp_path, now=datetime(2026, 11, 10, tzinfo=timezone.utc))
    assert output["coverage"] == "INCOMPLETE"
    assert all(not m["present"] for m in output["ledgers"].values())
    assert output["metrics"]["scans"]["count"] == 0


def test_large_ledger_tail_is_bounded_and_declares_truncation(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    path.write_text("x" * 6000 + "\n" + json.dumps({
        "attempted_at": "2026-11-10T00:00:00+00:00",
        "status": "DELIVERED"
    }) + "\n")
    rows, meta = module.recent_rows(
        path, cutoff=datetime(2026, 11, 9, tzinfo=timezone.utc), max_bytes=1024
    )
    assert meta["truncated"] is True
    assert [r["status"] for r in rows] == ["DELIVERED"]


def test_invalid_window_is_rejected(tmp_path: Path):
    import pytest
    with pytest.raises(ValueError):
        module.snapshot(tmp_path, hours=0)
