"""ATDD — isolated scan lifecycle, fencing and reconciliation (R01/R02/R03).

Covers the invariants that make isolation safe: no overlap, no terminalisation
without observed container evidence, and no false failure of a live async scan.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import scan_attempt as sa
from app.services import scan_preflight as sp
from app.services import scan_supervisor as sup


NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)


def _settings(tmp_path: Path, **overrides):
    base = {
        "opip_scan_state_dir": str(tmp_path / "scan"),
        "opip_scan_execution_mode": "isolated",
        "opip_scan_deadline_seconds": 300,
        "opip_scan_memory_cap_mib": 224,
        "opip_scan_host_reserve_mib": 64,
        "opip_scan_core_memory_ceiling_pct": 70.0,
        "opip_scan_dispatch_token_seconds": 120,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _healthy_snapshot(**overrides) -> sp.ResourceSnapshot:
    base = dict(
        host_available_mib=2048.0,
        core_current_mib=200.0,
        core_max_mib=384.0,
        cpu_pressure_avg10=10.0,
        memory_pressure_avg10=5.0,
        disk_free_mib=20_000.0,
        inodes_free=500_000,
        read_errors=(),
    )
    base.update(overrides)
    return sp.ResourceSnapshot(**base)


# --- AT-R02-01: an active attempt blocks a second attempt ------------------


def test_create_refuses_while_an_attempt_is_active(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)

    with pytest.raises(sa.ScanAttemptError):
        store.create(attempt_id="SCAN:B", deadline_seconds=300, now=NOW)


def test_expired_deadline_alone_never_authorises_overlap(tmp_path: Path):
    """An expired lease is not proof of termination (R02)."""
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=60, now=NOW)

    later = NOW + timedelta(hours=2)
    assert store.active() is not None
    with pytest.raises(sa.ScanAttemptError):
        store.create(attempt_id="SCAN:B", deadline_seconds=300, now=later)


def test_new_attempt_allowed_after_terminal(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    store.terminalize("SCAN:A", sa.OUTCOME_COMPLETED, None, now=NOW)

    record = store.create(attempt_id="SCAN:B", deadline_seconds=300, now=NOW)
    assert record.attempt_id == "SCAN:B"
    assert record.fence > 1, "fencing must advance across attempts"


def test_terminalize_is_idempotent_and_never_conflicts(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    first = store.terminalize("SCAN:A", sa.OUTCOME_FAILED, "CONTAINER_ABSENT", now=NOW)
    second = store.terminalize("SCAN:A", sa.OUTCOME_COMPLETED, None, now=NOW)

    assert first.outcome == sa.OUTCOME_FAILED
    assert second.outcome == sa.OUTCOME_FAILED, "first terminal outcome wins"
    assert store.get("SCAN:A").outcome == sa.OUTCOME_FAILED


def test_partial_is_not_successful(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    record = store.terminalize(
        "SCAN:A", sa.OUTCOME_PARTIAL, "COVERAGE_INCOMPLETE", now=NOW
    )

    assert record.terminal is True
    assert record.successful is False


def test_ledger_is_append_only(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    store.terminalize("SCAN:A", sa.OUTCOME_COMPLETED, None, now=NOW)
    lines = store.ledger_file.read_text(encoding="utf-8").splitlines()

    assert len(lines) == 2
    assert '"event":"CREATE"' in lines[0]
    assert '"event":"TERMINAL"' in lines[1]


# --- AT-R02-02: reconciliation needs attempt-specific post-launch evidence -


def _write_evidence(settings, attempt_id: str, *, launched=True, exit_state=None, exit_at=None):
    """Write dispatcher-style evidence records for one attempt."""
    from app.services.scan_supervisor import attempt_evidence_dir

    directory = attempt_evidence_dir(settings, attempt_id)
    directory.mkdir(parents=True, exist_ok=True)
    if launched:
        (directory / "launch.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "launched_at": (NOW - timedelta(minutes=1)).isoformat(),
                }
            ),
            encoding="utf-8",
        )
    if exit_state is not None:
        (directory / "container_exit.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "attempt_id": attempt_id,
                    "captured_at": (exit_at or NOW).isoformat(),
                    "state": exit_state,
                }
            ),
            encoding="utf-8",
        )
    return directory


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-001")
def test_reconcile_withholds_without_exit_evidence(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-001 liveness needs post-launch exit evidence."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=60, now=NOW)
    _write_evidence(settings, "SCAN:A", launched=True)

    assert sup.reconcile(store, settings=settings, now=NOW + timedelta(hours=1)) == []
    assert store.active() is not None


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-001")
def test_reconcile_terminalises_on_post_launch_exit_evidence(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-001 observed exit closes the attempt."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    _write_evidence(settings, "SCAN:A", exit_state={"ExitCode": 1, "OOMKilled": False})

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=1))

    assert [item.attempt_id for item in reconciled] == ["SCAN:A"]
    assert reconciled[0].outcome == sa.OUTCOME_FAILED
    assert reconciled[0].reason_code == "CONTAINER_EXIT_1"


def test_reconcile_ignores_exit_evidence_older_than_launch(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    _write_evidence(
        settings,
        "SCAN:A",
        exit_state={"ExitCode": 0, "OOMKilled": False},
        exit_at=NOW - timedelta(hours=2),
    )

    assert sup.reconcile(store, settings=settings, now=NOW) == []
    assert store.active() is not None


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-002")
def test_reconcile_classifies_oom_kill(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-002 OOM is classified, not guessed."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    _write_evidence(settings, "SCAN:A", exit_state={"ExitCode": 137, "OOMKilled": True})

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=1))

    assert reconciled[0].outcome == sa.OUTCOME_OOM_KILLED
    assert reconciled[0].reason_code == "CGROUP_OOM_KILL"


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-002")
def test_reconcile_classifies_deadline_kill_as_timeout(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-002 a deadline kill is a timeout."""
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=60, now=NOW)
    _write_evidence(settings, "SCAN:A", exit_state={"ExitCode": 143, "OOMKilled": False})

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=5))

    assert reconciled[0].outcome == sa.OUTCOME_TIMED_OUT
    assert reconciled[0].reason_code == "DEADLINE_KILLED"


