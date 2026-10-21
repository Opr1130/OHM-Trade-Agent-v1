from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "learning" / "CANONICAL_LINEAGE_BOUNDARY_V1.md"


@pytest.mark.acceptance
def test_canonical_lineage_boundary_and_archival_policy_is_explicit():
    """ATDD-OPIP-LEARNING-RECOVERY-v1/AC-011: contract criterion."""
    text = POLICY.read_text(encoding="utf-8")
    assert "LINEAGE-A-LEGACY" in text
    assert "LINEAGE-B-V2-GENESIS" in text
    assert "220,721" in text
    assert "Never merge" in text
    assert "Never relabel" in text
    assert "Copy (never move)" in text
    assert "archive_manifest.json" in text
    assert "historical_evidence_modified=false" in text
    assert "lineage-separated counts" in text
