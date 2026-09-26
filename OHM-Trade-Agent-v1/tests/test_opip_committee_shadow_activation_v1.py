"""Committee SHADOW activation control plane (IC-043 governance).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

These tests hold the governance shape of the OFF -> credentialled SHADOW
boundary. Source assertions cover the activation workflow and the committed host
scripts. Behavioral cases execute those scripts under
``OPIP_COMMITTEE_RUNTIME_TEST_HARNESS`` with a mock ``systemctl``. They do not
contact a host, and they never accept a credential value in output.

The most important assertion in this file is a separation: the installation
workflow must still prove it never activates credentialled SHADOW calls, while the
activation workflow is the only place that may.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import re
import shutil
import stat
import subprocess
import time

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


@pytest.fixture(scope="module")
def install_workflow() -> dict:
    return yaml.safe_load(INSTALL.read_text(encoding="utf-8"))


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


def _run_bash(
    argv: list[str],
    *,
    cwd: pathlib.Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    for _ in range(3):
        proc = subprocess.run(argv, capture_output=True, text=True, cwd=cwd, env=env)
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
    # The receipt prints proof lines and machine-readable markers only.
    assert "grep -hE '^(PASS|FAIL|INFO|ROLLBACK_APPLIED=|ROLLBACK_PROOF=|SHADOW_ACTIVATION=|SHADOW_PROOF=|STABLE_BUNDLE=|STABLE_PROOF=|SAFE_OFF=|pre_operation_shadow=|release_compatibility_status=)'" in (
        activation_text
    )
    assert "gh api --method POST \"repos/$GITHUB_REPOSITORY/issues/64/comments\"" in (
        activation_text
    )


def test_activation_fails_closed_per_command(activation_text: str) -> None:
    for token in (
        "SHADOW activation did not succeed.",
        "The stable proof bundle was not installed.",
        "The installed stable proof helper could not prove the plane.",
        "SHADOW mode was not proven.",
        "Pre-canary SHADOW proof failed; the canary was not started.",
        "Canary cycle did not run.",
        "Pre-timer SHADOW proof failed; the timer was not enabled.",
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
    rollback = script.split('if [[ "$ROLLBACK_MODE" -eq 1 ]]; then')[1].split(
        "# ------------------------------------------------------------- SHADOW-mode only"
    )[0]
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
    # Use the guarded runner: a Git-bash launch failure must skip rather than fail,
    # exactly as every other bash invocation in this module does.
    proc = _run_bash([bash, str(script_path)], env=env)
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
    rollback = script.split('if [[ "$ROLLBACK_MODE" -eq 1 ]]; then')[1].split(
        "# ------------------------------------------------------------- SHADOW-mode only"
    )[0]
    assert "ROLLBACK_PROOF=PASS" in rollback
    assert "no provider egress allowlist remains" in rollback
    # It must not fail the run for the allowlist being absent.
    assert "no provider egress allowlist is installed" not in rollback


def test_activation_workflow_does_not_set_the_runtime_test_harness(
    activation_text: str,
) -> None:
    assert "OPIP_COMMITTEE_RUNTIME_TEST_HARNESS" not in activation_text


# ---------------------------------------------------- harness execution (A-F)

_SHA = "3457d59fb80c68d5c49a6c1df9ebe1fc7c2bbf8e"
_NOT_BEFORE = "2026-09-25T13:20:00Z"
_REVIEW_BY = "2026-12-25T00:00:00Z"
_OPENAI_SENTINEL = "SENTINEL_OPENAI_CREDENTIAL_VALUE"
_ANTHROPIC_SENTINEL = "SENTINEL_ANTHROPIC_CREDENTIAL_VALUE"
_DISAGREEMENT = (
    "unit-level mode 'off' disagrees with the environment file 'shadow'"
)

_SYSTEMCTL = r"""#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${OPIP_TEST_SYSTEMCTL_LOG:-}" ]]; then
  printf '%s\n' "$*" >> "$OPIP_TEST_SYSTEMCTL_LOG"
fi
UNIT_NAME="opip-committee-shadow.service"
UNIT_DIR="${OPIP_COMMITTEE_UNIT_DIR:?systemctl mock requires OPIP_COMMITTEE_UNIT_DIR}"
UNIT_FILE="$UNIT_DIR/$UNIT_NAME"
DROPIN_DIR="$UNIT_DIR/${UNIT_NAME}.d"
ENABLEMENT="${OPIP_TEST_TIMER_ENABLEMENT:?}"
ACTIVE_FILE="${OPIP_TEST_TIMER_ACTIVE:?}"

merge_environment() {
  local state line rest key value conf
  state="$(mktemp)"
  : > "$state"
  apply_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      case "$line" in
        Environment=)
          : > "$state"
          ;;
        Environment=*)
          rest="${line#Environment=}"
          if [[ "$rest" != *=* ]]; then
            continue
          fi
          key="${rest%%=*}"
          value="${rest#*=}"
          [[ -n "$key" ]] || continue
          if [[ -s "$state" ]]; then
            grep -v "^${key}=" "$state" > "${state}.next" || true
          else
            : > "${state}.next"
          fi
          printf '%s=%s\n' "$key" "$value" >> "${state}.next"
          mv "${state}.next" "$state"
          ;;
      esac
    done < "$file"
  }
  apply_file "$UNIT_FILE"
  if [[ -d "$DROPIN_DIR" ]]; then
    shopt -s nullglob
    local files=("$DROPIN_DIR"/*.conf)
    shopt -u nullglob
    if [[ ${#files[@]} -gt 0 ]]; then
      while IFS= read -r conf; do
        [[ -z "$conf" ]] && continue
        apply_file "$conf"
      done < <(printf '%s\n' "${files[@]}" | sort)
    fi
  fi
  if [[ "${OPIP_TEST_FORCE_UNIT_MODE:-}" == "off" ]]; then
    grep -v '^OPIP_COMMITTEE_MODE=' "$state" > "${state}.forced" || true
    printf '%s\n' 'OPIP_COMMITTEE_MODE=off' >> "${state}.forced"
    mv "${state}.forced" "$state"
  fi
  if [[ -s "$state" ]]; then
    paste -sd ' ' "$state"
  fi
  rm -f "$state"
}

merge_allows() {
  local state line conf
  state="$(mktemp)"
  : > "$state"
  collect_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0
    while IFS= read -r line || [[ -n "$line" ]]; do
      line="${line%$'\r'}"
      case "$line" in
        IPAddressAllow=*)
          printf '%s\n' "${line#IPAddressAllow=}" >> "$state"
          ;;
        *) ;;
      esac
    done < "$file"
  }
  collect_file "$UNIT_FILE"
  if [[ -d "$DROPIN_DIR" ]]; then
    shopt -s nullglob
    local files=("$DROPIN_DIR"/*.conf)
    shopt -u nullglob
    if [[ ${#files[@]} -gt 0 ]]; then
      while IFS= read -r conf; do
        [[ -z "$conf" ]] && continue
        collect_file "$conf"
      done < <(printf '%s\n' "${files[@]}" | sort)
    fi
  fi
  if [[ -n "${OPIP_TEST_FORCE_IP_ALLOW:-}" ]]; then
    printf '%s\n' "$OPIP_TEST_FORCE_IP_ALLOW" >> "$state"
  fi
  # systemd's in_addr_prefix_to_string always prints the prefix length.
  # A bare host in the drop-in is stored as /32 or /128 and shown that way.
  # An explicit prefix is left unchanged so a broader CIDR stays broader.
  canon="$(mktemp)"
  while IFS= read -r token || [[ -n "$token" ]]; do
    token="${token%$'\r'}"
    token="${token%% *}"
    [[ -z "$token" ]] && continue
    case "$token" in
      */*) printf '%s\n' "$token" >> "$canon" ;;
      *:*) printf '%s/128\n' "$token" >> "$canon" ;;
      *.*.*.*) printf '%s/32\n' "$token" >> "$canon" ;;
      *) printf '%s\n' "$token" >> "$canon" ;;
    esac
  done < "$state"
  if [[ -s "$canon" ]]; then
    paste -sd ' ' "$canon"
  fi
  rm -f "$state" "$canon"
}

cmd="${1:-}"
shift || true
case "$cmd" in
  daemon-reload)
    exit 0
    ;;
  is-enabled)
    if [[ -f "$ENABLEMENT" ]]; then
      state="$(tr -d '\r\n' < "$ENABLEMENT")"
    else
      state="disabled"
    fi
    printf '%s\n' "$state"
    case "$state" in
      enabled|enabled-runtime|linked|linked-runtime|alias) exit 0 ;;
      *) exit 1 ;;
    esac
    ;;
  disable)
    if [[ "${OPIP_TEST_DISABLE_ALWAYS_FAIL:-}" == "1" ]]; then
      exit 1
    fi
    if [[ "${OPIP_TEST_DISABLE_FAILS_ONCE:-}" == "1" && ! -f "${ENABLEMENT}.disable-once" ]]; then
      : > "${ENABLEMENT}.disable-once"
      exit 1
    fi
    if [[ "${OPIP_TEST_DISABLE_IGNORES_ONCE:-}" == "1" && ! -f "${ENABLEMENT}.disable-ignore" ]]; then
      : > "${ENABLEMENT}.disable-ignore"
      exit 0
    fi
    printf '%s\n' disabled > "$ENABLEMENT"
    exit 0
    ;;
  stop)
    if [[ "${OPIP_TEST_STOP_ALWAYS_FAIL:-}" == "1" ]]; then
      exit 1
    fi
    if [[ "${OPIP_TEST_STOP_FAILS_ONCE:-}" == "1" && ! -f "${ACTIVE_FILE}.stop-once" ]]; then
      : > "${ACTIVE_FILE}.stop-once"
      exit 1
    fi
    if [[ "${OPIP_TEST_STOP_IGNORES_ONCE:-}" == "1" && ! -f "${ACTIVE_FILE}.stop-ignore" ]]; then
      : > "${ACTIVE_FILE}.stop-ignore"
      exit 0
    fi
    printf '%s\n' inactive > "$ACTIVE_FILE"
    exit 0
    ;;
  enable)
    printf '%s\n' enabled > "$ENABLEMENT"
    if [[ "${1:-}" == "--now" ]]; then
      printf '%s\n' active > "$ACTIVE_FILE"
    fi
    exit 0
    ;;
  show)
    prop=""
    target=""
    while [[ $# -gt 0 ]]; do
      case "$1" in
        -p)
          prop="${2:-}"
          shift 2
          ;;
        --value)
          shift
          ;;
        *)
          target="$1"
          shift
          ;;
      esac
    done
    if [[ -z "$prop" ]]; then
      printf '%s\n' "Id=${target}" "FragmentPath=${UNIT_FILE}"
      exit 0
    fi
    case "$prop" in
      Environment)
        printf '%s\n' "$(merge_environment)"
        ;;
      IPAddressDeny)
        if [[ -n "${OPIP_TEST_FORCE_DENY:-}" ]]; then
          printf '%s\n' "$OPIP_TEST_FORCE_DENY"
        else
          deny="$(sed -n 's/^IPAddressDeny=//p' "$UNIT_FILE" | head -n1 | tr -d '\r')"
          printf '%s\n' "${deny:-any}"
        fi
        ;;
      IPAddressAllow)
        egress="$DROPIN_DIR/10-provider-egress.conf"
        if [[ -n "${OPIP_TEST_EFFECTIVE_IP_ALLOW+x}" && -f "$egress" ]]; then
          printf '%s\n' "$OPIP_TEST_EFFECTIVE_IP_ALLOW"
        else
          printf '%s\n' "$(merge_allows)"
        fi
        ;;
      ActiveState)
        if [[ -f "$ACTIVE_FILE" ]]; then
          tr -d '\r' < "$ACTIVE_FILE"
        else
          printf '%s\n' inactive
        fi
        ;;
      *)
        printf '\n'
        ;;
    esac
    exit 0
    ;;
  *)
    echo "unexpected systemctl command: $cmd" >&2
    exit 99
    ;;
esac
"""

_GETENT = """#!/usr/bin/env bash
set -euo pipefail
if [[ "${OPIP_TEST_GETENT_FAIL:-}" == "1" ]]; then
  exit 1
fi
if [[ "${1:-}" == "ahosts" ]]; then
  case "${2:-}" in
    api.openai.com|api.anthropic.com)
      printf '%s\\n' '203.0.113.10 STREAM'
      exit 0
      ;;
  esac
fi
exit 1
"""

_TIMEOUT = """#!/usr/bin/env bash
if [[ "${OPIP_TEST_TIMEOUT_FAIL:-}" == "1" ]]; then
  exit 1
