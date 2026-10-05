"""Bounded, PROSPECTIVE SHADOW feasibility-evidence capture (R4-B2 Slice 3A).

This is the scheduled producer that closes the F5 evidence chain on the proven
60-second grid:

    committed FeatureSnapshot (60s, NEW since activation)
        -> genuine contemporaneous feasibility evidence assembly
        -> feasibility.evidence.recorded (canonical writer)

Owner decisions (Slice 3A): ONE feasibility-evidence record per instrument per
committed minute, keyed to the F3 IGNITION detector evaluation; the shadow
validation notional is an explicitly configured value, never derived from live
account equity.

PROSPECTIVE, never retrospective
--------------------------------
Evidence is genuine market/execution evidence for a snapshot ONLY when the
acquisition happens within a frozen, tightly bounded contemporaneous window that
can truthfully belong to that snapshot's evaluation epoch. The producer never
backfills historical FeatureSnapshots with current market/book/trade data:

* COLD START -- a missing cursor does NOT mean "start from the beginning of
  canonical history." The cursor is initialized at the current committed head and
  only NEW records are collected after activation; the cold-start action is
  deterministic and persisted.
* STALENESS -- before any live market read, the snapshot's evaluation/availability
  boundary is compared with the real acquisition instant. A snapshot outside the
  frozen contemporaneous window is never fetched against; it is recorded with an
  explicit stale disposition and advanced by the terminal-cursor rule.
* HONEST TIMESTAMPS -- ``evaluation_time`` is the snapshot's own evaluation epoch
  and ``source_cutoff`` truthfully describes the source evidence cutoff; current
  receipt/visibility is never backdated. If the source cutoff would fall after the
  evaluation epoch the builder fails closed rather than stamping newer data onto an
  older epoch.

Evidence is assembled from the proven primitives the live scanner uses
(``validate_market_data`` and ``evaluate_execution``). Negative evidence is
PRESERVED: a present ``REJECT`` market record and a present ``INVALID`` execution
record remain present typed evidence (they lead F5 to its existing hard VETO).
``missingness`` is reserved for genuinely absent/unavailable evidence, never for
unfavourable evidence.

This job runs as its OWN bounded process on the repository's ONE scheduler (cron),
never inside the protected unified cycle, and holds its OWN process-level lock
(distinct identity from the Feature Bus capture, so neither can suppress the
other). It is authorized only when the Feature Bus AND the canonical writer are
both in ``shadow``. It grants no trading, ranking, admission, allocation, order or
exchange authority.

SINGLE CANONICAL WRITER
-----------------------
``opip-canonical-writer`` is the SOLE writable owner of the canonical store: it
holds ``CanonicalStoreLock`` for its process lifetime, and acquisition fails
closed rather than waiting. This producer is therefore strictly a store CONSUMER.
It submits ``feasibility.evidence.recorded`` through ``CanonicalWriterClient``
(see ``resolve_canonical_submitter``) and never opens a writable canonical store
handle, so a second writer can never contend for or corrupt canonical ownership.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Sequence

from app.jobs.capture_feature_bus_shadow import emit_capture_marker
from app.opip.canonical.bridge import resolve_writer_mode
from app.opip.contracts.feasibility_evidence import (
    EVIDENCE_AVAILABLE,
    FeasibilityEvidence,
)
from app.opip.fev_evidence_event import (
    build_feasibility_evidence_recorded_payload,
    feasibility_evidence_event_correlation_id,
    feasibility_evidence_event_idempotency_key,
    feasibility_evidence_event_time,
)
from app.opip.features.committed_snapshot_reader import CommittedSnapshotReader
from app.opip.features.publisher import resolve_feature_bus_mode

# Reuse the live scanner's Bitnomial margin authority (single authority, no
# re-implementation): the execution-venue token and the eligibility/leverage
# resolution live in app.scanner.margin_eligibility and are consumed here.
from app.scanner.margin_eligibility import (
    BITNOMIAL_EXECUTION_VENUE,
    validate_short_margin_eligibility,
)
#: Bounded failure classification token for a durable per-record disposition (the
#: SAME classifier the Feature Bus producer uses, so a stalled public read is
#: reported identically by both producers).
from app.services.opip_feature_bus_market_source import classify_capture_error

#: Default bound: how many committed snapshots one batch may convert.
DEFAULT_CAPTURE_LIMIT = 8
MAX_CAPTURE_LIMIT = 32

#: Default total wall-clock budget for one pass (well inside the minute slot).
DEFAULT_BUDGET_SECONDS = 45.0
MAX_BUDGET_SECONDS = 50.0

#: Conservative per-batch reservation used to gate reading the next batch.
PER_BATCH_BUDGET_SECONDS = 5.0

#: Conservative per-snapshot reservation for one complete bounded evidence
#: acquisition (public OHLC + ticker + book + trades). Proved before any live
#: market read so normal control flow -- not the process timeout -- stops the pass.
PER_RECORD_BUDGET_SECONDS = 10.0

#: Per-REQUEST bound for this producer's pass-scoped Kraken client. One record
#: makes several bounded public reads (analytical OHLC, fresh freshness-anchor
#: OHLC, ticker, book; a SHORT record adds margin discovery), so the per-record
#: reservation above is the "may another record start?" gate while the
#: pass-scoped client's absolute deadline is the hard bound.
PER_REQUEST_BUDGET_SECONDS = 10.0

DEFAULT_DIRECTION = "LONG"
SUPPORTED_DIRECTIONS = frozenset({"LONG", "SHORT"})

#: Frozen maximum contemporaneous age. A snapshot whose evaluation/availability
#: boundary is older than this (relative to the real acquisition instant) is a
#: backlog record: the producer will NOT fetch current market data against it.
#: Two evaluation intervals (2 x 60s) tolerates one minute of scheduler jitter
#: while still refusing to backfill genuinely historical snapshots.
MAX_CONTEMPORANEOUS_AGE_SECONDS = 120.0

#: The ANALYTICAL horizon the feasibility checks consume: 60-minute Kraken OHLC
#: candles. Preserved EXACTLY (``market_data_validation`` thresholds, continuity
#: windows and spike detection are defined for this interval), never replaced by
#: one-minute bars: converting the model to 1m would change analytical semantics.
ANALYTICAL_INTERVAL_MINUTES = 60
ANALYTICAL_INTERVAL_SECONDS = 3600

#: The FRESHNESS ANCHOR interval: a closed one-minute Kraken OHLC candle for the
#: SAME instrument, read during the same evidence acquisition. The runtime
#: freshness contract (``release_runtime_verifier.MAX_FEV_SOURCE_AGE``, 120s)
#: cannot be met by an hourly close except in the ~two minutes after an hour
#: boundary, so the canonical ``source_cutoff`` is anchored to this genuinely
#: fresh observation while the hourly series remains the analytical input.
FRESHNESS_ANCHOR_INTERVAL_MINUTES = 1
FRESHNESS_ANCHOR_INTERVAL_SECONDS = 60

#: The freshness the anchor must have AT ACQUISITION. This mirrors
#: ``release_runtime_verifier.MAX_FEV_SOURCE_AGE`` exactly and is deliberately
#: NOT relaxed: the producer must MEET the verifier contract, so a stale or
#: missing anchor fails closed instead of being stamped fresh.
MAX_FRESH_ANCHOR_AGE_SECONDS = 120.0

#: Provenance tokens carried in ``source_evidence_refs`` (an existing frozen
#: canonical text-list field), so BOTH planes are explicit, durable and part of
#: the evidence identity with NO schema change: the analytical horizon and its
#: latest hourly cutoff, and the freshness anchor with its acquisition instant
#: and calculated source age.
ANALYTICAL_PROVENANCE_PREFIX = "analytical:kraken_public_ohlc"
FRESHNESS_PROVENANCE_PREFIX = "freshness:kraken_public_ohlc"
FRESHNESS_AGE_PROVENANCE_PREFIX = "freshness:anchor_provenance"

#: Point-in-time audit provenance: one token per supporting input naming its KIND
#: and its own event cutoff beside the record's evaluation epoch, so a reader can
#: confirm that no input observed AFTER the epoch supports an epoch-anchored record.
#: Carried in the existing frozen ``source_evidence_refs`` text-list: no schema
#: change and no new store, exactly like the two provenance planes above.
PIT_PROVENANCE_PREFIX = "pit:kraken_public_ohlc"

#: Durable reason recorded when a market plane that would require a LIVE read after
#: the evaluation epoch cannot be truthfully reconstructed as-of that epoch. The
#: plane is then recorded as explicitly UNAVAILABLE (present, never fabricated)
#: instead of being populated with post-epoch prices, depth or trades.
PIT_UNAVAILABLE_REASON = (
    "point-in-time integrity: a live market read after the evaluation epoch "
    "cannot be reconstructed as-of that epoch, so this plane is explicitly "
    "unavailable rather than stamped with the epoch cutoff"
)


#: The feasibility producer's OWN process-lock identity. Deliberately distinct
#: from the Feature Bus capture lock so neither producer can suppress the other.
FEASIBILITY_CAPTURE_LOCK_ENV = "OPIP_FEASIBILITY_CAPTURE_LOCK"
FEASIBILITY_CAPTURE_LOCK_PATH = "/tmp/opip-feasibility-evidence-capture.lock"

#: Read-position checkpoint. A non-authoritative resume marker (a read position,
#: not a durable dedupe or evidence store): the producer surfaces each batch's
#: cursor and persists it so successive passes advance through canonical history.
#: It lives beside the canonical store, never in a second durable store.
DEFAULT_CURSOR_FILENAME = "fev_capture_cursor.json"


class FeasibilityCaptureError(RuntimeError):
    """A feasibility-capture defect. The producer fails closed."""


class FeasibilityEvidenceStaleError(FeasibilityCaptureError):
    """The source evidence is not contemporaneous with the snapshot's evaluation epoch."""


