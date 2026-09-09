"""Functional contract: one-shot coverage-discontinuity env reaches docker.

Proven production failure (PR #226 recovery): ENV_FILE was sourced but not
exported, so bare `docker run -e NAME` dropped the one-shot authorization and
outcomes failed closed with LEGACY_COVERAGE_DISCONTINUITY_REQUIRED.

These tests execute the real job runner against a fake docker that records the
process environment the Docker CLI would see for bare `-e NAME` forwarding.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEARNING = ROOT / "deploy" / "learning"
RUNNER = LEARNING / "opip-learning-job.sh"

ONESHOT_ESTABLISH = "OPIP_LEARNING_ESTABLISH_COVERAGE_DISCONTINUITY"
ONESHOT_PREFIX = "OPIP_LEARNING_COVERAGE_DISCONTINUITY_ARCHIVE_PREFIX"
ONESHOT_SHA = "OPIP_LEARNING_COVERAGE_DISCONTINUITY_EXPECTED_STATE_SHA"
LEGACY_STATE_SHA = (
    "d14f062d11d73cd16d797af1a2ae9b647fb42d40796d2a0da02d3fb62501d997"
)
ARCHIVE_PREFIX = "screening_evaluations"
SECRET_KEY = "OPIP_SECRET_CREDENTIAL"
SECRET_VALUE = "must-not-reach-container"
KRAKEN_KEY = "KRAKEN_API_KEY"
KRAKEN_VALUE = "kraken-must-not-reach-container"

_EXPORT_MARK_START = "for _opip_oneshot_var in"
_EXPORT_MARK_END = "unset -v _opip_oneshot_var"


def _strip_oneshot_export_loop(source: str) -> str:
    start = source.index(_EXPORT_MARK_START)
    end = source.index(_EXPORT_MARK_END, start) + len(_EXPORT_MARK_END)
    # Drop the preceding comment block that documents the export contract.
    comment_anchor = (
        "# Bare `docker run -e NAME` (no `=value`) only forwards *exported* process"
    )
    comment_start = source.rfind(comment_anchor, 0, start)
    if comment_start != -1:
        start = comment_start
    return source[:start] + "\n" + source[end:].lstrip("\n")


_requires_linux_job_harness = pytest.mark.skipif(
    sys.platform == "win32",
    reason="Git Bash flock/fork is unreliable on Windows; Linux CI executes this path",
)


def _write_exec(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _prepare_workspace(
    tmp_path: Path,
    *,
    oneshot: bool,
    runner_source: str | None = None,
) -> dict[str, Path | str]:
    state = tmp_path / "state"
    data = tmp_path / "data"
    lock = tmp_path / "plane.lock"
    env_file = tmp_path / "opip-learning.env"
    bin_dir = tmp_path / "bin"
    dumps = tmp_path / "dumps"
    state.mkdir()
    data.mkdir()
    bin_dir.mkdir()
    dumps.mkdir()

    worker = "bcc90925097c39a9d94941bc47771b2520b2898d"
    env_lines = [
        f"OPIP_LEARNING_IMAGE=opip-learning:{worker}",
        f"OPIP_DEPLOYED_SHA={worker}",
        f"{SECRET_KEY}={SECRET_VALUE}",
        f"{KRAKEN_KEY}={KRAKEN_VALUE}",
    ]
    if oneshot:
        # Plain KEY=value (not `export KEY=`) — matches production env file.
        env_lines.extend(
            [
                f"{ONESHOT_ESTABLISH}=1",
                f"{ONESHOT_PREFIX}={ARCHIVE_PREFIX}",
                f"{ONESHOT_SHA}={LEGACY_STATE_SHA}",
            ]
        )
    env_file.write_text("\n".join(env_lines) + "\n", encoding="utf-8", newline="\n")
    # Prove the file does not use export KEY= form.
    raw = env_file.read_text(encoding="utf-8")
    assert f"export {ONESHOT_ESTABLISH}" not in raw
    assert f"export {SECRET_KEY}" not in raw

    (data / "manifest.env").write_text(
        f"schema_version=4\nproduction_deployed_sha={worker}\np1_shadow_outbox_retired=1\n",
        encoding="utf-8",
        newline="\n",
    )

    oneshot_dump = dumps / "oneshot.env"
    env_dump = dumps / "docker.env"
    call_log = dumps / "calls.log"
    call_log.write_text("", encoding="utf-8")

    _write_exec(
        bin_dir / "docker",
        f"""#!/usr/bin/env bash
