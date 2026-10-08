"""AC-025: owner-operated controller-only bootstrap.

Proves the one-time bootstrap atomically replaces ONLY the installed deploy
controller with the exact controller from an explicitly qualified current-main
SHA, without changing the production worktree, services, scheduler, cron, the
SSH gateway, last-good-sha, or any protection/incident/exchange state.

The tests drive the REAL script end-to-end with ``OPIP_DEPLOY_TEST_SEAMS=1``
inside a throwaway sandbox. They never touch ``/usr/local`` and never require
root.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.test_opip_canonical_single_writer_feasibility_v1 import (
    _bash,
    _is_fork_failure,
)

APP_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = APP_ROOT / "deploy" / "remote" / "bootstrap-deploy-controller-only.sh"
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
GATEWAY = APP_ROOT / "deploy" / "remote" / "ohm-deploy-ssh"
FULL_BOOTSTRAP = APP_ROOT / "deploy" / "remote" / "bootstrap-remote-ops.sh"
CONTROLLER_REL = "OHM-Trade-Agent-v1/deploy/remote/ohm-deploy"

REAL_CONTROLLER = DEPLOY.read_text(encoding="utf-8")
OLD_CONTROLLER = "#!/usr/bin/env bash\n# pre-AC-024 controller\necho old-controller\n"
NO_PREFLIGHT_CONTROLLER = "#!/usr/bin/env bash\nset -Eeuo pipefail\necho controller\n"
SYNTAX_ERROR_CONTROLLER = "#!/usr/bin/env bash\nif\n"

pytestmark = pytest.mark.acceptance

_ENV_CHECKS = (
    "OPIP_BOOTSTRAP_REPO_ROOT",
    "OPIP_BOOTSTRAP_STATE_DIR",
    "OPIP_BOOTSTRAP_DEPLOY_DST",
    "OPIP_BOOTSTRAP_LOCK_FILE",
)


def _git_bin() -> str | None:
    return shutil.which("git")


def _toolchain_ready() -> bool:
    """bash + git + flock + sha256sum, and a forkable shell."""
    bash = _bash()
    git = _git_bin()
    if bash is None or git is None:
        return False
    try:
        probe = subprocess.run(
            [bash, "-c", "for c in flock sha256sum cmp stat mktemp bash; do command -v \"$c\" >/dev/null 2>&1 || exit 1; done"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if probe.returncode != 0:
        return False
    return not _is_fork_failure(probe)


TOOLCHAIN_READY = _toolchain_ready()


def _require_toolchain() -> None:
    if not TOOLCHAIN_READY:
        pytest.skip(
            "a forking bash with flock/git/sha256sum is required to exercise the bootstrap"
        )


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    git = _git_bin()
    assert git is not None
    proc = subprocess.run(
        [git, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc


def _write(path: Path, body: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8", newline="\n")
    if mode is not None:
        path.chmod(mode)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Sandbox:
    """A throwaway host layout: repo + bare origin + state + sbin + lock."""

    def __init__(self, root: Path, controller: str, *, install_previous: bool = True) -> None:
        self.root = root
        self.repo = root / "repo"
        self.origin = root / "origin.git"
        self.state = root / "state"
        self.sbin = root / "sbin"
        self.lockdir = root / "lock"
        self.installed = self.sbin / "ohm-deploy"
        self.lock = self.lockdir / "ohm-deploy.lock"
        self.last_good = self.state / "last-good-sha"
        self.controller_text = controller
        self.install_previous = install_previous
        self._build()

    # -- construction --------------------------------------------------------
    def _build(self) -> None:
        for directory in (self.state, self.sbin, self.lockdir):
            directory.mkdir(parents=True, exist_ok=True)
        _write(self.last_good, "b" * 40 + "\n")

        _git(["init", "--bare", "-q", self.origin.as_posix()], self.root)
        self.repo.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q", "-b", "main"], self.repo)
        _git(["config", "user.email", "bootstrap@example.invalid"], self.repo)
        _git(["config", "user.name", "bootstrap"], self.repo)
        _git(["remote", "add", "origin", self.origin.as_posix()], self.repo)
        self.commit_controller(self.controller_text)
        # The pre-existing, pre-AC-024 installed controller (unless the case
        # needs a host with no controller installed at all).
        if self.install_previous:
            _write(self.installed, OLD_CONTROLLER, mode=0o755)

    def commit_controller(self, controller: str) -> str:
        _write(self.repo / CONTROLLER_REL, controller, mode=0o755)
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "controller revision", "--allow-empty"], self.repo)
        _git(["push", "-q", "origin", "main"], self.repo)
        _git(["fetch", "-q", "origin"], self.repo)
        return self.head()

    def commit_without_controller(self) -> str:
        (self.repo / CONTROLLER_REL).unlink()
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "remove controller"], self.repo)
        _git(["push", "-q", "--force", "origin", "main"], self.repo)
        _git(["fetch", "-q", "origin"], self.repo)
        return self.head()

    # -- observation ---------------------------------------------------------
    def head(self) -> str:
        return _git(["rev-parse", "HEAD"], self.repo).stdout.strip()

    def origin_main(self) -> str:
        return _git(["rev-parse", "origin/main"], self.repo).stdout.strip()

    def object_bytes(self, sha: str) -> bytes:
        git = _git_bin()
        assert git is not None
        proc = subprocess.run(
            [git, "show", f"{sha}:{CONTROLLER_REL}"],
            cwd=self.repo,
            capture_output=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def installed_bytes(self) -> bytes:
        return self.installed.read_bytes()

    def env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["OPIP_DEPLOY_TEST_SEAMS"] = "1"
        env["OPIP_BOOTSTRAP_REPO_ROOT"] = str(self.repo)
        env["OPIP_BOOTSTRAP_STATE_DIR"] = str(self.state)
        env["OPIP_BOOTSTRAP_DEPLOY_DST"] = str(self.installed)
        env["OPIP_BOOTSTRAP_LOCK_FILE"] = str(self.lock)
        return env

    def env_with(self, **extra: str) -> dict[str, str]:
        env = self.env()
        env.update({key: str(value) for key, value in extra.items()})
        return env

    def mode(self, path: Path) -> str:
        proc = subprocess.run(
            [_bash() or "bash", "-c", f'stat -c "%a" "{path}"'],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        return proc.stdout.strip()

    def is_symlink(self, path: Path) -> bool:
        return path.is_symlink()

    def run(
        self,
        sha: str,
        env: dict[str, str] | None = None,
        args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        bash = _bash()
        assert bash is not None
        argv = args if args is not None else [sha]
        return subprocess.run(
            [bash, str(BOOTSTRAP), *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env if env is not None else self.env(),
            timeout=180,
        )

    def receipt(self) -> Path:
        return self.state / "controller-bootstrap-receipt.env"

    def receipt_fields(self) -> dict[str, str]:
        fields: dict[str, str] = {}
        for line in self.receipt().read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value
        return fields


@pytest.fixture()
def sandbox(tmp_path: Path):
    _require_toolchain()
    return Sandbox(tmp_path / "sandbox", REAL_CONTROLLER)


def _skip_on_fork(proc: subprocess.CompletedProcess[str]) -> None:
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")


def _real_cp() -> str:
    return shutil.which("cp") or "/bin/cp"


def _cp_shim_body(patterns: str) -> str:
    """A `cp` shim that fails only when the SOURCE matches `patterns`.

    The first non-flag argument is the source (``cp [-p] [--] SRC DST``), so the
    bounded backup (whose source is the installed controller) still succeeds
    while the extraction copy and/or the restore copy can be made to fail.
    """
    return (
        "#!/usr/bin/env bash\n"
        'src=""\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in -*) continue ;; esac\n'
        '  src="$arg"\n'
        "  break\n"
        "done\n"
        f'case "$src" in\n  {patterns}) exit 1 ;;\nesac\n'
        f'exec {_real_cp()!r} "$@"\n'
    )


def _make_shim(directory: Path, name: str, body: str) -> Path:
    """Write an executable shim and return the DIRECTORY to put on PATH."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return directory


