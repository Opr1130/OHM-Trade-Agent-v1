"""Acceptance check for REST-only label handling in the v6 workflow."""

from __future__ import annotations

from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "v6-agent.yml"


@pytest.mark.acceptance
def test_label_swap_uses_rest_not_graphql() -> None:
    """ATDD-OPIP-V6-LABELS-v1/AC-001"""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "gh issue edit" not in text
    assert '/issues/${ISSUE_NUMBER}/labels/build' in text
    assert "-f 'labels[]=built'" in text
