"""Read-only Decision Intelligence evidence reader (v1).

Reconstructs P1A Decision Intelligence evidence that is *already recorded* in
the canonical SQLite WAL. This module is a read-only reconstruction surface: it
opens the canonical store through the canonical read-only connection mechanism,
freezes the recorded Decision Intelligence watermark, and interprets only
committed evidence at or before that boundary.

What this module deliberately does not do
-----------------------------------------
It never writes, never instantiates ``CanonicalWriter``, never creates a table,
never activates the Committee runtime, never calls a model, and never carries
trading, execution, alert, ranking, risk, or learning authority. It also does
not invent semantics P1A does not define:

* no authoritative "latest assessment" or correction-branch winner,
* no reconciled AI cost total,
* no Committee effectiveness or runtime-state claim,
* no baseline decision resolution and no trading recommendation.

Supersession edges are reconstructed as recorded evidence only. Every recorded
record stays in the snapshot, including superseded originals; selecting a winner
is not this reader's authority and no API here does it.

Watermark semantics
-------------------
The frozen boundary is the canonical per-stream watermark row for
``decision_intelligence.v1`` (``(history_epoch, local_sequence)`` commit order).

Repository evidence fixes what that row means. The canonical writer's
``_commit_new`` is the only writer of the ``watermarks`` table, and it upserts
``(stream, history_epoch, local_sequence)`` from the coordinate of the event it
is inserting, in the same transaction, before bumping ``meta``'s
``next_local_sequence``. It routes Decision Intelligence event types to
``decision_intelligence.v1`` and other traffic to their own streams, and it
creates a stream's row only when that stream commits. So the DI watermark row is
the *exact coordinate of the last committed DI event* — never a global event
tip, and never a boundary that can run ahead of DI evidence.

The boundary is therefore validated in both directions against DI-stream
evidence only (never against the global event tip, since non-DI streams share
the global sequence space):

* evidence recorded beyond the frozen watermark, or
* a watermark that claims progress with no corresponding recorded DI boundary
  event (including a watermark row with no DI evidence at all, or DI evidence
  with no watermark row),

is an inconsistent frozen boundary and fails closed. A stream with no DI
evidence, and no watermark row, is the valid empty state with boundary
``(0, 0)``.

A missing canonical database is a third, distinct state: the source of truth is
unavailable and nothing can be proven, so the read fails closed with
``DIEvidenceSourceUnavailableError`` rather than reporting an empty complete
snapshot. An *existing* canonical database with no DI evidence is the legitimate
empty state.

Lifecycle semantics
-------------------
Request lifecycle is replayed from recorded ``transition.recorded`` events in
canonical commit order ``(history_epoch, local_sequence)``, which is exactly the
order the writer used to validate them. A request with no transitions is
recorded-state ``ELIGIBLE``. Transitions that carry ``supersedes_id`` are
recorded corrections: they are preserved as evidence but do not advance the
reconstructed state, mirroring the canonical writer's own projection rule. A
non-superseding transition whose ``from_state`` does not match the state
reconstructed at that point, or that references a request with no recorded
``request.recorded`` event, is an impossible lifecycle and fails closed.

A recorded correction is admitted as a correction only after its supersession
edge is proven intact (target exists, and the lifecycle coordinates the writer
requires to be preserved are identical to the target's). A malformed correction
therefore fails closed rather than being classified into ``corrected_transitions``.

Known-record integrity
----------------------
Because the canonical writer validates ancestry and supersession before commit,
the recorded evidence it accepts satisfies a known set of relationships. The
reader re-derives those same relationships from recorded evidence, scoped to the
frozen boundary, and fails closed when any of them is impossible: a
``CommitteeRequest`` whose context is unrecorded or whose frozen snapshot link
does not match; a ``ModelInvocation``, ``CommitteeRoleResult``,
``CommitteeAssessmentSummary``, or ``ComparisonRecord`` whose request is
unrecorded; a role result or assessment invocation reference that is unrecorded
or belongs to a different request; an assessment role-result reference that is
unrecorded or cross-request; a comparison whose context, request/context link,
experiment, assessment, or invocation references disagree; a supersession
whose target is unrecorded or violates the writer's ownership constraints; and
role-result or assessment ``evidence_refs`` that are absent from the request's
frozen ``evidence_eligibility_manifest`` or not available at its
``evidence_cutoff`` (re-derived by reusing the ``DecisionContext`` contract's
own validation, not trusted).

Every one of those references is additionally required to have been committed
*before* the referencing event. The writer validates ancestry against already
committed evidence, so a target whose commit coordinate is not strictly earlier
than the source event is corrupt evidence, not history. Precedence uses the
repository's authoritative commit order ``(history_epoch, local_sequence)``
(``ConsumedInputWatermark``), never a payload or wall-clock timestamp: snapshot
membership alone can never admit a forward reference.

Failure semantics
-----------------
Fail closed (raise, never silently skip) when the source of truth is
unavailable or recorded known evidence cannot be safely interpreted: a missing
or unreadable canonical database; a canonical or event-envelope schema version
this build cannot interpret; a canonical read error; a committed known Decision
Intelligence payload that is not a JSON object, that carries an unsupported
payload ``schema_version``, that fails P1A validation, that cannot be hydrated
into its P1A contract, or whose hydrated form does not round-trip to the
committed canonical payload; a duplicate record identity; an impossible
lifecycle; any impossible relationship among known P1A records; an unresolved
or malformed supersession; and an inconsistent frozen boundary in either
direction.

Surface explicitly as ``DIEvidenceAnomaly`` evidence instead of failing closed
only where the reader's interpreted vocabulary is genuinely incomplete:
Decision Intelligence event types outside this vocabulary (forward
compatibility, payload left uninterpreted), and forked correction branches
(more than one recorded successor for one record). Both make the snapshot
``is_complete`` false. The reader never claims a complete snapshot it cannot
prove.

Correction branches can only fork, never cycle: P1A record identities are
content-derived and a supersession target must already be recorded at commit
time, so recorded supersession edges necessarily point at older evidence. A
forked branch is representable and is surfaced as ambiguous rather than
resolved.

Cost evidence is exposed raw per invocation. Incomplete or unknown cost is
allowed by the P1A ``ModelInvocation`` contract, so it is reported through
``cost_evidence_complete`` rather than treated as a reconstruction anomaly, and
no total is ever summed.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from app.opip.canonical.paths import EVENT_SCHEMA_VERSION, SCHEMA_VERSION
from app.opip.canonical.paths import db_path as canonical_db_path
from app.opip.contracts.identity import ConsumedInputWatermark
from app.opip.decision_intelligence.contracts import (
    AdvisoryStance,
    CommitteeAssessmentSummary,
    CommitteeRequest,
    CommitteeRequestTransition,
    CommitteeRole,
    CommitteeRoleResult,
    ComparisonRecord,
    ModelInvocation,
    RequestState,
    ResultDisposition,
)
from app.opip.decision_intelligence.events import (
    DECISION_INTELLIGENCE_ASSESSMENT_RECORDED,
    DECISION_INTELLIGENCE_COMPARISON_RECORDED,
    DECISION_INTELLIGENCE_CONTEXT_RECORDED,
    DECISION_INTELLIGENCE_INVOCATION_RECORDED,
    DECISION_INTELLIGENCE_REQUEST_RECORDED,
    DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED,
    DECISION_INTELLIGENCE_STREAM,
    DECISION_INTELLIGENCE_TRANSITION_RECORDED,
    validate_di_payload,
)
from app.opip.decision_intelligence.identity import DecisionContext, Provenance
from app.opip.decision_intelligence.serialization import canonicalize_nested

#: Every Decision Intelligence event type shares this stream namespace.
DI_EVENT_TYPE_PREFIX = "decision_intelligence."

#: Payload schema version this reader can interpret (P1A is exactly 1).
DI_PAYLOAD_SCHEMA_VERSION = 1

#: Anomaly codes. Explicit, bounded, and stable for downstream filtering.
#:
#: Anomalies describe *interpreted-vocabulary gaps*, never broken known-record
#: relationships: an unknown future event type stays opaque, and a forked
#: correction branch stays ambiguous. Impossible relationships among known P1A
#: records are canonical integrity failures and fail closed instead.
ANOMALY_UNKNOWN_EVENT_TYPE = "UNKNOWN_DI_EVENT_TYPE"
ANOMALY_AMBIGUOUS_SUPERSESSION = "AMBIGUOUS_SUPERSESSION"

DI_ANOMALY_CODES = frozenset(
    {
        ANOMALY_UNKNOWN_EVENT_TYPE,
        ANOMALY_AMBIGUOUS_SUPERSESSION,
    }
)

#: ``ModelInvocation`` permits unknown cost, but only under this marker.
_UNKNOWN_COST_COMPLETENESS = frozenset({"UNKNOWN", "INCOMPLETE"})

_RECORD_TYPES_BY_EVENT: Mapping[str, type] = MappingProxyType(
    {
        DECISION_INTELLIGENCE_CONTEXT_RECORDED: DecisionContext,
        DECISION_INTELLIGENCE_REQUEST_RECORDED: CommitteeRequest,
        DECISION_INTELLIGENCE_TRANSITION_RECORDED: CommitteeRequestTransition,
        DECISION_INTELLIGENCE_ROLE_RESULT_RECORDED: CommitteeRoleResult,
        DECISION_INTELLIGENCE_ASSESSMENT_RECORDED: CommitteeAssessmentSummary,
        DECISION_INTELLIGENCE_INVOCATION_RECORDED: ModelInvocation,
        DECISION_INTELLIGENCE_COMPARISON_RECORDED: ComparisonRecord,
    }
)

_IDENTITY_FIELDS: Mapping[type, str] = MappingProxyType(
    {
        DecisionContext: "context_id",
        CommitteeRequest: "request_id",
        CommitteeRequestTransition: "transition_id",
        CommitteeRoleResult: "result_id",
        CommitteeAssessmentSummary: "assessment_id",
        ModelInvocation: "invocation_id",
        ComparisonRecord: "comparison_id",
    }
)

_ENUM_FIELD_TYPES: Mapping[str, type] = MappingProxyType(
    {
        "from_state": RequestState,
        "to_state": RequestState,
        "result_disposition": ResultDisposition,
        "stance": AdvisoryStance,
        "advisory_stance": AdvisoryStance,
        "advisory_disposition": ResultDisposition,
        "role": CommitteeRole,
    }
)

_TIMESTAMP_FIELDS = frozenset(
    {
        "evaluation_time",
        "evidence_cutoff",
        "eligibility_at",
        "deadline_at",
        "enqueue_time",
        "transition_time",
        "completion_time",
        "commit_time",
        "started_at",
        "completed_at",
        "evaluation_window_start",
        "evaluation_window_end",
    }
)

_WATERMARK_FIELDS = frozenset({"consumed_input_watermark", "as_of_watermark"})

_SNAPSHOT_MAPPING_FIELDS = (
    "contexts",
    "requests",
    "transitions",
    "transition_history",
    "lifecycles",
    "request_states",
    "role_results",
    "assessments",
    "invocations",
    "comparisons",
    "provenance",
)


class DIEvidenceReaderError(ValueError):
    """Base class for read-only Decision Intelligence reconstruction failures."""


class DIEvidenceSourceUnavailableError(DIEvidenceReaderError):
    """The canonical source of truth is absent, so nothing can be proven.

    Distinct from integrity failure on purpose: "no Decision Intelligence
    evidence exists" and "the canonical store cannot be reached" are different
    states. A caller must never be told the stream is empty and complete when
    the source of truth was simply unavailable.
    """


class DIEvidenceIntegrityError(DIEvidenceReaderError):
    """Committed known Decision Intelligence evidence is corrupt or unusable."""


class DIIncompatibleSchemaError(DIEvidenceIntegrityError):
    """Canonical or Decision Intelligence schema cannot be safely interpreted."""


class DIInconsistentBoundaryError(DIEvidenceIntegrityError):
    """Decision Intelligence evidence exists beyond the frozen watermark."""


class DILifecycleReconstructionError(DIEvidenceIntegrityError):
    """Recorded transitions do not reconstruct a deterministic lifecycle."""


@dataclass(frozen=True)
class DIEvidenceAnomaly:
    """Explicitly surfaced ambiguity or incompleteness.

    Anomalies never invent a resolution. They mark that the snapshot cannot
    claim to be a complete reconstruction of the Decision Intelligence stream.
    """

    code: str
    detail: str
    event_id: str | None = None
    record_id: str | None = None
    history_epoch: int | None = None
    local_sequence: int | None = None


@dataclass(frozen=True)
class DIEvidenceEvent:
    """One interpreted Decision Intelligence event inside the frozen boundary."""

    event_id: str
    event_type: str
    record_id: str
    history_epoch: int
    local_sequence: int

    @property
    def coordinate(self) -> ConsumedInputWatermark:
        return ConsumedInputWatermark(
            history_epoch=self.history_epoch,
            local_sequence=self.local_sequence,
        )


@dataclass(frozen=True)
class UnknownDIEvidenceEvent:
    """A Decision Intelligence stream event outside this reader's vocabulary.

    Its payload is intentionally not interpreted. P1A does not define what it
    means, so the reader records that it exists and marks the snapshot
    incomplete instead of guessing.
    """

    event_id: str
    event_type: str
    history_epoch: int
    local_sequence: int

    @property
    def coordinate(self) -> ConsumedInputWatermark:
        return ConsumedInputWatermark(
            history_epoch=self.history_epoch,
            local_sequence=self.local_sequence,
        )


@dataclass(frozen=True)
class SupersessionEdge:
    """A recorded ``supersedes_id`` edge. Evidence only; never a winner pick.

    ``resolved`` is always ``True`` for an exposed edge: an unrecorded or
    ownership-violating target fails the read closed during resolution. It is
    retained so the verified invariant is visible in the evidence itself.
    """

    event_id: str
    record_id: str
    record_kind: str
    supersedes_id: str
    supersession_reason: str | None
    history_epoch: int
    local_sequence: int
    resolved: bool
    superseded_kind: str | None

    @property
    def coordinate(self) -> ConsumedInputWatermark:
        return ConsumedInputWatermark(
            history_epoch=self.history_epoch,
            local_sequence=self.local_sequence,
        )


@dataclass(frozen=True)
class RequestLifecycle:
    """Recorded request lifecycle through the frozen boundary.

    ``state`` is the reconstructable recorded state, not an assessment or
    correction winner. ``corrected_transitions`` holds recorded superseding
    corrections that were preserved but not applied, matching canonical writer
    projection semantics.
    """

    request_id: str
    initial_state: RequestState
    state: RequestState
    applied_transitions: tuple[CommitteeRequestTransition, ...]
    corrected_transitions: tuple[CommitteeRequestTransition, ...]


@dataclass(frozen=True)
class InvocationCostEvidence:
    """Raw per-invocation cost evidence. No total is implied or computed."""

    invocation_id: str
    request_id: str
    role: CommitteeRole
    attempt: int
    provider: str
    model: str
    price_version: str
    currency: str
    billed_cost_microunits: int | None
    estimated_cost_microunits: int | None
    reconciliation_status: str
    cost_completeness: str

    @property
    def complete(self) -> bool:
        """True only when P1A-complete cost was recorded for this invocation."""
        if self.cost_completeness in _UNKNOWN_COST_COMPLETENESS:
            return False
        return (
            self.billed_cost_microunits is not None
            or self.estimated_cost_microunits is not None
        )


@dataclass(frozen=True)
class DIEvidenceSnapshot:
    """Immutable reconstruction of recorded Decision Intelligence evidence."""

    boundary: ConsumedInputWatermark
    events: tuple[DIEvidenceEvent, ...]
    unknown_events: tuple[UnknownDIEvidenceEvent, ...]
    contexts: Mapping[str, DecisionContext]
    requests: Mapping[str, CommitteeRequest]
    transitions: Mapping[str, CommitteeRequestTransition]
    transition_history: Mapping[str, tuple[CommitteeRequestTransition, ...]]
    lifecycles: Mapping[str, RequestLifecycle]
    request_states: Mapping[str, RequestState]
    role_results: Mapping[str, CommitteeRoleResult]
    assessments: Mapping[str, CommitteeAssessmentSummary]
    invocations: Mapping[str, ModelInvocation]
    comparisons: Mapping[str, ComparisonRecord]
    provenance: Mapping[str, Provenance]
    supersession_edges: tuple[SupersessionEdge, ...]
    anomalies: tuple[DIEvidenceAnomaly, ...]

    def __post_init__(self) -> None:
        for name in _SNAPSHOT_MAPPING_FIELDS:
            object.__setattr__(
                self,
                name,
                MappingProxyType(dict(getattr(self, name))),
            )

    @property
    def is_complete(self) -> bool:
        """True only when no interpreted-vocabulary gap was found.

        False whenever any anomaly is recorded: a decision intelligence event
        type outside this reader's vocabulary, or a forked correction branch.
        Impossible relationships among known P1A records are not reported here
        at all: they fail the read closed.
        """
        return not self.anomalies

    @property
    def anomaly_codes(self) -> frozenset[str]:
        return frozenset(anomaly.code for anomaly in self.anomalies)

    def requests_for_context(self, context_id: str) -> tuple[str, ...]:
        return tuple(
            request_id
            for request_id, request in self.requests.items()
            if request.context_id == context_id
        )

    def transitions_for(self, request_id: str) -> tuple[CommitteeRequestTransition, ...]:
        return self.transition_history.get(request_id, ())

    def role_results_for(self, request_id: str) -> tuple[CommitteeRoleResult, ...]:
        return tuple(
            result
            for result in self.role_results.values()
            if result.request_id == request_id
        )

    def assessments_for(
        self, request_id: str
    ) -> tuple[CommitteeAssessmentSummary, ...]:
        return tuple(
            assessment
            for assessment in self.assessments.values()
            if assessment.request_id == request_id
        )

    def invocations_for(self, request_id: str) -> tuple[ModelInvocation, ...]:
        return tuple(
            invocation
            for invocation in self.invocations.values()
            if invocation.request_id == request_id
        )

    def comparisons_for(self, request_id: str) -> tuple[ComparisonRecord, ...]:
        return tuple(
            comparison
            for comparison in self.comparisons.values()
            if comparison.committee_request_id == request_id
        )

    def state_of(self, request_id: str) -> RequestState:
        return self.request_states[request_id]

    def supersessions_of(self, record_id: str) -> tuple[SupersessionEdge, ...]:
        """Edges recorded *by* ``record_id`` (it claims to supersede targets)."""
        return tuple(
            edge for edge in self.supersession_edges if edge.record_id == record_id
        )

    def superseded_by(self, record_id: str) -> tuple[SupersessionEdge, ...]:
        """Edges whose target is ``record_id``. More than one means forked."""
        return tuple(
            edge for edge in self.supersession_edges if edge.supersedes_id == record_id
        )

    def invocation_cost_evidence(self) -> tuple[InvocationCostEvidence, ...]:
        return tuple(
            InvocationCostEvidence(
                invocation_id=invocation.invocation_id,
                request_id=invocation.request_id,
                role=invocation.role,
                attempt=invocation.attempt,
                provider=invocation.provider,
                model=invocation.model,
                price_version=invocation.price_version,
                currency=invocation.currency,
                billed_cost_microunits=invocation.billed_cost_microunits,
                estimated_cost_microunits=invocation.estimated_cost_microunits,
                reconciliation_status=invocation.reconciliation_status,
                cost_completeness=invocation.cost_completeness,
            )
            for invocation in self.invocations.values()
        )

    @property
    def cost_evidence_complete(self) -> bool:
        """True only when every recorded invocation carries complete cost.

        A false value is *not* an integrity defect: P1A permits unknown cost.
        It only means no reconciled total could be derived, and none is.
        """
        return all(
            evidence.complete for evidence in self.invocation_cost_evidence()
        )


def read_di_evidence_snapshot(db_path: Path | None = None) -> DIEvidenceSnapshot:
    """Freeze the Decision Intelligence boundary and reconstruct evidence.

    ``db_path`` defaults to the canonical database path.

    A missing canonical database fails closed with
    ``DIEvidenceSourceUnavailableError``: the reader never creates the file,
    creates its parent directory, initializes schema, or instantiates the
    writer, and it never reports an empty snapshot as complete when the source
    of truth is unreachable. An *existing* canonical database that contains no
    Decision Intelligence evidence is a different state and does return a
    valid, complete, empty snapshot.

    Fails closed (see module docstring) instead of returning a truncated or
    silently repaired snapshot. Never mutates canonical storage.
    """
    target = Path(db_path) if db_path is not None else canonical_db_path()
    if not target.is_file():
        # Deliberately before any connection attempt: the canonical connect
        # helper creates parent directories, and an absent source of truth must
        # leave the filesystem untouched.
        raise DIEvidenceSourceUnavailableError(
            f"canonical database is not an existing file: {target}"
        )

    # Imported lazily so this module has no canonical-writer coupling at import
    # time, and so the read-only connection mechanism stays the canonical one.
    from app.opip.canonical.schema import connect

    try:
        connection = connect(target, read_only=True)
    except sqlite3.Error as exc:
        raise DIEvidenceIntegrityError(
            f"canonical database could not be opened read-only: {exc}"
        ) from exc

    try:
        try:
            # One read transaction: the frozen boundary, the DI evidence tip,
            # and the evidence read through it observe the same committed state.
            connection.execute("BEGIN")
            _assert_interpretable_schema(connection)
            watermark = _read_di_watermark_row(connection)
            di_event_count, di_tip = _read_di_event_tip(connection)
            boundary = _validate_boundary(
                watermark=watermark,
                di_event_count=di_event_count,
                di_tip=di_tip,
            )
            rows = _read_committed_di_events(connection, boundary)
        except sqlite3.Error as exc:
            raise DIEvidenceIntegrityError(
                f"canonical decision intelligence read failed: {exc}"
            ) from exc
    finally:
        # Closing a read-only connection rolls back the read transaction; no
        # canonical storage is written either way.
        connection.close()

    _assert_interpretable_envelopes(rows)
    return _reconstruct(boundary, rows)


def _assert_interpretable_schema(connection: sqlite3.Connection) -> None:
    """The canonical physical schema version must be one this build understands."""
    meta = connection.execute(
        "SELECT schema_version FROM meta WHERE id = 1"
    ).fetchone()
    if meta is None:
        raise DIIncompatibleSchemaError("canonical meta row is missing")
    version = meta["schema_version"]
    if type(version) is not int or version != SCHEMA_VERSION:
        raise DIIncompatibleSchemaError(
            f"canonical schema_version={version!r} incompatible with code "
            f"SCHEMA_VERSION={SCHEMA_VERSION}"
        )


def _read_di_watermark_row(
    connection: sqlite3.Connection,
) -> ConsumedInputWatermark | None:
    """Read the frozen DI stream watermark row, or ``None`` when absent.

    Repository evidence (canonical writer ``_commit_new``) shows the stream
    watermark is written in the same transaction as the event it describes, as
    that event's exact ``(history_epoch, local_sequence)`` coordinate. So this
    row is the last committed DI event coordinate, not a global event tip.
    """
    row = connection.execute(
        "SELECT history_epoch, local_sequence FROM watermarks WHERE stream = ?",
        (DECISION_INTELLIGENCE_STREAM,),
    ).fetchone()
    if row is None:
        return None

    history_epoch = row["history_epoch"]
    local_sequence = row["local_sequence"]
    if (
        type(history_epoch) is not int
        or type(local_sequence) is not int
        or history_epoch < 0
        or local_sequence < 0
    ):
        raise DIInconsistentBoundaryError(
            "canonical decision intelligence watermark row is invalid"
        )
    return ConsumedInputWatermark(
        history_epoch=history_epoch,
        local_sequence=local_sequence,
    )


def _read_di_event_tip(
    connection: sqlite3.Connection,
) -> tuple[int, ConsumedInputWatermark | None]:
    """Count and highest coordinate of recorded DI-stream evidence.

    Scoped to ``decision_intelligence.*`` only: non-DI streams share the global
    sequence space, so the global event tip is never the right comparison for
    the DI boundary. Unknown future DI event types are included, because the
    stream watermark describes stream progress, not this reader's vocabulary.
    """
    count_row = connection.execute(
        "SELECT COUNT(*) AS n FROM events WHERE event_type GLOB ?",
        (f"{DI_EVENT_TYPE_PREFIX}*",),
    ).fetchone()
    count = int(count_row["n"]) if count_row is not None else 0
    if count == 0:
        return 0, None

    tip_row = connection.execute(
        """
        SELECT history_epoch, local_sequence
        FROM events
        WHERE event_type GLOB ?
        ORDER BY history_epoch DESC, local_sequence DESC
        LIMIT 1
        """,
        (f"{DI_EVENT_TYPE_PREFIX}*",),
    ).fetchone()
    if tip_row is None:
        # Counted rows are necessarily selectable in the same transaction.
        raise DIInconsistentBoundaryError(
            "decision intelligence evidence count and tip disagree"
        )
    return count, ConsumedInputWatermark(
        history_epoch=int(tip_row["history_epoch"]),
        local_sequence=int(tip_row["local_sequence"]),
    )


def _validate_boundary(
    *,
    watermark: ConsumedInputWatermark | None,
    di_event_count: int,
    di_tip: ConsumedInputWatermark | None,
) -> ConsumedInputWatermark:
    """Validate the frozen DI boundary in both directions.

    The canonical writer creates a stream's watermark row only when that stream
    commits an event, in the same transaction, at that event's exact
    coordinate. Therefore the DI watermark must equal the highest recorded DI
    event coordinate: a watermark behind it means evidence exists beyond the
    boundary, and a watermark ahead of it claims progress with no corresponding
    evidence. Both are inconsistent and fail closed.

    A stream with no DI evidence at all is the valid zero/empty state.
    """
    if di_event_count == 0:
        if watermark is not None:
            raise DIInconsistentBoundaryError(
                "canonical decision intelligence watermark "
                f"{watermark.to_dict()} exists but no decision intelligence "
                "evidence is recorded"
            )
        return ConsumedInputWatermark.zero()

    if di_tip is None:
        raise DIInconsistentBoundaryError(
            "decision intelligence evidence exists but its highest coordinate "
            "could not be read"
        )
    if watermark is None:
        raise DIInconsistentBoundaryError(
            f"decision intelligence evidence exists through {di_tip.to_dict()} "
            "but the canonical stream watermark is missing"
        )
    if watermark > di_tip:
        raise DIInconsistentBoundaryError(
            f"frozen canonical watermark {watermark.to_dict()} claims progress "
            f"beyond the highest recorded decision intelligence evidence "
            f"{di_tip.to_dict()}; no matching boundary event exists"
        )
    if watermark < di_tip:
        raise DIInconsistentBoundaryError(
            "recorded decision intelligence evidence exists beyond the frozen "
            f"canonical watermark {watermark.to_dict()}: highest recorded "
            f"evidence is {di_tip.to_dict()} across {di_event_count} event(s)"
        )
    return watermark


def _read_committed_di_events(
    connection: sqlite3.Connection,
    boundary: ConsumedInputWatermark,
) -> tuple[sqlite3.Row, ...]:
    return tuple(
        connection.execute(
            """
            SELECT event_id, event_type, history_epoch, local_sequence,
                   schema_version, payload_json
            FROM events
            WHERE event_type GLOB ?
              AND (
                    history_epoch < ?
                    OR (history_epoch = ? AND local_sequence <= ?)
                  )
            ORDER BY history_epoch ASC, local_sequence ASC
            """,
            (
                f"{DI_EVENT_TYPE_PREFIX}*",
                boundary.history_epoch,
                boundary.history_epoch,
                boundary.local_sequence,
            ),
        ).fetchall()
    )


def _assert_interpretable_envelopes(rows: tuple[sqlite3.Row, ...]) -> None:
    """Validate the canonical event-envelope version before interpretation.

    The physical envelope version in ``events.schema_version`` is written by the
    canonical writer from ``EVENT_SCHEMA_VERSION`` and is independent of both
    ``meta.schema_version`` and the DI payload ``schema_version``. It applies to
    every Decision Intelligence event inside the frozen snapshot, known or
    unknown: an unknown event *type* may stay opaque, but the envelope that
    carries it must still be interpretable before anything is read out of it.
    """
    for row in rows:
        version = row["schema_version"]
        if type(version) is not int or version != EVENT_SCHEMA_VERSION:
            raise DIIncompatibleSchemaError(
                f"canonical event envelope schema_version={version!r} for event "
                f"{row['event_id']} ({row['event_type']}) is not interpretable "
                f"by this build (supported {EVENT_SCHEMA_VERSION})"
            )


def _reconstruct(
    boundary: ConsumedInputWatermark,
    rows: tuple[sqlite3.Row, ...],
) -> DIEvidenceSnapshot:
    by_type: dict[type, dict[str, Any]] = {
        record_type: {} for record_type in _RECORD_TYPES_BY_EVENT.values()
    }
    events: list[DIEvidenceEvent] = []
    unknown_events: list[UnknownDIEvidenceEvent] = []
    anomalies: list[DIEvidenceAnomaly] = []
    event_id_by_record_id: dict[str, str] = {}
    coordinate_by_record_id: dict[str, ConsumedInputWatermark] = {}
    edge_drafts: list[tuple[str, str, type, Any, ConsumedInputWatermark]] = []
    transitions_in_order: list[CommitteeRequestTransition] = []
    transitions_by_request: dict[str, list[CommitteeRequestTransition]] = {}

    for row in rows:
        event_id = str(row["event_id"])
        event_type = str(row["event_type"])
        coordinate = ConsumedInputWatermark(
            history_epoch=int(row["history_epoch"]),
            local_sequence=int(row["local_sequence"]),
        )
        record_type = _RECORD_TYPES_BY_EVENT.get(event_type)
        if record_type is None:
            unknown_events.append(
                UnknownDIEvidenceEvent(
                    event_id=event_id,
                    event_type=event_type,
                    history_epoch=coordinate.history_epoch,
                    local_sequence=coordinate.local_sequence,
                )
            )
            anomalies.append(
                DIEvidenceAnomaly(
                    code=ANOMALY_UNKNOWN_EVENT_TYPE,
                    detail=(
                        "decision intelligence event type is outside this "
                        f"reader's interpreted vocabulary: {event_type}"
                    ),
                    event_id=event_id,
                    history_epoch=coordinate.history_epoch,
                    local_sequence=coordinate.local_sequence,
                )
            )
            continue

        payload = _decode_known_payload(
            event_id=event_id,
            event_type=event_type,
            payload_json=str(row["payload_json"]),
        )
        record = _hydrate_record(
            event_id=event_id,
            event_type=event_type,
            record_type=record_type,
            payload=payload,
        )
        record_id = str(getattr(record, _IDENTITY_FIELDS[record_type]))
        store = by_type[record_type]
        if record_id in store:
            raise DIEvidenceIntegrityError(
                f"duplicate {record_type.__name__} identity {record_id} recorded "
                f"at {coordinate.to_dict()}; canonical record identity is not unique"
            )
        store[record_id] = record
        event_id_by_record_id[record_id] = event_id
        coordinate_by_record_id[record_id] = coordinate
        events.append(
            DIEvidenceEvent(
                event_id=event_id,
                event_type=event_type,
                record_id=record_id,
                history_epoch=coordinate.history_epoch,
                local_sequence=coordinate.local_sequence,
            )
        )

        supersedes_id = getattr(record, "supersedes_id")
        if supersedes_id is not None:
            edge_drafts.append(
                (event_id, record_id, record_type, record, coordinate)
            )

        if record_type is CommitteeRequestTransition:
            transitions_in_order.append(record)
            transitions_by_request.setdefault(record.request_id, []).append(record)

    contexts = by_type[DecisionContext]
    requests = by_type[CommitteeRequest]

    # Fail closed on impossible known-record relationships, frozen-manifest
    # evidence eligibility, commit-order precedence, and supersession before any
    # of that evidence is classified or exposed. Supersession is resolved before
    # lifecycle reconstruction so a malformed correction can never be admitted
    # into a request's corrected-transition history.
    _assert_known_record_integrity(
        by_type=by_type,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )
    _assert_evidence_ref_eligibility(
        by_type=by_type,
        event_id_by_record_id=event_id_by_record_id,
    )
    supersession_edges = _resolve_supersession_edges(
        edge_drafts=edge_drafts,
        by_type=by_type,
        coordinate_by_record_id=coordinate_by_record_id,
    )
    lifecycles = _reconstruct_lifecycles(
        requests=requests,
        transitions_in_order=transitions_in_order,
        transitions_by_request=transitions_by_request,
        coordinate_by_record_id=coordinate_by_record_id,
        event_id_by_record_id=event_id_by_record_id,
    )
    anomalies.extend(_supersession_fork_anomalies(supersession_edges))

    return DIEvidenceSnapshot(
        boundary=boundary,
        events=tuple(events),
        unknown_events=tuple(unknown_events),
        contexts=dict(contexts),
        requests=dict(requests),
        transitions=dict(by_type[CommitteeRequestTransition]),
        transition_history={
            request_id: tuple(ordered)
            for request_id, ordered in transitions_by_request.items()
        },
        lifecycles=lifecycles,
        request_states={
            request_id: lifecycle.state
            for request_id, lifecycle in lifecycles.items()
        },
        role_results=dict(by_type[CommitteeRoleResult]),
        assessments=dict(by_type[CommitteeAssessmentSummary]),
        invocations=dict(by_type[ModelInvocation]),
        comparisons=dict(by_type[ComparisonRecord]),
        provenance={
            record_id: record.provenance
            for store in by_type.values()
            for record_id, record in store.items()
        },
        supersession_edges=supersession_edges,
        anomalies=_sorted_anomalies(anomalies),
    )


def _reconstruct_lifecycles(
    *,
    requests: Mapping[str, CommitteeRequest],
    transitions_in_order: list[CommitteeRequestTransition],
    transitions_by_request: Mapping[str, list[CommitteeRequestTransition]],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
    event_id_by_record_id: Mapping[str, str],
) -> dict[str, RequestLifecycle]:
    for transition in transitions_in_order:
        if transition.request_id not in requests:
            raise DILifecycleReconstructionError(
                f"recorded transition {transition.transition_id} references "
                f"request {transition.request_id} with no recorded "
                "request.recorded event"
            )
        # Membership alone cannot establish history: the request must already
        # have been recorded when the transition commits.
        _assert_committed_before(
            field_name="CommitteeRequestTransition.request_id",
            source_id=transition.transition_id,
            target_id=transition.request_id,
            source_coordinate=coordinate_by_record_id[transition.transition_id],
            target_coordinate=coordinate_by_record_id[transition.request_id],
            origin=_record_origin(
                event_id_by_record_id, transition.transition_id
            ),
        )

    lifecycles: dict[str, RequestLifecycle] = {}
    for request_id in requests:
        current = RequestState.ELIGIBLE
        applied: list[CommitteeRequestTransition] = []
        corrected: list[CommitteeRequestTransition] = []
        for transition in transitions_by_request.get(request_id, ()):
            if transition.supersedes_id is not None:
                # Recorded correction: preserved as evidence, never applied.
                corrected.append(transition)
                continue
            if transition.from_state is not current:
                raise DILifecycleReconstructionError(
                    f"recorded transition {transition.transition_id} moves "
                    f"{transition.from_state.value} -> "
                    f"{transition.to_state.value} at "
                    f"({transition.transition_time.isoformat()}) but the "
                    f"reconstructed state for request {request_id} was "
                    f"{current.value}"
                )
            current = transition.to_state
            applied.append(transition)
        lifecycles[request_id] = RequestLifecycle(
            request_id=request_id,
            initial_state=RequestState.ELIGIBLE,
            state=current,
            applied_transitions=tuple(applied),
            corrected_transitions=tuple(corrected),
        )
    return lifecycles


_SUPERSESSION_OWNERSHIP_FIELDS: Mapping[type, tuple[str, ...]] = MappingProxyType(
    {
        DecisionContext: (),
        CommitteeRequest: ("context_id",),
        ModelInvocation: ("request_id",),
        CommitteeRoleResult: ("request_id",),
        CommitteeAssessmentSummary: ("request_id",),
        ComparisonRecord: ("committee_request_id", "decision_context_id"),
    }
)


def _record_origin(
    event_id_by_record_id: Mapping[str, str],
    record_id: str,
) -> str:
    """Render the recording-event suffix for a failure message, when known."""
    event_id = event_id_by_record_id.get(record_id)
    return f" (event {event_id})" if event_id else ""


def _assert_committed_before(
    *,
    field_name: str,
    source_id: str,
    target_id: str,
    source_coordinate: ConsumedInputWatermark,
    target_coordinate: ConsumedInputWatermark,
    origin: str,
) -> None:
    """Fail closed when a reference points forward in canonical commit order.

    The canonical writer requires every ancestry and supersession target to be
    already recorded when the source event commits, so a target committed at or
    after the source coordinate is corrupt evidence, not a reconstruction.

    Ordering is the repository's authoritative commit order
    ``(history_epoch, local_sequence)`` (``ConsumedInputWatermark``), never a
    payload or wall-clock timestamp. Canonical coordinates are unique per event
    (``UNIQUE (history_epoch, local_sequence)``), so ``<`` means strictly
    committed earlier and a self-reference can never pass.
    """
    if target_coordinate < source_coordinate:
        return
    raise DIEvidenceIntegrityError(
        f"{field_name} of {source_id} references {target_id}, committed at "
        f"{target_coordinate.to_dict()}, which is not earlier than the source "
        f"event commit coordinate {source_coordinate.to_dict()}{origin}; the "
        "canonical writer only accepts a reference that is already recorded "
        "when the source event commits"
    )


def _require_recorded_target(
    store: Mapping[str, Any],
    *,
    target_id: str,
    field_name: str,
    source_id: str,
    target_kind: str,
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> Any:
    """Return a referenced record, or fail closed on an invalid reference.

    Frozen-snapshot membership alone is insufficient: the target must also have
    been committed before the source event, matching the writer's ancestry
    guarantee.
    """
    target = store.get(target_id)
    if target is None:
        raise DIEvidenceIntegrityError(
            f"{field_name} of {source_id} must reference recorded "
            f"{target_kind} evidence, but {target_id} is absent"
            f"{_record_origin(event_id_by_record_id, source_id)}; the canonical "
            "writer rejects an unrecorded reference at commit time"
        )
    _assert_committed_before(
        field_name=field_name,
        source_id=source_id,
        target_id=target_id,
        source_coordinate=coordinate_by_record_id[source_id],
        target_coordinate=coordinate_by_record_id[target_id],
        origin=_record_origin(event_id_by_record_id, source_id),
    )
    return target


def _require_owning_request(
    *,
    target: Any,
    owner_id: str,
    field_name: str,
    source_id: str,
    target_id: str,
    event_id_by_record_id: Mapping[str, str],
) -> None:
    """Fail closed when a referenced record belongs to a different request."""
    if target.request_id != owner_id:
        raise DIEvidenceIntegrityError(
            f"{field_name} of {source_id} references {target_id} which "
            f"belongs to request {target.request_id}, not {owner_id}"
            f"{_record_origin(event_id_by_record_id, source_id)}"
        )


def _assert_request_context_integrity(
    *,
    contexts: Mapping[str, Any],
    requests: Mapping[str, Any],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """CommitteeRequest -> DecisionContext, including the frozen snapshot link."""
    for request in requests.values():
        context = _require_recorded_target(
            contexts,
            target_id=request.context_id,
            field_name="CommitteeRequest.context_id",
            source_id=request.request_id,
            target_kind="DecisionContext",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        try:
            request.validate_against_context(context)
        except ValueError as exc:
            raise DIEvidenceIntegrityError(
                f"CommitteeRequest {request.request_id} is not valid against "
                f"DecisionContext {context.context_id}: {exc}"
                f"{_record_origin(event_id_by_record_id, request.request_id)}"
            ) from exc


def _assert_invocation_request_integrity(
    *,
    invocations: Mapping[str, Any],
    requests: Mapping[str, Any],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """ModelInvocation -> CommitteeRequest."""
    for invocation in invocations.values():
        _require_recorded_target(
            requests,
            target_id=invocation.request_id,
            field_name="ModelInvocation.request_id",
            source_id=invocation.invocation_id,
            target_kind="CommitteeRequest",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )


def _assert_role_result_integrity(
    *,
    role_results: Mapping[str, Any],
    requests: Mapping[str, Any],
    invocations: Mapping[str, Any],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """CommitteeRoleResult -> request, and its invocation owns the request."""
    for result in role_results.values():
        _require_recorded_target(
            requests,
            target_id=result.request_id,
            field_name="CommitteeRoleResult.request_id",
            source_id=result.result_id,
            target_kind="CommitteeRequest",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        if result.invocation_ref is None:
            continue
        invocation = _require_recorded_target(
            invocations,
            target_id=result.invocation_ref,
            field_name="CommitteeRoleResult.invocation_ref",
            source_id=result.result_id,
            target_kind="ModelInvocation",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        _require_owning_request(
            target=invocation,
            owner_id=result.request_id,
            field_name="CommitteeRoleResult.invocation_ref",
            source_id=result.result_id,
            target_id=result.invocation_ref,
            event_id_by_record_id=event_id_by_record_id,
        )


def _assert_assessment_integrity(
    *,
    assessments: Mapping[str, Any],
    requests: Mapping[str, Any],
    role_results: Mapping[str, Any],
    invocations: Mapping[str, Any],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """CommitteeAssessmentSummary -> request, role results, and invocations."""
    for assessment in assessments.values():
        _require_recorded_target(
            requests,
            target_id=assessment.request_id,
            field_name="CommitteeAssessmentSummary.request_id",
            source_id=assessment.assessment_id,
            target_kind="CommitteeRequest",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        for result_id in assessment.referenced_role_result_ids:
            role_result = _require_recorded_target(
                role_results,
                target_id=result_id,
                field_name=(
                    "CommitteeAssessmentSummary.referenced_role_result_ids"
                ),
                source_id=assessment.assessment_id,
                target_kind="CommitteeRoleResult",
                event_id_by_record_id=event_id_by_record_id,
                coordinate_by_record_id=coordinate_by_record_id,
            )
            _require_owning_request(
                target=role_result,
                owner_id=assessment.request_id,
                field_name=(
                    "CommitteeAssessmentSummary.referenced_role_result_ids"
                ),
                source_id=assessment.assessment_id,
                target_id=result_id,
                event_id_by_record_id=event_id_by_record_id,
            )
        for invocation_id in assessment.invocation_references:
            invocation = _require_recorded_target(
                invocations,
                target_id=invocation_id,
                field_name="CommitteeAssessmentSummary.invocation_references",
                source_id=assessment.assessment_id,
                target_kind="ModelInvocation",
                event_id_by_record_id=event_id_by_record_id,
                coordinate_by_record_id=coordinate_by_record_id,
            )
            _require_owning_request(
                target=invocation,
                owner_id=assessment.request_id,
                field_name="CommitteeAssessmentSummary.invocation_references",
                source_id=assessment.assessment_id,
                target_id=invocation_id,
                event_id_by_record_id=event_id_by_record_id,
            )


def _assert_comparison_integrity(
    *,
    comparisons: Mapping[str, Any],
    requests: Mapping[str, Any],
    contexts: Mapping[str, Any],
    assessments: Mapping[str, Any],
    invocations: Mapping[str, Any],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """ComparisonRecord -> request, context, and its request/context link."""
    for comparison in comparisons.values():
        request = _require_recorded_target(
            requests,
            target_id=comparison.committee_request_id,
            field_name="ComparisonRecord.committee_request_id",
            source_id=comparison.comparison_id,
            target_kind="CommitteeRequest",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        _require_recorded_target(
            contexts,
            target_id=comparison.decision_context_id,
            field_name="ComparisonRecord.decision_context_id",
            source_id=comparison.comparison_id,
            target_kind="DecisionContext",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        if request.context_id != comparison.decision_context_id:
            raise DIEvidenceIntegrityError(
                f"ComparisonRecord {comparison.comparison_id} claims context "
                f"{comparison.decision_context_id} but its request "
                f"{comparison.committee_request_id} belongs to context "
                f"{request.context_id}"
                f"{_record_origin(event_id_by_record_id, comparison.comparison_id)}"
            )
        if request.experiment_id != comparison.experiment_id:
            raise DIEvidenceIntegrityError(
                f"ComparisonRecord {comparison.comparison_id} experiment "
                f"{comparison.experiment_id} does not agree with request "
                f"{comparison.committee_request_id} experiment "
                f"{request.experiment_id}"
                f"{_record_origin(event_id_by_record_id, comparison.comparison_id)}"
            )
        assessment = _require_recorded_target(
            assessments,
            target_id=comparison.committee_assessment_id,
            field_name="ComparisonRecord.committee_assessment_id",
            source_id=comparison.comparison_id,
            target_kind="CommitteeAssessmentSummary",
            event_id_by_record_id=event_id_by_record_id,
            coordinate_by_record_id=coordinate_by_record_id,
        )
        if assessment.request_id != comparison.committee_request_id:
            raise DIEvidenceIntegrityError(
                f"ComparisonRecord {comparison.comparison_id} assessment "
                f"{comparison.committee_assessment_id} belongs to request "
                f"{assessment.request_id}, not "
                f"{comparison.committee_request_id}"
                f"{_record_origin(event_id_by_record_id, comparison.comparison_id)}"
            )
        for invocation_id in comparison.invocation_refs:
            invocation = _require_recorded_target(
                invocations,
                target_id=invocation_id,
                field_name="ComparisonRecord.invocation_refs",
                source_id=comparison.comparison_id,
                target_kind="ModelInvocation",
                event_id_by_record_id=event_id_by_record_id,
                coordinate_by_record_id=coordinate_by_record_id,
            )
            _require_owning_request(
                target=invocation,
                owner_id=comparison.committee_request_id,
                field_name="ComparisonRecord.invocation_refs",
                source_id=comparison.comparison_id,
                target_id=invocation_id,
                event_id_by_record_id=event_id_by_record_id,
            )


def _assert_known_record_integrity(
    *,
    by_type: Mapping[type, Mapping[str, Any]],
    event_id_by_record_id: Mapping[str, str],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> None:
    """Re-derive the canonical writer's recorded-evidence relationships.

    The writer validates ancestry before commit, so an impossible relationship
    here is not incompleteness: it is canonical integrity failure and the read
    fails closed. Each check mirrors a specific writer guard, named in the
    failure message so an operator can trace it. Unknown event types are not
    inspected (their payloads stay opaque) and forked supersession branches are
    not failures (they remain explicit ambiguity).

    Every resolved target must also have been committed before the referencing
    event, so snapshot membership alone never admits a forward reference.

    The per-record-kind checks live in small private helpers and are invoked
    here in the same order their record kinds are recorded in.
    """
    contexts = by_type[DecisionContext]
    requests = by_type[CommitteeRequest]
    role_results = by_type[CommitteeRoleResult]
    assessments = by_type[CommitteeAssessmentSummary]
    invocations = by_type[ModelInvocation]
    comparisons = by_type[ComparisonRecord]

    # CommitteeRequest -> DecisionContext, including the frozen snapshot link.
    _assert_request_context_integrity(
        contexts=contexts,
        requests=requests,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )

    # ModelInvocation -> CommitteeRequest.
    _assert_invocation_request_integrity(
        invocations=invocations,
        requests=requests,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )

    # CommitteeRoleResult -> CommitteeRequest, and its invocation owns the request.
    _assert_role_result_integrity(
        role_results=role_results,
        requests=requests,
        invocations=invocations,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )

    # CommitteeAssessmentSummary -> request, role results, and invocations.
    _assert_assessment_integrity(
        assessments=assessments,
        requests=requests,
        role_results=role_results,
        invocations=invocations,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )

    # ComparisonRecord -> request, context, and its request/context link.
    _assert_comparison_integrity(
        comparisons=comparisons,
        requests=requests,
        contexts=contexts,
        assessments=assessments,
        invocations=invocations,
        event_id_by_record_id=event_id_by_record_id,
        coordinate_by_record_id=coordinate_by_record_id,
    )


def _assert_evidence_ref_eligibility(
    *,
    by_type: Mapping[type, Mapping[str, Any]],
    event_id_by_record_id: Mapping[str, str],
) -> None:
    """Re-derive the writer's frozen-manifest evidence eligibility guard.

    The writer validates ``evidence_refs`` on role results and assessments
    against the request's frozen ``DecisionContext``
    (``_validate_di_evidence_eligibility`` -> ``_validate_evidence_ref``). The
    reader re-derives the same invariant from recorded evidence rather than
    trusting it, resolving request -> context and then reusing the contract's
    own ``DecisionContext.validate_evidence_refs`` so the semantics are not
    reimplemented here.

    ``evidence_refs`` is optional, so an empty tuple is valid and stays valid.
    """
    contexts = by_type[DecisionContext]
    # Not named ``requests``: that shadows the HTTP library name and trips
    # bandit's B113 false positive (request_without_timeout).
    committee_requests = by_type[CommitteeRequest]
    records = (
        *by_type[CommitteeRoleResult].values(),
        *by_type[CommitteeAssessmentSummary].values(),
    )
    for record in records:
        evidence_refs = record.evidence_refs
        if not evidence_refs:
            continue
        record_id = str(getattr(record, _IDENTITY_FIELDS[type(record)]))
        event_id = event_id_by_record_id.get(record_id)
        origin = f" (event {event_id})" if event_id else ""

        request = committee_requests.get(record.request_id)
        if request is None:
            raise DIEvidenceIntegrityError(
                f"{type(record).__name__} {record_id} carries evidence_refs but "
                f"its request {record.request_id} is not recorded{origin}"
            )
        context = contexts.get(request.context_id)
        if context is None:
            raise DIEvidenceIntegrityError(
                f"{type(record).__name__} {record_id} carries evidence_refs but "
                f"the DecisionContext {request.context_id} of its request "
                f"{record.request_id} is not recorded{origin}"
            )
        try:
            context.validate_evidence_refs(evidence_refs)
        except ValueError as exc:
            raise DIEvidenceIntegrityError(
                f"{type(record).__name__} {record_id} evidence_refs are not "
                f"eligible against the frozen DecisionContext "
                f"{context.context_id}{origin}: {exc}"
            ) from exc


def _resolve_supersession_edges(
    *,
    edge_drafts: list[tuple[str, str, type, Any, ConsumedInputWatermark]],
    by_type: Mapping[type, Mapping[str, Any]],
    coordinate_by_record_id: Mapping[str, ConsumedInputWatermark],
) -> tuple[SupersessionEdge, ...]:
    """Resolve recorded supersession edges, mirroring the writer's guards.

    The writer requires a supersession target to be an already-recorded record
    of the same kind, committed before the superseding event, and to satisfy a
    per-kind ownership constraint. A target that is absent, forwards in commit
    order, or violates the constraint, is canonical integrity failure, not
    incompleteness. Every returned edge is therefore resolved; ``resolved`` is
    retained to record that the check ran.
    """
    edges: list[SupersessionEdge] = []
    for event_id, record_id, record_type, record, coordinate in edge_drafts:
        supersedes_id = str(record.supersedes_id)
        same_kind_store = by_type[record_type]
        target = same_kind_store.get(supersedes_id)
        record_kind = _event_type_for(record_type)
        if target is None:
            raise DIEvidenceIntegrityError(
                f"{record_kind} {record_id} supersedes {supersedes_id}, which "
                f"is not a recorded record of the same kind (event {event_id}); "
                "the canonical writer rejects an unrecorded supersession target "
                "at commit time"
            )
        _assert_committed_before(
            field_name=f"{record_kind}.supersedes_id",
            source_id=record_id,
            target_id=supersedes_id,
            source_coordinate=coordinate,
            target_coordinate=coordinate_by_record_id[supersedes_id],
            origin=f" (event {event_id})",
        )
        _assert_supersession_ownership(
            record=record,
            target=target,
            record_type=record_type,
            event_id=event_id,
        )
        edges.append(
            SupersessionEdge(
                event_id=event_id,
                record_id=record_id,
                record_kind=record_kind,
                supersedes_id=supersedes_id,
                supersession_reason=record.supersession_reason,
                history_epoch=coordinate.history_epoch,
                local_sequence=coordinate.local_sequence,
                resolved=True,
                superseded_kind=record_kind,
            )
        )
    return tuple(edges)


def _assert_supersession_ownership(
    *,
    record: Any,
    target: Any,
    record_type: type,
    event_id: str,
) -> None:
    """Reproduce the writer's per-kind supersession ownership constraints."""
    if record_type is CommitteeRequestTransition:
        # Transitions are not covered by the generic field map: the writer
        # requires a correction to preserve the full lifecycle coordinate.
        for field_name in (
            "request_id",
            "from_state",
            "to_state",
            "transition_time",
        ):
            if getattr(record, field_name) != getattr(target, field_name):
                raise DIEvidenceIntegrityError(
                    "transition supersession must preserve lifecycle "
                    f"coordinate {field_name}: {record.transition_id} supersedes "
                    f"{target.transition_id} (event {event_id})"
                )
        return

    for field_name in _SUPERSESSION_OWNERSHIP_FIELDS[record_type]:
        if getattr(record, field_name) != getattr(target, field_name):
            raise DIEvidenceIntegrityError(
                f"{_event_type_for(record_type)} {getattr(record, _IDENTITY_FIELDS[record_type])} "
                f"supersedes {target.__class__.__name__} "
                f"{getattr(target, _IDENTITY_FIELDS[record_type])} with a "
                f"different {field_name} (event {event_id})"
            )


