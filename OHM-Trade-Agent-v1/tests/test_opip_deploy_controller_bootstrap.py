"""Deploy-controller bootstrap preservation (ATDD-RELEASE-PIPELINE-v1 / AC-014).

Proves the target-SHA scheduler reconcile can replace only the in-flight
deploy-controller snapshot, and that every failed proof leaves that snapshot
and the surrounding rollback files untouched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.release_profiles import RELEASE_PROFILES

APP_ROOT = Path(__file__).resolve().parents[1]
RECONCILE = APP_ROOT / "deploy" / "remote" / "reconcile-scheduler.sh"
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
COMPOSE = APP_ROOT / "docker-compose.yml"

OLD_CONTROLLER = "#!/usr/bin/env bash\necho old-controller\n"
NEW_CONTROLLER = "#!/usr/bin/env bash\necho new-controller\n"
BAD_CONTROLLER = "#!/usr/bin/env bash\nif\n"

PRESERVE = "preserve_target_deploy_controller_snapshot"
PRESERVE_IF = "preserve_target_deploy_controller_snapshot_if_transaction_present"

pytestmark = pytest.mark.acceptance


def _bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _is_fork_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    stderr = proc.stderr or ""
    return (
        proc.returncode in (254, 3221225794)
        or "fork:" in stderr
        or "dofork" in stderr
        or "Resource temporarily unavailable" in stderr
    )


def _extract(name: str) -> str:
    text = RECONCILE.read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}$", text)
    assert match, f"{name} function not found"
    return match.group(0)


def _sibling_bodies() -> dict[str, str]:
    return {
        "remote-op-ohm-deploy-ssh": "gateway-snapshot\n",
        "remote-op-ohm-deploy-ssh.present": "",
        "remote-op-learning-reader": "reader-snapshot\n",
        "remote-op-learning-reader.present": "",
        "remote-op-learning-diagnostics": "diagnostics-snapshot\n",
        "remote-op-learning-diagnostics.present": "",
        "ohm-unified-cycle": "cron-unified\n",
        "opip-learning-export": "cron-export\n",
        "opip-ml-evidence": "cron-ml\n",
        "ohm-movement-discovery": "cron-legacy\n",
        "root.crontab": "root-crontab\n",
        "root.crontab.present": "",
    }


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")


def _fingerprints(paths: list[Path]) -> dict[str, bytes]:
    return {str(path): path.read_bytes() for path in paths}


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    git = shutil.which("git")
    assert git is not None
    return subprocess.run(
        [git, *args], cwd=cwd, capture_output=True, text=True, timeout=60
    )


def _init_app(root: Path, controller: str, *, commit: bool) -> str | None:
    app = root / "app"
    remote = app / "deploy" / "remote"
    remote.mkdir(parents=True)
    _write(remote / "ohm-deploy", controller)
    init = _git(["init", "-q", "-b", "main"], app)
    if init.returncode != 0:
        init = _git(["init", "-q"], app)
    assert init.returncode == 0, init.stderr
    if not commit:
        return None
    committed = _git(
        [
            "-c",
            "user.email=bootstrap@example.invalid",
            "-c",
            "user.name=bootstrap",
            "commit",
            "--allow-empty",
            "-m",
            "bootstrap",
            "-q",
        ],
        app,
    )
    assert committed.returncode == 0, committed.stderr
    parsed = _git(["rev-parse", "HEAD"], app)
    assert parsed.returncode == 0, parsed.stderr
    sha = parsed.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{40}", sha), sha
    return sha


def _make_snapshot(state: Path, name: str, controller: str) -> Path:
    snap = state / name
    snap.mkdir(parents=True)
    _write(snap / "remote-op-ohm-deploy", controller)
    _write(snap / "remote-op-ohm-deploy.present", "")
    for fname, body in _sibling_bodies().items():
        _write(snap / fname, body)
    return snap


def _seed_baseline_files(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    _write(state / "last-good-sha", "8fa47372ed00b7218dc3d76cf0cef9a74dc59481\n")
    _write(
        state / "safe-baseline-rollback.override.yml",
        'OPIP_RELEASE_PROFILE: "SAFE_BASELINE"\nOPIP_PAPER_V2_MODE: "off"\n',
    )


def _run(
    source: str,
    call: str,
    app: Path,
    state: Path,
    *,
    seams: bool = True,
) -> subprocess.CompletedProcess[str]:
    bash = _bash()
    assert bash is not None
    if seams:
        exports = (
            "export OPIP_DEPLOY_TEST_SEAMS=1\n"
            f"export OPIP_BOOTSTRAP_APP_ROOT='{app.as_posix()}'\n"
            f"export OPIP_BOOTSTRAP_STATE_DIR='{state.as_posix()}'\n"
        )
    else:
        exports = (
            "unset OPIP_DEPLOY_TEST_SEAMS || true\n"
            f"export OPIP_BOOTSTRAP_APP_ROOT='{app.as_posix()}'\n"
            f"export OPIP_BOOTSTRAP_STATE_DIR='{state.as_posix()}'\n"
        )
    script = "set -Eeuo pipefail\n" + exports + source + "\n" + call + "\n"
    return subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, timeout=60
    )


def _unchanged(before: dict[str, bytes], paths: list[Path]) -> None:
    after = _fingerprints(paths)
    assert after == before


@pytest.mark.acceptance
def test_ac_014_changed_controller_updates_only_the_deploy_snapshot(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a different target controller replaces only the deploy-controller snapshot."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    sha = _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    _seed_baseline_files(state)
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    watched = [
        snap / name for name in _sibling_bodies()
    ] + [
        state / "last-good-sha",
        state / "safe-baseline-rollback.override.yml",
    ]
    before = _fingerprints(watched)
    before_inode = (snap / "remote-op-ohm-deploy").stat().st_ino
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" in proc.stdout
    assert f"OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA={sha}" in proc.stdout
    assert (snap / "remote-op-ohm-deploy").read_text(encoding="utf-8") == NEW_CONTROLLER
    assert (snap / "remote-op-ohm-deploy").stat().st_ino != before_inode
    _unchanged(before, watched)
    assert not list(snap.glob("remote-op-ohm-deploy.bootstrap.*"))


@pytest.mark.acceptance
def test_ac_014_identical_controller_is_idempotent(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: an identical snapshot controller is not rewritten."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    sha = _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", NEW_CONTROLLER)
    controller = snap / "remote-op-ohm-deploy"
    before = controller.read_bytes()
    before_mtime = controller.stat().st_mtime_ns
    before_inode = controller.stat().st_ino
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=NOT_NEEDED" in proc.stdout
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" not in proc.stdout
    assert f"OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA={sha}" in proc.stdout
    assert controller.read_bytes() == before
    assert controller.stat().st_mtime_ns == before_mtime
    assert controller.stat().st_ino == before_inode


@pytest.mark.acceptance
def test_ac_014_zero_snapshots_fail_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: zero transaction snapshots fail closed."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    state.mkdir()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "exactly one scheduler-before transaction snapshot" in proc.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" not in proc.stdout
    assert list(state.iterdir()) == []


@pytest.mark.acceptance
def test_ac_014_multiple_snapshots_fail_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: multiple transaction snapshots fail closed without rewriting either controller."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    first = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    second = _make_snapshot(state, "scheduler-before.BBBBBB", OLD_CONTROLLER)
    before = _fingerprints(
        [first / "remote-op-ohm-deploy", second / "remote-op-ohm-deploy"]
    )
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "exactly one scheduler-before transaction snapshot" in proc.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" not in proc.stdout
    _unchanged(before, [first / "remote-op-ohm-deploy", second / "remote-op-ohm-deploy"])


@pytest.mark.acceptance
def test_ac_014_non_regular_snapshot_fails_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a non-regular snapshot controller fails closed."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    controller = snap / "remote-op-ohm-deploy"
    controller.unlink()
    controller.mkdir()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "snapshot controller is not a regular file" in proc.stderr
    assert controller.is_dir()

    link_state = tmp_path / "link-state"
    link_snap = _make_snapshot(link_state, "scheduler-before.CCCCCC", OLD_CONTROLLER)
    link = link_snap / "remote-op-ohm-deploy"
    target = tmp_path / "linked-controller"
    _write(target, OLD_CONTROLLER)
    link.unlink()
    try:
        link.symlink_to(target)
    except OSError:
        return
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", link_state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "snapshot controller is not a regular file" in proc.stderr
    assert link.is_symlink()
    assert target.read_text(encoding="utf-8") == OLD_CONTROLLER


@pytest.mark.acceptance
def test_ac_014_missing_present_marker_fails_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a snapshot without the present marker fails closed."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    (snap / "remote-op-ohm-deploy.present").unlink()
    before = (snap / "remote-op-ohm-deploy").read_bytes()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "present marker is missing" in proc.stderr
    assert (snap / "remote-op-ohm-deploy").read_bytes() == before


@pytest.mark.acceptance
def test_ac_014_bash_invalid_target_fails_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a target controller that fails bash -n is not installed into the snapshot."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, BAD_CONTROLLER, commit=True)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    before = (snap / "remote-op-ohm-deploy").read_bytes()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "failed bash -n" in proc.stderr
    assert (snap / "remote-op-ohm-deploy").read_bytes() == before


@pytest.mark.acceptance
def test_ac_014_malformed_head_fails_closed(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a repository HEAD that is not a 40-character SHA fails closed."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=False)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    before = (snap / "remote-op-ohm-deploy").read_bytes()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "HEAD is not a 40-char SHA" in proc.stderr
    assert (snap / "remote-op-ohm-deploy").read_bytes() == before


@pytest.mark.acceptance
def test_ac_014_no_transaction_reconcile_does_not_invent_a_snapshot(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: a reconcile with no transaction snapshot does not invent one and does not fail."""
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    state.mkdir()
    source = _extract(PRESERVE) + "\n" + _extract(PRESERVE_IF)
    proc = _run(source, PRESERVE_IF, tmp_path / "app", state)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
    assert list(state.iterdir()) == []

    _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    armed = _run(source, PRESERVE_IF, tmp_path / "app", state)
    if _is_fork_failure(armed):
        pytest.skip("bash cannot fork reliably in this environment")
    assert armed.returncode == 0, armed.stderr
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" in armed.stdout