class FeasibilityAnchorPendingError(FeasibilityCaptureError):
    """The epoch's own closed one-minute anchor candle is not published YET.

    Deliberately NOT a :class:`FeasibilityEvidenceStaleError`: a candle the venue
    has not published yet can appear on the next poll, so the snapshot must stay at
    the cursor and be RETRIED rather than skipped. It becomes terminal only when
    waiting can no longer help -- the source has definitively advanced past the
    required epoch, or the 120-second source-age contract has already expired (see
    :func:`resolve_source_evidence_anchor`).
    """


class FeasibilityPointInTimeError(FeasibilityCaptureError):
    """A supporting market input was observed AFTER the record's evaluation epoch.

    Point-in-time integrity: a record whose ``evaluation_time`` is the snapshot
    cutoff may only be supported by market inputs whose own event cutoff is at or
    before that epoch. Raising this means the record is NOT published rather than
    being stamped with an older candle cutoff over newer live observations.
    """


#: Kinds of input admitted to an F5 record's point-in-time audit.
PIT_KIND_MARKET = "market"
PIT_KIND_VENUE_METADATA = "venue_metadata"


@dataclass(frozen=True)
class PointInTimeInput:
    """One supporting input's EXPLICIT event/cutoff semantics.

    ``event_cutoff`` is the instant the observation itself is about (a closed
    candle's close, the venue's trade time, the instant a live read was taken).
    ``pit_valid`` is ``event_cutoff <= <record evaluation_time>`` for a market
    input, or ``epoch_invariant`` venue metadata (a venue capability lookup whose
    truth does not depend on the epoch) -- never a fabricated timestamp.
    """

    name: str
    kind: str
    event_cutoff: datetime
    epoch_invariant: bool = False

    def pit_valid(self, evaluation_time: datetime) -> bool:
        if self.kind == PIT_KIND_VENUE_METADATA and self.epoch_invariant:
            return True
        return self.event_cutoff <= evaluation_time

    def provenance_ref(self, evaluation_time: datetime) -> str:
        return (
            f"{PIT_PROVENANCE_PREFIX}"
            f":input={self.name}"
            f":kind={self.kind}"
            f":event={_compact_z(self.event_cutoff)}"
            # ``epoch_invariant`` is PERSISTED, not inferred on read: venue
            # metadata carries a post-epoch acquisition cutoff that is only
            # PIT-valid BECAUSE it is epoch-invariant, so a reader that defaulted
            # the flag would recompute the durable ``pit_valid`` as False and the
            # audit would contradict itself.
            f":epoch_invariant={self.epoch_invariant}"
            f":evaluation_time={_compact_z(evaluation_time)}"
            f":pit_valid={self.pit_valid(evaluation_time)}"
        )


def point_in_time_inputs(refs: Sequence[str]) -> tuple[PointInTimeInput, ...]:
    """Read back the durable per-input point-in-time audit from provenance refs.

    Parses the tokens the producer wrote, so a reader can confirm WHICH inputs
    supported a record and what event cutoff each one declared instead of trusting
    a summary. Diagnostics only: it grants no authority.

    A ref carrying this audit's prefix that cannot be parsed is a DEFECT, not a
    silently dropped entry: the audit would otherwise understate which inputs
    supported a record, so it fails closed.
    """
    parsed: list[PointInTimeInput] = []
    for ref in refs:
        if not ref.startswith(f"{PIT_PROVENANCE_PREFIX}:"):
            continue
        fields: dict[str, str] = {}
        for token in ref.split(":")[2:]:
            if "=" in token:
                key, value = token.split("=", 1)
                fields[key] = value
        try:
            event = _parse_pit_instant(fields["event"])
        except (KeyError, ValueError) as exc:  # pragma: no cover - defensive
            raise FeasibilityPointInTimeError(
                f"unreadable point-in-time provenance ref: {ref}"
            ) from exc
        parsed.append(
            PointInTimeInput(
                name=fields.get("input", "UNKNOWN"),
                kind=fields.get("kind", PIT_KIND_MARKET),
                event_cutoff=event,
                # Deterministic round-trip of the persisted flag. An ABSENT or
                # malformed token reads as non-invariant, which is the stricter
                # (fail-closed) reading: it can only make a post-epoch input
                # invalid, never rescue one.
                epoch_invariant=fields.get("epoch_invariant", "False") == "True",
            )
        )
    return tuple(parsed)


def assert_point_in_time_support(
    inputs: Sequence[PointInTimeInput], *, evaluation_time: datetime
) -> None:
    """Fail closed when ANY supporting input postdates the evaluation epoch.

    This is the enforcement behind the point-in-time contract: a record keyed to
    ``evaluation_time`` may never be supported by an input observed after it, so an
    input that cannot be truthfully reconstructed as-of the epoch refuses the
    record instead of being stamped with an older candle cutoff.
    """
    violations = [
        entry for entry in inputs if not entry.pit_valid(evaluation_time)
    ]
    if violations:
        described = ", ".join(
            f"{entry.name}@{_iso_z(entry.event_cutoff)}" for entry in violations
        )
        raise FeasibilityPointInTimeError(
            f"supporting input(s) observed after the evaluation epoch "
            f"{_iso_z(evaluation_time)}: {described}"
        )


