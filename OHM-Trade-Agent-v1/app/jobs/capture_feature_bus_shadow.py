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
  60-second slot: before each instrument the loop proves enough budget remains for
  the next bounded request and stops requesting more when it does not; and
* a cron ``timeout`` subprocess bound (final containment only), also below 60s.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable

from app.opip.canonical.bridge import resolve_writer_mode
from app.opip.features.pipeline import CycleIdentityMismatch, run_cycle
from app.opip.features.publisher import (
    FeatureBusPublisher,
    resolve_feature_bus_mode,
)
from app.opip.market.aggregates import grid_floor
from app.opip.market.instrument_version_store import hydrate_instrument_version_registry
from app.services.opip_feature_bus_market_source import (
    KRAKEN_OHLC_SOURCE_LABEL,
    KrakenInstrumentProvider,
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
#: Kraken client request timeout is 15s, so a bounded single attempt plus slack
#: cannot exceed this; the pass starts a wave only when at least this much budget
#: remains. The shared transport may retry a genuinely failing request and the
#: cron timeout (below the cadence) is the final containment, so wave gating
#: bounds the worst-case overrun past the internal deadline to a single wave.
PER_REQUEST_BUDGET_SECONDS = 15.0

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
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "publish_counts": dict(self.publish_counts),
            "errors": list(self.errors[:8]),
        }


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
    moment = now or datetime.now(timezone.utc)
    now_utc = wall_clock or (lambda: datetime.now(timezone.utc))
    cycle_cutoff = grid_floor(moment)
    publisher = publisher or FeatureBusPublisher(settings=settings)

    if restore_continuity is None:
        from app.jobs.run_feature_bus_pilot import restore_pilot_continuity

        restore_continuity = restore_pilot_continuity
    if instrument_provider is None:
        registry = hydrate_instrument_version_registry()
        instrument_provider = KrakenInstrumentProvider(registry=registry)
    if source is None:
        source = kraken_minute_source()

    summary = FeatureBusCaptureSummary(mode=mode, enabled=True, inert=False)
    try:
        universe = instrument_provider.refresh(observed_at_utc=moment)
    except Exception as exc:  # noqa: BLE001 - an unavailable source must not fabricate
        summary.errors.append(f"instrument refresh failed: {type(exc).__name__}: {exc}")
        summary.elapsed_seconds = tick() - started
        return summary

    selected = list(universe[:bounded])
    summary.instruments = len(selected)

    committed_versions = []
    for version in selected:
        outcome = publisher.publish_instrument_version(version)
        if outcome.committed:
            committed_versions.append(version)
        else:
            summary.errors.append(
                f"instrument version not committed: {outcome.status} {outcome.error_code}"
            )
    summary.committed_instrument_versions = len(committed_versions)

    if not committed_versions:
        summary.errors.append("no instrument version committed; nothing captured")
        summary.publish_counts = publisher.summary()
        summary.elapsed_seconds = tick() - started
        return summary

    restored_states, restored_ledgers, source_watermarks = restore_continuity(
        committed_versions
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

    wave_index = 0
    while wave_index < len(committed_versions):
        if deadline - tick() < PER_REQUEST_BUDGET_SECONDS:
            # Not enough budget for one more bounded wave: stop cleanly and say so.
            summary.budget_exhausted = True
            summary.errors.append(
                "budget exhausted before fetching all instruments"
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

    # Phase B - sequential materialization. The canonical writer has a single
    # connection, so dependent writes commit in instrument order, never
    # concurrently: acquisition is bounded and parallel, evidence stays serial.
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
        if result.promoted:
            summary.promoted += 1
        else:
            summary.deferred += 1

    summary.publish_counts = publisher.summary()
    summary.elapsed_seconds = tick() - started
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
DEFAULT_PROCESS_LOCK_PATH = "/tmp/opip-feature-bus-capture.lock"


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
) -> dict[str, Any]:
    """Run one capture pass guarded by the process-level non-overlap lock.

    If the lock is already held the invocation does NOT run capture; it returns an
    explicit ``SKIPPED_LOCK_HELD`` disposition so a busy skip is always observable
    rather than a silent exit.
    """
    path = (
        lock_path
        or os.getenv("OPIP_FEATURE_BUS_CAPTURE_LOCK", DEFAULT_PROCESS_LOCK_PATH)
    )
    lock = CaptureProcessLock(path)
    if not lock.acquire():
        return {"status": "SKIPPED_LOCK_HELD", "lock_path": path}
    try:
        fn = capture_fn or capture_feature_bus_shadow
        summary = fn()
    finally:
        lock.release()
    return {"status": "RAN", "summary": summary.to_dict()}


def main() -> None:
    result = run_capture_locked()
    print("O'Pip Feature Bus SHADOW capture — EVIDENCE ONLY")
    print("Trading authority: NONE")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()


__all__ = [
    "CaptureProcessLock",
    "DEFAULT_BUDGET_SECONDS",
    "DEFAULT_CAPTURE_LIMIT",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_PROCESS_LOCK_PATH",
    "FeatureBusCaptureSummary",
    "MAX_BUDGET_SECONDS",
    "MAX_CAPTURE_LIMIT",
    "MAX_CONCURRENCY",
    "capture_feature_bus_shadow",
    "main",
    "run_capture_locked",
    "shadow_capture_authorized",
]
