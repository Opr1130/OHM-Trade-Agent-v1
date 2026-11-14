"""Focused tests for the non-blocking V6 architecture evidence collector."""
from __future__ import annotations

import json

import pytest

from tools.architecture_gap_capture import capture, load_catalog, markdown_report, normalized_path


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
    changed, proof = setup_repo(tmp_path, "from app.services.protection import protect\n")
    result = capture(tmp_path, catalog(), [changed])
    assert len(result["findings"]) == 1
    assert result["findings"][0]["type"] == "CONTRACT_REVIEW_CANDIDATE"
    assert result["findings"][0]["status"] == "UNVERIFIED"
    assert result["confirmed_defects"] == 0
    assert result["contract_coverage"][0]["evidence_references_present"] == [proof]


def test_catches_direct_forbidden_import_as_candidate_not_proven_failure(tmp_path):
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


def test_baseline_is_inventory_only_and_existing_proof_not_claimed_validated(tmp_path):
    _, _ = setup_repo(tmp_path, "import math\n")
    result = capture(tmp_path, catalog(), [], baseline=True)
    assert result["findings"] == []
    assert result["contract_coverage"][0]["proof_status"] == "EXISTING_REFERENCES_NOT_REVALIDATED"
    assert "NON-BLOCKING" in markdown_report(result)


def test_absent_proof_is_reported_not_auto_created(tmp_path):
    changed, proof = setup_repo(tmp_path, "import math\n")
    (tmp_path / proof).unlink()
    result = capture(tmp_path, catalog(), [changed])
    assert result["contract_coverage"][0]["proof_status"] == "NO_EVIDENCE_REFERENCES_FOUND"
    assert not (tmp_path / proof).exists()


def test_changed_proof_and_duplicate_paths_are_deterministic(tmp_path):
    changed, proof = setup_repo(tmp_path, "import math\n")
    result = capture(tmp_path, catalog(), [changed, changed, proof])
    assert result["changed_paths"] == sorted([changed, proof])
    assert result["contract_coverage"][0]["proof_status"] == "EVIDENCE_CHANGED_REQUIRES_REVIEW"
    assert json.loads(json.dumps(result)) == result


@pytest.mark.parametrize("bad", ["../outside", "/etc/passwd", "foo/../bar", "", "x\x00y"])
def test_rejects_unsafe_changed_paths(bad):
    with pytest.raises(ValueError):
        normalized_path(bad)


def test_catalog_rejects_duplicate_ids(tmp_path):
    invalid = catalog()
    invalid["contracts"].append(dict(invalid["contracts"][0]))
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(invalid), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_catalog(path)