def _iso_z(moment: datetime) -> str:
    """Canonical UTC second-resolution rendering used inside provenance tokens."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _compact_z(moment: datetime) -> str:
    """COLON-FREE UTC rendering for colon-delimited ``key=value`` provenance tokens.

    The point-in-time audit ref is a ``:``-delimited ``key=value`` list, so a full
    ISO timestamp -- which itself contains ``:`` -- is split apart by any reader
    that tokenizes the ref and the audit becomes unreadable. This keeps the
    timestamp lossless AND machine-readable in that form.
    """
    return moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_pit_instant(value: str) -> datetime:
    """Parse the compact colon-free UTC form written by :func:`_compact_z`."""
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _closed_candles(candles: Sequence[Any]) -> list[Any]:
    """Candles EXCLUDING the still-forming last row Kraken returns.

    Kraken's OHLC response includes the currently forming interval as its final
    row; the completed series is everything before it.
    """
    rows = list(candles)
    return rows[:-1] if len(rows) > 1 else rows


@dataclass(frozen=True)
class SourceEvidenceAnchor:
    """The two explicit provenance planes behind one evidence ``source_cutoff``.

    ``analytical_*`` describes the 60-minute series the feasibility calculations
    actually consume. ``anchor_*``/``source_cutoff``/``source_age_seconds``
    describe the genuinely fresh closed one-minute observation that the runtime
    freshness contract is evaluated against. Both are carried into
    ``source_evidence_refs`` so a freshness anchor can never be mistaken for
    fabricated freshness: the hourly cutoff it coexists with is recorded beside it.
    """

    analytical_interval_seconds: int
    analytical_bar_count: int
    analytical_latest_cutoff: datetime
    anchor_interval_seconds: int
    anchor_open: datetime
    anchor_close: datetime
    #: The anchor candle's OWN close price: the epoch's last observed price. Its
    #: event cutoff is exactly the evaluation epoch, so it is the point-in-time
    #: price a record for that epoch may be supported by (a live ticker read taken
    #: later must never be substituted for it).
    anchor_close_price: float
    acquisition_instant: datetime
    source_age_seconds: float

    @property
    def source_cutoff(self) -> datetime:
        """The freshest market datum that actually supports the determination."""
        return self.anchor_close

    def provenance_refs(self, *, snapshot_id: str) -> tuple[str, ...]:
        return (
            snapshot_id,
            f"{ANALYTICAL_PROVENANCE_PREFIX}"
            f":interval_seconds={self.analytical_interval_seconds}"
            f":bars={self.analytical_bar_count}"
            f":latest_close={_iso_z(self.analytical_latest_cutoff)}",
            f"{FRESHNESS_PROVENANCE_PREFIX}"
            f":interval_seconds={self.anchor_interval_seconds}"
            f":bar_open={_iso_z(self.anchor_open)}"
            f":bar_close={_iso_z(self.anchor_close)}",
            f"{FRESHNESS_AGE_PROVENANCE_PREFIX}"
            f":source={FRESHNESS_PROVENANCE_PREFIX}"
            f":acquisition={_iso_z(self.acquisition_instant)}"
            f":source_age_seconds={self.source_age_seconds:.3f}"
            f":max_source_age_seconds={MAX_FRESH_ANCHOR_AGE_SECONDS:.1f}",
        )


def resolve_source_evidence_anchor(
    *,
    snapshot_id: str,
    candles: Sequence[Any],
    candles_1m: Sequence[Any],
    evaluation_time: datetime,
    acquisition_instant: datetime,
    interval_minutes: int = ANALYTICAL_INTERVAL_MINUTES,
    interval_seconds: int = ANALYTICAL_INTERVAL_SECONDS,
) -> SourceEvidenceAnchor:
    """Resolve the analytical horizon AND the fresh one-minute freshness anchor.

    The analytical series keeps its EXISTING semantics (latest completed candle of
    ``interval_minutes``) and its existing fail-closed rule: a source cutoff after
    the evaluation epoch means the market already moved past this epoch, so the
    evidence would be retrospective and the builder must refuse rather than
    backdate.

    The freshness anchor is a separately acquired closed ONE-MINUTE candle for the
    same instrument, and it must be the candle that closes exactly at the
    snapshot's own evaluation epoch. That is the freshest datum which (a) genuinely
    supports this epoch's determination and (b) satisfies
    ``source_cutoff <= evaluation_time`` in the runtime verifier. An anchor older
    than :data:`MAX_FRESH_ANCHOR_AGE_SECONDS` at acquisition fails closed: no
    synthetic timestamp is ever manufactured.

    A MISSING epoch candle is classified from the source's own semantics, never
    assumed terminal:

    * ``ANCHOR_PENDING`` -- the source has not yet published it (its newest row,
      including the still-forming one, starts at or before the epoch minute) and
      the 120s source-age contract has not expired: RETRYABLE
      (:class:`FeasibilityAnchorPendingError`), so the snapshot stays at the cursor
      and is retried instead of being silently skipped;
    * terminal stale -- the source has definitively advanced past the epoch minute
      (its newest row starts at or after ``epoch + 60s``) and the candle is still
      absent, or the epoch is already older than the 120s contract so no candle
      published later could ever be fresh enough.
    """
    completed = _closed_candles(candles)
    if not completed:
        raise FeasibilityCaptureError("no source candles returned")
    latest_close = int(completed[-1].timestamp) + int(interval_seconds)
    analytical_latest_cutoff = datetime.fromtimestamp(latest_close, tz=timezone.utc)
    # Existing fail-closed rule, checked FIRST so its disposition is unchanged.
    if analytical_latest_cutoff > evaluation_time:
        raise FeasibilityEvidenceStaleError(
            f"source cutoff {analytical_latest_cutoff.isoformat()} is after the "
            f"evaluation epoch {evaluation_time.isoformat()}"
        )

    completed_1m = _closed_candles(candles_1m)
    if not completed_1m:
        # A transient/empty read: retryable, never a manufactured anchor.
        raise FeasibilityCaptureError(
            f"{snapshot_id}: no freshness-anchor candles returned"
        )
    epoch_seconds = int(evaluation_time.timestamp())
    anchor = None
    for candle in completed_1m:
        if int(candle.timestamp) + FRESHNESS_ANCHOR_INTERVAL_SECONDS == epoch_seconds:
            anchor = candle
    if anchor is None:
        # The epoch's own minute is not in the returned window. Kraken publishes a
        # closed interval with a lag and its response includes the still-forming
        # interval LAST, so the newest returned row's start tells which side of the
        # epoch the SOURCE has reached -- a missing candle is not automatically
        # terminal, and the snapshot may simply have been read too early.
        newest_row_start = max(int(row.timestamp) for row in candles_1m)
        epoch_age_seconds = (acquisition_instant - evaluation_time).total_seconds()
        still_publishable = epoch_age_seconds <= MAX_FRESH_ANCHOR_AGE_SECONDS
        source_past_epoch = newest_row_start >= epoch_seconds + FRESHNESS_ANCHOR_INTERVAL_SECONDS
        if still_publishable and not source_past_epoch:
            # RETRYABLE: the candle can still appear (and still be within the 120s
            # source-age contract). The caller must NOT advance its cursor.
            raise FeasibilityAnchorPendingError(
                f"{snapshot_id}: the closed {FRESHNESS_ANCHOR_INTERVAL_MINUTES}m "
                f"candle for the evaluation epoch {_iso_z(evaluation_time)} is not "
                f"published yet ({epoch_age_seconds:.1f}s after the epoch, source "
                f"newest row {_iso_z(datetime.fromtimestamp(newest_row_start, tz=timezone.utc))}); "
                "retryable within the freshness window"
            )
        raise FeasibilityEvidenceStaleError(
            f"{snapshot_id}: no closed {FRESHNESS_ANCHOR_INTERVAL_MINUTES}m candle "
            f"closes at the evaluation epoch {_iso_z(evaluation_time)}; a fresh "
            "source anchor cannot be constructed for this epoch"
        )
    if not (
        isinstance(anchor.close, (int, float))
        and math.isfinite(float(anchor.close))
        and float(anchor.close) > 0
        and float(anchor.high) >= float(anchor.low)
    ):
        raise FeasibilityCaptureError(
            f"{snapshot_id}: freshness-anchor candle is not a usable observation"
        )

    anchor_close = datetime.fromtimestamp(
        int(anchor.timestamp) + FRESHNESS_ANCHOR_INTERVAL_SECONDS, tz=timezone.utc
    )
    source_age_seconds = (acquisition_instant - anchor_close).total_seconds()
    if source_age_seconds < 0.0:
        raise FeasibilityEvidenceStaleError(
            f"{snapshot_id}: freshness anchor {_iso_z(anchor_close)} is after the "
            f"acquisition instant {_iso_z(acquisition_instant)}"
        )
    if source_age_seconds > MAX_FRESH_ANCHOR_AGE_SECONDS:
        raise FeasibilityEvidenceStaleError(
            f"{snapshot_id}: freshness anchor {_iso_z(anchor_close)} is "
            f"{source_age_seconds:.1f}s old at acquisition, beyond the "
            f"{MAX_FRESH_ANCHOR_AGE_SECONDS:.0f}s source-age contract"
        )

    return SourceEvidenceAnchor(
        analytical_interval_seconds=int(interval_seconds),
        analytical_bar_count=len(completed),
        analytical_latest_cutoff=analytical_latest_cutoff,
        anchor_interval_seconds=FRESHNESS_ANCHOR_INTERVAL_SECONDS,
        anchor_open=datetime.fromtimestamp(int(anchor.timestamp), tz=timezone.utc),
        anchor_close=anchor_close,
        anchor_close_price=float(anchor.close),
        acquisition_instant=acquisition_instant,
        source_age_seconds=source_age_seconds,
    )


def anchor_source_age_seconds(refs: Sequence[str]) -> float | None:
    """Read back the recorded freshness anchor age from provenance refs.

    Diagnostics/tests only: it parses the durable token the producer wrote, so a
    reader can confirm the anchor actually satisfied the source-age contract
    instead of trusting a summary.
    """
    for ref in refs:
        if ref.startswith(f"{FRESHNESS_AGE_PROVENANCE_PREFIX}:"):
            for field in ref.split(":"):
                if field.startswith("source_age_seconds="):
                    try:
                        return float(field.split("=", 1)[1])
                    except ValueError:  # pragma: no cover - defensive
                        return None
    return None


def resolve_capture_budget_seconds(settings: Any, override: float | None = None) -> float:
    """Resolve the clamped pass budget. One authority for the pass AND its client."""
    resolved = (
        float(override)
        if override is not None
        else float(
            getattr(
                settings,
                "opip_feasibility_capture_budget_seconds",
                DEFAULT_BUDGET_SECONDS,
            )
        )
    )
    return max(PER_BATCH_BUDGET_SECONDS, min(resolved, MAX_BUDGET_SECONDS))



def _load_cursor(path: str | os.PathLike[str]) -> tuple[int, int] | None:
    """Load the persisted read cursor, or ``None`` when there is none (cold start)."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return (int(raw["history_epoch"]), int(raw["local_sequence"]))
    except FileNotFoundError:
        return None
    except (KeyError, ValueError, TypeError, OSError):
        # A corrupt cursor must not be trusted; a cold start re-reads from the
        # current head and the cursor is rewritten from a clean batch boundary.
        return None


