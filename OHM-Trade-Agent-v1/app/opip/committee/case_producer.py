"""Read-only canonical-replica producer for prospective Committee SHADOW cases.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

The producer reads one verified learning-replica generation and converts eligible
Paper-v2 DecisionContextV2 records plus their already-committed decision snapshots
into the Committee's sealed case-ingress contract. It never reaches production,
never writes canonical evidence, and never fabricates the v1 committee-manifest
fields that DecisionContextV2 deliberately does not contain.

A context is eligible only when the replica proves the externally supplied production
release SHA; Decision Intelligence reconstruction is complete; the context is Paper,
eligible, effective (not superseded), and no older than the explicit SHADOW activation
boundary; and exactly one source record in the context provenance is a valid canonical
Paper-v2 decision snapshot whose snapshot id/hash and episode match the context.

Anything ambiguous fails closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping

from app.opip.canonical.schema import connect
from app.opip.committee.case_ingress import CaseIngressPopulation, CommittedCaseEnvelope
from app.opip.committee.contracts import (
    CanonicalDecisionBinding,
    CaseType,
    CommitteeCase,
    CommitteePolicy,
    EvidenceItem,
    EvidenceSnapshot,
    ProviderFamily,
)
from app.opip.committee.registry import APPROVED_MAX_CASE_COST_MICROUNITS
from app.opip.contracts.paper_execution_runtime import (
    PAPER_DECISION_SNAPSHOT_RECORDED,
    validate_decision_snapshot_payload,
)
from app.opip.decision_intelligence.evidence_reader import read_di_evidence_snapshot
from app.opip.decision_intelligence.identity import DecisionContextV2, Provenance
from app.opip.decision_intelligence.serialization import require_utc, stable_hash
from app.opip.learning.canonical_replica import (
    resolve_current_generation,
    resolve_verified_replica_bundle,
)

SHADOW_CASE_POLICY_VERSION = "committee-shadow-case-v1"
SHADOW_PROMPT_TEMPLATE_ID = "committee.case.market_opportunity"
SHADOW_PROMPT_VERSION = "1"
SHADOW_CASE_ID_DOMAIN = "COMMITTEE-SHADOW-CASE-SOURCE"
SHADOW_EVIDENCE_ID_DOMAIN = "COMMITTEE-SHADOW-EVIDENCE"
PAPER_ENVIRONMENT = "paper"

_SNAPSHOT_METRICS = (
    "symbol",
    "reference_price",
    "last_price",
    "volume_24h",
    "liquidity_24h_usd_approx",
    "high_24h",
    "low_24h",
    "lift_from_24h_low_pct",
    "distance_from_24h_high_pct",
    "decision_status",
    "candidate_rank",
    "stage",
    "pattern",
    "opportunity_score",
    "explosion_potential_score",
    "tradeability_score",
    "pattern_strength_score",
    "volume_acceleration_score",
    "relative_strength_score",
    "persistence_scans",
    "exhaustion_penalty",
    "exhaustion_band",
    "relative_strength_percentile",
    "suppressed",
)

_CONTEXT_METRICS = (
    "policy_version",
    "policy_fingerprint",
    "environment",
    "eligibility",
)


class CaseSourceError(ValueError):
    """A canonical source cannot prove one unambiguous Committee case."""


@dataclass(frozen=True)
class CanonicalDecisionSnapshotRecord:
    """One validated decision-snapshot event from the immutable replica."""

    event_id: str
    recorded_at: datetime
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise CaseSourceError("decision snapshot event_id is required")
        object.__setattr__(
            self,
            "recorded_at",
            require_utc(self.recorded_at, field_name="recorded_at"),
        )
        try:
            normalized = validate_decision_snapshot_payload(self.payload)
        except ValueError as exc:
            raise CaseSourceError(f"invalid canonical decision snapshot: {exc}") from exc
        object.__setattr__(self, "payload", normalized)


def _parse_utc(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise CaseSourceError(f"{field_name} is required")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise CaseSourceError(f"{field_name} is not an ISO-8601 timestamp") from exc
    try:
        return require_utc(parsed, field_name=field_name)
    except ValueError as exc:
        raise CaseSourceError(str(exc)) from exc


def _read_decision_snapshots(
    db_path: Path,
) -> Mapping[str, CanonicalDecisionSnapshotRecord]:
    """Read and validate every canonical Paper-v2 decision snapshot, read-only."""
    try:
        connection = connect(Path(db_path), read_only=True)
    except sqlite3.Error as exc:
        raise CaseSourceError(
            f"canonical replica could not be opened read-only: {exc}"
        ) from exc
    try:
        rows = connection.execute(
            """
            SELECT event_id, recorded_at, payload_json
            FROM events
            WHERE event_type = ?
            ORDER BY history_epoch, local_sequence
            """,
            (PAPER_DECISION_SNAPSHOT_RECORDED,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise CaseSourceError(f"decision snapshot read failed: {exc}") from exc
    finally:
        connection.close()

    records: dict[str, CanonicalDecisionSnapshotRecord] = {}
    for row in rows:
        event_id = str(row["event_id"] or "").strip()
        if not event_id or event_id in records:
            raise CaseSourceError(
                "decision snapshot event identity is missing or duplicated"
            )
        try:
            decoded = json.loads(row["payload_json"])
        except (TypeError, ValueError) as exc:
            raise CaseSourceError(
                f"decision snapshot {event_id!r} has unreadable payload_json"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise CaseSourceError(
                f"decision snapshot {event_id!r} payload is not an object"
            )
        records[event_id] = CanonicalDecisionSnapshotRecord(
            event_id=event_id,
            recorded_at=_parse_utc(row["recorded_at"], field_name="recorded_at"),
            payload=decoded,
        )
    return records


def _source_snapshot_for_context(
    context: DecisionContextV2,
    records: Mapping[str, CanonicalDecisionSnapshotRecord],
) -> CanonicalDecisionSnapshotRecord:
    """Resolve exactly one snapshot cited by this context and verify its subject."""
    candidates: list[CanonicalDecisionSnapshotRecord] = []
    for source_ref in context.provenance.source_record_refs:
        record = records.get(source_ref)
        if record is None:
            continue
        payload = record.payload
        if (
            payload["snapshot_id"] == context.snapshot_id
            and payload["snapshot_hash"] == context.snapshot_hash
            and payload["episode_id"] == context.episode_id
        ):
            candidates.append(record)
    if len(candidates) != 1:
        raise CaseSourceError(
            f"context {context.context_id!r} must cite exactly one matching "
            f"decision snapshot; found {len(candidates)}"
        )
    return candidates[0]


def _metric_value(value: object) -> object:
    """Normalize a canonical scalar to the Committee no-binary-float vocabulary."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return str(value)
    raise CaseSourceError(
        f"unsupported canonical metric type {type(value).__name__}; refusing source"
    )


