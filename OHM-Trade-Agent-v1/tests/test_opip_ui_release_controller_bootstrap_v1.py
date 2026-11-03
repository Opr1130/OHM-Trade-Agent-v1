"""ATDD: first-use bootstrap of the complete UI release controller set.

Proves the owner-operated bootstrap that makes the owner-gated UI-only edge
release reachable for the first time on a host whose deployed release predates
the controller/gateway/library corrections - and proves it against the OLD
installed components, not against newly updated source files.

The behavioural tests drive the REAL script end-to-end with
``OPIP_UI_BOOTSTRAP_TEST_SEAMS=1`` inside a throwaway sandbox. They never touch
``/usr/local`` and never require root.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_opip_canonical_single_writer_feasibility_v1 import (
    _bash,
    _is_fork_failure,
)

INCREMENT = "ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1"
ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "deploy/remote/bootstrap-ui-release-controller.sh"

COMPONENTS = (
    ("deploy/remote/ohm-deploy", "ohm-deploy", 0o755),
    ("deploy/remote/ohm-deploy-ssh", "ohm-deploy-ssh", 0o755),
    ("deploy/remote/lib/opip_release_tree.sh", "opip_release_tree.sh", 0o644),
    ("deploy/remote/lib/opip_ui_release.sh", "opip_ui_release.sh", 0o644),
)
REPO_PREFIX = "OHM-Trade-Agent-v1/"

# The pre-#496 gateway: the two bounded actions the deployed host actually has.
OLD_GATEWAY = """#!/usr/bin/env bash
set -Eeuo pipefail
ORIGINAL="${SSH_ORIGINAL_COMMAND:-}"
if [[ "$ORIGINAL" =~ ^deploy[[:space:]]+([0-9a-f]{40})$ ]]; then
  TARGET_SHA="${BASH_REMATCH[1]}"
  exec sudo /usr/local/sbin/ohm-deploy "$TARGET_SHA"
fi
if [[ "$ORIGINAL" == "diagnose-learning" ]]; then
  exec sudo /usr/local/sbin/ohm-deploy diagnose-learning
