"""Profit Intelligence qualification-funnel projection (read-only, derived).

Profit Intelligence could not answer "where and why was this opportunity lost?"
because the pre-trade qualification evidence is produced on the trading host and
is not part of the canonical replica the analytics plane reads. This module is
the typed, versioned *projection* of that evidence: it turns already-persisted
canonical funnel rows into an availability-annotated record set without
inventing a single fact.

What it is
----------
A deterministic, pure function over canonical O'Pip qualification rows:

* ``app.opip.decision.funnel`` records one ordered gate history per candidate per
  scan (``GateResult`` sequence), and
* ``app.opip.decision.store`` appends those rows to the trading host's
  ``/app/data/opip/qualification/funnel_events.jsonl`` (and Stage-0 screening to
  ``screening_evaluations.jsonl``).

The producer is therefore authoritative; this module is a derived read model. It
is **not** a second source of truth, holds no trading/risk/execution/alert/ranking
authority, never writes, and exposes no HTTP surface. Canonical production
evidence remains the authority for every fact projected here.

Two vocabularies, kept distinct on purpose
------------------------------------------
*Canonical* (published unchanged on every record): ``decision``
(:class:`~app.opip.decision.models.DecisionOutcome`), ``terminal_reason_code``
(:class:`~app.opip.decision.models.ReasonCode`), ``terminal_reason_class``
(:class:`~app.opip.decision.models.ReasonClass`) and the ordered gate history.
These are the evidence.

*Derived* (this projection's own grouping, versioned by
:data:`QUALIFICATION_FUNNEL_PROJECTION_VERSION`): :class:`QualificationDisposition`,
a coarse bucketing of the canonical terminal facts for owner-facing questions.
It starts from the canonical ``ReasonClass`` and may **narrow** it through a
documented, closed reason-code refinement (for example ``NO_CAPITAL`` is
canonically ``POLICY`` but is bucketed as ``CAPACITY_BLOCKED`` because that is the
actionable fact), and it fails closed to ``UNKNOWN`` when no rule matches. The
canonical ``decision`` / ``terminal_reason_code`` / ``terminal_reason_class`` are
always published unchanged on every record, so the view never replaces the
taxonomy. It is a view, never a competing rejection taxonomy.

Honesty rules
-------------
* ``UNKNOWN`` / ``UNAVAILABLE`` / ``INCOMPLETE`` are never converted into ``0``.
* A fact that cannot exist for this evidence family is declared in
  :data:`DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER` rather than fabricated as a
  measured zero. In particular the pre-trade funnel never yields
  ``RISK_BLOCKED`` or ``EXECUTION_FAILED``: risk gates and execution attempts are
  downstream of this evidence family and are not recorded here.
* Replayed/duplicate WAL rows are de-duplicated by canonical identity
  (``(scan_id, candidate_id)``) so a retry cannot inflate a count, and the number
  of ignored duplicates is reported.
* Terminal dispositions reconcile to the population that entered the funnel
  (:func:`funnel_conservation`); an unattributed row makes ``holds`` false
  instead of being silently dropped.

Scope boundary
--------------
This projection covers pre-trade *qualification* evidence. Execution, fill, exit
and economics live on the canonical replica and are projected separately by
:mod:`app.opip.profit_intelligence.lineage`. The two are joined only by the
identities the canonical evidence actually shares (episode/candidate), never by a
guessed bridge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Sequence

from app.opip.cockpit.trust import (
    Completeness,
    Freshness,
    TrustEnvelope,
    unavailable as unavailable_trust,
)
from app.opip.decision.models import (
    DecisionOutcome,
    ReasonClass,
    ReasonCode,
)
from app.opip.profit_intelligence.semantics import FactAvailability

#: Bumped whenever a derived definition or bucket rule in this projection
#: changes, so a stored or cached projection can be recognised as predating it.
QUALIFICATION_FUNNEL_PROJECTION_VERSION = "profit-intelligence-qualification-funnel-v1"

#: Envelope schema version for this projection's serialised form.
QUALIFICATION_FUNNEL_SCHEMA_VERSION = 1

#: The canonical producer whose persisted rows this projection reads. Named once
#: so a record's provenance is never inferred from its fields.
QUALIFICATION_FUNNEL_PRODUCER = "app.opip.decision.observer"

#: What this projection can and cannot prove, stated once for every consumer.
QUALIFICATION_FUNNEL_SCOPE = (
    "Pre-trade qualification progression for one directional candidate per scan: "
    "the ordered gate history, the terminal disposition and its canonical reason, "
    "from the O'Pip qualification funnel evidence. Stage-0 instrument screening is "
    "a separate evidence family that this version does not project. "
    "Execution, fill, exit and economics are out of scope and are projected by the "
    "trade lineage."
)

#: The population this projection counts over, stated so a consumer never has to
#: infer it from a missing key.
QUALIFICATION_FUNNEL_POPULATION_SEMANTICS = (
    "One population = registered directional candidates in the qualification "
    "funnel, de-duplicated by (scan_id, candidate_id). Screening evaluations are a "
    "distinct population (venue instruments per scanner per scan) that this version "
    "does not project and never adds to the candidate population."
)

#: How to read a bucket value in this projection.
QUALIFICATION_FUNNEL_AVAILABILITY_SEMANTICS = (
    "Bucket counts are intrinsic counts observed in canonical evidence. A bucket "
    "listed in DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER is absent because this "
    "evidence family cannot produce it, not because it was measured as zero. A "
    "record whose canonical fields could not be read is UNAVAILABLE and is excluded "
    "from bucket counts and reported in the conservation remainder."
)


class QualificationDisposition(str, Enum):
    """Derived coarse disposition of one candidate in one scan.

    This is a *view* over the canonical terminal facts, not a canonical taxonomy.
    Every record still carries the canonical ``decision``, ``terminal_reason_code``
    and ``terminal_reason_class`` unchanged.

    The cause-class bucket names are intentionally the same vocabulary as
    :class:`app.opip.profit_intelligence.semantics.MissedOpportunityCause`. This
    enum re-states those names plus ``QUALIFIED`` / ``INCOMPLETE``; the shared
    names are held equal to ``MissedOpportunityCause`` by test, so the plane
    cannot drift into two rival vocabularies. The canonical decision/reason
    taxonomy remains the single authority and is published unchanged.
    """

    QUALIFIED = "QUALIFIED"
    REJECTED = "REJECTED"
    FILTERED = "FILTERED"
    RISK_BLOCKED = "RISK_BLOCKED"
    CAPACITY_BLOCKED = "CAPACITY_BLOCKED"
    TECHNICALLY_UNAVAILABLE = "TECHNICALLY_UNAVAILABLE"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    INCOMPLETE = "INCOMPLETE"
    UNKNOWN = "UNKNOWN"


#: Buckets this evidence family cannot produce. Declared, never fabricated as a
#: measured zero: risk gates and execution attempts are downstream of pre-trade
#: qualification and are not recorded in the funnel evidence.
DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER: tuple[QualificationDisposition, ...] = (
    QualificationDisposition.RISK_BLOCKED,
    QualificationDisposition.EXECUTION_FAILED,
)

#: Reason codes whose canonical semantics refine their class (documented, closed).
_DISPOSITION_BY_REASON: Mapping[ReasonCode, QualificationDisposition] = {
    ReasonCode.NO_CAPITAL: QualificationDisposition.CAPACITY_BLOCKED,
    ReasonCode.AI_BUDGET_LIMIT: QualificationDisposition.CAPACITY_BLOCKED,
    ReasonCode.AI_SERVICE_UNAVAILABLE: QualificationDisposition.TECHNICALLY_UNAVAILABLE,
    ReasonCode.MARGIN_VALIDATION_UNAVAILABLE: (
        QualificationDisposition.TECHNICALLY_UNAVAILABLE
    ),
    ReasonCode.REFERENCE_EVIDENCE_UNAVAILABLE: (
        QualificationDisposition.TECHNICALLY_UNAVAILABLE
    ),
    ReasonCode.MARKET_INTELLIGENCE_UNAVAILABLE: (
        QualificationDisposition.TECHNICALLY_UNAVAILABLE
    ),
    ReasonCode.SNAPSHOT_MISSING: QualificationDisposition.TECHNICALLY_UNAVAILABLE,
    ReasonCode.GATE_EVALUATION_ERROR: QualificationDisposition.TECHNICALLY_UNAVAILABLE,
    ReasonCode.TRADE_QUALITY_UNAVAILABLE: (
        QualificationDisposition.TECHNICALLY_UNAVAILABLE
    ),
}

#: The reason-class fallback when a specific reason code carries no refinement.
_REASON_CLASS_DISPOSITION = {
    ReasonClass.POLICY: QualificationDisposition.REJECTED,
    ReasonClass.MODEL: QualificationDisposition.FILTERED,
    ReasonClass.BUDGET: QualificationDisposition.CAPACITY_BLOCKED,
    ReasonClass.OPERATIONAL: QualificationDisposition.TECHNICALLY_UNAVAILABLE,
    ReasonClass.INFORMATIONAL: QualificationDisposition.UNKNOWN,
}


def disposition_for(
    decision: DecisionOutcome | str | None,
    reason_code: ReasonCode | str | None,
    reason_class: ReasonClass | str | None = None,
) -> QualificationDisposition:
    """Derive the disposition for one terminal decision.

    The canonical ``decision`` drives the coarse outcome; a specific reason code
    may refine it; and the canonical ``reason class`` is the fallback. No rule
    invents a bucket, and anything unmatched fails closed to ``UNKNOWN`` rather
    than being folded into a neighbour.
    """
    try:
        outcome = DecisionOutcome(decision) if decision is not None else None
    except ValueError:
        outcome = None

    if outcome is DecisionOutcome.QUALIFIED:
        return QualificationDisposition.QUALIFIED
    if outcome is DecisionOutcome.INCOMPLETE:
        return QualificationDisposition.INCOMPLETE
    if outcome is DecisionOutcome.OPERATIONAL_FAILURE:
        return QualificationDisposition.TECHNICALLY_UNAVAILABLE

    if outcome is DecisionOutcome.REJECTED:
        if reason_code is not None:
            try:
                refined = _DISPOSITION_BY_REASON.get(ReasonCode(reason_code))
            except ValueError:
                refined = None
            if refined is not None:
                return refined
        if reason_class is not None:
            try:
                fallback = _REASON_CLASS_DISPOSITION.get(ReasonClass(reason_class))
            except ValueError:
                fallback = None
            if fallback is not None:
                return fallback
        return QualificationDisposition.REJECTED

    # COUNTERFACTUAL_ELIGIBLE is reserved and never assigned by Build 1; an
    # unknown decision must not be guessed into a real disposition.
    return QualificationDisposition.UNKNOWN


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class QualificationGateObservation:
    """One gate's conclusion, published from the canonical record unchanged."""

    gate: str
    status: str
    reason_code: str
    reason_class: str
    reason: str
    measured_value: float | None
    threshold: float | None
    threshold_distance: float | None
    evaluated_at: str | None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "QualificationGateObservation":
        def _finite(value: Any) -> float | None:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return None
            return number if number == number and number not in (
                float("inf"),
                float("-inf"),
            ) else None

        return cls(
            gate=str(row.get("gate") or ""),
            status=str(row.get("status") or ""),
            reason_code=str(row.get("reason_code") or ""),
            reason_class=str(row.get("reason_class") or ""),
            reason=str(row.get("reason") or ""),
            measured_value=_finite(row.get("measured_value")),
            threshold=_finite(row.get("threshold")),
            threshold_distance=_finite(row.get("threshold_distance")),
            evaluated_at=_text(row.get("evaluated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "status": self.status,
            "reason_code": self.reason_code,
            "reason_class": self.reason_class,
            "reason": self.reason,
            "measured_value": self.measured_value,
            "threshold": self.threshold,
            "threshold_distance": self.threshold_distance,
            "evaluated_at": self.evaluated_at,
        }


@dataclass(frozen=True)
class QualificationFunnelRecord:
    """The projected progression of one candidate in one scan."""

    scan_id: str | None
    cohort_id: str | None
    candidate_id: str | None
    episode_id: str | None
    signal_id: str | None
    symbol: str | None
    pair: str | None
    market: str | None
    direction: str | None
    decided_at: datetime | None
    decision: str | None
    disposition: QualificationDisposition
    stage_reached: str | None
    terminal_gate: str | None
    terminal_reason_code: str | None
    terminal_reason_class: str | None
    terminal_reason: str | None
    counterfactual_eligible: bool
    paper_trade_id: str | None
    strategy_version: str | None
    intelligence_version: str | None
    gate_policy_version: str | None
    gate_policy_fingerprint: str | None
    source_schema_version: int | None
    producer: str = QUALIFICATION_FUNNEL_PRODUCER
    availability: FactAvailability = FactAvailability.KNOWN
    unavailable_fields: tuple[str, ...] = ()
    gates: tuple[QualificationGateObservation, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_id": self.scan_id,
            "cohort_id": self.cohort_id,
            "candidate_id": self.candidate_id,
            "episode_id": self.episode_id,
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "pair": self.pair,
            "market": self.market,
            "direction": self.direction,
            "decided_at": _iso(self.decided_at),
            "decision": self.decision,
            "disposition": self.disposition.value,
            "stage_reached": self.stage_reached,
            "terminal_gate": self.terminal_gate,
            "terminal_reason_code": self.terminal_reason_code,
            "terminal_reason_class": self.terminal_reason_class,
            "terminal_reason": self.terminal_reason,
            "counterfactual_eligible": self.counterfactual_eligible,
            "paper_trade_id": self.paper_trade_id,
            "strategy_version": self.strategy_version,
            "intelligence_version": self.intelligence_version,
            "gate_policy_version": self.gate_policy_version,
            "gate_policy_fingerprint": self.gate_policy_fingerprint,
            "source_schema_version": self.source_schema_version,
            "producer": self.producer,
            "availability": self.availability.value,
            "unavailable_fields": list(self.unavailable_fields),
            "gates": [gate.to_dict() for gate in self.gates],
        }


@dataclass(frozen=True)
class FunnelConservation:
    """Terminal attribution must reconcile against the population that entered."""

    entered: int
    qualified: int
    rejected: int
    operational_failure: int
    incomplete: int
    unattributed: int
    holds: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "entered": self.entered,
            "qualified": self.qualified,
            "rejected": self.rejected,
            "operational_failure": self.operational_failure,
            "incomplete": self.incomplete,
            "unattributed": self.unattributed,
            "holds": self.holds,
            "detail": self.detail,
        }


#: Required identity fields for a funnel record to be considered attributable.
_REQUIRED_FIELDS = ("scan_id", "candidate_id", "decision")


def _record_from_row(row: Mapping[str, Any]) -> QualificationFunnelRecord:
    scan_id = _text(row.get("scan_id"))
    candidate_id = _text(row.get("candidate_id"))
    decision = _text(row.get("decision"))
    reason_code = _text(row.get("terminal_reason_code"))
    reason_class = _text(row.get("terminal_reason_class"))
    decided_at = _parse_utc(row.get("decided_at") or row.get("decision_at_utc"))

    missing = tuple(
        name
        for name, value in (
            ("scan_id", scan_id),
            ("candidate_id", candidate_id),
            ("decision", decision),
        )
        if value is None
    )

    raw_gates = row.get("gate_results") or []
    gates = tuple(
        QualificationGateObservation.from_row(gate)
        for gate in raw_gates
        if isinstance(gate, Mapping)
    )

    schema_version = row.get("schema_version")
    try:
        source_schema_version = int(schema_version)
    except (TypeError, ValueError):
        source_schema_version = None

    return QualificationFunnelRecord(
        scan_id=scan_id,
        cohort_id=_text(row.get("cohort_id")),
        candidate_id=candidate_id,
        episode_id=_text(row.get("episode_id")),
        signal_id=_text(row.get("signal_id")),
        symbol=_text(row.get("asset")),
        pair=_text(row.get("pair")),
        market=_text(row.get("market_type")),
        direction=_text(row.get("direction")),
        decided_at=decided_at,
        decision=decision,
        disposition=disposition_for(decision, reason_code, reason_class),
        stage_reached=_text(row.get("deepest_gate")),
        terminal_gate=_text(row.get("first_terminal_gate")),
        terminal_reason_code=reason_code,
        terminal_reason_class=reason_class,
        terminal_reason=_text(row.get("terminal_reason")) or "",
        counterfactual_eligible=bool(row.get("counterfactual_eligible", False)),
        # The pre-trade funnel never carries a paper trade id; it is surfaced as
        # None rather than guessed from a downstream join.
        paper_trade_id=None,
        strategy_version=_text(row.get("strategy_version")),
        intelligence_version=_text(row.get("intelligence_version")),
        gate_policy_version=_text(row.get("gate_policy_version")),
        gate_policy_fingerprint=_text(row.get("gate_policy_fingerprint")),
        source_schema_version=source_schema_version,
        availability=(
            FactAvailability.KNOWN if not missing else FactAvailability.UNAVAILABLE
        ),
        unavailable_fields=missing,
        gates=gates,
    )


def _identity_key(record: QualificationFunnelRecord) -> tuple[str, str]:
    """Canonical de-duplication identity for one candidate in one scan.

    The funnel writes one row per candidate per scan, so a repeated
    ``(scan_id, candidate_id)`` is a replayed/retried append, not a second
    opportunity. A row missing either part returns a blank pair, which the
    caller replaces with a unique sentinel so distinct unreadable rows stay
    addressable rather than being collapsed as replays.
    """
    return (record.scan_id or "", record.candidate_id or "")


def funnel_conservation(
    records: Sequence[QualificationFunnelRecord],
) -> FunnelConservation:
    """Reconcile terminal dispositions against the entering population.

    Only *attributable* records - those whose canonical identity and decision were
    readable - form the population that entered. A row whose identity could not be
    read is counted in ``unattributed`` and forces ``holds`` false, so a lost or
    un-joinable row is stated rather than silently dropped or double-counted.
    """
    attributable = [
        record
        for record in records
        if record.availability is FactAvailability.KNOWN
    ]
    entered = len(attributable)
    qualified = sum(
        1
        for record in attributable
        if record.decision == DecisionOutcome.QUALIFIED.value
    )
    rejected = sum(
        1
        for record in attributable
        if record.decision == DecisionOutcome.REJECTED.value
    )
    operational = sum(
        1
        for record in attributable
        if record.decision == DecisionOutcome.OPERATIONAL_FAILURE.value
    )
    incomplete = sum(
        1
        for record in attributable
        if record.decision == DecisionOutcome.INCOMPLETE.value
    )
    unattributed = len(records) - entered
    holds = unattributed == 0 and (
        entered == qualified + rejected + operational + incomplete
    )
    if holds:
        detail = "terminal dispositions reconcile to the entering population"
    elif unattributed:
        detail = (
            f"{unattributed} record(s) carry no readable canonical "
            "identity/decision"
        )
    else:
        detail = (
            "terminal dispositions do not reconcile: "
            f"entered={entered} but attributed terminal buckets sum to "
            f"{qualified + rejected + operational + incomplete}"
        )
    return FunnelConservation(
        entered=entered,
        qualified=qualified,
        rejected=rejected,
        operational_failure=operational,
        incomplete=incomplete,
        unattributed=unattributed,
        holds=holds,
        detail=detail,
    )


def _counts_by_disposition(
    records: Sequence[QualificationFunnelRecord],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = record.disposition.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _counts(values: Sequence[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


@dataclass(frozen=True)
class QualificationFunnelProjection:
    """Versioned, availability-annotated view of the qualification funnel."""

    generated_at: datetime
    records: tuple[QualificationFunnelRecord, ...] = ()
    schema_version: int = QUALIFICATION_FUNNEL_SCHEMA_VERSION
    projection_version: str = QUALIFICATION_FUNNEL_PROJECTION_VERSION
    producer: str = QUALIFICATION_FUNNEL_PRODUCER
    producer_version: str | None = None
    source_provenance: str = ""
    scope: str = QUALIFICATION_FUNNEL_SCOPE
    population_semantics: str = QUALIFICATION_FUNNEL_POPULATION_SEMANTICS
    availability_semantics: str = QUALIFICATION_FUNNEL_AVAILABILITY_SEMANTICS
    window_start: datetime | None = None
    window_end: datetime | None = None
    bucket_counts: Mapping[str, int] = field(default_factory=dict)
    gate_counts: Mapping[str, int] = field(default_factory=dict)
    reason_code_counts: Mapping[str, int] = field(default_factory=dict)
    dispositions_without_canonical_producer: tuple[str, ...] = tuple(
        disposition.value for disposition in DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER
    )
    duplicate_rows_ignored: int = 0
    conservation: FunnelConservation | None = None
    trust: TrustEnvelope = field(
        default_factory=lambda: TrustEnvelope(
            freshness=Freshness.LIVE,
            completeness=Completeness.COMPLETE,
        )
    )
    details: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "projection_version": self.projection_version,
            "producer": self.producer,
            "producer_version": self.producer_version,
            "source_provenance": self.source_provenance,
            "scope": self.scope,
            "population_semantics": self.population_semantics,
            "availability_semantics": self.availability_semantics,
            "generated_at": _iso(self.generated_at),
            "window_start": _iso(self.window_start),
            "window_end": _iso(self.window_end),
            "bucket_counts": dict(self.bucket_counts),
            "gate_counts": dict(self.gate_counts),
            "reason_code_counts": dict(self.reason_code_counts),
            "dispositions_without_canonical_producer": list(
                self.dispositions_without_canonical_producer
            ),
            "duplicate_rows_ignored": self.duplicate_rows_ignored,
            "conservation": self.conservation.to_dict() if self.conservation else None,
            "trust": self.trust.to_dict(),
            "records": [record.to_dict() for record in self.records],
            "details": list(self.details),
        }


def _producer_version(records: Sequence[QualificationFunnelRecord]) -> str | None:
    fingerprints = sorted(
        {
            record.gate_policy_fingerprint
            for record in records
            if record.gate_policy_fingerprint
        }
    )
    if not fingerprints:
        return None
    if len(fingerprints) > 1:
        return f"MIXED[{len(fingerprints)}]"
    return fingerprints[0]


def build_qualification_funnel_projection(
    rows: Sequence[Mapping[str, Any]],
    *,
    generated_at: datetime | None = None,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    trust: TrustEnvelope | None = None,
) -> QualificationFunnelProjection:
    """Project canonical funnel rows into a versioned read model.

    Deterministic: the same rows always yield the same projection. Duplicate
    identities are collapsed so a replayed append cannot inflate a count, and the
    canonical decision/class/code on every record is published unchanged.
    """
    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    parsed = [_record_from_row(row) for row in rows if isinstance(row, Mapping)]

    latest: dict[tuple[str, str], QualificationFunnelRecord] = {}
    order: list[tuple[str, str]] = []
    duplicates = 0
    for index, record in enumerate(parsed):
        key = _identity_key(record)
        if key == ("", ""):
            # Keep an unreadable row addressable instead of collapsing distinct
            # lost rows onto one blank identity and mislabelling them as replays.
            key = ("<unidentified>", str(index))
        if key in latest:
            duplicates += 1
        else:
            order.append(key)
        latest[key] = record
    records = tuple(latest[key] for key in order)

    conservation = funnel_conservation(records)
    attributable = tuple(
        record
        for record in records
        if record.availability is FactAvailability.KNOWN
    )
    details: list[str] = []
    if duplicates:
        details.append(
            f"{duplicates} duplicate funnel row(s) ignored by (scan_id, candidate_id)"
        )
    if not conservation.holds:
        details.append(
            f"funnel conservation does not hold: {conservation.detail}"
        )

    resolved_trust = trust or TrustEnvelope(
        freshness=Freshness.LIVE,
        completeness=(
            Completeness.COMPLETE if conservation.holds else Completeness.INCOMPLETE
        ),
        reasons=() if conservation.holds else ("FUNNEL_CONSERVATION_INCOMPLETE",),
    )

    return QualificationFunnelProjection(
        generated_at=moment,
        records=records,
        producer_version=_producer_version(records),
        source_provenance=(
            "canonical O'Pip qualification funnel rows "
            "(app.opip.decision.store funnel_events.jsonl)"
        ),
        window_start=window_start,
        window_end=window_end,
        bucket_counts=_counts_by_disposition(attributable),
        gate_counts=_counts([record.terminal_gate or "" for record in attributable]),
        reason_code_counts=_counts(
            [record.terminal_reason_code or "" for record in attributable]
        ),
        duplicate_rows_ignored=duplicates,
        conservation=conservation,
        trust=resolved_trust,
        details=tuple(details),
    )


def unavailable_qualification_funnel(
    reason: str,
    *,
    generated_at: datetime | None = None,
) -> QualificationFunnelProjection:
    """The projection for evidence that could not be read.

    Structurally identical to a healthy projection (same keys, empty records) and
    explicitly ``UNAVAILABLE``/``UNKNOWN`` so it is never a healthy-looking empty
    result. ``bucket_counts`` is left empty rather than zeroed: "not read" and
    "read and empty" are different truths.
    """
    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return QualificationFunnelProjection(
        generated_at=moment.astimezone(timezone.utc),
        records=(),
        source_provenance=(
            "canonical O'Pip qualification funnel rows "
            "(app.opip.decision.store funnel_events.jsonl)"
        ),
        trust=unavailable_trust(str(reason)),
        details=(str(reason),),
    )


def read_qualification_funnel_projection(
    *,
    funnel_events_path: Any = None,
    generated_at: datetime | None = None,
    window_hours: int | None = None,
) -> QualificationFunnelProjection:
    """Read persisted qualification evidence and project it.

    Read-only. The canonical store's tolerant reader skips a single malformed
    line rather than failing the whole read, but it also returns ``[]`` for an
    absent file, so this function distinguishes an **absent** canonical evidence
    file (``UNAVAILABLE``) from a file that was read and held nothing (a healthy
    empty population). Resolves the default trading-host path only when a path is
    not supplied.
    """
    # Imported lazily so this pure read model does not pull the store's write
    # machinery or filesystem paths into module import time.
    from pathlib import Path

    from app.opip.decision.store import (
        FUNNEL_EVENTS_FILE,
        read_jsonl,
    )

    moment = generated_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    moment = moment.astimezone(timezone.utc)

    expected = Path(funnel_events_path or FUNNEL_EVENTS_FILE)
    if not expected.exists():
        return unavailable_qualification_funnel(
            "QUALIFICATION_EVIDENCE_ABSENT", generated_at=moment
        )
    try:
        rows = read_jsonl(expected)
    except OSError as exc:
        return unavailable_qualification_funnel(
            f"QUALIFICATION_EVIDENCE_UNREADABLE:{type(exc).__name__}",
            generated_at=moment,
        )

    window_start = None
    window_end = None
    if window_hours is not None:
        effective = max(1, int(window_hours))
        from datetime import timedelta

        window_end = moment
        window_start = moment - timedelta(hours=effective)

    return build_qualification_funnel_projection(
        rows,
        generated_at=moment,
        window_start=window_start,
        window_end=window_end,
    )


__all__ = [
    "DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER",
    "FunnelConservation",
    "QUALIFICATION_FUNNEL_AVAILABILITY_SEMANTICS",
    "QUALIFICATION_FUNNEL_POPULATION_SEMANTICS",
    "QUALIFICATION_FUNNEL_PRODUCER",
    "QUALIFICATION_FUNNEL_PROJECTION_VERSION",
    "QUALIFICATION_FUNNEL_SCHEMA_VERSION",
    "QUALIFICATION_FUNNEL_SCOPE",
    "QualificationDisposition",
    "QualificationFunnelProjection",
    "QualificationFunnelRecord",
    "QualificationGateObservation",
    "build_qualification_funnel_projection",
    "disposition_for",
    "funnel_conservation",
    "read_qualification_funnel_projection",
    "unavailable_qualification_funnel",
]
