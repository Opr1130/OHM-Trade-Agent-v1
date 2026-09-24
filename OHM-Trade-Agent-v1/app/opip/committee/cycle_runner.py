"""One bounded committee shadow cycle, for the isolated worker to invoke.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

This is the deployment entry point: the isolated worker runs it once per scheduled
cycle and it exits. It exists so the deployment change references a real module
rather than an imaginary one.

What it does, in order:

1. Resolves the explicit OFF/SHADOW gate. OFF remains inert and reads no provider
   credential.
2. In SHADOW only, resolves one verified canonical-replica generation and builds
   sealed point-in-time Committee cases no older than the explicit activation
   boundary.
3. Constructs the credentialled executor only after those gates pass, then runs one
   bounded scheduling cycle with a durable UTC daily reservation written before
   external work.
4. Writes append-only provider/case evidence, scheduling dispositions, and a trust
   report into the dedicated advisory directory.

What it deliberately does **not** do:

* It writes nothing to canonical evidence, Decision Intelligence streams, the order
  path, or any trading registry.
* It grants no admission, ranking, sizing, protection, execution, funded, or live
  authority. Committee output remains measurement-only advisory evidence.
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

#: Initial per-cycle bounds. The daily ceiling is the stronger, approved bound.
DEFAULT_CYCLE_CASES = 8
MAX_CASES_PER_CYCLE_ENV = "OPIP_COMMITTEE_MAX_CASES_PER_CYCLE"


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
        for number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            line = line.strip()
            if not line:
                continue
            try:
                decoded = json.loads(line)
            except ValueError as exc:
                raise CycleConfigurationError(
                    f"{self.path.name} line {number} is corrupt; scheduler state "
                    "cannot be reconstructed safely"
                ) from exc
            if not isinstance(decoded, Mapping) or "disposition" not in decoded:
                raise CycleConfigurationError(
                    f"{self.path.name} line {number} is not a schedule disposition"
                )
            yield decoded

    def load_decided(self) -> Mapping[str, ScheduleDispositionRecord]:
        from app.opip.committee.scheduler import CommitteeScheduleDisposition

        decided: dict[str, ScheduleDispositionRecord] = {}
        for row in self._iter_rows():
            try:
                decision = CommitteeScheduleDisposition(str(row["disposition"]))
                record = ScheduleDispositionRecord(
                    schedule_key_id=str(row["schedule_key_id"]),
                    evidence_id=str(row["evidence_id"]),
                    disposition=decision,
                    decided_at=datetime.fromisoformat(str(row["decided_at"])),
                    reason=(None if row.get("reason") is None else str(row["reason"])),
                    detail=(None if row.get("detail") is None else str(row["detail"])),
                )
            except (KeyError, ValueError) as exc:
                raise CycleConfigurationError(
                    "scheduler checkpoint contains an invalid disposition"
                ) from exc
            previous = decided.get(record.schedule_key_id)
            if previous is not None and previous != record:
                raise CycleConfigurationError(
                    "scheduler checkpoint contains conflicting duplicate identities"
                )
            decided[record.schedule_key_id] = record
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
            handle.flush()
            os.fsync(handle.fileno())


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


def _resolve_population(
    *,
    evidence_path: Path,
    case_ingress_path: Path | None,
    case_population: CaseIngressPopulation | None,
    case_executor: Callable[[CommitteeCase], bool] | None,
) -> tuple[CaseIngressPopulation | None, tuple[CommittedEvidenceItem, ...]]:
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
        return None, load_evidence_items(evidence_path)
    return population, population.scheduler_items


@dataclass
class _ReservedCaseExecutor:
    population: CaseIngressPopulation
    executor: Callable[[CommitteeCase], bool]
    ceiling: DailyCeiling
    moment: datetime

    def __call__(self, item: CommittedEvidenceItem) -> bool:
        self.ceiling.admit(
            estimated_cost_microunits=item.estimated_cost_microunits,
            at=self.moment,
        )
        return self.executor(self.population.case_for(item))


def _scheduler_executor(
    *,
    population: CaseIngressPopulation | None,
    case_executor: Callable[[CommitteeCase], bool] | None,
    ceiling: DailyCeiling,
    moment: datetime,
):
    if population is None or case_executor is None:
        return None
    return _ReservedCaseExecutor(
        population=population,
        executor=case_executor,
        ceiling=ceiling,
        moment=moment,
    )


def _write_trust_report(
    *,
    committee_home: Path,
    release_sha: str,
    moment: datetime,
    run,
    remaining_daily_ceiling_microunits: int,
):
    report = build_trust_report(
        report_version="committee-trust-v1",
        release_sha=release_sha,
        registry_version="committee-shadow-registry-v1",
        generated_at=moment,
        population=run.tally,
        investment=CommitteeInvestment(),
    )
    payload = {
        "report_id": report.report_id,
        "stage": report.stage.value,
        "blocked_gates": [gate.value for gate in report.blocked_gates],
        "insufficiency_reasons": list(report.insufficiency_reasons),
        "population": report.population.as_dict(),
        "mean_latency_micros": report.mean_latency_micros,
        "remaining_daily_ceiling_microunits": remaining_daily_ceiling_microunits,
    }
    (committee_home / TRUST_REPORT_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


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

    population, items = _resolve_population(
        evidence_path=evidence_path,
        case_ingress_path=case_ingress_path,
        case_population=case_population,
        case_executor=case_executor,
    )

    # The daily ceiling caps this cycle's cost budget, so a cycle can never spend
    # more than the UTC day's remaining allowance. The per-case ceiling still applies
    # inside the cycle. Together the two bounds are the approved economics.
    ceiling = DailyCeiling(
        max_daily_microunits=APPROVED_MAX_DAILY_COST_MICROUNITS,
        store=FileDailySpendStore(committee_home),
        now=lambda: moment,
    )
    remaining_today = ceiling.remaining_today()

    reserved_executor = _scheduler_executor(
        population=population,
        case_executor=case_executor,
        ceiling=ceiling,
        moment=moment,
    )

    scheduler = CommitteeScheduler(
        checkpoint=checkpoint,
        now=lambda: moment,
        budget=budget
        or SchedulerBudget(
            max_committee_cases=_resolve_cycle_case_limit(),
            max_cost_microunits=remaining_today,
        ),
        settings=resolved_settings,
    )
    # OFF has no executor. SHADOW receives an explicitly constructed executor only
    # after provenance, activation-boundary, credential and budget checks pass.
    run = scheduler.run_cycle(items=items, executor=reserved_executor)

    report = _write_trust_report(
        committee_home=committee_home,
        release_sha=release_sha,
        moment=moment,
        run=run,
        remaining_daily_ceiling_microunits=ceiling.remaining_today(),
    )
    return CycleOutcome(
        ran=run.ran,
        reason=run.reason,
        considered=run.tally.considered,
        dispositions=run.tally.as_dict(),
        report_id=report.report_id,
    )


def _resolve_cycle_case_limit() -> int:
    raw = str(os.environ.get(MAX_CASES_PER_CYCLE_ENV, DEFAULT_CYCLE_CASES)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise CycleConfigurationError(
            f"{MAX_CASES_PER_CYCLE_ENV} must be an integer in 0..{DEFAULT_CYCLE_CASES}"
        ) from exc
    if not 0 <= value <= DEFAULT_CYCLE_CASES:
        raise CycleConfigurationError(
            f"{MAX_CASES_PER_CYCLE_ENV} must be in 0..{DEFAULT_CYCLE_CASES}"
        )
    return value


def _required_path_env(name: str) -> Path:
    raw = os.environ.get(name)
    if not isinstance(raw, str) or not raw.strip():
        raise CycleConfigurationError(
            f"{name} is required for SHADOW; no host path is inferred"
        )
    return Path(raw.strip())


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


def _shadow_dependencies(
    *,
    settings: CommitteeShadowSettings,
    committee_home: Path,
    moment: datetime,
) -> tuple[CaseIngressPopulation | None, Callable[[CommitteeCase], bool] | None]:
    if settings.opip_committee_mode != COMMITTEE_MODE_SHADOW:
        return None, None
    activation_boundary = _parse_activation_boundary(
        os.environ.get(SHADOW_NOT_BEFORE_ENV)
    )
    learning_manifest = _required_path_env(LEARNING_DATA_MANIFEST_ENV)
    replica_root = _required_path_env(REPLICA_ROOT_ENV)
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
    return population, executor


def _emit_outcome(outcome: CycleOutcome) -> None:
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
        population, executor = _shadow_dependencies(
            settings=settings,
            committee_home=committee_home,
            moment=moment,
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
        _emit_outcome(outcome)
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
    "EVIDENCE_ITEMS_FILE",
    "EXIT_CONFIG_ERROR",
    "EXIT_OK",
    "EXIT_OPERATIONAL_ERROR",
    "LEARNING_DATA_MANIFEST_ENV",
    "MAX_CASES_PER_CYCLE_ENV",
    "REPLICA_ROOT_ENV",
    "SHADOW_NOT_BEFORE_ENV",
    "TRUST_REPORT_FILE",
    "CycleConfigurationError",
    "CycleOutcome",
    "FileCheckpoint",
    "_parse_activation_boundary",
    "_production_sha_from_learning_manifest",
    "_required_path_env",
    "_resolve_cycle_case_limit",
    "load_evidence_items",
    "main",
    "run_once",
]