@pytest.mark.acceptance
def test_ac_014_test_seams_are_inert_without_the_marker(tmp_path: Path) -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: snapshot path overrides are inert unless OPIP_DEPLOY_TEST_SEAMS=1."""
    text = _extract(PRESERVE)
    assert text.count('"${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1"') == 1
    assert 'app_root="/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1"' in text
    assert 'state_dir="/var/lib/ohm-deploy"' in text
    wrapper = _extract(PRESERVE_IF)
    assert '"${OPIP_DEPLOY_TEST_SEAMS:-0}" == "1"' in wrapper
    reconcile = RECONCILE.read_text(encoding="utf-8")
    assert len(re.findall(rf"(?m)^{PRESERVE}\(\) \{{$", reconcile)) == 1
    assert len(re.findall(rf"(?m)^  {PRESERVE}$", reconcile)) == 1
    assert len(re.findall(rf"(?m)^{PRESERVE_IF}\(\) \{{$", reconcile)) == 1
    assert len(re.findall(rf"(?m)^{PRESERVE_IF}$", reconcile)) == 1
    if _bash() is None:
        pytest.skip("bash is not available in this environment")
    _init_app(tmp_path, NEW_CONTROLLER, commit=True)
    state = tmp_path / "state"
    snap = _make_snapshot(state, "scheduler-before.AAAAAA", OLD_CONTROLLER)
    before = (snap / "remote-op-ohm-deploy").read_bytes()
    proc = _run(_extract(PRESERVE), PRESERVE, tmp_path / "app", state, seams=False)
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")
    assert proc.returncode != 0
    assert "OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED" not in proc.stdout
    assert (snap / "remote-op-ohm-deploy").read_bytes() == before


@pytest.mark.acceptance
def test_ac_014_safe_baseline_rollback_and_authority_remain_unchanged() -> None:
    """ATDD-RELEASE-PIPELINE-v1/AC-014: SAFE_BASELINE application rollback, Paper-v2, legacy entry, funded authority and Committee stay unchanged."""
    deploy = DEPLOY.read_text(encoding="utf-8")
    for literal in (
        'OPIP_RELEASE_PROFILE: "SAFE_BASELINE"',
        'OPIP_FEATURE_BUS_MODE: "off"',
        'OPIP_CANONICAL_WRITER_MODE: "off"',
        'OPIP_TARGET_SPINE_MODE: "off"',
        'OPIP_PAPER_V2_MODE: "off"',
        'OPIP_COMMITTEE_MODE: "off"',
        'OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD: "0.0"',
        "$DEPLOY_SCRIPT_DST|remote-op-ohm-deploy",
        "$SSH_GATEWAY_DST|remote-op-ohm-deploy-ssh",
    ):
        assert literal in deploy
    assert "write_safe_baseline_override()" in deploy
    assert "restore_scheduler_state()" in deploy
    compose = COMPOSE.read_text(encoding="utf-8")
    assert 'OPIP_PAPER_V2_MODE: "off"' in compose
    assert 'OPIP_COMMITTEE_MODE: "off"' in compose
    for name in ("SAFE_BASELINE", "EVIDENCE_SHADOW"):
        modes = RELEASE_PROFILES[name]["allowed_modes"]
        assert modes["OPIP_PAPER_V2_MODE"] == "off"
        assert modes["OPIP_COMMITTEE_MODE"] == "off"
        assert RELEASE_PROFILES[name]["expected_new_entry_authority"] == "LEGACY_ONLY"
    assert RELEASE_PROFILES["TARGET_PAPER"]["status"] == "BLOCKED"
    assert "funded/live execution" in RELEASE_PROFILES["SAFE_BASELINE"]["forbidden_capabilities"]
    function = _extract(PRESERVE)
    assert 'install -m 0755 -- "$target_controller" "$staged"' in function
    assert 'mv -f -- "$staged" "$snapshot/remote-op-ohm-deploy"' in function
    for forbidden in (
        "last-good-sha",
        "safe-baseline-rollback",
        "crontab",
        "docker",
        "remote-op-ohm-deploy-ssh",
        "remote-op-learning-reader",
        "remote-op-learning-diagnostics",
        "OPIP_PAPER_V2_MODE",
        "OPIP_COMMITTEE_MODE",
        "/etc/cron.d",
    ):
        assert forbidden not in function
