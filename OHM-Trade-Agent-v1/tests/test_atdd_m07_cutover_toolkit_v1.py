"""Acceptance criteria for the Mission 07 V2 cutover toolkit increment.

Scope contract: ``docs/atdd/scope-contracts/ATDD-M07-V2-cutover-toolkit-v1.md``.

These are contract-level checks that bind the delivered artifacts (host scripts,
offline suite, Linux workflow, evidence set) to the approved criteria. The
behavioural proof of the toolkit itself is the 116-test offline suite under
``deploy/m07/tests``, executed by the ``m07-cutover-toolkit`` Linux workflow;
these checks keep that evidence honest and cheap enough to run with the
application suite on every change.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
M07 = APP / "deploy" / "m07"
BOOTSTRAP = M07 / "scripts" / "v2" / "bootstrap-v2.sh"
ROLLBACK = M07 / "scripts" / "v2" / "rollback-v2.sh"
CONTROLLER = APP / "deploy" / "remote" / "ohm-deploy"
WORKFLOW = REPO / ".github" / "workflows" / "m07-cutover-toolkit.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _compose_overlay_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if re.search(r"docker compose\s+-f|compose\s+-f", line)
        and "PAPER_COMPOSE" not in line
        and not line.strip().startswith("#")
    ]


def _first_compose_file(line: str) -> str | None:
    match = re.search(r"-f\s+(\"[^\"]+\"|'[^']+'|\S+)", line)
    return match.group(1).strip("\"'") if match else None


@pytest.mark.acceptance
def test_ac_001_compose_is_invoked_exactly_as_the_certified_controller():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-001: one Compose source of truth."""
    bootstrap = _read(BOOTSTRAP)
    rollback = _read(ROLLBACK)
    controller = _read(CONTROLLER)

    assert '( cd "$APP_ROOT" && docker compose "$@" )' in bootstrap
    assert "docker compose -f" not in bootstrap
    assert 'cd "$APP_ROOT"' in controller

    for text in (bootstrap, rollback):
        assert "/tmp/opip-v2" not in text, "no owner-staged overlay input"
        assert "nginx-mount.override.yml" not in text, "no generated nginx override"

    overlays = _compose_overlay_lines(rollback)
    assert overlays, "rollback needs its base-first overlays"
    for line in overlays:
        assert _first_compose_file(line) == "docker-compose.yml", line


@pytest.mark.acceptance
def test_ac_002_writer_and_scheduler_activation_are_receipt_gated():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-002: no ungated authority."""
    bootstrap = _read(BOOTSTRAP)

    gate = bootstrap.index('opip_rollback_guard.py" writer-gate')
    start_writer = bootstrap.index(
        'run "start writer" compose up -d --no-deps opip-canonical-writer'
    )
    assert gate < start_writer, "the writer may only start after its receipt gate"

    assert 'compose up -d --no-deps ohm-trade-agent' in bootstrap
    assert "activate-writer" in bootstrap and "activate-scheduler" in bootstrap
    assert '"$LIVE_ACCEPTANCE"' in bootstrap, "scheduler needs live acceptance evidence"
    assert "retire-p1" not in bootstrap
    assert "git clone" not in bootstrap


@pytest.mark.acceptance
def test_ac_003_rollback_freezes_then_snapshots_before_stopping_services():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-003: WAL-safe, newest-data-aware rollback."""
    rollback = _read(ROLLBACK)

    freeze = rollback.index('run "freeze scheduler"')
    snapshot = rollback.index('opip_rollback_guard.py" snapshot')
    stop = rollback.index('run "stop services"')
    assert freeze < snapshot < stop, "freeze, then preserve, then stop"

    assert 'DURABLE="/var/lib/opip-rollback"' in rollback
    assert "WAL-safe canonical snapshot failed verification" in rollback
    assert "live canonical history advanced since prepare" in rollback
    assert 'cp -a "$APP_ROOT/data/opip/canonical"' not in rollback



@pytest.mark.acceptance
def test_ac_004_linux_ci_qualifies_shell_compose_and_the_offline_suite_read_only():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-004: real Linux qualification."""
    document = yaml.safe_load(_read(WORKFLOW))
    assert document["permissions"] == {"contents": "read"}

    job = document["jobs"]["linux-qualification"]
    runs = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "bash -n scripts/v2/bootstrap-v2.sh" in runs
    assert "bash -n scripts/v2/rollback-v2.sh" in runs
    assert "docker compose config --quiet" in runs
    assert "pytest tests/scenarios -q" in runs
    assert "tests/scenarios/test_hf02_recovery_gate.py" in runs
    assert "tests/scenarios/test_r3_concurrency_and_compose.py" in runs
    assert "compose up" not in runs and "ssh" not in runs.lower()

    text = _read(WORKFLOW)
    assert "'OHM-Trade-Agent-v1/deploy/m07/**'" in text
    assert "'OHM-Trade-Agent-v1/docker-compose.yml'" in text


@pytest.mark.acceptance
def test_ac_005_certified_tree_is_portable_and_the_lock_holder_is_genuinely_young():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-005: portable suite, honest lock age."""
    for path in (
        M07 / "tests" / "conftest.py",
        M07 / "tests" / "make_fixture.py",
        M07 / "tests" / "scenarios" / "test_r1_operations.py",
    ):
        text = _read(path)
        assert "opip-mission-07" not in text, path
        assert "C:\\Users" not in text, path

    conftest = _read(M07 / "tests" / "conftest.py")
    assert "OPIP_CERTIFIED_SRC" in conftest
    operations = _read(M07 / "tests" / "scenarios" / "test_r1_operations.py")
    assert "from conftest import CERT_SRC" in operations

    lock_suite = _read(APP / "tests" / "test_opip_export_diagnostics_readonly_v1.py")
    assert "_start_lock_holder" in lock_suite
    assert 'holder = _start_lock_holder(fx["internal"])' in lock_suite
    assert 'fields["internal_export_lock_owner_pid"] == str(holder.pid)' in lock_suite
    # Genuinely old/stalled holders and unconfirmed openers keep their coverage.
    assert "def test_lock_stall_truth_table" in lock_suite
    assert "def test_unconfirmed_opener_is_unknown_never_a_stall" in lock_suite


@pytest.mark.acceptance
def test_ac_006_the_r3_evidence_chain_stays_explicitly_non_deployable():
    """ATDD-M07-V2-cutover-toolkit-v1/AC-006: evidence preserved, verdict explicit."""
    verdict = _read(M07 / "R3-FINAL-VERDICT.md")
    assert "ENGINEERING QUALIFICATION .......... PASS" in verdict
    assert "PRODUCTION READINESS ............... HOLD" in verdict
    assert "AUTHORIZED DEPLOYMENT .............. NO" in verdict

    readme = _read(M07 / "README.md")
    authority = (
        "R3-FINAL-VERDICT.md",
        "R3-RELEASE-QUALIFICATION.md",
        "R3-OWNER-GATES.md",
        "06-SINGLE-CUTOVER-RUNBOOK.md",
        "R3-SECURITY-REVIEW.md",
    )
    positions = [readme.index(name) for name in authority]
    assert positions == sorted(positions), "document authority order is preserved"
    assert "historical" in readme, "earlier R1/R2 evidence stays labelled historical"

    for name in ("R3-OWNER-GATES.md", "R3-SECURITY-REVIEW.md", "SOURCE-PIN.json"):
        assert (M07 / name).is_file(), name
