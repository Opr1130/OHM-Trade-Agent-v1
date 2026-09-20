"""B/C-3 Increment 6B Paper-v2 scan router (default OFF).

This is the single seam that turns an already-qualified ranked opportunity into a
``PaperV2Opportunity`` and calls the frozen producer. It exists so the scan
orchestrator stays thin: no qualification, ranking, action-gate or target logic
lives here, and nothing here can widen authority.

Authority split
---------------

Paper v2 is selected by mode, never by availability. When the mode is inactive the
scan keeps calling the existing Freqtrade dry-run and legacy Paper-v1 paths and
this module is not used at all. When the mode is active this router is the only
paper authority: the scan does not call either legacy path, and a Paper-v2 failure
never falls back to them. There is no dual admission and no dual execution.

What this router derives, and from where
----------------------------------------

* **Policy identity** comes from one immutable scan-level stamp captured when the
  opportunity set was qualified. It is never recomputed per opportunity or at
  execution time, so a context describes the policy that actually qualified it
  even if the live policy changes later.
* **Decision snapshots** are built once per scan from the already-observed cohort
  using the native opportunity-scan semantics and mapped by symbol. No market
  rescan, no P1 JSONL outbox, no Feature Bus.
* **Candidate identity** is the funnel's canonical ``OPIPC:`` id for the exact
  symbol and direction - never the signal/alert identity.
* **Instrument identity** comes from the exact Kraken ``AssetPairs`` metadata this
  scan already fetched, reconstructed through canonical instrument-version history
  so a restarted deployment does not restart version numbers at 1.
* **Execution symbol** is the metadata-derived ``venue_instrument_id``, used
  verbatim as the pre-trade and quote-evidence symbol. The two are the same string
  by construction; no alias is converted after identity is set.
* **Capital and quantity** come from the post-action-gate result and the validated
  snapshot's own reference price, not from an earlier economic envelope and not
  from a later quote.

Out of scope by construction: no pending-entry state machine, so a LONG that is
not immediately actionable is a WAIT rather than an immediate BUY; and no short
engine, so a SHORT is refused rather than mapped onto a BUY.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import math
from typing import Any, Callable, Iterable, Mapping

from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.temporal import require_utc
from app.opip.market.instrument_version_store import (
    hydrate_instrument_version_registry,
)
from app.opip.market.instruments import InstrumentVersionRegistry, kraken_descriptor
from app.services.canonical_episode_capture import build_canonical_episode_snapshots
from app.services.paper_v2_pretrade_adapter import system_utc_clock
from app.services.paper_v2_execution import (
    OPPORTUNITY_DIRECTION_LONG,
    PaperV2ExecutionError,
    PaperV2Opportunity,
    run_paper_v2_opportunity,
)

#: The scan source the native production scan cohort uses. Reusing it keeps the
#: decision snapshot semantics identical to the existing native capture.
PAPER_V2_SCAN_SOURCE = "LIVE_OPPORTUNITY_SCAN"

#: The only direction this slice can execute. Kept as its own name so the
#: router's boundary is legible without importing producer internals.
SUPPORTED_DIRECTION = OPPORTUNITY_DIRECTION_LONG

#: The candidate domain the funnel mints. A candidate id outside it is not the
#: qualified candidate identity.
CANDIDATE_ID_PREFIX = "OPIPC:"

#: Tolerance for the long-spot leverage-consistency check. The action gate rounds
#: the notional to cents, so a matched 1x position may differ by less than a cent.
_NOTIONAL_CONSISTENCY_TOLERANCE = 0.011


class PaperV2HandoffError(RuntimeError):
    """The handoff could not be proven, so this opportunity must not be routed."""


@dataclass(frozen=True)
class PaperV2QualificationStamp:
    """One immutable scan-level Paper-v2 qualification stamp.

    Captured once, immediately after the opportunity set is qualified. The policy
    identity is read here and nowhere else on this path, so every context in the
    scan describes the same qualification policy.
    """

    qualification_time: datetime
    policy_version: str
    policy_fingerprint: str


def capture_qualification_stamp(*, qualification_time: datetime) -> PaperV2QualificationStamp:
    """Capture the qualification-time policy identity exactly once per scan."""
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    return PaperV2QualificationStamp(
        qualification_time=require_utc(
            qualification_time, field_name="qualification_time"
        ),
        policy_version=GATE_POLICY_VERSION,
        policy_fingerprint=gate_policy_fingerprint(),
    )


@dataclass(frozen=True)
class PaperV2ScanFacts:
    """Already-observed scan facts. The router never re-derives these."""

    snapshots: tuple[Any, ...]
    decision_at: datetime
    #: ``scan.universe.assets``. Absent universe metadata is a handoff failure for
    #: every opportunity rather than a reason to re-request ``AssetPairs``.
    universe_assets: tuple[Any, ...] = ()


@dataclass
class PaperV2RouterSummary:
    """Operator-observable outcome of one scan's Paper-v2 routing.

    Deliberately a set of counts plus plain detail strings, not a score: the scan
    prints it, and no downstream consumer may treat it as an authority signal.
    """

    executed: int = 0
    capital_rejected: int = 0
    capacity_rejected: int = 0
    short_unsupported: int = 0
    wait_not_executable: int = 0
    handoff_failures: int = 0
    operational_failures: int = 0
    #: Trades that ended in a provably pre-exposure no-fill terminal state, so their
    #: reservation was released. Distinct from an operational failure, which leaves
    #: the trade unresolved.
    no_fill_terminal: int = 0
    details: list[str] = field(default_factory=list)

    @property
    def legacy_calls(self) -> int:
        """Always zero: the active route never touches a legacy paper authority."""
        return 0

    def record(self, message: str) -> None:
        self.details.append(message)


# Test/extension seams, mirroring app.services.paper_outcome_outbox.
_test_client: Any = None
_test_kraken_client: Any = None


def set_writer_client_for_tests(client: Any) -> None:
    global _test_client
    _test_client = client


def set_kraken_client_for_tests(client: Any) -> None:
    global _test_kraken_client
    _test_kraken_client = client


def _writer_client() -> Any:
    if _test_client is not None:
        return _test_client
    from app.opip.canonical.client import CanonicalWriterClient

    return CanonicalWriterClient()


def _kraken_client() -> Any:
    if _test_kraken_client is not None:
        return _test_kraken_client
    from app.exchanges.kraken import KrakenClient

    return KrakenClient()


def _build_cohort_snapshots(scan_facts: PaperV2ScanFacts) -> dict[str, dict]:
    """Build the decision-snapshot cohort once, mapped by symbol."""
    rows = build_canonical_episode_snapshots(
        scan_facts.snapshots,
        candidates=(),
        decision_at=scan_facts.decision_at,
        signal_quality_enabled=False,
        scan_source=PAPER_V2_SCAN_SOURCE,
    )
    by_symbol: dict[str, dict] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol in by_symbol:
            raise PaperV2HandoffError(
                f"duplicate canonical snapshot for symbol {symbol}"
            )
        by_symbol[symbol] = row
    return by_symbol


def _universe_by_pair(scan_facts: PaperV2ScanFacts) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for asset in scan_facts.universe_assets:
        pair = str(getattr(asset, "primary_pair", "") or "")
        if pair:
            mapping[pair] = asset
    return mapping


def _instrument_version(
    asset: Any,
    *,
    registry: InstrumentVersionRegistry,
    observed_at_utc: datetime,
) -> InstrumentVersion:
    """Recreate the canonical instrument version from exact observed metadata."""
    pair_id = str(getattr(asset, "primary_pair_id", "") or "")
    details = getattr(asset, "primary_pair_details", None)
    if not pair_id or not isinstance(details, Mapping):
        raise PaperV2HandoffError(
            "exact Kraken pair metadata is unavailable for this instrument"
        )
    descriptor = kraken_descriptor(pair_id, details)
    if descriptor is None:
        raise PaperV2HandoffError(
            f"Kraken pair metadata for {pair_id!r} is not a usable descriptor"
        )
    # Observing against the hydrated registry yields the correct current version
    # rather than restarting at 1, and is idempotent when the reference data has
    # not changed.
    return registry.observe(descriptor, observed_at_utc=observed_at_utc)


def _positive_finite(value: object, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaperV2HandoffError(f"{field_name} is not a finite positive number")
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PaperV2HandoffError(f"{field_name} is not a finite positive number")
    return number


def _route_one(
    ranked: Any,
    *,
    scan_facts: PaperV2ScanFacts,
    stamp: PaperV2QualificationStamp,
    summary: PaperV2RouterSummary,
    snapshots_by_symbol: Mapping[str, dict],
    universe_by_pair: Mapping[str, Any],
    registry: InstrumentVersionRegistry,
    client: Any,
    kraken_client: Any,
    settings: Any,
    opip: Any,
    execution_clock: Callable[[], datetime],
) -> None:
    opportunity = ranked.opportunity
    snapshot = opportunity.snapshot
    alert = opportunity.alert
    plan = opportunity.plan
    symbol = str(snapshot.symbol)
    direction = str(getattr(snapshot, "trade_direction", "") or "LONG").upper()

    if direction != SUPPORTED_DIRECTION:
        # No short engine exists in this slice. Recorded explicitly; never mapped
        # onto a BUY, and never routed to a legacy paper authority.
        summary.short_unsupported += 1
        summary.record(f"SHORT_UNSUPPORTED {symbol}")
        return

    if not bool(plan.valid_now):
        # A wait decision is not an immediate entry. The producer has no pending
        # state machine, so executing here would silently convert WAIT into a BUY.
        summary.wait_not_executable += 1
        summary.record(f"WAIT_NOT_EXECUTABLE {symbol}")
        return

    # --- canonical candidate identity --------------------------------------
    if opip is None or getattr(opip, "funnel", None) is None:
        raise PaperV2HandoffError("qualification funnel is unavailable")
    state = opip.funnel.get(symbol, direction)
    if state is None:
        raise PaperV2HandoffError(f"no funnel candidate state for {symbol} {direction}")
    candidate_id = str(getattr(state, "candidate_id", "") or "")
    if not candidate_id.startswith(CANDIDATE_ID_PREFIX):
        raise PaperV2HandoffError(
            f"funnel candidate id for {symbol} is not a canonical {CANDIDATE_ID_PREFIX} id"
        )

    # --- decision snapshot --------------------------------------------------
    snapshot_payload = snapshots_by_symbol.get(symbol)
    if snapshot_payload is None:
        raise PaperV2HandoffError(f"no canonical decision snapshot for {symbol}")
    if str(snapshot_payload.get("episode_id")) != str(getattr(state, "episode_id", "")):
        raise PaperV2HandoffError(
            f"snapshot episode does not match the funnel candidate episode for {symbol}"
        )

    # --- instrument identity -----------------------------------------------
    asset = universe_by_pair.get(str(getattr(snapshot, "primary_pair", "") or ""))
    if asset is None:
        raise PaperV2HandoffError(f"no universe metadata for {symbol}")
    version = _instrument_version(
        asset, registry=registry, observed_at_utc=stamp.qualification_time
    )
    # Hard ancestry invariant: the execution symbol IS the instrument identity.
    native_symbol = version.venue_instrument_id

    # --- capital and quantity ----------------------------------------------
    requested_capital = _positive_finite(
        alert.get("recommended_capital"), field_name="recommended_capital"
    )
    requested_notional = _positive_finite(
        alert.get("recommended_position_notional"),
        field_name="recommended_position_notional",
    )
    if abs(requested_notional - requested_capital) > _NOTIONAL_CONSISTENCY_TOLERANCE:
        # Long spot is 1x. A disagreement means leverage would be implied, which
        # this slice must not silently apply.
        raise PaperV2HandoffError(
            "recommended position notional and recommended capital disagree for a "
            "long spot trade"
        )
    reference_price = _positive_finite(
        snapshot_payload.get("reference_price"),
        field_name="canonical snapshot reference_price",
    )
    requested_quantity = requested_notional / reference_price

    paper_opportunity = PaperV2Opportunity(
        candidate_id=candidate_id,
        episode_id=str(snapshot_payload["episode_id"]),
        cohort_id=str(snapshot_payload["cohort_id"]),
        direction=direction,
        instrument_version_id=version.instrument_version_id,
        snapshot_payload=snapshot_payload,
        evaluation_time=stamp.qualification_time,
        evidence_cutoff=scan_facts.decision_at,
        # The two durable ancestry proofs are composed into context provenance
        # automatically, so no ref has to be invented here.
        source_record_refs=(),
        qualification_policy_version=stamp.policy_version,
        qualification_policy_fingerprint=stamp.policy_fingerprint,
        instrument_version=version,
        quote_currency=str(snapshot.primary_quote_currency or "USD").upper(),
        requested_capital=requested_capital,
        requested_reservation_amount=requested_capital,
        decision_time=stamp.qualification_time,
        native_symbol=native_symbol,
        requested_quantity=requested_quantity,
        requested_notional=requested_notional,
        # Copied verbatim from the already-approved plan: never recalculated.
        entry_low=float(plan.entry_low),
        entry_high=float(plan.entry_high),
        chase_limit=float(plan.chase_limit),
        stop_price=float(plan.stop_price),
        target_prices=(float(plan.target_1), float(plan.target_2)),
    )

    # Execution runs on the execution clock, not the qualification stamp: quote
    # freshness and occurrence times are execution facts. ``now`` opens the
    # execution and ``clock`` is re-read after the venue response.
    execution_now = require_utc(execution_clock(), field_name="execution_now")
    result = run_paper_v2_opportunity(
        paper_opportunity,
        client=client,
        kraken_client=kraken_client,
        settings=settings,
        now=execution_now,
        clock=execution_clock,
    )
    status = str(getattr(result, "status", "") or "UNKNOWN")
    if status == "EXECUTED":
        summary.executed += 1
    elif status == "CAPITAL_REJECTED":
        summary.capital_rejected += 1
    elif status == "CAPACITY_REJECTED":
        summary.capacity_rejected += 1
    elif status == "NO_FILL_TERMINAL":
        # A provably pre-exposure failure that was closed and released. Distinct
        # from an operational failure: the trade ended safely and its capacity
        # returned to the portfolio.
        summary.no_fill_terminal += 1
    else:
        summary.operational_failures += 1
    summary.record(f"{status} {symbol}: {str(getattr(result, 'detail', '') or '')}")


def route_qualified_opportunities(
    ranked_opportunities: Iterable[Any],
    *,
    scan_facts: PaperV2ScanFacts,
    stamp: PaperV2QualificationStamp,
    settings: Any,
    opip: Any = None,
    client: Any = None,
    kraken_client: Any = None,
    registry: InstrumentVersionRegistry | None = None,
    execution_clock: Callable[[], datetime] | None = None,
) -> PaperV2RouterSummary:
    """Route every eligible opportunity to Paper v2, independently.

    Each opportunity is evaluated in isolation: a handoff or execution failure on
    one produces a visible count and does not stop another independently valid
    opportunity. Nothing here falls back to Freqtrade or legacy Paper v1.
    """
    summary = PaperV2RouterSummary()
    ranked_list = list(ranked_opportunities)
    if not ranked_list:
        return summary

    writer = client if client is not None else _writer_client()
    kraken = kraken_client if kraken_client is not None else _kraken_client()
    clock = execution_clock or system_utc_clock

    try:
        snapshots_by_symbol = _build_cohort_snapshots(scan_facts)
    except Exception as exc:
        # Without the cohort there is no verifiable snapshot ancestry, so every
        # opportunity fails closed rather than executing against unproven
        # evidence.
        summary.handoff_failures += len(ranked_list)
        summary.record(f"DECISION_SNAPSHOT_COHORT_FAILED: {type(exc).__name__}: {exc}")
        return summary

    universe_by_pair = _universe_by_pair(scan_facts)
    if registry is None:
        try:
            registry = hydrate_instrument_version_registry()
        except Exception as exc:
            summary.handoff_failures += len(ranked_list)
            summary.record(
                f"INSTRUMENT_REGISTRY_UNAVAILABLE: {type(exc).__name__}: {exc}"
            )
            return summary

    for ranked in ranked_list:
        try:
            _route_one(
                ranked,
                scan_facts=scan_facts,
                stamp=stamp,
                summary=summary,
                snapshots_by_symbol=snapshots_by_symbol,
                universe_by_pair=universe_by_pair,
                registry=registry,
                client=writer,
                kraken_client=kraken,
                settings=settings,
                opip=opip,
                execution_clock=clock,
            )
        except PaperV2HandoffError as exc:
            summary.handoff_failures += 1
            summary.record(
                f"HANDOFF_FAILURE {ranked.opportunity.snapshot.symbol}: {exc}"
            )
        except PaperV2ExecutionError as exc:
            summary.operational_failures += 1
            summary.record(
                f"PAPER_V2_FAILURE {ranked.opportunity.snapshot.symbol}: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - one opportunity must not stop others
            summary.operational_failures += 1
            summary.record(
                f"PAPER_V2_ERROR {ranked.opportunity.snapshot.symbol}: "
                f"{type(exc).__name__}: {exc}"
            )
    return summary


__all__ = [
    "CANDIDATE_ID_PREFIX",
    "PAPER_V2_SCAN_SOURCE",
    "PaperV2HandoffError",
    "PaperV2QualificationStamp",
    "PaperV2RouterSummary",
    "PaperV2ScanFacts",
    "capture_qualification_stamp",
    "route_qualified_opportunities",
    "set_kraken_client_for_tests",
    "set_writer_client_for_tests",
]
