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
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
GATEWAY = ROOT / "deploy" / "remote" / "ohm-deploy-ssh"

BLOCK_START = "# Read-only production export observability."
BLOCK_END = "\nif docker inspect ohm-trade-agent >/dev/null 2>&1; then"

#: Behavioural POSIX tests execute the real block; they need flock, lslocks,
#: /proc and a normal fork environment, so they skip on Windows.
pytestmark_posix = pytest.mark.skipif(
    os.name == "nt", reason="POSIX-only: needs flock/lslocks//proc and a normal fork"
)


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
        "cron_daemon_source=",
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
        "export_journal_pattern_scoped=",
        "export_journal_matched_lines=",
        "export_journal_export_records_seen=",
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
        "${prefix}_lock_held_instantaneously=",
        "${prefix}_lock_stall_verdict=",
    ):
        assert template in block
    for template in (
        "${prefix}_pid=",
        "${prefix}_ppid=",
        "${prefix}_comm=",
        "${prefix}_exe_basename=",
        "${prefix}_start_time=",
        "${prefix}_elapsed_seconds=",
    ):
        assert template in block
    # Holder discovery must come from kernel-reported state.
    assert "lslocks" in block
    assert "/proc/" in block
    assert "export_lock_stall_suspected=" in block


def test_cron_daemon_state_maps_to_active_without_conflating_unknown():
    """systemctl "unknown" must NOT map to cron_daemon_active=NO.

    unknown means systemctl could not decide - the unit is unavailable, the name
    does not resolve, or cron is managed outside that unit. It is a distinct
    state from proven inactivity, and treating it as NO would skip the pgrep
    fallback and could degrade diagnostics for a healthy daemon.
    """
    block = _block()
    # The dispatch is by state name, and unknown is neither NO nor YES.
    assert 'active) cron_daemon_active="YES"' in block
    assert 'inactive | failed | deactivating) cron_daemon_active="NO"' in block
    assert '*) cron_daemon_active="UNKNOWN"' in block
    # The old collapsing "unknown" into NO must be gone.
    assert 'inactive | failed | deactivating | unknown)' not in block
    # The pgrep fallback fires only when systemctl was inconclusive, upgrades
    # cron_daemon_active to YES with source=PGREP, and reports state so the
    # source of the YES is distinguishable from an authoritative systemctl.
    assert '[[ "$cron_daemon_active" == "UNKNOWN" ]] && command -v pgrep' in block
    assert 'cron_daemon_active="YES"' in block
    assert 'cron_daemon_source="PGREP"' in block
    assert 'cron_daemon_state="PROCESS_PRESENT"' in block
    # No unqueryable systemctl result is converted directly to NO anywhere else.
    for line in block.splitlines():
        if 'cron_daemon_active="NO"' in line:
            assert "inactive | failed | deactivating" in line, line


def test_degrade_reads_cron_daemon_active_not_state():
    """The degrade decision must not misread UNKNOWN as inactive."""
    block = _block()
    assert 'if [[ "$cron_daemon_active" == "NO" ]]; then' in block
    # No decision derives inactive from the raw systemctl state string.
    forbidden_patterns = (
        '"$cron_daemon_state" == "unknown"',
        '"$cron_daemon_state" != "active"',
    )
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for pattern in forbidden_patterns:
            assert pattern not in stripped, stripped


def test_block_reports_committed_state_log_classification_and_orphans():
    block = _block()
    for field in (
        "export_log_exists=",
        "export_log_size_bytes=",
        "export_log_mtime_utc=",
        "export_log_activity_class=",
        "export_log_timestamp_semantics=",
        "export_log_post_manifest_evidence=",
        "export_log_lifetime_skip_count=",
        "export_log_lifetime_success_count=",
        "export_log_lifetime_bundle_ok_count=",
        "export_log_lifetime_failure_count=",
        "export_log_post_manifest_skip_count=",
        "export_log_post_manifest_success_count=",
        "export_log_post_manifest_failure_count=",
        "export_log_post_manifest_timestamped_count=",
        "export_log_post_manifest_recognized_event_count=",
        "export_log_post_manifest_unclassified_line_count=",
        "export_process_count=",
        "export_process_present=",
        "export_process_max_elapsed_seconds=",
        "export_processes=",
        "export_committed_after_release_receipt=",
        "release_receipt_sha=",
        "release_receipt_mtime_utc=",
    ):
        assert field in block
    # Every classification the block can emit, including the honest
    # "cannot attribute events" outcomes for an untimestamped log and for a
    # timestamped log that carries no recognized exporter event.
    for classification in (
        "UNPROVABLE_FROM_UNTIMESTAMPED_LOG",
        "NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE",
        "POST_MANIFEST_RUNS_FAIL",
        "POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD",
        "POST_MANIFEST_RUNS_SUCCEED",
        "POST_MANIFEST_RECOGNIZED_ACTIVITY_UNCLASSIFIED",
    ):
        assert classification in block
    # The old enum values used timestamp-only attribution and are retired.
    for retired in (
        "POST_MANIFEST_TIMESTAMPED_ACTIVITY_UNCLASSIFIED",
        "NO_POST_MANIFEST_TIMESTAMPED_EVIDENCE",
    ):
        assert retired not in block
    # Orphan inventory is emitted through one bounded shared printer.
    for prefix, pattern in (
        ("replica_staging", ".canonical_learning_replica.staging.*"),
        ("replica_published", "canonical_learning_replica.*"),
        ("manifest_tmp", ".manifest.env.tmp.*"),
    ):
        assert f'emit_export_entries "{prefix}" "{pattern}"' in block
    assert "${prefix}_count=" in block
    assert "${prefix}_entries=" in block