def _save_cursor(path: str | os.PathLike[str], cursor: tuple[int, int]) -> None:
    """Atomically persist the read cursor after a completed terminal batch."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(
        json.dumps(
            {"history_epoch": int(cursor[0]), "local_sequence": int(cursor[1])}
        ),
        encoding="utf-8",
    )
    os.replace(tmp, target)


def _default_cursor_path() -> Path:
    from app.opip.canonical.paths import canonical_dir

    return canonical_dir() / DEFAULT_CURSOR_FILENAME


@dataclass
class FeasibilityCaptureSummary:
    """Machine-readable outcome of one bounded feasibility-capture pass."""

    mode: str
    enabled: bool
    inert: bool
    reason: str | None = None
    cold_start: bool = False
    snapshots_seen: int = 0
    records_built: int = 0
    recorded: int = 0
    duplicate: int = 0
    stale: int = 0
    rejected: int = 0
    rejected_by_reader: int = 0
    unavailable: int = 0
    retryable: int = 0
    budget_exhausted: bool = False
    elapsed_seconds: float = 0.0
    cursor: tuple[int, int] | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "enabled": self.enabled,
            "inert": self.inert,
            "reason": self.reason,
            "cold_start": self.cold_start,
            "snapshots_seen": self.snapshots_seen,
            "records_built": self.records_built,
            "recorded": self.recorded,
            "duplicate": self.duplicate,
            "stale": self.stale,
            "rejected": self.rejected,
            "rejected_by_reader": self.rejected_by_reader,
            "unavailable": self.unavailable,
            "retryable": self.retryable,
            "budget_exhausted": self.budget_exhausted,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "cursor": None if self.cursor is None else [self.cursor[0], self.cursor[1]],
            "errors": list(self.errors[:8]),
        }


def feasibility_capture_authorized(settings: Any) -> bool:
    """Exact SHADOW authorization: Feature Bus shadow AND canonical writer shadow."""
    return (
        resolve_feature_bus_mode(settings) == "shadow"
        and resolve_writer_mode(settings) == "shadow"
    )


def resolve_capture_notional(
    settings: Any, *, override: float | None = None
) -> tuple[float | None, str | None]:
    """Resolve the F5 validation notional. The configured value is the sole authority.

    The notional is a repo-controlled evidence constant (the release profile sets
    it; `SAFE_BASELINE` sets it to zero, which disables capture). A free-form
    `--notional-usd` override that differs from the configured value is refused,
    so an arbitrary notional can never influence an evidence epoch. Returns
    ``(notional, None)`` on success or ``(None, reason)`` when refused.
    """
    configured_raw = getattr(settings, "opip_feasibility_capture_notional_usd", 0.0) or 0.0
    try:
        configured = float(configured_raw)
    except (TypeError, ValueError):
        return (None, "configured notional is not a number")
    if override is not None:
        try:
            override_value = float(override)
        except (TypeError, ValueError):
            return (None, "notional override is not a number")
        if not math.isfinite(override_value):
            return (None, "notional override is not finite")
        if override_value != configured:
            return (None, "arbitrary notional override rejected")
    if not math.isfinite(configured) or configured <= 0:
        return (None, "notional not configured")
    return (configured, None)


def _inert_summary(mode: str, reason: str) -> FeasibilityCaptureSummary:
    return FeasibilityCaptureSummary(mode=mode, enabled=False, inert=True, reason=reason)


def _direction_for(snapshot: Any) -> str:
    """The F3 IGNITION route is long-biased: the default direction is LONG.

    Supplied as an injectable seam so the R4-B2 SHORT route can drive the same
    producer with genuine BTNL evidence rather than re-deriving direction here.
    """
    return DEFAULT_DIRECTION


def _snapshot_boundary(snapshot: Any) -> datetime:
    """The instant a snapshot became observable, for the contemporaneity check.

    Prefers the source availability visibility, then the evaluation stamp, then the
    evaluation cutoff, so the producer never assumes a snapshot was available at its
    cutoff when the availability record says otherwise.
    """
    availability = getattr(snapshot, "availability", None)
    for candidate in (
        getattr(availability, "visible_at_utc", None),
        getattr(snapshot, "evaluated_at_utc", None),
        getattr(snapshot, "evaluation_cutoff", None),
    ):
        if isinstance(candidate, datetime):
            return candidate if candidate.tzinfo else candidate.replace(tzinfo=timezone.utc)
    raise FeasibilityCaptureError("snapshot carries no usable evaluation boundary")


def capture_feasibility_evidence_shadow(
    *,
    settings: Any = None,
    limit: int | None = None,
    budget_seconds: float | None = None,
    max_age_seconds: float | None = None,
    now: datetime | None = None,
    reader: CommittedSnapshotReader | None = None,
    evidence_builder: Callable[[Any, str], FeasibilityEvidence] | None = None,
    direction_for: Callable[[Any], str] | None = None,
    submit_payload: Callable[[dict], str] | None = None,
    cursor_path: str | os.PathLike[str] | None = None,
    clock: Callable[[], float] | None = None,
) -> FeasibilityCaptureSummary:
    """Run one bounded PROSPECTIVE SHADOW feasibility-capture pass.

    Writes nothing unless authorized. Reads one bounded batch after the persisted
    cursor, materializes every snapshot in that batch, then persists the advanced
    cursor ONLY across a contiguous prefix of snapshots that reached a TERMINAL
    disposition. A retryable snapshot halts cursor advancement (and the pass) so it
    is retried next pass rather than silently skipped.

    ``now`` is the honest acquisition instant. ``evidence_builder(snapshot,
    direction)`` returns genuine contemporaneous evidence or raises.
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    mode = resolve_feature_bus_mode(settings)
    if not feasibility_capture_authorized(settings):
        emit_capture_marker(
            "inert",
            prefix="OPIP_FEASIBILITY_CAPTURE",
            reason="FEASIBILITY_CAPTURE_NOT_AUTHORIZED_SHADOW",
        )
        return _inert_summary(mode, "FEASIBILITY_CAPTURE_NOT_AUTHORIZED_SHADOW")

    resolved_limit = (
        int(limit)
        if limit is not None
        else int(getattr(settings, "opip_feasibility_capture_limit", DEFAULT_CAPTURE_LIMIT))
    )
    resolved_budget = resolve_capture_budget_seconds(settings, budget_seconds)
    bounded = min(max(1, resolved_limit), MAX_CAPTURE_LIMIT)
    budget = resolved_budget
    resolved_max_age = (
        float(max_age_seconds)
        if max_age_seconds is not None
        else MAX_CONTEMPORANEOUS_AGE_SECONDS
    )
    tick = clock or monotonic
    started = tick()
    deadline = started + budget
    resolve_direction = direction_for or _direction_for
    acquisition_instant = now or datetime.now(timezone.utc)

    summary = FeasibilityCaptureSummary(mode=mode, enabled=True, inert=False)
    emit_capture_marker(
        "start",
        prefix="OPIP_FEASIBILITY_CAPTURE",
        limit=bounded,
        budget_seconds=budget,
        max_age_seconds=resolved_max_age,
    )

    owns_reader = reader is None
    if reader is None:
        from app.opip.canonical.paths import db_path

        reader = CommittedSnapshotReader(db_path=db_path())
    resolved_cursor_path: str | os.PathLike[str] | None = (
        cursor_path if cursor_path is not None else _default_cursor_path()
    )
    try:
        # Cold start: begin at the current committed head, never at the beginning
        # of history. Deterministic and persisted; collects nothing this pass.
        persisted = _load_cursor(resolved_cursor_path) if resolved_cursor_path else None
        if persisted is None:
            head = reader.head_cursor()
            if head is not None and resolved_cursor_path:
                _save_cursor(resolved_cursor_path, head)
                summary.cursor = head
            summary.cold_start = True
            summary.elapsed_seconds = tick() - started
            emit_capture_marker(
                "done",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                status="COLD_START",
                elapsed_seconds=round(summary.elapsed_seconds, 3),
            )
            return summary

        cursor: tuple[int, int] | None = persisted
        while True:
            if deadline - tick() < PER_BATCH_BUDGET_SECONDS:
                summary.budget_exhausted = True
                summary.errors.append("budget exhausted before reading the next batch")
                emit_capture_marker(
                    "budget_exhausted",
                    prefix="OPIP_FEASIBILITY_CAPTURE",
                    where="batch",
                    reason="INSUFFICIENT_BATCH_BUDGET",
                    required_seconds=PER_BATCH_BUDGET_SECONDS,
                )
                break
            records, tail = reader.read_records(after=cursor, limit=bounded)
            if not records:
                break
            if tail is None or tail == cursor:
                # No forward progress: stop rather than loop.
                break
            cursor_advanced = False
            for record in records:
                if record.rejected:
                    # A malformed/corrupt committed row is deterministic (it can
                    # never become valid on replay): terminal, counted, surfaced, and
                    # the cursor advances to THAT ROW's own canonical position.
                    summary.rejected_by_reader += 1
                    if len(summary.errors) < 8:
                        summary.errors.append(
                            f"{record.event_id}: reader rejected a committed "
                            f"record: {record.reject_reason}"
                        )
                    cursor = record.cursor
                    summary.cursor = cursor
                    if resolved_cursor_path:
                        _save_cursor(resolved_cursor_path, cursor)
                    cursor_advanced = True
                    continue

                summary.snapshots_seen += 1
                # Per-snapshot budget gate: prove enough budget for a complete
                # bounded evidence acquisition BEFORE any live market read, rather
                # than relying on the process timeout to interrupt normal flow.
                if deadline - tick() < PER_RECORD_BUDGET_SECONDS:
                    summary.budget_exhausted = True
                    summary.errors.append(
                        f"{record.event_id}: budget exhausted before evidence "
                        "acquisition"
                    )
                    emit_capture_marker(
                        "budget_exhausted",
                        prefix="OPIP_FEASIBILITY_CAPTURE",
                        where="record",
                        reason="INSUFFICIENT_RECORD_BUDGET",
                        required_seconds=PER_RECORD_BUDGET_SECONDS,
                    )
                    # Leave the cursor at the last terminal row so this snapshot is
                    # retried next pass. Do not advance past it.
                    cursor_advanced = False
                    break
                terminal = _process_snapshot(
                    record.snapshot,
                    summary=summary,
                    acquisition_instant=acquisition_instant,
                    max_age_seconds=resolved_max_age,
                    resolve_direction=resolve_direction,
                    evidence_builder=evidence_builder,
                    submit_payload=submit_payload,
                )
                if not terminal:
                    # Retryable: halt immediately, leaving the durable cursor at the
                    # prior terminal row so this row (and later ones) are re-read.
                    cursor_advanced = False
                    break
                cursor = record.cursor
                summary.cursor = cursor
                if resolved_cursor_path:
                    _save_cursor(resolved_cursor_path, cursor)
                cursor_advanced = True
            if not cursor_advanced:
                break
    finally:
        if owns_reader:
            reader.close()

    summary.elapsed_seconds = tick() - started
    emit_capture_marker(
        "done",
        prefix="OPIP_FEASIBILITY_CAPTURE",
        status="OK",
        snapshots_seen=summary.snapshots_seen,
        recorded=summary.recorded,
        duplicate=summary.duplicate,
        stale=summary.stale,
        rejected=summary.rejected,
        retryable=summary.retryable,
        budget_exhausted=summary.budget_exhausted,
        elapsed_seconds=round(summary.elapsed_seconds, 3),
    )
    return summary