set -euo pipefail
echo "$*" >> "{call_log.as_posix()}"
case "${{1:-}}" in
  ps|rm)
    exit 0
    ;;
  run)
    {{
      echo "ESTABLISH=${{{ONESHOT_ESTABLISH}-<UNSET>}}"
      echo "PREFIX=${{{ONESHOT_PREFIX}-<UNSET>}}"
      echo "SHA=${{{ONESHOT_SHA}-<UNSET>}}"
      echo "SECRET=${{{SECRET_KEY}-<UNSET>}}"
      echo "KRAKEN=${{{KRAKEN_KEY}-<UNSET>}}"
    }} > "{oneshot_dump.as_posix()}"
    env | sort > "{env_dump.as_posix()}"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
""",
    )
    _write_exec(
        bin_dir / "timeout",
        """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 && "$1" != docker ]]; do
  shift
done
exec "$@"
""",
    )
    # flock exists on Linux CI; provide a no-op only if missing.
    if shutil.which("flock") is None:
        _write_exec(
            bin_dir / "flock",
            """#!/usr/bin/env bash
set -euo pipefail
while [[ $# -gt 0 ]]; do
  case "$1" in
    -*) shift ;;
    *) break ;;
  esac
done
shift || true
exec "$@"
""",
        )

    script_path = tmp_path / "opip-learning-job.sh"
    source = runner_source if runner_source is not None else RUNNER.read_text(encoding="utf-8")
    script_path.write_text(source, encoding="utf-8", newline="\n")
    script_path.chmod(script_path.stat().st_mode | stat.S_IEXEC)

    return {
        "script": script_path,
        "env_file": env_file,
        "data": data,
        "state": state,
        "lock": lock,
        "bin_dir": bin_dir,
        "oneshot_dump": oneshot_dump,
        "env_dump": env_dump,
        "call_log": call_log,
        "worker": worker,
    }


def _run_job(ws: dict[str, Path | str], *, job: str = "outcomes") -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "OPIP_LEARNING_ENV_FILE": str(ws["env_file"]),
        "OPIP_LEARNING_LOCK_FILE": str(ws["lock"]),
        "OPIP_LEARNING_DATA_ROOT": str(ws["data"]),
        "OPIP_LEARNING_STATE_ROOT": str(ws["state"]),
        # Ensure one-shot keys are NOT pre-exported by the parent test process.
        "PATH": f"{ws['bin_dir']}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    for key in (ONESHOT_ESTABLISH, ONESHOT_PREFIX, ONESHOT_SHA, SECRET_KEY, KRAKEN_KEY):
        env.pop(key, None)
    return subprocess.run(
        ["bash", str(ws["script"]), job],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


def _read_oneshot_dump(path: Path) -> dict[str, str]:
    payload: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        payload[key] = value
    return payload


def test_job_script_exports_only_oneshot_trio_after_source():
    source = RUNNER.read_text(encoding="utf-8")
    subprocess.run(["bash", "-n", str(RUNNER)], check=True)
    source_idx = source.index('source "$ENV_FILE"')
    export_idx = source.index("for _opip_oneshot_var in")
    docker_e_idx = source.index(f"-e {ONESHOT_ESTABLISH}")
    assert source_idx < export_idx < docker_e_idx
    assert "export \"${_opip_oneshot_var}\"" in source
    assert "--env-file" not in source
    # Must not blanket-export the env file.
    assert "set -a" not in source.split('source "$ENV_FILE"', 1)[1].split(": \"${OPIP_LEARNING_IMAGE", 1)[0]
    assert "export $((" not in source


@_requires_linux_job_harness
def test_plain_env_file_oneshot_values_reach_docker_process_env(tmp_path: Path):
    ws = _prepare_workspace(tmp_path, oneshot=True)
    result = _run_job(ws)
    assert result.returncode == 0, (result.stdout, result.stderr)
    calls = Path(ws["call_log"]).read_text(encoding="utf-8")
    assert "run --rm" in calls
    dump = _read_oneshot_dump(Path(ws["oneshot_dump"]))
    assert dump["ESTABLISH"] == "1"
    assert dump["PREFIX"] == ARCHIVE_PREFIX
    assert dump["SHA"] == LEGACY_STATE_SHA
    # Exact identity survival.
    assert dump["SHA"] == LEGACY_STATE_SHA
    assert dump["PREFIX"] == "screening_evaluations"


@_requires_linux_job_harness
def test_absent_oneshot_keys_leave_migration_disabled(tmp_path: Path):
    ws = _prepare_workspace(tmp_path, oneshot=False)
    result = _run_job(ws)
    assert result.returncode == 0, (result.stdout, result.stderr)
    dump = _read_oneshot_dump(Path(ws["oneshot_dump"]))
    assert dump["ESTABLISH"] == "<UNSET>"
    assert dump["PREFIX"] == "<UNSET>"
    assert dump["SHA"] == "<UNSET>"


@_requires_linux_job_harness
def test_unrelated_env_file_secrets_are_not_forwarded_to_docker(tmp_path: Path):
    ws = _prepare_workspace(tmp_path, oneshot=True)
    result = _run_job(ws)
    assert result.returncode == 0, (result.stdout, result.stderr)
    dump = _read_oneshot_dump(Path(ws["oneshot_dump"]))
    assert dump["SECRET"] == "<UNSET>"
    assert dump["KRAKEN"] == "<UNSET>"
    env_dump = Path(ws["env_dump"]).read_text(encoding="utf-8")
    assert SECRET_VALUE not in env_dump
    assert KRAKEN_VALUE not in env_dump
    assert f"{SECRET_KEY}=" not in env_dump
    assert f"{KRAKEN_KEY}=" not in env_dump


@_requires_linux_job_harness
def test_recurring_job_after_oneshot_key_removal_cannot_reauthorize(tmp_path: Path):
    """After keys are removed from ENV_FILE, a later invocation stays disabled."""
    ws = _prepare_workspace(tmp_path, oneshot=True)
    first = _run_job(ws)
    assert first.returncode == 0, (first.stdout, first.stderr)
    assert _read_oneshot_dump(Path(ws["oneshot_dump"]))["ESTABLISH"] == "1"

    # Remove one-shot authorization; keep unrelated and required image/sha lines.
    env_path = Path(ws["env_file"])
    kept = [
        line
        for line in env_path.read_text(encoding="utf-8").splitlines()
        if not line.startswith(
            (
                f"{ONESHOT_ESTABLISH}=",
                f"{ONESHOT_PREFIX}=",
                f"{ONESHOT_SHA}=",
            )
        )
    ]
    env_path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    Path(ws["oneshot_dump"]).unlink(missing_ok=True)

    second = _run_job(ws)
    assert second.returncode == 0, (second.stdout, second.stderr)
    dump = _read_oneshot_dump(Path(ws["oneshot_dump"]))
    assert dump["ESTABLISH"] == "<UNSET>"
    assert dump["PREFIX"] == "<UNSET>"
    assert dump["SHA"] == "<UNSET>"


@_requires_linux_job_harness
def test_legacy_unexported_source_fails_functional_propagation(tmp_path: Path):
    """Regression: pre-fix runner (source without export) fails this contract."""
    legacy = RUNNER.read_text(encoding="utf-8")
    assert _EXPORT_MARK_START in legacy, "export loop missing from current runner"
    legacy = _strip_oneshot_export_loop(legacy)
    assert _EXPORT_MARK_START not in legacy
    assert 'export "${_opip_oneshot_var}"' not in legacy

    ws = _prepare_workspace(tmp_path, oneshot=True, runner_source=legacy)
    result = _run_job(ws)
    assert result.returncode == 0, (result.stdout, result.stderr)
    dump = _read_oneshot_dump(Path(ws["oneshot_dump"]))
    # Plain sourced KEY=value without export must not reach docker's process env.
    assert dump["ESTABLISH"] == "<UNSET>"
    assert dump["PREFIX"] == "<UNSET>"
    assert dump["SHA"] == "<UNSET>"
