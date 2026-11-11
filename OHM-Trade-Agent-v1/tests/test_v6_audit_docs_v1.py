"""Acceptance check for the documentation-only v6 audit increment."""

from __future__ import annotations

from pathlib import Path

import pytest

AUDIT = Path(__file__).resolve().parents[1] / "docs" / "V6_AUDIT.md"


@pytest.mark.acceptance
def test_audit_states_method_limits_and_staged_retirement() -> None:
    """ATDD-OPIP-V6-AUDIT-DOCS-v1/AC-601"""
    text = AUDIT.read_text(encoding="utf-8")
    assert "## 2. Method and limits" in text
    assert "## 11. Retirement method and sequence" in text
    assert "candidates" in text.lower()
