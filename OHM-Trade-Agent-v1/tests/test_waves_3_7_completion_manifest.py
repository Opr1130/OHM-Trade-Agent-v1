from pathlib import Path


def test_completion_manifest_documents_manual_scaling_and_validation():
    text = Path("WAVES_3_7_COMPLETION.md").read_text()
    assert "explicit human approval" in text
    assert "pytest" in text
    assert "compileall" in text
    assert "no automatic full-capital mode" in text.lower()
