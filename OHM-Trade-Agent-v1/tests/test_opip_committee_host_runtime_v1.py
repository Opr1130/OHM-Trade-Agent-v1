"""Host runtime provisioning for the isolated Committee worker (Module 2H).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests exercise the committed provisioner in a temporary root. They do not
contact a host, print provider credential values, or change trading authority.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
COMMITTEE = REPO_ROOT / "OHM-Trade-Agent-v1" / "deploy" / "committee"
SERVICE = COMMITTEE / "opip-committee-shadow.service"
PROVISION = COMMITTEE / "provision-committee-host-runtime.sh"
ROLLBACK = COMMITTEE / "rollback-committee-host-runtime.sh"
VERIFY = COMMITTEE / "verify-committee-runtime.sh"
LIB = COMMITTEE / "committee-host-runtime-lib.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-committee.yml"

SHA_A = "a" * 40
SHA_B = "b" * 40
OPENAI_SENTINEL = "SENTINEL_OPENAI_CREDENTIAL_VALUE"
ANTHROPIC_SENTINEL = "SENTINEL_ANTHROPIC_CREDENTIAL_VALUE"
_BASH_LAUNCH_FAILURE = 3221225794


def _bash() -> str | None:
    found = shutil.which("bash")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if pathlib.Path(candidate).exists():
            return candidate
    return None


def _is_bash_launch_failure(proc: subprocess.CompletedProcess[str]) -> bool:
    return (
        proc.returncode == _BASH_LAUNCH_FAILURE
        and not proc.stdout
        and not proc.stderr
    )


def _run(argv: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    for _ in range(3):
        proc = subprocess.run(argv, capture_output=True, text=True, env=env)
        if _is_bash_launch_failure(proc):
            continue
        return proc
    pytest.skip(
        "bash could not be launched on this host "
        f"(status {_BASH_LAUNCH_FAILURE:#x}); no verdict was produced"
    )


def _require_bash() -> str:
    bash = _bash()
    if bash is None:
        pytest.skip("no bash available to exercise host runtime provisioning")
    return bash


def _write_executable(path: pathlib.Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _install_fake_python(bin_dir: pathlib.Path) -> None:
    bin_dir.mkdir(parents=True)
    venv_python = r"""#!/usr/bin/env bash
log="${OPIP_TEST_TOOL_LOG:-}"
if [[ -n "$log" ]]; then printf '%s\n' "$*" >> "$log"; fi
if [[ "${1:-}" == "-m" && "${2:-}" == "pip" ]]; then
  if [[ "${OPIP_TEST_FAIL_PIP:-}" == "1" ]]; then exit 1; fi
  exit 0
fi
if [[ "${1:-}" == "-s" && "${2:-}" == "-c" ]]; then
  if [[ -f app/opip/committee/cycle_runner.py ]]; then exit 0; fi
  exit 1
fi
exit 0
"""
    launcher = f"""#!/usr/bin/env bash
log="${{OPIP_TEST_TOOL_LOG:-}}"
if [[ -n "$log" ]]; then printf '%s\\n' "$*" >> "$log"; fi
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
  dest="${{3:?}}"
  mkdir -p "$dest/bin"
  cat > "$dest/bin/python" << 'EOF'
{venv_python}EOF
  chmod +x "$dest/bin/python"
  exit 0
