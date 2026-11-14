"""Router telemetry records the output cap in force (additive, no behaviour change)."""
from __future__ import annotations

import json

import pytest

from app.services import opip_ai_router as router


def _target():
    return router.RouteTarget(
        provider="openai", model="m", api_key="k", base_url=None,
        reasoning_effort="high", send_reasoning=True,
    )


@pytest.mark.acceptance
def test_router_usage_record_carries_the_output_cap(tmp_path, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-004: each router usage row records max_output_tokens and keeps its existing fields."""
    monkeypatch.setattr(router, "ROUTER_USAGE_FILE", tmp_path / "u.jsonl")
    monkeypatch.setattr(router, "ROUTER_USAGE_LOCK", tmp_path / ".u.lock")
    router._append_router_usage(
        target=_target(), route_tier="premium", route_reason="r",
        candidate_count=8, latency_ms=5, status="failed",
        error_code="TRUNCATED_OUTPUT", incomplete_reason="max_output_tokens",
        max_output_tokens=1200,
    )
    router._append_router_usage(
        target=_target(), route_tier="premium", route_reason="r",
        candidate_count=8, latency_ms=5, status="succeeded",
    )
    rows = [json.loads(x) for x in (tmp_path / "u.jsonl").read_text().splitlines()]
    assert rows[0]["max_output_tokens"] == 1200
    assert rows[1]["max_output_tokens"] is None
    for key in ("provider", "model", "route_tier", "reasoning_effort", "candidate_count",
                "status", "error_code", "incomplete_reason", "output_tokens",
                "reasoning_tokens", "advisory_only", "funded_trade_authority_changed"):
        assert key in rows[0]
    assert rows[0]["advisory_only"] is True and rows[0]["funded_trade_authority_changed"] is False
