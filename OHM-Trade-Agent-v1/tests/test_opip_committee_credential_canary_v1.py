"""Credentialled SHADOW canary contract tests.

No test opens a socket. The real provider boundary is exercised through a scripted
HTTP poster, while credentials are synthetic fragments that secret scanners do not
mistake for live values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from app.opip.committee.contracts import ProviderFamily
from app.opip.committee.credential_canary import (
    CredentialCanaryError,
    run_credential_canary,
)
from app.opip.committee.fakes import opinion_json
from app.opip.committee.registry import APPROVED_SHADOW_MODELS
from app.opip.committee.transports import (
    ALLOWED_ENDPOINTS,
    EnvironmentCredentialSource,
    HttpResponse,
)

NOW = datetime(2026, 9, 23, 23, 55, tzinfo=timezone.utc)
SHA = "a" * 40


class ScriptedPoster:
    def __init__(self) -> None:
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        opinion = opinion_json(
            lists={"supporting_evidence_refs": ("CANARY-EVIDENCE-1",)}
        )
        if request.url == ALLOWED_ENDPOINTS[ProviderFamily.OPENAI]:
            body = {
                "model": APPROVED_SHADOW_MODELS[ProviderFamily.OPENAI],
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": opinion}],
                    }
                ],
                "usage": {"input_tokens": 100, "output_tokens": 50},
            }
        elif request.url == ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC]:
            body = {
                "model": APPROVED_SHADOW_MODELS[ProviderFamily.ANTHROPIC],
                "content": [{"type": "text", "text": opinion}],
                "usage": {"input_tokens": 90, "output_tokens": 40},
            }
        else:  # pragma: no cover - the transport allowlist should make this impossible.
            raise AssertionError(request.url)
        return HttpResponse(
            status_code=200,
            body_text=json.dumps(body),
            received_at=NOW,
        )


def _credentials(*, openai: bool = True, anthropic: bool = True):
    values = {}
    if openai:
        values["OPIP_COMMITTEE_OPENAI_API_KEY"] = "test-openai-" + "x" * 8
    if anthropic:
        values["OPIP_COMMITTEE_ANTHROPIC_API_KEY"] = "test-anthropic-" + "y" * 8
    return EnvironmentCredentialSource(environ=values)


def test_canary_runs_exactly_one_call_per_approved_provider(tmp_path):
    poster = ScriptedPoster()
    result = run_credential_canary(
        release_sha=SHA,
        root=tmp_path,
        poster=poster,
        credentials=_credentials(),
        now=lambda: NOW,
    )
    assert len(poster.requests) == 2
    assert {seat.provider for seat in result.seats} == {"openai", "anthropic"}
    assert {seat.served_model for seat in result.seats} == set(
        APPROVED_SHADOW_MODELS.values()
    )
    assert result.total_measured_cost_microunits > 0
    assert result.safe_view()["canary_proof"] == "PASS"


def test_canary_is_durable_and_does_not_repeat_provider_calls(tmp_path):
    poster = ScriptedPoster()
    first = run_credential_canary(
        release_sha=SHA,
        root=tmp_path,
        poster=poster,
        credentials=_credentials(),
        now=lambda: NOW,
    )
    assert first.duplicate is False
    calls_after_first = len(poster.requests)
    second = run_credential_canary(
        release_sha=SHA,
        root=tmp_path,
        poster=poster,
        credentials=_credentials(),
        now=lambda: NOW,
    )
    assert second.duplicate is True
    assert len(poster.requests) == calls_after_first


@pytest.mark.parametrize(
    ("openai", "anthropic"),
    ((False, True), (True, False), (False, False)),
)
def test_missing_credential_fails_before_any_provider_call(
    tmp_path, openai, anthropic
):
    poster = ScriptedPoster()
    with pytest.raises(CredentialCanaryError, match="credential is absent"):
        run_credential_canary(
            release_sha=SHA,
            root=tmp_path,
            poster=poster,
            credentials=_credentials(openai=openai, anthropic=anthropic),
            now=lambda: NOW,
        )
    assert poster.requests == []


def test_invalid_release_sha_fails_before_reading_credentials(tmp_path):
    poster = ScriptedPoster()
    with pytest.raises(CredentialCanaryError, match="40 lowercase hex"):
        run_credential_canary(
            release_sha="main",
            root=tmp_path,
            poster=poster,
            credentials=_credentials(),
            now=lambda: NOW,
        )
    assert poster.requests == []


def test_canary_model_bound_payload_contains_no_account_or_trading_evidence(tmp_path):
    poster = ScriptedPoster()
    run_credential_canary(
        release_sha=SHA,
        root=tmp_path,
        poster=poster,
        credentials=_credentials(),
        now=lambda: NOW,
    )
    payloads = []
    for request in poster.requests:
        if request.url == ALLOWED_ENDPOINTS[ProviderFamily.OPENAI]:
            payloads.append(request.body["input"][0]["content"])
        else:
            payloads.append(request.body["messages"][0]["content"])
    rendered = " ".join(payloads).lower()
    for forbidden in (
        "kraken",
        "telegram",
        "order_id",
        "position",
        "account_balance",
        "api_key",
    ):
        assert forbidden not in rendered


def test_canary_pins_low_reasoning_and_no_openai_tools(tmp_path):
    poster = ScriptedPoster()
    run_credential_canary(
        release_sha=SHA,
        root=tmp_path,
        poster=poster,
        credentials=_credentials(),
        now=lambda: NOW,
    )
    openai = next(
        request
        for request in poster.requests
        if request.url == ALLOWED_ENDPOINTS[ProviderFamily.OPENAI]
    )
    anthropic = next(
        request
        for request in poster.requests
        if request.url == ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC]
    )
    assert openai.body["reasoning"] == {"effort": "low"}
    assert openai.body["tools"] == []
    assert anthropic.body["output_config"] == {"effort": "low"}
    assert anthropic.body["thinking"] == {"type": "adaptive"}