def _evidence_item(
    *,
    context: DecisionContextV2,
    source: CanonicalDecisionSnapshotRecord,
    metric_name: str,
    metric_value: object,
) -> EvidenceItem:
    evidence_id = stable_hash(
        SHADOW_EVIDENCE_ID_DOMAIN,
        {
            "context_id": context.context_id,
            "source_event_id": source.event_id,
            "metric_name": metric_name,
        },
    )
    return EvidenceItem(
        evidence_id=evidence_id,
        source_id=source.event_id,
        available_at=context.evidence_cutoff,
        payload={
            "observed_at": context.evidence_cutoff.isoformat(),
            "instrument_id": context.instrument_version,
            "metric_name": metric_name,
            "metric_value": _metric_value(metric_value),
        },
    )


def _evidence_items(
    context: DecisionContextV2,
    source: CanonicalDecisionSnapshotRecord,
) -> tuple[EvidenceItem, ...]:
    snapshot_payload = source.payload["snapshot_payload"]
    items: list[EvidenceItem] = []
    for name in _SNAPSHOT_METRICS:
        value = snapshot_payload.get(name)
        if value is None:
            continue
        items.append(
            _evidence_item(
                context=context,
                source=source,
                metric_name=f"snapshot.{name}",
                metric_value=value,
            )
        )
    for name in _CONTEXT_METRICS:
        items.append(
            _evidence_item(
                context=context,
                source=source,
                metric_name=f"context.{name}",
                metric_value=getattr(context, name),
            )
        )
    if not items:
        raise CaseSourceError(
            f"context {context.context_id!r} produced no allowlisted evidence"
        )
    return tuple(items)