fi
exit 0
"""

_PYTHON = """#!/usr/bin/env bash
exit 0
"""


def _bash_path(path: pathlib.Path) -> str:
    return path.resolve().as_posix()


def _write_exe(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _assert_no_sentinels(proc: subprocess.CompletedProcess[str]) -> None:
    blob = proc.stdout + proc.stderr
    assert _OPENAI_SENTINEL not in blob
    assert _ANTHROPIC_SENTINEL not in blob


def _plane(tmp_path: pathlib.Path, *, mode: str) -> dict[str, pathlib.Path]:
    root = tmp_path / "plane"
    unit_dir = root / "systemd"
    unit_dir.mkdir(parents=True)
    service = (COMMITTEE_DEPLOY / "opip-committee-shadow.service").read_text(encoding="utf-8")
    (unit_dir / "opip-committee-shadow.service").write_text(
        service.replace("\r\n", "\n"), encoding="utf-8", newline="\n"
    )
    dropin = unit_dir / "opip-committee-shadow.service.d"
    dropin.mkdir()
    app = root / "app"
    (app / "app" / "opip" / "committee").mkdir(parents=True)
    (app / "app" / "opip" / "committee" / "cycle_runner.py").write_text(
        "VALUE = 1\n", encoding="utf-8", newline="\n"
    )
    venv_python = root / "venv" / "bin" / "python"
    _write_exe(venv_python, _PYTHON)
    manifest = root / "manifest.env"
    manifest.write_text("RELEASE_SHA=test\n", encoding="utf-8", newline="\n")
    replica = root / "replica"
    replica.mkdir()
    evidence = root / "evidence"
    evidence.mkdir()
    advisory = root / "advisory"
    advisory.mkdir()
    (advisory / "role_results.jsonl").write_text("", encoding="utf-8", newline="\n")
    resolv = root / "resolv.conf"
    resolv.write_text("nameserver 203.0.113.53\n", encoding="utf-8", newline="\n")
    enablement = root / "timer-enablement"
    enablement.write_text("disabled\n", encoding="utf-8", newline="\n")
    active = root / "timer-active"
    active.write_text("inactive\n", encoding="utf-8", newline="\n")
    log = root / "systemctl.log"
    bin_dir = root / "bin"
    _write_exe(bin_dir / "systemctl", _SYSTEMCTL)
    _write_exe(bin_dir / "getent", _GETENT)
    _write_exe(bin_dir / "timeout", _TIMEOUT)
    env_file = root / "committee-credentials.env"
    env_file.write_text(
        "\n".join(
            [
                f"OPIP_COMMITTEE_OPENAI_API_KEY={_OPENAI_SENTINEL}",
                f"OPIP_COMMITTEE_ANTHROPIC_API_KEY={_ANTHROPIC_SENTINEL}",
                f"OPIP_COMMITTEE_MODE={mode}",
                f"OPIP_COMMITTEE_RELEASE_SHA={_SHA}",
                f"OPIP_COMMITTEE_SHADOW_NOT_BEFORE={_NOT_BEFORE}",
                f"OPIP_COMMITTEE_REGISTRY_REVIEW_BY={_REVIEW_BY}",
                "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE=1",
                f"OPIP_APP_ROOT={_bash_path(app)}",
                f"OPIP_VENV_PYTHON={_bash_path(venv_python)}",
                f"OPIP_COMMITTEE_LEARNING_MANIFEST={_bash_path(manifest)}",
                f"OPIP_CANONICAL_REPLICA_ROOT_HOST={_bash_path(replica)}",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )
    return {
        "root": root,
        "unit_dir": unit_dir,
        "dropin": dropin,
        "env": env_file,
        "advisory": advisory,
        "evidence": evidence,
        "resolv": resolv,
        "enablement": enablement,
        "active": active,
        "log": log,
        "bin": bin_dir,
        "app": app,
    }


def _egress(dropin: pathlib.Path) -> None:
    (dropin / "10-provider-egress.conf").write_text(
        "\n".join(
            [
                "[Service]",
                "IPAddressAllow=203.0.113.10",
                "IPAddressAllow=203.0.113.53",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )


def _mode_dropin(dropin: pathlib.Path) -> None:
    (dropin / "20-shadow-mode.conf").write_text(
        "[Service]\nEnvironment=OPIP_COMMITTEE_MODE=shadow\n",
        encoding="utf-8",
        newline="\n",
    )


def _harness_env(plane: dict[str, pathlib.Path]) -> dict[str, str]:
    env = os.environ.copy()
    # The scripts prefer these process variables over the harness env file.
    # A developer shell must not redirect the fixture at the real runtime.
    env.pop("OPIP_APP_ROOT", None)
    env.pop("OPIP_VENV_PYTHON", None)
    env.update(
        {
            "OPIP_COMMITTEE_RUNTIME_TEST_HARNESS": "1",
            "OPIP_COMMITTEE_HARNESS_ROOT": _bash_path(plane["root"]),
            "OPIP_COMMITTEE_UNIT_DIR": _bash_path(plane["unit_dir"]),
            "OPIP_COMMITTEE_ENV_FILE": _bash_path(plane["env"]),
            "OPIP_COMMITTEE_HOME": _bash_path(plane["advisory"]),
            "OPIP_COMMITTEE_EVIDENCE_ROOT": _bash_path(plane["evidence"]),
            "OPIP_COMMITTEE_RESOLV_CONF": _bash_path(plane["resolv"]),
            "OPIP_TEST_TIMER_ENABLEMENT": _bash_path(plane["enablement"]),
            "OPIP_TEST_TIMER_ACTIVE": _bash_path(plane["active"]),
            "OPIP_TEST_SYSTEMCTL_LOG": _bash_path(plane["log"]),
            "PATH": _bash_path(plane["bin"]) + os.pathsep + env.get("PATH", ""),
        }
    )
    return env


def _bash_child_died(proc: subprocess.CompletedProcess[str]) -> bool:
    """Git bash on this host can start, then fail every fork with 0xC0000142."""
    blob = f"{proc.stdout}{proc.stderr}"
    return proc.returncode == _BASH_LAUNCH_FAILURE or "0xC0000142" in blob or "3221225794" in blob


def _skip_if_bash_cannot_fork(proc: subprocess.CompletedProcess[str]) -> None:
    if _bash_child_died(proc):
        pytest.skip(
            "bash could not start (Windows exit 3221225794); no verdict was produced"
        )


@pytest.fixture(scope="module")
def fork_bash() -> str:
    bash = _bash()
    if bash is None:
        pytest.skip("no bash available to exercise the shadow scripts")
    proc = subprocess.run(
        [bash, "-c", "command -v sed >/dev/null && sed --version"],
        capture_output=True,
        text=True,
    )
    # Child processes of Git bash on this host die during DLL init
    # (Windows status 0xC0000142 / 3221225794), sometimes reported as 127.
    if _bash_child_died(proc) or (os.name == "nt" and proc.returncode != 0):
        pytest.skip(
            "bash could not start (Windows exit 3221225794); no verdict was produced"
        )
    if proc.returncode != 0:
        pytest.skip(f"bash cannot run sed ({proc.returncode})")
    return bash


def _chmod_advisory(bash: str, plane: dict[str, pathlib.Path]) -> None:
    bin_dir = _bash_path(plane["bin"])
    python_path = _bash_path(plane["root"] / "venv" / "bin" / "python")
    proc = _run_bash(
        [
            bash,
            "-c",
            "chmod 700 "
            f"\"{_bash_path(plane['advisory'])}\" && chmod +x "
            f"\"{bin_dir}/systemctl\" \"{bin_dir}/getent\" \"{bin_dir}/timeout\" "
            f"\"{python_path}\"",
        ]
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 0, proc.stderr


def _run_script(
    bash: str,
    script: pathlib.Path,
    args: list[str],
    plane: dict[str, pathlib.Path],
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    _chmod_advisory(bash, plane)
    env = _harness_env(plane)
    if extra:
        env.update(extra)
    proc = subprocess.run(
        [bash, str(script), *args],
        capture_output=True,
        text=True,
        env=env,
    )
    _skip_if_bash_cannot_fork(proc)
    _assert_no_sentinels(proc)
    return proc


def _file_mode(plane: dict[str, pathlib.Path]) -> str:
    for line in plane["env"].read_text(encoding="utf-8").splitlines():
        if line.startswith("OPIP_COMMITTEE_MODE="):
            return line.split("=", 1)[1]
    return ""


def _set_release_sha(plane: dict[str, pathlib.Path], value: str) -> None:
    """Rewrite the worker release SHA an existing plane fixture reports."""
    path = plane["env"]
    lines = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith("OPIP_COMMITTEE_RELEASE_SHA=")
    ]
    lines.append(f"OPIP_COMMITTEE_RELEASE_SHA={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _show_environment(bash: str, plane: dict[str, pathlib.Path]) -> str:
    proc = subprocess.run(
        [
            bash,
            "-c",
            "systemctl show -p Environment --value opip-committee-shadow.service",
        ],
        capture_output=True,
        text=True,
        env=_harness_env(plane),
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def _show_allow(bash: str, plane: dict[str, pathlib.Path]) -> str:
    proc = subprocess.run(
        [
            bash,
            "-c",
            "systemctl show -p IPAddressAllow --value opip-committee-shadow.service",
        ],
        capture_output=True,
        text=True,
        env=_harness_env(plane),
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _activation(plane: dict[str, pathlib.Path]) -> list[str]:
    return [_SHA, _NOT_BEFORE, _REVIEW_BY]


def _assert_proven_off(
    proc: subprocess.CompletedProcess[str],
    plane: dict[str, pathlib.Path],
    bash: str,
) -> None:
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_ACTIVATION=PASS" not in combined
    assert "SAFE_OFF=PROVEN" in proc.stdout
    assert "SAFE_OFF=FAIL" not in combined
    assert _file_mode(plane) == "off"
    assert not (plane["dropin"] / "10-provider-egress.conf").exists()
    assert not (plane["dropin"] / "20-shadow-mode.conf").is_file()
    assert _show_mode(bash, plane) == "off"
    assert _show_allow(bash, plane) == ""
    assert plane["enablement"].read_text(encoding="utf-8").strip() == "disabled"
    assert plane["active"].read_text(encoding="utf-8").strip() == "inactive"
    marker = plane["advisory"] / "role_results.jsonl"
    assert marker.is_file()


def _show_mode(bash: str, plane: dict[str, pathlib.Path]) -> str:
    proc = subprocess.run(
        [
            bash,
            "-c",
            "systemctl show -p Environment --value opip-committee-shadow.service"
            " | tr ' ' '\\n' | sed -n 's/^OPIP_COMMITTEE_MODE=//p' | head -n1",
        ],
        capture_output=True,
        text=True,
        env=_harness_env(plane),
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_shadow_proof_fails_when_unit_stays_off(tmp_path: pathlib.Path, fork_bash: str) -> None:
    """Case A: env file shadow without the mode drop-in still shows unit off."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--expected-sha", _SHA],
        plane,
    )
    assert proc.returncode == 1
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert _DISAGREEMENT in proc.stdout


def test_shadow_proof_passes_when_mode_dropin_overrides_base_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case B: 20-shadow-mode.conf makes show and the env file agree on shadow."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    _mode_dropin(plane["dropin"])
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--expected-sha", _SHA],
        plane,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout
    assert "unit-level mode agrees with the environment file (shadow)" in proc.stdout
    assert "PYTHONDONTWRITEBYTECODE=1" in _show_environment(bash, plane)


