"""ATDD — manifest integrity, cycle seam, recovery guard and worker authority.

These are the critical corrections: a live asynchronous scan must not be marked
FAILED, suspension must not fall back to the in-process scanner, and the worker
must refuse to run outside isolated mode.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import scan_manifest as sm


def _manifest_payload(**overrides):
    base = {
        "coverage": {"requested": 10, "analyzed": 10, "failed": 0, "skipped": 0},
        "snapshots": {"rows": 10, "sha256": "deadbeef"},
    }
    base.update(overrides)
    return base


# --- AT-R07-03: manifest integrity ---------------------------------------


def test_missing_sentinel_is_incomplete(tmp_path: Path):
    with pytest.raises(sm.ManifestIncomplete):
        sm.read_manifest(tmp_path)


def test_complete_manifest_round_trips(tmp_path: Path):
    sm.write_manifest(tmp_path, _manifest_payload(), complete=True, row_count=10)
    manifest = sm.read_manifest(tmp_path)

    assert manifest["complete"] is True
    assert sm.is_complete(manifest) is True


def test_partial_manifest_is_never_complete(tmp_path: Path):
    payload = _manifest_payload(
        coverage={"requested": 10, "analyzed": 8, "failed": 2, "skipped": 0}
    )
    sm.write_manifest(tmp_path, payload, complete=False, row_count=8)

    assert sm.is_complete(sm.read_manifest(tmp_path)) is False


def test_tampered_manifest_is_rejected(tmp_path: Path):
    sm.write_manifest(tmp_path, _manifest_payload(), complete=True, row_count=10)
    (tmp_path / sm.MANIFEST_NAME).write_text('{"complete": true}', encoding="utf-8")

    with pytest.raises(sm.ManifestCorrupt) as excinfo:
        sm.read_manifest(tmp_path)
    assert excinfo.value.reason == "MANIFEST_CHECKSUM_MISMATCH"


def test_completeness_disagreement_is_rejected(tmp_path: Path):
    sm.write_manifest(tmp_path, _manifest_payload(), complete=True, row_count=10)
    sentinel = tmp_path / sm.SENTINEL_NAME
    sentinel.write_text(
        sentinel.read_text(encoding="utf-8").replace('"complete":true', '"complete":false'),
        encoding="utf-8",
    )

    with pytest.raises(sm.ManifestCorrupt):
        sm.read_manifest(tmp_path)


# --- AT-R02-04: a live asynchronous scan is never marked FAILED ----------


def test_recovery_withheld_while_attempt_is_live(tmp_path: Path, monkeypatch):
    from app.services import operator_control as oc

    state_file = tmp_path / "operator_control.json"
    monkeypatch.setattr(oc, "STATE_FILE", state_file)
    monkeypatch.setattr(oc, "LOCK_FILE", tmp_path / ".operator_control.lock")
    oc._save_state(
        {
            "override_mode": "AUTO",
            "last_search_status": "STARTED",
            "last_search_started_at": "2026-10-29T12:00:00+00:00",
        }
    )

    recovered = oc.recover_interrupted_search(
        now=datetime(2026, 10, 29, 12, 1, tzinfo=timezone.utc),
        live_attempt_probe=lambda: True,
    )

    assert recovered is False
    assert oc._load_state()["last_search_status"] == "STARTED"


def test_recovery_withheld_when_liveness_is_unknown(tmp_path: Path, monkeypatch):
    from app.services import operator_control as oc

    state_file = tmp_path / "operator_control.json"
    monkeypatch.setattr(oc, "STATE_FILE", state_file)
    monkeypatch.setattr(oc, "LOCK_FILE", tmp_path / ".operator_control.lock")
    oc._save_state({"last_search_status": "STARTED"})

    def _boom():
        raise RuntimeError("ledger unreadable")

    recovered = oc.recover_interrupted_search(
        now=datetime(2026, 10, 29, 12, 1, tzinfo=timezone.utc),
        live_attempt_probe=_boom,
    )

    assert recovered is False
    assert oc._load_state()["last_search_status"] == "STARTED"


def test_recovery_proceeds_when_no_attempt_is_live(tmp_path: Path, monkeypatch):
    from app.services import operator_control as oc

    state_file = tmp_path / "operator_control.json"
    monkeypatch.setattr(oc, "STATE_FILE", state_file)
    monkeypatch.setattr(oc, "LOCK_FILE", tmp_path / ".operator_control.lock")
    oc._save_state({"last_search_status": "STARTED"})

    recovered = oc.recover_interrupted_search(
        now=datetime(2026, 10, 29, 12, 1, tzinfo=timezone.utc),
        live_attempt_probe=lambda: False,
    )

    assert recovered is True
    state = oc._load_state()
    assert state["last_search_status"] == "FAILED"
    assert state["last_search_failure_reason"] == "INTERRUPTED_PREVIOUS_CYCLE"


# --- AT-R01-01/R01-02: the cycle never executes the scan -----------------


def _cycle_settings(tmp_path: Path, mode: str):
    return SimpleNamespace(
        opip_scan_execution_mode=mode,
        opip_scan_state_dir=str(tmp_path / "scan"),
        opip_scan_deadline_seconds=300,
        opip_scan_memory_cap_mib=224,
        opip_scan_host_reserve_mib=64,
        opip_scan_core_memory_ceiling_pct=70.0,
        opip_scan_dispatch_token_seconds=120,
        tradingview_v2_enabled=False,
    )


@pytest.fixture()
def cycle_harness(monkeypatch):
    """Isolate the discovery seam from operator state and the real scanner."""
    from app.jobs import run_cycle

    calls = {"scan": 0, "started": 0}

    monkeypatch.setattr(run_cycle, "search_due", lambda decision: True)
    monkeypatch.setattr(
        run_cycle, "mark_search_started", lambda: calls.__setitem__("started", calls["started"] + 1)
    )
    monkeypatch.setattr(run_cycle, "mark_search_finished", lambda status="COMPLETED": None)

    def _fake_scan():
        calls["scan"] += 1

    monkeypatch.setattr(run_cycle, "run_scan_with_telemetry", lambda fn: _fake_scan())
    return run_cycle, calls


def test_suspended_mode_never_falls_back_to_in_process_scanning(
    tmp_path: Path, cycle_harness, monkeypatch
):
    run_cycle, calls = cycle_harness
    settings = _cycle_settings(tmp_path, "suspended")

    executed = run_cycle._run_broad_discovery_if_due(
        decision=SimpleNamespace(effective_mode="SEARCH"),
        entry_watch_ready=False,
        settings=settings,
    )

    assert executed is False
    assert calls["scan"] == 0, "suspension must not run the unbounded scanner"
    assert calls["started"] == 0


def test_isolated_mode_reserves_and_writes_token_without_scanning(
    tmp_path: Path, cycle_harness, monkeypatch
):
    run_cycle, calls = cycle_harness
    settings = _cycle_settings(tmp_path, "isolated")

    from app.services import scan_preflight as sp
    from app.services import scan_supervisor as sup

    monkeypatch.setattr(
        sup,
        "collect_snapshot",
        lambda **kwargs: sp.ResourceSnapshot(
            host_available_mib=2048.0,
            core_current_mib=200.0,
            core_max_mib=384.0,
            cpu_pressure_avg10=10.0,
            disk_free_mib=20_000.0,
            inodes_free=500_000,
        ),
    )

    executed = run_cycle._run_broad_discovery_if_due(
        decision=SimpleNamespace(effective_mode="SEARCH"),
        entry_watch_ready=False,
        settings=settings,
    )

    assert executed is True
    assert calls["scan"] == 0, "the cycle must never run acquisition itself"
    token = sup.read_token(settings)
    assert token is not None and token["attempt_id"].startswith("SCAN:")
    assert sup.store_for(settings).active() is not None


def test_isolated_mode_records_skip_when_preflight_denies(
    tmp_path: Path, cycle_harness, monkeypatch
):
    run_cycle, calls = cycle_harness
    settings = _cycle_settings(tmp_path, "isolated")

    from app.services import scan_preflight as sp
    from app.services import scan_supervisor as sup

    monkeypatch.setattr(
        sup,
        "collect_snapshot",
        lambda **kwargs: sp.ResourceSnapshot(host_available_mib=10.0, read_errors=()),
    )

    executed = run_cycle._run_broad_discovery_if_due(
        decision=SimpleNamespace(effective_mode="SEARCH"),
        entry_watch_ready=False,
        settings=settings,
    )

    assert executed is False
    assert calls["scan"] == 0
    assert sup.read_token(settings) is None
    latest = sup.store_for(settings).latest()
    assert latest is not None and latest.outcome == "SKIPPED"
    assert latest.reason_code == sp.REASON_HOST_RESERVE


def test_legacy_mode_preserves_previous_behaviour(tmp_path: Path, cycle_harness):
    run_cycle, calls = cycle_harness
    settings = _cycle_settings(tmp_path, "legacy_inprocess")

    executed = run_cycle._run_broad_discovery_if_due(
        decision=SimpleNamespace(effective_mode="SEARCH"),
        entry_watch_ready=False,
        settings=settings,
    )

    assert executed is True
    assert calls["scan"] == 1
    assert calls["started"] == 1


# --- AT-R01-03: the worker has no authority outside isolated mode --------


def test_worker_rejects_unknown_attempt(tmp_path: Path, monkeypatch):
    from app.jobs import scan_worker

    monkeypatch.setattr(
        scan_worker, "get_settings", lambda: _cycle_settings(tmp_path, "isolated")
    )
    assert scan_worker.run(attempt_id="SCAN:DOES-NOT-EXIST") == scan_worker.EXIT_REJECTED


def test_worker_rejects_execution_outside_isolated_mode(tmp_path: Path, monkeypatch):
    from app.jobs import scan_worker
    from app.services.scan_attempt import ScanAttemptStore

    settings = _cycle_settings(tmp_path, "legacy_inprocess")
    store = ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:A", deadline_seconds=300)
    monkeypatch.setattr(scan_worker, "get_settings", lambda: settings)

    assert scan_worker.run(attempt_id="SCAN:A") == scan_worker.EXIT_PRECONDITION


def test_worker_rejects_terminal_attempt(tmp_path: Path, monkeypatch):
    from app.jobs import scan_worker
    from app.services.scan_attempt import ScanAttemptStore

    settings = _cycle_settings(tmp_path, "isolated")
    store = ScanAttemptStore(settings.opip_scan_state_dir)
    store.create(attempt_id="SCAN:A", deadline_seconds=300)
    store.terminalize("SCAN:A", "COMPLETED", None)
    monkeypatch.setattr(scan_worker, "get_settings", lambda: settings)

    assert scan_worker.run(attempt_id="SCAN:A") == scan_worker.EXIT_REJECTED
