"""Real governed provider transports and the UTC daily ceiling (IC-008).

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

No test in this module contacts a provider. Every transport is exercised against a
mock HTTP poster, which is the point: the wire contract, the allowlist, the
credential handling, and the failure translation are all provable offline. The
credentialled shadow validation is a separate step that requires OWNER-supplied
credentials.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

from app.opip.committee.contracts import CostCompleteness, ProviderFailureClass, ProviderFamily
from app.opip.committee.providers import ProviderInvocationError, ProviderWireRequest
from app.opip.committee.pricing import PriceBook, TokenPrice
from app.opip.committee.registry import (
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_DAILY_COST_MICROUNITS,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_ROLE_PRIMARIES,
    ApprovalState,
    ModelIdKind,
    ModelRegistryEntry,
    ReasoningMode,
    RegistryError,
    default_shadow_registry,
)
from app.opip.committee.roles import CommitteeRole
from app.opip.committee.settings import committee_shadow_enabled
from app.opip.committee.transports import (
    ALLOWED_ENDPOINTS,
    ANTHROPIC_API_VERSION,
    CREDENTIAL_ENV_NAMES,
    AnthropicTransport,
    EgressDeniedError,
    EnvironmentCredentialSource,
    HttpRequest,
    HttpResponse,
    MAX_HTTP_RESPONSE_BYTES,
    OpenAITransport,
    StdlibHttpPoster,
    build_approved_transports,
    build_transport,
)

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
LATER = NOW.replace(hour=23, minute=30)
NEXT_DAY = datetime(2026, 9, 24, 0, 30, tzinfo=timezone.utc)

OPENAI_KEY = "test-openai-credential-value"
ANTHROPIC_KEY = "test-anthropic-credential-value"


class MockPoster:
    """Records requests and replays a scripted response. Never opens a socket."""

    def __init__(self, *, status_code: int = 200, body: str | None = None) -> None:
        self.requests: list[HttpRequest] = []
        self._status = status_code
        self._body = body

    def __call__(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return HttpResponse(
            status_code=self._status, body_text=self._body or "{}", received_at=NOW
        )


def _credentials() -> EnvironmentCredentialSource:
    return EnvironmentCredentialSource(
        environ={
            CREDENTIAL_ENV_NAMES[ProviderFamily.OPENAI]: OPENAI_KEY,
            CREDENTIAL_ENV_NAMES[ProviderFamily.ANTHROPIC]: ANTHROPIC_KEY,
        }
    )


def _wire(model: str = "gpt-5.6-terra") -> ProviderWireRequest:
    return ProviderWireRequest(
        case_id="case-1",
        logical_observation_id="COMMITTEE-LOGICAL:aaaa",
        model=model,
        system_prompt="role prompt",
        user_payload={"evidence": "screened"},
        max_output_tokens=256,
        timeout_seconds=30,
    )


def _openai_body(
    text: str = '{"assessment":"SUPPORTIVE"}',
    model: str = "gpt-5.6-terra",
    *,
    status: str = "completed",
) -> str:
    return json.dumps(
        {
            "model": model,
            "status": status,
            "output": [
                {"type": "reasoning", "summary": []},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                },
            ],
            "usage": {"input_tokens": 120, "output_tokens": 40},
        }
    )


def _anthropic_body(text: str = '{"assessment":"SUPPORTIVE"}', model: str = "claude-sonnet-5") -> str:
    return json.dumps(
        {
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 100, "output_tokens": 30},
        }
    )


# ------------------------------------------------------- IC-008: models


def test_the_approved_models_are_provider_defined_fixed_ids():
    assert APPROVED_SHADOW_MODELS[ProviderFamily.OPENAI] == "gpt-5.6-terra"
    assert APPROVED_SHADOW_MODELS[ProviderFamily.ANTHROPIC] == "claude-sonnet-5"
    # The requirement is a fixed or versioned id, not literally a date suffix: the
    # Anthropic id carries no date and is still a fixed id.
    assert all(isinstance(model, str) and model.strip() for model in APPROVED_SHADOW_MODELS.values())


def test_a_rolling_alias_is_refused_at_construction():
    """An alias that floats would change what answered a role without a registry change."""
    with pytest.raises(RegistryError, match="rolling alias"):
        ModelRegistryEntry(
            entry_id="e",
            role=CommitteeRole.RISK_CRITIC,
            provider_family=ProviderFamily.OPENAI,
            model_id="latest",
            endpoint="",
            prompt_hash="p",
            schema_hash="s",
            owner="owner",
            approval=ApprovalState.APPROVED,
            effective_from=NOW,
            review_by=NOW.replace(year=2027),
            model_id_kind=ModelIdKind.ROLLING_ALIAS,
        )

def test_a_fixed_model_id_without_a_date_is_accepted():
    """The Anthropic scheme uses no date suffix; that must not be a blocker."""
    entry = ModelRegistryEntry(
        entry_id="e",
        role=CommitteeRole.RISK_CRITIC,
        provider_family=ProviderFamily.ANTHROPIC,
        model_id="claude-sonnet-5",
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC],
        prompt_hash="p",
        schema_hash="s",
        owner="owner",
        approval=ApprovalState.APPROVED,
        effective_from=NOW,
        review_by=NOW.replace(year=2027),
        model_id_kind=ModelIdKind.FIXED,
    )
    assert entry.model_id_kind is ModelIdKind.FIXED
    assert entry.model_id == "claude-sonnet-5"


# ------------------------------------------- IC-008: cross-vendor coverage


def test_primaries_are_distributed_across_both_vendors():
    """A single-vendor primary set would leave the fallback untested and produce
    no genuinely independent vendor evidence for the disagreement matrix."""
    primaries = set(APPROVED_SHADOW_ROLE_PRIMARIES.values())
    assert primaries == {ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC}
    # Both vendors must actually answer several roles, not one token role.
    for family in (ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC):
        assert sum(
            1 for value in APPROVED_SHADOW_ROLE_PRIMARIES.values() if value is family
        ) >= 2


def test_every_role_has_at_most_one_fallback_from_the_other_vendor():
    registry = default_shadow_registry(
        effective_from=NOW, review_by=NOW.replace(year=2027)
    )
    for role in CommitteeRole:
        primary_id, fallback_id = registry.routes[role]
        assert primary_id != fallback_id
        assert fallback_id is not None
        primary = registry.entry(primary_id)
        fallback = registry.entry(fallback_id)
        # Exactly one fallback, and it is the other approved vendor.
        assert primary.provider_family is not fallback.provider_family
        assert primary.provider_family is APPROVED_SHADOW_ROLE_PRIMARIES[role]
        assert {primary.provider_family, fallback.provider_family} == set(
            APPROVED_SHADOW_MODELS
        )


def test_the_approved_registry_pins_reasoning_and_the_case_ceiling():
    registry = default_shadow_registry(
        effective_from=NOW, review_by=NOW.replace(year=2027)
    )
    for entry in registry.entries:
        assert entry.reasoning_mode is ReasoningMode.LOW
        assert entry.max_cost_microunits == APPROVED_MAX_CASE_COST_MICROUNITS
        assert entry.model_id_kind is ModelIdKind.FIXED
        assert entry.model_id in APPROVED_SHADOW_MODELS.values()


def test_the_approved_ceilings_are_the_owner_values():
    assert APPROVED_MAX_CASE_COST_MICROUNITS == 500_000  # $0.50
    assert APPROVED_MAX_DAILY_COST_MICROUNITS == 10_000_000  # $10


# ------------------------------------------------- IC-008: egress allowlist


def test_the_allowlist_names_exactly_the_two_provider_endpoints():
    assert set(ALLOWED_ENDPOINTS) == {ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC}
    for url in ALLOWED_ENDPOINTS.values():
        assert url.startswith("https://")
        assert url.count("/") >= 3  # an exact path, not a bare host


def test_a_transport_cannot_be_built_for_an_ungoverned_family():
    with pytest.raises(EgressDeniedError, match="no approved transport"):
        build_transport(
            family=ProviderFamily.DEEPSEEK,
            model="whatever",
            poster=MockPoster(),
            credentials=_credentials(),
        )


def test_a_transport_refuses_a_destination_outside_the_allowlist():
    """Construction refuses a wrong endpoint, so such an object cannot exist."""
    with pytest.raises(EgressDeniedError, match="may only reach its declared endpoint"):
        OpenAITransport(
            family=ProviderFamily.OPENAI,
            model="gpt-5.6-terra",
            poster=MockPoster(),
            credentials=_credentials(),
            endpoint="https://evil.invalid/v1/chat/completions",
        )


def test_the_require_allowed_guard_refuses_a_substituted_url():
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    transport.require_allowed(ALLOWED_ENDPOINTS[ProviderFamily.OPENAI])
    with pytest.raises(EgressDeniedError, match="refused"):
        transport.require_allowed("https://api.anthropic.com/v1/messages")


# ------------------------------------------------- IC-008: credentials


def test_an_absent_credential_makes_the_seat_unavailable_without_a_call():
    """A missing secret is an unavailable seat, never an unauthenticated request."""
    poster = MockPoster(body=_openai_body())
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=EnvironmentCredentialSource(environ={}),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    assert transport.availability_reason() is not None
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is ProviderFailureClass.PROVIDER_UNAVAILABLE
    assert poster.requests == []


def test_the_credential_source_never_renders_a_value():
    source = _credentials()
    rendered = repr(source)
    assert OPENAI_KEY not in rendered
    assert ANTHROPIC_KEY not in rendered
    assert "redacted" in rendered
    assert source.is_configured(ProviderFamily.OPENAI)
    assert source.is_configured(ProviderFamily.DEEPSEEK) is False


def test_the_credential_reaches_only_the_authorization_header():
    poster = MockPoster(body=_openai_body())
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    response = transport(_wire())
    sent = poster.requests[0]
    assert sent.headers["Authorization"] == f"Bearer {OPENAI_KEY}"
    # The credential appears in no other header, and never in the body.
    assert sum(OPENAI_KEY in value for value in sent.headers.values()) == 1
    assert OPENAI_KEY not in json.dumps(sent.body)
    # Nor does it appear in the returned record or its reference.
    assert OPENAI_KEY not in repr(response)
    assert OPENAI_KEY not in (response.raw_response_ref or "")


def test_the_credential_safe_view_redacts_the_header():
    poster = MockPoster(body=_openai_body())
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    transport(_wire())
    view = poster.requests[0].credential_safe_view()
    assert view["headers"]["Authorization"] == "<redacted>"
    assert OPENAI_KEY not in json.dumps(view)


def test_the_anthropic_credential_uses_the_api_key_header():
    poster = MockPoster(body=_anthropic_body())
    transport = AnthropicTransport(
        family=ProviderFamily.ANTHROPIC,
        model="claude-sonnet-5",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC],
    )
    transport(_wire("claude-sonnet-5"))
    sent = poster.requests[0]
    assert sent.headers["x-api-key"] == ANTHROPIC_KEY
    assert sent.headers["anthropic-version"] == ANTHROPIC_VERSION_EXPECTED
    assert "Authorization" not in sent.headers


ANTHROPIC_VERSION_EXPECTED = ANTHROPIC_API_VERSION


# ------------------------------------------------- IC-008: wire contract


def test_the_openai_adapter_pins_the_model_and_reasoning_effort():
    poster = MockPoster(body=_openai_body())
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
        reasoning_effort="low",
    )
    transport(_wire())
    body = poster.requests[0].body
    assert body["model"] == "gpt-5.6-terra"
    assert body["reasoning"] == {"effort": "low"}
    assert body["max_output_tokens"] == 256
    assert body["instructions"] == "role prompt"
    assert body["store"] is False
    assert body["tools"] == []
    assert "messages" not in body
    assert poster.requests[0].url == "https://api.openai.com/v1/responses"
    assert poster.requests[0].url == ALLOWED_ENDPOINTS[ProviderFamily.OPENAI]


def test_the_anthropic_adapter_pins_the_model_and_api_version():
    poster = MockPoster(body=_anthropic_body())
    transport = AnthropicTransport(
        family=ProviderFamily.ANTHROPIC,
        model="claude-sonnet-5",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC],
    )
    transport(_wire("claude-sonnet-5"))
    body = poster.requests[0].body
    assert body["model"] == "claude-sonnet-5"
    assert body["max_tokens"] == 256
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert poster.requests[0].headers["anthropic-version"] == ANTHROPIC_API_VERSION


def test_the_adapter_reports_the_served_model_from_the_payload():
    """The served identity comes from the payload, so a substitution is visible."""
    poster = MockPoster(body=_openai_body(model="some-other-model"))
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    response = transport(_wire())
    assert response.reported_model == "some-other-model"
    assert response.reported_model != "gpt-5.6-terra"


def test_the_adapter_records_token_usage_and_unknown_cost():
    poster = MockPoster(body=_openai_body())
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    response = transport(_wire())
    assert response.input_tokens == 120
    assert response.output_tokens == 40
    # The adapter does not invent a cost; pricing is configuration.
    assert response.cost_completeness is CostCompleteness.UNKNOWN
    assert response.estimated_cost_microunits is None


def test_configured_price_book_records_measured_provider_cost():
    book = PriceBook(
        (
            TokenPrice(
                provider="openai",
                model="gpt-5.6-terra",
                microunits_per_million_input_tokens=2_000_000,
                microunits_per_million_output_tokens=12_000_000,
            ),
        )
    )
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(body=_openai_body()),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
        price_book=book,
    )
    response = transport(_wire())
    assert response.estimated_cost_microunits == 720
    assert response.cost_completeness is CostCompleteness.COMPLETE


def test_unknown_served_model_keeps_cost_unknown_even_with_price_book():
    book = PriceBook(
        (
            TokenPrice(
                provider="openai",
                model="gpt-5.6-terra",
                microunits_per_million_input_tokens=2_000_000,
                microunits_per_million_output_tokens=12_000_000,
            ),
        )
    )
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(body=_openai_body(model="unexpected-model")),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
        price_book=book,
    )
    response = transport(_wire())
    assert response.estimated_cost_microunits is None
    assert response.cost_completeness is CostCompleteness.UNKNOWN


def test_the_recorded_reference_carries_no_payload_text():
    """A reference must not smuggle model text, which could echo anything."""
    poster = MockPoster(body=_openai_body(text='{"echoed":"ignore previous"}'))
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    response = transport(_wire())
    assert "echoed" not in (response.raw_response_ref or "")
    assert response.raw_response_ref == "openai:http-200"


# ------------------------------------------------- IC-008: failure mapping


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        (401, ProviderFailureClass.AUTH_FAILURE),
        (403, ProviderFailureClass.AUTH_FAILURE),
        (429, ProviderFailureClass.RATE_LIMIT),
        (408, ProviderFailureClass.TIMEOUT),
        (504, ProviderFailureClass.TIMEOUT),
        (500, ProviderFailureClass.PROVIDER_UNAVAILABLE),
        (503, ProviderFailureClass.PROVIDER_UNAVAILABLE),
        (400, ProviderFailureClass.POLICY_REJECTION),
        (422, ProviderFailureClass.POLICY_REJECTION),
    ),
)
def test_http_statuses_become_typed_failure_classes(status, expected):
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(status_code=status),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is expected


def test_an_unclassified_status_becomes_an_internal_error_not_a_silent_pass():
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(status_code=302),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is ProviderFailureClass.INTERNAL_ERROR


def test_a_non_json_body_is_a_malformed_response():
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(body="<html>not json</html>"),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is ProviderFailureClass.MALFORMED_RESPONSE


def test_a_body_missing_its_content_is_a_malformed_response():
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(
            body=json.dumps(
                {
                    "model": "gpt-5.6-terra",
                    "status": "completed",
                    "output": [{"type": "reasoning", "summary": []}],
                }
            )
        ),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is ProviderFailureClass.MALFORMED_RESPONSE


def test_an_incomplete_openai_response_is_not_admitted_as_an_opinion():
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=MockPoster(body=_openai_body(status="incomplete")),
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    with pytest.raises(ProviderInvocationError) as error:
        transport(_wire())
    assert error.value.failure_class is ProviderFailureClass.MALFORMED_RESPONSE


def test_openai_output_text_is_found_after_non_message_items():
    poster = MockPoster(body=_openai_body(text='{"assessment":"NEUTRAL"}'))
    transport = OpenAITransport(
        family=ProviderFamily.OPENAI,
        model="gpt-5.6-terra",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
    )
    assert transport(_wire()).text == '{"assessment":"NEUTRAL"}'


def test_the_anthropic_adapter_joins_text_blocks():
    poster = MockPoster(
        body=json.dumps(
            {
                "model": "claude-sonnet-5",
                "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}],
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }
        )
    )
    transport = AnthropicTransport(
        family=ProviderFamily.ANTHROPIC,
        model="claude-sonnet-5",
        poster=poster,
        credentials=_credentials(),
        endpoint=ALLOWED_ENDPOINTS[ProviderFamily.ANTHROPIC],
    )
    assert transport(_wire("claude-sonnet-5")).text == "ab"


class _FakeHttpStream:
    def __init__(self, body: bytes, *, status: int = 200) -> None:
        self._body = body
        self._status = status

    def read(self, amount: int) -> bytes:
        return self._body[:amount]

    def getcode(self) -> int:
        return self._status


class _FakeOpener:
    def __init__(self, response: _FakeHttpStream) -> None:
        self.response = response
        self.calls = []

    def open(self, request, timeout):
        self.calls.append((request, timeout))
        return self.response


def test_real_http_poster_refuses_a_non_governed_destination_before_opening():
    opener = _FakeOpener(_FakeHttpStream(b"{}"))
    poster = StdlibHttpPoster(opener=opener, now=lambda: NOW)
    with pytest.raises(EgressDeniedError, match="non-governed destination"):
        poster(
            HttpRequest(
                url="https://example.invalid/v1",
                headers={"Content-Type": "application/json"},
                body={"safe": True},
                timeout_seconds=3,
            )
        )
    assert opener.calls == []


def test_real_http_poster_serializes_only_the_supplied_request_and_bounds_time():
    opener = _FakeOpener(_FakeHttpStream(b'{"ok":true}'))
    poster = StdlibHttpPoster(opener=opener, now=lambda: NOW)
    response = poster(
        HttpRequest(
            url=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
            headers={"Content-Type": "application/json"},
            body={"b": 2, "a": 1},
            timeout_seconds=7,
        )
    )
    request, timeout = opener.calls[0]
    assert request.full_url == ALLOWED_ENDPOINTS[ProviderFamily.OPENAI]
    assert request.get_method() == "POST"
    assert request.data == b'{"a":1,"b":2}'
    assert timeout == 7
    assert response.body_text == '{"ok":true}'
    assert response.received_at == NOW


def test_real_http_poster_refuses_an_oversized_response():
    opener = _FakeOpener(
        _FakeHttpStream(b"x" * (MAX_HTTP_RESPONSE_BYTES + 1))
    )
    poster = StdlibHttpPoster(opener=opener, now=lambda: NOW)
    with pytest.raises(ValueError, match="exceeds"):
        poster(
            HttpRequest(
                url=ALLOWED_ENDPOINTS[ProviderFamily.OPENAI],
                headers={"Content-Type": "application/json"},
                body={"safe": True},
                timeout_seconds=3,
            )
        )


# ------------------------------------------------- IC-008: mode stays off


def test_the_approved_build_makes_transports_without_enabling_the_plane():
    """Building transports must not switch the plane on."""
    transports = build_approved_transports(
        poster=MockPoster(),
        credentials=_credentials(),
        models=APPROVED_SHADOW_MODELS,
    )
    assert set(transports) == {ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC}
    from app.opip.committee.settings import CommitteeShadowSettings

    assert committee_shadow_enabled(CommitteeShadowSettings()) is False


def test_no_transport_is_constructed_by_importing_the_module():
    """Importing must not read the environment or build anything."""
    import importlib

    module = importlib.import_module("app.opip.committee.transports")
    assert not hasattr(module, "_DEFAULT_TRANSPORTS")


# ------------------------------------------------- the UTC daily ceiling


def test_the_daily_ceiling_admits_and_refuses_correctly():
    from app.opip.committee.daily_ceiling import (
        DailyCeiling,
        DailyCeilingExceededError,
        InMemoryDailySpendStore,
        UnboundedReservationError,
    )

    store = InMemoryDailySpendStore()
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=store,
        now=lambda: NOW,
    )
    assert ceiling.remaining_today() == APPROVED_MAX_DAILY_COST_MICROUNITS
    ceiling.admit(estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS, at=NOW)
    assert ceiling.remaining_today() == (
        APPROVED_MAX_DAILY_COST_MICROUNITS - APPROVED_MAX_CASE_COST_MICROUNITS
    )
    # An unbounded reservation is refused rather than assumed to fit.
    with pytest.raises(UnboundedReservationError, match="cannot be bounded"):
        ceiling.admit(estimated_cost_microunits=None, at=NOW)
    # The twentieth $0.50 case would breach $10.
    with pytest.raises(DailyCeilingExceededError, match="would be exceeded"):
        ceiling.admit(estimated_cost_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS, at=NOW)


def test_the_daily_ceiling_resets_at_the_utc_day_boundary():
    from app.opip.committee.daily_ceiling import (
        DailyCeiling,
        InMemoryDailySpendStore,
        utc_day_of,
    )

    store = InMemoryDailySpendStore()
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
        store=store,
        now=lambda: LATER,
    )
    ceiling.admit(estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS, at=LATER)
    assert ceiling.remaining_today() == 0
    # The next UTC day has its own full ceiling.
    assert utc_day_of(LATER) == date(2026, 9, 23)
    assert utc_day_of(NEXT_DAY) == date(2026, 9, 24)
    tomorrow = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
        store=store,
        now=lambda: NEXT_DAY,
    )
    assert tomorrow.remaining_today() == APPROVED_MAX_CASE_COST_MICROUNITS


def test_the_daily_ceiling_survives_a_restart():
    from app.opip.committee.daily_ceiling import DailyCeiling, InMemoryDailySpendStore

    store = InMemoryDailySpendStore()
    DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=store,
        now=lambda: NOW,
    ).admit(estimated_cost_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS, at=NOW)
    # A fresh ceiling over the same durable store must still see the spend.
    restarted = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=store,
        now=lambda: NOW,
    )
    assert restarted.remaining_today() == 0


def test_settling_charges_the_greater_of_reservation_and_report():
    """An under-estimate cannot escape the ceiling."""
    from app.opip.committee.daily_ceiling import DailyCeiling, InMemoryDailySpendStore

    store = InMemoryDailySpendStore()
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=store,
        now=lambda: NOW,
    )
    ceiling.admit(estimated_cost_microunits=40, at=NOW)
    record = ceiling.settle(reserved_microunits=40, reported_microunits=1_000, at=NOW)
    assert record.spent_microunits == 1_000
    # An unknown report keeps the reservation rather than crediting a refund.
    ceiling.admit(estimated_cost_microunits=100, at=NOW)
    kept = ceiling.settle(reserved_microunits=100, reported_microunits=None, at=NOW)
    assert kept.spent_microunits == 1_100


def test_the_daily_ceiling_requires_a_valid_declared_limit():
    from app.opip.committee.daily_ceiling import DailyCeiling, InMemoryDailySpendStore

    with pytest.raises(ValueError, match="non-negative integer"):
        DailyCeiling(
            max_daily_microunits=-1,
            store=InMemoryDailySpendStore(),
            now=lambda: NOW,
        )
