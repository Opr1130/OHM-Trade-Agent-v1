"""Committee SHADOW activation control plane (IC-043 governance).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the governance shape of the OFF -> credentialled SHADOW
boundary. They assert the *shape* of the activation workflow and the committed
host scripts; they cannot execute them, because activation runs on a protected
environment with host secrets an agent must never hold.

The most important assertion in this file is a separation: the installation
workflow must still prove it never activates credentialled SHADOW calls, while the
activation workflow is the only place that may.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
ACTIVATION = WORKFLOW_DIR / "committee-shadow-activation.yml"
INSTALL = WORKFLOW_DIR / "deploy-committee.yml"
COMMITTEE_DEPLOY = REPO_ROOT / "OHM-Trade-Agent-v1" / "deploy" / "committee"

#: Windows ``STATUS_DLL_INIT_FAILED``: bash never started, so there is no verdict.
_BASH_LAUNCH_FAILURE = 3221225794


@pytest.fixture(scope="module")
def activation_text() -> str:
    return ACTIVATION.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def activation() -> dict:
    return yaml.safe_load(ACTIVATION.read_text(encoding="utf-8"))


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


def _run_bash(argv: list[str]) -> subprocess.CompletedProcess:
    for _ in range(3):
        proc = subprocess.run(argv, capture_output=True, text=True)
        if proc.returncode == _BASH_LAUNCH_FAILURE and not proc.stdout and not proc.stderr:
            continue
        return proc
    pytest.skip("bash could not be launched on this host; no verdict was produced")


# ------------------------------------------------------------------- trigger shape


def test_activation_is_owner_gated_on_issue_64(activation_text: str) -> None:
    assert "github.event.issue.number == 64" in activation_text
    assert "github.event.comment.user.login == github.repository_owner" in activation_text
    assert "github.event.comment.author_association == 'OWNER'" in activation_text
    for command in (
        "/shadow-committee ",
        "/committee-canary ",
        "/committee-timer ",
        "/rollback-committee",
    ):
        assert command in activation_text, command
    assert "[0-9a-f]{40}" in activation_text


def test_activation_uses_the_dedicated_protected_environment(activation: dict) -> None:
    assert activation["jobs"]["control"]["environment"] == "committee-shadow"


def test_activation_never_uses_a_manual_or_branch_trigger(activation_text: str) -> None:
    assert "workflow_dispatch" not in activation_text
    assert "refs/heads" not in activation_text


def test_every_sha_bearing_operation_requires_the_exact_main_sha(
    activation_text: str,
) -> None:
    assert "Refusing committee activation: target is not current main." in activation_text
    assert "ref: ${{ steps.command.outputs.sha }}" in activation_text
    assert "pytest.yml" in activation_text
    assert "is not successful for the exact SHA" in activation_text


# ------------------------------------------------------------------- separation


def test_only_the_activation_workflow_may_enable_shadow_mode() -> None:
    """The install workflow must keep proving it never activates SHADOW calls."""
    install_text = INSTALL.read_text(encoding="utf-8")
    activation_text = ACTIVATION.read_text(encoding="utf-8")
    activate_script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "OPIP_COMMITTEE_MODE=shadow" not in install_text
    # The activation boundary is the only path that sets the mode to shadow, and it
    # does so from the committed script rather than inline workflow logic.
    assert "OPIP_COMMITTEE_MODE shadow" in activate_script
    assert "activate-committee-shadow.sh" in activation_text


def test_activation_reuses_the_established_host_identity(activation_text: str) -> None:
    for secret in ("OPIP_LEARNING_HOST", "OPIP_LEARNING_SSH_KEY_B64"):
        assert f"secrets.{secret}" in activation_text, secret
    assert "StrictHostKeyChecking=yes" in activation_text
    assert "IdentitiesOnly=yes" in activation_text
    assert "BatchMode=yes" in activation_text


def test_activation_carries_no_credential_value(activation_text: str) -> None:
    """The boundary reads the host's own file; a value must never travel in YAML."""
    for forbidden in (
        "OPENAI_API_KEY=",
        "ANTHROPIC_API_KEY=",
        "sk-",
        "cat /etc/opip/committee-credentials.env",
        "set -x",
        "printenv",
    ):
        assert forbidden not in activation_text, forbidden