def test_post_manifest_classification_is_time_scoped_not_lifetime():
    """Lifetime totals must never be used to attribute post-manifest events.

    The regression this guards: counting occurrences across the whole log and
    then reading them as post-manifest evidence whenever the file mtime is newer
    than the manifest. Historical failures or skips would contaminate the
    diagnosis.
    """
    block = _block()
    # The classification decision must read the post-manifest, timestamp-scoped
    # counters, never the lifetime ones.
    decision = block[block.index("if (( export_log_timestamped_line_count == 0 ))") :]
    decision = decision[: decision.index("echo \"export_log_activity_class=")]
    for scoped in (
        "export_log_post_manifest_failure_count",
        "export_log_post_manifest_skip_count",
        "export_log_post_manifest_success_count",
    ):
        assert scoped in decision
    for lifetime in (
        "export_log_lifetime_failure_count",
        "export_log_lifetime_skip_count",
        "export_log_lifetime_success_count",
    ):
        assert lifetime not in decision
    # File mtime may be reported but must not drive the classification.
    assert "log_mtime" not in decision
    # Event time comes from each line's own timestamp, never from the file mtime.
    assert "${log_line%% *}" in block
    assert "line_epoch > manifest_epoch" in block


def test_journal_filter_is_opip_specific():
    """Generic CRON matches would return unrelated system jobs."""
    script = _script()
    block = _block()
    # The match pattern is O'Pip export-specific and holds no bare CRON token.
    assert "EXPORT_JOURNAL_PATTERN=" in script
    pattern_line = next(
        line for line in script.splitlines() if line.startswith("EXPORT_JOURNAL_PATTERN=")
    )
    assert "opip-learning-export" in pattern_line
    assert "export-opip-learning-evidence" in pattern_line
    assert "opip-learning-export-trigger" in pattern_line
    assert not re.search(r"\|CRON", pattern_line)
    # Both probes use the scoped pattern.
    assert 'grep -Ei "$EXPORT_JOURNAL_PATTERN"' in block
    assert block.count('grep -Ei "$EXPORT_JOURNAL_PATTERN"') == 2
    # No raw journal line is emitted: counts and a boolean only.
    assert "export_journal_last_line" not in block
    assert "export_journal_matched_lines=" in block
    assert "export_journal_export_records_seen=" in block
    assert "export_journal_pattern_scoped=YES" in block


def test_block_emits_no_raw_argv_or_environment():
    """Process reporting must be bounded identity, never a command line."""
    block = _block()
    for forbidden in (
        "/proc/$pid/cmdline",
        "cmdline",
        "ps -o args",
        "ps -p ",
        "_command=",
        "printenv",
        "os.environ",
    ):
        assert forbidden not in block, f"block must not emit raw argv: {forbidden}"
    # The safe replacements are present instead.
    assert "/proc/$pid/comm" in block
    assert "/proc/$pid/exe" in block
    assert "${prefix}_comm=" in block
    assert "${prefix}_exe_basename=" in block


def test_diagnostics_emits_no_raw_argv_anywhere():
    """The whole helper, not just the new block, must stay argv-free."""
    script = _script()
    assert "cmdline" not in script
    assert "ps -o args" not in script
    assert "_command=" not in script
    assert "lock_owner_comm=" in script


def _function_body(name: str) -> str:
    """Extract one shell function body from the block, up to its closing brace."""
    block = _block()
    start = block.index(f"{name}() {{")
    end = block.index("\n}\n", start)
    return block[start : end + 2]


def test_stall_requires_proven_ownership_and_a_duration_past_threshold():
    """Instantaneous presence is not a stall, and neither is an unconfirmed opener."""
    script = _script()
    block = _block()
    assert "EXPORT_STALL_THRESHOLD_SECONDS=300" in script
    assert "EXPORT_STALL_THRESHOLD_SECONDS" in block
    assert "classify_duration_verdict" in block
    assert "classify_lock_stall_verdict" in block
    assert "note_stall_evidence" in block
    assert "export_stall_threshold_seconds=" in block
    assert "lock_ownership_proven=" in block
    # The duration seam needs elapsed > threshold, and reports UNKNOWN when the
    # duration is unavailable.
    duration = _function_body("classify_duration_verdict")
    assert "elapsed > EXPORT_STALL_THRESHOLD_SECONDS" in duration
    assert "printf 'UNKNOWN" in duration
    # The lock seam dispatches on evidence strength: only HELD reaches the
    # duration verdict, and an unconfirmed opener is pinned to UNKNOWN.
    lock_seam = _function_body("classify_lock_stall_verdict")
    assert "HELD) classify_duration_verdict" in lock_seam
    assert "OPENED_UNCONFIRMED) printf 'UNKNOWN" in lock_seam
    assert "NOT_HELD | ABSENT) printf 'NO" in lock_seam
    # OPENED_UNCONFIRMED must never sit on a path that yields YES. Pin the two
    # legitimate occurrences (the state assignment and the dispatch case) and
    # require that no line mentioning it also mentions YES.
    code_lines = [
        line.strip()
        for line in block.splitlines()
        if "OPENED_UNCONFIRMED" in line and not line.strip().startswith("#")
    ]
    assert len(code_lines) == 2, code_lines
    assert any('state="OPENED_UNCONFIRMED"' in line for line in code_lines)
    assert any(
        line.startswith("OPENED_UNCONFIRMED) printf 'UNKNOWN") for line in code_lines
    )
    for line in code_lines:
        assert "YES" not in line, line
    # Presence and ownership never set the verdict directly: exactly one
    # assignment of YES exists, inside the monotonic escalation helper.
    stall_assignments = [
        line for line in block.splitlines()
        if 'export_lock_stall_suspected="YES"' in line
    ]
    assert len(stall_assignments) == 1
    assert 'YES) export_lock_stall_suspected="YES" ;;' in block