fi
echo "unexpected python3 invocation" >&2
exit 1
"""
    _write_executable(bin_dir / "python3", launcher)


def _source_tree(root: pathlib.Path) -> pathlib.Path:
    package = root / "app" / "opip" / "committee"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "cycle_runner.py").write_text("VALUE = 1\n", encoding="utf-8")
    (root / "requirements.txt").write_text("fastapi>=0.115,<1\n", encoding="utf-8")
    return root


def _env_text(*, mode: str = "shadow", cases: str = "8", sha: str = "CHANGEME") -> str:
    return (
        f"OPIP_COMMITTEE_OPENAI_API_KEY={OPENAI_SENTINEL}\n"
        f"OPIP_COMMITTEE_ANTHROPIC_API_KEY={ANTHROPIC_SENTINEL}\n"
        f"OPIP_COMMITTEE_MODE={mode}\n"
        f"OPIP_COMMITTEE_MODE=shadow\n"
        f"OPIP_COMMITTEE_MAX_CASES_PER_CYCLE={cases}\n"
        f"OPIP_COMMITTEE_RELEASE_SHA={sha}\n"
    )


def _layout(tmp: pathlib.Path) -> dict[str, pathlib.Path]:
    prefix = tmp / "prefix"
    prefix.mkdir()
    source = _source_tree(tmp / "source")
    env_file = tmp / "committee-credentials.env"
    env_file.write_text(_env_text(), encoding="utf-8", newline="\n")
    manifest = tmp / "manifest.env"
    manifest.write_text("RELEASE_SHA=test\n", encoding="utf-8", newline="\n")
    replica = tmp / "replica"
    replica.mkdir()
    unit = tmp / "opip-committee-shadow.service"
    unit.write_text(SERVICE.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    fake_bin = tmp / "fake-bin"
    _install_fake_python(fake_bin)
    return {
        "prefix": prefix,
        "source": source,
        "env": env_file,
        "manifest": manifest,
        "replica": replica,
        "unit": unit,
        "fake_bin": fake_bin,
        "log": prefix / "tool.log",
    }


def _env(layout: dict[str, pathlib.Path], **extra: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "OPIP_COMMITTEE_RUNTIME_TEST_HARNESS": "1",
            "OPIP_RUNTIME_PREFIX": str(layout["prefix"]),
            "OPIP_COMMITTEE_ENV_FILE": str(layout["env"]),
            "OPIP_COMMITTEE_SOURCE_ROOT": str(layout["source"]),
            "OPIP_TEST_MANIFEST": str(layout["manifest"]),
            "OPIP_TEST_REPLICA": str(layout["replica"]),
            "OPIP_TEST_UNIT_FILE": str(layout["unit"]),
            "OPIP_TEST_TOOL_LOG": str(layout["log"]),
            "PATH": str(layout["fake_bin"]) + os.pathsep + env.get("PATH", ""),
        }
    )
    env.pop("OPIP_TEST_FAIL_PIP", None)
    env.update(extra)
    return env


def _provision(
    bash: str, layout: dict[str, pathlib.Path], sha: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    return _run([bash, str(PROVISION), sha], _env(layout, **extra))


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    return f"{proc.stdout}\n{proc.stderr}"


def _assert_secrets_hidden(proc: subprocess.CompletedProcess[str]) -> None:
    output = _combined(proc)
    assert OPENAI_SENTINEL not in output
    assert ANTHROPIC_SENTINEL not in output


def _credential_lines(text: str) -> list[str]:
    return [
        line
        for line in text.splitlines()
        if line.startswith("OPIP_COMMITTEE_OPENAI_API_KEY=")
        or line.startswith("OPIP_COMMITTEE_ANTHROPIC_API_KEY=")
    ]


def _key_counts(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0]
        if key.isidentifier():
            counts[key] = counts.get(key, 0) + 1
    return counts


def test_cycle_runner_module_imports() -> None:
    import app.opip.committee.cycle_runner

    assert app.opip.committee.cycle_runner.EXIT_OK == 0


_IMPORT_PROOF = """
import os, sys
root = os.path.realpath(os.getcwd())
entry = sys.path[0]
resolved = root if entry in ("", ".") else os.path.realpath(entry)
if os.path.realpath(resolved) != root:
    raise SystemExit(2)