def test_rollback_proves_off_and_keeps_advisory_evidence(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case C: rollback removes both drop-ins and proves unit and file are off."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    _mode_dropin(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    stale = plane["dropin"] / "99-stale-allow.conf"
    stale.write_text(
        "[Service]\nIPAddressAllow=198.51.100.10\n",
        encoding="utf-8",
        newline="\n",
    )
    note = plane["dropin"] / "30-admin-note.conf"
    note.write_text("# administrator note\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--rollback"],
        plane,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ROLLBACK_PROOF=PASS" in proc.stdout
    assert "ROLLBACK_APPLIED=off+deny-all" in proc.stdout
    assert _file_mode(plane) == "off"
    assert not (plane["dropin"] / "20-shadow-mode.conf").exists()
    assert not (plane["dropin"] / "10-provider-egress.conf").exists()
    assert not stale.exists()
    assert note.is_file()
    assert _show_mode(bash, plane) == "off"
    assert _show_allow(bash, plane) == ""
    assert plane["enablement"].read_text(encoding="utf-8").strip() == "disabled"
    assert plane["active"].read_text(encoding="utf-8").strip() == "inactive"
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "kept\n"


def test_activation_refuses_pass_when_mode_dropin_install_fails(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case D: a directory at the mode drop-in path cannot become a PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    (plane["dropin"] / "20-shadow-mode.conf").mkdir()
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        [_SHA, _NOT_BEFORE, _REVIEW_BY],
        plane,
    )
    _assert_proven_off(proc, plane, bash)


def test_base_unit_and_bootstrap_stay_fail_closed() -> None:
    """Case E, source half: the base unit, bootstrap, and install workflow stay off."""
    service = (COMMITTEE_DEPLOY / "opip-committee-shadow.service").read_text(encoding="utf-8")
    assert "Environment=OPIP_COMMITTEE_MODE=off" in service
    assert "IPAddressDeny=any" in service
    bootstrap = (COMMITTEE_DEPLOY / "bootstrap-opip-committee-worker.sh").read_text(
        encoding="utf-8"
    )
    assert "20-shadow-mode.conf" not in bootstrap
    assert "IPAddressAllow=" not in bootstrap
    install = INSTALL.read_text(encoding="utf-8")
    assert "OPIP_COMMITTEE_MODE=shadow" not in install
    activate = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(encoding="utf-8")
    verify = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(encoding="utf-8")
    assert "converge_to_safe_off" in activate
    assert "recovery_required" in activate
    assert "plane_requires_recovery" in activate
    assert "SAFE_OFF=PROVEN" in activate
    assert "SAFE_OFF=FAIL" in activate
    assert 'current_file_mode)" != "shadow"' not in activate
    assert "printf '%s\\n' '[Service]' 'Environment=OPIP_COMMITTEE_MODE=shadow'" in activate
    assert "ip_allow_policy.py" in verify
    assert "ip_allow_policy.py" in activate
    assert "s|/.*||" not in verify
    policy = (COMMITTEE_DEPLOY / "ip_allow_policy.py").read_text(encoding="utf-8")
    assert "ip_network" in policy
    assert "subnet_of" not in policy
    for script in (activate, verify):
        else_branch = script.split('== "1" ]]; then', 1)[1].split("else", 1)[1].split("fi", 1)[0]
        assert 'COMMITTEE_HOME="/var/lib/opip-committee"' in else_branch
        assert 'EVIDENCE_ROOT="/var/lib/opip-learning"' in else_branch
        assert "OPIP_COMMITTEE_HOME:-" not in else_branch
        assert "OPIP_COMMITTEE_EVIDENCE_ROOT:-" not in else_branch


def test_base_unit_stays_off_and_bootstrap_does_not_open_shadow(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case E, harness half: a merge of the base unit alone reports mode off."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    assert _file_mode(plane) == "off"
    assert list(plane["dropin"].glob("*.conf")) == []
    assert _show_mode(bash, plane) == "off"


def test_activation_reconciles_a_shadow_file_whose_unit_still_shows_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case F: re-activation installs the mode drop-in and leaves the timer disabled."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        [_SHA, _NOT_BEFORE, _REVIEW_BY],
        plane,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_ACTIVATION=PASS" in proc.stdout
    assert "SHADOW_PROOF=PASS" in proc.stdout
    mode_dropin = plane["dropin"] / "20-shadow-mode.conf"
    assert mode_dropin.is_file()
    mode_text = mode_dropin.read_text(encoding="utf-8")
    assert "Environment=OPIP_COMMITTEE_MODE=shadow" in mode_text
    assert "Environment=\n" not in mode_text
    assert not any(line.strip() == "Environment=" for line in mode_text.splitlines())
    shown = _show_environment(bash, plane)
    assert "OPIP_COMMITTEE_MODE=shadow" in shown
    assert "PYTHONDONTWRITEBYTECODE=1" in shown
    assert _show_mode(bash, plane) == "shadow"
    assert plane["enablement"].read_text(encoding="utf-8").strip() == "disabled"
    assert plane["active"].read_text(encoding="utf-8").strip() == "inactive"
    log = plane["log"].read_text(encoding="utf-8") if plane["log"].exists() else ""
    assert not any(line.startswith("enable") for line in log.splitlines())
    assert marker.is_file()
    assert marker.read_text(encoding="utf-8") == "kept\n"
    service = (plane["unit_dir"] / "opip-committee-shadow.service").read_text(encoding="utf-8")
    assert "Environment=OPIP_COMMITTEE_MODE=off" in service


def test_activation_does_not_leave_an_allowlist_when_unit_mode_stays_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """A drop-in that does not change show must not leave OFF with provider egress."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        [_SHA, _NOT_BEFORE, _REVIEW_BY],
        plane,
        extra={"OPIP_TEST_FORCE_UNIT_MODE": "off"},
    )
    _assert_proven_off(proc, plane, bash)


def test_unreachable_providers_return_the_mixed_state_to_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case H: reachability failure after writes must end at proven OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_TIMEOUT_FAIL": "1"},
    )
    _assert_proven_off(proc, plane, bash)
    assert marker.read_text(encoding="utf-8") == "kept\n"


def test_rollback_fails_when_effective_allowlist_remains(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case J: a stale allowlist that is still effective cannot produce ROLLBACK_PROOF=PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    _mode_dropin(plane["dropin"])
    stale = plane["dropin"] / "99-stale-provider-egress.conf"
    stale.write_text(
        "[Service]\nIPAddressAllow=198.51.100.10\n",
        encoding="utf-8",
        newline="\n",
    )
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--rollback"],
        plane,
        extra={"OPIP_TEST_FORCE_IP_ALLOW": "198.51.100.10"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 1
    assert "ROLLBACK_PROOF=FAIL" in proc.stdout
    assert "ROLLBACK_PROOF=PASS" not in proc.stdout
    assert "effective IPAddressAllow still has exceptions after rollback" in proc.stdout


def test_timer_disable_failure_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case K: a failed disable cannot be followed by SHADOW_ACTIVATION=PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    plane["enablement"].write_text("enabled\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_DISABLE_FAILS_ONCE": "1"},
    )
    _assert_proven_off(proc, plane, bash)


def test_timer_stop_failure_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case L: a failed stop cannot be followed by SHADOW_ACTIVATION=PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    plane["active"].write_text("active\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_STOP_FAILS_ONCE": "1"},
    )
    _assert_proven_off(proc, plane, bash)


def test_timer_enabled_runtime_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """enabled-runtime is still enabled and cannot pass activation."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    plane["enablement"].write_text("enabled-runtime\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_DISABLE_IGNORES_ONCE": "1"},
    )
    _assert_proven_off(proc, plane, bash)


def test_timer_remaining_enabled_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case M: a disable that leaves the timer enabled cannot pass activation."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    plane["enablement"].write_text("enabled\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_DISABLE_IGNORES_ONCE": "1"},
    )
    _assert_proven_off(proc, plane, bash)


def test_timer_remaining_active_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case N: a stop that leaves the timer active cannot pass activation."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    plane["active"].write_text("active\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_STOP_IGNORES_ONCE": "1"},
    )
    _assert_proven_off(proc, plane, bash)


def test_activation_is_idempotent(tmp_path: pathlib.Path, fork_bash: str) -> None:
    """Case S: a second corrected activation stays on proven SHADOW."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    script = COMMITTEE_DEPLOY / "activate-committee-shadow.sh"
    first = _run_script(bash, script, _activation(plane), plane)
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run_script(bash, script, _activation(plane), plane)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "SHADOW_ACTIVATION=PASS" in second.stdout
    assert "SHADOW_PROOF=PASS" in second.stdout
    assert "SAFE_OFF=PROVEN" not in second.stdout
    assert _file_mode(plane) == "shadow"
    assert _show_mode(bash, plane) == "shadow"
    assert plane["enablement"].read_text(encoding="utf-8").strip() == "disabled"
    assert plane["active"].read_text(encoding="utf-8").strip() == "inactive"


def test_rollback_is_idempotent(tmp_path: pathlib.Path, fork_bash: str) -> None:
    """Case T: rolling back an already-OFF plane still proves OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    script = COMMITTEE_DEPLOY / "verify-committee-shadow.sh"
    first = _run_script(bash, script, ["--rollback"], plane)
    assert first.returncode == 0, first.stdout + first.stderr
    second = _run_script(bash, script, ["--rollback"], plane)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "ROLLBACK_PROOF=PASS" in second.stdout
    assert _file_mode(plane) == "off"
    assert _show_mode(bash, plane) == "off"
    assert _show_allow(bash, plane) == ""
    marker = plane["advisory"] / "role_results.jsonl"
    assert marker.is_file()