def _supersession_fork_anomalies(
    edges: tuple[SupersessionEdge, ...],
) -> list[DIEvidenceAnomaly]:
    """Record forked correction branches. A fork is ambiguity, not failure.

    Unresolved supersession never reaches here: it fails closed during edge
    resolution. More than one recorded successor for one target is representable
    evidence, and P1A defines no winner, so it is surfaced rather than resolved.
    """
    anomalies: list[DIEvidenceAnomaly] = []

    successors: dict[str, list[SupersessionEdge]] = {}
    for edge in edges:
        successors.setdefault(edge.supersedes_id, []).append(edge)

    for target_id, target_edges in successors.items():
        if len(target_edges) < 2:
            continue
        sources = sorted(edge.record_id for edge in target_edges)
        anomalies.append(
            DIEvidenceAnomaly(
                code=ANOMALY_AMBIGUOUS_SUPERSESSION,
                detail=(
                    f"{target_id} is superseded by multiple recorded branches "
                    f"{sources}; P1A defines no winner and none is selected"
                ),
                record_id=target_id,
            )
        )
    return anomalies


def _sorted_anomalies(
    anomalies: list[DIEvidenceAnomaly],
) -> tuple[DIEvidenceAnomaly, ...]:
    return tuple(
        sorted(
            anomalies,
            key=lambda anomaly: (
                anomaly.code,
                -1 if anomaly.history_epoch is None else anomaly.history_epoch,
                -1 if anomaly.local_sequence is None else anomaly.local_sequence,
                anomaly.record_id or "",
                anomaly.detail,
            ),
        )
    )


