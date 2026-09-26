from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    ProviderFamily,
)
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.committee.providers import ProviderWireRequest
from app.opip.committee.settings import CommitteeShadowSettings
from app.opip.committee.shadow_execution import (
    HttpxPoster,
    ShadowExecutionError,
    build_governed_shadow_providers,
    conservative_request_cost_microunits,
    execute_shadow_case,
)
from app.opip.committee.store import CommitteeEvidenceStore
from app.opip.committee.transports import EgressDeniedError, HttpRequest
from app.opip.decision_intelligence.identity import Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


class FakeCredentials:
    def is_configured(self, family: ProviderFamily) -> bool:
        return True

    def access_token(self, family: ProviderFamily) -> str | None:
        return "test-only-token"


def _case() -> CommitteeCase:
    policy = CommitteePolicy(
        policy_version="committee-shadow-policy-v1",
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id="committee-system",
        prompt_version="v1",
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=500_000,
    )
    item = build_evidence_item(
        evidence_id="evidence-1",
        source_id="canonical-learning-replica",
        available_at=NOW,
        payload={"symbol": "BTC/USD", "signal": "neutral"},
        evidence_cutoff_at=NOW,
    )
    snapshot = build_evidence_snapshot(
        case_id="case-shadow-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=NOW,
        assembled_at=NOW,
        items=(item,),
        source_refs=("canonical:decision-1",),
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id="BTC/USD",
    )
    return CommitteeCase(
        case_id="case-shadow-1",
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=NOW,
        provenance=Provenance(
            producing_component="tests.shadow-execution",
            artifact_or_build_id="build-1",
            process_instance_id="test-1",
            emitted_at=NOW,
            source_record_refs=("canonical:decision-1",),
        ),
        instrument_id="BTC/USD",
    )


def _wire() -> ProviderWireRequest:
    return ProviderWireRequest(
        case_id="case-shadow-1",
        logical_observation_id="logical-1",
        model="gpt-5.6-terra",
        system_prompt="research only",
        user_payload={"evidence": [{"evidence_id": "evidence-1"}]},
        max_output_tokens=1200,
        timeout_seconds=45,
    )


def test_conservative_cost_is_known_and_below_the_case_ceiling() -> None:
    cost = conservative_request_cost_microunits(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        request=_wire(),
    )
    assert cost is not None
    assert 0 < cost < 500_000


def test_governed_provider_builder_seats_only_the_approved_families() -> None:
    providers = build_governed_shadow_providers(
        poster=lambda request: pytest.fail("must not call"),
        credentials=FakeCredentials(),
    )
    assert set(providers) == {ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC}


def test_http_poster_refuses_an_unapproved_destination_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    poster = HttpxPoster(client=client)
    with pytest.raises(EgressDeniedError):
        poster(
            HttpRequest(
                url="https://example.invalid/v1/messages",
                headers={"Authorization": "Bearer test"},
                body={"x": 1},
                timeout_seconds=1,
            )
        )
    assert calls == 0
    client.close()


def test_http_poster_does_not_follow_redirects() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://example.invalid/escape"},
            content=b"redirect",
        )

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    poster = HttpxPoster(client=client)
    response = poster(
        HttpRequest(
            url="https://api.openai.com/v1/chat/completions",
            headers={"Authorization": "Bearer test"},
            body={"model": "gpt-5.6-terra"},
            timeout_seconds=1,
        )
    )
    assert response.status_code == 302
    assert calls == ["https://api.openai.com/v1/chat/completions"]
    client.close()


def test_off_mode_refuses_before_daily_reservation_or_network(tmp_path: Path) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, content=b"must not happen")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = CommitteeEvidenceStore(root=tmp_path)
    with pytest.raises(ShadowExecutionError, match="not shadow"):
        execute_shadow_case(
            _case(),
            store=store,
            settings=CommitteeShadowSettings(opip_committee_mode="off"),
            poster=HttpxPoster(client=client),
            credentials=FakeCredentials(),
            now=lambda: NOW,
        )
    assert calls == 0
    assert not (tmp_path / "daily_spend.json").exists()
    client.close()


def test_shadow_execution_persists_calls_and_case_outcome_offline(tmp_path: Path) -> None:
    def opinion() -> str:
        return json.dumps(
            {
                "schema_version": 1,
                "evidence_sufficiency": "SUFFICIENT",
                "assessment": "NEUTRAL",
                "hypothesis": "No directional edge in the supplied evidence.",
                "confidence": 50,
                "supporting_evidence_refs": ["evidence-1"],
                "contradicting_evidence_refs": [],
                "major_assumptions": [],
                "risk_factors": [],
                "missing_evidence": [],
                "alternative_explanations": [],
                "recommended_research_action": "NO_ACTION",
                "abstention_reason": None,
            }
        )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        model = body["model"]
        if request.url.host == "api.openai.com":
            payload = {
                "model": model,
                "choices": [{"message": {"content": opinion()}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 100},
            }
        else:
            payload = {
                "model": model,
                "content": [{"type": "text", "text": opinion()}],
                "usage": {"input_tokens": 50, "output_tokens": 100},
            }
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    store = CommitteeEvidenceStore(root=tmp_path)
    result = execute_shadow_case(
        _case(),
        store=store,
        settings=CommitteeShadowSettings(
            opip_committee_mode="shadow",
            opip_committee_max_estimated_cost_microunits=500_000,
        ),
        poster=HttpxPoster(client=client),
        credentials=FakeCredentials(),
        now=lambda: NOW,
    )
    assert result.answered_count == 2
    assert len(tuple(store.iter_call_outcomes())) == 2
    assert len(tuple(store.iter_case_outcomes())) == 1
    spend = json.loads((tmp_path / "daily_spend.json").read_text(encoding="utf-8"))
    assert spend[NOW.date().isoformat()]["spent_microunits"] == 500_000
    client.close()
