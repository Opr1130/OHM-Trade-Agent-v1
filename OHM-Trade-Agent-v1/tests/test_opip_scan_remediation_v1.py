"""ATDD — remediation of the reviewed scanner defects (R01–R08).

Covers the corrections required after independent review: attempt-specific
liveness evidence, deadline/exit classification, rebuildable durable state,
result promotion, worker volume isolation, resource bounds and safe suspension.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import scan_attempt as sa
from app.services import scan_manifest as sm
from app.services import scan_result_promotion as promo
from app.services.scan_supervisor import attempt_evidence_dir

INCREMENT = "ATDD-SCANNER-RELIABILITY-M01"
APP_ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _real_now():
    """Manifests are written with a real clock; promotion tests must match it."""
    return datetime.now(timezone.utc)


def _settings(tmp_path: Path, **overrides):
    base = {
        "opip_scan_state_dir": str(tmp_path / "scan"),
        "opip_scan_execution_mode": "isolated",
        "opip_scan_deadline_seconds": 300,
        "signal_quality_scan_interval_seconds": 600,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _snapshot(**overrides):
    from app.scanner.models import MarketSnapshot

    base = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=55.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=1.5,
        technical_score=85,
        trend="UP",
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def _publish(settings, attempt_id: str, *, complete=True, coverage=None, snapshots=None):
    """Write a manifest + snapshots exactly as the worker does."""
    directory = attempt_evidence_dir(settings, attempt_id)
    directory.mkdir(parents=True, exist_ok=True)
    rows = snapshots if snapshots is not None else [_snapshot()]
    lines = b"".join(
        json.dumps(asdict(row), sort_keys=True, allow_nan=False).encode("utf-8") + b"\n"
        for row in rows
    )
    (directory / "snapshots.jsonl").write_bytes(lines)
    from app.services.scan_manifest import sha256_path

    cov = coverage or {
        "requested": len(rows),
        "analyzed": len(rows),
        "failed": 0,
        "skipped": 0,
    }
    attempt = None
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    attempt = store.get(attempt_id)
    sm.write_manifest(
        directory,
        {
            "attempt_id": attempt_id,
            "fence": attempt.fence if attempt else 1,
            "coverage": cov,
            "snapshots": {
                "file": "snapshots.jsonl",
                "rows": len(rows),
                "sha256": sha256_path(directory / "snapshots.jsonl"),
            },
        },
        complete=complete,
        row_count=len(rows),
    )
    return directory


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_promotion_rejects_partial_scan(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 partial acquisition is never qualified."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:P", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:P", sa.OUTCOME_PARTIAL, "COVERAGE_INCOMPLETE", now=NOW)
    _publish(settings, "SCAN:P", complete=False)

    with pytest.raises(promo.PromotionRejected):
        promo.load_promoted(settings=settings, attempt=attempt, now=_real_now())


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_promotion_accepts_verified_manifest(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 a verified complete manifest is consumable."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:C", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:C", sa.OUTCOME_COMPLETED, None, now=NOW)
    _publish(settings, "SCAN:C")

    promoted = promo.load_promoted(settings=settings, attempt=attempt, now=_real_now())

    assert promoted.complete is True
    assert len(promoted.snapshots) == 1
    assert promoted.snapshots[0].symbol == "BTCUSD"
    result = promo.to_scan_result(promoted)
    assert result.analyzed == 1 and result.failed == 0


def test_promotion_rejects_fence_mismatch(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:C", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:C", sa.OUTCOME_COMPLETED, None, now=NOW)
    directory = _publish(settings, "SCAN:C")
    manifest = json.loads((directory / sm.MANIFEST_NAME).read_text(encoding="utf-8"))
    manifest["fence"] = 999
    sm.write_manifest(directory, manifest, complete=True, row_count=1)

    with pytest.raises(promo.PromotionRejected) as excinfo:
        promo.load_promoted(settings=settings, attempt=attempt, now=_real_now())
    assert excinfo.value.reason == "MANIFEST_FENCE_MISMATCH"


def test_promotion_rejects_snapshot_checksum_mismatch(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:C", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:C", sa.OUTCOME_COMPLETED, None, now=NOW)
    directory = _publish(settings, "SCAN:C")
    (directory / "snapshots.jsonl").write_bytes(b'{"symbol":"ETHUSD"}\n')

    with pytest.raises(promo.PromotionRejected) as excinfo:
        promo.load_promoted(settings=settings, attempt=attempt, now=_real_now())
    assert excinfo.value.reason == "SNAPSHOTS_CHECKSUM_MISMATCH"


def test_promotion_rejects_stale_manifest(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:C", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:C", sa.OUTCOME_COMPLETED, None, now=NOW)
    _publish(settings, "SCAN:C")

    with pytest.raises(promo.PromotionRejected) as excinfo:
        promo.load_promoted(
            settings=settings, attempt=attempt, now=_real_now() + timedelta(hours=6)
        )
    assert excinfo.value.reason == "MANIFEST_STALE"


# --- AC-005: worker isolation and resource bounds ------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_worker_service_mounts_only_scan_staging():
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 the scanner cannot write non-acquisition state."""
    compose = (APP_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    block = compose.split("opip-scan-worker:", 1)[1].split("\nnetworks:", 1)[0]

    assert "./data/scan:/app/data/scan" in block
    assert "./data:/app/data" not in block
    assert "env_file" not in block
    assert "read_only: true" in block
    assert "mem_limit: 224m" in block


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_telemetry_bounds_producer_without_newline():
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 an endless unterminated line stays bounded."""
    from app.services import operations_analytics as oa

    capture = oa._BoundedCapture(max_lines=50, max_bytes=4_000)
    for _ in range(500):
        capture.write("x" * 500)

    size = len(capture.getvalue().encode("utf-8"))
    assert size < 20_000, size
    assert capture.dropped_lines > 0


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_batching_is_bounded_and_ordered(monkeypatch):
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 assets are acquired in bounded batches."""
    import concurrent.futures as cf

    from app.scanner import market_scanner

    assets = [SimpleNamespace(primary_pair=f"SYM{index}USD") for index in range(120)]
    monkeypatch.setattr(
        market_scanner,
        "build_kraken_asset_universe",
        lambda client=None, limit=200: SimpleNamespace(assets=assets, usdt_usd_rate=1.0),
    )
    monkeypatch.setattr(
        market_scanner, "analyze_symbol", lambda pair, asset=None: ("ok", _snapshot(symbol=pair), None)
    )
    monkeypatch.setattr(market_scanner, "configured_batch_assets", lambda: 50)
    monkeypatch.setattr(market_scanner, "configured_max_workers", lambda: 4)

    submissions: list[int] = []

    class _SpyPool(cf.ThreadPoolExecutor):
        def __init__(self, max_workers=None, **kwargs):
            super().__init__(max_workers=max_workers, **kwargs)
            submissions.append(0)

        def submit(self, fn, *args, **kwargs):
            submissions[-1] += 1
            return super().submit(fn, *args, **kwargs)

    monkeypatch.setattr(market_scanner, "ThreadPoolExecutor", _SpyPool)

    result = market_scanner.scan_market(limit=200)

    assert result.analyzed == 120
    assert len(submissions) == 3, "three batches of at most fifty"
    assert max(submissions) <= 50

