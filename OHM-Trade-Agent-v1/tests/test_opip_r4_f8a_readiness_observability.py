"""R4-F8A Paper-v2 cutover readiness observability.

These tests prove a *read-only* observability follow-up: the sanctioned
``diagnose-learning`` wrapper now runs the deployed bounded readiness probe so
the owner can observe the R4-A cutover-readiness evidence in production through
the existing audited path. It adds no authority, no remote command dispatch, and
no mutation, and the forced-command gateway keeps exactly its two entry points.

The assertions are static (they read the shell wrapper and the Python job), so
they run on every platform without needing a POSIX shell or Docker.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
WRAPPER = APP_ROOT / "deploy" / "remote" / "diagnose-opip-learning.sh"
GATEWAY = APP_ROOT / "deploy" / "remote" / "ohm-deploy-ssh"
READINESS_JOB = APP_ROOT / "app" / "jobs" / "report_paper_v2_cutover_readiness.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "pytest.yml"
DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-production.yml"

pytestmark = pytest.mark.acceptance

#: The bounded readiness section the wrapper emits.
SECTION_START = "# R4-F8A - Paper-v2 cutover readiness evidence (read-only)."
SECTION_END = 'echo "OPIP_PAPER_V2_CUTOVER_READINESS_END"'

READINESS_COMMAND = "python -m app.jobs.report_paper_v2_cutover_readiness"


def _wrapper() -> str:
    return WRAPPER.read_text(encoding="utf-8")


def _readiness_section() -> str:
    text = _wrapper()
    start = text.index(SECTION_START)
    end = text.index(SECTION_END, start)
    return text[start : end + len(SECTION_END)]


# ---------------------------------------------------------------------------
# AC-001 - the sanctioned wrapper runs the readiness probe, bounded
# ---------------------------------------------------------------------------


def test_ac_001_wrapper_runs_the_deployed_readiness_probe():
    """ATDD-R4-F8A-readiness-observability/AC-001: the sanctioned diagnostics wrapper invokes the bounded readiness job."""
    text = _wrapper()
    assert text.count(READINESS_COMMAND) == 1
    section = _readiness_section()
    assert READINESS_COMMAND in section
    # It runs inside the core container through the existing read-only exec path.
    assert "docker exec ohm-trade-agent" in section
    # Bounded section markers so the output is machine-readable.
    assert 'echo "OPIP_PAPER_V2_CUTOVER_READINESS"' in section


def test_ac_001_probe_is_time_boxed_and_byte_bounded():
    """ATDD-R4-F8A-readiness-observability/AC-001: a slow or noisy probe cannot stall, persist, or flood."""
    section = _readiness_section()
    # Host-side deadline for a stuck Docker client.
    assert "timeout --signal=TERM --kill-after=5s 45 docker exec" in section
    # Container-side deadline so a stalled read does not keep running in the core.
    assert "timeout --signal=TERM --kill-after=5s 40 \\" in section
    # The byte ceiling is applied while the probe streams.
    assert "| head -c 8000" in section


def test_ac_001_probe_reports_unavailable_when_the_container_is_not_running():
    """ATDD-R4-F8A-readiness-observability/AC-001: absent or stopped core reports UNAVAILABLE rather than nothing."""
    section = _readiness_section()
    assert 'docker inspect ohm-trade-agent >/dev/null 2>&1' in section
    assert ".State.Running" in section
    # Two UNAVAILABLE branches: no container, and an incomplete/failed probe.
    assert section.count('echo "readiness=UNAVAILABLE"') == 2


def test_ac_001_incomplete_probe_is_rejected_not_printed():
    """ATDD-R4-F8A-readiness-observability/AC-001: a failed, timed-out or truncated probe is never accepted as evidence."""
    section = _readiness_section()
    code = _code_lines(section)
    # The probe's own status is never masked with `|| true`.
    capture_block = section[
        section.index('readiness_verdict="$(', section.index("State.Running"))
        : section.index("if printf '%s")
    ]
    assert "|| true" not in capture_block, capture_block
    # Failure is absorbed without aborting the wrapper, and completeness is proven
    # by the verdict line the job prints last.
    assert sum(1 for line in code if line.startswith('if ! readiness_verdict="$(')) == 1
    assert (
        sum(
            1
            for line in code
            if line.startswith("if printf '%s\\n' \"$readiness_verdict\" | grep -q '^Readiness:'")
        )
        == 1
    )


def test_ac_001_probe_emits_no_raw_argv_or_environment():
    """ATDD-R4-F8A-readiness-observability/AC-001: the probe reports typed facts only, never argv or env."""
    section = _readiness_section()
    for forbidden in ("cmdline", "ps -o args", "printenv", "os.environ", "env |"):
        assert forbidden not in section, forbidden


# ---------------------------------------------------------------------------
# AC-002 - NOT_READY is evidence; only an unavailable probe degrades
# ---------------------------------------------------------------------------


def _code_lines(section: str) -> list[str]:
    """Non-comment, non-blank lines of the readiness section."""
    return [
        line.strip()
        for line in section.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_ac_002_unavailable_probe_degrades_but_a_verdict_does_not():
    """ATDD-R4-F8A-readiness-observability/AC-002: only unavailability degrades learning diagnostics, never the verdict itself."""
    section = _readiness_section()
    code = _code_lines(section)
    # The two UNAVAILABLE branches each call `degrade`; nothing else does.
    assert code.count("degrade") == 2, code
    assert section.count('echo "readiness=UNAVAILABLE"') == 2
    # Nothing in the executable section branches on the readiness verdict text,
    # so a NOT_READY result is reported as expected evidence rather than a failure.
    for line in code:
        assert "NOT_READY" not in line, line
        assert "SHORT_AUTHORITY_MISSING" not in line, line
    # No parsing of the probe's verdict is introduced.
    assert "jq" not in section


def test_ac_002_readiness_section_precedes_the_final_status_line():
    """ATDD-R4-F8A-readiness-observability/AC-002: the section survives the workflow's bounded output window."""
    text = _wrapper()
    assert text.index(SECTION_START) < text.index('echo "diagnostics_status=$status"')


