"""Credentialled SHADOW transport canary.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This module validates the two OWNER-approved provider transports with one synthetic,
non-trading Committee case. It is deliberately separate from the recurring shadow
worker: running the canary does not enable a timer, consume production evidence,
change OPIP_COMMITTEE_MODE on the host, or create any trading authority.

The synthetic case is content-stable for a release SHA so a retry after a process
crash reuses the durable logical observation ids instead of multiplying provider
calls. Provider credentials are read only through EnvironmentCredentialSource and
are never rendered in the result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from app.opip.committee.contracts import (
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    CostCompleteness,
    EvaluationPhase,
    ObservationStatus,
    ProviderFamily,
)
from app.opip.committee.daily_ceiling import DailyCeiling, FileDailySpendStore
from app.opip.committee.evidence import build_evidence_item, build_evidence_snapshot
from app.opip.committee.providers import (
    CommitteeProvider,
    TransportBackedProvider,
)
from app.opip.committee.registry import (
    APPROVED_DEADLINE_SECONDS,
    APPROVED_MAX_CASE_COST_MICROUNITS,
    APPROVED_MAX_DAILY_COST_MICROUNITS,
    APPROVED_MAX_OUTPUT_TOKENS,
    APPROVED_SHADOW_MODELS,
    APPROVED_SHADOW_REASONING_MODE,
    approved_price_book,
)
from app.opip.committee.runtime import CommitteeRunner
from app.opip.committee.settings import (
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
)
from app.opip.committee.store import CommitteeEvidenceStore, DurableObservationLedger
from app.opip.committee.transports import (
    CredentialSource,
    EnvironmentCredentialSource,
    HttpPoster,
    StdlibHttpPoster,
    build_approved_transports,
)
from app.opip.decision_intelligence.identity import Provenance

CANARY_POLICY_VERSION = "committee-credential-canary-v1"
CANARY_PROMPT_TEMPLATE_ID = "committee.credential-canary.v1"
CANARY_PROMPT_VERSION = "1"
CANARY_FIXTURE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
CANARY_PROVIDER_FAMILIES = (ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC)
CANARY_SEAT_RESERVATION_MICROUNITS = (
    APPROVED_MAX_CASE_COST_MICROUNITS // len(CANARY_PROVIDER_FAMILIES)
)
_EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")


class CredentialCanaryError(RuntimeError):
    """The credentialled canary could not prove the approved transport contract."""


@dataclass(frozen=True)
class CredentialCanarySeat:
    provider: str
    requested_model: str
    served_model: str
    status: str
    input_tokens: int
    output_tokens: int
    cost_microunits: int

    def safe_view(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "requested_model": self.requested_model,
            "served_model": self.served_model,
            "status": self.status,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_microunits": self.cost_microunits,
        }


@dataclass(frozen=True)
class CredentialCanaryResult:
    release_sha: str
    case_id: str
    seats: tuple[CredentialCanarySeat, ...]
    duplicate: bool = False

    @property
    def total_measured_cost_microunits(self) -> int:
        return sum(seat.cost_microunits for seat in self.seats)

    def safe_view(self) -> dict[str, object]:
        return {
            "canary_proof": "PASS",
            "release_sha": self.release_sha,
            "case_id": self.case_id,
            "duplicate": self.duplicate,
            "total_measured_cost_microunits": self.total_measured_cost_microunits,
            "seats": [seat.safe_view() for seat in self.seats],
        }


def _case_id(release_sha: str) -> str:
    return f"committee-credential-canary-{release_sha}"


def _build_case(release_sha: str) -> CommitteeCase:
    case_id = _case_id(release_sha)
    item = build_evidence_item(
        evidence_id="CANARY-EVIDENCE-1",
        source_id="synthetic-credential-canary",
        available_at=CANARY_FIXTURE_TIME,
        payload={
            "metric_name": "transport_contract_fixture",
            "metric_value": "1",
            "metric_unit": "synthetic",
            "note": (
                "Synthetic provider connectivity validation only. "
                "Contains no market, account, order, or trading data."
            ),
        },
        evidence_cutoff_at=CANARY_FIXTURE_TIME,
    )
    snapshot = build_evidence_snapshot(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=CANARY_FIXTURE_TIME,
        assembled_at=CANARY_FIXTURE_TIME,
        items=(item,),
        source_refs=("synthetic:credential-canary:v1",),
        committee_policy_version=CANARY_POLICY_VERSION,
        prompt_template_id=CANARY_PROMPT_TEMPLATE_ID,
        prompt_version=CANARY_PROMPT_VERSION,
        instrument_id=None,
    )
    policy = CommitteePolicy(
        policy_version=CANARY_POLICY_VERSION,
        seated_providers=CANARY_PROVIDER_FAMILIES,
        prompt_template_id=CANARY_PROMPT_TEMPLATE_ID,
        prompt_version=CANARY_PROMPT_VERSION,
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
    )
    return CommitteeCase(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=CANARY_FIXTURE_TIME,
        provenance=Provenance(
            producing_component="app.opip.committee.credential_canary",
            artifact_or_build_id=release_sha,
            process_instance_id=f"credential-canary-{release_sha[:12]}",
            emitted_at=CANARY_FIXTURE_TIME,
            source_record_refs=("synthetic:credential-canary:v1",),
        ),
        instrument_id=None,
    )


def _providers(
    *,
    poster: HttpPoster,
    credentials: CredentialSource,
) -> Mapping[ProviderFamily, CommitteeProvider]:
    price_book = approved_price_book()
    transports = build_approved_transports(
        poster=poster,
        credentials=credentials,
        models=APPROVED_SHADOW_MODELS,
        reasoning_effort=APPROVED_SHADOW_REASONING_MODE.value.lower(),
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
        price_book=price_book,
    )

    def reserve(_: object) -> int:
        return CANARY_SEAT_RESERVATION_MICROUNITS

    return {
        family: TransportBackedProvider(
            family=family,
            model=APPROVED_SHADOW_MODELS[family],
            transport=transports[family],
            cost_estimator=reserve,
        )
        for family in CANARY_PROVIDER_FAMILIES
    }


def _validate_credentials(credentials: CredentialSource) -> None:
    missing = [
        family.value
        for family in CANARY_PROVIDER_FAMILIES
        if not credentials.is_configured(family)
    ]
    if missing:
        raise CredentialCanaryError(
            "required provider credential is absent for: " + ",".join(missing)
        )


def _seat_from_outcome(outcome) -> CredentialCanarySeat:
    if outcome.status not in (ObservationStatus.COMPLETED, ObservationStatus.DUPLICATE_OK):
        failure = None if outcome.failure_class is None else outcome.failure_class.value
        raise CredentialCanaryError(
            f"provider {outcome.provider_family.value} did not complete "
            f"(status={outcome.status.value}, failure={failure or 'none'})"
        )
    if outcome.reported_model != APPROVED_SHADOW_MODELS[outcome.provider_family]:
        raise CredentialCanaryError(
            f"provider {outcome.provider_family.value} served an unapproved model"
        )
    if outcome.cost_completeness is not CostCompleteness.COMPLETE:
        raise CredentialCanaryError(
            f"provider {outcome.provider_family.value} did not report complete cost"
        )
    if (
        outcome.input_tokens is None
        or outcome.output_tokens is None
        or outcome.estimated_cost_microunits is None
    ):
        raise CredentialCanaryError(
            f"provider {outcome.provider_family.value} omitted usage accounting"
        )
    return CredentialCanarySeat(
        provider=outcome.provider_family.value,
        requested_model=outcome.requested_model,
        served_model=outcome.reported_model,
        status=outcome.status.value,
        input_tokens=outcome.input_tokens,
        output_tokens=outcome.output_tokens,
        cost_microunits=outcome.estimated_cost_microunits,
    )


def _existing_result(
    *, store: CommitteeEvidenceStore, release_sha: str
) -> CredentialCanaryResult | None:
    case_id = _case_id(release_sha)
    for outcome in store.iter_case_outcomes():
        if outcome.case_id != case_id:
            continue
        seats = tuple(_seat_from_outcome(item) for item in outcome.outcomes)
        return CredentialCanaryResult(
            release_sha=release_sha,
            case_id=case_id,
            seats=seats,
            duplicate=True,
        )
    return None


def run_credential_canary(
    *,
    release_sha: str,
    root: Path,
    poster: HttpPoster | None = None,
    credentials: CredentialSource | None = None,
    now: Callable[[], datetime] | None = None,
) -> CredentialCanaryResult:
    """Run at most one real attempt per approved provider for this release."""

    if not _EXACT_SHA.fullmatch(release_sha):
        raise CredentialCanaryError("release_sha must be exactly 40 lowercase hex characters")
    root = Path(root)
    store = CommitteeEvidenceStore(root=root)
    existing = _existing_result(store=store, release_sha=release_sha)
    if existing is not None:
        return existing

    credential_source = credentials or EnvironmentCredentialSource()
    _validate_credentials(credential_source)
    clock = now or (lambda: datetime.now(timezone.utc))
    moment = clock()
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise CredentialCanaryError("canary clock must return a timezone-aware datetime")

    # Reserve the full approved case ceiling durably before either provider can run.
    # A crash therefore cannot erase spend that may already have happened.
    DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=FileDailySpendStore(root),
        now=lambda: moment,
    ).admit(
        estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
        at=moment,
    )

    runner = CommitteeRunner(
        providers=_providers(
            poster=poster or StdlibHttpPoster(),
            credentials=credential_source,
        ),
        ledger=DurableObservationLedger(store=store),
        max_output_tokens=APPROVED_MAX_OUTPUT_TOKENS,
        timeout_seconds=APPROVED_DEADLINE_SECONDS,
        now=clock,
        settings=CommitteeShadowSettings(
            opip_committee_mode=COMMITTEE_MODE_SHADOW,
            opip_committee_max_estimated_cost_microunits=(
                APPROVED_MAX_CASE_COST_MICROUNITS
            ),
        ),
    )
    result = runner.run_case(
        _build_case(release_sha),
        phase=EvaluationPhase.RETROSPECTIVE,
    )
    store.append_case_outcome(result.case_outcome)
    seats = tuple(_seat_from_outcome(seat.outcome) for seat in result.seats)
    if {seat.provider for seat in seats} != {
        ProviderFamily.OPENAI.value,
        ProviderFamily.ANTHROPIC.value,
    }:
        raise CredentialCanaryError("canary did not prove both approved provider families")
    return CredentialCanaryResult(
        release_sha=release_sha,
        case_id=result.case.case_id,
        seats=seats,
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the credentialled Committee canary")
    parser.add_argument("release_sha")
    parser.add_argument(
        "--root",
        default="/var/lib/opip-committee/credential-canary",
    )
    args = parser.parse_args()
    try:
        result = run_credential_canary(
            release_sha=args.release_sha,
            root=Path(args.root),
        )
    except CredentialCanaryError as exc:
        print(json.dumps({"canary_proof": "FAIL", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result.safe_view(), sort_keys=True))
    print("CREDENTIAL_CANARY_PROOF=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CANARY_POLICY_VERSION",
    "CANARY_PROVIDER_FAMILIES",
    "CANARY_SEAT_RESERVATION_MICROUNITS",
    "CredentialCanaryError",
    "CredentialCanaryResult",
    "CredentialCanarySeat",
    "run_credential_canary",
]