def _process_snapshot(
    snapshot: Any,
    *,
    summary: FeasibilityCaptureSummary,
    acquisition_instant: datetime,
    max_age_seconds: float,
    resolve_direction: Callable[[Any], str],
    evidence_builder: Callable[[Any, str], FeasibilityEvidence] | None,
    submit_payload: Callable[[dict], str] | None,
) -> bool:
    """Process one snapshot, returning ``True`` for a TERMINAL disposition.

    TERMINAL dispositions advance the cursor (canonical OK / DUPLICATE_OK,
    deterministic producer rejection, explicit stale/out-of-scope). RETRYABLE
    dispositions (transient source failure, unexpected assembly exception, canonical
    RETRYABLE / unexpected canonical status, no builder/submit configured) do NOT
    advance the cursor so the record is retried.
    """
    snapshot_id = str(getattr(snapshot, "snapshot_id", "UNKNOWN"))
    try:
        # 1. Contemporaneity gate: never fetch current market data against a
        # snapshot whose epoch is no longer contemporaneous.
        try:
            boundary = _snapshot_boundary(snapshot)
        except FeasibilityCaptureError as exc:
            # A snapshot with no usable boundary is a deterministic structural
            # rejection (it can never become usable on replay).
            summary.rejected += 1
            summary.errors.append(f"{snapshot_id}: {exc}")
            return True
        age = (acquisition_instant - boundary).total_seconds()
        if age > max_age_seconds:
            summary.stale += 1
            summary.errors.append(
                f"{snapshot_id}: stale snapshot (age {age:.1f}s > {max_age_seconds:.0f}s)"
            )
            emit_capture_marker(
                "stale",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason="SNAPSHOT_AGE",
                age_seconds=round(age, 3),
                max_age_seconds=round(max_age_seconds, 3),
            )
            return True
        if age < 0.0:
            # Not yet observable at the acquisition instant: fail closed without
            # dropping the record -- revisit next pass.
            summary.retryable += 1
            summary.errors.append(
                f"{snapshot_id}: snapshot not yet visible at acquisition instant"
            )
            emit_capture_marker(
                "retryable",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason="NOT_YET_VISIBLE",
            )
            return False

        # 2. Direction: the F3 route supplies it; unsupported tokens are deterministic.
        direction = resolve_direction(snapshot)
        if direction not in SUPPORTED_DIRECTIONS:
            summary.rejected += 1
            summary.errors.append(
                f"{snapshot_id}: unsupported direction {direction!r}"
            )
            return True

        if evidence_builder is None:
            # No genuine builder configured: cannot produce evidence. Retryable so
            # the record is not silently dropped while misconfigured.
            summary.unavailable += 1
            summary.errors.append(
                f"{snapshot_id}: no evidence builder configured"
            )
            return False
        if submit_payload is None:
            summary.unavailable += 1
            summary.errors.append(
                f"{snapshot_id}: no canonical submit configured"
            )
            return False

        # 3. Build genuine contemporaneous evidence.
        try:
            evidence = evidence_builder(snapshot, direction)
        except FeasibilityPointInTimeError as exc:
            # Deterministic, and NOT recoverable: a snapshot's evaluation epoch is
            # fixed, so an input observed after it can never become point-in-time
            # valid on replay. The record is REJECTED (terminal) instead of being
            # published with post-epoch observations.
            summary.rejected += 1
            summary.errors.append(f"{snapshot_id}: point-in-time integrity: {exc}")
            emit_capture_marker(
                "rejected",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason="POINT_IN_TIME_VIOLATION",
                error=type(exc).__name__,
            )
            return True
        except FeasibilityAnchorPendingError as exc:
            # NOT terminal: the venue has simply not published this epoch's closed
            # one-minute candle yet. Keep the cursor HERE so the same snapshot (and
            # every later one) is retried on the next pass.
            summary.retryable += 1
            summary.errors.append(f"{snapshot_id}: freshness anchor pending: {exc}")
            emit_capture_marker(
                "retryable",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason="FRESHNESS_ANCHOR_PENDING",
                error=type(exc).__name__,
            )
            return False
        except FeasibilityEvidenceStaleError as exc:
            summary.stale += 1
            summary.errors.append(f"{snapshot_id}: out-of-epoch evidence: {exc}")
            # Durable freshness disposition: this epoch's own source anchor can
            # never appear, so the release receipt must name it explicitly.
            emit_capture_marker(
                "stale",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason="FRESHNESS_ANCHOR",
                error=type(exc).__name__,
            )
            return True
        except Exception as exc:  # noqa: BLE001 - transient: may succeed on replay
            summary.retryable += 1
            summary.errors.append(
                f"{snapshot_id}: evidence assembly failed: {type(exc).__name__}: {exc}"
            )
            emit_capture_marker(
                "retryable",
                prefix="OPIP_FEASIBILITY_CAPTURE",
                snapshot=snapshot_id,
                reason=classify_capture_error(exc),
                error=type(exc).__name__,
            )
            return False
        if not isinstance(evidence, FeasibilityEvidence):
            summary.rejected += 1
            summary.errors.append(f"{snapshot_id}: builder returned non-evidence")
            return True
        if getattr(evidence, "direction", None) != direction:
            # Fail closed: never record evidence built for a different direction
            # (a SHORT request can never be satisfied by LONG/spot evidence).
            summary.rejected += 1
            summary.errors.append(
                f"{snapshot_id}: builder evidence direction "
                f"{getattr(evidence, 'direction', None)!r} != {direction!r}"
            )
            return True
        summary.records_built += 1

        # 4. Persist through the single canonical writer.
        payload = build_feasibility_evidence_recorded_payload(evidence)
        status = submit_payload(payload)
        if status == "OK":
            summary.recorded += 1
            return True
        if status == "DUPLICATE_OK":
            summary.duplicate += 1
            return True
        if status in {"REJECTED", "RETRYABLE"}:
            # A writer rejection is treated as RETRYABLE (safe: never silently skip
            # evidence the writer refused; a persistent rejection surfaces loudly).
            summary.retryable += 1
            summary.errors.append(f"{snapshot_id}: canonical {status}")
            return False
        summary.retryable += 1
        summary.errors.append(f"{snapshot_id}: canonical {status}")
        return False
    except Exception as exc:  # noqa: BLE001 - unexpected: retryable, never skipped
        summary.retryable += 1
        summary.errors.append(
            f"{snapshot_id}: unexpected capture failure: {type(exc).__name__}: {exc}"
        )
        return False