def test_activation_references_no_trading_credential(activation_text: str) -> None:
    for forbidden in ("KRAKEN_API", "KRAKEN_SECRET", "TELEGRAM_BOT", "TELEGRAM_TOKEN"):
        assert forbidden not in activation_text, forbidden


def test_activation_cannot_merge_or_release(activation_text: str) -> None:
    for forbidden in ("gh pr merge", "git push", "git merge", "gh release create"):
        assert forbidden not in activation_text, forbidden
    assert "systemctl start opip-learning" not in activation_text


def test_activation_is_serialised_with_the_install_workflow(activation: dict) -> None:
    """Both touch the same host; they must never interleave."""
    assert activation["concurrency"]["group"] == "opip-committee"
    assert activation["concurrency"]["cancel-in-progress"] is False


def test_activation_runs_only_the_committed_artifacts(activation_text: str) -> None:
    assert "deploy/committee/activate-committee-shadow.sh" in activation_text
    assert "deploy/committee/verify-committee-shadow.sh" in activation_text
    assert "git archive --format=tar \"$TARGET_SHA\"" in activation_text


def test_activation_always_cleans_the_remote_release(activation_text: str) -> None:
    assert "name: Clean remote release" in activation_text
    assert "sudo -n rm -rf -- '$RELEASE_DIR'" in activation_text


def test_activation_receipt_never_dumps_the_environment(activation_text: str) -> None:
    assert "grep -hE '^(PASS|FAIL|INFO|ROLLBACK_APPLIED=|SHADOW_ACTIVATION=|SHADOW_PROOF=)'" in (
        activation_text
    )
    assert "gh api --method POST \"repos/$GITHUB_REPOSITORY/issues/64/comments\"" in (
        activation_text
    )


def test_activation_fails_closed_per_command(activation_text: str) -> None:
    for token in (
        "SHADOW activation did not succeed.",
        "SHADOW mode was not proven.",
        "Canary cycle did not run.",
        "The recurring timer was not enabled.",
        "Rollback was not proven.",
        "The temporary remote release directory was not removed.",
    ):
        assert token in activation_text, token


# ---------------------------------------------------------------- committed scripts


@pytest.mark.parametrize(
    "name",
    ["activate-committee-shadow.sh", "verify-committee-shadow.sh"],
)
def test_the_new_host_scripts_are_syntactically_valid(name: str) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("no bash available to syntax-check the deployment scripts")
    script = COMMITTEE_DEPLOY / name
    assert script.exists(), name
    proc = _run_bash([bash, "-n", str(script)])
    assert proc.returncode == 0, (name, proc.stderr)


def test_activation_script_refuses_placeholder_credentials() -> None:
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    # A template value must be detected as unset, not treated as a credential.
    assert "PLACEHOLDER_VALUES=" in script
    assert "are still placeholders" in script
    assert "are unset" in script
    # It must never echo a value.
    for forbidden in (
        'echo "$value"',
        "printf '%s' \"$value\"",
        'echo "$PROVIDER_CREDENTIAL_NAMES',
        "set -x",
    ):
        assert forbidden not in script, forbidden


def test_activation_script_pins_provider_only_egress() -> None:
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "PROVIDER_ENDPOINTS=(api.openai.com api.anthropic.com)" in script
    assert "IPAddressAllow=" in script
    assert "10-provider-egress.conf" in script
    # An unresolvable endpoint must refuse activation rather than widen egress.
    assert "could not resolve" in script


def test_activation_script_sets_an_explicit_activation_boundary() -> None:
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    for key in (
        "OPIP_COMMITTEE_SHADOW_NOT_BEFORE",
        "OPIP_COMMITTEE_REGISTRY_REVIEW_BY",
        "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE",
    ):
        assert key in script, key
    # The recurring timer is opt-in and never implied by simple activation.
    assert "--enable-timer" in script
    assert "SHADOW_ACTIVATION=PASS" in script


