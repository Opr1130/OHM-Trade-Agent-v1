"""Focused tests for the non-blocking V6 architecture evidence collector."""
from __future__ import annotations

import json

import pytest

from tools.architecture_gap_capture import capture, load_catalog, markdown_report, normalized_path

pytestmark = pytest.mark.acceptance


def catalog():
    return {
        "schema_version": 1, "mode": "advisory-only",
        "contracts": [{
            "id": "V6-F11-001", "title": "Independent protective path", "risk": "P0",
            "implementation_paths": ["OHM-Trade-Agent-v1/app/**/*f11*.py"],
            "evidence_refs": ["OHM-Trade-Agent-v1/tests/test_protection.py"],
            "review_question": "Does this add a database dependency?",
            "import_boundary": {
                "paths": ["OHM-Trade-Agent-v1/app/**/*f11*.py"],
                "forbidden_modules": ["app.opip.data_platform"],
            },
        }],
    }


def setup_repo(tmp_path, source):
    module = tmp_path / "OHM-Trade-Agent-v1/app/services/f11_protection.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    proof = tmp_path / "OHM-Trade-Agent-v1/tests/test_protection.py"
    proof.parent.mkdir(parents=True)
    proof.write_text("def test_protection(): assert True\n", encoding="utf-8")
    return module.relative_to(tmp_path).as_posix(), proof.relative_to(tmp_path).as_posix()


