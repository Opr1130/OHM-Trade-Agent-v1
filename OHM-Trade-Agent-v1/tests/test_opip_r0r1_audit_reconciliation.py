"""Acceptance tests for the R0/R1 audit reconciliation.

These tests prove the governance/documentation reconciliation only. They do not
import application runtime, do not touch trading or exchange modules, and do not
change any runtime behavior.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    normalize_repo_path,
    parse_scope_contract,
)

INCREMENT = "ATDD-R0R1-audit-reconciliation"
HISTORICAL_BASE = "a416be0a068dc58543a4b6cd254d5c42fcaf4c96"
CURRENT_BASELINE = "facf8e369e1251697bf9799bc9b1c575a9cdc3ec"
DOCX_RELATIVE = (
    "OHM-Trade-Agent-v1/docs/architecture/v1.4.3/"
    "OPIP_Profit_Intelligence_Architecture_v1_4_3.docx"
)
DOCX_SHA256 = "ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83"

ARCH = APP_ROOT / "docs" / "architecture"
ATDD = APP_ROOT / "docs" / "atdd"
CONTRACT_PATH = ATDD / "scope-contracts" / f"{INCREMENT}.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(text: str, heading: str, next_heading: str) -> str:
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[start:end]


@pytest.mark.acceptance
def test_active_increment_authorizes_only_reconciliation_docs():
    """ATDD-R0R1-audit-reconciliation/AC-001: the pointer and only this contract authorize files."""
    pointer = _read(ATDD / "ACTIVE_INCREMENT").strip()
    assert pointer == INCREMENT

    contract = parse_scope_contract(_read(CONTRACT_PATH))
    assert contract.increment == INCREMENT

    allowed = {
        normalize_repo_path(path)
        for paths in contract.implementation_map.values()
        for path in paths
    }
    # The contract may authorize governance docs and its own acceptance tests only.
    assert allowed
    assert "OHM-Trade-Agent-v1/app/main.py" not in allowed
    for path in allowed:
        assert not path.startswith("OHM-Trade-Agent-v1/app/"), path
        assert not path.startswith(".github/"), path
        assert path.startswith(("OHM-Trade-Agent-v1/docs/", "OHM-Trade-Agent-v1/tests/")), path


@pytest.mark.acceptance
def test_status_distinguishes_historical_and_current_baseline():
    """ATDD-R0R1-audit-reconciliation/AC-002: historical base vs current reconciled baseline."""
    text = _read(ARCH / "CURRENT_ARCHITECTURE_STATUS.md")
    assert HISTORICAL_BASE in text
    assert CURRENT_BASELINE in text
    assert "Historical audit base" in text
    assert "Current reconciled code/production baseline" in text
    assert "R3 has not started" in text
    assert "FEATURE BUS ACTIVATED = NO" in text or "FEATURE BUS ACTIVATED BY THIS RECONCILIATION = NO" in text
    assert "FUNDED TRADING ENABLED = NO" in text


@pytest.mark.acceptance
def test_conformance_records_r2_proof_and_no_runtime_authority():
    """ATDD-R0R1-audit-reconciliation/AC-003: F2 verified shadow proof AND no runtime authority."""
    text = _read(ARCH / "OPIP_CONFORMANCE_LEDGER.md")
    f2 = _section(text, "## F2", "## F3")
    assert "VERIFIED_SHADOW_EVIDENCE" in f2
    assert "replay" in f2.lower()
    assert "parity" in f2.lower()
    assert "OPIP_FEATURE_BUS_MODE=off" in f2
    assert "run_cycle" in f2 and "does not call" in f2
    assert "IMPLEMENTED_NOT_ACTIVE" in f2

    # Absence of evidence is not converted into completion for the other features.
    assert "MISSING" in _section(text, "## F3", "## F4")
    assert "PARTIAL" in _section(text, "## F4", "## F5")
    assert "LEGACY_ACTIVE" in _section(text, "## F5", "## F6")
    assert "MISSING" in _section(text, "## F6", "## F7")
    assert "LEGACY_ACTIVE" in _section(text, "## F7", "## F8")
    assert "MISSING" in _section(text, "## F12", "## Adjacent authorities") or (
        "SHADOW" in _section(text, "## F12", "## Adjacent authorities")
    )


@pytest.mark.acceptance
def test_roadmap_marks_r2_complete_and_r3_next():
    """ATDD-R0R1-audit-reconciliation/AC-004: R2 complete, R3 the single next phase in order."""
    text = _read(ARCH / "OPIP_RECOVERY_ROADMAP.md")
    assert "### R2 — Feature Bus shadow proof (COMPLETE)" in text

    r3 = _section(text, "### R3", "### R4")
    assert "evaluate(FeatureSnapshot, DetectorState, evaluation_time)" in r3
    assert "opportunity lifecycle" in r3
    assert "feasibility seam" in r3
    assert "forecast owner" in r3
    assert "selector" in r3
    assert "no Paper v2 activation" in r3
    assert "no legacy deletion in R3" in r3

    # R2's actual completed proof replaces the stale pre-R2 wording.
    assert "b26dab8d58116c5560e5bbed73ce95f0c814fbeb" in text
    assert "36473910247" in text


@pytest.mark.acceptance
def test_retirement_ledger_preserves_history_and_claims_no_cutover():
    """ATDD-R0R1-audit-reconciliation/AC-005: nothing deleted, no cutover claimed."""
    text = _read(ARCH / "OPIP_RETIREMENT_LEDGER.md")
    assert "no cutover has occurred" in text
    assert "cutover not performed" in text
    assert "separate owner approval" in text
    assert CURRENT_BASELINE in text


@pytest.mark.acceptance
def test_runtime_truth_records_only_observed_facts():
    """ATDD-R0R1-audit-reconciliation/AC-006: 2026-09-28 runtime truth holds observed facts only."""
    text = _read(ARCH / "OPIP_RUNTIME_TRUTH_2026-09-28.md")
    assert CURRENT_BASELINE in text
    for marker in (
        "OPIP_CORE_DEPLOY_STATUS=SUCCESS",
        "OPIP_CORE_POSTCOMMIT_HEALTH=OK",
        "OPIP_LEARNING_EXPORT_STATUS=SUCCESS",
        "OPIP_LEARNING_READINESS=READY",
        "OPIP_PAPER_REGISTRY_GENESIS_STATUS=OK",
        "scheduler reconciliation: OK",
        "degraded (rc=1)",
    ):
        assert marker in text, marker
    assert "OPIP_FEATURE_BUS_MODE" in text and "`off`" in text
    assert "UNKNOWN_NEEDS_EVIDENCE" in text
    # The historical 2026-09-27 observation is preserved, not rewritten.
    assert (ARCH / "OPIP_RUNTIME_TRUTH_2026-09-27.md").is_file()


@pytest.mark.acceptance
def test_authority_docx_hash_is_unchanged():
    """ATDD-R0R1-audit-reconciliation/AC-007: the owner v1.4.3 DOCX bytes are unchanged."""
    docx = APP_ROOT.parent / DOCX_RELATIVE
    if not docx.is_file():
        docx = APP_ROOT.parent.parent / DOCX_RELATIVE
    assert docx.is_file(), docx
    digest = hashlib.sha256(docx.read_bytes()).hexdigest()
    assert digest == DOCX_SHA256
    assert DOCX_SHA256 in _read(ARCH / "v1.4.3" / "SOURCE.md")