def test_reconcile_cancels_attempt_that_was_never_launched(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=60, now=NOW)
    _write_evidence(settings, "SCAN:A", launched=False)

    reconciled = sup.reconcile(store, settings=settings, now=NOW + timedelta(minutes=30))

    assert reconciled[0].outcome == sa.OUTCOME_CANCELLED
    assert reconciled[0].reason_code == sup.REASON_NEVER_LAUNCHED


# --- AT-R08-01: repeated failure suspends discovery, never falls back -----


def test_breaker_opens_after_configured_failures(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    for index in range(sa.BREAKER_FAILURE_THRESHOLD):
        attempt_id = f"SCAN:F{index}"
        store.create(attempt_id=attempt_id, deadline_seconds=60, now=NOW)
        store.terminalize(attempt_id, sa.OUTCOME_FAILED, "CONTAINER_EXIT_1", now=NOW)

    assert store.is_discovery_blocked(NOW) is True
    assert store.is_discovery_blocked(NOW + timedelta(minutes=20)) is False


def test_completed_scan_resets_the_breaker(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=60, now=NOW)
    store.terminalize("SCAN:A", sa.OUTCOME_FAILED, "CONTAINER_EXIT_1", now=NOW)
    store.create(attempt_id="SCAN:B", deadline_seconds=60, now=NOW)
    store.terminalize("SCAN:B", sa.OUTCOME_COMPLETED, None, now=NOW)

    assert store.consecutive_failures() == 0
    assert store.is_discovery_blocked(NOW) is False


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-006")
def test_admission_refused_while_breaker_is_open(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-006 failure suspends discovery, never falls back."""
    settings = _settings(tmp_path)
    store = sup.store_for(settings)
    for index in range(sa.BREAKER_FAILURE_THRESHOLD):
        attempt_id = f"SCAN:F{index}"
        store.create(attempt_id=attempt_id, deadline_seconds=60, now=NOW)
        store.terminalize(attempt_id, sa.OUTCOME_FAILED, "CONTAINER_EXIT_1", now=NOW)

    outcome = sup.evaluate_admission(
        settings=settings, store=store, snapshot=_healthy_snapshot(), now=NOW
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sup.REASON_CIRCUIT_OPEN


# --- AT-R03-02: the index is a projection and can be rebuilt --------------


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-003")
def test_index_rebuilds_from_ledger_after_lost_save(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-003 a lost index save is repaired by replay."""
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    store.terminalize("SCAN:A", sa.OUTCOME_COMPLETED, None, now=NOW)
    # Simulate a crash between ledger append and index save.
    store.index_file.unlink()

    assert store.get("SCAN:A").outcome == sa.OUTCOME_COMPLETED
    assert store.active() is None
    assert store.index_file.exists()


@pytest.mark.acceptance("ATDD-SCANNER-RELIABILITY-M01/AC-003")
def test_stale_index_is_repaired_and_fence_stays_monotonic(tmp_path: Path):
    """ATDD-SCANNER-RELIABILITY-M01/AC-003 fencing stays monotonic after repair."""
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    stale = json.loads(store.index_file.read_text(encoding="utf-8"))
    store.terminalize("SCAN:A", sa.OUTCOME_COMPLETED, None, now=NOW)
    store.index_file.write_text(json.dumps(stale), encoding="utf-8")

    record = store.create(attempt_id="SCAN:B", deadline_seconds=300, now=NOW)

    assert record.fence == 2
    assert store.get("SCAN:A").outcome == sa.OUTCOME_COMPLETED
    assert store.active().attempt_id == "SCAN:B"


def test_torn_ledger_line_does_not_wedge_reading(tmp_path: Path):
    store = sa.ScanAttemptStore(tmp_path / "scan")
    store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)
    with store.ledger_file.open("a", encoding="utf-8") as handle:
        handle.write('{"schema_version":1,"event":"TERMIN')

    assert store.active() is not None
    assert store.get("SCAN:A").attempt_id == "SCAN:A"


# --- AT-R03-01: admission fails closed ------------------------------------


def test_admission_refused_when_host_reserve_insufficient(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sup.store_for(settings)

    outcome = sup.evaluate_admission(
        settings=settings,
        store=store,
        snapshot=_healthy_snapshot(host_available_mib=200.0),
        now=NOW,
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sp.REASON_HOST_RESERVE
    assert store.active() is None, "a refused admission must not reserve an attempt"


def test_admission_refused_on_read_error(tmp_path: Path):
    settings = _settings(tmp_path)
    outcome = sup.evaluate_admission(
        settings=settings,
        store=sup.store_for(settings),
        snapshot=_healthy_snapshot(read_errors=("MEMINFO",)),
        now=NOW,
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sp.REASON_READ_ERROR


def test_admission_refused_under_cpu_pressure(tmp_path: Path):
    settings = _settings(tmp_path)
    outcome = sup.evaluate_admission(
        settings=settings,
        store=sup.store_for(settings),
        snapshot=_healthy_snapshot(cpu_pressure_avg10=95.0),
        now=NOW,
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sp.REASON_CPU_PRESSURE


def test_admission_refused_when_core_cgroup_is_hot(tmp_path: Path):
    settings = _settings(tmp_path)
    outcome = sup.evaluate_admission(
        settings=settings,
        store=sup.store_for(settings),
        snapshot=_healthy_snapshot(core_current_mib=360.0),
        now=NOW,
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sp.REASON_CORE_CEILING


def test_admission_approved_reserves_exactly_one_attempt(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sup.store_for(settings)

    outcome = sup.evaluate_admission(
        settings=settings, store=store, snapshot=_healthy_snapshot(), now=NOW
    )

    assert outcome.admitted is True
    assert outcome.attempt is not None
    assert store.active().attempt_id == outcome.attempt.attempt_id


def test_admission_refused_while_attempt_live(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sup.store_for(settings)
    store.create(attempt_id="SCAN:LIVE", deadline_seconds=300, now=NOW)

    outcome = sup.evaluate_admission(
        settings=settings, store=store, snapshot=_healthy_snapshot(), now=NOW
    )

    assert outcome.admitted is False
    assert outcome.reason_code == sup.REASON_LIVE_ATTEMPT


# --- AT-R02-03: dispatch token and container evidence --------------------


def test_token_round_trip_and_expiry(tmp_path: Path):
    settings = _settings(tmp_path)
    store = sup.store_for(settings)
    attempt = store.create(attempt_id="SCAN:A", deadline_seconds=300, now=NOW)

    sup.write_token(settings, attempt, now=NOW)
    token = sup.read_token(settings)

    assert token["attempt_id"] == "SCAN:A"
    assert sup.token_is_actionable(token, now=NOW + timedelta(seconds=30)) is True
    assert sup.token_is_actionable(token, now=NOW + timedelta(seconds=200)) is False
    assert sup.clear_token(settings) is True
    assert sup.read_token(settings) is None


def test_container_evidence_freshness_bound(tmp_path: Path):
    settings = _settings(tmp_path)
    sup.write_container_evidence(settings, alive_attempt_ids=["SCAN:A"], observed_at=NOW)

    evidence = sup.read_container_evidence(settings)
    assert evidence["alive_attempt_ids"] == ["SCAN:A"]
    assert sup.evidence_is_fresh(
        evidence, settings=settings, now=NOW + timedelta(minutes=1)
    )
    assert not sup.evidence_is_fresh(
        evidence, settings=settings, now=NOW + timedelta(hours=1)
    )