def test_opens_backed_evidence_and_the_opener_fallback_are_distinct():
    """fuser fallback must be reported, and must never reach the duration path."""
    block = _block()
    # Both evidence strengths remain reported.
    assert "LSLOCKS" in block
    assert "FUSER_OPENERS" in block
    assert "OPENED_UNCONFIRMED" in block
    assert 'state="OPENED_UNCONFIRMED"' in block
    # The opener metadata is retained as evidence.
    assert 'describe_pid "${prefix}_lock_owner" "$pid"' in block
    # The old conflation is gone: no branch classifies both states together.
    assert not re.search(
        r'\[\[\s*"\$state"\s*==\s*"HELD"\s*\|\|\s*"\$state"\s*==\s*"OPENED_UNCONFIRMED"\s*\]\]',
        block,
    )
    assert 'verdict="$(classify_lock_stall_verdict "$state"' in block


def test_redactor_structurally_covers_all_credential_forms():
    """Structural check only. The behavioural tests below are the authoritative proof.

    Regex presence is not evidence that a secret disappears - the previous
    ordering bug passed a structural check while still emitting the credential -
    so this test only pins which forms the redactor claims to handle.
    Correctness is proven by test_redactor_removes_secrets_from_real_output.
    """
    body = _function_body("redact_export_secrets")
    # KEY=value assignments, including the broadened prefix set.
    assert "(API|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|PRIVATE|SESSION|COOKIE|AUTH)" in body
    # The HTTP Authorization header rule redacts to end of line.
    assert "authorization[[:space:]]*:).*$" in body
    # Standalone Bearer and Basic tokens.
    assert "bearer[[:space:]]+" in body
    assert "basic[[:space:]]+" in body
    # Common JSON credential fields.
    for field in (
        "access_?token",
        "refresh_?token",
        "id_?token",
        "api_?key",
        "secret",
        "password",
        "credential",
        "session_?id",
        "cookie",
    ):
        assert field in body, field
    # Every replacement writes <redacted>, never a passthrough.
    assert body.count("<redacted>") >= 5


#: Credential forms that must never survive redaction. Each entry is
#: (line, the secret substring that must be absent from the output).
REDACTION_CASES = [
    ("Authorization: Bearer bearer-secret-123", "bearer-secret-123"),
    ("Authorization: Basic basic-secret-456", "basic-secret-456"),
    ("Bearer standalone-secret-789", "standalone-secret-789"),
    ("Basic standalone-basic-012", "standalone-basic-012"),
    ("API_TOKEN=env-secret-345", "env-secret-345"),
    ('{"access_token":"json-secret-678"}', "json-secret-678"),
    ('{"password":"password-secret-901"}', "password-secret-901"),
    # Mixed content: a real log-line shape with the credential embedded.
    (
        "2026-09-17T19:05:00Z INFO Authorization: Bearer mixed-secret-234",
        "mixed-secret-234",
    ),
    ("2026-09-17T19:05:00Z WARN Bearer mixed2-secret-567", "mixed2-secret-567"),
    ("2026-09-17T19:05:00Z INFO API_TOKEN=env-mixed-678", "env-mixed-678"),
]


def _redactor_harness(tmp_path: Path) -> Path:
    """A harness that pipes its argument through the real redact_export_secrets."""
    harness = tmp_path / "redact.sh"
    harness.write_text(
        "set -Eeuo pipefail\n"
        + _function_body("redact_export_secrets")
        + '\nprintf "%s\\n" "$1" | redact_export_secrets\n',
        encoding="utf-8",
    )
    return harness


