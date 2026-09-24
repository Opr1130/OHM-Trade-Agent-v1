"""Durable credentialled executor for bounded Committee SHADOW cases.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module closes the scheduler -> governed provider runtime seam. Construction is
explicit and fail-closed: OFF mode refuses before credentials are read, both approved
provider credentials must be present, requests are bounded before a cost reservation
is returned, and every call/case outcome is written to the append-only Committee
store.

Importing this module does not read credentials and does not open a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Mapping

from app.opip.committee.contracts import CommitteeCase, EvaluationPhase, ProviderFamily
from app.opip.committee.pricing import PriceBook
from app.opip.committee.providers import (
    CommitteeProvider,
    TransportBackedProvider,
    UnavailableProvider,
)
from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_REASONING_MODE,
    approved_price_book,
)
from app.opip.committee.runtime import CommitteeRunner
from app.opip.committee.settings import (
    CommitteeShadowSettings,
    committee_shadow_enabled,
    resolve_committee_cost_ceiling,
    resolve_committee_mode,
)
from app.opip.committee.store import CommitteeEvidenceStore, DurableObservationLedger
from app.opip.committee.transports import (
    EnvironmentCredentialSource,
    HttpPoster,
    UrllibHttpPoster,
    build_approved_transports,
)

#: Hard cap on the already-screened logical request before it is handed to a vendor.
#: The producer's real payload is much smaller; this is an independent spend bound.
MAX_SCREENED_REQUEST_BYTES = 16 * 1024

#: Deliberately pessimistic conversion used only for the pre-flight reservation.
#: Four tokens per UTF-8 byte plus a fixed protocol allowance is far more conservative
#: than the approved providers' normal tokenisation, so a reservation cannot be made
#: optimistic merely because a tokenizer is unavailable locally.
INPUT_TOKEN_SAFETY_MULTIPLIER = 4
PROTOCOL_TOKEN_ALLOWANCE = 4_096

#: Two approved provider-family seats share the $0.50 complete-candidate ceiling.
APPROVED_PROVIDER_FAMILIES = (
    ProviderFamily.OPENAI,
    ProviderFamily.ANTHROPIC,
)
SEAT_RESERVATION_MICROUNITS = (
    APPROVED_MAX_CASE_COST_MICROUNITS // len(APPROVED_PROVIDER_FAMILIES)
)


class ShadowExecutorConfigurationError(ValueError):
    """The credentialled executor cannot be constructed safely."""


def _request_bytes(request) -> int:
    encoded = json.dumps(
        {
            "system_prompt": request.system_prompt,
            "user_payload": dict(request.user_payload),
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return len(encoded)


def conservative_cost_reservation(
    *,
    request,
    family: ProviderFamily,
    price_book: PriceBook,
) -> int | None:
    """Return the fixed seat reservation only when a worst-case bound fits it."""
    payload_bytes = _request_bytes(request)
    if payload_bytes > MAX_SCREENED_REQUEST_BYTES:
        return None
    input_token_bound = (
        payload_bytes * INPUT_TOKEN_SAFETY_MULTIPLIER + PROTOCOL_TOKEN_ALLOWANCE
    )
    worst_case = price_book.cost_microunits(
        provider=family.value,
        model=request.model,
        input_tokens=input_token_bound,
        output_tokens=request.max_output_tokens,
    )
    if worst_case is None or worst_case > SEAT_RESERVATION_MICROUNITS:
        return None
    return SEAT_RESERVATION_MICROUNITS


def build_credentialled_providers(
    *,
    credentials: EnvironmentCredentialSource,
    poster: HttpPoster,
    price_book: PriceBook,
) -> Mapping[ProviderFamily, CommitteeProvider]:
    """Build exactly the two approved provider seats, never a silent subset."""
    transports = build_approved_transports(
        poster=poster,
        credentials=credentials,
        models=APPROVED_SHADOW_MODELS,
        reasoning_effort=APPROVED_SHADOW_REASONING_MODE.value,
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
        price_book=price_book,
    )
    providers: dict[ProviderFamily, CommitteeProvider] = {}
    for family in APPROVED_PROVIDER_FAMILIES:
        model = APPROVED_SHADOW_MODELS[family]
        if not credentials.is_configured(family):
            providers[family] = UnavailableProvider(family, model=model)
            continue

        def estimate(request, *, _family=family):
            return conservative_cost_reservation(
                request=request,
                family=_family,
                price_book=price_book,
            )

        providers[family] = TransportBackedProvider(
            family=family,
            model=model,
            transport=transports[family],
            cost_estimator=estimate,
        )
    return providers


class ShadowCaseExecutor:
    """Execute and durably persist one advisory Committee case."""

    def __init__(
        self,
        *,
        committee_home: Path,
        providers: Mapping[ProviderFamily, CommitteeProvider],
        settings: CommitteeShadowSettings,
        now: Callable | None = None,
    ) -> None:
        if not committee_shadow_enabled(settings):
            raise ShadowExecutorConfigurationError(
                "credentialled Committee executor requires explicit SHADOW mode"
            )
        self._store = CommitteeEvidenceStore(root=Path(committee_home))
        self._ledger = DurableObservationLedger(store=self._store)
        self._providers = dict(providers)
        self._settings = settings
        self._now = now

    def __call__(self, case: CommitteeCase) -> bool:
        if not isinstance(case, CommitteeCase):
            raise ShadowExecutorConfigurationError(
                "shadow executor requires a CommitteeCase"
            )
        runner = CommitteeRunner(
            providers=self._providers,
            ledger=self._ledger,
            max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
            timeout_seconds=APPROVED_DEADLINE_SECONDS,
            now=self._now,
            settings=self._settings,
        )
        result = runner.run_case(case, phase=EvaluationPhase.PROSPECTIVE)
        self._store.append_case_outcome(result.case_outcome)
        return True


def build_credentialled_shadow_executor(
    *,
    committee_home: Path,
    environ: Mapping[str, str] | None = None,
    poster: HttpPoster | None = None,
) -> ShadowCaseExecutor:
    """Build the real executor only after runtime mode explicitly resolves SHADOW."""
    mode = resolve_committee_mode()
    ceiling = resolve_committee_cost_ceiling()
    settings = CommitteeShadowSettings(
        opip_committee_mode=mode,
        opip_committee_max_estimated_cost_microunits=(
            APPROVED_MAX_CASE_COST_MICROUNITS if ceiling is None else ceiling
        ),
    )
    if not committee_shadow_enabled(settings):
        # Critical ordering: return before EnvironmentCredentialSource touches env.
        raise ShadowExecutorConfigurationError(
            "credentialled Committee executor is unavailable while mode is OFF"
        )

    credentials = (
        EnvironmentCredentialSource()
        if environ is None
        else EnvironmentCredentialSource(environ=environ)
    )
    missing = [
        family.value
        for family in APPROVED_PROVIDER_FAMILIES
        if not credentials.is_configured(family)
    ]
    if missing:
        raise ShadowExecutorConfigurationError(
            "credentialled SHADOW requires both approved provider credentials; "
            f"missing families: {missing}"
        )

    price_book = approved_price_book()
    providers = build_credentialled_providers(
        credentials=credentials,
        poster=poster or UrllibHttpPoster(),
        price_book=price_book,
    )
    return ShadowCaseExecutor(
        committee_home=committee_home,
        providers=providers,
        settings=settings,
    )


__all__ = [
    "APPROVED_PROVIDER_FAMILIES",
    "INPUT_TOKEN_SAFETY_MULTIPLIER",
    "MAX_SCREENED_REQUEST_BYTES",
    "PROTOCOL_TOKEN_ALLOWANCE",
    "SEAT_RESERVATION_MICROUNITS",
    "ShadowCaseExecutor",
    "ShadowExecutorConfigurationError",
    "build_credentialled_providers",
    "build_credentialled_shadow_executor",
    "conservative_cost_reservation",
]
