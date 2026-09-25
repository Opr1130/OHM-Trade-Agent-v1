"""Durable role-governed executor for bounded Committee SHADOW cases.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module closes the scheduler -> governed role runtime seam. The orchestration
it performs is the governed seven-role path: each role is resolved through the
approved model registry, executed through :class:`RoleRouter` under its governed
budget, recorded as an attributable role result, and aggregated into a durable
role-governed case outcome by the designated synthesizer role.

Construction is explicit and fail-closed:

* OFF mode refuses before credentials are read;
* both approved provider credentials must be present;
* the registry approval window is mandatory, so an approved route cannot become
  permanently approved by default;
* requests are bounded before a cost reservation is returned;
* every role result and the case outcome are written to the append-only
  Committee store.

Importing this module does not read credentials and does not open a socket.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.opip.committee.contracts import (
    CommitteeCase,
    EvaluationPhase,
    ProviderFamily,
)
from app.opip.committee.pricing import PriceBook
from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_REASONING_MODE,
    approved_price_book,
)
from app.opip.committee.role_runtime import (
    RoleGovernedRunner,
    build_approved_shadow_registry,
    build_role_providers,
)
from app.opip.committee.settings import (
    CommitteeShadowSettings,
    committee_shadow_enabled,
    resolve_committee_cost_ceiling,
    resolve_committee_mode,
)
from app.opip.committee.store import CommitteeEvidenceStore, DurableRoleResultLedger
from app.opip.committee.transports import (
    EnvironmentCredentialSource,
    HttpPoster,
    UrllibHttpPoster,
    build_approved_transports,
)

#: Hard cap on the already-screened logical request before it is handed to a vendor.
#: The role runtime applies its own identical bound; this is the independent
#: pre-flight bound used for the reservation estimate.
MAX_SCREENED_REQUEST_BYTES = 16 * 1024

#: Deliberately pessimistic conversion used only for the pre-flight reservation.
#: Four tokens per UTF-8 byte plus a fixed protocol allowance is far more conservative
#: than the approved providers' normal tokenisation, so a reservation cannot be made
#: optimistic merely because a tokenizer is unavailable locally.
INPUT_TOKEN_SAFETY_MULTIPLIER = 4
PROTOCOL_TOKEN_ALLOWANCE = 4_096

#: Two approved provider families back the seven governed roles. Both must be
#: configured; a silent single-vendor committee is refused rather than run.
APPROVED_PROVIDER_FAMILIES = (
    ProviderFamily.OPENAI,
    ProviderFamily.ANTHROPIC,
)

#: The pre-flight share of the approved case ceiling used by
#: :func:`conservative_cost_reservation`. The role runtime budgets per role
#: instead; this remains the single-request bound.
SEAT_RESERVATION_MICROUNITS = (
    APPROVED_MAX_CASE_COST_MICROUNITS // len(APPROVED_PROVIDER_FAMILIES)
)

#: The activation boundary is also the registry's approval instant: routes become
#: effective exactly when SHADOW is authorised, and the review date is explicit so
#: "approved once" cannot silently mean "approved forever".
SHADOW_APPROVED_FROM_ENV = "OPIP_COMMITTEE_SHADOW_NOT_BEFORE"
REGISTRY_REVIEW_BY_ENV = "OPIP_COMMITTEE_REGISTRY_REVIEW_BY"


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


def _required_utc_moment(environ: Mapping[str, str], name: str) -> datetime:
    """Parse a mandatory timezone-aware ISO-8601 instant from the environment."""
    raw = environ.get(name)
    if raw is None or not str(raw).strip():
        raise ShadowExecutorConfigurationError(
            f"{name} is required to activate the governed SHADOW registry"
        )
    try:
        parsed = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ShadowExecutorConfigurationError(
            f"{name} is not an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ShadowExecutorConfigurationError(
            f"{name} must be timezone-aware; a naive timestamp cannot identify an "
            "activation instant"
        )
    return parsed.astimezone(timezone.utc)


class ShadowCaseExecutor:
    """Execute and durably persist one role-governed advisory Committee case."""

    def __init__(
        self,
        *,
        committee_home: Path,
        registry,
        providers: Mapping[str, Any],
        price_book: PriceBook,
        settings: CommitteeShadowSettings,
        now: Any | None = None,
        monotonic: Any | None = None,
    ) -> None:
        if not committee_shadow_enabled(settings):
            raise ShadowExecutorConfigurationError(
                "credentialled Committee executor requires explicit SHADOW mode"
            )
        self._store = CommitteeEvidenceStore(root=Path(committee_home))
        self._ledger = DurableRoleResultLedger(store=self._store)
        self._runner = RoleGovernedRunner(
            registry=registry,
            providers=providers,
            ledger=self._ledger,
            price_book=price_book,
            now=now,
            monotonic=monotonic,
            settings=settings,
        )

    def __call__(self, case: CommitteeCase) -> bool:
        if not isinstance(case, CommitteeCase):
            raise ShadowExecutorConfigurationError(
                "shadow executor requires a CommitteeCase"
            )
        # The runner records each executed role result durably through the
        # idempotency ledger; only the aggregate case outcome is appended here.
        result = self._runner.run_case(case, phase=EvaluationPhase.PROSPECTIVE)
        self._store.append_role_case_outcome(result.case_outcome)
        # A case that could not seat every required role is durable evidence of an
        # incomplete committee, not a completed one. Reporting it as completed
        # would let the scheduler record committee work that never happened.
        return result.case_outcome.complete


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

    resolved_environ: Mapping[str, str] = (
        _process_environ() if environ is None else environ
    )

    credentials = EnvironmentCredentialSource(environ=resolved_environ)
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

    approved_from = _required_utc_moment(resolved_environ, SHADOW_APPROVED_FROM_ENV)
    review_by = _required_utc_moment(resolved_environ, REGISTRY_REVIEW_BY_ENV)

    price_book = approved_price_book()
    registry = build_approved_shadow_registry(
        approved_from=approved_from,
        review_by=review_by,
    )
    transports = build_approved_transports(
        poster=poster or UrllibHttpPoster(),
        credentials=credentials,
        models=APPROVED_SHADOW_MODELS,
        reasoning_effort=APPROVED_SHADOW_REASONING_MODE.value,
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
        price_book=price_book,
    )
    providers = build_role_providers(
        registry=registry,
        transports=transports,
        available_families=APPROVED_PROVIDER_FAMILIES,
    )
    return ShadowCaseExecutor(
        committee_home=Path(committee_home),
        registry=registry,
        providers=providers,
        price_book=price_book,
        settings=settings,
    )


def _process_environ() -> Mapping[str, str]:
    import os

    return os.environ


__all__ = [
    "APPROVED_PROVIDER_FAMILIES",
    "INPUT_TOKEN_SAFETY_MULTIPLIER",
    "MAX_SCREENED_REQUEST_BYTES",
    "PROTOCOL_TOKEN_ALLOWANCE",
    "REGISTRY_REVIEW_BY_ENV",
    "SEAT_RESERVATION_MICROUNITS",
    "SHADOW_APPROVED_FROM_ENV",
    "ShadowCaseExecutor",
    "ShadowExecutorConfigurationError",
    "build_credentialled_shadow_executor",
    "conservative_cost_reservation",
]