@pytestmark_posix
@pytest.mark.parametrize(
    ("line", "secret"), REDACTION_CASES, ids=[c[1] for c in REDACTION_CASES]
)
def test_redactor_removes_secrets_from_real_output(tmp_path, line, secret):
    """Execute the real redact_export_secrets() and prove the secret is gone.

    The primary regression proof for redaction. The previous implementation
    passed a static regex-presence check while still emitting
    "Authorization: <redacted> supersecrettoken123", because the header rule
    consumed only the first token and the standalone rule could no longer match.
    Only running the function and asserting the *original secret is absent* can
    catch that, so both `<redacted>` presence and secret absence are asserted.
    """
    proc = subprocess.run(
        ["bash", str(_redactor_harness(tmp_path)), line],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.rstrip("\n")
    # The credential must be gone...
    assert secret not in out, f"credential leaked for {line!r}: {out!r}"
    # ...and a redaction marker must show something was removed.
    assert "<redacted>" in out, f"no redaction marker for {line!r}: {out!r}"


@pytestmark_posix
def test_authorization_header_is_redacted_to_end_of_line(tmp_path):
    """Regression pin for the ordering defect specifically.

    The header rule must consume the whole value. If it regresses to consuming
    only the first token, the scheme is removed and the standalone rule cannot
    match, leaving the credential visible - which is what this asserts against.
    """
    harness = _redactor_harness(tmp_path)
    for line, secret, expected in (
        (
            "Authorization: Bearer bearer-secret-123",
            "bearer-secret-123",
            "Authorization: <redacted>",
        ),
        (
            "Authorization: Basic basic-secret-456",
            "basic-secret-456",
            "Authorization: <redacted>",
        ),
        (
            "2026-09-17T19:05:00Z INFO Authorization: Bearer mixed-secret-234",
            "mixed-secret-234",
            "2026-09-17T19:05:00Z INFO Authorization: <redacted>",
        ),
    ):
        proc = subprocess.run(
            ["bash", str(harness), line],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        out = proc.stdout.rstrip("\n")
        assert secret not in out, out
        assert out == expected, f"{line!r} -> {out!r}, expected {expected!r}"


@pytestmark_posix
def test_redactor_leaves_benign_export_lines_untouched(tmp_path):
    """The redactor must not mangle ordinary exporter status lines."""
    harness = _redactor_harness(tmp_path)
    for benign in (
        "O'Pip learning evidence export: OK",
        "O'Pip learning export: canonical replica bundle OK bytes=123456",
        "O'Pip learning export already active; skipping",
    ):
        proc = subprocess.run(
            ["bash", str(harness), benign],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.rstrip("\n") == benign, benign


def test_process_pattern_is_not_the_journal_pattern():
    """pgrep must not match log/lock filenames or system-wide keywords.

    Regression cover for the previous defect: a long-lived `tail`/`less` on
    /var/log/opip-learning-export.log would have matched the old shared pattern
    and, at elapsed >300s, falsely raised the stall verdict.
    """
    script = _script()
    assert "EXPORT_PROCESS_PATTERN=" in script
    pattern_line = next(
        line for line in script.splitlines() if line.startswith("EXPORT_PROCESS_PATTERN=")
    )
    # Deliberately narrow: only the exporter script name.
    assert "export-opip-learning-evidence" in pattern_line
    # No log filename or lock filename in the process pattern.
    assert "log" not in pattern_line
    assert "trigger" not in pattern_line
    # And both pgrep sites use the process pattern, never the journal pattern.
    block = _block()
    assert 'pgrep -fc "$EXPORT_PROCESS_PATTERN"' in block
    assert 'pgrep -f "$EXPORT_PROCESS_PATTERN"' in block
    assert 'pgrep -fc "$EXPORT_JOURNAL_PATTERN"' not in block
    assert 'pgrep -f "$EXPORT_JOURNAL_PATTERN"' not in block


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
        "mktemp",
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


def test_block_writes_no_file():
    """No redirection into any path: the block is read-only in both directions."""
    block = _block()
    # `>` and `>>` are only legitimate inside arithmetic comparisons.
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "((" in stripped:
            continue
        assert not re.search(r">>?\s*[\"$]", line), line
        assert not re.search(r">>?\s*\S*\.(log|tmp|env)", line), line


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
    """Build on-disk fixture state and return the paths the block reads."""
    export_root = tmp_path / "export"
    export_root.mkdir()
    manifest = export_root / "manifest.env"
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
        "replica_dir_name": replica_dir_name,
        "log": log_path,
        "receipt": receipt,
        "cron": cron,
        "cron_src": cron_src,
        "internal": tmp_path / "internal.lock",
        "wrapper": tmp_path / "wrapper.lock",
        "publish": export_root / ".publish.lock",
    }


def _write_manifest(fx: dict, exported_at: str) -> None:
    """Write the committed manifest with a chosen exported_at_utc."""
    replica_dir_name = fx["replica_dir_name"]
    fx["manifest"].write_text(
        "schema_version=4\n"
        f"exported_at_utc={exported_at}\n"
        f"production_deployed_sha={SHA}\n"
        "p1_shadow_outbox_retired=1\n"
        "canonical_learning_replica_version=1\n"
        f"canonical_learning_replica_dir={replica_dir_name}\n"
        "canonical_learning_replica_bytes=123456\n"
        "canonical_learning_replica_sha256=" + "b" * 64 + "\n",
        encoding="utf-8",
    )


def _run_block(
    tmp_path: Path, fx: dict, exported_at: str, *, path_prefix: Path | None = None
) -> dict:
    """Set ALL fixture state, then execute the block.

    Ordering matters: the manifest's exported_at_utc must be on disk *before*
    the subprocess runs, otherwise the timestamp under test cannot influence the
    behaviour being asserted. ``path_prefix`` prepends a directory to PATH so a
    test can shim a probe (e.g. hide lslocks to force the fuser fallback).
    """
    _write_manifest(fx, exported_at)
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
            "EXPORT_STALL_THRESHOLD_SECONDS=300",
            "EXPORT_JOURNAL_PATTERN="
            "'opip-learning-export|export-opip-learning-evidence|opip-learning-export-trigger\\.lock'",
            "EXPORT_PROCESS_PATTERN='export-opip-learning-evidence\\.sh'",
            f'PATH="{path_prefix}:$PATH"' if path_prefix else "",
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
    return fields


def _seam_functions() -> str:
    """Extract the evidence-to-verdict seam so its truth table can be tested.

    The decision is factored into these two pure functions precisely so the
    classification can be tested directly: reliably aging a real process beyond
    the threshold is impractical in CI, so the >threshold cases are proven here
    while real lslocks/fuser integration is proven separately.
    """
    block = _block()
    return block[block.index("classify_duration_verdict() {") : block.index("observe_lock_owner() {")]


@pytestmark_posix
def test_fixture_timestamp_is_written_before_execution(tmp_path):
    """Finding: the manifest timestamp must exist before the block runs.

    Proven through the value the block actually read: it reports the sha256 of
    the committed manifest, so if the manifest were (re)written after
    subprocess.run() the block would have hashed a different file and the first
    assertion would fail.
    """
    import hashlib

    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")

    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["manifest_sha256"] == hashlib.sha256(
        fx["manifest"].read_bytes()
    ).hexdigest()

    fields2 = _run_block(tmp_path, fx, "2026-09-17T21:15:00Z")
    assert fields2["manifest_sha256"] == hashlib.sha256(
        fx["manifest"].read_bytes()
    ).hexdigest()
    # The two runs genuinely saw different manifests, so the timestamp is written
    # per run and before execution rather than after it.
    assert fields["manifest_sha256"] != fields2["manifest_sha256"]


@pytestmark_posix
def test_untimestamped_log_is_never_called_post_manifest(tmp_path):
    """Production reality: the exporter's echoed lines carry no timestamp.

    The same untimestamped log must never be attributed to a post-manifest
    window, whatever the manifest timestamp is - not even when the log file's
    mtime is newer, and not even when it contains failure or skip text.
    """
    log_lines = "\n".join(
        ["O'Pip learning evidence export: OK"]
        + ["O'Pip learning export already active; skipping"] * 9
        + ["O'Pip learning export: canonical replica export FAILED (rc=3)"]
    )
    fx = _fixture(tmp_path)
    fx["log"].write_text(log_lines + "\n", encoding="utf-8")

    for exported_at in ("2026-09-17T19:00:47Z", "1999-01-01T00:00:00Z"):
        fields = _run_block(tmp_path, fx, exported_at)
        assert fields["export_log_timestamp_semantics"] == "UNTIMESTAMPED"
        assert fields["export_log_activity_class"] == "UNPROVABLE_FROM_UNTIMESTAMPED_LOG"
        assert (
            fields["export_log_post_manifest_evidence"]
            == "UNPROVABLE_FROM_UNTIMESTAMPED_LOG"
        )
        # Lifetime counters remain descriptive.
        assert fields["export_log_lifetime_skip_count"] == "9"
        assert fields["export_log_lifetime_failure_count"] == "1"
        # No event was attributed to the post-manifest window.
        assert fields["export_log_post_manifest_timestamped_count"] == "0"
        assert fields["export_log_post_manifest_skip_count"] == "0"
        assert fields["export_log_post_manifest_failure_count"] == "0"


@pytestmark_posix
def test_timestamped_log_discriminates_post_manifest_events(tmp_path):
    """Discrimination: identical log, different manifest timestamp.

    This is the assertion that fails if the manifest timestamp does not affect
    the run - a stale or post-execution write would make both cases identical.
    """
    older = "2026-09-17T18:00:00Z"
    newer = "2026-09-17T20:00:00Z"
    log_lines = (
        "2026-09-17T18:30:00Z O'Pip learning export already active; skipping\n"
        "2026-09-17T19:30:00Z O'Pip learning export already active; skipping\n"
    )
    fx = _fixture(tmp_path)
    fx["log"].write_text(log_lines, encoding="utf-8")

    # Manifest committed before both events: both are post-manifest skips.
    fields_old = _run_block(tmp_path, fx, older)
    assert fields_old["export_log_timestamp_semantics"] == "TIMESTAMPED"
    assert fields_old["export_log_timestamped_line_count"] == "2"
    assert fields_old["export_log_post_manifest_timestamped_count"] == "2"
    assert fields_old["export_log_post_manifest_skip_count"] == "2"
    assert fields_old["export_log_activity_class"] == "POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD"
    assert fields_old["export_log_post_manifest_evidence"] == "PROVEN"

    # Manifest committed after both events: neither is post-manifest, and no
    # recognized post-manifest event exists.
    fields_new = _run_block(tmp_path, fx, newer)
    assert fields_new["export_log_timestamped_line_count"] == "2"
    assert fields_new["export_log_post_manifest_timestamped_count"] == "0"
    assert fields_new["export_log_post_manifest_recognized_event_count"] == "0"
    assert fields_new["export_log_post_manifest_skip_count"] == "0"
    assert (
        fields_new["export_log_activity_class"]
        == "NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
    )
    # Same lifetime totals both times: only the time scoping differs.
    assert fields_old["export_log_lifetime_skip_count"] == "2"
    assert fields_new["export_log_lifetime_skip_count"] == "2"
    # And the two runs genuinely differ, so the timestamp is not inert.
    assert (
        fields_old["export_log_activity_class"] != fields_new["export_log_activity_class"]
    )


@pytestmark_posix
def test_post_manifest_unrelated_line_is_not_degrading(tmp_path):
    """Case A: an unrelated timestamped line must not degrade diagnostics.

    A post-manifest timestamp proves *when* a line was emitted, not that it is
    an exporter event. An unrelated line (a benign informational record, another
    consumer of the log) must be counted for observability but must not feed the
    classification or the degrade decision.
    """
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T19:05:00Z something else wrote this line\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["export_log_timestamped_line_count"] == "1"
    assert fields["export_log_post_manifest_timestamped_count"] == "1"
    assert fields["export_log_post_manifest_unclassified_line_count"] == "1"
    assert fields["export_log_post_manifest_recognized_event_count"] == "0"
    assert fields["export_log_post_manifest_skip_count"] == "0"
    assert fields["export_log_post_manifest_success_count"] == "0"
    assert fields["export_log_post_manifest_failure_count"] == "0"
    assert (
        fields["export_log_activity_class"]
        == "NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
    )
    assert (
        fields["export_log_post_manifest_evidence"]
        == "NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
    )
    # The lone reason to degrade below in this scenario is the pre-existing
    # export_committed_after_release_receipt check (the fixture's manifest was
    # written before the release receipt). The unrelated line contributed
    # nothing to that decision; confirm classification is not what degraded.
    for line in ("POST_MANIFEST_RUNS_FAIL", "POST_MANIFEST_RUNS_SKIPPED_LOCK_HELD"):
        assert fields["export_log_activity_class"] != line


@pytestmark_posix
def test_post_manifest_unrelated_line_then_success_is_success(tmp_path):
    """Case B: unrelated line, then a recognized success -> POST_MANIFEST_RUNS_SUCCEED."""
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T19:05:00Z something else wrote this line\n"
        "2026-09-17T19:06:00Z O'Pip learning evidence export: OK\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["export_log_post_manifest_timestamped_count"] == "2"
    assert fields["export_log_post_manifest_recognized_event_count"] == "1"
    assert fields["export_log_post_manifest_success_count"] == "1"
    assert fields["export_log_post_manifest_unclassified_line_count"] == "1"
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_SUCCEED"


@pytestmark_posix
def test_post_manifest_unrelated_line_then_failure_is_failure(tmp_path):
    """Case C: unrelated line, then a recognized failure -> POST_MANIFEST_RUNS_FAIL."""
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T19:05:00Z something else wrote this line\n"
        "2026-09-17T19:06:00Z O'Pip learning export: canonical replica export FAILED (rc=3)\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["export_log_post_manifest_timestamped_count"] == "2"
    assert fields["export_log_post_manifest_recognized_event_count"] == "1"
    assert fields["export_log_post_manifest_failure_count"] == "1"
    assert fields["export_log_post_manifest_unclassified_line_count"] == "1"
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_FAIL"


@pytestmark_posix
def test_historical_failure_before_manifest_does_not_contaminate(tmp_path):
    """Case D: a historical recognized failure with only unrelated post-manifest lines.

    The classification must not degrade on account of the historical event: only
    line_epoch > manifest_epoch can reach the recognized-event counters, so a
    pre-manifest failure is descriptive (lifetime) but not authoritative.
    """
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T18:00:00Z O'Pip learning export: canonical replica export FAILED (rc=3)\n"
        "2026-09-17T19:05:00Z something else wrote this line\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    # Lifetime totals still see the historical event...
    assert fields["export_log_lifetime_failure_count"] == "1"
    # ...but nothing recognized happened post-manifest.
    assert fields["export_log_post_manifest_recognized_event_count"] == "0"
    assert fields["export_log_post_manifest_failure_count"] == "0"
    assert fields["export_log_post_manifest_timestamped_count"] == "1"
    assert fields["export_log_post_manifest_unclassified_line_count"] == "1"
    assert (
        fields["export_log_activity_class"]
        == "NO_POST_MANIFEST_RECOGNIZED_EXPORT_EVIDENCE"
    )


@pytestmark_posix
def test_timestamped_log_proves_a_post_manifest_success(tmp_path):
    """A proven post-manifest success is classified as such."""
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T19:05:00Z O'Pip learning export: canonical replica bundle OK bytes=1\n"
        "2026-09-17T19:05:00Z O'Pip learning evidence export: OK\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_SUCCEED"
    assert fields["export_log_post_manifest_success_count"] == "1"
    # Only the terminal-OK line matches; the bundle-OK line is counted separately.
    assert fields["export_log_lifetime_success_count"] == "1"
    assert fields["export_log_lifetime_bundle_ok_count"] == "1"


@pytestmark_posix
def test_cron_probe_systemctl_unknown_is_pgrep_upgraded_to_yes(tmp_path):
    """systemctl unknown + a real cron process must yield cron_daemon_active=YES.

    The block is executed with shims on PATH that make systemctl return
    "unknown" and pgrep return a valid pid. The dispatch must map unknown to
    UNKNOWN (not NO), and the fallback must upgrade to YES.
    """
    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    shim = tmp_path / "shim"
    shim.mkdir()
    systemctl = shim / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "is-active cron") echo unknown; exit 0 ;;\n'
        '  "show -p") echo ""; exit 0 ;;\n'
        'esac\n'
        'echo ""\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    pgrep = shim / "pgrep"
    pgrep.write_text('#!/bin/sh\necho 12345\n', encoding="utf-8")
    pgrep.chmod(0o755)
    ps = shim / "ps"
    ps.write_text("#!/bin/sh\necho 'Thu Sep 17 12:00:00 2026'\n", encoding="utf-8")
    ps.chmod(0o755)

    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z", path_prefix=shim)
    assert fields["cron_daemon_active"] == "YES"
    assert fields["cron_daemon_source"] == "PGREP"
    assert fields["cron_daemon_pid"] == "12345"
    # The state reports where the YES came from so it is not confused with an
    # authoritative systemctl "active".
    assert fields["cron_daemon_state"] == "PROCESS_PRESENT"
    # And the degrade decision does not fire on cron in this case.
    assert not fields.get("cron_daemon_active", "") == "NO"


@pytestmark_posix
def test_cron_probe_systemctl_inactive_is_no(tmp_path):
    """systemctl inactive is proof of inactivity - keep mapping it to NO."""
    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    shim = tmp_path / "shim"
    shim.mkdir()
    systemctl = shim / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "is-active cron") echo inactive; exit 0 ;;\n'
        'esac\n'
        'echo ""\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    # A pgrep that would falsely upgrade is deliberately available, and MUST NOT
    # be consulted because systemctl was authoritative.
    pgrep = shim / "pgrep"
    pgrep.write_text('#!/bin/sh\necho 77777\n', encoding="utf-8")
    pgrep.chmod(0o755)

    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z", path_prefix=shim)
    assert fields["cron_daemon_active"] == "NO"
    assert fields["cron_daemon_source"] == "SYSTEMCTL"
    assert fields["cron_daemon_state"] == "inactive"
    # The fallback is NOT consulted, so the falsely-friendly pgrep pid does not
    # leak in.
    assert fields["cron_daemon_pid"] != "77777"