def test_the_shadow_proof_script_proves_the_claims_the_receipt_reports() -> None:
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    for claim in (
        "mode is shadow in the environment file",
        "provider egress allowlist is installed",
        "belongs to an approved provider endpoint",
        "egress default remains deny-all",
        "activation boundary is recorded",
        "registry review date is recorded",
        "capped at one case",
        "no Kraken/Telegram/trading credential name",
        "exposes no provider credential name",
        "no listening socket",
        "advisory evidence directory is present",
        "ROLLBACK_APPLIED=off+deny-all",
        "ROLLBACK_PROOF",
        "SHADOW_PROOF",
    ):
        assert claim in script, claim


def test_the_shadow_proof_script_never_prints_a_credential() -> None:
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        'cat "$ENV_FILE"',
        'echo "$ENV_FILE"',
        "set -x",
        "env |",
        "printenv",
        'echo "$show_output"',
    ):
        assert forbidden not in script, forbidden


def test_the_rollback_path_is_non_destructive_to_advisory_evidence() -> None:
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    rollback = script.split('if [[ "${1:-}" == "--rollback" ]]')[1].split("else")[0]
    # Rollback removes the egress drop-in and restores mode off; it deletes no
    # evidence and stops no trading path.
    assert "rm -f \"$DROPIN\"" in rollback
    assert "OPIP_COMMITTEE_MODE=off" in rollback
    for forbidden in ("rm -rf \"$COMMITTEE_HOME\"", "rm -rf /var/lib/opip-committee"):
        assert forbidden not in script, forbidden


def test_the_credentials_template_documents_the_activation_inputs() -> None:
    template = (COMMITTEE_DEPLOY / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    )
    for key in (
        "OPIP_COMMITTEE_SHADOW_NOT_BEFORE",
        "OPIP_COMMITTEE_REGISTRY_REVIEW_BY",
        "OPIP_COMMITTEE_LEARNING_MANIFEST",
        "OPIP_CANONICAL_REPLICA_ROOT_HOST",
        "OPIP_APP_ROOT",
        "OPIP_VENV_PYTHON",
        "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE=1",
    ):
        assert key in template, key


# ------------------------------------------- defects found in independent review


def test_every_referenced_workflow_step_id_exists(activation: dict) -> None:
    """A receipt or gate that reads a non-existent step id always sees empty.

    That is how a cleanup step with no `id` made every non-rollback command fail
    its own final gate while the receipt misreported the cleanup as NOT RUN.
    """
    import re

    steps = activation["jobs"]["control"]["steps"]
    declared = {step["id"] for step in steps if "id" in step}
    text = ACTIVATION.read_text(encoding="utf-8")
    referenced = set(re.findall(r"steps\.([A-Za-z0-9_-]+)\.outputs", text))
    missing = sorted(referenced - declared)
    assert missing == [], f"referenced but undeclared step ids: {missing}"


def test_the_cleanup_step_is_addressable_by_the_final_gate(activation: dict) -> None:
    steps = {
        step.get("name"): step for step in activation["jobs"]["control"]["steps"]
    }
    cleanup = steps["Clean remote release"]
    assert cleanup.get("id") == "committee_cleanup"
    # Cleanup remains a success requirement for the commands that upload a release.
    assert cleanup.get("continue-on-error") is not True


def test_the_canary_cannot_report_success_on_a_failed_remote_start() -> None:
    """The only execution proof must be able to fail."""
    text = ACTIVATION.read_text(encoding="utf-8")
    canary = text.split("Run one bounded credentialled canary cycle")[1].split(
        "Enable the bounded recurring timer"
    )[0]
    assert 'if [[ "$RC" -eq 0 ]]' in canary
    assert 'echo "result=RAN"' in canary
    assert 'echo "result=FAILED"' in canary
    # The gate requires RAN, so a non-zero remote start cannot pass.
    assert 'test "$CANARY_RESULT" = "RAN"' in text


def test_activation_validates_the_timestamps_before_they_reach_the_host() -> None:
    """An unvalidated value must not be interpolated into a remote sudo command."""
    text = ACTIVATION.read_text(encoding="utf-8")
    # Instants are constrained to ISO-8601 shape before they are used.
    assert r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}" in text
    # And the host script re-validates them independently.
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "must be an ISO-8601 UTC instant" in script


# ----------------------------------------------------------- parser behaviour


