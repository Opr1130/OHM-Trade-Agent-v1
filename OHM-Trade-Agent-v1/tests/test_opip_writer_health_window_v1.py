"""The deploy's writer health wait: wider, still bounded, and never hiding a failing writer.

Each test cites its criterion in ATDD-OPIP-WRITER-HEALTH-WINDOW-V1. The function body is
extracted from ``deploy/remote/ohm-deploy`` and run under bash with stub ``docker`` and
``sleep`` commands, so the real control flow is exercised without Docker or real waiting.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
DEPLOY = APP_ROOT / "deploy" / "remote" / "ohm-deploy"
requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")

HEALTHY = "running|false|false|0|healthy"
STARTING = "running|false|false|0|starting"
UNHEALTHY = "running|false|false|0|unhealthy"


def _function_source() -> str:
    text = DEPLOY.read_text(encoding="utf-8")
    start = text.index("WRITER_HEALTH_TIMEOUT_SECONDS=")
    end = text.index("validate_writer_resource_limits() {")
    return text[start:end]


def _run(tmp_path: Path, states: list[str], container: bool = True) -> tuple[int, str, list[int]]:
    """Run the real function; ``states`` is the docker-inspect line returned per poll
    (the last one repeats). Returns (exit code, stderr, sleep arguments)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "states").write_text("\n".join(states) + "\n", encoding="utf-8")
    (tmp_path / "count").write_text("0", encoding="utf-8")
    (bin_dir / "docker").write_text(
        f"""#!/usr/bin/env bash
if [ "$1" = compose ] && [ "$2" = ps ] && [ "$3" = -q ]; then
  {'echo cid123' if container else 'true'}; exit 0
fi
if [ "$1" = compose ]; then echo "compose ps output"; exit 0; fi
if [ "$1" = inspect ]; then
  n=$(cat {tmp_path}/count); total=$(wc -l < {tmp_path}/states)
  line=$((n + 1)); [ "$line" -gt "$total" ] && line="$total"
  sed -n "${{line}}p" {tmp_path}/states
  echo $((n + 1)) > {tmp_path}/count; exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    (bin_dir / "sleep").write_text(f'#!/usr/bin/env bash\necho "$1" >> {tmp_path}/sleeps\n', encoding="utf-8")
    for name in ("docker", "sleep"):
        (bin_dir / name).chmod(0o755)
    script = tmp_path / "run.sh"
    script.write_text(
        "set -Eeuo pipefail\n" + _function_source() + "\nif wait_writer_health; then exit 0; else exit 1; fi\n",
        encoding="utf-8",
    )
    done = subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    sleeps_file = tmp_path / "sleeps"
    sleeps = [int(x) for x in sleeps_file.read_text().split()] if sleeps_file.exists() else []
    return done.returncode, done.stderr, sleeps


def test_ac_001_the_window_is_a_fixed_180_second_constant_with_no_override():
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-001: 180 s, named, bounded; not an environment-overridable value."""
    source = _function_source()
    assert re.search(r"^WRITER_HEALTH_TIMEOUT_SECONDS=180$", source, flags=re.MULTILINE)
    assert re.search(r"^WRITER_HEALTH_POLL_SECONDS=2$", source, flags=re.MULTILINE)
    assert "${WRITER_HEALTH_TIMEOUT_SECONDS:-" not in source  # no override path
    assert "seq 1 30" not in source


@requires_bash
def test_ac_001_a_slow_but_working_start_up_inside_180_seconds_succeeds(tmp_path):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-001: still-starting then 'unhealthy' (probe fails until the socket listens) then healthy at ~140 s is a success."""
    states = [STARTING] * 10 + [UNHEALTHY] * 60 + [HEALTHY]
    rc, err, sleeps = _run(tmp_path, states)
    assert rc == 0, err
    assert sum(sleeps) == 140 and len(sleeps) == 70


@requires_bash
def test_ac_001_an_immediately_healthy_writer_does_not_wait(tmp_path):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-001: the wider window adds no delay when the writer is healthy at once."""
    rc, _, sleeps = _run(tmp_path, [HEALTHY])
    assert rc == 0 and sleeps == []


@requires_bash
def test_ac_002_a_writer_that_never_becomes_healthy_fails_after_exactly_180_seconds(tmp_path):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-003: worst case is bounded: 90 polls of 2 s, then the deploy fails (which triggers the existing rollback)."""
    rc, err, sleeps = _run(tmp_path, [UNHEALTHY])
    assert rc == 1
    assert sum(sleeps) == 180 and len(sleeps) == 90
    assert "not healthy within 180s" in err


@requires_bash
def test_ac_002_a_missing_container_is_bounded_not_endless(tmp_path):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-003: no container id for the whole window still ends at 180 s."""
    rc, err, sleeps = _run(tmp_path, [HEALTHY], container=False)
    assert rc == 1 and sum(sleeps) == 180
    assert "health check failed" in err


@requires_bash
@pytest.mark.parametrize(
    "bad_state,needle",
    [
        ("exited|false|false|0|unhealthy", "status=exited"),
        ("dead|false|false|0|none", "status=dead"),
        ("restarting|true|false|3|starting", "restarting=true"),
        ("running|false|true|0|starting", "oom_killed=true"),
    ],
)
def test_ac_002_a_crashed_or_oom_killed_writer_fails_immediately(tmp_path, bad_state, needle):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-002: the longer window must not conceal a genuinely failing writer; these fail on the first poll with no waiting."""
    rc, err, sleeps = _run(tmp_path, [bad_state])
    assert rc == 1 and sleeps == []
    assert needle in err


@requires_bash
def test_ac_002_a_restart_loop_is_detected_before_the_window_ends(tmp_path):
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-002: RestartCount rising during the wait fails at once even though each restart looks 'running'."""
    states = ["running|false|false|0|starting"] * 3 + ["running|false|false|1|starting"]
    rc, err, sleeps = _run(tmp_path, states)
    assert rc == 1
    assert len(sleeps) == 3  # detected on the 4th poll, not after 180 s
    assert "restart-looping" in err


def test_ac_003_both_call_sites_still_fail_the_deploy_and_the_state_template_is_pinned():
    """ATDD-OPIP-WRITER-HEALTH-WINDOW-V1/AC-003: callers are unchanged (a failed wait still fails the deploy and enters the existing rollback), and the inspect template carries the five fields the logic parses."""
    text = DEPLOY.read_text(encoding="utf-8")
    assert text.count("wait_writer_health") == 3  # definition + two guarded callers
    assert "if ! wait_writer_health; then" in text
    assert 'echo "production writer health check failed" >&2' in text
    for field in (".State.Status", ".State.Restarting", ".State.OOMKilled", ".RestartCount", ".State.Health.Status"):
        assert field in _function_source()