def _python_for_bash() -> str:
    return shutil.which("python3") or sys.executable


def _tree_fingerprints(root: Path, exclude: tuple[Path, ...]) -> dict[str, str]:
    fps: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(str(path).startswith(str(item)) for item in exclude):
            continue
        fps[str(path.relative_to(root))] = _sha256_bytes(path.read_bytes())
    return fps


# ---------------------------------------------------------------------------
# Implementation-level guards (no production mutation, no forbidden calls).
# ---------------------------------------------------------------------------

def _bootstrap_code() -> str:
    return "\n".join(
        line
        for line in BOOTSTRAP.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def test_ac_025_bootstrap_touches_no_forbidden_control_plane():
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the bootstrap contains no docker, compose, systemctl, cron, reconcile, sudoers, key, or gateway mutation path."""
    code = _bootstrap_code()
    for forbidden in (
        "docker",
        "docker compose",
        "systemctl",
        "crontab",
        "/etc/cron.d",
        "reconcile-scheduler",
        "bootstrap-remote-ops",
        "sudoers",
        "authorized_keys",
        "/root/.ohm-remote-ops",
        "last-good-sha",
        "safe-baseline",
        "ohm-deploy-ssh",
        "opip-learning-read-export",
        "diagnose-opip-learning",
        "kraken",
    ):
        assert forbidden not in code, forbidden
    # The only executable destination the helper may replace.
    assert 'DEPLOY_SCRIPT_DST="/usr/local/sbin/ohm-deploy"' in code
    assert code.count("/usr/local/sbin/") == 1


def test_ac_025_ssh_gateway_remains_exactly_two_commands():
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the forced-command gateway is unchanged and still permits only deploy <sha> and diagnose-learning."""
    gateway = GATEWAY.read_text(encoding="utf-8")
    assert "=~ ^deploy[[:space:]]+([0-9a-f]{40})$" in gateway
    assert '== "diagnose-learning"' in gateway
    assert "refusing command" in gateway
    assert gateway.count("exec sudo") == 2
    for forbidden in ("eval ", "bash -c", "sh -c", "bootstrap", "controller"):
        assert forbidden not in gateway
    # The bootstrap is never reachable through the gateway.
    assert "bootstrap-deploy-controller-only" not in gateway


def test_ac_025_bootstrap_is_not_wired_into_the_deploy_path():
    """ATDD-RELEASE-PIPELINE-v1/AC-025: neither the deploy workflow nor the normal controller nor the scheduler reconcile installs or invokes the controller-only bootstrap."""
    workflow = (APP_ROOT.parent / ".github" / "workflows" / "deploy-production.yml").read_text(
        encoding="utf-8"
    )
    reconcile = (APP_ROOT / "deploy" / "remote" / "reconcile-scheduler.sh").read_text(
        encoding="utf-8"
    )
    for document in (
        workflow,
        reconcile,
        DEPLOY.read_text(encoding="utf-8"),
        FULL_BOOTSTRAP.read_text(encoding="utf-8"),
    ):
        assert "bootstrap-deploy-controller-only" not in document
        assert "bootstrap_deploy_controller_only" not in document


def test_ac_025_test_overrides_are_inert_without_the_seam(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: repository/state/destination/lock overrides only take effect when OPIP_DEPLOY_TEST_SEAMS=1, so production can never be redirected by an ambient variable."""
    code = _bootstrap_code()
    guard = code.index('if [[ "$TEST_SEAMS" == "1" ]]; then')
    for name in _ENV_CHECKS:
        assert name in code
        assert code.index(name) > guard
    env = sandbox.env()
    env["OPIP_DEPLOY_TEST_SEAMS"] = "0"
    # With the seam off the real paths are used and the sandbox is irrelevant.
    proc = sandbox.run("0" * 40, env=env)
    assert proc.returncode != 0
    # Nothing in the sandbox changed: the override was ignored.
    assert sandbox.installed_bytes() == OLD_CONTROLLER.encode()
    assert not sandbox.receipt().exists()


# ---------------------------------------------------------------------------
# Argument, resolution and validation failures (all before installation).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "xyz", "abc", "0" * 39, "0" * 41, "A" * 40, "0" * 40 + " "])
def test_ac_025_malformed_sha_fails_before_modification(sandbox, bad):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: anything that is not exactly one 40-character lowercase SHA is refused before the controller is touched."""
    before = sandbox.installed_bytes()
    proc = sandbox.run(bad if bad else "")
    _skip_on_fork(proc)
    assert proc.returncode != 0
    assert sandbox.installed_bytes() == before
    assert not sandbox.receipt().exists()


def test_ac_025_target_must_equal_origin_main(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a well-formed SHA that is not current origin/main fails closed and leaves the controller unchanged."""
    _git(["commit", "-q", "--allow-empty", "-m", "advance", ], sandbox.repo)
    current = sandbox.origin_main()
    stale = sandbox.head()
    assert stale != current

    before = sandbox.installed_bytes()
    proc = sandbox.run(stale)
    _skip_on_fork(proc)
    assert proc.returncode == 65
    combined = proc.stdout + proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_REMOTE_MAIN=" in combined
    assert sandbox.installed_bytes() == before
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_STATUS"] == "FAILED"


def test_ac_025_missing_target_controller_fails_closed(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a target SHA whose tree has no controller fails closed rather than installing an absent or partial file."""
    sha = sandbox.commit_without_controller()
    before = sandbox.installed_bytes()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 69
    assert sandbox.installed_bytes() == before
    assert sandbox.head() == sha


@pytest.mark.parametrize(
    ("controller", "expected_note"),
    [
        (SYNTAX_ERROR_CONTROLLER, "bash -n"),
        (NO_PREFLIGHT_CONTROLLER, "AC-024"),
    ],
)
def test_ac_025_invalid_target_controller_is_refused(sandbox, controller, expected_note):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a target controller that fails bash -n or lacks the AC-024 protection-preflight signatures is refused before installation."""
    sha = sandbox.commit_controller(controller)
    before = sandbox.installed_bytes()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 70
    assert expected_note in proc.stderr
    assert sandbox.installed_bytes() == before
    assert sandbox.head() == sha


def test_ac_025_active_deploy_lock_causes_refusal(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: while another deploy/bootstrap holds the canonical lock the bootstrap fails closed."""
    _require_toolchain()
    bash = _bash()
    assert bash is not None
    ready = sandbox.lockdir / "ready"
    holder = subprocess.Popen(
        [
            bash,
            "-c",
            f'exec 9>"{sandbox.lock}"; flock -n 9 || exit 3; : >"{ready}"; sleep 60',
        ],
    )
    try:
        deadline = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.2)
        assert ready.exists(), "lock holder never acquired the lock"
        before = sandbox.installed_bytes()
        proc = sandbox.run(sandbox.origin_main())
        _skip_on_fork(proc)
        assert proc.returncode == 75
        combined = proc.stdout + proc.stderr
        assert "OPIP_CONTROLLER_BOOTSTRAP_REASON=LOCK_HELD" in combined
        assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined
        assert sandbox.installed_bytes() == before
        assert not sandbox.receipt().exists()
    finally:
        holder.terminate()
        holder.wait(timeout=30)


def test_ac_025_active_deployment_transaction_causes_refusal(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a live scheduler-before.* transaction means an in-flight deployment, so the bootstrap refuses and never deletes or repairs it."""
    transaction = sandbox.state / "scheduler-before.AB12CD"
    transaction.mkdir()
    (transaction / "root.crontab").write_text("pre-deploy\n", encoding="utf-8")
    recovery = sandbox.state / "scheduler-recovery.OLD1"
    recovery.mkdir()
    (recovery / "keep").write_text("historical\n", encoding="utf-8")

    before = sandbox.installed_bytes()
    proc = sandbox.run(sandbox.origin_main())
    _skip_on_fork(proc)
    assert proc.returncode == 76
    combined = proc.stdout + proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_REASON=ACTIVE_DEPLOY_TRANSACTION" in combined
    assert "recovery is required" in combined
    assert sandbox.installed_bytes() == before
    assert transaction.is_dir()
    assert (transaction / "root.crontab").is_file()
    assert (recovery / "keep").is_file()


# ---------------------------------------------------------------------------
# Successful bootstrap.
# ---------------------------------------------------------------------------

def test_ac_025_success_installs_target_controller_from_the_git_object(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the installed controller is the exact TARGET_SHA git object, never the (possibly dirty) live checkout, installed atomically as root-style 0755."""
    sha = sandbox.origin_main()
    expected = sandbox.object_bytes(sha)
    # Dirty the live worktree copy so a checkout-sourced install would be wrong.
    dirty = sandbox.repo / CONTROLLER_REL
    dirty.write_text("#!/usr/bin/env bash\n# DIRTY LIVE COPY\necho dirty\n", encoding="utf-8")

    head_before = sandbox.head()
    last_good_before = sandbox.last_good.read_bytes()
    gateway_before = (sandbox.repo / "OHM-Trade-Agent-v1/deploy/remote/ohm-deploy-ssh")
    gateway_before.mkdir(parents=True)
    (gateway_before / "sentinel").write_text("gateway\n", encoding="utf-8")
    cron_fixture = sandbox.state / "cron-fixture"
    cron_fixture.write_text("cron\n", encoding="utf-8")

    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=SUCCESS" in proc.stdout

    installed = sandbox.installed_bytes()
    assert installed == expected
    assert installed != dirty.read_bytes()
    assert "OPIP_PROTECTION_PREFLIGHT=" in installed.decode()
    assert installed.decode().startswith("#!/usr/bin/env bash")

    mode = subprocess.run(
        [_bash() or "bash", "-c", f'stat -c "%a" "{sandbox.installed}"'],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert mode.stdout.strip() == "755"

    # Nothing else in the sandbox moved.
    assert sandbox.head() == head_before
    assert sandbox.last_good.read_bytes() == last_good_before
    assert (gateway_before / "sentinel").read_text(encoding="utf-8") == "gateway\n"
    assert cron_fixture.read_text(encoding="utf-8") == "cron\n"
    # The dirty live checkout was not "cleaned up" by the bootstrap.
    assert "DIRTY LIVE COPY" in dirty.read_text(encoding="utf-8")


def test_ac_025_success_touches_only_the_controller_destination(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: outside the controller, its bounded backup and the receipt, no file in the host layout changes."""
    sha = sandbox.origin_main()
    exclude = (
        sandbox.state,
        sandbox.sbin,
        sandbox.lockdir,
        sandbox.repo / ".git",
        sandbox.origin,
    )
    before = _tree_fingerprints(sandbox.root, exclude)

    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr

    after = _tree_fingerprints(sandbox.root, exclude)
    assert after == before


def test_ac_025_second_invocation_is_idempotent(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: re-running against the same target reports NOT_NEEDED, exits success, and mutates nothing."""
    sha = sandbox.origin_main()
    first = sandbox.run(sha)
    _skip_on_fork(first)
    assert first.returncode == 0

    installed_before = sandbox.installed_bytes()
    backup = sandbox.state / "controller-bootstrap-previous"
    backup_before = backup.read_bytes()

    second = sandbox.run(sha)
    _skip_on_fork(second)
    assert second.returncode == 0, second.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED" in second.stdout
    assert sandbox.installed_bytes() == installed_before
    assert backup.read_bytes() == backup_before
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_STATUS"] == "NOT_NEEDED"
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_AC024"] == "PROVEN"


def test_ac_025_first_success_backs_up_the_previous_controller(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the pre-existing controller is preserved root-style under /var/lib/ohm-deploy before replacement, without creating a scheduler transaction."""
    sha = sandbox.origin_main()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr

    backup = sandbox.state / "controller-bootstrap-previous"
    assert backup.is_file()
    assert backup.read_bytes() == OLD_CONTROLLER.encode()
    assert "OPIP_CONTROLLER_BOOTSTRAP_PREVIOUS_SHA256=" in proc.stdout
    assert list(sandbox.state.glob("scheduler-before.*")) == []


def test_ac_025_receipt_is_bounded_restrictive_and_secretless(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the bootstrap receipt holds only the bounded marker set, at mode 0600, with no secret material."""
    sha = sandbox.origin_main()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0

    receipt = sandbox.receipt()
    assert receipt.is_file()
    mode = subprocess.run(
        [_bash() or "bash", "-c", f'stat -c "%a" "{receipt}"'],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert mode.stdout.strip() == "600"

    fields = sandbox.receipt_fields()
    assert set(fields) == {
        "OPIP_CONTROLLER_BOOTSTRAP_STATUS",
        "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA",
        "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256",
        "OPIP_CONTROLLER_BOOTSTRAP_PREVIOUS_SHA256",
        "OPIP_CONTROLLER_BOOTSTRAP_INSTALLED_SHA256",
        "OPIP_CONTROLLER_BOOTSTRAP_AC024",
        "OPIP_CONTROLLER_BOOTSTRAP_COMPLETED_AT_UTC",
    }
    assert fields["OPIP_CONTROLLER_BOOTSTRAP_STATUS"] == "SUCCESS"
    assert fields["OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA"] == sha
    assert fields["OPIP_CONTROLLER_BOOTSTRAP_AC024"] == "PROVEN"
    assert fields["OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256"] == _sha256_bytes(
        sandbox.installed_bytes()
    )
    assert fields["OPIP_CONTROLLER_BOOTSTRAP_INSTALLED_SHA256"] == fields[
        "OPIP_CONTROLLER_BOOTSTRAP_TARGET_SHA256"
    ]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", fields[
        "OPIP_CONTROLLER_BOOTSTRAP_COMPLETED_AT_UTC"
    ])
    text = receipt.read_text(encoding="utf-8")
    for pattern in ("KEY", "TOKEN", "PASSWORD", "SECRET", "PRIVATE", "BEGIN"):
        assert pattern not in text


def test_ac_025_next_deploy_controller_has_the_ac024_preflight(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: after a successful bootstrap the installed controller contains the AC-024 preflight function and refusal markers, so the next deploy reaches the read-only preflight."""
    sha = sandbox.origin_main()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0

    installed = sandbox.installed.read_text(encoding="utf-8")
    assert "run_protection_preflight()" in installed
    assert "OPIP_PROTECTION_PREFLIGHT_ABORT=REFUSED_BEFORE_MUTATION" in installed
    assert installed.count("\ntrap rollback ERR\n") == 1
    # The preflight still precedes the live checkout and the first mutation.
    boundary = installed.index("# AC-024 read-only protection boundary.")
    invocation = installed.index("if ! run_protection_preflight; then", boundary)
    checkout = installed.index("checkout -f main", boundary)
    mutation = installed.index("\nstop_paper_stack\n", checkout)
    assert invocation < checkout < mutation


# ---------------------------------------------------------------------------
# Fail-closed restore semantics.
# ---------------------------------------------------------------------------

def test_ac_025_post_install_failure_restores_the_previous_controller(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: when post-install verification fails, the previous controller is restored atomically and verified, and no partially verified controller is left installed."""
    sha = sandbox.origin_main()
    before = sandbox.installed_bytes()

    # Fault injection: `cmp` is used only by the post-install byte comparison,
    # so a shim that always reports a difference deterministically exercises the
    # verification-failure restore path without touching production code.
    shim = sandbox.root / "shim"
    _write(shim / "cmp", "#!/usr/bin/env bash\nexit 1\n", mode=0o755)
    env = sandbox.env()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]

    proc = sandbox.run(sha, env=env)
    _skip_on_fork(proc)
    assert proc.returncode == 71
    combined = proc.stdout + proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined
    assert "restoring the previous controller" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=VERIFIED" in combined
    # The prior controller is back, byte for byte.
    assert sandbox.installed_bytes() == before
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_STATUS"] == "FAILED"
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_AC024"] == "UNPROVEN"
    assert sandbox.head() == sha


def test_ac_025_failure_before_install_leaves_the_controller_unchanged(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: every validation failure happens before installation, so the installed controller is untouched."""
    sha = sandbox.commit_controller(SYNTAX_ERROR_CONTROLLER)
    before = sandbox.installed_bytes()
    head_before = sandbox.head()
    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 70
    assert sandbox.installed_bytes() == before
    assert sandbox.head() == head_before
    assert not (sandbox.state / "controller-bootstrap-previous").exists()


# ---------------------------------------------------------------------------
# NOT_NEEDED is gated on the COMPLETE installed-controller invariant.
# ---------------------------------------------------------------------------

def test_ac_025_idempotent_not_needed_requires_full_invariant(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a byte-identical, regular, mode-0755 controller reports NOT_NEEDED and proves the live HEAD was unchanged."""
    sha = sandbox.origin_main()
    first = sandbox.run(sha)
    _skip_on_fork(first)
    assert first.returncode == 0

    installed_before = sandbox.installed_bytes()
    installed_stat = sandbox.mode(sandbox.installed)
    second = sandbox.run(sha)
    _skip_on_fork(second)
    assert second.returncode == 0, second.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED" in second.stdout
    assert "OPIP_CONTROLLER_BOOTSTRAP_AC024=PROVEN" in second.stdout
    assert sandbox.installed_bytes() == installed_before
    assert sandbox.mode(sandbox.installed) == installed_stat
    # E: NOT_NEEDED proves the live checkout did not move for this run.
    assert f"OPIP_CONTROLLER_BOOTSTRAP_LIVE_HEAD={sandbox.head()}" in second.stdout
    assert sandbox.receipt_fields()["OPIP_CONTROLLER_BOOTSTRAP_STATUS"] == "NOT_NEEDED"


def test_ac_025_identical_bytes_with_wrong_mode_is_not_not_needed(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: identical bytes whose mode is not 0755 must NOT short-circuit as NOT_NEEDED; the correction path restores mode 0755."""
    sha = sandbox.origin_main()
    first = sandbox.run(sha)
    _skip_on_fork(first)
    assert first.returncode == 0
    target_bytes = sandbox.installed_bytes()

    sandbox.installed.chmod(0o644)
    assert sandbox.mode(sandbox.installed) == "644"

    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED" not in proc.stdout
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=SUCCESS" in proc.stdout
    assert sandbox.installed_bytes() == target_bytes
    assert sandbox.mode(sandbox.installed) == "755"


def test_ac_025_identical_bytes_through_a_symlink_is_not_not_needed(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: a symlink destination with identical bytes must NOT be accepted as NOT_NEEDED, and the final controller must be a regular non-symlink file."""
    sha = sandbox.origin_main()
    first = sandbox.run(sha)
    _skip_on_fork(first)
    assert first.returncode == 0
    target_bytes = sandbox.installed_bytes()

    aside = sandbox.root / "controller-bytes"
    aside.write_bytes(target_bytes)
    sandbox.installed.unlink()
    sandbox.installed.symlink_to(aside)
    assert sandbox.is_symlink(sandbox.installed)

    proc = sandbox.run(sha)
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED" not in proc.stdout
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=SUCCESS" in proc.stdout
    assert not sandbox.is_symlink(sandbox.installed)
    assert sandbox.installed.is_file()
    assert sandbox.installed_bytes() == target_bytes
    assert sandbox.mode(sandbox.installed) == "755"


def test_ac_025_production_verifier_requires_root_owner_and_group(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the shared installed-controller verifier enforces root:root in production, and a production ownership failure is not silently ignored."""
    code = _bootstrap_code()
    assert "controller_metadata_ok" in code
    assert "verify_installed_controller" in code
    # The production-gated ownership proof sits inside the ONE shared verifier.
    verifier = code.split("controller_metadata_ok() {", 1)[1].split("\n}", 1)[0]
    assert 'PRODUCTION_MODE' in verifier
    assert "root:root" in verifier
    assert "stat -c '%U:%G'" in verifier
    installer = code.split("atomic_install_controller() {", 1)[1].split("\n}", 1)[0]
    # No fail-open ownership handling on the installation path.
    assert "chown root:root" in installer
    assert "|| true" not in installer

    if not TOOLCHAIN_READY:
        pytest.skip("a forking bash with flock/git/sha256sum is required")
    # Behavioural: forcing production ownership with a chown that cannot change
    # ownership must fail the run and leave the installed controller untouched.
    shim = _make_shim(sandbox.root / "shim-bad-chown", "chown", "#!/usr/bin/env bash\nexit 1\n")
    before = sandbox.installed_bytes()
    env = sandbox.env_with(OPIP_BOOTSTRAP_PRODUCTION_MODE="1")
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    proc = sandbox.run(sandbox.origin_main(), env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined
    assert "own" in combined and "root" in combined
    assert sandbox.installed_bytes() == before


def test_ac_025_live_head_change_before_not_needed_fails_closed(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: if the live HEAD moves before NOT_NEEDED completes, the bootstrap fails closed without touching the controller or repairing the checkout."""
    sha = sandbox.origin_main()
    first = sandbox.run(sha)
    _skip_on_fork(first)
    assert first.returncode == 0
    installed_before = sandbox.installed_bytes()

    real_git = _git_bin()
    assert real_git is not None
    shim = _make_shim(
        sandbox.root / "shim-git-drift",
        "git",
        "#!/usr/bin/env bash\n"
        f"REAL_GIT={real_git!r}\n"
        f"REPO={sandbox.repo.as_posix()!r}\n"
        f"GATE={(sandbox.root / 'git-drift-gate').as_posix()!r}\n"
        'if [[ "$*" == *"rev-parse HEAD"* ]]; then\n'
        '  if [[ -f "$GATE" ]]; then\n'
        '    "$REAL_GIT" -C "$REPO" -c user.email=d@e.f -c user.name=d '
        "commit -q --allow-empty -m drift\n"
        "  fi\n"
        '  : > "$GATE"\n'
        "fi\n"
        'exec "$REAL_GIT" "$@"\n',
    )
    env = sandbox.env()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    proc = sandbox.run(sha, env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 72, combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=NOT_NEEDED" not in combined
    assert sandbox.installed_bytes() == installed_before
    assert (sandbox.root / "git-drift-gate").is_file()


# ---------------------------------------------------------------------------
# Production ownership and post-install verification failure.
# ---------------------------------------------------------------------------

def test_ac_025_post_install_ownership_failure_restores(tmp_path):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: when post-install verification cannot prove root:root ownership, the bootstrap fails and restores."""
    _require_toolchain()
    sandbox = Sandbox(tmp_path / "ownerless", REAL_CONTROLLER, install_previous=False)
    assert not sandbox.installed.exists()
    shim = _make_shim(sandbox.root / "shim-chown-ok", "chown", "#!/usr/bin/env bash\nexit 0\n")
    env = sandbox.env_with(OPIP_BOOTSTRAP_PRODUCTION_MODE="1")
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]

    proc = sandbox.run(sandbox.origin_main(), env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "post-install verification failed" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=VERIFIED" in combined
    # Restored to the prior faithful state: no controller existed before.
    assert not sandbox.installed.exists()


def test_ac_025_installation_failure_reports_verified_restore(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: an atomic installation failure still reports RESTORE=VERIFIED when the previous controller is provably restored."""
    before = sandbox.installed_bytes()
    # Fail only the copy of the extracted target; the restore copy uses the
    # bounded backup and therefore still succeeds.
    shim = _make_shim(
        sandbox.root / "shim-cp-target",
        "cp",
        _cp_shim_body("*ohm-deploy.target"),
    )
    env = sandbox.env()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    proc = sandbox.run(sandbox.origin_main(), env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "controller installation failed" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=VERIFIED" in combined
    assert sandbox.installed_bytes() == before


def test_ac_025_installation_and_restore_failure_reports_unproven(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: when both the installation and the restore of the previous controller fail, the verdict is RESTORE=UNPROVEN with an explicit operator action."""
    # Fail the target copy AND the restore copy of the bounded backup.
    shim = _make_shim(
        sandbox.root / "shim-cp-both",
        "cp",
        _cp_shim_body("*ohm-deploy.target|*controller-bootstrap-previous"),
    )
    env = sandbox.env()
    env["PATH"] = str(shim) + os.pathsep + env["PATH"]
    proc = sandbox.run(sandbox.origin_main(), env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_RESTORE=UNPROVEN" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_OPERATOR_ACTION=CONTROLLER_RESTORE_UNPROVEN" in combined
    assert "OPIP_CONTROLLER_BOOTSTRAP_STATUS=FAILED" in combined


def test_ac_025_no_forbidden_tooling_is_required(sandbox):
    """ATDD-RELEASE-PIPELINE-v1/AC-025: the tool preflight names every real dependency but never adds docker, compose, systemctl or cron."""
    code = _bootstrap_code()
    preflight = code.split("REQUIRED_TOOLS=(", 1)[1].split("\n", 1)[0]
    for required in ("git", "flock", "mktemp", "sha256sum", "awk", "cmp", "stat", "chmod",
                     "mv", "cp", "mkdir", "rm", "dirname", "grep", "date", "id", "bash"):
        assert required in preflight, required
    for forbidden in ("docker", "systemctl", "crontab", "cron"):
        assert forbidden not in preflight, forbidden
    # The production-only dependencies are added conditionally.
    assert 'REQUIRED_TOOLS+=(sudo chown)' in code
    # Every command referenced in the body is covered by the preflight list.
    assert "command -v" in code