def _event_type_for(record_type: type) -> str:
    for event_type, candidate in _RECORD_TYPES_BY_EVENT.items():
        if candidate is record_type:
            return event_type
    raise DIEvidenceIntegrityError(
        f"no canonical event type maps to {record_type.__name__}"
    )


def _decode_known_payload(
    *,
    event_id: str,
    event_type: str,
    payload_json: str,
) -> dict[str, Any]:
    """Decode and P1A-validate one committed known Decision Intelligence payload."""
    try:
        raw = json.loads(payload_json)
    except (TypeError, ValueError) as exc:
        raise DIEvidenceIntegrityError(
            f"committed {event_type} payload_json is not valid JSON "
            f"(event {event_id})"
        ) from exc
    if not isinstance(raw, dict):
        raise DIEvidenceIntegrityError(
            f"committed {event_type} payload_json did not decode to a JSON "
            f"object (got {type(raw).__name__}, event {event_id}); refusing to "
            "silently skip malformed canonical evidence"
        )

    version = raw.get("schema_version")
    if type(version) is not int or version != DI_PAYLOAD_SCHEMA_VERSION:
        raise DIIncompatibleSchemaError(
            f"committed {event_type} payload schema_version={version!r} is not "
            f"interpretable by this build (supported "
            f"{DI_PAYLOAD_SCHEMA_VERSION}, event {event_id})"
        )

    try:
        normalized = validate_di_payload(event_type, raw)
    except (TypeError, ValueError) as exc:
        raise DIEvidenceIntegrityError(
            f"committed {event_type} payload failed P1A validation "
            f"(event {event_id}): {exc}"
        ) from exc
    if not isinstance(normalized, dict):
        raise DIEvidenceIntegrityError(
            f"P1A validation of {event_type} did not yield a canonical object "
            f"(event {event_id})"
        )
    return normalized


