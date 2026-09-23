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


def test_the_workflow_does_not_start_the_committee_timer(workflow_text: str) -> None:
    """Initial OFF installation must not enable scheduled committee execution.

    Owner requirement: install the service and timer artifacts, keep mode off, and
    leave the timer disabled/inactive so no scheduled run happens. Timer activation
    belongs to the later SHADOW activation boundary.
    """
    # The `--enable-timer` argument must not be passed to the bootstrap invocation.
    # (The workflow may *comment* that it is deliberately not passed, so assert on the
    # invocation line itself rather than on the whole file.)
    invocation_lines = [
        line
        for line in workflow_text.splitlines()
        if "bootstrap-opip-committee-worker.sh" in line and not line.strip().startswith("#")
    ]
    assert len(invocation_lines) == 1, invocation_lines
    # Everything after the script name is the argument list: the exact SHA and no flags.
    tail = invocation_lines[0].split("bootstrap-opip-committee-worker.sh", 1)[1]
    assert "--" not in tail, tail
    assert "'$TARGET_SHA'\"" in tail, tail
    # The intent is stated, not merely implemented.
    assert "disabled and inactive" in workflow_text
    assert "no scheduled committee execution" in workflow_text


def test_off_mode_egress_is_deny_all_with_no_provider_allowlist(
    workflow_text: str,
) -> None:
    """OFF-mode egress fails closed; host names are not a boundary."""
    service = (COMMITTEE_DEPLOY / "opip-committee-shadow.service").read_text(
        encoding="utf-8"
    )
    assert "IPAddressDeny=any" in service
    # No allowlist at all: not host names, not addresses.
    allow_lines = [
        line
        for line in service.splitlines()
        if line.strip().startswith("IPAddressAllow=")
    ]
    assert allow_lines == [], allow_lines
    for host in ("api.openai.com", "api.anthropic.com"):
        assert host not in service, host
    # The boundary is stated where it is enforced, and in the receipt.
    assert "deny-all" in service
    assert "No provider allowlist" in workflow_text


def test_cleanup_is_a_success_requirement_not_best_effort(
    workflow_text: str,
) -> None:
    """A proven deployment with a failed remote cleanup is not a success."""
    workflow = yaml.safe_load(workflow_text)
    steps = {step.get("name"): step for step in workflow["jobs"]["deploy"]["steps"]}
    cleanup = steps["Clean remote release"]
    # Explicitly NOT continue-on-error, so a failed removal fails the job.
    assert cleanup.get("continue-on-error") is not True
    assert cleanup.get("id") == "committee_cleanup"
    assert cleanup.get("if") == "always() && steps.target.outputs.sha != ''"
    # The result is captured and the step exits non-zero on failure.
    assert 'echo "result=CLEANED"' in workflow_text
    assert 'exit "$RC"' in workflow_text
    # The receipt surfaces it.
    assert "CLEANUP_RESULT" in workflow_text
    assert "**Remote cleanup:**" in workflow_text
    # And the final gate requires it.
    assert 'test "$CLEANUP_RESULT" = "CLEANED"' in workflow_text
    assert "was not removed" in workflow_text


def test_the_receipt_reports_the_required_fields(workflow_text: str) -> None:
    for token in (
        "## O'Pip Intelligence Committee Deployment Receipt",
        "**Result:**",
        "**SHA:**",
        "Remote exit codes:",
        "**Remote cleanup:**",
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
        "disable path is available",
        "rollback path is valid",
        "no committee container is running",
        "ISOLATION_PROOF=",
        # Owner remediation 1: OFF-mode egress must be deny-all and fail closed.
        "egress is deny-all at the unit level",
        "OFF-mode egress fails closed",
        "no IPAddressAllow entry",
        # Owner remediation 2: no scheduled committee execution while OFF.
        "timer is not enabled",
        "no scheduled committee execution",
        "timer is inactive",
        "not continuously active",
        # Owner remediation 4: provider credentials absent from diagnostics.
        "no provider credential is exposed through service diagnostics",
        "exposes no provider credential name",
        "emit no provider credential name",
        "no credential-looking value is inlined",
        "credential-shaped value",
    ):
        assert claim in script, claim