fi
echo "refusing command" >&2
exit 64
"""

# The pre-#496 controller: no UI hand-off at all.
OLD_CONTROLLER = "#!/usr/bin/env bash\nset -Eeuo pipefail\necho old-controller\n"

pytestmark = pytest.mark.acceptance


def _git_bin() -> str | None:
    return shutil.which("git")


def _toolchain_ready() -> bool:
    if _bash() is None or _git_bin() is None:
        return False
    try:
        probe = subprocess.run(
            [_bash(), "-c", "for c in flock sha256sum cmp stat mktemp install; do command -v \"$c\" >/dev/null 2>&1 || exit 1; done; v=\"$(printf ok)\"; [ \"$v\" = ok ]"],
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
        pytest.skip("a forking bash with git/flock/sha256sum is required")


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Sandbox:
    """A throwaway host layout: trusted checkout + bare origin + sbin/lib + state."""

    def __init__(
        self,
        root: Path,
        *,
        skip: tuple[str, ...] = (),
        install_previous: bool = True,
        wiring: str | None = None,
    ) -> None:
        self.root = root
        self.repo = root / "repo"
        self.origin = root / "origin.git"
        self.state = root / "state"
        self.sbin = root / "sbin"
        self.lib = root / "lib"
        self.lock = root / "lock" / "ohm-deploy.lock"
        self.wiring = root / "authorized_keys"
        self.skip = skip
        self.install_previous = install_previous
        self._build()
        if wiring is not None:
            _write(self.wiring, wiring)

    def _build(self) -> None:
        for directory in (self.state, self.sbin, self.lib, self.lock.parent):
            directory.mkdir(parents=True, exist_ok=True)
        _git(["init", "--bare", "-q", self.origin.as_posix()], self.root)
        self.repo.mkdir(parents=True, exist_ok=True)
        _git(["init", "-q", "-b", "main"], self.repo)
        _git(["config", "user.email", "bootstrap@example.invalid"], self.repo)
        _git(["config", "user.name", "bootstrap"], self.repo)
        _git(["remote", "add", "origin", self.origin.as_posix()], self.repo)
        self.commit_components()
        if self.install_previous:
            _write(self.sbin / "ohm-deploy", OLD_CONTROLLER, mode=0o755)

    def commit_components(self, extra: str | None = None) -> str:
        for relative, _name, mode in COMPONENTS:
            if relative in self.skip:
                continue
            _write(
                self.repo / REPO_PREFIX / relative,
                (ROOT / relative).read_text(encoding="utf-8"),
                mode,
            )
        if extra is not None:
            _write(self.repo / "README.md", extra)
        _git(["add", "-A"], self.repo)
        _git(["commit", "-q", "-m", "controller revision", "--allow-empty"], self.repo)
        _git(["push", "-q", "origin", "main"], self.repo)
        _git(["fetch", "-q", "origin"], self.repo)
        return self.head()

    def head(self) -> str:
        return _git(["rev-parse", "HEAD"], self.repo).stdout.strip()

    def origin_main(self) -> str:
        return _git(["rev-parse", "origin/main"], self.repo).stdout.strip()

    def dest(self, index: int) -> Path:
        name = COMPONENTS[index][1]
        base = self.sbin if index < 2 else self.lib
        return base / name

    def object_bytes(self, relative: str, sha: str | None = None) -> bytes:
        git = _git_bin()
        assert git is not None
        proc = subprocess.run(
            [git, "show", f"{sha or self.origin_main()}:{REPO_PREFIX}{relative}"],
            cwd=self.repo,
            capture_output=True,
            timeout=120,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    def env(self, **overrides: str) -> dict[str, str]:
        env = dict(os.environ)
        env["OPIP_UI_BOOTSTRAP_TEST_SEAMS"] = "1"
        env["OPIP_UI_BOOTSTRAP_REPO_ROOT"] = str(self.repo)
        env["OPIP_UI_BOOTSTRAP_STATE_DIR"] = str(self.state)
        env["OPIP_UI_BOOTSTRAP_SBIN_DIR"] = str(self.sbin)
        env["OPIP_UI_BOOTSTRAP_LIB_DIR"] = str(self.lib)
        env["OPIP_UI_BOOTSTRAP_LOCK_FILE"] = str(self.lock)
        env["OPIP_UI_BOOTSTRAP_GATEWAY_WIRING_FILE"] = str(self.wiring)
        env.update(overrides)
        return env

    def run(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [_bash() or "bash", str(BOOTSTRAP), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
            env=env if env is not None else self.env(),
        )

    def receipt_fields(self) -> dict[str, str]:
        path = self.state / "ui-controller-bootstrap-receipt.env"
        fields: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key] = value
        return fields


@pytest.fixture()
def sandbox(tmp_path: Path):
    _require_toolchain()
    return Sandbox(tmp_path / "sb")


def _skip_on_fork(proc: subprocess.CompletedProcess[str]) -> None:
    if _is_fork_failure(proc):
        pytest.skip("bash cannot fork reliably in this environment")


def _make_shim(directory: Path, name: str, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return directory


def _real_cp() -> str:
    return shutil.which("cp") or "/bin/cp"


def _cp_shim(fail: str, allow: str | None = None) -> str:
    """Fail `cp` only when the SOURCE matches `fail` (and not `allow`).

    ``cp [-p] [--] SRC DST``: the first non-flag argument is the source, so the
    bounded backup (whose source is the installed component) still succeeds while
    a specific installation or restore copy can be made to fail.
    """
    lines = [
        "#!/usr/bin/env bash",
        'src=""',
        'for arg in "$@"; do',
        '  case "$arg" in -*) continue ;; esac',
        '  src="$arg"',
        "  break",
        "done",
        'case "$src" in',
    ]
    if allow is not None:
        lines.append(f"  {allow}) exec {_real_cp()!r} \"$@\" ;;")
    lines.append(f"  {fail}) exit 1 ;;")
    lines.extend(["esac", f"exec {_real_cp()!r} \"$@\""])
    return "\n".join(lines) + "\n"


def _code() -> str:
    return "\n".join(
        line
        for line in BOOTSTRAP.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )


def _install_previous_set(root: Path, sandbox: Sandbox) -> dict[Path, bytes]:
    """A host that already runs every component, but an OLDER revision of each."""
    before: dict[Path, bytes] = {}
    for index, (_rel, _name, mode) in enumerate(COMPONENTS):
        body = f"#!/usr/bin/env bash\n# previous revision of {COMPONENTS[index][1]}\nexit 0\n"
        _write(sandbox.dest(index), body, mode)
        before[sandbox.dest(index)] = sandbox.dest(index).read_bytes()
    return before


def test_ac_006_bootstrap_installs_the_complete_controller_set(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: first-use install succeeds exactly."""
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 0, combined
    assert "OPIP_UI_BOOTSTRAP_STATUS=SUCCESS" in proc.stdout
    for index, (relative, _name, mode) in enumerate(COMPONENTS):
        dest = sandbox.dest(index)
        assert dest.is_file(), dest
        assert dest.read_bytes() == sandbox.object_bytes(relative), relative
        assert dest.stat().st_mode & 0o777 == mode, (relative, oct(dest.stat().st_mode))
    fields = sandbox.receipt_fields()
    assert fields["OPIP_UI_BOOTSTRAP_STATUS"] == "SUCCESS"
    assert fields["OPIP_UI_BOOTSTRAP_TARGET_SHA"] == sandbox.origin_main()


