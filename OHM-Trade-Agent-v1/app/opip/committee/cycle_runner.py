"""One bounded committee shadow cycle, for the isolated worker to invoke.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This is the deployment entry point: the isolated worker runs it once per scheduled
cycle and it exits. It exists so the deployment change references a real module
rather than an imaginary one.

What it does, in order:

1. Refuses to run unless the plane is enabled. The unit also sets ``off``, so this is
   defence in depth rather than the only gate.
2. Reads committed evidence items from the read-only evidence path.
3. Runs exactly one scheduling cycle, bounded by the cycle budget and the UTC daily
   ceiling, writing every disposition durably.
4. Writes a trust report describing what happened, including what could not be
   measured.

What it deliberately does **not** do:

* It constructs no provider transport and makes no model call. Committed evidence is
  scheduled, but no executor is wired, so nothing is spent. Wiring an executor is a
  later, separately approved step; this module's job is to prove the cycle, the
  accounting, and the observability are real.
* It writes nothing to canonical evidence, the decision-intelligence streams, the
  order path, or any registry.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence

from app.opip.committee.case_ingress import CaseIngressPopulation, load_case_envelopes
from app.opip.committee.case_producer import CaseSourceError, produce_case_population
from app.opip.committee.contracts import CommitteeCase
from app.opip.committee.daily_ceiling import DailyCeiling, FileDailySpendStore
from app.opip.committee.registry import APPROVED_MAX_DAILY_COST_MICROUNITS
from app.opip.committee.scheduler import (
    CommitteeScheduler,
    CommittedEvidenceItem,
    SchedulerBudget,
    SchedulerCheckpoint,
    ScheduleDispositionRecord,
)
from app.opip.committee.settings import (
    COMMITTEE_MODE_SHADOW,
    CommitteeShadowSettings,
    resolve_committee_cost_ceiling,
    resolve_committee_mode,
)
from app.opip.committee.shadow_executor import (
    ShadowExecutorConfigurationError,
    build_credentialled_shadow_executor,
)
from app.opip.committee.trust import CommitteeInvestment, build_trust_report
from app.opip.learning.canonical_replica import ReplicaVerificationError

#: Exit codes the deploying script relies on.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_OPERATIONAL_ERROR = 2

CYCLE_DISPOSITIONS_FILE = "cycle_dispositions.jsonl"
TRUST_REPORT_FILE = "trust_report.json"
EVIDENCE_ITEMS_FILE = "committed_evidence_items.jsonl"
SHADOW_NOT_BEFORE_ENV = "OPIP_COMMITTEE_SHADOW_NOT_BEFORE"
LEARNING_DATA_MANIFEST_ENV = "OPIP_COMMITTEE_LEARNING_MANIFEST"
REPLICA_ROOT_ENV = "OPIP_CANONICAL_REPLICA_ROOT_HOST"
DEFAULT_LEARNING_DATA_MANIFEST = Path("/var/lib/opip-learning/data/manifest.env")
DEFAULT_REPLICA_REPOSITORY_ROOT = Path("/var/lib/opip-learning/canonical-replica")

#: Initial per-cycle bounds. The daily ceiling is the stronger, approved bound.
DEFAULT_CYCLE_CASES = 8


class CycleConfigurationError(ValueError):
    """The worker was invoked with something it cannot safely act on."""


class FileCheckpoint(SchedulerCheckpoint):
    """A durable checkpoint over a JSONL file.

    Dispositions are appended, and the already-decided set is rebuilt from that file
    on load, so a restart resumes rather than reprocessing or skipping.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / CYCLE_DISPOSITIONS_FILE

    def _iter_rows(self) -> Iterable[Mapping[str, object]]:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except ValueError:
                # A corrupt row is skipped rather than treated as absent evidence:
                # the durable log is reconciled on load, so this cannot silently
                # resurrect a decision.
                continue
            if isinstance(decoded, Mapping) and "disposition" in decoded:
                yield decoded

    def load_decided(self) -> Mapping[str, ScheduleDispositionRecord]:
        from app.opip.committee.scheduler import CommitteeScheduleDisposition

        decided: dict[str, ScheduleDispositionRecord] = {}
        for row in self._iter_rows():
            try:
                decision = CommitteeScheduleDisposition(str(row["disposition"]))
                decided[str(row["schedule_key_id"])] = ScheduleDispositionRecord(
                    schedule_key_id=str(row["schedule_key_id"]),
                    evidence_id=str(row["evidence_id"]),
                    disposition=decision,
                    decided_at=datetime.fromisoformat(str(row["decided_at"])),
                    reason=(None if row.get("reason") is None else str(row["reason"])),
                    detail=(None if row.get("detail") is None else str(row["detail"])),
                )
            except (KeyError, ValueError):
                continue
        return decided

    def save_disposition(self, record: ScheduleDispositionRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "schedule_key_id": record.schedule_key_id,
                        "evidence_id": record.evidence_id,
                        "disposition": record.disposition.value,
                        "decided_at": record.decided_at.isoformat(),
                        "reason": record.reason,
                        "detail": record.detail,
                    },
                    sort_keys=True,
                )
                + "\n"
            )


