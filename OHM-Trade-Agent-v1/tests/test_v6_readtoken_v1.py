"""Acceptance check: advance reads with the workflow token."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.acceptance
def test_advance_reads_with_workflow_token() -> None:
    """ATDD-OPIP-V6-READTOKEN-v1/AC-001"""
    workflow = (ROOT / ".github" / "workflows" / "v6-agent.yml").read_text(encoding="utf-8")
    script = (ROOT / "OHM-Trade-Agent-v1" / "tools" / "v6_agent" / "advance.py").read_text(
        encoding="utf-8"
    )
    assert "READ_TOKEN: ${{ github.token }}" in workflow
    assert 'os.environ.get("READ_TOKEN")' in script
    assert "token=read" in script