def test_ac_006_ui_release_is_unreachable_from_the_old_gateway(sandbox, tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: the deployed host cannot reach deploy-ui."""
    old = tmp_path / "old-gateway"
    _write(old, OLD_GATEWAY, 0o755)
    proc = subprocess.run(
        [_bash() or "bash", str(old)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env={**os.environ, "SSH_ORIGINAL_COMMAND": f"deploy-ui {'a' * 40}"},
    )
    _skip_on_fork(proc)
    assert proc.returncode == 64
    assert "refusing command" in proc.stderr


def test_ac_006_bootstrap_makes_ui_release_reachable_and_keeps_the_allowlist(sandbox, tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: same key, wider bounded allowlist only."""
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    log = tmp_path / "sudo.log"
    shim = _make_shim(
        tmp_path / "binsudo",
        "sudo",
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$SUDO_LOG\"\nexit 0\n",
    )
    gateway = sandbox.sbin / "ohm-deploy-ssh"
    sha = "b" * 40
    env = {**os.environ, "PATH": f"{shim}{os.pathsep}{os.environ.get('PATH', '')}", "SUDO_LOG": str(log)}
    accepted = subprocess.run(
        [_bash() or "bash", str(gateway)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**env, "SSH_ORIGINAL_COMMAND": f"deploy-ui {sha}"},
    )
    _skip_on_fork(accepted)
    assert accepted.returncode == 0, accepted.stderr
    assert f"ui-deploy {sha}" in log.read_text(encoding="utf-8")
    # Arbitrary commands are still refused, and nothing reaches sudo.
    log.write_text("", encoding="utf-8")
    refused = subprocess.run(
        [_bash() or "bash", str(gateway)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**env, "SSH_ORIGINAL_COMMAND": f"bash -c 'deploy-ui {sha}'"},
    )
    _skip_on_fork(refused)
    assert refused.returncode == 64
    assert "refusing command" in refused.stderr
    assert log.read_text(encoding="utf-8") == ""


def test_ac_006_missing_component_refuses_without_partial_install(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: a missing asset fails closed."""
    _require_toolchain()
    sandbox = Sandbox(tmp_path / "sb", skip=("deploy/remote/lib/opip_ui_release.sh",))
    before = sandbox.sbin.joinpath("ohm-deploy").read_bytes()
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 69, combined
    assert "opip_ui_release.sh is missing from" in combined
    assert sandbox.sbin.joinpath("ohm-deploy").read_bytes() == before
    assert not sandbox.dest(1).exists()
    assert not sandbox.dest(2).exists()
    assert not sandbox.dest(3).exists()


def test_ac_006_repeated_bootstrap_is_idempotent(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: a second run changes nothing."""
    first = sandbox.run("--apply")
    _skip_on_fork(first)
    assert first.returncode == 0, first.stderr
    digest = [_sha256(sandbox.dest(index)) for index in range(len(COMPONENTS))]
    second = sandbox.run("--apply")
    _skip_on_fork(second)
    assert second.returncode == 0, second.stderr
    assert "OPIP_UI_BOOTSTRAP_STATUS=NOT_NEEDED" in second.stdout
    assert [_sha256(sandbox.dest(index)) for index in range(len(COMPONENTS))] == digest
    assert sandbox.receipt_fields()["OPIP_UI_BOOTSTRAP_STATUS"] == "NOT_NEEDED"


def test_ac_006_wrong_mode_is_corrected_not_short_circuited(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: matching bytes with a wrong mode still installs."""
    gateway = sandbox.dest(1)
    body = sandbox.object_bytes("deploy/remote/ohm-deploy-ssh").decode("utf-8")
    _write(gateway, body, 0o644)
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_UI_BOOTSTRAP_STATUS=SUCCESS" in proc.stdout
    assert gateway.stat().st_mode & 0o777 == 0o755


def test_ac_006_installation_failure_restores_the_previous_set(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: a failed install is provably reverted."""
    _require_toolchain()
    sandbox = Sandbox(tmp_path / "sb", install_previous=False)
    before = _install_previous_set(tmp_path / "sb", sandbox)
    shim = _make_shim(
        tmp_path / "bincp",
        "cp",
        _cp_shim("*/opip_ui_release.sh", allow="*controller-bootstrap-previous*"),
    )
    env = sandbox.env()
    env["PATH"] = f"{shim}{os.pathsep}{env['PATH']}"
    proc = sandbox.run("--apply", env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "installation of opip_ui_release.sh failed" in combined
    assert "OPIP_UI_BOOTSTRAP_RESTORE=VERIFIED" in combined
    for path, body in before.items():
        assert path.read_bytes() == body, path


def test_ac_006_installation_and_restore_failure_is_unproven(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: an unprovable restore is reported, never hidden."""
    _require_toolchain()
    sandbox = Sandbox(tmp_path / "sb", install_previous=False)
    _install_previous_set(tmp_path / "sb", sandbox)
    shim = _make_shim(tmp_path / "bincp", "cp", _cp_shim("*opip_ui_release.sh"))
    env = sandbox.env()
    env["PATH"] = f"{shim}{os.pathsep}{env['PATH']}"
    proc = sandbox.run("--apply", env=env)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "OPIP_UI_BOOTSTRAP_RESTORE=UNPROVEN" in combined
    assert "OPIP_UI_BOOTSTRAP_OPERATOR_ACTION=CONTROLLER_RESTORE_UNPROVEN" in combined
    assert "OPIP_UI_BOOTSTRAP_STATUS=FAILED" in combined


def test_ac_006_refuses_a_sha_that_is_not_origin_main(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: only provable current main is installed."""
    older = sandbox.head()
    sandbox.commit_components(extra="second revision\n")
    proc = sandbox.run("--apply", "--sha", older)
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 65, combined
    assert "target SHA does not match origin/main" in combined
    assert not sandbox.dest(1).exists()


def test_ac_006_gateway_wiring_mismatch_is_refused(tmp_path):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: a key wired elsewhere fails closed."""
    _require_toolchain()
    wiring = 'restrict,command="/bin/true" ssh-ed25519 AAAA test\n'
    sandbox = Sandbox(tmp_path / "sb", wiring=wiring)
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 71, combined
    assert "forced-command wiring does not target the stable gateway path" in combined
    assert not sandbox.dest(1).exists()


def test_ac_006_gateway_wiring_is_verified_never_rewritten(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: authorized_keys is read-only to this bootstrap."""
    wiring = 'restrict,command="/usr/local/sbin/ohm-deploy-ssh" ssh-ed25519 AAAA test\n'
    _write(sandbox.wiring, wiring)
    proc = sandbox.run("--apply")
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "OPIP_UI_BOOTSTRAP_GATEWAY_WIRING=OK" in proc.stdout
    assert sandbox.wiring.read_text(encoding="utf-8") == wiring


def test_ac_006_check_mode_reports_without_changing_anything(sandbox):
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: --check is read-only."""
    proc = sandbox.run("--check")
    _skip_on_fork(proc)
    assert proc.returncode == 0, proc.stderr
    assert "component_ohm-deploy-ssh=ABSENT" in proc.stdout
    assert not sandbox.dest(1).exists()
    assert not sandbox.state.joinpath("ui-controller-bootstrap-receipt.env").exists()


def test_ac_006_bootstrap_touches_no_forbidden_control_plane():
    """ATDD-OPIP-UI-ONLY-RELEASE-CONTROLLER-v1/AC-006: no deployment or infra mutation path."""
    code = _code()
    for forbidden in (
        "docker",
        "systemctl",
        "crontab",
        "/etc/cron.d",
        "reconcile-scheduler",
        "bootstrap-remote-ops",
        "sudoers",
        "last-good-sha",
        "deploy-learning",
    ):
        assert forbidden not in code, forbidden
    # authorized_keys is named exactly once, in the read-only wiring constant.
    assert code.count("authorized_keys") == 1
    assert ".ssh/authorized_keys" in code
    # Nothing redirects INTO the wiring file.
    assert '> "$GATEWAY_WIRING_FILE"' not in code
