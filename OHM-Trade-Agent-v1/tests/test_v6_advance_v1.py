"""Acceptance check: the merged PR is excluded when advance counts open v6 PRs."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.acceptance
def test_merged_pr_excluded_from_open_count() -> None:
    """ATDD-OPIP-V6-ADVANCE-v1/AC-001"""
    workflow = (ROOT / ".github" / "workflows" / "v6-agent.yml").read_text(encoding="utf-8")
    script = (ROOT / "OHM-Trade-Agent-v1" / "tools" / "v6_agent" / "advance.py").read_text(
        encoding="utf-8"
    )
    assert "MERGED_PR: ${{ github.event.pull_request.number }}" in workflow
    assert 'os.environ.get("MERGED_PR"' in script
    assert 'str(p["number"]) != merged' in script