def _hydrate_record(
    *,
    event_id: str,
    event_type: str,
    record_type: type,
    payload: Mapping[str, Any],
) -> Any:
    """Build the P1A contract from a validated canonical payload.

    ``validate_di_payload`` returns the canonical JSON-safe payload, so the
    typed contract is rebuilt from it and then re-canonicalized. If the rebuilt
    record does not round-trip to the committed canonical payload, the read
    fails closed rather than exposing a subtly reinterpreted record.
    """
    expected = {field.name for field in fields(record_type)}
    if set(payload) != expected:
        raise DIEvidenceIntegrityError(
            f"committed {event_type} payload fields do not match "
            f"{record_type.__name__} (event {event_id})"
        )
    try:
        record = record_type(
            **{
                name: _hydrate_value(name, payload[name])
                for name in expected
            }
        )
    except (TypeError, ValueError, KeyError, AttributeError) as exc:
        raise DIEvidenceIntegrityError(
            f"committed {event_type} payload could not be hydrated into "
            f"{record_type.__name__} (event {event_id}): {exc}"
        ) from exc

    reconstructed = (
        record.as_dict()
        if isinstance(record, DecisionContext)
        else asdict(record)
    )
    if canonicalize_nested(reconstructed) != payload:
        raise DIEvidenceIntegrityError(
            f"hydrated {record_type.__name__} does not round-trip to the "
            f"committed canonical payload (event {event_id})"
        )
    return record


