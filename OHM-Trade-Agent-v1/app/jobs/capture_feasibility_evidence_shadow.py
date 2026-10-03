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
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Callable

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

DEFAULT_DIRECTION = "LONG"
SUPPORTED_DIRECTIONS = frozenset({"LONG", "SHORT"})

#: Frozen maximum contemporaneous age. A snapshot whose evaluation/availability
#: boundary is older than this (relative to the real acquisition instant) is a
#: backlog record: the producer will NOT fetch current market data against it.
#: Two evaluation intervals (2 x 60s) tolerates one minute of scheduler jitter
#: while still refusing to backfill genuinely historical snapshots.
MAX_CONTEMPORANEOUS_AGE_SECONDS = 120.0

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
        return _inert_summary(mode, "FEASIBILITY_CAPTURE_NOT_AUTHORIZED_SHADOW")

    resolved_limit = (
        int(limit)
        if limit is not None
        else int(getattr(settings, "opip_feasibility_capture_limit", DEFAULT_CAPTURE_LIMIT))
    )
    resolved_budget = (
        float(budget_seconds)
        if budget_seconds is not None
        else float(
            getattr(
                settings,
                "opip_feasibility_capture_budget_seconds",
                DEFAULT_BUDGET_SECONDS,
            )
        )
    )
    bounded = min(max(1, resolved_limit), MAX_CAPTURE_LIMIT)
    budget = max(PER_BATCH_BUDGET_SECONDS, min(float(resolved_budget), MAX_BUDGET_SECONDS))
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
            return summary

        cursor: tuple[int, int] | None = persisted
        while True:
            if deadline - tick() < PER_BATCH_BUDGET_SECONDS:
                summary.budget_exhausted = True
                summary.errors.append("budget exhausted before reading the next batch")
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
            return True
        if age < 0.0:
            # Not yet observable at the acquisition instant: fail closed without
            # dropping the record -- revisit next pass.
            summary.retryable += 1
            summary.errors.append(
                f"{snapshot_id}: snapshot not yet visible at acquisition instant"
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
        except FeasibilityEvidenceStaleError as exc:
            summary.stale += 1
            summary.errors.append(f"{snapshot_id}: out-of-epoch evidence: {exc}")
            return True
        except Exception as exc:  # noqa: BLE001 - transient: may succeed on replay
            summary.retryable += 1
            summary.errors.append(
                f"{snapshot_id}: evidence assembly failed: {type(exc).__name__}: {exc}"
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
    hidden clock is read. The evidence epoch is the snapshot's own evaluation
    cutoff and ``source_cutoff`` is the truthful close of the latest completed
    source candle; the builder fails closed if that source cutoff would fall after
    the evaluation epoch (newer data must never be stamped onto an older epoch).

    A SHORT request is refused here (the SHORT route must supply genuine BTNL
    evidence through its own builder).
    """
    from app.scanner.execution_validation import evaluate_execution
    from app.scanner.market_data_validation import validate_market_data

    symbol = str(snapshot.venue_instrument_id)
    moment = acquisition_instant
    candles = list(client.get_ohlc(symbol, interval=interval_minutes))
    if not candles:
        raise FeasibilityCaptureError("no source candles returned")
    ticker_last = float(client.get_ticker(symbol)["last"])
    market = validate_market_data(
        candles, ticker_last, interval_minutes=interval_minutes, now=moment
    )
    book = client.get_pre_trade(symbol)
    try:
        trades = client.get_post_trade(symbol, count=100)
    except Exception:  # noqa: BLE001 - unavailable recent trades are still present-earlier
        trades = None
    execution = evaluate_execution(
        book=book,
        validation_notional_usd=float(notional_usd),
        ticker_last=ticker_last,
        quote_to_usd_rate=1.0,
        trades=trades,
        now=moment,
    )

    evaluation_time = snapshot.evaluation_cutoff
    # Truthful source cutoff: the close of the latest COMPLETED candle. If it falls
    # after the evaluation epoch, the market has already moved past this epoch and
    # the evidence would be retrospective -- fail closed rather than backdate.
    completed = candles[:-1] if len(candles) > 1 else candles
    latest_close = int(completed[-1].timestamp) + int(interval_seconds)
    source_cutoff = datetime.fromtimestamp(latest_close, tz=timezone.utc)
    if source_cutoff > evaluation_time:
        raise FeasibilityEvidenceStaleError(
            f"source cutoff {source_cutoff.isoformat()} is after the evaluation "
            f"epoch {evaluation_time.isoformat()}"
        )

    return FeasibilityEvidence(
        instrument_version_id=snapshot.instrument_version_id,
        venue_instrument_id=symbol,
        direction=DEFAULT_DIRECTION,
        evaluation_time=evaluation_time,
        source_cutoff=source_cutoff,
        source_snapshot_id=snapshot.snapshot_id,
        source_evidence_refs=(snapshot.snapshot_id,),
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


def _make_canonical_submit(writer: Any) -> Callable[[dict], str]:
    """Build the live canonical-writer submit callable for one evidence payload.

    The writer lifecycle is owned by the caller (created before the pass, closed
    after) so the single connection is reused for the whole bounded pass.
    """
    from app.opip.canonical.models import WriterIntent
    from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
    from app.opip.fev_evidence_event import (
        FEASIBILITY_EVIDENCE_PRIORITY,
        FEASIBILITY_EVIDENCE_RECORDED,
    )

    def _submit(payload: dict) -> str:
        return writer.submit(
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

    notional = (
        float(args.notional_usd)
        if args.notional_usd is not None
        else float(
            getattr(settings, "opip_feasibility_capture_notional_usd", 0.0) or 0.0
        )
    )
    if not (notional == notional and notional > 0):
        print(json.dumps({"status": "REFUSED", "reason": "notional not configured"}))
        return

    from app.exchanges.kraken import KrakenClient
    from app.jobs.capture_feature_bus_shadow import run_capture_locked
    from app.opip.canonical.paths import db_path
    from app.opip.canonical.writer import CanonicalWriter

    client = KrakenClient()
    writer = CanonicalWriter(db_path())
    try:
        submit = _make_canonical_submit(writer)
        result = run_capture_locked(
            lock_path=args.lock_path or None,
            lock_env=FEASIBILITY_CAPTURE_LOCK_ENV,
            lock_default=FEASIBILITY_CAPTURE_LOCK_PATH,
            capture_fn=lambda: capture_feasibility_evidence_shadow(
                settings=settings,
                evidence_builder=lambda snapshot, direction: build_long_feasibility_evidence(
                    snapshot,
                    client=client,
                    notional_usd=notional,
                    acquisition_instant=datetime.now(timezone.utc),
                ),
                submit_payload=submit,
                cursor_path=args.cursor_path or None,
            ),
        )
    finally:
        writer.close()
    print("O'Pip Feasibility Evidence SHADOW capture — EVIDENCE ONLY")
    print("Trading authority: NONE")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_DIRECTION",
    "FEASIBILITY_CAPTURE_LOCK_ENV",
    "FEASIBILITY_CAPTURE_LOCK_PATH",
    "FeasibilityCaptureError",
    "FeasibilityCaptureSummary",
    "FeasibilityEvidenceStaleError",
    "MAX_CONTEMPORANEOUS_AGE_SECONDS",
    "build_long_feasibility_evidence",
    "capture_feasibility_evidence_shadow",
    "feasibility_capture_authorized",
    "main",
]