def test_safe_off_does_not_claim_success_when_proof_fails(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case U: a cleanup that cannot prove deny-all never claims SAFE_OFF or activation PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_FORCE_DENY": "broken"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_ACTIVATION=PASS" not in combined
    assert "SAFE_OFF=PROVEN" not in proc.stdout
    assert "SAFE_OFF=FAIL" in proc.stderr


def test_harness_rejects_parent_relative_escape(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case Q: allowed-root/../etc is rejected before any write."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    env = _harness_env(plane)
    env["OPIP_COMMITTEE_UNIT_DIR"] = _bash_path(plane["unit_dir"]) + "/../etc"
    proc = subprocess.run(
        [bash, str(COMMITTEE_DEPLOY / "verify-committee-shadow.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 76
    assert "parent-relative" in proc.stderr
    assert "SHADOW_PROOF=PASS" not in proc.stdout
    assert "ROLLBACK_PROOF=PASS" not in proc.stdout


def test_harness_rejects_symlink_escape(tmp_path: pathlib.Path, fork_bash: str) -> None:
    """Case R: a symlink from the harness root to /etc is rejected."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    link = plane["root"] / "escape-etc"
    try:
        link.symlink_to("/etc", target_is_directory=True)
    except OSError:
        pytest.skip("this platform cannot create the /etc symlink")
    env = _harness_env(plane)
    env["OPIP_COMMITTEE_UNIT_DIR"] = link.as_posix()
    proc = subprocess.run(
        [bash, str(COMMITTEE_DEPLOY / "verify-committee-shadow.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 76
    assert "outside the harness root" in proc.stderr
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def _show_deny(bash: str, plane: dict[str, pathlib.Path]) -> str:
    proc = subprocess.run(
        [
            bash,
            "-c",
            "systemctl show -p IPAddressDeny --value opip-committee-shadow.service",
        ],
        capture_output=True,
        text=True,
        env=_harness_env(plane),
    )
    _skip_if_bash_cannot_fork(proc)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_mixed_dns_failure_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case V: provider resolution failure on the mixed host cannot leave it mixed."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_GETENT_FAIL": "1"},
    )
    _assert_proven_off(proc, plane, bash)
    assert marker.read_text(encoding="utf-8") == "kept\n"
    deny = _show_deny(bash, plane)
    assert deny == "any" or "0.0.0.0/0" in deny


def test_mixed_precondition_failure_returns_to_proven_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case W: a missing replica root on the mixed host still proves OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    shutil.rmtree(plane["root"] / "replica")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
    )
    combined = proc.stdout + proc.stderr
    assert "SHADOW_ACTIVATION=BLOCKED" in proc.stdout
    assert "SHADOW_ACTIVATION=PASS" not in combined
    _assert_proven_off(proc, plane, bash)
    assert marker.read_text(encoding="utf-8") == "kept\n"


def test_pristine_off_precondition_failure_does_not_mutate(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case X: the same preflight refusal on proven OFF writes nothing."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    shutil.rmtree(plane["root"] / "replica")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_ACTIVATION=BLOCKED" in proc.stdout
    assert "nothing was changed; the plane remains as it was" in proc.stderr
    assert "SHADOW_ACTIVATION=PASS" not in combined
    assert "SAFE_OFF=PROVEN" not in proc.stdout
    assert _file_mode(plane) == "off"
    assert _show_mode(bash, plane) == "off"
    assert list(plane["dropin"].glob("*.conf")) == []
    assert not (plane["dropin"] / "10-provider-egress.conf").exists()
    assert not (plane["dropin"] / "20-shadow-mode.conf").exists()


def test_mixed_early_failure_does_not_claim_off_when_proof_fails(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case Y: early cleanup that cannot prove deny-all never claims SAFE_OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(plane),
        plane,
        extra={"OPIP_TEST_GETENT_FAIL": "1", "OPIP_TEST_FORCE_DENY": "broken"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_ACTIVATION=PASS" not in combined
    assert "SAFE_OFF=PROVEN" not in proc.stdout
    assert "SAFE_OFF=FAIL" in proc.stderr


def test_malformed_arguments_do_not_clean_a_mixed_plane(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """A bad SHA is not a reconciliation attempt and must not return the plane to OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _egress(plane["dropin"])
    marker = plane["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        ["not-a-sha", _NOT_BEFORE, _REVIEW_BY],
        plane,
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode == 64
    assert "SAFE_OFF=PROVEN" not in proc.stdout
    assert "SAFE_OFF=FAIL" not in combined
    assert _file_mode(plane) == "shadow"
    assert (plane["dropin"] / "10-provider-egress.conf").is_file()
    assert marker.read_text(encoding="utf-8") == "kept\n"


def _load_ip_allow_policy():
    path = COMMITTEE_DEPLOY / "ip_allow_policy.py"
    spec = importlib.util.spec_from_file_location("ip_allow_policy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def test_ip_allow_policy_canonical_sets_keep_network_width() -> None:
    """Cases A–I at the comparison itself: host prefixes match, wider networks do not."""
    policy = _load_ip_allow_policy()
    assert policy.canonical_network("203.0.113.7") == "203.0.113.7/32"
    assert policy.canonical_network("203.0.113.7/32") == "203.0.113.7/32"
    assert policy.canonical_network("2001:db8::7") == "2001:db8::7/128"
    assert policy.canonical_network("2001:db8::7/128") == "2001:db8::7/128"
    assert (
        policy.canonical_network("2001:0db8:0000:0000:0000:0000:0000:0007")
        == "2001:db8::7/128"
    )
    assert policy.canonical_network("203.0.113.0/24") == "203.0.113.0/24"
    assert policy.canonical_network("203.0.113.0/24") != policy.canonical_network(
        "203.0.113.7"
    )
    assert policy.canonical_network("2001:db8::/64") != policy.canonical_network(
        "2001:db8::7"
    )
    with pytest.raises(ValueError):
        policy.canonical_network("not-an-ip")
    with pytest.raises(ValueError):
        policy.canonical_network("203.0.113.7/24")
    with pytest.raises(ValueError):
        policy.canonical_network("localhost")

    def compare(
        expected: list[str], installed: list[str], effective: list[str]
    ) -> tuple[int, str]:
        lines = ["EXPECTED", *expected, "INSTALLED", *installed, "EFFECTIVE", *effective]
        return policy.compare_groups(lines)

    assert compare(["203.0.113.7"], ["203.0.113.7"], ["203.0.113.7/32"])[0] == 0
    assert compare(["2001:db8::7"], ["2001:db8::7"], ["2001:db8::7/128"])[0] == 0
    assert (
        compare(
            ["2001:db8::7"],
            ["2001:db8::7"],
            ["2001:0db8:0000:0000:0000:0000:0000:0007/128"],
        )[0]
        == 0
    )
    status, message = compare(["203.0.113.7"], ["203.0.113.7"], ["203.0.113.0/24"])
    assert status == 1
    assert message.startswith("effective-mismatch")
    status, message = compare(["2001:db8::7"], ["2001:db8::7"], ["2001:db8::/64"])
    assert status == 1
    assert message.startswith("effective-mismatch")
    status, message = compare(
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7/32", "2001:db8::7/128", "198.51.100.9/32"],
    )
    assert status == 1
    assert "extra=1" in message
    status, message = compare(
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7/32"],
    )
    assert status == 1
    assert "missing=1" in message
    assert (
        compare(
            ["203.0.113.7", "2001:db8::7"],
            ["203.0.113.7", "203.0.113.7", "2001:db8::7"],
            ["203.0.113.7/32", "203.0.113.7", "2001:db8::7/128"],
        )[0]
        == 0
    )
    status, message = compare(
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7", "2001:db8::7"],
        ["203.0.113.7/32", "203.0.113.7/32"],
    )
    assert status == 1
    assert "missing=1" in message
    status, _message = compare(["203.0.113.7"], ["203.0.113.7"], ["not-an-ip"])
    assert status == 2


def _write_getent(plane: dict[str, pathlib.Path], mapping: dict[str, list[str]]) -> None:
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        'if [[ "${1:-}" != "ahosts" ]]; then exit 1; fi',
        'case "${2:-}" in',
    ]
    for host, addresses in mapping.items():
        lines.append(f"{host})")
        for address in addresses:
            lines.append(f"  printf '%s\\n' '{address} STREAM'")
        lines.append("  exit 0")
        lines.append("  ;;")
    lines.extend(["*) exit 1 ;;", "esac", ""])
    _write_exe(plane["bin"] / "getent", "\n".join(lines))


def _pin_allow(
    plane: dict[str, pathlib.Path],
    addresses: list[str],
    *,
    resolv: str,
    providers: dict[str, list[str]],
) -> None:
    _write_getent(plane, providers)
    plane["resolv"].write_text(resolv, encoding="utf-8", newline="\n")
    body = ["[Service]", *[f"IPAddressAllow={address}" for address in addresses], ""]
    (plane["dropin"] / "10-provider-egress.conf").write_text(
        "\n".join(body), encoding="utf-8", newline="\n"
    )
    _mode_dropin(plane["dropin"])


def _prove_shadow(
    bash: str,
    plane: dict[str, pathlib.Path],
    extra: dict[str, str] | None = None,
    *,
    expected_sha: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run SHADOW proof bound to the plane's release SHA unless told otherwise.

    An unbound proof cannot PASS, so the default binds to the SHA the plane
    fixture actually wrote, and a caller can bind a different expected SHA to
    exercise release drift.
    """
    args = ["--expected-sha", _SHA if expected_sha is None else expected_sha]
    return _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        args,
        plane,
        extra=extra,
    )


def test_shadow_proof_accepts_systemd_ipv4_host_prefix(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case A: bare IPv4 in the drop-in matches systemd's /32 show form."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7"],
        resolv="nameserver 203.0.113.7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    shown = _show_allow(bash, plane)
    assert shown.split() == ["203.0.113.7/32"]
    proc = _prove_shadow(bash, plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout
    assert "effective IPAddressAllow exactly matches the approved host set" in proc.stdout


def test_shadow_proof_accepts_systemd_ipv6_host_prefix(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case B: bare IPv6 in the drop-in matches systemd's /128 show form."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["2001:db8::7"],
            "api.anthropic.com": ["2001:db8::7"],
        },
    )
    assert _show_allow(bash, plane).split() == ["2001:db8::7/128"]
    proc = _prove_shadow(bash, plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout


def test_shadow_proof_accepts_equivalent_ipv6_spellings(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case C: expanded and compressed IPv6 are the same host prefix."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["2001:db8::7"],
            "api.anthropic.com": ["2001:db8::7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={
            "OPIP_TEST_EFFECTIVE_IP_ALLOW": "2001:0db8:0000:0000:0000:0000:0000:0007/128"
        },
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout


def test_shadow_proof_rejects_broader_ipv4_network(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case D: an effective /24 is not the approved IPv4 host."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7"],
        resolv="nameserver 203.0.113.7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "203.0.113.0/24"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_PROOF=PASS" not in combined
    assert "effective allowlist does not exactly match the approved host set" in proc.stdout
    assert "extra=1" in proc.stdout
    assert "missing=1" in proc.stdout


def test_shadow_proof_rejects_broader_ipv6_network(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case E: an effective /64 is not the approved IPv6 host."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["2001:db8::7"],
            "api.anthropic.com": ["2001:db8::7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "2001:db8::/64"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_PROOF=PASS" not in combined
    assert "effective allowlist does not exactly match the approved host set" in proc.stdout


def test_shadow_proof_rejects_an_extra_effective_host(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case F: one extra effective host fails even when every approved host is present."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7", "2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={
            "OPIP_TEST_EFFECTIVE_IP_ALLOW": "203.0.113.7/32 2001:db8::7/128 198.51.100.9/32"
        },
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_PROOF=PASS" not in combined
    assert "extra=1" in proc.stdout
    assert "missing=0" in proc.stdout


def test_shadow_proof_rejects_a_missing_effective_host(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case G: a missing effective host fails closed."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7", "2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "203.0.113.7/32"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_PROOF=PASS" not in combined
    assert "missing=1" in proc.stdout


def test_shadow_proof_duplicate_spellings_do_not_hide_a_missing_host(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case H: duplicate spellings collapse, so they cannot stand in for another host."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7", "2001:db8::7"],
        resolv="nameserver 2001:db8::7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    duplicate_only = _prove_shadow(
        bash,
        plane,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "203.0.113.7/32 203.0.113.7"},
    )
    assert duplicate_only.returncode != 0
    assert "SHADOW_PROOF=PASS" not in duplicate_only.stdout
    assert "missing=1" in duplicate_only.stdout
    complete = _prove_shadow(
        bash,
        plane,
        extra={
            "OPIP_TEST_EFFECTIVE_IP_ALLOW": "203.0.113.7/32 203.0.113.7 2001:db8::7/128"
        },
    )
    assert complete.returncode == 0, complete.stdout + complete.stderr
    assert "SHADOW_PROOF=PASS" in complete.stdout


def test_shadow_proof_rejects_an_unparseable_effective_token(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case I: an unparseable effective token fails closed and activation returns to proven OFF."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        ["203.0.113.7"],
        resolv="nameserver 203.0.113.7\n",
        providers={
            "api.openai.com": ["203.0.113.7"],
            "api.anthropic.com": ["203.0.113.7"],
        },
    )
    proc = _prove_shadow(
        bash,
        plane,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "not-an-ip"},
    )
    combined = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "SHADOW_PROOF=PASS" not in combined
    assert "unparseable token" in proc.stdout

    activation = _plane(tmp_path / "activation", mode="off")
    marker = activation["advisory"] / "role_results.jsonl"
    marker.write_text("kept\n", encoding="utf-8", newline="\n")
    activated = _run_script(
        bash,
        COMMITTEE_DEPLOY / "activate-committee-shadow.sh",
        _activation(activation),
        activation,
        extra={"OPIP_TEST_EFFECTIVE_IP_ALLOW": "not-an-ip"},
    )
    _assert_proven_off(activated, activation, bash)
    assert marker.read_text(encoding="utf-8") == "kept\n"


def test_shadow_proof_accepts_production_shaped_nine_entry_policy(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case J: bare provider, resolver, and loopback hosts match systemd /32 and /128 output."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    addresses = [
        "203.0.113.10",
        "203.0.113.11",
        "2001:db8::10",
        "203.0.113.20",
        "203.0.113.21",
        "2001:db8::20",
        "127.0.0.53",
        "127.0.0.1",
        "::1",
    ]
    _pin_allow(
        plane,
        addresses,
        resolv="nameserver 127.0.0.53\n",
        providers={
            "api.openai.com": ["203.0.113.10", "203.0.113.11", "2001:db8::10"],
            "api.anthropic.com": ["203.0.113.20", "203.0.113.21", "2001:db8::20"],
        },
    )
    shown = set(_show_allow(bash, plane).split())
    assert "203.0.113.10" not in shown
    assert shown == {
        "203.0.113.10/32",
        "203.0.113.11/32",
        "2001:db8::10/128",
        "203.0.113.20/32",
        "203.0.113.21/32",
        "2001:db8::20/128",
        "127.0.0.53/32",
        "127.0.0.1/32",
        "::1/128",
    }
    proc = _prove_shadow(bash, plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout
    assert "SHADOW_PROOF=FAIL" not in proc.stdout


# ===========================================================================
# Stable proof bundle (IC-045): the installed durable proof must be USABLE.
#
# The incident this section exists to prevent: the activation workflow installed
# only `verify-committee-shadow.sh` to the stable path. That helper resolves its
# canonicalizer as a SIBLING, so the installed artifact permanently failed
# `FAIL  IP allowlist canonicalizer is absent` while the same script run from the
# release tree passed. Activation reported PASS and the durable proof was unusable.
#
# The workflow steps are exercised by executing their real `run:` bodies from the
# committed YAML with a fake `ssh`, and their real `if:` expressions are evaluated
# against simulated step outputs. Nothing here contacts a host.
# ===========================================================================

#: Absolute destinations the stable bundle must install to.
STABLE_PROOF_PATH = "/usr/local/sbin/opip-committee-shadow-proof"
STABLE_POLICY_PATH = "/usr/local/sbin/ip_allow_policy.py"


def _control_steps(activation: dict) -> dict[str, dict]:
    return {
        step["id"]: step
        for step in activation["jobs"]["control"]["steps"]
        if step.get("id")
    }


def _step_run_body(activation: dict, step_id: str) -> str:
    return _control_steps(activation)[step_id]["run"]


def _step_if(activation: dict, step_id: str) -> str:
    return _control_steps(activation)[step_id].get("if", "")


def _evaluate_if(expression: str, outputs: dict[tuple[str, str], str]) -> bool:
    """Evaluate a GitHub `if:` expression over simulated step outputs.

    Supports the subset the control plane uses: ``steps.<id>.outputs.<key>``
    references compared with ``==`` or ``!=`` against a single-quoted literal,
    combined with ``&&`` and ``||``. An absent output is the empty string, which
    is how GitHub treats a skipped step's outputs.
    """
    text = " ".join(expression.split())

    def leaf(part: str) -> bool:
        match = re.fullmatch(
            r"\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*(==|!=)\s*'([^']*)'\s*",
            part,
        )
        assert match, f"unsupported gate expression: {part!r}"
        step_id, key, operator, literal = match.groups()
        observed = outputs.get((step_id, key), "")
        return (observed == literal) if operator == "==" else (observed != literal)

    def conjunction(part: str) -> bool:
        return all(leaf(item) for item in part.split("&&"))

    return any(conjunction(item) for item in text.split("||"))


#: A fake `ssh` that answers each remote command the control plane issues. It logs
#: every invocation, so a test can prove a command was NOT executed.
_FAKE_SSH = r"""#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >> "$FAKE_SSH_LOG"
command="${!#}"
case "$command" in
  *"sudo -n install "*) printf 'install output\n'; exit "${FAKE_INSTALL_RC:-0}" ;;
  *"echo READY"*) printf '%s\n' "${FAKE_BUNDLE_STATE:-READY}"; exit "${FAKE_BUNDLE_RC:-0}" ;;
  *activate-committee-shadow.sh*) printf '%s\n' "${FAKE_ACT_OUTPUT:-SHADOW_ACTIVATION=PASS}"; exit "${FAKE_ACT_RC:-0}" ;;
  *--rollback*) printf '%s\n' "${FAKE_SAFEOFF_OUTPUT:-ROLLBACK_PROOF=PASS}"; exit "${FAKE_SAFEOFF_RC:-0}" ;;
  *opip-committee-shadow-proof*) printf '%s\n' "${FAKE_STABLE_OUTPUT:-SHADOW_PROOF=PASS}"; exit "${FAKE_STABLE_RC:-0}" ;;
  *systemctl\ start*) printf 'canary started\n'; exit "${FAKE_START_RC:-0}" ;;
  *systemctl\ enable*) printf 'timer enabled\n'; exit "${FAKE_ENABLE_RC:-0}" ;;
  *journalctl*|*role_results.jsonl*|*--no-pager*) printf 'logged\n'; exit 0 ;;
  *verify-committee-shadow.sh*) printf '%s\n' "${FAKE_PREOP_OUTPUT:-SHADOW_PROOF=PASS}"; exit "${FAKE_PREOP_RC:-0}" ;;
  *) printf 'ok\n'; exit 0 ;;
