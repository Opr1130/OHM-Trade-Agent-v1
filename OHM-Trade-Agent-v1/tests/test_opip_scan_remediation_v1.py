"""ATDD — remediation of the reviewed scanner defects (R01–R08).

Covers the corrections required after independent review: attempt-specific
liveness evidence, deadline/exit classification, rebuildable durable state,
result promotion, worker volume isolation, resource bounds and safe suspension.
"""

from __future__ import annotations

import json
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
    """Write a manifest + snapshots exactly as the worker does (staging only)."""
    from app.services.scan_supervisor import attempt_staging_dir

    directory = attempt_staging_dir(settings, attempt_id)
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

    assert "./data/scan/staging:/app/data/scan/staging" in block, "staging is writable"
    assert "./data/scan:/app/data/scan:ro" in block, "attempt metadata is read-only"
    assert "./data:/app/data\n" not in block
    assert "env_file" not in block
    assert "read_only: true" in block
    assert "mem_limit: 224m" in block


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_read_only_store_refuses_operational_writes(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 the worker cannot mutate the ledger."""
    writer = sa.ScanAttemptStore(tmp_path / "scan")
    writer.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    ledger_before = writer.ledger_file.read_bytes()

    reader = sa.ScanAttemptStore(tmp_path / "scan", read_only=True)
    assert reader.active().attempt_id == "SCAN:A", "read-only readers still resolve state"

    for call in (
        lambda: reader.create(attempt_id="SCAN:B", deadline_seconds=300, now=NOW),
        lambda: reader.terminalize("SCAN:A", sa.OUTCOME_FAILED, "X", now=NOW),
        lambda: reader.transition("SCAN:A", state=sa.STATE_RUNNING, now=NOW),
        lambda: reader.record_skip(attempt_id="SCAN:C", reason_code="X", now=NOW),
    ):
        with pytest.raises(sa.ScanAttemptError):
            call()

    assert writer.ledger_file.read_bytes() == ledger_before


# --- AC-002: launch request vs successful launch -------------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_launch_failure_is_classified_and_recoverable(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-002 a refused start is classified, not hung."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:L", deadline_seconds=300, now=NOW)
    directory = attempt_evidence_dir(settings, "SCAN:L")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "launch_request.json").write_text(
        json.dumps({"attempt_id": "SCAN:L", "launched_at": NOW.isoformat()}),
        encoding="utf-8",
    )
    (directory / "launch_failed.json").write_text(
        json.dumps(
            {
                "attempt_id": "SCAN:L",
                "observed_containers": 0,
                "reason_code": "DOCKER_START_FAILED",
            }
        ),
        encoding="utf-8",
    )

    from app.services import scan_supervisor as sup

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=1))

    assert reconciled[0].outcome == sa.OUTCOME_FAILED
    assert reconciled[0].reason_code == "LAUNCH_FAILED"
    follow_up = store.create(attempt_id="SCAN:M", deadline_seconds=300, now=NOW)
    assert follow_up.attempt_id == "SCAN:M", "discovery is not blocked by a failed start"


# --- AC-004: crash-recoverable, idempotent promotion ---------------------


def _completed_attempt(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:C", deadline_seconds=300, now=NOW)
    attempt = store.terminalize("SCAN:C", sa.OUTCOME_COMPLETED, None, now=NOW)
    _publish(settings, "SCAN:C")
    return settings, attempt


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_promotion_claim_is_exclusive_and_idempotent(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 one claim, one business outcome."""
    settings, _ = _completed_attempt(tmp_path)

    first, first_reason, token = promo.claim_promotion(settings, "SCAN:C", now=NOW)
    second, second_reason, _ = promo.claim_promotion(
        settings, "SCAN:C", now=NOW + timedelta(seconds=30)
    )
    promo.complete_promotion(settings, "SCAN:C", claim_token=token, now=NOW)
    third, third_reason, _ = promo.claim_promotion(
        settings, "SCAN:C", now=NOW + timedelta(seconds=60)
    )

    assert first is True and first_reason == "CLAIMED" and token
    assert second is False and second_reason == "RECLAIM_BLOCKED_INCONCLUSIVE"
    assert third is False and third_reason == "ALREADY_COMPLETED"


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_expired_lease_never_reclaims_a_possibly_live_worker(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 an expired lease is not proof of death."""
    settings, _ = _completed_attempt(tmp_path)
    promo.claim_promotion(settings, "SCAN:C", now=NOW)

    reclaimed, reason, token = promo.claim_promotion(
        settings,
        "SCAN:C",
        now=NOW + timedelta(seconds=promo.PROMOTION_LEASE_SECONDS + 1),
    )

    assert reclaimed is False
    assert reason == "RECLAIM_BLOCKED_INCONCLUSIVE"
    assert token is None


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_stale_claim_owner_cannot_write_the_outcome(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 a superseded owner is fenced out."""
    settings, _ = _completed_attempt(tmp_path)
    _, _, first_token = promo.claim_promotion(settings, "SCAN:C", now=NOW)
    ok, _, second_token = promo.claim_promotion(
        settings, "SCAN:C", now=NOW, force_reclaim=True
    )
    assert ok is True and second_token != first_token

    with pytest.raises(promo.PromotionRejected) as excinfo:
        promo.complete_promotion(settings, "SCAN:C", claim_token=first_token, now=NOW)
    assert excinfo.value.reason == "CLAIM_FENCE_MISMATCH"

    promo.complete_promotion(settings, "SCAN:C", claim_token=second_token, now=NOW)
    assert promo.read_promotion(settings, "SCAN:C")["state"] == promo.STATE_COMPLETED


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_repeated_promotion_run_qualifies_exactly_once(tmp_path: Path, monkeypatch):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 retries never duplicate qualification."""
    settings, _ = _completed_attempt(tmp_path)
    from app.services import scan_promotion_runner as runner

    monkeypatch.setattr(
        runner, "revalidate_policy", lambda settings, now=None: (True, "APPROVED")
    )
    monkeypatch.setattr(
        runner, "core_headroom_ok", lambda settings: (True, "OK")
    )

    calls: list[int] = []

    def _qualify(scan_result):
        calls.append(1)

    first = runner.run_pending_promotion(settings, now=_real_now(), qualify=_qualify)
    second = runner.run_pending_promotion(settings, now=_real_now(), qualify=_qualify)

    assert first["status"] == runner.STATUS_COMPLETED
    assert second["status"] == runner.STATUS_NONE
    assert len(calls) == 1, "qualification must not run twice for one attempt"


# --- AC-006: policy revalidation and lock separation --------------------


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
@pytest.mark.parametrize(
    "decision, expected",
    [
        ({"effective_mode": "MAINTENANCE", "search_allowed": True, "quiet_hours": False}, "MODE_MAINTENANCE"),
        ({"effective_mode": "MONITOR", "search_allowed": True, "quiet_hours": False}, "MODE_MONITOR"),
        ({"effective_mode": "SEARCH", "search_allowed": False, "quiet_hours": False}, "SEARCH_NOT_ALLOWED"),
        ({"effective_mode": "SEARCH", "search_allowed": True, "quiet_hours": True}, "QUIET_HOURS"),
    ],
)
def test_promotion_refused_when_policy_forbids_output(tmp_path, monkeypatch, decision, expected):
    """ATDD-SCANNER-RELIABILITY-M01/AC-006 stale policy never authorises an entry."""
    settings, _ = _completed_attempt(tmp_path)
    from app.services import operator_control
    from app.services import scan_promotion_runner as runner

    monkeypatch.setattr(
        operator_control,
        "get_operator_decision",
        lambda now=None: SimpleNamespace(**decision),
    )
    calls: list[int] = []
    result = runner.run_pending_promotion(
        settings, now=_real_now(), qualify=lambda scan_result: calls.append(1)
    )

    assert result["status"] == runner.STATUS_REJECTED
    assert result["reason_code"] == expected
    assert calls == [], "no qualification may run when policy forbids it"


@pytest.mark.acceptance(f"{INCREMENT}/AC-006")
def test_qualification_holds_only_its_own_lock(tmp_path, monkeypatch):
    """ATDD-SCANNER-RELIABILITY-M01/AC-006 a hung qualification blocks nothing else."""
    import threading

    from app.services import scan_promotion_runner as runner
    from app.services.registry_io import registry_lock

    settings, _ = _completed_attempt(tmp_path)
    monkeypatch.setattr(
        runner, "revalidate_policy", lambda settings, now=None: (True, "APPROVED")
    )
    monkeypatch.setattr(runner, "core_headroom_ok", lambda settings: (True, "OK"))

    entered = threading.Event()
    release = threading.Event()

    def _hang(scan_result):
        entered.set()
        release.wait(5)

    thread = threading.Thread(
        target=lambda: runner.run_pending_promotion(
            settings, now=_real_now(), qualify=_hang
        ),
        daemon=True,
    )
    thread.start()
    try:
        assert entered.wait(5), "qualification should start"

        qualify_lock = runner.qualify_lock_path(settings)
        with pytest.raises(TimeoutError):
            with registry_lock(qualify_lock, timeout=0.0):
                pass

        # Any other lock — including the unified cycle's — is still available.
        with registry_lock(tmp_path / ".unified_cycle.lock", timeout=0.0):
            pass
    finally:
        release.set()
        thread.join(5)

    # The cycle itself never performs promotion any more.
    from app.jobs import run_cycle

    with pytest.raises(RuntimeError):
        run_cycle._promote_and_qualify(settings=settings)


@pytest.mark.acceptance(f"{INCREMENT}/AC-004")
def test_completed_scan_is_consumed_despite_a_newer_reserved_attempt(tmp_path, monkeypatch):
    """ATDD-SCANNER-RELIABILITY-M01/AC-004 consumption is not tied to the newest attempt."""
    settings, _ = _completed_attempt(tmp_path)
    from app.services import scan_promotion_runner as runner

    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    # Scan B is reserved immediately after scan A completed.
    store.create(attempt_id="SCAN:B", deadline_seconds=300, now=NOW)

    monkeypatch.setattr(
        runner, "revalidate_policy", lambda settings, now=None: (True, "APPROVED")
    )
    monkeypatch.setattr(runner, "core_headroom_ok", lambda settings: (True, "OK"))

    calls: list[str] = []
    result = runner.run_pending_promotion(
        settings,
        now=_real_now(),
        qualify=lambda scan_result: calls.append(scan_result.snapshots[0].symbol),
    )

    assert result["status"] == runner.STATUS_COMPLETED
    assert result["attempt_id"] == "SCAN:C"
    assert calls == ["BTCUSD"]


@pytest.mark.acceptance(f"{INCREMENT}/AC-002")
def test_launch_uncertainty_is_quarantined_not_terminalised(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-002 uncertainty withholds instead of guessing."""
    from app.services import scan_supervisor as sup

    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:Q", deadline_seconds=300, now=NOW)
    directory = attempt_evidence_dir(settings, "SCAN:Q")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "launch_request.json").write_text(
        json.dumps({"attempt_id": "SCAN:Q", "launched_at": NOW.isoformat()}),
        encoding="utf-8",
    )
    (directory / "quarantine.json").write_text(
        json.dumps(
            {"attempt_id": "SCAN:Q", "reason_code": "LAUNCH_UNCERTAIN"},
        ),
        encoding="utf-8",
    )

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(hours=1))

    assert reconciled == []
    assert store.active() is not None, "quarantine must never terminalise"
    with pytest.raises(sa.ScanAttemptError):
        store.create(attempt_id="SCAN:R", deadline_seconds=300, now=NOW)


