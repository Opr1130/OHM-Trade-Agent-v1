"""Bounded, production-safe Feature Bus SHADOW capture.

This is the scheduled producer for the owner-authorized R4-B2 shadow evidence
path:

    market source -> Feature Bus SHADOW capture -> canonical writer
    (FEATURE_SNAPSHOT_RECORDED) -> committed-snapshot reader -> target spine SHADOW

It reuses the *already-proven* Feature Bus components (the Kraken source and
instrument provider the manual pilot uses, the fixed evaluation grid,
rolling-state/checkpoint continuity, the revision ledger and
``FeatureBusPublisher``). No new feature math, no new schema, no second
market-data authority.

This job runs as its OWN bounded process on the repository's ONE scheduler (cron),
never inside the protected unified cycle. It therefore cannot hold or delay
protection. THREE independent bounds contain it:

* the F3 60-second evaluation grid: one pass is one closed minute, so consecutive
  passes yield consecutive ``FeatureSnapshot`` cutoffs (the R4-B2 cadence bridge);
* an internal total wall-clock budget (``budget_seconds``), clamped below the
  60-second slot, split into an ACQUISITION deadline and a reserved
  materialization window: before each acquisition wave the loop proves that the
  DECLARED worst-case cost of one full request (every attempt, every inter-attempt
  backoff/jitter sleep and every rate-limiter wait, bounded by the pass-scoped
  client's derived per-attempt timeout) still fits the remaining acquisition
  budget, so no upstream operation ever starts that cannot complete, and one
  instrument can never consume the whole pass; and
* a cron ``timeout`` subprocess bound (final containment only), also below 60s.

Every phase and every disposition is emitted as a flushed, line-buffered
``OPIP_FEATURE_BUS_CAPTURE_PHASE=`` marker (including an explicit
deadline-exhausted disposition, a per-instrument failure classification, a
lock-contention skip and a zero-materialized-snapshot disposition), so a pass that
IS terminated by the containment bound is still attributable from its own log.

Acquisition is bounded-concurrent (Phase A) and materialization is strictly
sequential (Phase B): Kraken has no bulk multi-pair OHLC endpoint, so each target
instrument needs one bounded public request per closed minute, but the canonical
writer has a single connection, so dependent writes never run concurrently.

It is authorized only when ``OPIP_FEATURE_BUS_MODE=shadow`` AND
``OPIP_CANONICAL_WRITER_MODE=shadow`` (exact match). Feature Bus ``active`` does
NOT authorize this SHADOW producer. It is read/evidence only with respect to
trading: it grants no ranking, admission, allocation, order or exchange authority.

Fail-closed: a per-instrument failure is recorded and skipped (never fabricated),
an errored batch never triggers a fresh snapshot, and a budget-exhausted pass
stops cleanly with explicit evidence.
"""

from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable

from app.opip.canonical.bridge import resolve_writer_mode
from app.opip.canonical.protocol import WriterDeadlineExceeded
from app.opip.features.checkpoint_store import CheckpointDeadlineExceeded
from app.opip.features.pipeline import (
    CycleIdentityMismatch,
    declared_cycle_submit_bound,
    run_cycle,
)
from app.opip.features.revision_ledger import RevisionLedgerDeadlineExceeded
from app.opip.features.publisher import (
    FeatureBusPublisher,
    resolve_feature_bus_mode,
)
from app.opip.market.aggregates import grid_floor
from app.opip.market.instrument_version_store import hydrate_instrument_version_registry
from app.services.kraken_transport import KrakenTransportDeadlineExceeded
from app.services.opip_feature_bus_market_source import (
    KRAKEN_OHLC_SOURCE_LABEL,
    KrakenInstrumentProvider,
    capture_kraken_client,
    capture_worst_case_request_seconds,
    classify_capture_error,
    kraken_minute_source,
)

#: Default bound: how many instruments one capture pass may fetch.
DEFAULT_CAPTURE_LIMIT = 8
MAX_CAPTURE_LIMIT = 32

#: Default total wall-clock budget for one pass. The R4-B2 shadow cadence is the
#: F3 60-second evaluation grid, so one pass must finish well inside its minute
#: slot. The cron ``timeout`` (final containment only) sits below the cadence too.
DEFAULT_BUDGET_SECONDS = 45.0
MAX_BUDGET_SECONDS = 50.0

#: Conservative per-request reservation used to gate each acquisition wave. The
#: pass-scoped Kraken client's per-attempt timeout is DERIVED so that one complete
#: request + all retries + all retry backoff + all rate-limiter waiting fits this
#: budget (see ``capture_worst_case_request_seconds``); the pass starts a wave only
#: when at least that declared worst case remains before the acquisition deadline.
#: The cron timeout (below the cadence) stays final containment only.
PER_REQUEST_BUDGET_SECONDS = 15.0

#: Time RESERVED at the end of the pass for Phase B (sequential canonical
#: materialization). Acquisition may never consume it, so a completed acquisition
#: is always committed rather than discarded because the pass deadline expired
#: while the writer was still working. Clamped so it can never exceed the pass.
CAPTURE_MATERIALIZE_RESERVE_SECONDS = 10.0

