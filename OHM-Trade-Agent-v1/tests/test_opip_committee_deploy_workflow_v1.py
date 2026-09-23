"""Committee deployment workflow contract (IC-042 governance).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the deployment control plane's governance. They are the Committee
analogue of the learning deployment contract tests: install-only, owner-gated,
exact-main, exact-SHA-CI-gated, secret-from-Actions-only, fail-closed, and never an
activation of credentialled SHADOW calls.

They assert the workflow's *shape*. They cannot execute it, and they do not attempt
to: the workflow runs on a protected environment with host secrets that an agent must
never hold.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-committee.yml"
LEARNING_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-learning.yml"
COMMITTEE_DEPLOY = REPO_ROOT / "OHM-Trade-Agent-v1" / "deploy" / "committee"


@pytest.fixture(scope="module")
def workflow_text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


# ------------------------------------------------------------ trigger shape


def test_the_workflow_is_owner_gated_on_an_exact_sha_command(workflow_text: str) -> None:
    assert "github.event.comment.user.login == github.repository_owner" in workflow_text
    assert "github.event.comment.author_association == 'OWNER'" in workflow_text
    assert "/deploy-committee" in workflow_text
    # The format check must require exactly 40 lowercase hex characters.
    assert "[0-9a-f]{40}" in workflow_text
    assert "command format is /deploy-committee <40-char-sha>" in workflow_text


def test_the_workflow_mirrors_the_learning_governance_shape(workflow_text: str) -> None:
    """The same governance shape, not a novel one."""
    learning = LEARNING_WORKFLOW.read_text(encoding="utf-8")
    for token in (
        "github.event.issue.number == 64",
        "github.event.comment.user.login == github.repository_owner",
        "github.event.comment.author_association == 'OWNER'",
        "Require target to equal current main",
        "Require successful exact-SHA CI",
        "persist-credentials: false",
        "Clean remote release",
    ):
        assert token in workflow_text, token
        assert token in learning, token


def test_the_target_must_equal_current_main(workflow_text: str) -> None:
    assert "Refusing committee deployment: target is not current main." in workflow_text
    # The deploy ref is the exact SHA, never a branch.
    assert "ref: ${{ steps.target.outputs.sha }}" in workflow_text
    # A manual or branch-based trigger would bypass the exact-SHA requirement.
    assert "workflow_dispatch" not in workflow_text
    assert "refs/heads" not in workflow_text
    # `--branch main` appears only to scope the CI lookup, which is correct: it asks
    # for main's run of the exact SHA, it does not deploy from a branch.
    assert "--branch main" in workflow_text
    assert "steps.target.outputs.sha" in workflow_text


def test_exact_sha_ci_must_be_successful(workflow_text: str) -> None:
    assert "pytest.yml" in workflow_text
    assert "--commit \"$TARGET_SHA\"" in workflow_text
    assert "is not successful for the exact SHA" in workflow_text
    assert "completed\\tsuccess" in workflow_text


# ------------------------------------------------------- protected environment


def test_a_dedicated_protected_environment_gates_the_host_secrets(workflow) -> None:
    """The Committee environment must be separate from learning and analytics."""
    assert workflow["jobs"]["deploy"]["environment"] == "committee-shadow"


def test_permissions_are_read_only_plus_issue_comments(workflow) -> None:
    permissions = workflow["permissions"]
    assert permissions["contents"] == "read"
    assert permissions["actions"] == "read"
    assert permissions["issues"] == "write"


# ------------------------------------------------------------ connection path


def test_the_connection_reuses_the_established_host_secrets(workflow_text: str) -> None:
    """The approved host identity, not a newly invented one."""
    for secret in (
        "OPIP_LEARNING_HOST",
        "OPIP_LEARNING_USER",
        "OPIP_LEARNING_PORT",
        "OPIP_LEARNING_SSH_KEY_B64",
        "OPIP_LEARNING_KNOWN_HOSTS",
    ):
        assert f"secrets.{secret}" in workflow_text, secret
    # Host key validation must stay strict.
    assert "StrictHostKeyChecking=yes" in workflow_text
    assert "IdentitiesOnly=yes" in workflow_text
    assert "BatchMode=yes" in workflow_text
    assert "ssh-keygen -y -f ~/.ssh/opip_committee" in workflow_text


def test_no_local_workstation_credential_is_referenced(workflow_text: str) -> None:
    """The workflow must not reach for a developer's local key or config."""
    for forbidden in (
        "~/.ssh/id_rsa",
        "~/.ssh/id_ed25519",
        "OPIP_LOCAL_SSH",
        "ssh-agent",
        "SSH_AUTH_SOCK",
        "known_hosts.local",
    ):
        assert forbidden not in workflow_text, forbidden