def test_the_diagnostics_check_covers_each_systemd_surface_separately() -> None:
    """A clean result on one diagnostic surface must not mask a leak on another."""
    script = (COMMITTEE_DEPLOY / "verify-committee-isolation.sh").read_text(
        encoding="utf-8"
    )
    # (a) `systemctl show` — the Environment= property, readable via systemctl.
    assert 'show_output="$(systemctl show "$UNIT"' in script
    # (b) `systemctl cat` — the unit file text.
    assert 'cat_output="$(systemctl cat "$UNIT"' in script
    # (c) `journalctl` — what an operator actually reads.
    assert 'journal_output="$(journalctl -u "$UNIT"' in script
    # The credential names are data-driven, and only names are referenced.
    assert "PROVIDER_CREDENTIAL_NAMES=" in script
    assert "OPIP_COMMITTEE_OPENAI_API_KEY" in script
    assert "OPIP_COMMITTEE_ANTHROPIC_API_KEY" in script
    # Credentials must arrive only by file reference.
    assert "EnvironmentFile=" in script


def test_the_diagnostics_check_never_prints_a_credential() -> None:
    """The proof must print counts and statuses, never a value or the environment."""
    script = (COMMITTEE_DEPLOY / "verify-committee-isolation.sh").read_text(
        encoding="utf-8"
    )
    # A matching diagnostic line must never be echoed.
    assert "grep -qE" in script  # matched/not-matched only
    for forbidden in (
        "cat \"$ENV_FILE\"",
        "cat $ENV_FILE",
        "systemctl show -p Environment --value \"$UNIT\" | grep -v",
        "echo \"$show_output\"",
        "echo \"$journal_output\"",
        "printf '%s\\n' \"$show_output\"",
        "printf '%s\\n' \"$journal_output\"",
        "set -x",
        "env |",
        "printenv",
    ):
        assert forbidden not in script, forbidden


def test_the_timer_and_service_state_checks_are_proofs_not_advisories() -> None:
    """Timer/service state must be PASS/FAIL, since OFF mode requires them inert."""
    script = (COMMITTEE_DEPLOY / "verify-committee-isolation.sh").read_text(
        encoding="utf-8"
    )
    timer_block = script.split("# ------------------------------------------- scheduled execution is off")[
        1
    ].split("# ---------------------------------------------- provider credentials")[0]
    # Every assertion in the block must fail closed, not merely report.
    assert "fail " in timer_block
    assert "is-enabled" in timer_block
    assert "ActiveState" in timer_block
    assert 'timer_state" == "disabled"' in script
    assert 'service_active" == "inactive"' in script


def test_the_bootstrap_does_not_enable_the_oneshot_service() -> None:
    """The service has no [Install] section, so enabling it would abort the install.

    This mattered for owner remediation 2: the install-without-timer path must
    actually succeed, and `systemctl enable` on a unit with no installation config
    exits non-zero, which `set -Eeuo pipefail` would turn into a hard failure.
    """
    bootstrap = (COMMITTEE_DEPLOY / "bootstrap-opip-committee-worker.sh").read_text(
        encoding="utf-8"
    )
    service = (COMMITTEE_DEPLOY / "opip-committee-shadow.service").read_text(
        encoding="utf-8"
    )
    assert "[Install]" not in service
    assert "systemctl enable opip-committee-shadow.service" not in bootstrap
    # The timer is explicitly disabled on the no-timer path.
    assert "systemctl disable opip-committee-shadow.timer" in bootstrap
    # And the units are still installed.
    assert "install -m 0644 -o root -g root \"$SOURCE_DIR/opip-committee-shadow.service\"" in bootstrap
    assert "install -m 0644 -o root -g root \"$SOURCE_DIR/opip-committee-shadow.timer\"" in bootstrap


def test_the_bootstrap_still_supports_the_timer_for_the_later_boundary() -> None:
    """--enable-timer remains available for the separately authorised activation."""
    bootstrap = (COMMITTEE_DEPLOY / "bootstrap-opip-committee-worker.sh").read_text(
        encoding="utf-8"
    )
    assert "--enable-timer" in bootstrap
    assert 'systemctl enable --now opip-committee-shadow.timer' in bootstrap


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