@pytestmark_posix
def test_cron_probe_systemctl_unknown_and_no_pgrep_evidence_stays_unknown(tmp_path):
    """systemctl unknown + pgrep proves nothing must remain UNKNOWN, not fabricate."""
    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    shim = tmp_path / "shim"
    shim.mkdir()
    systemctl = shim / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        'case "$1 $2" in\n'
        '  "is-active cron") echo unknown; exit 0 ;;\n'
        'esac\n'
        'echo ""\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)
    pgrep = shim / "pgrep"
    # pgrep proves nothing: empty stdout, non-zero exit.
    pgrep.write_text('#!/bin/sh\nexit 1\n', encoding="utf-8")
    pgrep.chmod(0o755)

    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z", path_prefix=shim)
    assert fields["cron_daemon_active"] == "UNKNOWN"
    # State is the raw systemctl answer (no fabricated PROCESS_PRESENT here).
    assert fields["cron_daemon_state"] == "unknown"


@pytestmark_posix
def test_lock_stall_truth_table(tmp_path):
    """Exhaustive truth table for the evidence-to-verdict seam.

    The critical row is OPENED_UNCONFIRMED with a large elapsed: opening a file
    does not prove holding the lock on it, so no duration may upgrade it to YES.
    """
    harness = tmp_path / "seam.sh"
    harness.write_text(
        "set -Eeuo pipefail\n"
        "EXPORT_STALL_THRESHOLD_SECONDS=300\n"
        + _seam_functions()
        + '\nprintf "%s" "$(classify_lock_stall_verdict "$1" "$2")"\n',
        encoding="utf-8",
    )
    cases = [
        # state, elapsed, expected
        ("HELD", "900", "YES"),
        ("HELD", "301", "YES"),
        ("HELD", "300", "NO"),          # threshold is exclusive
        ("HELD", "100", "NO"),
        ("HELD", "0", "NO"),
        ("HELD", "", "UNKNOWN"),        # unprovable duration
        ("HELD", "UNKNOWN", "UNKNOWN"),
        # An unconfirmed opener is UNKNOWN whatever its age.
        ("OPENED_UNCONFIRMED", "900", "UNKNOWN"),
        ("OPENED_UNCONFIRMED", "999999", "UNKNOWN"),
        ("OPENED_UNCONFIRMED", "301", "UNKNOWN"),
        ("OPENED_UNCONFIRMED", "10", "UNKNOWN"),
        ("OPENED_UNCONFIRMED", "", "UNKNOWN"),
        ("OPENED_UNCONFIRMED", "UNKNOWN", "UNKNOWN"),
        ("NOT_HELD", "9999", "NO"),
        ("NOT_HELD", "", "NO"),
        ("ABSENT", "9999", "NO"),
        ("WEIRD", "9999", "UNKNOWN"),
    ]
    for state, elapsed, expected in cases:
        proc = subprocess.run(
            ["bash", str(harness), state, elapsed],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == expected, (
            f"classify_lock_stall_verdict({state!r}, {elapsed!r}) "
            f"returned {proc.stdout.strip()!r}, expected {expected!r}"
        )


@pytestmark_posix
def test_duration_verdict_requires_past_threshold(tmp_path):
    """The duration-only seam (used for a positively identified process)."""
    harness = tmp_path / "seam.sh"
    harness.write_text(
        "set -Eeuo pipefail\n"
        "EXPORT_STALL_THRESHOLD_SECONDS=300\n"
        + _seam_functions()
        + '\nprintf "%s" "$(classify_duration_verdict "$1")"\n',
        encoding="utf-8",
    )
    for elapsed, expected in (
        ("900", "YES"),
        ("301", "YES"),
        ("300", "NO"),
        ("0", "NO"),
        ("", "UNKNOWN"),
        ("UNKNOWN", "UNKNOWN"),
    ):
        proc = subprocess.run(
            ["bash", str(harness), elapsed],
            capture_output=True, text=True, encoding="utf-8",
        )
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == expected, f"{elapsed!r} -> {proc.stdout!r}"


@pytestmark_posix
@pytest.mark.skipif(
    shutil.which("fuser") is None, reason="fuser (psmisc) required for the fallback path"
)
def test_unconfirmed_opener_is_unknown_never_a_stall(tmp_path):
    """Real integration of the fuser fallback: open without locking.

    ``lslocks`` is shimmed away so the fuser path is genuinely exercised. The
    opener is this test process, which has the file open but holds no lock on it.
    The verdict must be UNKNOWN - not NO (the age is meaningful only for proven
    ownership) and emphatically not YES.
    """
    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    opener = open(fx["internal"], "w")
    shim = tmp_path / "shim"
    shim.mkdir()
    lslocks = shim / "lslocks"
    lslocks.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    lslocks.chmod(0o755)
    try:
        fields = _run_block(
            tmp_path, fx, "2026-09-17T19:00:47Z", path_prefix=shim
        )
        # The fallback was genuinely used, and the file is genuinely open.
        assert fields["internal_export_lock_file"] == "EXISTS"
        assert fields["internal_export_lock_owner_source"] == "FUSER_OPENERS"
        assert fields["internal_export_lock_state"] == "OPENED_UNCONFIRMED"
        # The opener is real and its identity is reported as evidence.
        assert fields["internal_export_lock_owner_pid"] == str(os.getpid())
        assert fields["internal_export_lock_owner_comm"] not in ("", "UNKNOWN")
        assert fields["internal_export_lock_owner_elapsed_seconds"] not in ("", "UNKNOWN")
        # Ownership is NOT proven, so no duration can be applied to it.
        assert fields["internal_export_lock_ownership_proven"] == "NO"
        assert fields["internal_export_lock_held_instantaneously"] == "NO"
        assert fields["internal_export_lock_stall_verdict"] == "UNKNOWN"
        # And the derived decision never becomes YES. At least one lock file
        # exists but none is provably held, so the overall verdict is UNKNOWN.
        assert fields["export_lock_stall_suspected"] == "UNKNOWN"
        assert fields["export_lock_stall_suspected"] != "YES"
    finally:
        opener.close()


@pytestmark_posix
def test_long_lived_log_consumer_is_not_reported_as_the_exporter(tmp_path):
    """A `tail`/`less` on the log file must not appear as an exporter.

    Regression cover: the previous pattern matched the log filename
    (opip-learning-export.log), so any long-lived tail/less/rotator would be
    counted by pgrep -f as the exporter. If its elapsed age exceeded the stall
    threshold, that would falsely set the stall verdict to YES. The pgrep
    pattern is now the exporter script name only, so this test process spawning
    a long-lived subprocess that reads the log path must NOT count.
    """
    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    # A subprocess whose command line contains the log filename but not the
    # exporter script name. sleep is a harmless long-lived stand-in.
    consumer = subprocess.Popen(
        ["sh", "-c", f'exec -a "cat /var/log/opip-learning-export.log" sleep 30'],
    )
    try:
        fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
        assert fields["export_process_count"] == "0"
        assert fields["export_process_present"] == "NO"
        # And nothing raises a false stall.
        assert fields["export_lock_stall_suspected"] != "YES"
    finally:
        consumer.terminate()
        try:
            consumer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            consumer.kill()
            consumer.wait(timeout=5)


@pytestmark_posix
@pytest.mark.skipif(
    shutil.which("lslocks") is None,
    reason="lslocks (util-linux) required to observe kernel-reported flock ownership",
)
def test_true_flock_ownership_is_reported_and_young_holder_is_not_a_stall(tmp_path):
    """Real integration of the lslocks path: a genuine flock IS ownership.

    Proves the strong evidence path still works end to end. The holder is this
    test process, so its age is far below the threshold and the verdict is NO -
    proving a real held lock is observed but not automatically called a stall.
    """
    import fcntl

    fx = _fixture(tmp_path)
    fx["log"].write_text("O'Pip learning evidence export: OK\n", encoding="utf-8")
    holder = open(fx["internal"], "w")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX)
    try:
        fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
        assert fields["internal_export_lock_file"] == "EXISTS"
        assert fields["internal_export_lock_owner_source"] == "LSLOCKS"
        assert fields["internal_export_lock_state"] == "HELD"
        assert fields["internal_export_lock_ownership_proven"] == "YES"
        assert fields["internal_export_lock_held_instantaneously"] == "YES"
        assert fields["internal_export_lock_owner_pid"] == str(os.getpid())
        # A real held lock, but a young holder: observed, not condemned.
        assert fields["internal_export_lock_stall_verdict"] == "NO"
        assert fields["export_stall_threshold_seconds"] == "300"
        assert fields["export_lock_stall_suspected"] == "NO"
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()


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

    # The log carries no timestamps, so no post-manifest event attribution is
    # claimed; the frozen state is reported through metadata and counts instead.
    assert fields["export_log_activity_class"] == "UNPROVABLE_FROM_UNTIMESTAMPED_LOG"
    assert fields["export_log_lifetime_skip_count"] == "9"
    assert fields["FINAL_STATUS"] == "DEGRADED"  # export committed before the receipt
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
    # No process of this name is running, so no stall is claimed.
    assert fields["export_process_present"] == "NO"
    assert fields["export_lock_stall_suspected"] == "NO"


