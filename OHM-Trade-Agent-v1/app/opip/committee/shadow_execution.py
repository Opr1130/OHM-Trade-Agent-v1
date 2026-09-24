"""Governed real-provider execution for Committee SHADOW cases.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module closes the runtime wiring gap without changing deployment state. It
binds the already-approved provider transports to a generic HTTPS client, supplies
a conservative pre-flight cost bound, reserves the UTC daily ceiling before any
provider call, and persists call/case outcomes only in the Committee evidence store.

Nothing here enables the plane. CommitteeRunner still refuses unless explicit
settings resolve to shadow.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Mapping

import httpx

from app.opip.committee.contracts import CommitteeCase, ProviderFamily
from app.opip.committee.daily_ceiling import DailyCeiling, FileDailySpendStore
from app.opip.committee.ledger import DurableObservationLedger
from app.opip.committee.providers import (
    CommitteeProvider,
    ProviderWireRequest,
    TransportBackedProvider,
)
from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_DAILY_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_SHADOW_MODELS,
    approved_price_book,
)
from app.opip.committee.runtime import CommitteeRunResult, CommitteeRunner
from app.opip.committee.settings import committee_shadow_enabled
from app.opip.committee.store import CommitteeEvidenceStore
from app.opip.committee.transports import (
    ALLOWED_ENDPOINTS,
    CredentialSource,
    EgressDeniedError,
    EnvironmentCredentialSource,
    HttpPoster,
    HttpRequest,
    HttpResponse,
    build_approved_transports,
)

MAX_PROVIDER_RESPONSE_BYTES = 1_048_576
REQUEST_FRAMING_TOKEN_BOUND = 4_096


class ShadowExecutionError(RuntimeError):
    """The governed SHADOW executor refused to perform a provider call."""


class HttpxPoster:
    """Exact-endpoint HTTPS POST implementation with no redirects."""

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.url not in frozenset(ALLOWED_ENDPOINTS.values()):
            raise EgressDeniedError(
                "committee HTTP egress is restricted to the declared provider endpoints"
            )
        response = self._client.post(
            request.url,
            headers=dict(request.headers),
            json=dict(request.body),
            timeout=request.timeout_seconds,
            follow_redirects=False,
        )
        content = response.content
        if len(content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ValueError(
                f"provider response exceeds {MAX_PROVIDER_RESPONSE_BYTES} bytes"
            )
        return HttpResponse(
            status_code=response.status_code,
            body_text=content.decode("utf-8"),
            received_at=datetime.now(timezone.utc),
        )


def conservative_request_cost_microunits(
    *,
    family: ProviderFamily,
    model: str,
    request: ProviderWireRequest,
) -> int | None:
    """Return a deliberately pessimistic request-cost bound."""
    payload = json.dumps(
        dict(request.user_payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    prompt = request.system_prompt.encode("utf-8")
    input_token_bound = len(payload) + len(prompt) + REQUEST_FRAMING_TOKEN_BOUND
    return approved_price_book().cost_microunits(
        provider=family.value,
        model=model,
        input_tokens=input_token_bound,
        output_tokens=request.max_output_tokens,
    )


def build_governed_shadow_providers(
    *,
    poster: HttpPoster,
    credentials: CredentialSource,
) -> Mapping[ProviderFamily, CommitteeProvider]:
    """Bind approved families to transports and conservative estimators."""
    transports = build_approved_transports(
        poster=poster,
        credentials=credentials,
        models=APPROVED_SHADOW_MODELS,
        reasoning_effort="low",
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
    )
    providers: dict[ProviderFamily, CommitteeProvider] = {}
    for family, transport in transports.items():
        model = APPROVED_SHADOW_MODELS[family]

        def estimate(
            request: ProviderWireRequest,
            *,
            bound_family: ProviderFamily = family,
            bound_model: str = model,
        ) -> int | None:
            return conservative_request_cost_microunits(
                family=bound_family,
                model=bound_model,
                request=request,
            )

        providers[family] = TransportBackedProvider(
            family=family,
            model=model,
            transport=transport,
            cost_estimator=estimate,
        )
    return providers


def execute_shadow_case(
    case: CommitteeCase,
    *,
    store: CommitteeEvidenceStore,
    settings: object,
    poster: HttpPoster | None = None,
    credentials: CredentialSource | None = None,
    now: Callable[[], datetime] | None = None,
) -> CommitteeRunResult:
    """Execute one case under both case and UTC-daily spend ceilings."""
    if not committee_shadow_enabled(settings):
        raise ShadowExecutionError(
            "SHADOW execution refused because OPIP_COMMITTEE_MODE is not shadow"
        )
    reservation = case.policy.max_estimated_cost_microunits
    if reservation is None:
        raise ShadowExecutionError(
            "SHADOW execution requires a bounded case cost reservation"
        )

    clock = now or (lambda: datetime.now(timezone.utc))
    moment = clock()
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=FileDailySpendStore(store.root),
        now=clock,
    )
    ceiling.admit(estimated_cost_microunits=reservation, at=moment)

    resolved_poster = poster or HttpxPoster()
    resolved_credentials = credentials or EnvironmentCredentialSource()
    providers = build_governed_shadow_providers(
        poster=resolved_poster,
        credentials=resolved_credentials,
    )
    runner = CommitteeRunner(
        providers=providers,
        ledger=DurableObservationLedger(store=store),
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
        now=clock,
        settings=settings,
    )
    result = runner.run_case(case)
    store.append_case_outcome(result.case_outcome)

    reported = sum(
        outcome.charge_microunits or outcome.estimated_microunits_reported()
        for outcome in result.case_outcome.outcomes
    )
    ceiling.settle(
        reserved_microunits=reservation,
        reported_microunits=reported,
        at=moment,
    )
    return result


__all__ = [
    "MAX_PROVIDER_RESPONSE_BYTES",
    "REQUEST_FRAMING_TOKEN_BOUND",
    "HttpxPoster",
    "ShadowExecutionError",
    "build_governed_shadow_providers",
    "conservative_request_cost_microunits",
    "execute_shadow_case",
]
