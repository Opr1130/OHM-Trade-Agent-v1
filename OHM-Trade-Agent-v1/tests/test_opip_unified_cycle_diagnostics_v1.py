"""Contracts for the read-only unified-cycle observability block.

A production `EVIDENCE_SHADOW` release fails closed when no unified cycle reports
`SUCCESS` inside the bounded release window, but the deploy records only that
verdict. On its own the verdict cannot separate "the scheduler stopped invoking
the cycle" from "cycles run but never complete / never report `SUCCESS`", which
is exactly the question an operator has to answer after a fail-closed rollback.

The diagnostics helper gained an observer-only block that reports the installed
cycle cron entry and a bounded, redacted tail of the cycle's own log. These
tests are the guardrail on that block:

* it must stay strictly observational - it never runs, signals, times out or
  repairs the cycle, never takes a lock, and never writes a file. Its own tail
  read must stay line- and byte-bounded and must pass through the existing
  redactor, because the cycle log carries structured application output;
* the derived hard runtime bound must be the SAME expression the release
  controller uses, so diagnostics can never report a bound the deploy did not
  enforce;
* the block must not silently change the diagnostics verdict, and must report
  `UNKNOWN`/`NONE` rather than a fabricated value when an input is absent.

The behavioural half executes the real block against fixtures and is POSIX-only:
it needs GNU `date -d` plus grep/tail/head/stat.
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
DEPLOY = ROOT / "deploy" / "remote" / "ohm-deploy"

BLOCK_START = "# Read-only unified-cycle observability (Release Pipeline v1)."
BLOCK_END = '\necho "diagnostics_status=$status"'

CRON_HEADER = 'if [[ -f "$UNIFIED_CYCLE_CRON" ]]; then'
LOG_HEADER = 'if [[ -f "$UNIFIED_CYCLE_LOG" ]]; then'

#: The one expression both the release controller and the diagnostics use to
#: derive the authorized unified-cycle hard runtime bound.
BOUND_PATTERN = r"timeout --signal=TERM --kill-after=[0-9]+s [0-9]+"

#: Behavioural POSIX tests execute the real block; they need GNU `date -d`,
#: lslocks-free read-only probes, and a normal fork environment.
pytestmark_posix = pytest.mark.skipif(
    os.name == "nt", reason="POSIX-only: needs GNU date/grep/tail and a normal fork"
)


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _block() -> str:
    text = _script()
    start = text.index(BLOCK_START)
    return text[start : text.index(BLOCK_END, start)]


def _function_body(name: str) -> str:
    """Extract one shell function body from the script, up to its closing brace."""
    script = _script()
    start = script.index(f"{name}() {{")
    end = script.index("\n}\n", start)
    return script[start : end + 2]


def _branches(header: str) -> tuple[str, str]:
    """The then/else halves of one top-level `if` in the block.

    Compared by field surface so a field emitted on only one branch - the defect
    that hid ``export_log_exists`` whenever the log did exist - cannot return.
    """
    block = _block()
    body = block[block.index(header) :]
    body = body[: body.index("\nfi\n")]
    then_part, _, else_part = body.rpartition("\nelse\n")
    assert then_part and else_part
    return then_part, else_part


def _emitted_fields(section: str) -> set[str]:
    return set(re.findall(r'echo "([a-z_]+)=', section))


# ---------------------------------------------------------------------------
# The block must contain the required observation surface
# ---------------------------------------------------------------------------


def test_block_reports_the_installed_cycle_entry_and_its_bound():
    block = _block()
    for field in (
        "unified_cycle_cron_path=",
        "unified_cycle_cron_exists=",
        "unified_cycle_cron_mtime_utc=",
        "unified_cycle_cron_schedule=",
        "unified_cycle_hard_runtime_bound_seconds=",
    ):
        assert field in block, field
    # An absent or unparseable entry fails closed to UNKNOWN, never to a bound.
    assert 'echo "unified_cycle_cron_exists=NO"' in block
    assert 'echo "unified_cycle_hard_runtime_bound_seconds=UNKNOWN"' in block


def test_schedule_read_skips_cron_d_environment_assignments():
    """A cron.d file carries `SHELL=`/`PATH=`/`MAILTO=` before the job line.

    Regression cover: reading the first non-comment line reported an assignment
    such as ``SHELL=/bin/bash`` as the cycle schedule on a real host, which is
    exactly where this field is read.
    """
    block = _block()
    assert r"/^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=/ {next}" in block
    assert r"/^[[:space:]]*#/ {next}" in block
    assert "grep -v '^#'" not in block


def test_block_reports_the_cycle_completion_history():
    block = _block()
    for field in (
        "unified_cycle_log_exists=",
        "unified_cycle_log_size_bytes=",
        "unified_cycle_log_mtime_utc=",
        "unified_cycle_log_age_seconds=",
        "unified_cycle_log_tail_lines_requested=",
        "unified_cycle_log_tail_bytes_limit=",
        "unified_cycle_log_tail_success_count=",
        "unified_cycle_log_tail_degraded_count=",
        "unified_cycle_log_tail_completed_count=",
        "unified_cycle_log_tail_skip_count=",
        "unified_cycle_log_tail_error_count=",
        "unified_cycle_log_latest_status=",
        "unified_cycle_log_latest_completed_at=",
        "unified_cycle_log_latest_completion_age_seconds=",
    ):
        assert field in block, field
    # The two markers the release controller consumes are the ones parsed.
    assert "OPIP_UNIFIED_CYCLE_STATUS=" in block
    assert "OPIP_UNIFIED_CYCLE_COMPLETED_AT=" in block


def test_block_emits_every_field_on_both_branches():
    """Absence must be reported, not silently omitted.

    Regression cover: a field echoed only on the present-branch is invisible in
    exactly the case an operator most needs to read it.
    """
    for header in (CRON_HEADER, LOG_HEADER):
        then_part, else_part = _branches(header)
        then_fields = _emitted_fields(then_part)
        else_fields = _emitted_fields(else_part)
        assert then_fields, header
        assert then_fields == else_fields, (
            f"{header}: then-only={sorted(then_fields - else_fields)} "
            f"else-only={sorted(else_fields - then_fields)}"
        )


def test_block_reports_absent_inputs_as_unknown_not_fabricated():
    for header in (CRON_HEADER, LOG_HEADER):
        _, else_part = _branches(header)
        assert "=UNKNOWN" in else_part or "=NONE" in else_part
        # No fabricated numeric evidence on the absent branch.
        for line in else_part.splitlines():
            match = re.search(r'echo "([a-z_]+)=([^"]*)"', line)
            if match and match.group(1).endswith("_count"):
                assert match.group(2) == "UNKNOWN", line


# ---------------------------------------------------------------------------
# The derived bound must match the release controller's own derivation
# ---------------------------------------------------------------------------


def test_hard_runtime_bound_matches_the_release_controller():
    """One bound, one expression: diagnostics cannot report a different bound."""
    block = _block()
    deploy = DEPLOY.read_text(encoding="utf-8")
    assert BOUND_PATTERN in deploy
    assert BOUND_PATTERN in block
    # Both take the trailing integer, and both stop after the first match.
    assert r"grep -oE '[0-9]+$'" in deploy
    assert r"grep -oE '[0-9]+$'" in block
    assert "grep -m1 -oE" in deploy
    assert "grep -m1 -oE" in block


# ---------------------------------------------------------------------------
# The block must stay bounded, redacted and read-only
# ---------------------------------------------------------------------------


def test_block_is_bounded_and_redacted():
    script = _script()
    assert "UNIFIED_CYCLE_LOG_TAIL_LINES=200" in script
    assert "UNIFIED_CYCLE_LOG_MAX_BYTES=64000" in script
    block = _block()
    assert 'tail -n "$UNIFIED_CYCLE_LOG_TAIL_LINES"' in block
    assert 'head -c "$UNIFIED_CYCLE_LOG_MAX_BYTES"' in block
    assert "redact_export_secrets" in block
    assert "OPIP_UNIFIED_CYCLE_LOG_TAIL" in block
    assert "OPIP_UNIFIED_CYCLE_LOG_TAIL_END" in block
    # No unbounded dump of the log.
    assert 'cat "$UNIFIED_CYCLE_LOG"' not in block
    assert "cat $UNIFIED_CYCLE_LOG" not in block
    # The bounded limits are reported so truncation is visible in the receipt.
    assert 'echo "unified_cycle_log_tail_lines_requested=' in block
    assert 'echo "unified_cycle_log_tail_bytes_limit=' in block


def test_block_performs_no_mutation_or_signalling():
    block = _block()
    for forbidden in (
        "rm ",
        "rm -",
        "mv ",
        "cp ",
        "chmod",
        "chown",
        "touch ",
        "truncate",
        "mktemp",
        "systemctl",
        "crontab",
        "pkill",
        "killall",
        "kill -",
        "docker",
        "tee",
        "sed -i",
        "sponge",
    ):
        assert forbidden not in block, f"block must not contain: {forbidden}"


def test_block_takes_no_lock_and_writes_no_file():
    block = _block()
    # No file-descriptor opens and no locking facility: the block cannot acquire
    # a lock, so it can never make a real cycle skip.
    assert "exec " not in block
    assert "flock" not in block
    assert "<>" not in block
    # No redirection into any path: the block is read-only in both directions.
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "((" in stripped:
            continue
        assert not re.search(r">>?\s*[\"$]", line), line
        assert not re.search(r">>?\s*\S*\.(log|tmp|env)", line), line


def test_block_does_not_execute_or_restart_the_cycle():
    """The cycle is observed through its own log, never re-run."""
    block = _block()
    assert "app.jobs.run_cycle" not in block
    assert "run_cycle" not in block
    assert "timeout --signal=TERM" not in block.replace(BOUND_PATTERN, "")
    for forbidden in ("bash ", "sh -c", "eval ", "source "):
        assert forbidden not in block, forbidden
    # No shell passthrough and no argument dispatch anywhere in the block.
    assert '"$@"' not in block
    assert '"$*"' not in block
    assert "SSH_ORIGINAL_COMMAND" not in block


def test_block_does_not_emit_raw_argv_or_environment():
    block = _block()
    for forbidden in ("cmdline", "/proc/", "printenv", "os.environ", "env |"):
        assert forbidden not in block, f"block must not emit raw argv: {forbidden}"


def test_block_does_not_change_the_diagnostics_verdict():
    """Cycle freshness is a release-window property, not a readiness property.

    The block is informational. Folding it into `degrade` would let a healthy
    in-flight cycle - whose authorized runtime bound is far above the export
    stall threshold - misreport the learning diagnostics verdict, and would also
    redefine the shared `status` contract for unrelated consumers.
    """
    code = "\n".join(
        line for line in _block().splitlines() if not line.strip().startswith("#")
    )
    assert not re.search(r"(?<![\w_])degrade\b", code)
    # The block never assigns the shared verdict, and never emits it: the field
    # name it may carry is its own `..._status=` observation, never a verdict.
    assert not re.search(r"(?<![\w_])status=", code)
    assert "diagnostics_status" not in code


# ---------------------------------------------------------------------------
# Behaviour: execute the real block against fixtures (POSIX only)
# ---------------------------------------------------------------------------

PRELUDE = """
set -Eeuo pipefail
now_epoch="$(date -u +%s)"
age_seconds() {
  local raw="$1"
  local epoch
  [[ -n "$raw" ]] || return 1
  epoch="$(date -u -d "$raw" +%s 2>/dev/null || true)"
  [[ "$epoch" =~ ^[0-9]+$ ]] || return 1
  if (( epoch > now_epoch + 120 )); then
    return 1
  elif (( epoch > now_epoch )); then
    printf '0\\n'
  else
    printf '%s\\n' "$((now_epoch - epoch))"
  fi
}
"""

CRON_ENTRY = (
    "SHELL=/bin/bash\n"
    "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin\n"
    "* * * * * root flock -n /var/run/ohm-unified-cycle.lock -c "
    "'cd /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1 && timeout --signal=TERM "
    "--kill-after=30s 3600 /usr/bin/docker compose exec -T ohm-trade-agent "
    "python -m app.jobs.run_cycle >> /var/log/ohm-unified-cycle.log 2>&1'\n"
)


def _run_block(
    tmp_path: Path,
    *,
    cron_text: str | None = None,
    log_text: str | None = None,
    tail_lines: int = 200,
    max_bytes: int = 64000,
) -> tuple[dict[str, str], list[str]]:
    """Set the fixture state, execute the real block, return (fields, tail)."""
    cron = tmp_path / "ohm-unified-cycle"
    log = tmp_path / "ohm-unified-cycle.log"
    if cron_text is not None:
        cron.write_text(cron_text, encoding="utf-8")
    if log_text is not None:
        log.write_text(log_text, encoding="utf-8")

    prelude = PRELUDE + _function_body("redact_export_secrets") + "\n" + "\n".join(
        [
            f'UNIFIED_CYCLE_CRON="{cron}"',
            f'UNIFIED_CYCLE_LOG="{log}"',
            f"UNIFIED_CYCLE_LOG_TAIL_LINES={tail_lines}",
            f"UNIFIED_CYCLE_LOG_MAX_BYTES={max_bytes}",
            "",
        ]
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(prelude + _block() + "\n", encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr

    lines = proc.stdout.splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        key, sep, value = line.partition("=")
        if sep and re.fullmatch(r"[a-z_]+", key):
            fields[key] = value

    start = lines.index("OPIP_UNIFIED_CYCLE_LOG_TAIL")
    end = lines.index("OPIP_UNIFIED_CYCLE_LOG_TAIL_END")
    assert start < end
    return fields, lines[start + 1 : end]


def _status_line(status: str, completed_at: str | None = None) -> str:
    stamp = completed_at or (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    return (
        f"OPIP_UNIFIED_CYCLE_STATUS={status}\n"
        f"OPIP_UNIFIED_CYCLE_COMPLETED_AT={stamp}\n"
    )


@pytestmark_posix
def test_committed_cycle_reports_success_completion_and_age(tmp_path):
    stamp = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    fields, tail = _run_block(
        tmp_path, cron_text=CRON_ENTRY, log_text=_status_line("SUCCESS", stamp)
    )
    assert fields["unified_cycle_log_exists"] == "YES"
    assert fields["unified_cycle_log_tail_success_count"] == "1"
    assert fields["unified_cycle_log_tail_degraded_count"] == "0"
    assert fields["unified_cycle_log_tail_completed_count"] == "1"
    assert fields["unified_cycle_log_latest_status"] == "SUCCESS"
    assert fields["unified_cycle_log_latest_completed_at"] == stamp
    # A five-minute-old completion is reported as an age, not as a status.
    age = int(fields["unified_cycle_log_latest_completion_age_seconds"])
    assert 250 <= age <= 400, age
    assert fields["unified_cycle_log_age_seconds"] not in ("", "UNKNOWN")
    assert int(fields["unified_cycle_log_size_bytes"]) > 0
    # The raw tail is emitted, and the cycle markers survive the redactor intact.
    assert "OPIP_UNIFIED_CYCLE_STATUS=SUCCESS" in tail
    assert f"OPIP_UNIFIED_CYCLE_COMPLETED_AT={stamp}" in tail


@pytestmark_posix
def test_degraded_cycle_is_reported_not_hidden(tmp_path):
    """A cycle that runs and reports DEGRADED is a different fact from silence."""
    fields, _ = _run_block(
        tmp_path, cron_text=CRON_ENTRY, log_text=_status_line("DEGRADED")
    )
    assert fields["unified_cycle_log_tail_success_count"] == "0"
    assert fields["unified_cycle_log_tail_degraded_count"] == "1"
    assert fields["unified_cycle_log_latest_status"] == "DEGRADED"


@pytestmark_posix
def test_skip_only_log_is_distinguishable_from_silence(tmp_path):
    """Lock-contention skips leave a marker but never a completion."""
    log_text = "\n".join(
        ["OHM Unified Cycle skipped: previous cycle still running."] * 5
    )
    fields, _ = _run_block(tmp_path, cron_text=CRON_ENTRY, log_text=log_text + "\n")
    assert fields["unified_cycle_log_tail_skip_count"] == "5"
    assert fields["unified_cycle_log_tail_completed_count"] == "0"
    assert fields["unified_cycle_log_tail_success_count"] == "0"
    assert fields["unified_cycle_log_latest_status"] == "NONE"
    assert fields["unified_cycle_log_latest_completed_at"] == "NONE"
    assert fields["unified_cycle_log_latest_completion_age_seconds"] == "UNKNOWN"


@pytestmark_posix
def test_crashed_cycle_is_reported(tmp_path):
    log_text = (
        "Traceback (most recent call last):\n"
        '  File "/app/app/jobs/run_cycle.py", line 1\n'
        "RuntimeError: cycle aborted\n"
    )
    fields, _ = _run_block(tmp_path, cron_text=CRON_ENTRY, log_text=log_text)
    assert fields["unified_cycle_log_tail_error_count"] == "1"
    assert fields["unified_cycle_log_tail_completed_count"] == "0"
    assert fields["unified_cycle_log_latest_status"] == "NONE"


@pytestmark_posix
def test_tail_is_line_bounded_and_keeps_the_newest_entries(tmp_path):
    log_text = "".join(f"cycle-{index:04d} line\n" for index in range(500))
    fields, tail = _run_block(
        tmp_path, cron_text=CRON_ENTRY, log_text=log_text, tail_lines=200
    )
    assert fields["unified_cycle_log_tail_lines_requested"] == "200"
    assert len(tail) == 200
    # 500 lines are written (0000..0499), so the bounded newest window is
    # 0300..0499 and the just-evicted boundary line is 0299.
    assert "cycle-0499 line" in tail
    assert "cycle-0300 line" in tail
    # Everything older than the bounded window is genuinely not read.
    assert "cycle-0299 line" not in tail
    assert "cycle-0000 line" not in tail


@pytestmark_posix
def test_tail_is_byte_bounded(tmp_path):
    log_text = "".join(f"cycle-{index:04d} line\n" for index in range(500))
    _, tail = _run_block(
        tmp_path,
        cron_text=CRON_ENTRY,
        log_text=log_text,
        tail_lines=200,
        max_bytes=64,
    )
    emitted = "\n".join(tail).encode()
    assert len(emitted) <= 64


@pytestmark_posix
def test_absent_log_reports_unknown_not_a_fabricated_zero(tmp_path):
    fields, tail = _run_block(tmp_path, cron_text=CRON_ENTRY)
    assert fields["unified_cycle_log_exists"] == "NO"
    for name in (
        "unified_cycle_log_size_bytes",
        "unified_cycle_log_age_seconds",
        "unified_cycle_log_tail_success_count",
        "unified_cycle_log_tail_degraded_count",
        "unified_cycle_log_tail_completed_count",
        "unified_cycle_log_tail_skip_count",
        "unified_cycle_log_tail_error_count",
        "unified_cycle_log_latest_completion_age_seconds",
    ):
        assert fields[name] == "UNKNOWN", name
    assert fields["unified_cycle_log_latest_status"] == "NONE"
    assert "".join(tail).strip() == ""


@pytestmark_posix
def test_credentials_in_the_cycle_log_are_redacted(tmp_path):
    log_text = (
        "2026-10-04T19:21:33Z INFO Authorization: Bearer fake9001\n"
        "2026-10-04T19:21:33Z INFO API_TOKEN=fake9002\n"
        "2026-10-04T19:21:33Z INFO cycle started\n"
    )
    fields, tail = _run_block(tmp_path, cron_text=CRON_ENTRY, log_text=log_text)
    joined = "\n".join(tail)
    assert "fake9001" not in joined
    assert "fake9002" not in joined
    assert "<redacted>" in joined
    # Benign lines are untouched, and the fields are never redacted.
    assert "cycle started" in joined
    assert fields["unified_cycle_log_exists"] == "YES"


@pytestmark_posix
def test_installed_entry_reports_schedule_and_derived_bound(tmp_path):
    fields, _ = _run_block(tmp_path, cron_text=CRON_ENTRY)
    assert fields["unified_cycle_cron_exists"] == "YES"
    assert fields["unified_cycle_cron_schedule"] == "* * * * *"
    # The fixture is a realistic cron.d file whose first lines are environment
    # assignments; neither may be reported as the schedule.
    assert "SHELL=" not in fields["unified_cycle_cron_schedule"]
    assert "PATH=" not in fields["unified_cycle_cron_schedule"]
    assert fields["unified_cycle_hard_runtime_bound_seconds"] == "3600"
    assert fields["unified_cycle_cron_mtime_utc"].endswith("Z")


@pytestmark_posix
def test_absent_or_boundless_entry_fails_closed_to_unknown(tmp_path):
    fields, _ = _run_block(tmp_path)
    assert fields["unified_cycle_cron_exists"] == "NO"
    assert fields["unified_cycle_cron_schedule"] == "UNKNOWN"
    assert fields["unified_cycle_hard_runtime_bound_seconds"] == "UNKNOWN"

    # An entry that exists but declares no authorized bound must not invent one.
    boundless = tmp_path / "boundless"
    boundless.mkdir()
    fields_no_bound, _ = _run_block(
        boundless, cron_text="* * * * * root /usr/bin/docker compose exec -T x y\n"
    )
    assert fields_no_bound["unified_cycle_cron_exists"] == "YES"
    assert fields_no_bound["unified_cycle_cron_schedule"] == "* * * * *"
    assert fields_no_bound["unified_cycle_hard_runtime_bound_seconds"] == "UNKNOWN"