def test_no_provider_credential_enters_the_deployment_workflow(
    workflow_text: str,
) -> None:
    """Deployment installs artifacts; it must not carry a model provider key."""
    for forbidden in (
        "OPIP_COMMITTEE_OPENAI_API_KEY",
        "OPIP_COMMITTEE_ANTHROPIC_API_KEY",
        "OPENAI_API_KEY=",
        "ANTHROPIC_API_KEY=",
        "sk-",
    ):
        assert forbidden not in workflow_text, forbidden


def test_no_trading_credential_is_referenced(workflow_text: str) -> None:
    for forbidden in ("KRAKEN_API", "KRAKEN_SECRET", "TELEGRAM_BOT", "TELEGRAM_TOKEN"):
        assert forbidden not in workflow_text, forbidden


# --------------------------------------------------------- deployment behavior


def test_the_exact_release_is_checked_out_without_persisted_credentials(
    workflow_text: str,
) -> None:
    assert "ref: ${{ steps.target.outputs.sha }}" in workflow_text
    assert "persist-credentials: false" in workflow_text


def test_only_the_committed_artifact_is_invoked(workflow_text: str) -> None:
    """No inline install logic: the reviewed artifact from the release tree is run."""
    assert "deploy/committee/bootstrap-opip-committee-worker.sh" in workflow_text
    assert "deploy/committee/verify-committee-isolation.sh" in workflow_text
    assert "'$RELEASE_DIR/OHM-Trade-Agent-v1/deploy/committee/" in workflow_text


def test_the_deployed_mode_stays_off(workflow_text: str) -> None:
    """Installation must not activate credentialled SHADOW calls."""
    assert "OPIP_COMMITTEE_MODE=off" in workflow_text
    assert "OPIP_COMMITTEE_MODE=shadow" not in workflow_text
    assert "does **not**" in workflow_text
    assert "activate credentialled SHADOW calls" in workflow_text


def test_the_workflow_uploads_only_the_exact_release_tree(workflow_text: str) -> None:
    assert "git archive --format=tar \"$TARGET_SHA\"" in workflow_text
    assert "/var/tmp/opip-committee-$TARGET_SHA-$GITHUB_RUN_ID" in workflow_text


# ------------------------------------------------------- mandatory verification


def test_isolation_proof_is_mandatory_and_machine_readable(
    workflow_text: str,
) -> None:
    assert "verify-committee-isolation.sh" in workflow_text
    # The verdict comes from the proof line, not merely the exit code.
    assert "grep -q '^ISOLATION_PROOF=PASS$'" in workflow_text
    assert 'echo "result=PROVEN"' in workflow_text


def test_the_receipt_reports_the_required_fields(workflow_text: str) -> None:
    for token in (
        "## O'Pip Intelligence Committee Deployment Receipt",
        "**Result:**",
        "**SHA:**",
        "Remote exit codes:",
        "Workflow run:",
        "ISOLATION_PROOF=",
    ):
        assert token in workflow_text, token


def test_the_receipt_never_prints_environment_contents(workflow_text: str) -> None:
    """The receipt must summarise proof lines, never dump the environment."""
    assert "grep -E '^(PASS|FAIL|INFO|ISOLATION_PROOF=)'" in workflow_text
    for forbidden in (
        "cat /etc/opip/committee-credentials.env",
        "cat \"$ENV_FILE\"",
        "env |",
        "printenv",
        "set -x",
    ):
        assert forbidden not in workflow_text, forbidden


def test_the_receipt_states_the_activation_boundary(workflow_text: str) -> None:
    assert "OFF -> credentialled SHADOW" in workflow_text
    assert "separate" in workflow_text
    assert "OWNER-authorised" in workflow_text


