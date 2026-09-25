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

import os
import pathlib
import shutil
import stat
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
  if [[ -s "$state" ]]; then
    paste -sd ' ' "$state"
  fi
  rm -f "$state"
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
        printf '%s\n' "$(merge_allows)"
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
    proc = _run_script(bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", [], plane)
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
    proc = _run_script(bash, COMMITTEE_DEPLOY / "verify-committee-shadow.sh", [], plane)
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
    assert "SAFE_OFF=PROVEN" in activate
    assert "SAFE_OFF=FAIL" in activate
    assert 'current_file_mode)" != "shadow"' not in activate
    assert "printf '%s\\n' '[Service]' 'Environment=OPIP_COMMITTEE_MODE=shadow'" in activate
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
