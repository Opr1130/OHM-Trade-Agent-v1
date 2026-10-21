from pathlib import Path

import pytest

from app.services.learning_recovery_binding import evaluate, failed

ROOT = Path(__file__).resolve().parents[2]
PRODUCTION = "a26d664972075b2a220a466215cf71e389decb86"
TOOLING = "b" * 40
MAIN = TOOLING


def _evaluate(**overrides):
    values = {
        "production_sha": PRODUCTION,
        "tooling_sha": TOOLING,
        "current_main_sha": MAIN,
        "manifest_sha": PRODUCTION,
        "compare_status": "ahead",
        "tooling_ci_success": True,
        "timers_enabled": False,
    }
    values.update(overrides)
    return evaluate(**values)


def _names(**overrides) -> set[str]:
    return {check.name for check in failed(_evaluate(**overrides))}


def test_valid_recovery_binding_passes_every_check():
    checks = _evaluate()
    assert failed(checks) == []
    assert len(checks) == 8


def test_manifest_must_bind_the_production_sha():
    assert "manifest_binds_production" in _names(manifest_sha="c" * 40)


def test_arbitrary_old_tooling_sha_is_refused():
    assert "tooling_equals_current_main" in _names(tooling_sha="d" * 40)


def test_normal_deploy_identity_equality_is_refused():
    assert "identities_are_distinct" in _names(tooling_sha=PRODUCTION, current_main_sha=PRODUCTION)


def test_tooling_ci_must_be_successful():
    assert "tooling_exact_sha_ci_success" in _names(tooling_ci_success=False)


@pytest.mark.parametrize("status", ["behind", "diverged", "unknown"])
def test_non_ancestor_compare_is_refused(status):
    assert "production_is_ancestor_of_tooling" in _names(compare_status=status)


def test_recovery_requires_timers_already_disabled():
    assert "timers_were_disabled_before_recovery" in _names(timers_enabled=True)


def test_recovery_workflow_is_owner_gated_and_separate():
    workflow = (
        ROOT / ".github" / "workflows" / "deploy-learning-recovery.yml"
    ).read_text(encoding="utf-8")
    assert "startsWith(github.event.comment.body, '/deploy-learning-recovery ')" in workflow
    assert "github.event.comment.author_association == 'OWNER'" in workflow
    assert "environment: learning-production" in workflow
    assert "compare/$PRODUCTION_SHA...$TOOLING_SHA" in workflow
    assert "run-recovery-learning-deploy.sh" in workflow
    assert "run-gated-learning-deploy.sh" not in workflow


def test_recovery_script_binds_authenticated_manifest_and_never_enables_timers():
    script = (
        ROOT
        / "OHM-Trade-Agent-v1"
        / "deploy"
        / "learning"
        / "run-recovery-learning-deploy.sh"
    ).read_text(encoding="utf-8")
    assert "opip-export-v1" in script
    assert 'manifest_production_sha" != "$PRODUCTION_SHA' in script
    assert "learning_recovery_binding" in script
    assert "systemctl enable" not in script
    assert "timers_enabled_by_recovery" in script