def test_reports_affected_contract_without_calling_it_a_confirmed_bug(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-001: Changed protected source becomes an unverified contract candidate without being treated as a confirmed defect."""
    changed, proof = setup_repo(tmp_path, "from app.services.protection import protect\n")
    result = capture(tmp_path, catalog(), [changed])
    assert len(result["findings"]) == 1
    assert result["findings"][0]["type"] == "CONTRACT_REVIEW_CANDIDATE"
    assert result["findings"][0]["status"] == "UNVERIFIED"
    assert result["confirmed_defects"] == 0
    assert result["contract_coverage"][0]["evidence_references_present"] == [proof]


def test_catches_direct_forbidden_import_as_candidate_not_proven_failure(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-002: Direct forbidden F11 to business-store imports produce AST evidence rather than a silent pass."""
    changed, _ = setup_repo(
        tmp_path,
        "from app.opip.data_platform.business_store import BusinessStore\n"
        "import app.opip.data_platform.db\n",
    )
    result = capture(tmp_path, catalog(), [changed])
    violations = [f for f in result["findings"] if f["type"] == "STATIC_IMPORT_BOUNDARY_CANDIDATE"]
    assert {f["import"] for f in violations} == {
        "app.opip.data_platform.business_store", "app.opip.data_platform.db"
    }
    assert all(f["line"] in {1, 2} for f in violations)


@pytest.mark.parametrize("source,expected", [
    ("from app.opip import data_platform\n", "app.opip.data_platform"),
    ("from app.opip import data_platform as dp\n", "app.opip.data_platform"),
    ("from app.opip.data_platform import business_store\n", "app.opip.data_platform"),
    ("from app.opip import (\n    other,\n    data_platform,\n)\n", "app.opip.data_platform"),
])
def test_from_import_of_forbidden_submodule_is_not_missed(tmp_path, source, expected):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-002: `from package import forbidden_submodule` is a candidate, one finding per statement."""
    changed, _ = setup_repo(tmp_path, source)
    hits = [f for f in capture(tmp_path, catalog(), [changed])["findings"]
            if f["type"] == "STATIC_IMPORT_BOUNDARY_CANDIDATE"]
    assert [h["import"] for h in hits] == [expected]


def test_unrelated_from_import_is_not_a_candidate(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-002: Sibling packages and prefix look-alikes are not flagged."""
    changed, _ = setup_repo(
        tmp_path,
        "from app.opip import analytics\nfrom app.opip.data_platform_extras import x\nfrom . import data_platform\n",
    )
    hits = [f for f in capture(tmp_path, catalog(), [changed])["findings"]
            if f["type"] == "STATIC_IMPORT_BOUNDARY_CANDIDATE"]
    assert hits == []


def test_baseline_scans_existing_files_for_forbidden_imports(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-003: The weekly baseline inspects the import boundary of unchanged files instead of reporting a false all-clear."""
    _, _ = setup_repo(tmp_path, "from app.opip import data_platform\n")
    result = capture(tmp_path, catalog(), [], baseline=True)
    hits = [f for f in result["findings"] if f["type"] == "STATIC_IMPORT_BOUNDARY_CANDIDATE"]
    assert [(h["import"], h["status"]) for h in hits] == [("app.opip.data_platform", "UNVERIFIED")]
    assert result["confirmed_defects"] == 0
    assert "No candidates identified" not in markdown_report(result)


def test_symlinked_source_is_not_followed(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-004: A symlinked changed path is not read by the observer."""
    changed, _ = setup_repo(tmp_path, "import math\n")
    outside = tmp_path / "outside.py"
    outside.write_text("import app.opip.data_platform\n", encoding="utf-8")
    link = tmp_path / "OHM-Trade-Agent-v1/app/services/f11_link.py"
    link.symlink_to(outside)
    result = capture(tmp_path, catalog(), [link.relative_to(tmp_path).as_posix()])
    assert [f for f in result["findings"] if f["type"] == "STATIC_IMPORT_BOUNDARY_CANDIDATE"] == []


def test_baseline_is_inventory_only_and_existing_proof_not_claimed_validated(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-003: Baseline reports existing evidence references without treating their presence as passing proof."""
    _, _ = setup_repo(tmp_path, "import math\n")
    result = capture(tmp_path, catalog(), [], baseline=True)
    assert result["findings"] == []
    assert result["contract_coverage"][0]["proof_status"] == "EXISTING_REFERENCES_NOT_REVALIDATED"
    assert "NON-BLOCKING" in markdown_report(result)


def test_absent_proof_is_reported_not_auto_created(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-003: Missing proof references are surfaced, never silently created or called validated."""
    changed, proof = setup_repo(tmp_path, "import math\n")
    (tmp_path / proof).unlink()
    result = capture(tmp_path, catalog(), [changed])
    assert result["contract_coverage"][0]["proof_status"] == "NO_EVIDENCE_REFERENCES_FOUND"
    assert not (tmp_path / proof).exists()


def test_changed_proof_and_duplicate_paths_are_deterministic(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-003: Repeated paths and changed evidence references yield deterministic review evidence."""
    changed, proof = setup_repo(tmp_path, "import math\n")
    result = capture(tmp_path, catalog(), [changed, changed, proof])
    assert result["changed_paths"] == sorted([changed, proof])
    assert result["contract_coverage"][0]["proof_status"] == "EVIDENCE_CHANGED_REQUIRES_REVIEW"
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("bad", ["../outside", "/etc/passwd", "foo/../bar", "", "x\x00y"])
def test_rejects_unsafe_changed_paths(bad):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-004: Path traversal and unsafe paths are rejected before source inspection."""
    with pytest.raises(ValueError):
        normalized_path(bad)


def test_catalog_rejects_duplicate_ids(tmp_path):
    """ATDD-V6-ARCH-OBSERVE-v1/AC-004: Catalog rejects duplicate contract identifiers."""
    invalid = catalog()
    invalid["contracts"].append(dict(invalid["contracts"][0]))
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(path)

def test_advisory_workflow_does_not_change_required_gate_or_write_issues():
    """ATDD-V6-ARCH-OBSERVE-v1/AC-005: Advisory observation collects artifacts without replacing security or safety checks."""
    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    observer = (root / ".github/workflows/architecture-observe.yml").read_text(encoding="utf-8")
    current = (root / ".github/workflows/pytest.yml").read_text(encoding="utf-8")
    assert "continue-on-error: true" in observer
    assert "contents: read" in observer
    assert "upload-artifact@v4" in observer
    assert "architecture_gap_capture.py" in observer
    assert "issue_comment:" not in observer and "pull-requests: write" not in observer
    assert "release architecture gate" in current
    assert "Run mandatory safety suite" in current