# ---------------------------------------------------------------------------
# Real LONG evidence builder (reuses the live scanner primitives)
# ---------------------------------------------------------------------------


def build_long_feasibility_evidence(
    snapshot: Any,
    *,
    client: Any,
    notional_usd: float,
    acquisition_instant: datetime,
    interval_minutes: int = 60,
    interval_seconds: int = 3600,
) -> FeasibilityEvidence:
    """Assemble genuine contemporaneous LONG feasibility evidence for one snapshot.

    Reuses ``validate_market_data`` and ``evaluate_execution`` -- the exact
    primitives the live scanner uses -- with an EXPLICIT acquisition instant so no
    hidden clock is read.

    TWO provenance planes (see :func:`resolve_source_evidence_anchor`):

    * the ANALYTICAL horizon is the 60-minute Kraken series, unchanged, and it
      still fails closed if its latest completed close would fall after the
      evaluation epoch (newer data must never be stamped onto an older epoch);
    * the FRESHNESS ANCHOR is a separately acquired closed one-minute candle for
      the same instrument, and ``source_cutoff`` is its close -- the freshest
      datum that actually supports this epoch. A missing/stale anchor fails closed
      rather than being fabricated or backdated.

    POINT-IN-TIME INTEGRITY (see :class:`PointInTimeInput`): every supporting
    market input must carry an explicit event cutoff at or before the record's
    evaluation epoch, and each one is recorded durably in ``source_evidence_refs``.
    The epoch's own last price is the CLOSED one-minute anchor candle's close (its
    event time is exactly the epoch); a live ticker read is never used as the
    record's price. The order book can only be read live, so it is admitted only
    when the read itself is at or before the epoch -- otherwise the execution plane
    is recorded as explicitly UNAVAILABLE rather than presenting post-epoch depth
    and liquidity as epoch liquidity. A post-epoch input refuses the record
    outright (:func:`assert_point_in_time_support`).

    A SHORT request is refused here (the SHORT route must supply genuine BTNL
    evidence through its own builder).
    """
    from app.scanner.execution_validation import evaluate_execution, unavailable_execution
    from app.scanner.market_data_validation import validate_market_data

    symbol = str(snapshot.venue_instrument_id)
    moment = acquisition_instant
    candles = list(client.get_ohlc(symbol, interval=interval_minutes))
    if not candles:
        raise FeasibilityCaptureError("no source candles returned")
    # Freshness anchor: a SEPARATE, fresh closed one-minute read of the SAME
    # instrument. The analytical series above keeps its 60-minute semantics; this
    # observation is what the runtime source-age contract is evaluated against.
    candles_1m = list(
        client.get_ohlc(symbol, interval=FRESHNESS_ANCHOR_INTERVAL_MINUTES)
    )

    evaluation_time = snapshot.evaluation_cutoff
    # Truthful source cutoff: the ANALYTICAL horizon (the 60-minute series above,
    # unchanged) PLUS the fresh one-minute freshness anchor. If the analytical
    # cutoff falls after the evaluation epoch the market has already moved past
    # this epoch and the evidence would be retrospective -- fail closed rather than
    # backdate (unchanged). ``source_cutoff`` is the freshest market datum that
    # genuinely supports this determination, and BOTH planes are recorded.
    anchor = resolve_source_evidence_anchor(
        snapshot_id=str(snapshot.snapshot_id),
        candles=candles,
        candles_1m=candles_1m,
        evaluation_time=evaluation_time,
        acquisition_instant=acquisition_instant,
        interval_minutes=interval_minutes,
        interval_seconds=interval_seconds,
    )

    # POINT-IN-TIME PRICE: the epoch's own last price is the closed one-minute
    # anchor candle's close. Its event cutoff is EXACTLY the evaluation epoch, so
    # it is the retained point-in-time datum this record's price checks may use.
    # ``get_ticker`` is a LIVE read: when it happens after the epoch its value
    # postdates the record's epoch and must not be substituted in its place.
    epoch_last = float(anchor.anchor_close_price)

    market = validate_market_data(
        candles, epoch_last, interval_minutes=interval_minutes, now=moment
    )
    market_inputs = [
        PointInTimeInput(
            name="analytical_60m_ohlc",
            kind=PIT_KIND_MARKET,
            event_cutoff=anchor.analytical_latest_cutoff,
        ),
        PointInTimeInput(
            name="freshness_1m_anchor",
            kind=PIT_KIND_MARKET,
            event_cutoff=anchor.anchor_close,
        ),
    ]
    # Liquidity can only be observed LIVE. When that observation happens after the
    # evaluation epoch it cannot be truthfully reconstructed as-of the epoch, so
    # the plane is recorded as explicitly unavailable instead of attaching
    # post-epoch depth/liquidity to an epoch-anchored record.
    live_read_is_point_in_time = acquisition_instant <= evaluation_time
    if live_read_is_point_in_time:
        book = client.get_pre_trade(symbol)
        try:
            trades = client.get_post_trade(symbol, count=100)
        except Exception:  # noqa: BLE001 - absent recent trades are still present
            trades = None
        execution = evaluate_execution(
            book=book,
            validation_notional_usd=float(notional_usd),
            ticker_last=epoch_last,
            quote_to_usd_rate=1.0,
            trades=trades,
            now=moment,
        )
        market_inputs.append(
            PointInTimeInput(
                name="spot_order_book",
                kind=PIT_KIND_MARKET,
                event_cutoff=acquisition_instant,
            )
        )
    else:
        execution = unavailable_execution(PIT_UNAVAILABLE_REASON)
    assert_point_in_time_support(market_inputs, evaluation_time=evaluation_time)

    return FeasibilityEvidence(
        instrument_version_id=snapshot.instrument_version_id,
        venue_instrument_id=symbol,
        direction=DEFAULT_DIRECTION,
        evaluation_time=evaluation_time,
        source_cutoff=anchor.source_cutoff,
        source_snapshot_id=snapshot.snapshot_id,
        source_evidence_refs=(
            *anchor.provenance_refs(snapshot_id=str(snapshot.snapshot_id)),
            *(
                entry.provenance_ref(evaluation_time)
                for entry in market_inputs
            ),
        ),
        market_data_validation=market,
        margin_validation_status=None,
        margin_eligible=None,
        margin_venue_symbol=None,
        margin_max_leverage=None,
        execution_validation=execution,
        # Both records are PRESENT typed evidence. Negative records (REJECT/INVALID)
        # are present, not missing: missingness is reserved for genuinely absent
        # evidence. F5 turns a present REJECT/INVALID into its existing hard VETO.
        availability=EVIDENCE_AVAILABLE,
        missingness=(),
        kraken_public_symbol=symbol,
        primary_pair=symbol,
    )