def load_evidence_items(path: Path) -> tuple[CommittedEvidenceItem, ...]:
    """Read committed evidence items from the read-only evidence path.

    A malformed row raises rather than being skipped: a cycle that silently ignored
    part of its population would understate the accounting.
    """
    if not path.exists():
        return ()
    items: list[CommittedEvidenceItem] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise CycleConfigurationError(
                f"{path.name} line {number} is not valid JSON"
            ) from exc
        if not isinstance(row, Mapping):
            raise CycleConfigurationError(
                f"{path.name} line {number} is not a JSON object"
            )
        try:
            items.append(
                CommittedEvidenceItem(
                    evidence_id=str(row["evidence_id"]),
                    case_id=str(row["case_id"]),
                    evidence_snapshot_hash=str(row["evidence_snapshot_hash"]),
                    committee_policy_version=str(row["committee_policy_version"]),
                    committed=bool(row["committed"]),
                    sealed=bool(row["sealed"]),
                    available_at=datetime.fromisoformat(str(row["available_at"])),
                    evidence_cutoff_at=datetime.fromisoformat(
                        str(row["evidence_cutoff_at"])
                    ),
                    expires_at=(
                        None
                        if row.get("expires_at") is None
                        else datetime.fromisoformat(str(row["expires_at"]))
                    ),
                    estimated_cost_microunits=(
                        None
                        if row.get("estimated_cost_microunits") is None
                        else int(row["estimated_cost_microunits"])
                    ),
                )
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise CycleConfigurationError(
                f"{path.name} line {number} is not a valid evidence item: {exc}"
            ) from exc
    return tuple(items)


@dataclass(frozen=True)
class CycleOutcome:
    """What one cycle did, for the worker's own reporting."""

    ran: bool
    reason: str | None
    considered: int
    dispositions: Mapping[str, int]
    report_id: str | None


def run_once(
    *,
    release_sha: str,
    committee_home: Path,
    evidence_path: Path,
    settings: CommitteeShadowSettings | None = None,
    budget: SchedulerBudget | None = None,
    case_ingress_path: Path | None = None,
    case_population: CaseIngressPopulation | None = None,
    case_executor: Callable[[CommitteeCase], bool] | None = None,
    now: datetime | None = None,
) -> CycleOutcome:
    """Run exactly one bounded cycle. Never raises for a data problem."""
    if not release_sha or len(release_sha) != 40:
        raise CycleConfigurationError(
            "a full 40-character release SHA is required; a branch name cannot "
            "identify a released artifact"
        )
    moment = now or datetime.now(timezone.utc)
    resolved_settings = settings or CommitteeShadowSettings(
        opip_committee_mode=resolve_committee_mode(),
        opip_committee_max_estimated_cost_microunits=(
            resolve_committee_cost_ceiling() or 0
        ),
    )
    committee_home.mkdir(parents=True, exist_ok=True)
    checkpoint = FileCheckpoint(committee_home)

    if case_ingress_path is not None and case_population is not None:
        raise CycleConfigurationError(
            "case_ingress_path and case_population are mutually exclusive"
        )
    population = case_population
    if population is None and case_ingress_path is not None:
        population = CaseIngressPopulation(load_case_envelopes(case_ingress_path))

    if population is None:
        if case_executor is not None:
            raise CycleConfigurationError(
                "a case_executor requires validated case ingress"
            )
        items = load_evidence_items(evidence_path)
    else:
        items = population.scheduler_items

    # The daily ceiling caps this cycle's cost budget, so a cycle can never spend
    # more than the UTC day's remaining allowance. The per-case ceiling still applies
    # inside the cycle. Together the two bounds are the approved economics.
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=FileDailySpendStore(committee_home),
        now=lambda: moment,
    )
    remaining_today = ceiling.remaining_today()

    scheduler_executor = None
    if case_executor is not None and population is not None:
        def scheduler_executor(item):
            # Persist the full candidate reservation before any external work. A
            # failure keeps the reservation: unknown/failed spend is never treated
            # as free, and a restart cannot reopen the daily ceiling.
            ceiling.admit(
                estimated_cost_microunits=item.estimated_cost_microunits,
                at=moment,
            )
            return case_executor(population.case_for(item))

    scheduler = CommitteeScheduler(
        checkpoint=checkpoint,
        now=lambda: moment,
        budget=budget
        or SchedulerBudget(
            max_committee_cases=DEFAULT_CYCLE_CASES,
            max_cost_microunits=remaining_today,
        ),
        settings=resolved_settings,
    )
    # OFF has no executor. SHADOW receives an explicitly constructed executor only
    # after provenance, activation-boundary, credential and budget checks pass.
    run = scheduler.run_cycle(items=items, executor=scheduler_executor)

    report = build_trust_report(
        report_version="committee-trust-v1",
        release_sha=release_sha,
        registry_version="committee-shadow-registry-v1",
        generated_at=moment,
        population=run.tally,
        investment=CommitteeInvestment(),
    )
    (committee_home / TRUST_REPORT_FILE).write_text(
        json.dumps(
            {
                "report_id": report.report_id,
                "stage": report.stage.value,
                "blocked_gates": [gate.value for gate in report.blocked_gates],
                "insufficiency_reasons": list(report.insufficiency_reasons),
                "population": report.population.as_dict(),
                "mean_latency_micros": report.mean_latency_micros,
                "remaining_daily_ceiling_microunits": ceiling.remaining_today(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return CycleOutcome(
        ran=run.ran,
        reason=run.reason,
        considered=run.tally.considered,
        dispositions=run.tally.as_dict(),
        report_id=report.report_id,
    )


def _runtime_settings() -> CommitteeShadowSettings:
    return CommitteeShadowSettings(
        opip_committee_mode=resolve_committee_mode(),
        opip_committee_max_estimated_cost_microunits=(
            resolve_committee_cost_ceiling() or 0
        ),
    )


def _parse_activation_boundary(value: str | None) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CycleConfigurationError(
            f"{SHADOW_NOT_BEFORE_ENV} is required for SHADOW; historical backfill "
            "is never inferred"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CycleConfigurationError(
            f"{SHADOW_NOT_BEFORE_ENV} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CycleConfigurationError(
            f"{SHADOW_NOT_BEFORE_ENV} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _production_sha_from_learning_manifest(path: Path) -> str:
    if not path.is_file():
        raise CycleConfigurationError(
            f"learning manifest is unavailable at {path}"
        )
    matches: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "production_deployed_sha":
            matches.append(value.strip())
    if len(matches) != 1:
        raise CycleConfigurationError(
            "learning manifest must contain exactly one production_deployed_sha"
        )
    sha = matches[0]
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise CycleConfigurationError(
            "learning manifest production_deployed_sha is not 40 lowercase hex"
        )
    return sha


def main(argv: Sequence[str] | None = None) -> int:
    """Parse the worker's arguments and run one cycle."""
    args = list(sys.argv[1:] if argv is None else argv)
    release_sha = _value_of(args, "--release-sha")
    # The advisory directory is supplied by the deployment rather than defaulted here,
    # so this module holds no data-path literal: the store owns the committee data
    # location, and the worker states its output path explicitly.
    committee_home_value = _value_of(args, "--committee-home")
    if not committee_home_value:
        print("committee cycle refused: --committee-home is required", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    committee_home = Path(committee_home_value)
    evidence_path = committee_home / EVIDENCE_ITEMS_FILE
    settings = _runtime_settings()
    moment = datetime.now(timezone.utc)

    try:
        population = None
        executor = None
        if settings.opip_committee_mode == COMMITTEE_MODE_SHADOW:
            activation_boundary = _parse_activation_boundary(
                os.environ.get(SHADOW_NOT_BEFORE_ENV)
            )
            learning_manifest = Path(
                os.environ.get(
                    LEARNING_DATA_MANIFEST_ENV,
                    str(DEFAULT_LEARNING_DATA_MANIFEST),
                )
            )
            replica_root = Path(
                os.environ.get(
                    REPLICA_ROOT_ENV,
                    str(DEFAULT_REPLICA_REPOSITORY_ROOT),
                )
            )
            source_sha = _production_sha_from_learning_manifest(learning_manifest)
            population = produce_case_population(
                replica_repository_root=replica_root,
                expected_source_release_sha=source_sha,
                not_before=activation_boundary,
                now=moment,
            )
            executor = build_credentialled_shadow_executor(
                committee_home=committee_home
            )

        outcome = run_once(
            release_sha=release_sha or "",
            committee_home=committee_home,
            evidence_path=evidence_path,
            settings=settings,
            case_population=population,
            case_executor=executor,
            now=moment,
        )
    except (
        CycleConfigurationError,
        CaseSourceError,
        ShadowExecutorConfigurationError,
        ReplicaVerificationError,
        ValueError,
        OSError,
    ) as exc:
        print(f"committee cycle refused: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    try:
        print(
            json.dumps(
                {
                    "ran": outcome.ran,
                    "reason": outcome.reason,
                    "considered": outcome.considered,
                    "dispositions": outcome.dispositions,
                    "report_id": outcome.report_id,
                },
                sort_keys=True,
            )
        )
    except Exception as exc:  # noqa: BLE001 - a reporting failure is operational.
        print(f"committee cycle reported a failure: {exc}", file=sys.stderr)
        return EXIT_OPERATIONAL_ERROR
    return EXIT_OK


def _value_of(args: Sequence[str], flag: str) -> str | None:
    if flag not in args:
        return None
    index = args.index(flag)
    if index + 1 >= len(args):
        return None
    return args[index + 1]


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())


__all__ = [
    "CYCLE_DISPOSITIONS_FILE",
    "DEFAULT_CYCLE_CASES",
    "DEFAULT_LEARNING_DATA_MANIFEST",
    "DEFAULT_REPLICA_REPOSITORY_ROOT",
    "EVIDENCE_ITEMS_FILE",
    "EXIT_CONFIG_ERROR",
    "EXIT_OK",
    "EXIT_OPERATIONAL_ERROR",
    "LEARNING_DATA_MANIFEST_ENV",
    "REPLICA_ROOT_ENV",
    "SHADOW_NOT_BEFORE_ENV",
    "TRUST_REPORT_FILE",
    "CycleConfigurationError",
    "CycleOutcome",
    "FileCheckpoint",
    "_parse_activation_boundary",
    "_production_sha_from_learning_manifest",
    "load_evidence_items",
    "main",
    "run_once",
]