@pytestmark_posix
def test_block_classifies_failing_replica_export(tmp_path):
    fx = _fixture(tmp_path)
    fx["log"].write_text(
        "2026-09-17T19:05:00Z O'Pip learning export: canonical replica export FAILED (rc=3)\n"
        "2026-09-17T19:05:00Z O'Pip learning evidence export: JSON artifacts OK, canonical replica FAILED\n",
        encoding="utf-8",
    )
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["export_log_activity_class"] == "POST_MANIFEST_RUNS_FAIL"
    # Both post-manifest lines match the recognized-failure pattern
    # (`canonical replica export FAILED` and `canonical replica FAILED`), so the
    # counter is 2 rather than 1. What matters for the verdict is that at least
    # one recognized failure was seen.
    assert int(fields["export_log_post_manifest_failure_count"]) >= 1
    assert fields["export_log_post_manifest_recognized_event_count"] == "2"
    assert fields["FINAL_STATUS"] == "DEGRADED"


@pytestmark_posix
def test_block_refuses_a_non_content_addressed_replica_dir(tmp_path):
    """A malformed or traversing name must never be used as a path."""
    fx = _fixture(tmp_path, replica_dir_name="../../etc")
    fields = _run_block(tmp_path, fx, "2026-09-17T19:00:47Z")
    assert fields["replica_dir_name_valid"] == "NO"
    assert fields["replica_dir_exists"] == "NOT_REFERENCED"
    # Because the name never becomes a path, no inner manifest is inspected at
    # all: the traversal is refused rather than partially followed.
    assert "replica_inner_generation_id" not in fields
    assert "replica_inner_source_release_sha" not in fields
    assert "replica_contract_freshness_1800s" not in fields
    # The name is still reported verbatim so the defect is visible.
    assert fields["manifest_replica_dir"] == "../../etc"