# ----------------------------------------------------------- failure behavior


def test_the_workflow_fails_closed_on_every_required_condition(
    workflow_text: str,
) -> None:
    for condition in (
        "target is not current main",
        "is not successful for the exact SHA",
        "Validate committee host connection secrets",
        "Committee installation did not succeed",
        "Committee OFF-mode isolation was not proven",
    ):
        assert condition in workflow_text, condition
    # The final gate requires both stages to have succeeded.
    assert 'test "$INSTALL_RESULT" = "INSTALLED"' in workflow_text
    assert 'test "$ISOLATION_RESULT" = "PROVEN"' in workflow_text
    # Absent host secrets fail the run rather than skipping the deploy.
    assert "test -n \"$SSH_KEY_B64\"" in workflow_text
    assert "test -n \"$KNOWN_HOSTS\"" in workflow_text


def test_the_remote_release_is_always_cleaned(workflow_text: str) -> None:
    assert "name: Clean remote release" in workflow_text
    assert "if: always() && steps.target.outputs.sha != ''" in workflow_text
    assert "sudo -n rm -rf -- '$RELEASE_DIR'" in workflow_text


def test_the_isolation_stage_is_skipped_without_a_successful_install(
    workflow_text: str,
) -> None:
    """Proving isolation for an install that never happened would be meaningless."""
    assert (
        "if: always() && steps.committee_install.outputs.result == 'INSTALLED'"
        in workflow_text
    )


def test_the_workflow_cannot_merge_or_release(workflow_text: str) -> None:
    for forbidden in (
        "gh pr merge",
        "git push",
        "git merge",
        "gh release create",
        "/deploy-production",
        "/deploy-learning",
        "/deploy-analytics",
        "systemctl start opip-learning",
    ):
        assert forbidden not in workflow_text, forbidden
    # Concurrency is serialised so two installs cannot interleave on the host.
    workflow = yaml.safe_load(workflow_text)
    assert workflow["concurrency"]["group"] == "opip-committee"
    assert workflow["concurrency"]["cancel-in-progress"] is False


# --------------------------------------------- the installed artifacts agree


def _bash() -> str | None:
    """A usable bash, or ``None`` on a host without one.

    The CI runner is Linux and has bash; a Windows workstation may not. The syntax
    check is skipped where bash is genuinely unavailable rather than failing for the
    wrong reason.
    """
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


def test_the_workflow_target_script_is_the_committed_bootstrap() -> None:
    """The invoked path must exist in the release tree and be syntactically valid."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash available to syntax-check the deployment scripts")
    for name in (
        "bootstrap-opip-committee-worker.sh",
        "verify-committee-isolation.sh",
        "run-committee-shadow-cycle.sh",
    ):
        script = COMMITTEE_DEPLOY / name
        assert script.exists(), name
        subprocess.run([bash, "-n", str(script)], check=True)


def test_the_verification_script_proves_every_claim_the_workflow_reports() -> None:
    """The receipt's claims must map onto checks that actually exist."""
    script = (COMMITTEE_DEPLOY / "verify-committee-isolation.sh").read_text(
        encoding="utf-8"
    )
    for claim in (
        "release identity is an exact 40-character SHA",
        "mode is off in the environment file",
        "every recorded cycle is an OFF skip",
        "no committee work",
        "read-only",
        "advisory directory is restricted",
        "trading-credential names",
        "no listening socket",
        "memory limit applied",
        "egress deny rule",
        "disable path is available",
        "no committee container is running",
        "ISOLATION_PROOF=",
    ):
        assert claim in script, claim


def test_the_credentials_template_declares_no_real_credential() -> None:
    template = (COMMITTEE_DEPLOY / "committee-credentials.env.example").read_text(
        encoding="utf-8"
    )
    credential_lines = [
        line
        for line in template.splitlines()
        if re.match(r"^OPIP_COMMITTEE_(OPENAI|ANTHROPIC)_API_KEY=", line)
    ]
    assert len(credential_lines) == 2
    for line in credential_lines:
        assert line.endswith("=CHANGEME"), line