#: Declared worst-case wall clock of ONE Phase-B canonical submit. The reserve
#: above bounds ACQUISITION; this constant bounds the CEILING of a single
#: MATERIALIZATION write, because it mirrors the canonical writer client's own
#: per-submit socket timeout (``CanonicalWriterClient(timeout=5.0)``). A producer
#: never admits a cycle unless the remaining Phase-B budget covers
#: :func:`declared_cycle_submit_bound` submits at the per-submit timeout it is
#: about to enforce, so the cron ``timeout`` stays final containment ONLY and is
#: never the mechanism that bounds Phase B.
CAPTURE_MATERIALIZE_WRITE_BOUND_SECONDS = 5.0

#: Smallest per-submit timeout Phase B is willing to DECLARE. Phase B shares the
#: remaining materialization budget equally across every submit the cycle may make
#: (``declared_cycle_submit_bound``), so a cycle whose share falls below this floor
#: cannot be given a meaningful bounded submit at all: it is not started, and the
#: acquired evidence is recorded as an explicit incomplete disposition instead of
#: being run against the cron containment.
CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS = 0.05

#: The canonical writer's explicit deadline-cut exception name, as recorded on a
#: refused ``PublishOutcome``. A submit that could not START before the absolute
#: Phase-B deadline elapsed leaves acquired evidence uncommitted, which is an
#: explicit incomplete disposition rather than a silent partial write.
WRITER_DEADLINE_ERROR_NAME = WriterDeadlineExceeded.__name__

#: Bounded acquisition concurrency. Kraken has no bulk multi-pair OHLC endpoint,
#: so one public request per instrument is required for each closed minute. A
#: small worker pool bounds the worst case to ``ceil(N / concurrency)`` request
#: timeouts instead of ``N``. The shared Kraken public transport is already
#: thread-safe and rate-limited (token bucket), so concurrency only hides request
#: latency; it cannot exceed the transport's own request rate.
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 8


@dataclass
class FeatureBusCaptureSummary:
    """Machine-readable outcome of one bounded capture pass."""

    mode: str
    enabled: bool
    inert: bool
    reason: str | None = None
    instruments: int = 0
    committed_instrument_versions: int = 0
    fetched: int = 0
    cycles: int = 0
    promoted: int = 0
    deferred: int = 0
    rejected_identity: int = 0
    source_errors: int = 0
    budget_exhausted: bool = False
    materialize_incomplete: bool = False
    elapsed_seconds: float = 0.0
    publish_counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "enabled": self.enabled,
            "inert": self.inert,
            "reason": self.reason,
            "instruments": self.instruments,
            "committed_instrument_versions": self.committed_instrument_versions,
            "fetched": self.fetched,
            "cycles": self.cycles,
            "promoted": self.promoted,
            "deferred": self.deferred,
            "rejected_identity": self.rejected_identity,
            "source_errors": self.source_errors,
            "budget_exhausted": self.budget_exhausted,
            "materialize_incomplete": self.materialize_incomplete,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "publish_counts": dict(self.publish_counts),
            "errors": list(self.errors[:8]),
        }