esac
"""

#: The address set the proof's own expectation is derived from, for a loopback
#: stub resolver: the provider address, the configured resolver, and the loopback
#: addresses the deny-all default also denies. The installed drop-in has to equal
#: this set exactly, because comparison is equality, not containment.
_STUB_PLANE_ADDRESSES = ["203.0.113.10", "127.0.0.53", "127.0.0.1", "::1"]
_STUB_PLANE_PROVIDERS = {"api.openai.com": ["203.0.113.10"], "api.anthropic.com": []}
_STUB_PLANE_RESOLV = "nameserver 127.0.0.53\n"


def _step_harness(tmp_path: pathlib.Path, **scenario: str) -> dict[str, pathlib.Path]:
    bin_dir = tmp_path / "workflow-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_exe(bin_dir / "ssh", _FAKE_SSH)
    log = tmp_path / "ssh.log"
    log.write_text("", encoding="utf-8")
    outputs = tmp_path / "github_output.txt"
    outputs.write_text("", encoding="utf-8")
    return {"bin": bin_dir, "log": log, "outputs": outputs}


def _resolve_step_env_value(
    raw: str, command_outputs: dict[str, str]
) -> str:
    """Resolve one declared step `env:` value the way GitHub would.

    A `${{ steps.<id>.outputs.<key> }}` reference resolves from the simulated
    outputs; a `${{ secrets.* }}` reference resolves to a placeholder. Anything else
    is passed through literally.
    """
    match = re.fullmatch(r"\$\{\{\s*steps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)\s*\}\}", raw)
    if match:
        step_name, key = match.groups()
        return command_outputs.get(f"{step_name}.{key}", "")
    if re.fullmatch(r"\$\{\{\s*secrets\.[A-Za-z0-9_]+\s*\}\}", raw):
        return "harness-secret"
    return raw


def _step_declared_env(
    activation: dict, step_id: str, command_outputs: dict[str, str]
) -> dict[str, str]:
    declared = _control_steps(activation)[step_id].get("env") or {}
    return {
        key: _resolve_step_env_value(str(value), command_outputs)
        for key, value in declared.items()
    }


def _run_workflow_step(
    bash: str,
    tmp_path: pathlib.Path,
    activation: dict,
    step_id: str,
    *,
    command: str = "shadow",
    scenario: dict[str, str] | None = None,
    command_outputs: dict[str, str] | None = None,
) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
    """Execute one real workflow step body and return (proc, outputs, ssh log).

    Faithfulness matters here: the environment is built ONLY from the harness
    plumbing plus the keys the step actually declares, with `${{ … }}` references
    resolved. Injecting a variable the step does not declare would hide exactly the
    class of defect where a step body uses a variable the runner never provides.
    """
    harness = _step_harness(tmp_path)
    body = _step_run_body(activation, step_id)
    script = tmp_path / f"step-{step_id}.sh"
    script.write_text(body, encoding="utf-8", newline="\n")
    outputs_map = {"command.sha": _SHA}
    outputs_map.update(command_outputs or {})
    declared = _step_declared_env(activation, step_id, outputs_map)

    env = os.environ.copy()
    # Evict any ambient value for a runner-supplied name, so the step observes the
    # same emptiness the real runner would give it. Only declared names are added
    # back below: an unconditional injection would mask the defect class where a
    # step body uses a variable the runner never provides.
    for name in (
        "TARGET_SHA",
        "NOT_BEFORE",
        "REVIEW_BY",
        "COMMAND",
        "RESULTS",
        "HOST",
        "USER",
        "PORT",
        "GH_TOKEN",
        "SSH_KEY_B64",
        "KNOWN_HOSTS",
    ):
        env.pop(name, None)
    env.update(
        {
            "PATH": _bash_path(harness["bin"]) + os.pathsep + env.get("PATH", ""),
            "GITHUB_OUTPUT": _bash_path(harness["outputs"]),
            "FAKE_SSH_LOG": _bash_path(harness["log"]),
            "GITHUB_RUN_ID": "12345",
        }
    )
    # Harness plumbing the runner itself supplies, then the step's own declarations.
    env.update(declared)
    env.update(scenario or {})
    # Run in the temporary directory so the step's `tee` logs land there rather
    # than in the checkout.
    proc = _run_bash([bash, str(script)], cwd=tmp_path, env=env)
    parsed: dict[str, str] = {}
    for line in harness["outputs"].read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            parsed[key] = value
    ssh_log = harness["log"].read_text(encoding="utf-8")
    # The step bodies are meaningless if the harnessed environment was not applied:
    # without the prepended PATH the real `ssh` runs and every verdict is empty.
    # Fail loudly here instead of leaving a confusing assertion behind.
    if "ssh" in body and not ssh_log:
        pytest.fail(
            "the fake ssh was never used, so the step environment was not applied: "
            f"{(proc.stdout + proc.stderr)[:400]}"
        )
    assert "Could not resolve hostname" not in proc.stdout, proc.stdout
    return proc, parsed, ssh_log


def _installed_bundle(
    destination: pathlib.Path, *, with_policy: bool = True
) -> pathlib.Path:
    """Reproduce the installed stable layout, including the renamed helper."""
    destination.mkdir(parents=True, exist_ok=True)
    proof = destination / "opip-committee-shadow-proof"
    shutil.copyfile(COMMITTEE_DEPLOY / "verify-committee-shadow.sh", proof)
    proof.chmod(0o755)
    if with_policy:
        shutil.copyfile(
            COMMITTEE_DEPLOY / "ip_allow_policy.py",
            destination / "ip_allow_policy.py",
        )
    return proof


# ------------------------------------------- Case A: stable bundle completeness


def test_case_a_the_workflow_installs_both_stable_proof_artifacts(
    activation: dict,
) -> None:
    """Case A: installing only the script must fail, because the helper needs it."""
    body = _step_run_body(activation, "activate")
    assert STABLE_PROOF_PATH in body
    assert STABLE_POLICY_PATH in body
    # Both come from the exact authorized release tree, never inline logic.
    assert "RELEASE_COMMITTEE" in body
    assert "/verify-committee-shadow.sh" in body
    assert "/ip_allow_policy.py" in body
    assert "deploy/committee" in body
    # The reviewed permissions and ownership.
    assert "install -m 0755 -o root -g root" in body
    assert "install -m 0644 -o root -g root" in body
    # No second semantic implementation of canonicalization.
    assert "ipaddress" not in body
    assert "ip_network" not in body


# ------------------------------------------- Case B: stable path resolution


def test_case_b_the_installed_stable_helper_resolves_its_sibling_policy(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case B: the INSTALLED helper must be able to prove the plane itself."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    addresses = [
        "203.0.113.10",
        "203.0.113.11",
        "2001:db8::10",
        "203.0.113.20",
        "203.0.113.21",
        "2001:db8::20",
        "127.0.0.53",
        "127.0.0.1",
        "::1",
    ]
    _pin_allow(
        plane,
        addresses,
        resolv="nameserver 127.0.0.53\n",
        providers={
            "api.openai.com": ["203.0.113.10", "203.0.113.11", "2001:db8::10"],
            "api.anthropic.com": ["203.0.113.20", "203.0.113.21", "2001:db8::20"],
        },
    )
    # systemd prints a bare host as /32 or /128; the canonicalizer must accept that.
    installed = _installed_bundle(tmp_path / "usr-local-sbin")
    proc = _run_script(bash, installed, ["--expected-sha", _SHA], plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout
    assert "SHADOW_PROOF=FAIL" not in proc.stdout
    assert "canonicalizer" not in proc.stdout


# ------------------------------------------- Case C: missing stable policy


def test_case_c_the_installed_helper_fails_closed_without_the_policy(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case C: the exact incident — policy absent, proof must FAIL, not pass."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    installed = _installed_bundle(tmp_path / "usr-local-sbin", with_policy=False)
    assert not (installed.parent / "ip_allow_policy.py").exists()
    proc = _run_script(bash, installed, ["--expected-sha", _SHA], plane)
    assert proc.returncode != 0
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert "FAIL  IP allowlist canonicalizer is absent" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_case_c_the_release_tree_helper_still_proves_the_same_plane(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case C: the same plane passes from the release tree, which has both files."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_PROOF=PASS" in proc.stdout


# ------------------------------------------- Case D: install failure propagation


def test_case_d_a_failed_stable_install_cannot_report_activation(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case D: `set +e` must not let a failed install reach result=ACTIVATED."""
    proc, outputs, log = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={"FAKE_INSTALL_RC": "1"},
    )
    assert outputs.get("result") == "FAILED", (outputs, proc.stdout, proc.stderr)
    assert outputs.get("result") != "ACTIVATED"
    assert outputs.get("bundle") == "FAILED"
    assert outputs.get("safe_off") == "PROVEN"
    assert "STABLE_BUNDLE=FAILED" in proc.stdout
    assert "STABLE_PROOF=PROVEN" not in proc.stdout
    # The plane must not be left activated, and the durable proof is not claimed.
    # The install destination contains the stable path, so the check is on the
    # proof INVOCATION form rather than on a bare substring.
    assert f"bash '{STABLE_PROOF_PATH}'" not in log


def test_case_d_an_incomplete_bundle_cannot_report_activation(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case D: install returning 0 while the bundle is incomplete still fails."""
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={"FAKE_INSTALL_RC": "0", "FAKE_BUNDLE_STATE": "INCOMPLETE"},
    )
    assert outputs.get("result") == "FAILED", (outputs, proc.stdout)
    assert outputs.get("bundle") == "FAILED"
    assert outputs.get("safe_off") == "PROVEN"
    assert "observed=INCOMPLETE" in proc.stdout


def test_case_d_a_failed_stable_install_returns_the_plane_to_off(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case D: the rollback vehicle is the release tree, so SAFE-OFF is provable."""
    _proc, outputs, log = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={"FAKE_INSTALL_RC": "1"},
    )
    assert "--rollback" in log
    assert "deploy/committee/verify-committee-shadow.sh" in log
    assert outputs.get("safe_off") == "PROVEN"


