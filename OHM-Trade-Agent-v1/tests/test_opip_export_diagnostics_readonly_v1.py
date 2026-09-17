"""Contracts for the read-only production export observability block.

The recurring production export can stop committing while production stays
otherwise healthy, so committed-manifest age alone cannot separate "the
scheduler stopped invoking the exporter" from "an exporter run is stuck holding
a lock". The diagnostics helper gained an observer-only block to distinguish
those cases.

These tests are the guardrail on that block. Two things matter and are checked
separately:

* the block must remain strictly observational - it must never take a lock,
  signal a process, run the exporter, or mutate anything. Taking a lock to "test"
  it would prove nothing about the holder and could itself make a real export run
  skip, destroying the very evidence being collected;
* the authority boundary must be unchanged - the forced-command gateway keeps
  exactly its two existing entry points and gains no generic command path.

The behavioural half of the suite executes the real block against fixtures and is
POSIX-only: it needs flock, lslocks//proc and a normal fork environment.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
GATEWAY = ROOT / "deploy" / "remote" / "ohm-deploy-ssh"

BLOCK_START = "# Read-only production export observability."
BLOCK_END = "\nif docker inspect ohm-trade-agent >/dev/null 2>&1; then"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _block() -> str:
    text = _script()
    start = text.index(BLOCK_START)
    return text[start : text.index(BLOCK_END, start)]


# ---------------------------------------------------------------------------
# Authority boundary: unchanged, and no new command path
# ---------------------------------------------------------------------------


def test_forced_command_gateway_keeps_exactly_its_two_entry_points():
    gateway = GATEWAY.read_text(encoding="utf-8")
    # The only two things this key may do, and everything else is refused.
    assert gateway.count("exec sudo") == 2
    assert "=~ ^deploy[[:space:]]+([0-9a-f]{40})$" in gateway
    assert '"$ORIGINAL" == "diagnose-learning"' in gateway
    assert "refusing command" in gateway
    assert "exit 64" in gateway


def test_gateway_does_not_gain_a_shell_passthrough():
    gateway = GATEWAY.read_text(encoding="utf-8")
    for forbidden in ("bash -c", "sh -c", "eval ", "$SSH_ORIGINAL_COMMAND/"):
        assert forbidden not in gateway
    # The caller-supplied string is read once and may only be compared against
    # the two allowed forms: it is never executed.
    assert gateway.count("SSH_ORIGINAL_COMMAND") == 1
    assert gateway.count("$ORIGINAL") == 2


def test_diagnostics_takes_no_positional_arguments_or_remote_command():
    diagnostics = _script()
    assert "SSH_ORIGINAL_COMMAND" not in diagnostics
    assert '"$@"' not in diagnostics
    assert '"$*"' not in diagnostics
    # No argument is ever treated as something to execute.
    assert not re.search(r"^\s*\"\$1\"", diagnostics, re.MULTILINE)


def test_export_block_adds_no_command_or_argument_dispatch():
    block = _block()
    assert '"$@"' not in block
    assert "eval " not in block
    assert "SSH_ORIGINAL_COMMAND" not in block
    # `$1` is allowed only as a function parameter, never as a command: the
    # helper functions take their target by argument, and nothing in the block
    # executes a caller-supplied string.
    parameter_lines = [line for line in block.splitlines() if '"$1"' in line]
    assert parameter_lines, "expected the block to pass values via function parameters"
    for line in parameter_lines:
        assert re.search(r'local\s+\w+="\$1"', line), line


# ---------------------------------------------------------------------------
# The block must contain the required observation surface
# ---------------------------------------------------------------------------


def test_block_reports_canonical_replica_fields():
    block = _block()
    for field in (
        "manifest_replica_marker_version=",
        "manifest_replica_marker_present=",
        "manifest_replica_dir=",
        "manifest_replica_bytes=",
        "manifest_replica_sha256=",
        "manifest_production_deployed_sha=",
        "manifest_schema_version=",
        "replica_dir_name_valid=",
        "replica_dir_exists=",
        "replica_inner_manifest=",
        "replica_inner_generation_id=",
        "replica_inner_source_release_sha=",
        "replica_inner_snapshot_created_at_utc=",
        "replica_inner_age_seconds=",
        "replica_inner_release_matches_production=",
        "replica_contract_freshness_1800s=",
    ):
        assert field in block
    # The content-addressed name is validated before it is ever used as a path.
    assert "REPLICA_DIR_NAME_PATTERN" in block
    assert "replica_dir_name_valid" in block


def test_block_reports_cron_metadata_and_release_artifact_identity():
    block = _block()
    for field in (
        "cron_daemon_active=",
        "cron_daemon_state=",
        "cron_daemon_pid=",
        "cron_daemon_started_at=",
        "export_cron_exists=",
        "export_cron_owner=",
        "export_cron_group=",
        "export_cron_mode=",
        "export_cron_size_bytes=",
        "export_cron_mtime_utc=",
        "export_cron_sha256=",
        "export_cron_job_line=",
        "export_cron_matches_release=",
        "export_journal_source=",
        "export_journal_matched_lines=",
        "export_journal_last_line=",
    ):
        assert field in block


def test_block_reports_all_three_export_locks():
    block = _block()
    assert 'EXPORT_INTERNAL_LOCK="/var/run/opip-learning-export.lock"' in _script()
    assert 'EXPORT_WRAPPER_LOCK="/var/run/opip-learning-export-trigger.lock"' in _script()
    assert 'EXPORT_PUBLISH_LOCK="$EXPORT_ROOT/.publish.lock"' in _script()
    # Each of the three locks is observed under its own prefix, and the shared
    # printer emits the full holder provenance for whichever one is inspected.
    for prefix, lock_var in (
        ("outer_cron", "EXPORT_WRAPPER_LOCK"),
        ("internal_export", "EXPORT_INTERNAL_LOCK"),
        ("publish", "EXPORT_PUBLISH_LOCK"),
    ):
        assert f'observe_lock_owner "{prefix}" "${lock_var}"' in block
    assert 'describe_pid "${prefix}_lock_owner" "$pid"' in block
    for template in (
        "${prefix}_lock_file=",
        "${prefix}_lock_state=",
        "${prefix}_lock_owner_source=",
    ):
        assert template in block
    for template in (
        "${prefix}_pid=",
        "${prefix}_ppid=",
        "${prefix}_start_time=",
        "${prefix}_elapsed_seconds=",
        "${prefix}_command=",
    ):
        assert template in block
    # Holder discovery must come from kernel-reported state.
    assert "lslocks" in block
    assert "/proc/" in block
    assert "export_lock_stall_suspected=" in block


def test_block_reports_committed_state_log_classification_and_orphans():
    block = _block()
    for field in (
        "export_log_exists=",
        "export_log_size_bytes=",
        "export_log_mtime_utc=",
        "export_log_activity_class=",
        "export_log_skip_count=",
        "export_log_success_count=",
        "export_log_bundle_ok_count=",
        "export_log_failure_count=",
        "export_process_count=",
        "export_processes=",
        "export_committed_after_release_receipt=",
        "release_receipt_sha=",
        "release_receipt_mtime_utc=",
    ):
        assert field in block
    # Orphan inventory is emitted through one bounded shared printer.
    for prefix, pattern in (
        ("replica_staging", ".canonical_learning_replica.staging.*"),
        ("replica_published", "canonical_learning_replica.*"),
        ("manifest_tmp", ".manifest.env.tmp.*"),
    ):
        assert f'emit_export_entries "{prefix}" "{pattern}"' in block
    assert "${prefix}_count=" in block
    assert "${prefix}_entries=" in block
    for classification in (
        "NO_POST_MANIFEST_LOG_ACTIVITY",
        "POST_MANIFEST_RUNS_FAIL",
        "POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD",
        "POST_MANIFEST_RUNS_SUCCEED",
    ):
        assert classification in block


def test_block_is_bounded_and_redacted():
    block = _block()
    assert "EXPORT_LOG_TAIL_LINES=100" in _script()
    assert "EXPORT_LOG_MAX_BYTES=20000" in _script()
    assert "EXPORT_JOURNAL_MAX_LINES=40" in _script()
    assert 'tail -n "$EXPORT_LOG_TAIL_LINES"' in block
    assert 'head -c "$EXPORT_LOG_MAX_BYTES"' in block
    assert "redact_export_secrets" in block
    assert "OPIP_EXPORT_LOG_TAIL" in block
    assert "OPIP_EXPORT_LOG_TAIL_END" in block
    # No unbounded dump of the log or an environment listing.
    assert 'cat "$EXPORT_LOG"' not in block
    assert "printenv" not in block
    assert "os.environ" not in block
    assert 'env |' not in block


# ---------------------------------------------------------------------------
# The block must not mutate anything
# ---------------------------------------------------------------------------


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
        "install -d",
        "systemctl restart",
        "systemctl stop",
        "systemctl start",
        "systemctl disable",
        "pkill",
        "killall",
        "kill ",
        "docker stop",
        "docker rm",
        "docker restart",
        "crontab",
    ):
        assert forbidden not in block, f"block must not contain: {forbidden}"


def test_block_does_not_open_or_write_any_path():
    block = _block()
    # No file-descriptor opens (the only way a lock could be taken in bash).
    assert "exec " not in block
    assert "flock" not in block
    # No writes into the export surface.
    assert '> "$EXPORT' not in block
    assert '>> "$EXPORT' not in block
    assert '> "$MANIFEST"' not in block
    assert '> "$replica' not in block


def test_block_does_not_invoke_the_exporter():
    block = _block()
    # The exporter filename may appear only as an observation pattern, never as
    # something that is executed.
    assert not re.search(
        r"\b(?:bash|sh)\s+\S*export-opip-learning-evidence\.sh", block
    )
    assert not re.search(r"\bexec\s+\S*export-opip-learning-evidence\.sh", block)
    assert "source " not in block
    assert ". export-opip-learning-evidence" not in block


def test_block_does_not_acquire_the_export_locks():
    block = _block()
    # Neither the lock variables nor their paths may be passed to a locking or
    # acquiring facility. Observation uses lslocks/fuser//proc only.
    for line in block.splitlines():
        if "flock" in line or "exec " in line:
            pytest.fail(f"lock acquisition idiom in read-only block: {line!r}")
    for lock_var in ("EXPORT_INTERNAL_LOCK", "EXPORT_WRAPPER_LOCK", "EXPORT_PUBLISH_LOCK"):
        assert lock_var in block
        assert not re.search(rf"flock[^\n]*{lock_var}", block)
        assert not re.search(rf"exec[^\n]*{lock_var}", block)


def test_block_only_reads_the_export_log_and_manifest():
    block = _block()
    # grep/tail/head/stat/sha256sum are read-only; assert no writer verbs appear
    # alongside the log or manifest paths.
    for path_var in ("EXPORT_LOG", "MANIFEST"):
        for verb in ("tee", "sponge", "dd ", "sed -i", "perl -i"):
            assert not re.search(rf"{verb}[^\n]*\${path_var}", block)


# ---------------------------------------------------------------------------
# Behaviour: execute the real block against fixtures (POSIX only)
# ---------------------------------------------------------------------------


PRELUDE = """
set -Eeuo pipefail
now_epoch="$(date -u +%s)"
env_value() {
  local file="$1"
  local key="$2"
  awk -F= -v k="$key" '$1 == k {sub(/^[^=]*=/, ""); print; exit}' "$file" 2>/dev/null || true
}
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
status="OK"
degrade() { if [[ "$status" == "OK" ]]; then status="DEGRADED"; fi; }
"""

SHA = "91f4b274bd8eaef84e4525e12fe29b81d084912a"
REPLICA_NAME = "canonical_learning_replica." + "a" * 64


def _fixture(tmp_path: Path, *, replica_dir_name: str = REPLICA_NAME) -> dict:
    export_root = tmp_path / "export"
    export_root.mkdir()
    manifest = export_root / "manifest.env"
    manifest.write_text(
        "schema_version=4\n"
        "exported_at_utc=2026-09-17T19:00:47Z\n"
        f"production_deployed_sha={SHA}\n"
        "p1_shadow_outbox_retired=1\n"
        "canonical_learning_replica_version=1\n"
        f"canonical_learning_replica_dir={replica_dir_name}\n"
        "canonical_learning_replica_bytes=123456\n"
        "canonical_learning_replica_sha256=" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    replica = export_root / replica_dir_name
    if replica_dir_name == REPLICA_NAME:
        replica.mkdir()
        (replica / "replica_manifest.json").write_text(
            json.dumps(
                {
                    "replica_schema_version": 1,
                    "generation_id": "gen-probe-0001",
                    "source_release_sha": SHA,
                    "snapshot_created_at_utc": "2026-09-17T19:00:30Z",
                }
            ),
            encoding="utf-8",
        )
    (export_root / ".canonical_learning_replica.staging.4242").mkdir()
    (export_root / ".manifest.env.tmp.4242").write_text("x", encoding="utf-8")
    log_path = tmp_path / "opip-learning-export.log"
    receipt = tmp_path / "last-good-sha"
    receipt.write_text(SHA + "\n", encoding="utf-8")
    cron = tmp_path / "cron.opip-learning-export"
    cron.write_text("SHELL=/bin/bash\nPATH=/usr/bin\n", encoding="utf-8")
    cron_src = tmp_path / "cron.src"
    cron_src.write_text("SHELL=/bin/bash\nPATH=/usr/bin\n", encoding="utf-8")
    return {
        "export_root": export_root,
        "manifest": manifest,
        "log": log_path,
        "receipt": receipt,
        "cron": cron,
        "cron_src": cron_src,
        "internal": tmp_path / "internal.lock",
        "wrapper": tmp_path / "wrapper.lock",
        "publish": export_root / ".publish.lock",
    }


def _run_block(tmp_path: Path, fx: dict, exported_at: str) -> dict:
    prelude = PRELUDE + "\n".join(
        [
            f'current_sha="{SHA}"',
            f'exported_at="{exported_at}"',
            f'EXPORT_ROOT="{fx["export_root"]}"',
            f'MANIFEST="{fx["manifest"]}"',
            f'EXPORT_CRON="{fx["cron"]}"',
            f'EXPORT_CRON_SRC="{fx["cron_src"]}"',
            f'EXPORT_INTERNAL_LOCK="{fx["internal"]}"',
            f'EXPORT_WRAPPER_LOCK="{fx["wrapper"]}"',
            f'EXPORT_PUBLISH_LOCK="{fx["publish"]}"',
            f'EXPORT_LOG="{fx["log"]}"',
            f'EXPORT_RELEASE_RECEIPT="{fx["receipt"]}"',
            "REPLICA_DIR_NAME_PATTERN='^canonical_learning_replica\\.[0-9a-f]{64}$'",
            "EXPORT_LOG_TAIL_LINES=100",
            "EXPORT_LOG_MAX_BYTES=20000",
            "EXPORT_JOURNAL_MAX_LINES=40",
            "",
        ]
    )
    harness = tmp_path / "harness.sh"
    harness.write_text(prelude + _block() + '\necho "FINAL_STATUS=$status"\n', encoding="utf-8")
    proc = subprocess.run(
        ["bash", str(harness)], capture_output=True, text=True, encoding="utf-8"
    )
    assert proc.returncode == 0, proc.stderr
    fields: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if "=" in line and " " not in line.split("=", 1)[0]:
            key, _, value = line.partition("=")
            fields[key] = value
    # Keep the manifest's own exported_at consistent with the expectation.
    manifest = fx["manifest"].read_text(encoding="utf-8")
    fx["manifest"].write_text(
        re.sub(r"exported_at_utc=\S+", f"exported_at_utc={exported_at}", manifest),
        encoding="utf-8",
    )
    return fields


pytestmark_posix = pytest.mark.skipif(
    os.name == "nt", reason="POSIX-only: needs flock/lslocks//proc and a normal fork"
)


@pytestmark_posix
def test_block_classifies_frozen_export_with_lock_skip_spam(tmp_path):
    """The observed production signature: frozen manifest plus skip spam."""
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "\n".join(
            ["O'Pip learning evidence export: OK"]
            + ["O'Pip learning export already active; skipping"] * 9
        )
        + "\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")

    # The log mtime is after the manifest, and every post-manifest run skipped on
    # the internal lock: that is a lock stall, not a scheduler problem.
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD"
    assert fields["export_log_skip_count"] == "9"
    assert fields["export_lock_stall_suspected"] == "YES"
    assert fields["FINAL_STATUS"] == "DEGRADED"
    # Replica provenance is reported without recomputing any content.
    assert fields["replica_dir_name_valid"] == "YES"
    assert fields["replica_dir_exists"] == "YES"
    assert fields["replica_inner_generation_id"] == "gen-probe-0001"
    assert fields["replica_inner_source_release_sha"] == SHA
    assert fields["replica_inner_release_matches_production"] == "YES"
    assert fields["manifest_replica_marker_present"] == "YES"
    assert fields["replica_staging_count"] == "1"
    assert fields["manifest_tmp_count"] == "1"
    assert fields["export_cron_matches_release"] == "YES"


@pytestmark_posix
def test_block_classifies_healthy_exporter_progress(tmp_path):
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "O'Pip learning export: canonical replica bundle OK bytes=123456\n"
        "O'Pip learning evidence export: OK\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T23:59:00Z")
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_SUCCEED"
    assert fields["export_log_skip_count"] == "0"
    assert fields["export_log_bundle_ok_count"] == "1"


@pytestmark_posix
def test_block_classifies_failing_replica_export(tmp_path):
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "O'Pip learning export: canonical replica export FAILED (rc=3)\n"
        "O'Pip learning evidence export: JSON artifacts OK, canonical replica FAILED\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T23:59:00Z")
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_FAIL"
    assert int(fields["export_log_failure_count"]) >= 1
    assert fields["FINAL_STATUS"] == "DEGRADED"


@pytestmark_posix
def test_block_refuses_a_non_content_addressed_replica_dir(tmp_path):
    """A malformed or traversing name must never be used as a path."""
    fx = _fixture(tmp_path, replica_dir_name="../../etc")
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["replica_dir_name_valid"] == "NO"
    assert fields["replica_dir_exists"] == "NOT_REFERENCED"
    assert fields["replica_inner_generation_id"] == "UNKNOWN"


@pytestmark_posix
def test_block_observes_a_held_lock_without_taking_it(tmp_path):
    """Detection must work from /proc while the lock is genuinely held."""
    import fcntl

    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    holder = open(fx["internal"], "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
        assert fields["internal_export_lock_file"] == "EXISTS"
        assert fields["internal_export_lock_state"] in {"HELD", "OPENED_UNCONFIRMED"}
        assert fields["export_lock_stall_suspected"] == "YES"
        # The observed holder is this test process.
        assert fields["internal_export_lock_owner_pid"] == str(os.getpid())
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()
