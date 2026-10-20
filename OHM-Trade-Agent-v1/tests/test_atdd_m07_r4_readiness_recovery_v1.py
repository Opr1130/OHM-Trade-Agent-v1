"""Acceptance criteria for the bounded R4 readiness-recovery correction.

Scope contract: ``docs/atdd/scope-contracts/ATDD-M07-R4-readiness-recovery-v1.md``.

These are contract-level checks that bind the corrective artifacts to their
approved criteria. Behavioural proof lives in the M07 suites
(``deploy/m07/tests/scenarios/test_r4_*.py``) and in the disposable-host
rehearsal job; these checks keep that evidence honest and cheap.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

APP = Path(__file__).resolve().parents[1]
REPO = APP.parent
M07 = APP / "deploy" / "m07"
WORKFLOW = REPO / ".github" / "workflows" / "m07-cutover-toolkit.yml"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.acceptance
def test_ac_001_readiness_reads_the_release_through_the_approved_sudo_path():
    """ATDD-M07-R4-readiness-recovery-v1/AC-001: L-01 privileged release read."""
    readiness = _read(M07 / "scripts" / "Invoke-V2Readiness.ps1")
    common = _read(M07 / "scripts" / "lib" / "OpipR1.Common.ps1")
    assert "sudo -n bash -c" in readiness
    assert "MISSING" in readiness and "UNREADABLE" in readiness
    assert "\"test -d $releases/$sha/OHM-Trade-Agent-v1 && echo present\"" not in readiness
    assert "-o BatchMode=yes" in common
    assert "-o StrictHostKeyChecking=yes" in common
    assert "StrictHostKeyChecking=no" not in common + readiness
    assert (M07 / "tests" / "scenarios" / "test_r4_readiness_probe.py").is_file()


@pytest.mark.acceptance
def test_ac_002_release_provenance_is_grounded_in_trusted_evidence():
    """ATDD-M07-R4-readiness-recovery-v1/AC-002: L-02 Git-free provenance."""
    tool = _read(M07 / "scripts" / "opip_release_provenance.py")
    tests = _read(M07 / "tests" / "scenarios" / "test_r4_release_provenance.py")
    assert "def build_expected" in tool and "def build_actual" in tool
    assert "def compare_inventories" in tool
    assert "source_authenticity" in tool and "UNPROVEN" in tool
    assert "candidate-generated inventory cannot establish its own authenticity" in tool
    assert "git-checkout" in tool and "git-archive" in tool
    assert "manifest_digest" in tool
    # Archive provenance must derive the expected inventory from verified bytes.
    assert "def _verified_archive_tree" in tool
    assert '"derived_from": "archive-bytes"' in tool
    assert '"extraction": "isolated-tempdir"' in tool
    assert "--root is not used with git-archive" in tool
    assert "archive-derived inventory is not bound to verified archive bytes" in tool
    assert "test_a_candidate_cannot_authenticate_against_an_unrelated_approved_archive" in tests
    assert (M07 / "L2-RELEASE-PROVENANCE.md").is_file()
    assert (M07 / "tests" / "scenarios" / "test_r4_release_provenance.py").is_file()


@pytest.mark.acceptance
def test_ac_003_remote_paths_stay_posix_and_the_wrapper_never_runs_ssh():
    """ATDD-M07-R4-readiness-recovery-v1/AC-003: Windows operator path safety."""
    common = _read(M07 / "scripts" / "lib" / "OpipR1.Common.ps1")
    wrapper = _read(M07 / "scripts" / "Invoke-V2Bootstrap.ps1")
    assert "function Join-OpipPosixPath" in common
    assert "function ConvertTo-OpipPosixArgument" in common
    assert "Join-OpipPosixPath -Root $ToolkitRoot" in wrapper
    assert "Join-Path $ToolkitRoot" not in wrapper
    assert "$toolkitArg" in wrapper and "$toolkitRootArg" in wrapper
    assert "Invoke-R1SshProbe" not in wrapper
    assert "& ssh" not in wrapper
    assert (M07 / "tests" / "scenarios" / "test_r4_posix_paths.py").is_file()


@pytest.mark.acceptance
def test_ac_004_the_rehearsal_is_disposable_and_labels_simulated_identity():
    """ATDD-M07-R4-readiness-recovery-v1/AC-004: R3-G2 Linux/Docker rehearsal."""
    workflow = yaml.safe_load(_read(WORKFLOW))
    job = workflow["jobs"]["r3-g2-rehearsal"]
    assert job["runs-on"] == "ubuntu-latest"
    steps = "\n".join(step.get("run", "") for step in job["steps"])
    assert "docker build" in steps
    assert "opip-v2:certified-c196dd67" in steps
    assert "IDENTITY_ATTESTATION=SIMULATED" in steps
    assert "production identity remains UNPROVEN" in steps
    assert "--mode provision-toolkit" in steps
    assert "--mode prepare" in steps and "--mode authorize" in steps
    assert "--mode execute" in steps and "--mode activate-writer" in steps
    assert "--mode activate-scheduler" in steps
    assert "restore-canonical" in steps
    assert "writer-gate" in steps
    assert "opip_rollback_guard.py snapshot" in steps
    assert "R3_G2_DISPOSITION" in steps
    assert "ac_004_runtime_sequence=NOT_PASS" in steps
    assert "self-hosted" not in job["runs-on"]


@pytest.mark.acceptance
def test_ac_005_the_restore_contract_resets_the_sequence_and_preserves_events():
    """ATDD-M07-R4-readiness-recovery-v1/AC-005: native restore semantics."""
    writer = _read(APP / "app" / "opip" / "canonical" / "writer.py")
    assert "SET history_epoch = ?, next_local_sequence = 1" in writer
    tests = _read(M07 / "tests" / "scenarios" / "test_r4_restore_semantics.py")
    assert "next_local_sequence" in tests and "== 1" in tests
    assert "test_the_previous_rehearsal_assertion_contradicted_the_contract" in tests
    assert "test_repeated_recovery_advances_the_epoch_each_time" in tests
    assert "test_failed_recovery_leaves_the_live_store_untouched" in tests
    assert "test_equal_epoch_destination_data_is_refused" in tests
    # The correction must not weaken the certified assertion.
    assert "restored[\"next_local_sequence\"] == 1" in tests


@pytest.mark.acceptance
def test_ac_006_the_correction_is_bounded_and_leaves_the_m07_contract_intact():
    """ATDD-M07-R4-readiness-recovery-v1/AC-006: bounded corrective increment."""
    active = _read(APP / "docs" / "atdd" / "ACTIVE_INCREMENT").strip()
    assert active == "ATDD-M07-R4-readiness-recovery-v1"
    contract = _read(
        APP / "docs" / "atdd" / "scope-contracts" / "ATDD-M07-R4-readiness-recovery-v1.md"
    )
    assert contract.startswith("INCREMENT:\nATDD-M07-R4-readiness-recovery-v1")
    assert contract.rstrip().endswith("NONE")
    for marker in ("GIVEN:", "WHEN:", "THEN:"):
        assert marker in contract
    previous = _read(
        APP / "docs" / "atdd" / "scope-contracts" / "ATDD-M07-V2-cutover-toolkit-v1.md"
    )
    assert "ATDD-M07-V2-cutover-toolkit-v1" in previous
    assert "AC-001" in previous and "AC-010" in previous

