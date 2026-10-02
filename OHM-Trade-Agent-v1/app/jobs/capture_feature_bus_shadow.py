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
protection. Two independent bounds contain it:

* an internal total wall-clock budget (``budget_seconds``): before each
  instrument the loop proves enough budget remains for the next bounded request
  and stops requesting more when it does not; and
* a cron ``timeout`` subprocess bound (final containment only).

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

#: Default total wall-clock budget for one pass. Chosen well below the cron
#: cadence and the outer ``timeout`` so the internal budget is the graceful stop
#: and the subprocess timeout is only final containment.
DEFAULT_BUDGET_SECONDS = 180.0

#: Conservative per-request reservation. The Kraken client uses a 15s request
#: timeout, so a bounded request plus slack cannot exceed this; the loop refuses
#: to start a request it cannot plausibly finish within the remaining budget.
PER_REQUEST_BUDGET_SECONDS = 20.0


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
    bounded = min(max(1, resolved_limit), MAX_CAPTURE_LIMIT)
    budget = max(PER_REQUEST_BUDGET_SECONDS, resolved_budget)
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

    for version in committed_versions:
        remaining = deadline - tick()
        if remaining < PER_REQUEST_BUDGET_SECONDS:
            # Not enough budget for one more bounded request: stop cleanly and say so.
            summary.budget_exhausted = True
            summary.errors.append(
                "budget exhausted before fetching all instruments"
            )
            break
        version_id = version.instrument_version_id
        try:
            batch = source.fetch_through(
                version, watermark=source_watermarks.get(version_id), now=cycle_cutoff
            )
        except Exception as exc:  # noqa: BLE001 - one instrument must not stop others
            summary.source_errors += 1
            summary.errors.append(
                f"{version_id}: fetch failed: {type(exc).__name__}: {exc}"
            )
            continue
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
        # Decision availability is the completed fetch time, never the pre-fetch
        # start: live observation receipt/visibility occurs after network completion.
        evaluated_at = max(now_utc(), cycle_cutoff)
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


def main() -> None:
    summary = capture_feature_bus_shadow()
    print("O'Pip Feature Bus SHADOW capture — EVIDENCE ONLY")
    print("Trading authority: NONE")
    print(json.dumps(summary.to_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
