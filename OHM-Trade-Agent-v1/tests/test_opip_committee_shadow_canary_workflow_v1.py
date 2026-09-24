"""Deployment contract for the credentialled Committee SHADOW canary."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "OHM-Trade-Agent-v1" / "deploy" / "committee"
WORKFLOW = ROOT / ".github" / "workflows" / "validate-committee-shadow.yml"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_canary_workflow_is_owner_gated_exact_main_and_protected():
    text = _text(WORKFLOW)
    assert "github.event.issue.number == 64" in text
    assert "github.event.comment.author_association == 'OWNER'" in text
    assert "/validate-committee-shadow " in text
    assert "environment: committee-shadow" in text
    assert "target is not current main" in text
    assert "pytest.yml quality-security.yml" in text
    assert "persist-credentials: false" in text


def test_canary_workflow_never_enables_the_recurring_timer_or_shadow_worker():
    text = _text(WORKFLOW)
    for forbidden in (
        "enable --now opip-committee-shadow.timer",
        "start opip-committee-shadow.timer",
        "OPIP_COMMITTEE_MODE=shadow",
        "/etc/opip/committee-credentials.env",
    ):
        assert forbidden not in text
    assert "POST_CANARY_OFF_ISOLATION=PASS" in text


def test_provider_secrets_are_transient_encoded_and_never_rendered():
    text = _text(WORKFLOW)
    assert "secrets.OPIP_COMMITTEE_OPENAI_API_KEY" in text
    assert "secrets.OPIP_COMMITTEE_ANTHROPIC_API_KEY" in text
    assert "OPIP_COMMITTEE_OPENAI_API_KEY_B64" in text
    assert "OPIP_COMMITTEE_ANTHROPIC_API_KEY_B64" in text
    assert "/run/opip/committee-credential-canary.env" in text
    assert "cat committee-credential-canary.env" not in text
    assert "set -x" not in text
    assert "Transient cleanup" in text


def test_canary_unit_is_fail_closed_and_has_no_listener():
    unit = _text(DEPLOY / "opip-committee-credential-canary.service")
    assert "Type=oneshot" in unit
    assert "IPAddressDeny=any" in unit
    assert "IPAddressAllow=" not in unit
    assert "Environment=OPIP_COMMITTEE_MODE=off" in unit
    assert "EnvironmentFile=/run/opip/committee-credential-canary.env" in unit
    assert (
        "BindReadOnlyPaths=/run/opip/committee-credential-canary-hosts:/etc/hosts"
        in unit
    )
    assert "ReadWritePaths=/var/lib/opip-committee/credential-canary /var/lock" in unit
    assert "PrivateTmp=true" in unit
    assert "ListenStream" not in unit
    assert "ListenDatagram" not in unit


def test_validation_adds_egress_only_to_the_transient_canary_dropin():
    text = _text(DEPLOY / "validate-committee-shadow-canary.sh")
    assert 'DROPIN_DIR="/run/systemd/system/$UNIT.d"' in text
    assert "IPAddressAllow=%s/32" in text
    assert "IPAddressAllow=%s/128" in text
    assert 'resolve_provider "api.openai.com"' in text
    assert 'resolve_provider "api.anthropic.com"' in text
    assert "committee-shadow.service" in text
    assert "persistent egress allowlist" in text
    assert "CANARY_EGRESS_POLICY=PASS" in text
    assert "systemctl enable" not in text


def test_private_tmp_does_not_hide_the_transient_release_tree():
    unit = _text(DEPLOY / "opip-committee-credential-canary.service")
    validation = _text(DEPLOY / "validate-committee-shadow-canary.sh")
    workflow = _text(WORKFLOW)
    assert "PrivateTmp=true" in unit
    assert "/opt/opip-committee-canary-staging/" in workflow
    assert "OPIP_COMMITTEE_CANARY_PYTHONPATH=%s" in validation
    assert '"$RELEASE_ROOT" >> "$ENV_FILE"' in validation
    assert "BindReadOnlyPaths=%s:%s" not in validation


def test_validation_proves_off_before_and_after_and_uses_committed_cleanup():
    text = _text(DEPLOY / "validate-committee-shadow-canary.sh")
    assert "PRE_CANARY_OFF_ISOLATION=PASS" in text
    assert "POST_CANARY_OFF_ISOLATION=PASS" in text
    assert "trap cleanup_on_exit EXIT" in text
    assert 'bash "$CLEANUP_SCRIPT"' in text
    assert "TRANSIENT_CANARY_CLEANUP=PASS" not in text
    assert "CREDENTIAL_CANARY_PROOF=PASS" in text


def test_cleanup_script_proves_transient_authority_is_gone():
    text = _text(DEPLOY / "cleanup-committee-shadow-canary.sh")
    assert 'rm -f -- "$ENV_FILE" "$HOSTS_FILE" "$CANARY_LOG"' in text
    assert 'rm -f -- "$UNIT_PATH" "$LAUNCHER"' in text
    assert 'systemctl is-active --quiet "$UNIT"' in text
    assert 'systemctl cat "$UNIT"' in text
    assert "TRANSIENT_CANARY_CLEANUP=PASS" in text
    assert "/var/lib/opip-committee/credential-canary" not in text


def test_launcher_decodes_credentials_only_inside_the_canary_process():
    text = _text(DEPLOY / "run-committee-credential-canary.sh")
    assert "^[0-9a-f]{40}$" in text
    assert "OPIP_COMMITTEE_OPENAI_API_KEY_B64" in text
    assert "OPIP_COMMITTEE_ANTHROPIC_API_KEY_B64" in text
    assert "base64 --decode" in text
    assert "credential_canary" in text
    assert "/var/lib/opip-committee/credential-canary" in text
    assert "opip-committee-shadow.timer" not in text


def test_canary_workflow_requires_all_machine_readable_proofs():
    text = _text(WORKFLOW)
    for proof in (
        "CANARY_EGRESS_POLICY=PASS",
        "CREDENTIAL_CANARY_PROOF=PASS",
        "TRANSIENT_CANARY_CLEANUP=PASS",
        "POST_CANARY_OFF_ISOLATION=PASS",
    ):
        assert proof in text