def discover_short_margin(
    snapshot: Any,
    *,
    client: Any,
    account_leverage_ceiling: float | None = None,
) -> dict[str, Any]:
    """Discover genuine BTNL margin evidence for one instrument.

    Delegates to the live scanner's ``validate_short_margin_eligibility`` -- the
    SAME authority the live SHORT route uses -- so there is exactly one Bitnomial
    venue/leverage policy and no drift. That function queries Kraken's Bitnomial
    execution-venue discovery (pair presence is tradability), resolves the
    ``:BTNL`` ``margin_venue_symbol`` F5 requires, and bounds the leverage tier by
    the account ceiling: an absent pair is INELIGIBLE and an unavailable discovery
    is UNAVAILABLE (never fabricated).
    """
    from app.scanner.models import MarketSnapshot

    primary = str(snapshot.venue_instrument_id)
    # A minimal candidate: only the fields margin discovery reads are meaningful.
    candidate = MarketSnapshot(
        symbol=primary,
        last_price=0.0,
        ema20=0.0,
        ema50=0.0,
        ema200=0.0,
        rsi=0.0,
        macd_line=0.0,
        macd_signal=0.0,
        macd_histogram=0.0,
        atr=0.0,
        atr_pct=0.0,
        volume_ratio=0.0,
        technical_score=0,
        trend="neutral",
        trade_direction="SHORT",
        primary_pair=primary,
    )
    kwargs: dict[str, Any] = {"client": client}
    if account_leverage_ceiling is not None:
        kwargs["account_leverage_ceiling"] = float(account_leverage_ceiling)
    validate_short_margin_eligibility([candidate], **kwargs)
    return {
        "margin_validation_status": candidate.margin_validation_status,
        "margin_eligible": candidate.margin_eligible,
        "margin_venue_symbol": candidate.margin_venue_symbol,
        "margin_max_leverage": candidate.margin_max_leverage,
    }


def build_short_feasibility_evidence(
    snapshot: Any,
    *,
    client: Any,
    notional_usd: float,
    acquisition_instant: datetime,
    interval_minutes: int = 60,
    interval_seconds: int = 3600,
    account_leverage_ceiling: float | None = None,
) -> FeasibilityEvidence:
    """Assemble genuine SHORT feasibility evidence for one committed snapshot.

    Market data is direction-agnostic (validated on the spot candles), margin
    eligibility is genuine BTNL discovery, and execution liquidity is the BTNL
    margin book (``get_pre_trade``/``get_post_trade`` with ``margin_venue_symbol``).
    Spot execution evidence is NEVER attached as BTNL: without genuine BTNL
    provenance the SHORT execution record is explicitly UNAVAILABLE (missing
    evidence), never spot-as-BTNL. The same honest ``evaluation_time``/``source_cutoff``
    contract as the LONG builder applies: the 60-minute analytical series plus a
    separately acquired fresh one-minute freshness anchor, with BOTH recorded in
    ``source_evidence_refs``.
    """
    from app.scanner.execution_validation import evaluate_execution, unavailable_execution
    from app.scanner.market_data_validation import validate_market_data

    symbol = str(snapshot.venue_instrument_id)
    moment = acquisition_instant
    candles = list(client.get_ohlc(symbol, interval=interval_minutes))
    if not candles:
        raise FeasibilityCaptureError("no source candles returned")
    # Freshness anchor: a SEPARATE, fresh closed one-minute read of the SAME
    # instrument (identical two-plane contract as the LONG builder).
    candles_1m = list(
        client.get_ohlc(symbol, interval=FRESHNESS_ANCHOR_INTERVAL_MINUTES)
    )

    evaluation_time = snapshot.evaluation_cutoff
    # Same two-plane source contract as the LONG builder: the analytical 60-minute
    # horizon keeps its semantics and its existing lookahead fail-closed rule, and
    # ``source_cutoff`` is the fresh one-minute anchor's close.
    anchor = resolve_source_evidence_anchor(
        snapshot_id=str(snapshot.snapshot_id),
        candles=candles,
        candles_1m=candles_1m,
        evaluation_time=evaluation_time,
        acquisition_instant=acquisition_instant,
        interval_minutes=interval_minutes,
        interval_seconds=interval_seconds,
    )
    # POINT-IN-TIME PRICE (identical rule to the LONG builder): the epoch's own last
    # price is the closed one-minute anchor candle's close, never a live ticker read
    # taken after the epoch.
    epoch_last = float(anchor.anchor_close_price)
    market = validate_market_data(
        candles, epoch_last, interval_minutes=interval_minutes, now=moment
    )
    market_inputs = [
        PointInTimeInput(
            name="analytical_60m_ohlc",
            kind=PIT_KIND_MARKET,
            event_cutoff=anchor.analytical_latest_cutoff,
        ),
        PointInTimeInput(
            name="freshness_1m_anchor",
            kind=PIT_KIND_MARKET,
            event_cutoff=anchor.anchor_close,
        ),
    ]

    margin = discover_short_margin(
        snapshot, client=client, account_leverage_ceiling=account_leverage_ceiling
    )
    # Margin discovery is a VENUE CAPABILITY lookup (does the margin venue list this
    # pair, at what leverage tier). Its truth is epoch-invariant, so it carries its
    # own explicit acquisition cutoff as venue metadata rather than pretending to be
    # an epoch-bounded market observation. The epoch price above is unaffected by it.
    market_inputs.append(
        PointInTimeInput(
            name="margin_venue_discovery",
            kind=PIT_KIND_VENUE_METADATA,
            event_cutoff=acquisition_instant,
            epoch_invariant=True,
        )
    )
    # The BTNL margin book can only be observed LIVE, so (identical rule to the LONG
    # builder) it is admitted only when the read itself is at or before the epoch;
    # otherwise the execution plane is explicitly unavailable instead of presenting
    # post-epoch BTNL depth as epoch liquidity.
    live_read_is_point_in_time = acquisition_instant <= evaluation_time
    if margin["margin_eligible"] and margin["margin_venue_symbol"] and live_read_is_point_in_time:
        # Genuine BTNL margin book (the SHORT quality thresholds are defined for it).
        venue_symbol = margin["margin_venue_symbol"]
        try:
            book = client.get_pre_trade(venue_symbol)
            try:
                trades = client.get_post_trade(venue_symbol, count=100)
            except Exception:  # noqa: BLE001 - absent recent trades are still present
                trades = None
            execution = evaluate_execution(
                book=book,
                validation_notional_usd=float(notional_usd),
                ticker_last=epoch_last,
                quote_to_usd_rate=1.0,
                trades=trades,
                now=moment,
            )
            market_inputs.append(
                PointInTimeInput(
                    name="btnl_margin_book",
                    kind=PIT_KIND_MARKET,
                    event_cutoff=acquisition_instant,
                )
            )
        except Exception as exc:  # noqa: BLE001 - BTNL book unavailable -> explicit absence
            execution = unavailable_execution(f"BTNL PreTrade unavailable: {exc}")
    elif margin["margin_eligible"] and margin["margin_venue_symbol"]:
        # Eligible, but the only book available was observed AFTER the epoch: it
        # cannot support an epoch-anchored record.
        execution = unavailable_execution(PIT_UNAVAILABLE_REASON)
    else:
        # No genuine BTNL provenance: the SHORT execution record is explicitly
        # UNAVAILABLE (missing evidence). Spot evidence is never used as BTNL.
        execution = unavailable_execution(
            "BTNL margin not eligible for this pair; SHORT execution evidence unavailable"
        )
    assert_point_in_time_support(market_inputs, evaluation_time=evaluation_time)

    return FeasibilityEvidence(
        instrument_version_id=snapshot.instrument_version_id,
        venue_instrument_id=symbol,
        direction="SHORT",
        evaluation_time=evaluation_time,
        source_cutoff=anchor.source_cutoff,
        source_snapshot_id=snapshot.snapshot_id,
        source_evidence_refs=(
            *anchor.provenance_refs(snapshot_id=str(snapshot.snapshot_id)),
            *(entry.provenance_ref(evaluation_time) for entry in market_inputs),
        ),
        market_data_validation=market,
        margin_validation_status=margin["margin_validation_status"],
        margin_eligible=margin["margin_eligible"],
        margin_venue_symbol=margin["margin_venue_symbol"],
        margin_max_leverage=margin["margin_max_leverage"],
        execution_validation=execution,
        # Present typed records are present evidence (a negative margin/execution
        # record leads F5 to its existing hard VETO, never missingness).
        availability=EVIDENCE_AVAILABLE,
        missingness=(),
        kraken_public_symbol=symbol,
        primary_pair=symbol,
    )