def build_case_envelope(
    *,
    context: DecisionContextV2,
    source: CanonicalDecisionSnapshotRecord,
    source_release_sha: str,
) -> CommittedCaseEnvelope:
    """Build one deterministic, sealed case from canonical point-in-time evidence."""
    if not isinstance(context, DecisionContextV2):
        raise CaseSourceError("context must be DecisionContextV2")
    release_sha = str(source_release_sha or "").strip()
    if len(release_sha) != 40 or any(
        char not in "0123456789abcdef" for char in release_sha
    ):
        raise CaseSourceError(
            "source_release_sha must be 40 lowercase hex characters"
        )
    if source.event_id not in context.provenance.source_record_refs:
        raise CaseSourceError(
            "decision snapshot is not cited by context provenance"
        )
    if (
        source.payload["snapshot_id"] != context.snapshot_id
        or source.payload["snapshot_hash"] != context.snapshot_hash
        or source.payload["episode_id"] != context.episode_id
    ):
        raise CaseSourceError(
            "decision snapshot identity does not match the context"
        )

    case_id = stable_hash(
        SHADOW_CASE_ID_DOMAIN,
        {
            "context_id": context.context_id,
            "snapshot_hash": context.snapshot_hash,
            "policy_version": SHADOW_CASE_POLICY_VERSION,
        },
    )
    policy = CommitteePolicy(
        policy_version=SHADOW_CASE_POLICY_VERSION,
        seated_providers=(ProviderFamily.OPENAI, ProviderFamily.ANTHROPIC),
        prompt_template_id=SHADOW_PROMPT_TEMPLATE_ID,
        prompt_version=SHADOW_PROMPT_VERSION,
        max_attempts_per_seat=1,
        max_estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
    )
    items = _evidence_items(context, source)
    source_refs = tuple(
        dict.fromkeys(
            (
                context.context_id,
                source.event_id,
                *context.provenance.source_record_refs,
            )
        )
    )
    snapshot = EvidenceSnapshot(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        evidence_cutoff_at=context.evidence_cutoff,
        assembled_at=context.evaluation_time,
        items=items,
        source_refs=source_refs,
        committee_policy_version=policy.policy_version,
        prompt_template_id=policy.prompt_template_id,
        prompt_version=policy.prompt_version,
        instrument_id=context.instrument_version,
        strategy_context_id=context.context_id,
    )
    case = CommitteeCase(
        case_id=case_id,
        case_type=CaseType.MARKET_OPPORTUNITY,
        snapshot=snapshot,
        policy=policy,
        created_at=context.evaluation_time,
        provenance=Provenance(
            producing_component="app.opip.committee.case_producer",
            artifact_or_build_id=release_sha,
            process_instance_id=context.context_id,
            emitted_at=context.provenance.emitted_at,
            source_record_refs=source_refs,
        ),
        instrument_id=context.instrument_version,
        strategy_context_id=context.context_id,
        canonical_binding=CanonicalDecisionBinding(episode_id=context.episode_id),
    )
    return CommittedCaseEnvelope(
        evidence_id=context.context_id,
        case=case,
        committed=True,
        sealed=True,
        available_at=context.evidence_cutoff,
        expires_at=None,
        estimated_cost_microunits=APPROVED_MAX_CASE_COST_MICROUNITS,
    )


def produce_case_population(
    *,
    replica_repository_root: Path,
    expected_source_release_sha: str,
    not_before: datetime,
    now: datetime,
) -> CaseIngressPopulation:
    """Produce prospective cases from one verified immutable replica generation.

    not_before is the explicit SHADOW activation boundary. Historical contexts
    before it are not silently backfilled into paid work.
    """
    start = require_utc(not_before, field_name="not_before")
    moment = require_utc(now, field_name="now")
    if start > moment:
        raise CaseSourceError("not_before cannot be in the future")

    generation = resolve_current_generation(Path(replica_repository_root))
    bundle = resolve_verified_replica_bundle(
        root=generation,
        expected_source_release_sha=expected_source_release_sha,
        now=moment,
    )
    di = read_di_evidence_snapshot(bundle.canonical_db_path)
    if not di.is_complete:
        raise CaseSourceError(
            "Decision Intelligence replica contains anomalies; SHADOW case "
            f"production is refused ({sorted(di.anomaly_codes)})"
        )
    records = _read_decision_snapshots(bundle.canonical_db_path)
    envelopes: list[CommittedCaseEnvelope] = []
    for context in sorted(
        di.contexts_v2.values(),
        key=lambda item: (item.evaluation_time, item.context_id),
    ):
        if context.environment != PAPER_ENVIRONMENT or not context.eligibility:
            continue
        if context.evaluation_time < start:
            continue
        if di.superseded_by(context.context_id):
            continue
        source = _source_snapshot_for_context(context, records)
        envelopes.append(
            build_case_envelope(
                context=context,
                source=source,
                source_release_sha=expected_source_release_sha,
            )
        )
    return CaseIngressPopulation(tuple(envelopes))


__all__ = [
    "CanonicalDecisionSnapshotRecord",
    "CaseSourceError",
    "PAPER_ENVIRONMENT",
    "SHADOW_CASE_ID_DOMAIN",
    "SHADOW_CASE_POLICY_VERSION",
    "SHADOW_EVIDENCE_ID_DOMAIN",
    "SHADOW_PROMPT_TEMPLATE_ID",
    "SHADOW_PROMPT_VERSION",
    "build_case_envelope",
    "produce_case_population",
]