# --- AC-005: trusted lifecycle evidence is not worker-writable -----------


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_worker_namespace_is_disjoint_from_evidence_namespace(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 staging can never contain evidence."""
    from app.services.scan_supervisor import attempt_evidence_dir, attempt_staging_dir

    settings = _settings(tmp_path)
    staging = attempt_staging_dir(settings, "SCAN:A")
    evidence = attempt_evidence_dir(settings, "SCAN:A")

    assert staging != evidence
    assert staging.parent.name == "staging"
    assert evidence.parent.name == "evidence"
    assert evidence.is_relative_to(staging) is False
    assert staging.is_relative_to(evidence) is False


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_forged_exit_evidence_in_staging_cannot_terminalise(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 a worker-written exit record is ignored."""
    from app.services import scan_supervisor as sup
    from app.services.scan_supervisor import attempt_staging_dir

    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:F", deadline_seconds=300, now=NOW)

    forged = attempt_staging_dir(settings, "SCAN:F")
    forged.mkdir(parents=True, exist_ok=True)
    (forged / "container_exit.json").write_text(
        json.dumps(
            {
                "attempt_id": "SCAN:F",
                "captured_at": NOW.isoformat(),
                "state": {"ExitCode": 0, "OOMKilled": False},
            }
        ),
        encoding="utf-8",
    )
    (forged / "launch_failed.json").write_text(
        json.dumps({"attempt_id": "SCAN:F", "observed_containers": 0}),
        encoding="utf-8",
    )
    (forged / "quarantine.json").write_text(
        json.dumps({"attempt_id": "SCAN:F"}), encoding="utf-8"
    )

    # A legitimate host-side launch record exists: the attempt is genuinely in
    # flight, so only a forged staging record could have "finished" it.
    from app.services.scan_supervisor import attempt_evidence_dir

    evidence = attempt_evidence_dir(settings, "SCAN:F")
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / "launch_request.json").write_text(
        json.dumps({"attempt_id": "SCAN:F", "launched_at": NOW.isoformat()}),
        encoding="utf-8",
    )

    assert sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=1)) == []
    assert store.active() is not None, "admission authority is unchanged by staging writes"
    assert sup.read_exit_evidence(settings, "SCAN:F") is None
    assert sup.read_launch_failure(settings, "SCAN:F") is None
    assert sup.read_quarantine(settings, "SCAN:F") is None


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_forged_promotion_claim_in_staging_cannot_block_or_grant(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-005 a worker-written claim has no authority."""
    from app.services.scan_supervisor import attempt_staging_dir

    settings, _ = _completed_attempt(tmp_path)
    forged = attempt_staging_dir(settings, "SCAN:C")
    (forged / "promotion.json").write_text(
        json.dumps({"attempt_id": "SCAN:C", "state": "COMPLETED"}), encoding="utf-8"
    )

    assert promo.read_promotion(settings, "SCAN:C") is None, "evidence namespace only"
    claimed, reason, token = promo.claim_promotion(settings, "SCAN:C", now=NOW)
    assert claimed is True and reason == "CLAIMED" and token


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