def test_ac_002_published_window_leaves_room_for_earlier_diagnostics():
    """ATDD-R4-F8A-readiness-observability/AC-002: the readiness section cannot crowd out the diagnostics it accompanies."""
    workflow = DEPLOY_WORKFLOW.read_text(encoding="utf-8")
    window_match = re.search(r"tail -c (\d+) diagnostics\.log", workflow)
    assert window_match, "diagnostics publish window not found"
    window = int(window_match.group(1))
    cap_match = re.search(r"head -c (\d+)", _readiness_section())
    assert cap_match, "readiness byte cap not found"
    cap = int(cap_match.group(1))
    # The readiness section must stay a minority of the published window so the
    # export, lock, manifest and funnel diagnostics keep their place.
    assert cap * 2 <= window, (cap, window)


# ---------------------------------------------------------------------------
# AC-003 - no mutation, no activation, no widened remote authority
# ---------------------------------------------------------------------------


def test_ac_003_section_performs_no_mutation_or_activation():
    """ATDD-R4-F8A-readiness-observability/AC-003: the section writes nothing and activates nothing."""
    code = _code_lines(_readiness_section())
    for forbidden in (
        "OPIP_PAPER_V2_MODE=",
        "export OPIP_PAPER_V2_MODE",
        "rm ",
        "mv ",
        "truncate",
        "tee ",
        "> ",
        ">>",
        "systemctl ",
        "service ",
        "kill ",
        "pkill ",
    ):
        for line in code:
            assert forbidden not in line, (forbidden, line)


def test_ac_003_forced_command_gateway_is_unchanged():
    """ATDD-R4-F8A-readiness-observability/AC-003: remote execution authority is not widened by this follow-up."""
    gateway = GATEWAY.read_text(encoding="utf-8")
    assert gateway.count("exec sudo") == 2
    assert gateway.count("SSH_ORIGINAL_COMMAND") == 1
    assert gateway.count("$ORIGINAL") == 2
    assert "refusing command" in gateway
    for forbidden in ("bash -c", "sh -c", "eval "):
        assert forbidden not in gateway
    # The readiness job is reached only through the fixed wrapper, never as a
    # caller-supplied remote command.
    assert READINESS_COMMAND not in gateway


def test_ac_003_readiness_job_has_no_activation_or_write_surface():
    """ATDD-R4-F8A-readiness-observability/AC-003: the deployed job starts the read-only report and nothing else."""
    tree = ast.parse(READINESS_JOB.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    for name in imported:
        assert not name.startswith("app.opip.committee")
        assert not name.startswith("app.opip.features")
        assert not name.startswith("app.exchanges")
    source = READINESS_JOB.read_text(encoding="utf-8")
    assert "setenv" not in source
    # The job's only production call is the read-only readiness report.
    assert "cutover_readiness_report()" in source


def test_ac_003_wrapper_remains_argument_and_argv_free():
    """ATDD-R4-F8A-readiness-observability/AC-003: the wrapper still accepts no caller-supplied command."""
    text = _wrapper()
    assert "SSH_ORIGINAL_COMMAND" not in text
    assert '"$@"' not in text
    assert '"$*"' not in text
    assert "cmdline" not in text


# ---------------------------------------------------------------------------
# AC-004 - the wrapper stays valid and syntax-checked
# ---------------------------------------------------------------------------


def test_ac_004_wrapper_is_syntax_checked_by_ci():
    """ATDD-R4-F8A-readiness-observability/AC-004: the modified wrapper is added to the CI shell-syntax gate."""
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "bash -n deploy/remote/diagnose-opip-learning.sh" in workflow


def test_ac_004_readiness_section_is_well_formed():
    """ATDD-R4-F8A-readiness-observability/AC-004: the section is balanced and inside one container guard."""
    section = _readiness_section()
    code = _code_lines(section)
    assert sum(1 for line in code if line.startswith("if docker inspect")) == 1
    assert sum(1 for line in code if line.startswith('if ! readiness_verdict="$(')) == 1
    assert sum(1 for line in code if line.startswith("if printf '%s\\n' \"$readiness_verdict\" | grep -q")) == 1
    # Outer container guard (if/else) + the completeness guard (if/else); the
    # `if !` capture has no else. Three else/fi closers total.
    assert code.count("else") == 2, code
    assert code.count("fi") == 3, code
    # No nested function definition or heredoc sneaks in.
    assert "<<" not in section
    assert re.search(r"^\w+\(\)", section, re.MULTILINE) is None