def test_case_d_an_unprovable_safe_off_is_reported_not_hidden(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={
            "FAKE_INSTALL_RC": "1",
            "FAKE_SAFEOFF_OUTPUT": "ROLLBACK_PROOF=FAIL failures=2",
            "FAKE_SAFEOFF_RC": "1",
        },
    )
    assert outputs.get("safe_off") == "FAIL"
    assert outputs.get("result") == "FAILED"


# ------------------------------------------- Case E: stable proof verification


def test_case_e_an_unusable_installed_helper_cannot_report_activation(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case E: the incident itself — bundle installed yet unusable."""
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={
            "FAKE_STABLE_OUTPUT": "FAIL  IP allowlist canonicalizer is absent\nSHADOW_PROOF=FAIL failures=1",
            "FAKE_STABLE_RC": "1",
        },
    )
    assert outputs.get("result") == "FAILED", (outputs, proc.stdout)
    assert outputs.get("stable_proof") == "FAILED"
    assert outputs.get("safe_off") == "PROVEN"
    assert "STABLE_PROOF=FAILED" in proc.stdout


def test_case_e_a_missing_pass_marker_cannot_report_activation(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case E: exit 0 without `SHADOW_PROOF=PASS` is not proof."""
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "activate",
        scenario={"FAKE_STABLE_OUTPUT": "SHADOW_PROOF=FAIL failures=1", "FAKE_STABLE_RC": "0"},
    )
    assert outputs.get("result") == "FAILED", (outputs, proc.stdout)
    assert outputs.get("stable_proof") == "FAILED"


