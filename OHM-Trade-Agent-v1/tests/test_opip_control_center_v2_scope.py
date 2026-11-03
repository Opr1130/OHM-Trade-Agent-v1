"""ATDD contract checks for isolated review-only O'Pip Control Center V2."""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "control-center-v2"
REPO = ROOT.parent
INCREMENT = "ATDD-OPIP-CONTROL-CENTER-V2-v1"


@pytest.mark.acceptance
def test_read_only_evidence_and_no_credential_bypass():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-001: all values must be sourced; never fabricate model authority."""
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    assert "V2_PRODUCTION_V1_OBSERVATION" in api
    assert "NO_EVIDENCE" in api
    assert "path.startsWith('/api/')" in api
    assert "Viewer credential required" in api
    assert "/api/opip/scoring-comparison" in view
    assert "No paired comparison evidence" in view
    assert "Unverified" in api


@pytest.mark.acceptance
def test_protected_advisory_only_workflow():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-002: no unprotected admin writes or funded authority."""
    view = (UI / "src" / "main.jsx").read_text(encoding="utf-8")
    api = (UI / "src" / "api.mjs").read_text(encoding="utf-8")
    assert "X-Strategy-Admin-Secret" in api
    assert "X-Expected-Profile-Sha256" in api
    assert "ACTIVATE_ADVISORY_ONLY" in view
    assert "ROLLBACK_ADVISORY_ONLY" in view
    assert "api/opip/strategy-admin/draft" in view
    assert "localStorage.setItem(" not in view
    assert "sessionStorage.setItem(" not in view
    assert "funded trading not activated" in view.lower()


@pytest.mark.acceptance
def test_networked_build_only_ci():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-003: CI proves build and browser without deploying."""
    workflow = (REPO / ".github/workflows/opip-control-center-v2-ci.yml").read_text(encoding="utf-8")
    pkg = (UI / "package.json").read_text(encoding="utf-8")
    lock = (UI / "package-lock.json").read_text(encoding="utf-8")
    smoke = (UI / "e2e/smoke.spec.mjs").read_text(encoding="utf-8")
    assert "npm ci --no-audit --no-fund" in workflow
    assert "npm run build" in workflow
    assert "npm run test:e2e" in workflow
    assert "actions/upload-artifact" in workflow
    assert "contents: read" in workflow
    # Source paths legitimately contain the word deploy (e.g. Nginx overlay).
    # Reject executable deployment authority rather than that substring.
    lower_workflow = workflow.lower()
    for forbidden in (
        "docker push", "docker compose up", "kubectl apply", "scp ",
        "rsync ", "ssh ", "doctl ", "secrets.", "id-token: write",
        "contents: write", "environment: production",
    ):
        assert forbidden not in lower_workflow
    assert "playwright" in pkg and "lockfileVersion" in lock
    assert "localStorage.length" in smoke


@pytest.mark.acceptance
def test_active_scope_and_legacy_are_preserved():
    """ATDD-OPIP-CONTROL-CENTER-V2-v1/AC-004: versioned ATDD map, unchanged old dashboard."""
    scope = ROOT / "docs/atdd"
    active = (scope / "ACTIVE_INCREMENT").read_text().strip()
    assert (scope / "scope-contracts" / (active + ".md")).is_file()
    assert (scope / "scope-contracts" / (INCREMENT + ".md")).is_file()
    assert (scope / "scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md").exists()
    assert (ROOT / "app/api/dashboard.py").exists()
    assert (ROOT / "app/api/dashboard.html").exists()