def _resolve_owner_command(body: str, tmp_path):
    """Run the workflow's real command parser and return (proc, outputs).

    The parser lives in the workflow's `run:` block. Executing that exact script
    is what makes a group-index mistake visible: asserting on the pattern's shape
    would not have caught it.
    """
    import os
    import subprocess

    bash = _bash()
    if bash is None:
        pytest.skip("no bash available to exercise the command parser")
    steps = yaml.safe_load(ACTIVATION.read_text(encoding="utf-8"))["jobs"]["control"][
        "steps"
    ]
    step = next(item for item in steps if item.get("id") == "command")
    script_path = tmp_path / "resolve_command.sh"
    script_path.write_text(step["run"], encoding="utf-8")
    output_path = tmp_path / "github_output.txt"
    output_path.write_text("", encoding="utf-8")
    env = {**os.environ, "COMMENT_BODY": body, "GITHUB_OUTPUT": str(output_path)}
    proc = subprocess.run(
        [bash, str(script_path)], capture_output=True, text=True, env=env
    )
    return proc, output_path.read_text(encoding="utf-8")


def test_the_parser_extracts_both_instants_in_full(tmp_path) -> None:
    """A nested group once shifted the index, so review-by became the timezone."""
    body = (
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00Z 2026-12-25T00:00:00Z"
    )
    proc, outputs = _resolve_owner_command(body, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "command=shadow" in outputs
    assert "sha=004d2f05cf92668594dafb492420eeda1dc74506" in outputs
    assert "not_before=2026-09-25T13:20:00Z" in outputs
    assert "review_by=2026-12-25T00:00:00Z" in outputs


def test_the_parser_accepts_a_numeric_offset_in_both_instants(tmp_path) -> None:
    body = (
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00+00:00 2026-12-25T00:00:00-05:00"
    )
    proc, outputs = _resolve_owner_command(body, tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "not_before=2026-09-25T13:20:00+00:00" in outputs
    assert "review_by=2026-12-25T00:00:00-05:00" in outputs


@pytest.mark.parametrize(
    "body",
    [
        # A shell metacharacter in an instant must never reach the remote command.
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00Z 2026-12-25T00:00:00Z';nc -l 1;'",
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "not-a-date 2026-12-25T00:00:00Z",
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00Z not-a-date",
        "/shadow-committee deadbeef "
        "2026-09-25T13:20:00Z 2026-12-25T00:00:00Z",
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00Z",
        "/shadow-committee 004d2f05cf92668594dafb492420eeda1dc74506 "
        "2026-09-25T13:20:00Z 2026-12-25T00:00:00Z extra",
        "/shadow-committee",
        "/not-a-command 004d2f05cf92668594dafb492420eeda1dc74506",
    ],
)
def test_the_parser_refuses_anything_that_is_not_strictly_formed(body, tmp_path) -> None:
    proc, outputs = _resolve_owner_command(body, tmp_path)
    assert proc.returncode == 64, (proc.returncode, proc.stdout, proc.stderr)
    assert outputs.strip() == ""


def test_activation_refuses_when_the_worker_cannot_execute() -> None:
    """Activation must not report PASS on a worker that cannot start one cycle."""
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "OPIP_APP_ROOT" in script
    assert "the worker application root" in script
    assert "import app.opip.committee.cycle_runner" in script
    proof = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "the worker interpreter can import the committee cycle runner" in proof
    assert "the worker interpreter is executable" in proof


def test_name_resolution_survives_the_deny_all_egress_policy() -> None:
    """Deny-all also denies the resolver, so resolution must be allowed explicitly."""
    activate = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "/etc/resolv.conf" in activate
    assert "nameserver" in activate
    proof = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "/etc/resolv.conf" in proof


def test_the_rollback_proof_does_not_require_the_allowlist_it_just_removed() -> None:
    """Rollback removes the drop-in, so it must be proven absent, not present."""
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    rollback = script.split('if [[ "${1:-}" == "--rollback" ]]')[1].split(
        "# ------------------------------------------------------------- SHADOW-mode only"
    )[0]
    assert "ROLLBACK_PROOF=PASS" in rollback
    assert "no provider egress allowlist remains" in rollback
    # It must not fail the run for the allowlist being absent.
    assert "no provider egress allowlist is installed" not in rollback