def line_buffered_stdout() -> None:
    """Make every disposition line durable before a process bound can kill it.

    The cron runs this producer with stdout redirected to a log file, where Python
    block-buffers by default. A pass terminated by the cron ``timeout`` therefore
    loses EVERY buffered line and leaves no durable record at all -- the release
    pipeline cannot then distinguish a stalled producer from a silent evidence
    drop. Line buffering makes each emitted marker durable as it is written.
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):  # pragma: no cover - non-reconfigurable stream
        pass


def emit_capture_marker(
    stage: str, *, prefix: str = "OPIP_FEATURE_BUS_CAPTURE", **fields: Any
) -> None:
    """Emit one durable, machine-readable capture disposition marker.

    Mirrors the unified-cycle phase markers: flushed line-by-line so the LAST
    marker written before any termination names the stage that overran. This is
    observability only; it grants no authority and changes no behavior.
    """
    detail = " ".join(f"{key}={value}" for key, value in fields.items())
    line = f"{prefix}_PHASE={stage} {detail}".rstrip()
    print(line, flush=True)


def _bound_writer_operation_timeout(
    publisher: Any, per_submit_seconds: float
) -> float | None:
    """Tighten the canonical writer's OWN per-operation timeout for this cycle.

    Phase B admits a cycle only when the remaining materialization budget covers
    EVERY submit that cycle may make at ``per_submit_seconds`` each; this clamps the
    SAME persistent writer instance the cycle's submits will use (``publisher``
    resolves one client and reuses it), so the enforced timeout is the one the
    budget was proved against rather than a discarded instance's. It only ever
    NARROWS a timeout: a client that exposes no timeout keeps its own bound, and no
    timeout is ever widened. Returns the timeout now in force (``None`` when the
    client exposes none). Observability and bounding only: it grants no authority
    and changes no evidence content.
    """
    try:
        client = publisher.resolved_writer_client()
    except Exception:  # noqa: BLE001 - an unresolvable client keeps the write bound
        return None
    current = getattr(client, "timeout", None)
    if not isinstance(current, (int, float)) or float(current) <= 0.0:
        return None
    bounded = max(CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS, min(float(current), float(per_submit_seconds)))
    try:
        setattr(client, "timeout", bounded)
    except Exception:  # noqa: BLE001 - a read-only client keeps its own bound
        return float(current)
    return bounded


def _bind_writer_deadline(publisher: Any, deadline_monotonic: float) -> float | None:
    """Bind ONE absolute Phase-B wall-clock deadline onto the persistent writer.

    Phase B performs MANY dependent canonical submits. A per-operation timeout can
    only bound a SINGLE blocking socket call, and one roundtrip makes several
    (connect, sendall, and every individual ``recv`` inside ``_recvexact``), so the
    ABSOLUTE ``materialize_deadline`` is the only enforceable wall-clock bound.
    Binding it ONCE on the SAME client every submit uses means each later submit
    inherits only the time REMAINING in the original Phase-B window instead of a
    fresh per-submit budget. A client that exposes no deadline seam keeps its own
    behavior (the per-submit timeout clamp still applies). Returns the deadline now
    in force, or ``None`` when the client cannot be bounded this way.
    """
    try:
        client = publisher.resolved_writer_client()
    except Exception:  # noqa: BLE001 - an unresolvable client keeps the write bound
        return None
    binder = getattr(client, "bind_deadline", None)
    if callable(binder):
        try:
            return binder(deadline_monotonic)
        except Exception:  # noqa: BLE001 - a refusing client keeps the write bound
            return None
    if hasattr(client, "deadline_monotonic"):
        try:
            client.deadline_monotonic = deadline_monotonic
            return deadline_monotonic
        except Exception:  # noqa: BLE001
            return None
    return None


def shadow_capture_authorized(settings: Any) -> bool:
    """Exact SHADOW authorization for THIS producer.

    Requires the Feature Bus AND the canonical writer in ``shadow``. Feature Bus
    ``active`` does not authorize this SHADOW-specific scheduled producer. The
    shared publisher helper's broader ``{shadow, active}`` semantics are left
    unchanged; this exact gate is owned here.
    """
    return (
        resolve_feature_bus_mode(settings) == "shadow"
        and resolve_writer_mode(settings) == "shadow"
    )


def _inert_summary(mode: str, reason: str) -> FeatureBusCaptureSummary:
    return FeatureBusCaptureSummary(
        mode=mode, enabled=False, inert=True, reason=reason
    )


def _cause_chain_contains(error: BaseException, target: type[BaseException]) -> bool:
    """Bounded walk of ``__cause__``/``__context__`` for a typed exception.

    ``KrakenClient._get`` re-raises ``KrakenTransportDeadlineExceeded`` as
    ``KrakenAPIError`` with the typed exception as ``__cause__``, so the typed
    deadline is only visible through the chain. Bounded so a pathological cycle
    cannot hang the producer. Observability only: it grants no authority.
    """
    link: BaseException | None = error
    for _ in range(8):
        if link is None:
            return False
        if isinstance(link, target):
            return True
        link = link.__cause__ or link.__context__
    return False


def _setup_deadline_exhausted(
    summary: FeatureBusCaptureSummary,
    *,
    phase: str,
    setup_deadline: float,
    tick: Callable[[], float],
    started: float,
) -> None:
    """Record a fail-closed setup-budget exhaustion disposition.

    Called when a mandatory pre-acquisition phase could not complete inside the
    setup envelope (``acquisition_deadline - wave_bound``). The caller returns
    immediately afterwards, BEFORE acquisition, so ``fetch_through`` is never
    called and no snapshot is fabricated. Observability and fail-closed only.
    """
    summary.budget_exhausted = True
    summary.errors.append(
        f"setup budget exhausted during {phase}; first acquisition wave cannot be admitted"
    )
    summary.elapsed_seconds = tick() - started
    emit_capture_marker(
        "setup_deadline_exhausted",
        where=phase,
        reason="INSUFFICIENT_SETUP_BUDGET",
        setup_remaining_seconds=round(setup_deadline - tick(), 3),
        elapsed_seconds=round(summary.elapsed_seconds, 3),
    )


def capture_feature_bus_shadow(
    *,
    settings: Any = None,
    limit: int | None = None,
    budget_seconds: float | None = None,
    concurrency: int | None = None,
    now: datetime | None = None,
    publisher: FeatureBusPublisher | None = None,
    instrument_provider: KrakenInstrumentProvider | None = None,
    source: Any | None = None,
    restore_continuity: Any | None = None,
    clock: Callable[[], float] | None = None,
    wall_clock: Callable[[], datetime] | None = None,
) -> FeatureBusCaptureSummary:
    """Run one bounded, budgeted SHADOW capture pass. Writes nothing unless authorized.

    ``limit`` and ``budget_seconds`` default to the configured Settings fields
    (``opip_feature_bus_capture_limit`` / ``opip_feature_bus_capture_budget_seconds``)
    and fall back to the module constants only when the settings object does not
    expose them. An explicit argument overrides the setting. All heavy dependencies
    are injectable so a caller (or test) supplies proven components.
    """
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()

    mode = resolve_feature_bus_mode(settings)
    if not shadow_capture_authorized(settings):
        emit_capture_marker("inert", reason="FEATURE_BUS_CAPTURE_NOT_AUTHORIZED_SHADOW")
        return _inert_summary(mode, "FEATURE_BUS_CAPTURE_NOT_AUTHORIZED_SHADOW")

    resolved_limit = (
        int(limit)
        if limit is not None
        else int(getattr(settings, "opip_feature_bus_capture_limit", DEFAULT_CAPTURE_LIMIT))
    )
    resolved_budget = (
        float(budget_seconds)
        if budget_seconds is not None
        else float(
            getattr(
                settings,
                "opip_feature_bus_capture_budget_seconds",
                DEFAULT_BUDGET_SECONDS,
            )
        )
    )
    resolved_concurrency = (
        int(concurrency)
        if concurrency is not None
        else int(getattr(settings, "opip_feature_bus_capture_concurrency", DEFAULT_CONCURRENCY))
    )
    bounded = min(max(1, resolved_limit), MAX_CAPTURE_LIMIT)
    # The one-minute cadence bounds the whole pass; the internal budget is clamped
    # below the 60s slot so the internal budget always stops the pass first and the
    # cron timeout is only final containment.
    budget = max(PER_REQUEST_BUDGET_SECONDS, min(float(resolved_budget), MAX_BUDGET_SECONDS))
    workers = min(max(1, resolved_concurrency), MAX_CONCURRENCY)
    tick = clock or monotonic
    started = tick()
    deadline = started + budget
    # Phase B (sequential canonical materialization) is reserved out of the pass
    # budget so ACQUISITION can never consume the time needed to COMMIT what it
    # acquired: a pass that fetched successfully but could not write would be an
    # evidence drop with no disposition.
    materialize_reserve = min(
        CAPTURE_MATERIALIZE_RESERVE_SECONDS, max(0.0, budget - PER_REQUEST_BUDGET_SECONDS)
    )
    acquisition_deadline = deadline - materialize_reserve
    #: Declared worst-case wall clock of ONE acquisition request (all attempts,
    #: all retry backoff, all rate-limiter waiting). No upstream operation may
    #: start when its worst-case bounded cost cannot fit the remaining budget.
    #: Resolved BEFORE the setup envelope so the envelope is derived from the
    #: ACTUAL bound the acquisition gate will use, not from a duplicated constant:
    #: retry configuration and the minimum request-timeout floor can make the true
    #: declared worst case larger than ``PER_REQUEST_BUDGET_SECONDS``.
    wave_bound = PER_REQUEST_BUDGET_SECONDS
    if instrument_provider is None or source is None:
        wave_bound = capture_worst_case_request_seconds(
            wave_budget_seconds=PER_REQUEST_BUDGET_SECONDS
        )
    #: The setup envelope: pre-acquisition work (refresh, publication, continuity)
    #: may consume at most ``acquisition_deadline - wave_bound``, so the FIRST
    #: bounded acquisition wave is always still admissible when setup finishes.
    #: Derived from the SAME ``wave_bound`` the acquisition gate uses, so the two
    #: can never drift. This is the budget architecture the pass previously left
    #: implicit: without it, setup could silently consume the first wave's reserve
    #: and the shortage was only discovered at the acquisition gate, after setup
    #: had already run.
    setup_deadline = acquisition_deadline - wave_bound
    moment = now or datetime.now(timezone.utc)
    now_utc = wall_clock or (lambda: datetime.now(timezone.utc))
    cycle_cutoff = grid_floor(moment)
    publisher = publisher or FeatureBusPublisher(settings=settings)

    emit_capture_marker(
        "start",
        limit=bounded,
        budget_seconds=budget,
        concurrency=workers,
        cycle_cutoff=cycle_cutoff.isoformat(),
    )

    if restore_continuity is None:
        from app.jobs.run_feature_bus_pilot import restore_pilot_continuity_batch

        # Default production continuity is deadline-aware AND batch-scoped: the
        # adapter captures the setup deadline AND the same clock domain that
        # produced it, so the durable reads' SQLite progress handler compares
        # against the SAME clock. The batch path scans each canonical event
        # family ONCE for the whole instrument batch instead of once per
        # instrument, removing the repeated full-history amplification
        # implicated by the restore_continuity production overrun. Injected
        # one-argument callables are untouched.
        def restore_continuity(versions: Any) -> Any:  # type: ignore[misc]
            return restore_pilot_continuity_batch(
                versions,
                deadline_monotonic=setup_deadline,
                clock=tick,
                observer=lambda stage, seconds: emit_capture_marker(
                    "continuity_phase",
                    stage=stage,
                    phase_seconds=round(seconds, 3),
                ),
            )
    market_client = None
    if instrument_provider is None or source is None:
        # ONE pass-scoped client so the provider and the minute source share the
        # pass-bounded request timeout, the pass acquisition deadline and the
        # shared rate limiter. Without this a single stalled public request could
        # consume the whole pass budget through transport retries and the producer
        # would be killed before recording any disposition.
        #
        # The setup envelope is STRICTER than the acquisition deadline, so the
        # pass-scoped client is armed with the setup deadline for the
        # pre-acquisition phases and re-armed with the acquisition deadline
        # immediately before Phase A. A pre-acquisition request can therefore
        # never consume the first wave's reserve.
        market_client = capture_kraken_client(
            wave_budget_seconds=PER_REQUEST_BUDGET_SECONDS,
            deadline_monotonic=setup_deadline,
        )
        emit_capture_marker(
            "budget_declared",
            wave_bound_seconds=round(wave_bound, 3),
            acquisition_budget_seconds=round(acquisition_deadline - started, 3),
            setup_budget_seconds=round(setup_deadline - started, 3),
            materialize_reserve_seconds=round(materialize_reserve, 3),
            request_timeout_seconds=round(market_client.timeout_seconds, 3),
        )
    #: Provenance flag: refresh is setup-budget-classifiable ONLY when the
    #: provider is the locally created one backed by the pass-scoped client that
    #: was explicitly armed with ``setup_deadline``. An INJECTED provider's
    #: deadline provenance is not ours, so its chained Kraken deadline keeps
    #: ordinary provider-failure semantics.
    refresh_setup_bound = instrument_provider is None
    if instrument_provider is None:
        registry = hydrate_instrument_version_registry()
        instrument_provider = KrakenInstrumentProvider(
            registry=registry, client=market_client
        )
    if source is None:
        source = kraken_minute_source(market_client)

    summary = FeatureBusCaptureSummary(mode=mode, enabled=True, inert=False)
    # Bind the setup deadline onto the writer client BEFORE any pre-acquisition
    # phase, so a canonical submit during publication is bounded by the setup
    # envelope rather than the (later) materialize deadline. Phase B re-binds the
    # materialize deadline below, exactly as before.
    #
    # Provenance flag: a ``WriterDeadlineExceeded`` outcome is setup-budget
    # classifiable ONLY when the bind actually took effect on the client the
    # publisher will submit through. An injected/unbound client that merely
    # returns that error code keeps ordinary publication-failure semantics,
    # because we cannot prove the cut belongs to OUR setup deadline.
    writer_setup_deadline_bound = (
        _bind_writer_deadline(publisher, setup_deadline) is not None
    )
    emit_capture_marker("refresh_universe")
    refresh_started = tick()
    try:
        universe = instrument_provider.refresh(observed_at_utc=moment)
    except Exception as exc:  # noqa: BLE001 - an unavailable source must not fabricate
        # A typed Kraken deadline during refresh is setup-budget exhaustion ONLY
        # when refresh is backed by the locally created client we armed with
        # ``setup_deadline``. Classified by the typed cause chain, NOT by the
        # clock: a fail-fast transport may raise before the literal deadline when
        # the remaining budget cannot safely start an attempt. An injected
        # provider's chained deadline is not ours and keeps ordinary semantics.
        if refresh_setup_bound and _cause_chain_contains(
            exc, KrakenTransportDeadlineExceeded
        ):
            _setup_deadline_exhausted(
                summary,
                phase="refresh_universe",
                setup_deadline=setup_deadline,
                tick=tick,
                started=started,
            )
            return summary
        summary.errors.append(f"instrument refresh failed: {type(exc).__name__}: {exc}")
        summary.elapsed_seconds = tick() - started
        emit_capture_marker(
            "done",
            where="refresh_universe",
            status="FAILED",
            elapsed_seconds=round(summary.elapsed_seconds, 3),
            error=f"{type(exc).__name__}",
        )
        return summary
    emit_capture_marker(
        "universe_ready",
        instruments=len(universe),
        phase_seconds=round(tick() - refresh_started, 3),
        elapsed_seconds=round(tick() - started, 3),
        budget_remaining=round(acquisition_deadline - tick(), 3),
        setup_remaining=round(setup_deadline - tick(), 3),
    )

    selected = list(universe[:bounded])
    summary.instruments = len(selected)

    emit_capture_marker("publish_instrument_versions", count=len(selected))
    publish_started = tick()
    committed_versions = []
    for version in selected:
        outcome = publisher.publish_instrument_version(version)
        if outcome.committed:
            committed_versions.append(version)
            continue
        # A writer deadline cut while the writer is setup-bound is setup-budget
        # exhaustion, not an ordinary publication failure. Classified by the
        # typed error code AND the bind provenance, NOT by the clock: an
        # injected/unbound client that merely returns this error code keeps
        # ordinary publication-failure semantics.
        if (
            writer_setup_deadline_bound
            and str(outcome.error_code) == WRITER_DEADLINE_ERROR_NAME
        ):
            _setup_deadline_exhausted(
                summary,
                phase="publish_instrument_versions",
                setup_deadline=setup_deadline,
                tick=tick,
                started=started,
            )
            return summary
        summary.errors.append(
            f"instrument version not committed: {outcome.status} {outcome.error_code}"
        )
    summary.committed_instrument_versions = len(committed_versions)

    if not committed_versions:
        summary.errors.append("no instrument version committed; nothing captured")
        summary.publish_counts = publisher.summary()
        summary.elapsed_seconds = tick() - started
        emit_capture_marker(
            "done",
            where="publish_instrument_versions",
            status="NOTHING_CAPTURED",
            elapsed_seconds=round(summary.elapsed_seconds, 3),
        )
        return summary
    emit_capture_marker(
        "instrument_versions_committed",
        committed=len(committed_versions),
        phase_seconds=round(tick() - publish_started, 3),
        elapsed_seconds=round(tick() - started, 3),
        budget_remaining=round(acquisition_deadline - tick(), 3),
        setup_remaining=round(setup_deadline - tick(), 3),
    )

    emit_capture_marker("restore_continuity", instruments=len(committed_versions))
    continuity_started = tick()
    try:
        restored_states, restored_ledgers, source_watermarks = restore_continuity(
            committed_versions
        )
    except (CheckpointDeadlineExceeded, RevisionLedgerDeadlineExceeded):
        # A durable read that could not finish inside the setup envelope is
        # setup-budget exhaustion. Fail closed BEFORE acquisition: no partial
        # continuity is used and fetch_through is never called.
        _setup_deadline_exhausted(
            summary,
            phase="restore_continuity",
            setup_deadline=setup_deadline,
            tick=tick,
            started=started,
        )
        return summary
    emit_capture_marker(
        "continuity_restored",
        phase_seconds=round(tick() - continuity_started, 3),
        elapsed_seconds=round(tick() - started, 3),
        budget_remaining=round(acquisition_deadline - tick(), 3),
        setup_remaining=round(setup_deadline - tick(), 3),
    )

    # Phase A - bounded-concurrent acquisition. Each target instrument needs
    # exactly one bounded public request for the current closed minute. The
    # deadline is re-checked before EVERY wave, so once the remaining budget
    # cannot fit one more bounded request the pass stops requesting more and
    # records explicit budget-exhausted evidence. The internal budget is the
    # graceful stop; the bounded cron timeout is final containment only. The
    # shared Kraken transport is thread-safe and rate-limited, so concurrency
    # only hides request latency; wave gating bounds the worst-case overrun past
    # the deadline to a single wave.
    acquired: dict[str, tuple[Any, datetime]] = {}

    def _acquire(version: Any) -> Any:
        return source.fetch_through(
            version,
            watermark=source_watermarks.get(version.instrument_version_id),
            now=cycle_cutoff,
        )

    # Setup is complete and within its envelope: re-arm the pass-scoped client
    # with the acquisition deadline so Phase A may use the full acquisition
    # window. Only the locally created client is re-armed; an injected
    # provider/source path never creates one. The existing first-wave gate below
    # remains the second defensive check and is unchanged.
    if market_client is not None:
        market_client.deadline_monotonic = acquisition_deadline
    emit_capture_marker(
        "acquire",
        instruments=len(committed_versions),
        concurrency=workers,
        pre_acquisition_seconds=round(tick() - started, 3),
        budget_remaining=round(acquisition_deadline - tick(), 3),
        wave_bound_seconds=round(wave_bound, 3),
    )
    wave_index = 0
    while wave_index < len(committed_versions):
        remaining = acquisition_deadline - tick()
        if remaining < wave_bound:
            # Not enough budget for one more bounded request: stop cleanly, emit a
            # DURABLE deadline-exhausted disposition, and never start work whose
            # worst-case cost cannot fit.
            summary.budget_exhausted = True
            summary.errors.append(
                "budget exhausted before fetching all instruments"
            )
            emit_capture_marker(
                "deadline_exhausted",
                where="acquire",
                reason="INSUFFICIENT_ACQUISITION_BUDGET",
                remaining_seconds=round(remaining, 3),
                required_seconds=round(wave_bound, 3),
                instruments_acquired=len(acquired),
                instruments_pending=len(committed_versions) - wave_index,
            )
            break
        wave = committed_versions[wave_index : wave_index + workers]
        wave_index += workers
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_acquire, version): version for version in wave}
            for future in as_completed(futures):
                version = futures[future]
                version_id = version.instrument_version_id
                # Record each instrument's OWN fetch-completion instant, so its
                # decision availability is that instrument's post-fetch time (not
                # a later pass-wide instant), faithful to the point-in-time rule.
                completed_at = now_utc()
                try:
                    acquired[version_id] = (future.result(), completed_at)
                except Exception as exc:  # noqa: BLE001 - one instrument must not stop others
                    acquired[version_id] = (exc, completed_at)
                    # A failed acquisition is a DURABLE disposition, never a silent
                    # gap: the release receipt must be able to distinguish a stalled
                    # request from a silent evidence drop.
                    emit_capture_marker(
                        "acquire_failure",
                        instrument=version_id,
                        reason=classify_capture_error(exc),
                        error=type(exc).__name__,
                        elapsed_seconds=round(tick() - started, 3),
                    )

    emit_capture_marker(
        "acquire_complete",
        acquired=len(acquired),
        budget_exhausted=summary.budget_exhausted,
        deadline_remaining=round(acquisition_deadline - tick(), 3),
        elapsed_seconds=round(tick() - started, 3),
    )

    # Phase B - sequential materialization with its OWN absolute deadline. The
    # canonical writer has a single connection, so dependent writes commit in
    # instrument order, never concurrently: acquisition is bounded and parallel,
    # evidence stays serial. The reserve stops ACQUISITION early; this deadline
    # bounds MATERIALIZATION itself, so a slow canonical submission can never
    # consume the reserve and cross cron containment with acquired evidence
    # uncommitted. A write that cannot fit is never started.
    materialize_deadline = deadline
    # ONE absolute Phase-B deadline, bound onto the SAME persistent writer client
    # every submit of every cycle uses. A per-operation socket timeout cannot bound
    # a multi-call roundtrip; this deadline is the final enforceable wall-clock
    # bound and is inherited as TIME REMAINING, never re-created per submit.
    _bind_writer_deadline(publisher, materialize_deadline)
    emit_capture_marker(
        "materialize",
        count=len(acquired),
        deadline_remaining=round(materialize_deadline - tick(), 3),
        write_bound_seconds=round(CAPTURE_MATERIALIZE_WRITE_BOUND_SECONDS, 3),
        min_write_seconds=round(CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS, 3),
    )
    pending_commits = sum(
        1
        for result_or_error, _ in acquired.values()
        if not isinstance(result_or_error, BaseException) and result_or_error.error is None
    )
    committed_this_phase = 0
    for version in committed_versions:
        version_id = version.instrument_version_id
        if version_id not in acquired:
            # Budget-exhausted: this instrument was never requested.
            continue
        result_or_error, completed_at = acquired[version_id]
        if isinstance(result_or_error, BaseException):
            summary.source_errors += 1
            summary.errors.append(
                f"{version_id}: fetch failed: "
                f"{type(result_or_error).__name__}: {result_or_error}"
            )
            continue
        batch = result_or_error
        if batch.error:
            # A failed fetch is not a fresh successful observation cycle. Record the
            # failure; do NOT run the pipeline, so no snapshot is fabricated.
            summary.source_errors += 1
            summary.errors.append(f"{version_id}: source error: {batch.error}")
            continue

        # Budget proof BEFORE starting this cycle: one ``run_cycle`` performs MANY
        # dependent canonical submits (every newly committed observation, every
        # coverage gap, the snapshot, the optional restart, the checkpoint), so the
        # per-cycle cost is the pipeline's declared submit bound for THIS batch
        # times the per-submit timeout actually enforced. The remaining Phase-B
        # budget is shared equally across those submits, and the cycle is started
        # only when that share is a meaningful submit timeout; otherwise no submit
        # of this cycle is started at all.
        remaining = materialize_deadline - tick()
        submit_bound = declared_cycle_submit_bound(len(batch.observations))
        per_submit = min(
            CAPTURE_MATERIALIZE_WRITE_BOUND_SECONDS, remaining / submit_bound
        )
        if per_submit < CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS:
            summary.materialize_incomplete = True
            summary.errors.append(
                f"{version_id}: materialization incomplete: "
                f"{remaining:.3f}s remaining cannot fit "
                f"{submit_bound} bounded canonical submits"
            )
            emit_capture_marker(
                "materialize_incomplete",
                where="materialize",
                reason="INSUFFICIENT_MATERIALIZE_BUDGET",
                instrument=version_id,
                remaining_seconds=round(remaining, 3),
                submit_bound=submit_bound,
                required_seconds=round(
                    submit_bound * CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS, 3
                ),
                committed=committed_this_phase,
                pending=pending_commits,
            )
            break
        # The SAME persistent writer instance every submit of this cycle will use,
        # narrowed to the per-submit timeout the budget was proved against. Only
        # ever tightens; never widened.
        _bound_writer_operation_timeout(publisher, per_submit)
        summary.fetched += 1

        prior = restored_states.get(version_id)
        ledger = restored_ledgers.get(version_id)
        window_start = None
        if prior is not None and prior.last_interval_epoch is not None:
            window_start = datetime.fromtimestamp(
                prior.last_interval_epoch, tz=timezone.utc
            )
        # Decision availability is this instrument's completed fetch time, never
        # the pre-fetch start: live observation receipt/visibility occurs after
        # network completion.
        evaluated_at = max(completed_at, cycle_cutoff)
        try:
            result = run_cycle(
                batch.observations,
                instrument_version=version,
                evaluation_cutoff=cycle_cutoff,
                evaluated_at_utc=evaluated_at,
                state=prior,
                revision_ledger=ledger,
                publisher=publisher,
                source_version=KRAKEN_OHLC_SOURCE_LABEL,
                window_start=window_start,
                source_coverage=batch.coverage,
            )
        except CycleIdentityMismatch as exc:
            summary.rejected_identity += 1
            summary.errors.append(f"{version_id}: identity mismatch: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001 - one instrument must not stop others
            summary.errors.append(
                f"{version_id}: capture failed: {type(exc).__name__}: {exc}"
            )
            continue
        summary.cycles += 1
        committed_this_phase += 1
        if result.promoted:
            summary.promoted += 1
        else:
            summary.deferred += 1

        # The ABSOLUTE writer deadline is the final enforceable wall-clock bound.
        # If it cut a submit inside this cycle, that submit never started: the
        # acquired evidence is uncommitted, which is an explicit durable
        # disposition -- never a silent partial write and never done=OK. Detect it
        # from the pipeline's OWN outcome so a fully committed cycle that merely
        # ends at the deadline is not mislabelled incomplete.
        if any(
            str(getattr(outcome, "error_code", "")) == WRITER_DEADLINE_ERROR_NAME
            for outcome in result.outcomes
        ):
            summary.materialize_incomplete = True
            summary.errors.append(
                f"{version_id}: materialization incomplete: the Phase-B absolute "
                f"deadline cut a canonical submit"
            )
            emit_capture_marker(
                "materialize_incomplete",
                where="materialize",
                reason="MATERIALIZE_DEADLINE_EXHAUSTED",
                instrument=version_id,
                remaining_seconds=round(materialize_deadline - tick(), 3),
                submit_bound=submit_bound,
                committed=committed_this_phase,
                pending=pending_commits,
            )
            break

    summary.publish_counts = publisher.summary()
    summary.elapsed_seconds = tick() - started
    if summary.cycles == 0:
        # Explicit, durable zero-materialization disposition: an empty evidence
        # minute must never be indistinguishable from a silent drop.
        emit_capture_marker(
            "zero_snapshots",
            where="materialize",
            fetched=summary.fetched,
            source_errors=summary.source_errors,
            budget_exhausted=summary.budget_exhausted,
        )
    emit_capture_marker(
        "done",
        status=(
            # An incomplete materialization is NEVER reported as OK: acquired
            # evidence that could not be committed is an explicit disposition.
            "MATERIALIZE_INCOMPLETE"
            if summary.materialize_incomplete
            else "DEADLINE_EXHAUSTED"
            if summary.cycles == 0 and summary.budget_exhausted
            else "NO_SNAPSHOTS"
            if summary.cycles == 0
            else "OK"
        ),
        materialize_incomplete=summary.materialize_incomplete,
        fetched=summary.fetched,
        cycles=summary.cycles,
        promoted=summary.promoted,
        deferred=summary.deferred,
        source_errors=summary.source_errors,
        budget_exhausted=summary.budget_exhausted,
        elapsed_seconds=round(summary.elapsed_seconds, 3),
    )
    return summary


# ---------------------------------------------------------------------------
# Process-level non-overlap guard
# ---------------------------------------------------------------------------

#: Stable advisory-lock path INSIDE the container. The capture runs via
#: ``docker compose exec``, so a host-side ``flock`` alone cannot prove the
#: in-container Python process is gone: killing the host Docker client does not
#: reliably terminate the process created inside the already-running container.
#: This file is therefore locked by the capture process itself, which guarantees
#: the invariant directly: at most one ``capture_feature_bus_shadow`` process
#: executes inside the container at any instant. Overridable for tests.
#: The Feature Bus producer's process-level lock IDENTITY (path). Named for its
#: producer so the two evidence producers' lock identities are explicit and
#: provably distinct, mirroring ``FEASIBILITY_CAPTURE_LOCK_PATH`` in the
#: feasibility producer. Overridable for tests.
FEATURE_BUS_CAPTURE_LOCK_PATH = "/tmp/opip-feature-bus-capture.lock"

#: Backwards-compatible alias for the Feature Bus lock identity. The canonical
#: name is :data:`FEATURE_BUS_CAPTURE_LOCK_PATH`; this alias remains so existing
#: importers keep working. Do not introduce a second identity here.
DEFAULT_PROCESS_LOCK_PATH = FEATURE_BUS_CAPTURE_LOCK_PATH


def _try_lock_fd(fd: int) -> bool:
    """Acquire a non-blocking exclusive OS advisory lock on ``fd``."""
    try:
        import fcntl  # POSIX

        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True
    except ImportError:
        pass
    try:
        import msvcrt  # Windows

        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    except ImportError as exc:  # pragma: no cover - no known platform lacks both
        raise RuntimeError("no advisory file-lock primitive is available") from exc


def _unlock_fd(fd: int) -> None:
    try:
        import fcntl

        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    except ImportError:
        pass
    import msvcrt

    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class CaptureProcessLock:
    """Process-level advisory lock guaranteeing at most one capture body runs.

    Held by the capture process itself, so it survives an unexpected host Docker
    client death: a surviving in-container capture keeps the lock and the next
    invocation is refused rather than starting a second overlapping producer. The
    lock is released automatically by the OS when the holding process dies, and
    explicitly by :meth:`release`.
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._fd: int | None = None
        self.acquired = False

    def acquire(self) -> bool:
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            # Some platforms (Windows msvcrt) require the locked region to exist.
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
        except OSError:
            pass
        if not _try_lock_fd(fd):
            os.close(fd)
            return False
        self._fd = fd
        self.acquired = True
        return True

    def release(self) -> None:
        fd = self._fd
        if fd is None:
            return
        self._fd = None
        self.acquired = False
        try:
            _unlock_fd(fd)
        finally:
            os.close(fd)

    def __enter__(self) -> "CaptureProcessLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def run_capture_locked(
    *,
    lock_path: str | None = None,
    capture_fn: Callable[[], FeatureBusCaptureSummary] | None = None,
    lock_env: str = "OPIP_FEATURE_BUS_CAPTURE_LOCK",
    lock_default: str = FEATURE_BUS_CAPTURE_LOCK_PATH,
    marker_prefix: str = "OPIP_FEATURE_BUS_CAPTURE",
) -> dict[str, Any]:
    """Run one capture pass guarded by the process-level non-overlap lock.

    If the lock is already held the invocation does NOT run capture; it returns an
    explicit ``SKIPPED_LOCK_HELD`` disposition so a busy skip is always observable
    rather than a silent exit.

    ``lock_env``/``lock_default`` select the lock IDENTITY. Different producers must
    pass a distinct identity (for example the feasibility producer passes
    ``OPIP_FEASIBILITY_CAPTURE_LOCK`` and its own default path) so one producer's
    lock can never suppress another's: the locking IMPLEMENTATION is shared, the
    identity is not. ``marker_prefix`` selects the durable disposition marker's
    producer identity for the same reason.
    """
    path = lock_path or os.getenv(lock_env, lock_default)
    lock = CaptureProcessLock(path)
    if not lock.acquire():
        emit_capture_marker(
            "skipped_lock_held", prefix=marker_prefix, lock_path=path
        )
        return {"status": "SKIPPED_LOCK_HELD", "lock_path": path}
    try:
        fn = capture_fn or capture_feature_bus_shadow
        summary = fn()
    finally:
        lock.release()
    return {"status": "RAN", "summary": summary.to_dict()}


def main() -> None:
    line_buffered_stdout()
    result = run_capture_locked()
    print("O'Pip Feature Bus SHADOW capture — EVIDENCE ONLY")
    print("Trading authority: NONE")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "CAPTURE_MATERIALIZE_MIN_WRITE_SECONDS",
    "CAPTURE_MATERIALIZE_RESERVE_SECONDS",
    "CAPTURE_MATERIALIZE_WRITE_BOUND_SECONDS",
    "CaptureProcessLock",
    "DEFAULT_BUDGET_SECONDS",
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PROCESS_LOCK_PATH",
    "FEATURE_BUS_CAPTURE_LOCK_PATH",
    "FeatureBusCaptureSummary",
    "MAX_BUDGET_SECONDS",
    "MAX_CAPTURE_LIMIT",
    "MAX_CONCURRENCY",
    "capture_feature_bus_shadow",
    "emit_capture_marker",
    "line_buffered_stdout",
    "main",
    "run_capture_locked",
    "shadow_capture_authorized",
]
