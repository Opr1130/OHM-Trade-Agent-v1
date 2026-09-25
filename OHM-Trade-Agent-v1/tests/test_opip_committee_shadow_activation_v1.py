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
        "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE=1",
    ):
        assert key in template, key