def test_case_e_a_proven_bundle_reports_activation(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case E: only a proven installed helper may claim activation."""
    proc, outputs, log = _run_workflow_step(
        fork_bash, tmp_path, activation, "activate"
    )
    assert outputs.get("result") == "ACTIVATED", (outputs, proc.stdout)
    assert outputs.get("bundle") == "READY"
    assert outputs.get("stable_proof") == "PROVEN"
    assert outputs.get("safe_off") == "NOT_ATTEMPTED"
    assert "STABLE_BUNDLE=READY" in proc.stdout
    assert "STABLE_PROOF=PROVEN" in proc.stdout
    assert "--rollback" not in log


def test_case_e_the_final_gate_requires_a_usable_durable_proof(
    activation: dict, activation_text: str
) -> None:
    """Case E: the workflow terminal gate pins every activation precondition."""
    for token in (
        'test "$STABLE_BUNDLE" = "READY"',
        'test "$STABLE_PROOF" = "PROVEN"',
        'test "$ACTIVATE_RESULT" = "ACTIVATED"',
        'test "$SHADOW_RESULT" = "PROVEN"',
    ):
        assert token in activation_text, token
    final_body = activation["jobs"]["control"]["steps"][-1]["run"]
    assert 'test "$STABLE_BUNDLE" = "READY"' in final_body
    assert 'test "$STABLE_PROOF" = "PROVEN"' in final_body


# ------------------------------------------- Cases F-I: pre-operation proof gate


def test_case_f_the_pre_operation_proof_runs_from_the_pinned_release_tree(
    activation: dict,
) -> None:
    """Case F: proof comes from the uploaded release tree, which has its sibling."""
    body = _step_run_body(activation, "pre_operation_shadow")
    assert "verify-committee-shadow.sh" in body
    assert "RELEASE_DIR" in body
    assert "steps.command.outputs.sha" in body
    assert "grep -q '^SHADOW_PROOF=PASS$'" in body
    assert "pre_operation_shadow=PROVEN" in body
    assert "pre_operation_shadow=FAILED" in body


def test_case_f_a_successful_pre_operation_proof_is_machine_readable(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    proc, outputs, _ = _run_workflow_step(
        fork_bash, tmp_path, activation, "pre_operation_shadow"
    )
    assert outputs.get("result") == "PROVEN", (outputs, proc.stdout)
    assert "pre_operation_shadow=PROVEN" in proc.stdout


def test_case_g_a_failed_pre_operation_proof_is_reported(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "pre_operation_shadow",
        scenario={"FAKE_PREOP_OUTPUT": "SHADOW_PROOF=FAIL failures=1", "FAKE_PREOP_RC": "1"},
    )
    assert outputs.get("result") == "FAILED", (outputs, proc.stdout)
    assert "pre_operation_shadow=FAILED" in proc.stdout


def test_case_g_the_canary_gate_requires_the_pre_operation_proof(
    activation: dict, activation_text: str
) -> None:
    """Case G: `systemctl start` must be unreachable without a proven plane."""
    gate = _step_if(activation, "canary")
    assert "steps.command.outputs.command == 'canary'" in gate
    assert "steps.pre_operation_shadow.outputs.result == 'PROVEN'" in gate
    assert _evaluate_if(
        gate, {("command", "command"): "canary", ("pre_operation_shadow", "result"): "PROVEN"}
    )
    assert not _evaluate_if(
        gate, {("command", "command"): "canary", ("pre_operation_shadow", "result"): "FAILED"}
    )
    # A skipped prerequisite step yields an empty output, which must not pass.
    assert not _evaluate_if(gate, {("command", "command"): "canary"})
    assert not _evaluate_if(gate, {("command", "command"): "timer"})
    assert 'test "$PRE_OPERATION_SHADOW" = "PROVEN"' in activation_text


def test_case_g_the_canary_body_starts_the_service_only_when_it_runs(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Case G: the gate is what prevents the start, so prove both directions."""
    gate = _step_if(activation, "canary")

    def run_if(proven: bool) -> str:
        scenario = {"FAKE_PREOP_OUTPUT": "SHADOW_PROOF=PASS"} if proven else {
            "FAKE_PREOP_OUTPUT": "SHADOW_PROOF=FAIL failures=1",
            "FAKE_PREOP_RC": "1",
        }
        _proc, pre_outputs, _log = _run_workflow_step(
            fork_bash,
            tmp_path / ("proven" if proven else "unproven"),
            activation,
            "pre_operation_shadow",
            scenario=scenario,
        )
        outputs = {("command", "command"): "canary", ("pre_operation_shadow", "result"): pre_outputs.get("result", "")}
        if not _evaluate_if(gate, outputs):
            return ""
        _p, _o, canary_log = _run_workflow_step(
            fork_bash, tmp_path / ("run" if proven else "skip"), activation, "canary"
        )
        return canary_log

    assert "systemctl start" in run_if(True)
    assert "systemctl start" not in run_if(False)


def test_case_h_the_timer_gate_requires_the_pre_operation_proof(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict, activation_text: str
) -> None:
    """Case H: the higher-authority boundary has the same gate."""
    bash = fork_bash
    gate = _step_if(activation, "timer")
    assert "steps.command.outputs.command == 'timer'" in gate
    assert "steps.pre_operation_shadow.outputs.result == 'PROVEN'" in gate
    assert _evaluate_if(
        gate, {("command", "command"): "timer", ("pre_operation_shadow", "result"): "PROVEN"}
    )
    assert not _evaluate_if(
        gate, {("command", "command"): "timer", ("pre_operation_shadow", "result"): "FAILED"}
    )
    assert not _evaluate_if(gate, {("command", "command"): "timer"})
    assert 'test "$PRE_OPERATION_SHADOW" = "PROVEN"' in activation_text

    def run_if(proven: bool) -> str:
        scenario = {"FAKE_PREOP_OUTPUT": "SHADOW_PROOF=PASS"} if proven else {
            "FAKE_PREOP_OUTPUT": "SHADOW_PROOF=FAIL failures=1",
            "FAKE_PREOP_RC": "1",
        }
        _proc, pre_outputs, _log = _run_workflow_step(
            bash,
            tmp_path / ("tproven" if proven else "tunproven"),
            activation,
            "pre_operation_shadow",
            scenario=scenario,
        )
        outputs = {
            ("command", "command"): "timer",
            ("pre_operation_shadow", "result"): pre_outputs.get("result", ""),
        }
        if not _evaluate_if(gate, outputs):
            return ""
        _p, _o, timer_log = _run_workflow_step(
            bash, tmp_path / ("tenable" if proven else "tblocked"), activation, "timer"
        )
        return timer_log

    assert "systemctl enable" in run_if(True)
    assert "systemctl enable" not in run_if(False)


def test_case_i_only_a_proven_plane_makes_the_timer_step_eligible(
    activation: dict,
) -> None:
    """Case I: workflow eligibility only; nothing here authorizes a real timer."""
    gate = _step_if(activation, "timer")
    assert _evaluate_if(
        gate,
        {
            ("command", "command"): "timer",
            ("pre_operation_shadow", "result"): "PROVEN",
        },
    )
    # The gate is not bypassable by the command alone.
    for other in ("shadow", "canary", "rollback", ""):
        assert not _evaluate_if(gate, {("command", "command"): other})


# ------------------------------------------- Case K: rollback stays usable


def test_case_k_rollback_is_not_gated_on_a_proof(activation: dict) -> None:
    """Case K: a safety action must never be blocked by a proof failure."""
    gate = _step_if(activation, "rollback")
    assert gate == "steps.command.outputs.command == 'rollback'"
    assert "pre_operation_shadow" not in gate
    assert _evaluate_if(
        gate,
        {
            ("command", "command"): "rollback",
            ("pre_operation_shadow", "result"): "FAILED",
        },
    )


def test_case_k_the_installed_helper_still_supports_rollback_without_the_policy(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case K: rollback never needs the canonicalizer, even when it is absent."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    installed = _installed_bundle(tmp_path / "usr-local-sbin", with_policy=False)
    proc = _run_script(bash, installed, ["--rollback"], plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ROLLBACK_PROOF=PASS" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout
    assert _file_mode(plane) == "off"


# ===========================================================================
# Abort convergence (IC-045 review finding).
#
# A command failure converges through the ERR trap, but a SIGNAL or an aborted
# transport does not raise ERR. Without an EXIT/signal trap, a run killed after
# the egress drop-in was written but before the mode/env write would leave the
# host carrying a provider `IPAddressAllow` entry while mode is still `off`, so
# the "OFF means deny-all" boundary would be briefly false.
# ===========================================================================

#: A fake `timeout` that blocks, giving the test a window to abort the run.
_SLOW_TIMEOUT = """#!/usr/bin/env bash
sleep "${OPIP_TEST_TIMEOUT_SLEEP:-30}"
exit 0
"""


def test_abort_traps_are_installed_and_the_success_line_clears_them() -> None:
    """The activation script converges on signals, and never on success."""
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert "activation_completed=0" in script
    assert "on_activation_exit" in script
    assert "trap on_activation_exit EXIT" in script
    for signal_name in ("TERM", "INT", "HUP"):
        assert f"trap 'exit 1" in script, signal_name
        assert signal_name in script, signal_name
    # The completion flag must be set before the success marker, so the trap can
    # distinguish a finished activation from an aborted one.
    completed = script.index("activation_completed=1")
    success = script.index("SHADOW_ACTIVATION=PASS release=")
    assert completed < success
    # And the trap must consult it.
    assert '"$activation_completed" -eq 0' in script


def test_an_aborted_activation_converges_to_off(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """Case: SIGTERM mid-activation must remove the egress drop-in and mode shadow."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    _pin_allow(
        plane,
        [],
        resolv="nameserver 127.0.0.53\n",
        providers={
            "api.openai.com": ["203.0.113.10"],
            "api.anthropic.com": ["203.0.113.20"],
        },
    )
    # Remove the drop-in the fixture created: activation must create it.
    (plane["dropin"] / "10-provider-egress.conf").unlink()
    (plane["dropin"] / "20-shadow-mode.conf").unlink()
    # Block inside the provider reachability probe, after the writes have begun.
    _write_exe(plane["bin"] / "timeout", _SLOW_TIMEOUT)
    _chmod_advisory(bash, plane)
    env = _harness_env(plane)
    env["OPIP_TEST_TIMEOUT_SLEEP"] = "30"

    script = COMMITTEE_DEPLOY / "activate-committee-shadow.sh"
    proc = subprocess.Popen(
        [bash, str(script), *_activation(plane)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        egress = plane["dropin"] / "10-provider-egress.conf"
        for _ in range(200):
            if egress.exists():
                break
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        if not egress.exists():
            proc.kill()
            proc.communicate()
            pytest.skip(
                "the activation run finished before the aborting window opened; "
                "no verdict was produced"
            )
        proc.terminate()
        stdout, stderr = proc.communicate(timeout=60)
    except Exception:  # noqa: BLE001 - never leave a stray child behind
        proc.kill()
        proc.communicate()
        raise

    combined = f"{stdout}{stderr}"
    # The abort converged rather than leaving a half-applied boundary.
    assert not egress.exists(), combined
    assert not (plane["dropin"] / "20-shadow-mode.conf").exists(), combined
    assert _file_mode(plane) == "off", combined
    assert "SHADOW_ACTIVATION=PASS" not in combined
    assert "converging to safe off: activation did not complete" in combined


def test_a_completed_activation_is_not_rolled_back_on_exit(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """The EXIT trap must not undo a successful activation."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="off")
    _pin_allow(
        plane,
        [],
        resolv="nameserver 127.0.0.53\n",
        providers={
            "api.openai.com": ["203.0.113.10"],
            "api.anthropic.com": ["203.0.113.20"],
        },
    )
    (plane["dropin"] / "10-provider-egress.conf").unlink()
    (plane["dropin"] / "20-shadow-mode.conf").unlink()
    proc = _run_script(bash, COMMITTEE_DEPLOY / "activate-committee-shadow.sh", _activation(plane), plane)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "SHADOW_ACTIVATION=PASS" in proc.stdout
    assert "converging to safe off" not in proc.stdout + proc.stderr
    # The activation survived its own exit.
    assert (plane["dropin"] / "10-provider-egress.conf").exists()
    assert (plane["dropin"] / "20-shadow-mode.conf").exists()
    assert _file_mode(plane) == "shadow"


# ===========================================================================
# Release-SHA binding (IC-046).
#
# The defect this section exists to prevent: SHADOW proof validated only that
# OPIP_COMMITTEE_RELEASE_SHA was a syntactically valid 40-character SHA. It never
# compared it against the SHA the requested operation was authorized for, so after
# main advanced an older worker could return SHADOW_PROOF=PASS and then permit a
# canary cycle or timer enablement that the receipt attributed to a newer target.
#
# Semantics reuse the learning plane's contract exactly
# (`app/opip/learning/job_disposition.py`, `deploy/learning/opip-learning-job.sh`):
# both full lowercase 40-character SHAs and equal -> CURRENT; both valid and
# unequal -> RELEASE_DRIFT; anything missing, malformed, or unverifiable ->
# UNVERIFIED. Only CURRENT may pass.
# ===========================================================================

#: A second, valid, different SHA, used to model a drifted worker.
_DRIFT_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _release_status(proc: subprocess.CompletedProcess[str]) -> str:
    for line in proc.stdout.splitlines():
        if line.startswith("release_compatibility_status="):
            return line.split("=", 1)[1].split()[0]
    return ""


def test_l1_current_release_binding_passes(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L1: observed == expected -> CURRENT, and the proof passes."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane, expected_sha=_SHA)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _release_status(proc) == "CURRENT"
    assert "SHADOW_PROOF=PASS" in proc.stdout


def test_l2_release_drift_fails_closed(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L2: both valid but different -> RELEASE_DRIFT and SHADOW_PROOF=FAIL."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane, expected_sha=_DRIFT_SHA)
    assert proc.returncode != 0
    assert _release_status(proc) == "RELEASE_DRIFT"
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout


@pytest.mark.parametrize(
    "observed",
    ["", "CHANGEME", "not-a-sha", _SHA[:12], _SHA.upper(), "main", "HEAD"],
)
def test_l3_unverifiable_observed_release_fails_closed(
    tmp_path: pathlib.Path, fork_bash: str, observed: str
) -> None:
    """L3: absent/short/malformed/uppercase observed -> UNVERIFIED, never PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    _set_release_sha(plane, observed)
    proc = _prove_shadow(bash, plane, expected_sha=_SHA)
    assert proc.returncode != 0
    assert _release_status(proc) == "UNVERIFIED"
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout


@pytest.mark.parametrize(
    "expected",
    ["not-a-sha", _SHA[:12], _SHA.upper(), "main", "refs/heads/main", " "],
)
def test_l4_unverifiable_expected_release_fails_closed(
    tmp_path: pathlib.Path, fork_bash: str, expected: str
) -> None:
    """L4: a malformed expected SHA must never normalize into CURRENT."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane, expected_sha=expected)
    assert proc.returncode != 0
    assert _release_status(proc) == "UNVERIFIED"
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_l4_an_empty_expected_value_is_refused_as_a_usage_error(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L4: an empty binding is a usage error, not a silent unbound proof."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane, expected_sha="")
    assert proc.returncode == 64
    assert "usage" in (proc.stdout + proc.stderr)
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_l5_an_unbound_shadow_proof_cannot_pass(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L5: no expected binding -> UNVERIFIED, FAIL, and a distinct usage exit.

    A SHADOW proof without its authority binding is a usage error (exit 64), which
    is reported distinctly from a genuine release mismatch (exit 1) so an operator
    can tell "I omitted the binding" from "the host is on the wrong release". The
    diagnostics still run, so the plane's state remains visible.
    """
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _run_script(
        bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", [], plane
    )
    assert proc.returncode == 64, proc.stdout + proc.stderr
    assert _release_status(proc) == "UNVERIFIED"
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout
    assert "--expected-sha" in (proc.stdout + proc.stderr)
    # Diagnostics still ran: an operator still learns why the plane is unsuitable.
    assert "mode is shadow in the environment file" in proc.stdout
    assert "PASS  " in proc.stdout


def test_l5_a_genuine_release_mismatch_keeps_the_proof_failure_exit(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L5: a real mismatch exits 1, so the two failure modes stay distinguishable."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _prove_shadow(bash, plane, expected_sha=_DRIFT_SHA)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert _release_status(proc) == "RELEASE_DRIFT"


def test_l5_a_missing_expected_sha_argument_value_is_refused(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--expected-sha"],
        plane,
    )
    assert proc.returncode == 64
    assert "usage" in (proc.stdout + proc.stderr)


def test_the_committee_classifier_matches_the_shell_learning_runner() -> None:
    """The Committee mirrors the SHELL runner, which is what actually gates work.

    The Python helper (`app/opip/learning/job_disposition.py`) lowercases before
    validating, so it reports CURRENT for an uppercase 40-hex value. The shell
    runner does not, and neither does the Committee. For an authority-binding
    equality check, normalizing case widens the accepted identity set, so the
    stricter behaviour is the correct one. This test states the divergence
    explicitly rather than asserting an agreement that does not exist.
    """
    from app.opip.learning.job_disposition import (
        RELEASE_CURRENT,
        RELEASE_DRIFT,
        RELEASE_UNVERIFIED,
        classify_release_compatibility,
    )

    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    for token in (RELEASE_CURRENT, RELEASE_DRIFT, RELEASE_UNVERIFIED):
        assert token in script, token
    # The shell classifier must not lowercase, or a non-canonical value could pass.
    assert ".lower()" not in script
    assert "tolower" not in script

    # Agreement on every canonical case.
    assert classify_release_compatibility(_SHA, _SHA) == RELEASE_CURRENT
    assert classify_release_compatibility(_SHA, _DRIFT_SHA) == RELEASE_DRIFT
    assert classify_release_compatibility(_SHA, "") == RELEASE_UNVERIFIED
    assert classify_release_compatibility("main", _SHA) == RELEASE_UNVERIFIED

    # Documented divergence: the Python helper normalizes case, the Committee
    # (like the shell runner) does not. L3/L4 prove the shell behaviour
    # executably; this asserts the Python side so the divergence cannot drift
    # silently in either direction.
    assert classify_release_compatibility(_SHA.upper(), _SHA) == RELEASE_CURRENT
    shell_runner = (
        REPO_ROOT / "OHM-Trade-Agent-v1" / "deploy" / "learning" / "opip-learning-job.sh"
    ).read_text(encoding="utf-8")
    shell_classifier = shell_runner.split("classify_release_compatibility()")[1].split(
        "\n}"
    )[0]
    # The shell runner compares case-sensitively, which is why the Committee does.
    assert "lower" not in shell_classifier


# ------------------------------------------------ binding of each proof call site


def test_l8_activation_internal_proof_is_bound_to_its_target(
    activation_text: str,
) -> None:
    """L8: activation proves the exact SHA it was authorized to activate."""
    script = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    assert 'bash "$proof_script" --expected-sha "$TARGET_SHA"' in script
    # An unbound internal proof would let activation report PASS on any valid SHA.
    assert 'if ! bash "$proof_script"; then' not in script


def test_l9_stable_installed_proof_is_bound_to_its_target(
    activation: dict,
) -> None:
    """L9: the installed durable helper is invoked bound to the activation target."""
    body = _step_run_body(activation, "activate")
    # The DURABLE installed helper is invoked, bound to the activation target. This
    # is what makes both guarantees hold at once: the installed artifact is proven
    # usable, and it is proven for the exact SHA that was authorized.
    assert "bash '$STABLE_PROOF' --expected-sha '$TARGET_SHA'" in body
    # Every SHADOW proof invocation in the activate step carries the binding.
    # Rollback is the deliberate exception: it is a safety action and must not
    # depend on the release identity it exists to remediate. Install lines name
    # the same file as a source and are not proof invocations.
    proof_calls = [
        line
        for line in body.splitlines()
        if "sudo -n bash" in line
        and ("$STABLE_PROOF'" in line or "verify-committee-shadow.sh'" in line)
        and "--rollback" not in line
    ]
    assert proof_calls, body
    for line in proof_calls:
        assert "--expected-sha '$TARGET_SHA'" in line, line
    # And the one deliberately unbound call is the rollback vehicle.
    rollback_calls = [
        line
        for line in body.splitlines()
        if "sudo -n bash" in line and "--rollback" in line
    ]
    assert rollback_calls, body
    for line in rollback_calls:
        assert "--expected-sha" not in line, line


def test_b_workflow_pre_operation_proof_is_bound_to_the_target(
    activation: dict,
) -> None:
    body = _step_run_body(activation, "pre_operation_shadow")
    assert "verify-committee-shadow.sh' --expected-sha '$TARGET_SHA'" in body


def test_d_post_activation_proof_step_is_bound_to_the_target(
    activation: dict,
) -> None:
    body = _step_run_body(activation, "shadow_proof")
    assert "verify-committee-shadow.sh' --expected-sha '$TARGET_SHA'" in body
    steps = _control_steps(activation)
    assert "TARGET_SHA" in steps["shadow_proof"]["env"]


def test_e_rollback_call_sites_carry_no_release_binding(activation: dict) -> None:
    """E: a safety action must not depend on the condition it remediates."""
    body = _step_run_body(activation, "rollback")
    assert "--rollback" in body
    assert "--expected-sha" not in body
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    rollback = script.split('if [[ "$ROLLBACK_MODE" -eq 1 ]]; then')[1].split(
        "# ------------------------------------------------------------- SHADOW-mode only"
    )[0]
    assert "--expected-sha" not in rollback
    assert "RELEASE_DRIFT" not in rollback


def test_no_production_shadow_proof_path_is_left_unbound() -> None:
    """Every SHADOW proof call must bind a SHA; rollback is the only exception."""
    workflow = ACTIVATION.read_text(encoding="utf-8")
    unbound: list[str] = []
    for line in workflow.splitlines():
        # Match an actual invocation of the proof script, not a comment or a path.
        if " -n bash " not in line or "verify-committee-shadow.sh'" not in line:
            continue
        if "--rollback" in line:
            continue
        if "--expected-sha '$TARGET_SHA'" not in line:
            unbound.append(line.strip())
    assert unbound == [], unbound
    # The activation script's own internal proof is bound too.
    activate = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    for line in activate.splitlines():
        if "proof_script" not in line or "--expected-sha" not in line:
            continue
        assert '"$TARGET_SHA"' in line, line
    assert 'bash "$proof_script" --expected-sha "$TARGET_SHA"' in activate


# ------------------------------------------------ drift blocks the operations


def test_l6_release_drift_blocks_the_canary_service_start(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """L6: workflow target B against host release A must not start the service."""
    bash = fork_bash
    gate = _step_if(activation, "canary")
    # The pre-operation proof for target B against a plane whose release is A.
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proof = _prove_shadow(bash, plane, expected_sha=_DRIFT_SHA)
    assert proof.returncode != 0
    outputs = {
        ("command", "command"): "canary",
        ("pre_operation_shadow", "result"): "FAILED",
    }
    assert not _evaluate_if(gate, outputs)
    # And the drift evidence itself is machine-readable and fail-closed.
    assert "release_compatibility_status=RELEASE_DRIFT" in proof.stdout
    assert "SHADOW_PROOF=FAIL" in proof.stdout


def test_l7_release_drift_blocks_the_timer_enable(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """L7: the higher-authority boundary is blocked by drift the same way."""
    bash = fork_bash
    gate = _step_if(activation, "timer")
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proof = _prove_shadow(bash, plane, expected_sha=_DRIFT_SHA)
    assert proof.returncode != 0
    outputs = {
        ("command", "command"): "timer",
        ("pre_operation_shadow", "result"): "FAILED",
    }
    assert not _evaluate_if(gate, outputs)


def test_l10_rollback_remains_release_independent(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """L10: rollback proves OFF even with a drifted or malformed release."""
    bash = fork_bash
    for observed in (_DRIFT_SHA, "CHANGEME", "main", ""):
        plane = _plane(tmp_path / observed.replace("/", "_") or "empty", mode="shadow")
        _pin_allow(
            plane,
            _STUB_PLANE_ADDRESSES,
            resolv=_STUB_PLANE_RESOLV,
            providers=_STUB_PLANE_PROVIDERS,
        )
        _set_release_sha(plane, observed)
        proc = _run_script(
            bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", ["--rollback"], plane
        )
        assert proc.returncode == 0, (observed, proc.stdout + proc.stderr)
        assert "ROLLBACK_PROOF=PASS" in proc.stdout
        assert _file_mode(plane) == "off"


def test_t1_the_installed_durable_helper_rejects_release_drift(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """T1: the DURABLE artifact is proven bound, not just the release-tree copy.

    String-level assertions on the workflow cannot show that the installed helper
    itself enforces the binding, and the installed copy is the artifact an operator
    can run on the host. This binds the installed helper to a different SHA and
    requires it to fail closed.
    """
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    installed = _installed_bundle(tmp_path / "usr-local-sbin")
    proc = _run_script(bash, installed, ["--expected-sha", _DRIFT_SHA], plane)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert _release_status(proc) == "RELEASE_DRIFT"
    assert "SHADOW_PROOF=FAIL" in proc.stdout
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_t1_the_installed_durable_helper_rejects_an_unbound_proof(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """T1: an operator running the installed helper by hand cannot get a false PASS."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    installed = _installed_bundle(tmp_path / "usr-local-sbin")
    proc = _run_script(bash, installed, [], plane)
    assert proc.returncode != 0
    assert _release_status(proc) == "UNVERIFIED"
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_f1_a_duplicate_expected_sha_is_refused(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """F1: two bindings are ambiguous and must not resolve last-wins."""
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--expected-sha", _SHA, "--expected-sha", _DRIFT_SHA],
        plane,
    )
    assert proc.returncode == 64
    assert "more than once" in (proc.stdout + proc.stderr)
    assert "SHADOW_PROOF=PASS" not in proc.stdout


def test_f1_an_unknown_argument_is_rejected(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--not-a-flag"],
        plane,
    )
    assert proc.returncode == 64
    assert "usage" in (proc.stdout + proc.stderr)


def test_f2_rollback_is_not_obstructed_by_the_binding_arguments(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """F2: no release-flag shape may prevent the safety action from running."""
    bash = fork_bash
    cases = (
        ["--rollback"],
        ["--rollback", "--expected-sha"],
        ["--rollback", "--expected-sha", _DRIFT_SHA],
        ["--rollback", "--expected-sha", "not-a-sha"],
        ["--rollback", "--not-a-flag"],
        ["--expected-sha", _DRIFT_SHA, "--rollback"],
    )
    for index, args in enumerate(cases):
        plane = _plane(tmp_path / f"rollback-case-{index}", mode="shadow")
        _pin_allow(
            plane,
            _STUB_PLANE_ADDRESSES,
            resolv=_STUB_PLANE_RESOLV,
            providers=_STUB_PLANE_PROVIDERS,
        )
        proc = _run_script(
            bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", args, plane
        )
        assert proc.returncode == 0, (args, proc.stdout + proc.stderr)
        assert "ROLLBACK_PROOF=PASS" in proc.stdout, args
        assert _file_mode(plane) == "off", args


def test_f2_a_rollback_token_in_a_value_position_is_not_a_rollback(
    tmp_path: pathlib.Path, fork_bash: str
) -> None:
    """F2: `--expected-sha --rollback` must NOT silently become a rollback.

    The value of `--expected-sha` is consumed before the rollback scan, so a
    malformed proof invocation cannot turn into a state-changing rollback. This is
    asserted behaviourally: the plane must be untouched, not returned to OFF.
    """
    bash = fork_bash
    plane = _plane(tmp_path, mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _run_script(
        bash,
        COMMITTEE_DEPLOY / "verify-committee-shadow.sh",
        ["--expected-sha", "--rollback"],
        plane,
    )
    assert "ROLLBACK_PROOF=PASS" not in proc.stdout
    assert "ROLLBACK_APPLIED" not in proc.stdout
    assert proc.returncode != 0
    # The plane was not mutated: still shadow, drop-ins intact.
    assert _file_mode(plane) == "shadow"
    assert (plane["dropin"] / "20-shadow-mode.conf").exists()


@pytest.mark.parametrize("token", ["--rollbackx", "x--rollback", "--ROLLBACK", "-rollback"])
def test_f2_a_similar_token_is_not_treated_as_rollback(
    tmp_path: pathlib.Path, fork_bash: str, token: str
) -> None:
    bash = fork_bash
    plane = _plane(tmp_path / token.replace("-", "d"), mode="shadow")
    _pin_allow(
        plane,
        _STUB_PLANE_ADDRESSES,
        resolv=_STUB_PLANE_RESOLV,
        providers=_STUB_PLANE_PROVIDERS,
    )
    proc = _run_script(
        bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", [token], plane
    )
    assert proc.returncode == 64, (token, proc.stdout + proc.stderr)
    assert "usage" in (proc.stdout + proc.stderr)
    assert "ROLLBACK_PROOF=PASS" not in proc.stdout


def test_l11_prior_a_to_k_protections_are_present_and_behavioral() -> None:
    """L11: the earlier protections survive as executable guarantees.

    Each marker this asserts is paired with a behavioral case elsewhere in this
    module, so this test documents the contract rather than replacing it.
    """
    script = (COMMITTEE_DEPLOY / "verify-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    activate = (COMMITTEE_DEPLOY / "activate-committee-shadow.sh").read_text(
        encoding="utf-8"
    )
    # Exact-set allowlist comparison, still delegated to one implementation.
    assert "ip_allow_policy.py" in script
    assert "canonicalizer is absent" in script
    # Install failure and unusable durable proof both converge to safe off.
    assert "converge_to_safe_off" in activate
    assert "SAFE_OFF=PROVEN" in activate
    assert "SAFE_OFF=FAIL" in activate
    # Activation converges on abort as well as on command failure.
    assert "trap on_activation_exit EXIT" in activate
    assert "activation_completed=1" in activate
    # Rollback stays non-destructive to advisory evidence.
    assert "advisory evidence directory survived rollback" in script


# ===========================================================================
# Step environment declaration (IC-046 verification finding).
#
# The defect this section exists to prevent: the `pre_operation_shadow` step body
# referenced `$TARGET_SHA` but did not declare it in `env:`. GitHub therefore
# expanded it to empty, the proof received `--expected-sha ''`, the parser refused
# it with exit 64, and the pre-operation proof was ALWAYS `FAILED` - permanently
# blocking `/committee-canary` and `/committee-timer`.
#
# The earlier harness hid this because it injected TARGET_SHA into every step
# regardless of what the step declared. The runner now injects only declared keys,
# and the guard below makes the class of defect fail loudly.
# ===========================================================================

#: Runner-supplied names a step body may consume. A body that references one of
#: these must declare it, or it silently observes an empty string. The set is
#: deliberately broader than the names currently in use, so a future body that
#: reaches for one of them without declaring it fails the guard rather than
#: shipping.
STEP_SUPPLIED_NAMES = (
    "TARGET_SHA",
    "NOT_BEFORE",
    "REVIEW_BY",
    "COMMAND",
    "RESULTS",
    "HOST",
    "USER",
    "PORT",
    "GH_TOKEN",
    "SSH_KEY_B64",
    "KNOWN_HOSTS",
    "INSTALL_RESULT",
    "INSTALL_RC",
    "ISOLATION_RESULT",
    "ISOLATION_RC",
    "CLEANUP_RESULT",
    "CLEANUP_RC",
    "PRE_OPERATION_SHADOW",
    "STABLE_BUNDLE",
    "STABLE_PROOF",
    "SAFE_OFF",
    "ACTIVATE_RESULT",
    "SHADOW_RESULT",
    "CANARY_RESULT",
    "TIMER_RESULT",
    "ROLLBACK_RESULT",
)

#: Names the runner always provides, whatever a step declares. Referencing one of
#: these without declaring it is fine.
RUNNER_PLUMBING_NAMES = ("GITHUB_OUTPUT", "GITHUB_RUN_ID", "GITHUB_REPOSITORY")

#: Matches `$NAME`, `${NAME}`, `${NAME:-default}`, `${NAME:?message}` and
#: `${NAME:=default}`, so a defaulted reference cannot slip past the guard.
_REFERENCE_PATTERN = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")


def test_c11_every_step_supplied_variable_is_declared_by_its_step(
    activation: dict, install_workflow: dict
) -> None:
    """A step must declare every runner-supplied variable its body references.

    GitHub expands an undeclared `$NAME` to empty, so a missing declaration is a
    functional defect, not a cosmetic one. Both workflows are checked, and the scan
    covers `${NAME:-default}`-style references and names assigned inside the body.
    """
    missing: list[str] = []
    for workflow, document in (("activation", activation), ("install", install_workflow)):
        for job in document["jobs"].values():
            for step in job.get("steps", []):
                body = step.get("run") or ""
                if not body:
                    continue
                step_name = step.get("id") or step.get("name") or "<unnamed>"
                declared = set((step.get("env") or {}).keys())
                assigned = set(re.findall(r"^\s*(?:local\s+)?([A-Za-z_][A-Za-z0-9_]*)=", body, re.M))
                assigned |= set(re.findall(r"read\s+-r\s+-a\s+([A-Za-z_][A-Za-z0-9_]*)", body))
                for name in sorted(set(_REFERENCE_PATTERN.findall(body))):
                    if name in declared or name in assigned or name in RUNNER_PLUMBING_NAMES:
                        continue
                    if name not in STEP_SUPPLIED_NAMES:
                        continue
                    missing.append(
                        f"{workflow}/{step_name}: references ${name} but does not declare it"
                    )
    assert missing == [], missing


def test_c11_the_pre_operation_step_declares_its_bound_target(activation: dict) -> None:
    """The pre-operation proof binds TARGET_SHA, so it must declare it."""
    steps = _control_steps(activation)
    declared = steps["pre_operation_shadow"].get("env") or {}
    assert "TARGET_SHA" in declared, declared
    assert "steps.command.outputs.sha" in str(declared["TARGET_SHA"])
    # And the body still binds it.
    body = _step_run_body(activation, "pre_operation_shadow")
    assert "--expected-sha '$TARGET_SHA'" in body


def test_c11_the_pre_operation_step_is_provable_with_faithful_env(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """Behavioural: with only the declared env, the pre-operation proof PROVES.

    This is the case the defect broke. TARGET_SHA resolves through the step's own
    declaration, so the proof is bound and succeeds.
    """
    proc, outputs, _ = _run_workflow_step(
        fork_bash,
        tmp_path,
        activation,
        "pre_operation_shadow",
        command_outputs={"command.sha": _SHA},
    )
    assert outputs.get("result") == "PROVEN", (outputs, proc.stdout, proc.stderr)
    assert "pre_operation_shadow=PROVEN" in proc.stdout


def test_c11_an_undeclared_target_would_corrupt_the_remote_command(
    tmp_path: pathlib.Path, fork_bash: str, activation: dict
) -> None:
    """The defect's mechanism, pinned on the REMOTE COMMAND rather than a verdict.

    An undeclared `$TARGET_SHA` expands to empty, so the proof is invoked as
    `--expected-sha ''`. That is the corruption the defect caused. The fake ssh
    echoes its arguments into the log, so the command line itself is the evidence -
    asserting on a verdict instead would only exercise the stub's canned reply.
    """
    # Declared and resolved: the binding carries the requested SHA.
    _proc, _outputs, good_log = _run_workflow_step(
        fork_bash,
        tmp_path / "bound",
        activation,
        "pre_operation_shadow",
        command_outputs={"command.sha": _SHA},
    )
    assert f"--expected-sha '{_SHA}'" in good_log, good_log
    assert "--expected-sha ''" not in good_log, good_log

    # Undeclared/empty: the binding is empty, which the parser refuses with exit 64.
    _proc2, _outputs2, bad_log = _run_workflow_step(
        fork_bash,
        tmp_path / "unbound",
        activation,
        "pre_operation_shadow",
        command_outputs={"command.sha": ""},
    )
    assert "--expected-sha ''" in bad_log, bad_log