def _make_canonical_submit(client: Any) -> Callable[[dict], str]:
    """Build the live canonical-writer submit callable for one evidence payload.

    ``client`` is the canonical writer CLIENT (``WriterClient`` protocol), never a
    store owner. The client lifecycle is owned by the caller; the production
    client opens one bounded socket round-trip per submission, so no store
    connection, lock, or transaction is held across the pass.
    """
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
    from app.opip.fev_evidence_event import (
        FEASIBILITY_EVIDENCE_PRIORITY,
        FEASIBILITY_EVIDENCE_RECORDED,
    )

    def _submit(payload: dict) -> str:
        return client.submit(
            WriterIntent(
                schema_version=EVENT_SCHEMA_VERSION,
                priority=FEASIBILITY_EVIDENCE_PRIORITY,
                idempotency_key=feasibility_evidence_event_idempotency_key(payload),
                event_type=FEASIBILITY_EVIDENCE_RECORDED,
                payload=payload,
                event_time=feasibility_evidence_event_time(payload),
                correlation_id=feasibility_evidence_event_correlation_id(payload),
                causation_id=None,
                ops_handoff=None,
            )
        ).status

    return _submit


def resolve_canonical_submitter(client: Any | None = None) -> Callable[[dict], str]:
    """Return the production canonical-writer submit callable for feasibility evidence.

    SINGLE-WRITER INVARIANT. This producer is a canonical-store *consumer*, never
    an owner. ``opip-canonical-writer`` (``CanonicalWriterServer`` ->
    ``CanonicalWriter``) holds ``CanonicalStoreLock`` for its whole process
    lifetime, and lock acquisition is deliberately fail-closed with no
    wait-and-retry. A second writable ``CanonicalWriter`` opened here could
    therefore never own the live store -- it raises ``CanonicalStoreBusyError``
    and publishes no ``feasibility.evidence.recorded`` at all, while
    ``release_runtime_verifier`` waits for matching F5 evidence. Production
    submits ``WriterIntent`` over the canonical writer socket through
    ``CanonicalWriterClient``, exactly like the Feature Bus publisher and the
    alert-governor bridge.

    ``client`` is an injectable seam so a test can drive the genuine submission
    path (``_make_canonical_submit``) without a Unix socket. Production always
    constructs the socket client; no writable store handle is ever created.
    """
    if client is None:
        from app.opip.canonical.client import CanonicalWriterClient

        client = CanonicalWriterClient()
    return _make_canonical_submit(client)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--lock-path", default=None)
    parser.add_argument("--cursor-path", default=None)
    parser.add_argument("--notional-usd", type=float, default=None)
    args = parser.parse_args()

    from app.core.config import get_settings

    settings = get_settings()

    # Authorization is checked before any resource is opened, so an inert
    # (unauthorized) run touches nothing -- not the canonical store, not Kraken.
    if not feasibility_capture_authorized(settings):
        print(
            json.dumps(
                {
                    "status": "INERT",
                    "reason": "FEASIBILITY_CAPTURE_NOT_AUTHORIZED_SHADOW",
                }
            )
        )
        return

    notional, reason = resolve_capture_notional(
        settings, override=args.notional_usd
    )
    if reason is not None:
        print(json.dumps({"status": "REFUSED", "reason": reason}))
        return

    from app.jobs.capture_feature_bus_shadow import line_buffered_stdout, run_capture_locked
    from app.services.opip_feature_bus_market_source import capture_kraken_client

    # Line-buffered so a pass killed by the cron bound still leaves its LAST
    # durable phase marker in the log instead of silently dropping all output.
    line_buffered_stdout()
    # Pass-bounded per-REQUEST timeout AND an absolute pass deadline: a full
    # transport retry/backoff sequence must fit one bounded request, and the whole
    # pass's upstream cost must fit the declared budget, or a single stalled public
    # request can consume the pass and the producer is killed before it records any
    # disposition.
    pass_budget = resolve_capture_budget_seconds(settings)
    client = capture_kraken_client(
        wave_budget_seconds=PER_REQUEST_BUDGET_SECONDS,
        deadline_monotonic=monotonic() + pass_budget,
    )
    submit = resolve_canonical_submitter()

    def _build(snapshot, direction):
        if direction == "SHORT":
            return build_short_feasibility_evidence(
                snapshot,
                client=client,
                notional_usd=notional,
                acquisition_instant=datetime.now(timezone.utc),
            )
        return build_long_feasibility_evidence(
            snapshot,
            client=client,
            notional_usd=notional,
            acquisition_instant=datetime.now(timezone.utc),
        )

    result = run_capture_locked(
        lock_path=args.lock_path or None,
        lock_env=FEASIBILITY_CAPTURE_LOCK_ENV,
        lock_default=FEASIBILITY_CAPTURE_LOCK_PATH,
        marker_prefix="OPIP_FEASIBILITY_CAPTURE",
        capture_fn=lambda: capture_feasibility_evidence_shadow(
            settings=settings,
            evidence_builder=_build,
            submit_payload=submit,
            cursor_path=args.cursor_path or None,
        ),
    )
    print("O'Pip Feasibility Evidence SHADOW capture — EVIDENCE ONLY")
    print("Trading authority: NONE")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "ANALYTICAL_INTERVAL_MINUTES",
    "ANALYTICAL_INTERVAL_SECONDS",
    "ANALYTICAL_PROVENANCE_PREFIX",
    "BITNOMIAL_EXECUTION_VENUE",
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_DIRECTION",
    "FEASIBILITY_CAPTURE_LOCK_ENV",
    "FEASIBILITY_CAPTURE_LOCK_PATH",
    "FRESHNESS_ANCHOR_INTERVAL_MINUTES",
    "FRESHNESS_ANCHOR_INTERVAL_SECONDS",
    "FRESHNESS_AGE_PROVENANCE_PREFIX",
    "FRESHNESS_PROVENANCE_PREFIX",
    "FeasibilityAnchorPendingError",
    "FeasibilityCaptureError",
    "FeasibilityCaptureSummary",
    "FeasibilityEvidenceStaleError",
    "FeasibilityPointInTimeError",
    "MAX_CONTEMPORANEOUS_AGE_SECONDS",
    "MAX_FRESH_ANCHOR_AGE_SECONDS",
    "PER_REQUEST_BUDGET_SECONDS",
    "PIT_KIND_MARKET",
    "PIT_KIND_VENUE_METADATA",
    "PIT_PROVENANCE_PREFIX",
    "PIT_UNAVAILABLE_REASON",
    "PointInTimeInput",
    "SourceEvidenceAnchor",
    "anchor_source_age_seconds",
    "assert_point_in_time_support",
    "build_long_feasibility_evidence",
    "build_short_feasibility_evidence",
    "capture_feasibility_evidence_shadow",
    "discover_short_margin",
    "feasibility_capture_authorized",
    "main",
    "point_in_time_inputs",
    "resolve_capture_budget_seconds",
    "resolve_capture_notional",
    "resolve_canonical_submitter",
    "resolve_source_evidence_anchor",
]