import app.opip.committee.cycle_runner
"""


def test_the_import_proof_snippet_resolves_the_release_root() -> None:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    app_root = pathlib.Path(__file__).resolve().parents[1]
    proc = subprocess.run(
        [sys.executable, "-s", "-c", _IMPORT_PROOF],
        cwd=app_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    lib = LIB.read_text(encoding="utf-8")
    verify = VERIFY.read_text(encoding="utf-8")
    assert "import app.opip.committee.cycle_runner" in lib
    assert "import app.opip.committee.cycle_runner" in verify
    assert "entry in (\"\", \".\")" in lib
    assert "entry in (\"\", \".\")" in verify


@pytest.mark.parametrize(
    "sha",
    ["main", "HEAD", "feature/module2h", "A" * 40, "a" * 39, "a" * 41, "not-a-sha"],
)
def test_exact_sha_is_required_and_refs_are_refused(sha: str) -> None:
    bash = _require_bash()
    proc = _run([bash, str(PROVISION), sha], os.environ.copy())
    assert proc.returncode == 64
    assert "40-character" in proc.stderr


def test_missing_sha_is_refused() -> None:
    bash = _require_bash()
    proc = _run([bash, str(PROVISION)], os.environ.copy())
    assert proc.returncode == 64


def test_library_cannot_be_executed_directly() -> None:
    bash = _require_bash()
    proc = _run([bash, str(LIB)], os.environ.copy())
    assert proc.returncode == 64


def test_provision_installs_the_release_and_proves_import(tmp_path: pathlib.Path) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    proc = _provision(bash, layout, SHA_A)
    assert proc.returncode == 0, _combined(proc)
    assert f"COMMITTEE_RUNTIME_INSTALL=PASS sha={SHA_A}" in proc.stdout
    _assert_secrets_hidden(proc)
    env_text = layout["env"].read_text(encoding="utf-8")
    assert _credential_lines(env_text) == [
        f"OPIP_COMMITTEE_OPENAI_API_KEY={OPENAI_SENTINEL}",
        f"OPIP_COMMITTEE_ANTHROPIC_API_KEY={ANTHROPIC_SENTINEL}",
    ]
    counts = _key_counts(env_text)
    assert all(value == 1 for value in counts.values())
    assert counts["OPIP_COMMITTEE_MODE"] == 1
    assert "OPIP_COMMITTEE_MODE=off" in env_text
    assert "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE=1" in env_text
    assert f"OPIP_COMMITTEE_RELEASE_SHA={SHA_A}" in env_text
    assert "OPIP_APP_ROOT=" in env_text
    assert "OPIP_VENV_PYTHON=" in env_text
    marker = (layout["prefix"] / "app" / ".opip-release-sha").read_text(encoding="utf-8")
    assert marker.strip() == SHA_A
    assert (layout["prefix"] / "timer-enablement").read_text(encoding="utf-8").strip() == "disabled"
    assert (layout["prefix"] / "timer-active").read_text(encoding="utf-8").strip() == "inactive"
    assert list(layout["prefix"].glob("staging.*")) == []
    log = layout["log"].read_text(encoding="utf-8")
    assert log.count("-m pip") == 1
    assert "requirements-dev.txt" not in log

    proof = _run([bash, str(VERIFY)], _env(layout))
    assert proof.returncode == 0, _combined(proof)
    assert "COMMITTEE_RUNTIME_PROOF=PASS" in proof.stdout
    assert "cycle_runner import succeeded" in proof.stdout
    assert "provider_call_count=0" in proof.stdout
    assert "mode is off" in proof.stdout
    assert "max cases per cycle is 1" in proof.stdout
    assert "timer is disabled" in proof.stdout
    assert "timer is inactive" in proof.stdout
    assert "deny-all" in proof.stdout
    assert "no provider egress drop-in" in proof.stdout
    _assert_secrets_hidden(proof)


def test_repeat_install_of_the_same_sha_does_not_rebuild_or_duplicate_config(
    tmp_path: pathlib.Path,
) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    first = _provision(bash, layout, SHA_A)
    assert first.returncode == 0, _combined(first)
    second = _provision(bash, layout, SHA_A)
    assert second.returncode == 0, _combined(second)
    _assert_secrets_hidden(second)
    log = layout["log"].read_text(encoding="utf-8")
    assert log.count("-m venv") == 1
    assert log.count("-m pip") == 1
    counts = _key_counts(layout["env"].read_text(encoding="utf-8"))
    assert all(value == 1 for value in counts.values())
    assert (layout["prefix"] / "timer-enablement").read_text(encoding="utf-8").strip() == "disabled"


def test_dependency_install_failure_keeps_the_previous_runtime(
    tmp_path: pathlib.Path,
) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    first = _provision(bash, layout, SHA_A)
    assert first.returncode == 0, _combined(first)
    before = layout["env"].read_text(encoding="utf-8")
    failed = _provision(bash, layout, SHA_B, OPIP_TEST_FAIL_PIP="1")
    assert failed.returncode != 0
    assert "COMMITTEE_RUNTIME_INSTALL=FAIL" in failed.stderr
    _assert_secrets_hidden(failed)
    marker = (layout["prefix"] / "app" / ".opip-release-sha").read_text(encoding="utf-8")
    assert marker.strip() == SHA_A
    assert list(layout["prefix"].glob("staging.*")) == []
    after = layout["env"].read_text(encoding="utf-8")
    assert _credential_lines(after) == _credential_lines(before)
    assert "OPIP_COMMITTEE_MODE=off" in after
    assert (layout["prefix"] / "timer-enablement").read_text(encoding="utf-8").strip() == "disabled"
    log = layout["log"].read_text(encoding="utf-8")
    assert log.count("-m venv") == 2
    assert sum(1 for line in log.splitlines() if "-m pip" in line and "install" in line) == 2


def test_symlink_in_the_release_tree_is_refused(tmp_path: pathlib.Path) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("nope\n", encoding="utf-8")
    link = layout["source"] / "app" / "escape"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are not available on this host")
    proc = _provision(bash, layout, SHA_A)
    assert proc.returncode != 0
    assert "symlink" in proc.stderr.lower()
    assert not (layout["prefix"] / "app").exists()
    assert list(layout["prefix"].glob("staging.*")) == []
    _assert_secrets_hidden(proc)


def test_rollback_restores_the_previous_release(tmp_path: pathlib.Path) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    assert _provision(bash, layout, SHA_A).returncode == 0
    second = _provision(bash, layout, SHA_B)
    assert second.returncode == 0, _combined(second)
    before = _credential_lines(layout["env"].read_text(encoding="utf-8"))
    proc = _run([bash, str(ROLLBACK)], _env(layout))
    assert proc.returncode == 0, _combined(proc)
    assert "ROLLBACK_RUNTIME=PASS" in proc.stdout
    _assert_secrets_hidden(proc)
    marker = (layout["prefix"] / "app" / ".opip-release-sha").read_text(encoding="utf-8")
    assert marker.strip() == SHA_A
    previous = (
        layout["prefix"] / "previous" / "app" / ".opip-release-sha"
    ).read_text(encoding="utf-8")
    assert previous.strip() == SHA_B
    env_text = layout["env"].read_text(encoding="utf-8")
    assert _credential_lines(env_text) == before
    assert "OPIP_COMMITTEE_MODE=off" in env_text
    assert f"OPIP_COMMITTEE_RELEASE_SHA={SHA_A}" in env_text
    assert (layout["prefix"] / "timer-enablement").read_text(encoding="utf-8").strip() == "disabled"


def test_runtime_proof_fails_closed_when_egress_or_the_timer_is_opened(
    tmp_path: pathlib.Path,
) -> None:
    bash = _require_bash()
    layout = _layout(tmp_path)
    assert _provision(bash, layout, SHA_A).returncode == 0
    dropin = layout["prefix"] / "dropin"
    dropin.mkdir()
    (dropin / "allow.conf").write_text("IPAddressAllow=203.0.113.10\n", encoding="utf-8")
    opened = _run([bash, str(VERIFY)], _env(layout))
    assert opened.returncode != 0
    assert "COMMITTEE_RUNTIME_PROOF=FAIL" in opened.stdout
    assert "203.0.113.10" not in _combined(opened)
    _assert_secrets_hidden(opened)
    (dropin / "allow.conf").unlink()
    (layout["prefix"] / "timer-enablement").write_text("enabled\n", encoding="utf-8")
    timer = _run([bash, str(VERIFY)], _env(layout))
    assert timer.returncode != 0
    assert "timer is not disabled" in timer.stdout


def test_provision_scripts_do_not_open_egress_or_touch_trading_credentials() -> None:
    lib = LIB.read_text(encoding="utf-8")
    provision = PROVISION.read_text(encoding="utf-8")
    rollback = ROLLBACK.read_text(encoding="utf-8")
    combined = "\n".join((lib, provision, rollback))
    for forbidden in (
        "systemctl enable",
        "systemctl start",
        "IPAddressAllow=",
        "KRAKEN",
        "TELEGRAM",
        "requirements-dev.txt",
        "set -x",
        "docker ",
        "printenv",
    ):
        assert forbidden not in combined, forbidden
    assert 'pip install' in lib
    assert 'requirements.txt' in lib
    assert "systemctl stop opip-committee-shadow.timer" in lib
    assert "python3-venv" in lib
    venv_fn = lib.split("ensure_python_venv()", 1)[1].split("provision_main()", 1)[0]
    assert venv_fn.index("OPIP_COMMITTEE_RUNTIME_TEST_HARNESS") < venv_fn.index("apt-get install")
    bootstrap = (COMMITTEE / "bootstrap-opip-committee-worker.sh").read_text(encoding="utf-8")
    assert any(
        line.strip() == 'bash "$SOURCE_DIR/provision-committee-host-runtime.sh" "$TARGET_SHA"'
        for line in bootstrap.splitlines()
    )
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "OPIP_COMMITTEE_RUNTIME_TEST_HARNESS" not in workflow
    assert "verify-committee-runtime.sh" in workflow
    assert "COMMITTEE_RUNTIME_PROOF=PASS" in workflow
    assert 'test "$RUNTIME_RESULT" = "PROVEN"' in workflow