def _hydrate_value(field_name: str, value: Any) -> Any:
    if field_name == "provenance":
        return _hydrate_provenance(value)
    if field_name in _WATERMARK_FIELDS:
        return _hydrate_watermark(field_name, value)
    if field_name in _ENUM_FIELD_TYPES:
        return _hydrate_enum(field_name, value)
    if field_name in _TIMESTAMP_FIELDS:
        return _parse_utc_timestamp(field_name, value)
    if isinstance(value, list):
        return tuple(value)
    return value


def _hydrate_enum(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    enum_type = _ENUM_FIELD_TYPES[field_name]
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        raise DIEvidenceIntegrityError(
            f"{field_name} is not a valid {enum_type.__name__}"
        ) from exc


def _hydrate_watermark(field_name: str, value: Any) -> Any:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DIEvidenceIntegrityError(f"{field_name} must be a watermark object")
    try:
        return ConsumedInputWatermark.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise DIEvidenceIntegrityError(f"{field_name} is invalid") from exc


def _hydrate_provenance(value: Any) -> Provenance:
    if not isinstance(value, Mapping):
        raise DIEvidenceIntegrityError("provenance must be an object")
    expected = {field.name for field in fields(Provenance)}
    if set(value) != expected:
        raise DIEvidenceIntegrityError(
            "provenance fields are incomplete or unknown"
        )
    data = dict(value)
    data["emitted_at"] = _parse_utc_timestamp(
        "provenance.emitted_at", data["emitted_at"]
    )
    refs = data["source_record_refs"]
    if not isinstance(refs, (list, tuple)):
        raise DIEvidenceIntegrityError(
            "provenance.source_record_refs must be an array"
        )
    data["source_record_refs"] = tuple(refs)
    try:
        return Provenance(**data)
    except (TypeError, ValueError) as exc:
        raise DIEvidenceIntegrityError(
            f"provenance is not interpretable: {exc}"
        ) from exc


def _parse_utc_timestamp(field_name: str, value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DIEvidenceIntegrityError(
                f"{field_name} is not a valid timestamp"
            ) from exc
    else:
        raise DIEvidenceIntegrityError(f"{field_name} must be a timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DIEvidenceIntegrityError(
            f"{field_name} must be an aware UTC timestamp"
        )
    return parsed.astimezone(timezone.utc)


__all__ = [
    "ANOMALY_AMBIGUOUS_SUPERSESSION",
    "ANOMALY_UNKNOWN_EVENT_TYPE",
    "DI_ANOMALY_CODES",
    "DI_EVENT_TYPE_PREFIX",
    "DI_PAYLOAD_SCHEMA_VERSION",
    "DIEvidenceAnomaly",
    "DIEvidenceEvent",
    "DIEvidenceIntegrityError",
    "DIEvidenceReaderError",
    "DIEvidenceSnapshot",
    "DIEvidenceSourceUnavailableError",
    "DIIncompatibleSchemaError",
    "DIInconsistentBoundaryError",
    "DILifecycleReconstructionError",
    "InvocationCostEvidence",
    "RequestLifecycle",
    "SupersessionEdge",
    "UnknownDIEvidenceEvent",
    "read_di_evidence_snapshot",
]
