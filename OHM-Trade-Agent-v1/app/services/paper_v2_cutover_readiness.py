"""Legacy paper drain readiness for the Paper-v2 cutover interlock.

Selecting Paper v2 makes it the sole *new* paper-entry authority, which silences
both legacy new-entry paths. That is only safe once the legacy engines have no
outstanding obligations left to manage: an open legacy position still needs its own
engine to run its exit lifecycle, and a legacy pending entry can still become a
position.

Configuration is deliberately not treated as proof. "The feature flag is off" and
"the control file says disabled" are not evidence that legacy exposure is gone, so
readiness is computed from the legacy subsystems' own authoritative state:

* Freqtrade dry-run - open trades reported by the worker databases, plus bridge
  signals admitted but not yet an open position;
* OHM paper v1 - its own non-terminal lifecycles (pending entries and open
  positions) and the reserved capital they imply.

The result is a status, never a granted authority:

``READY``      no outstanding obligation in either subsystem -> cutover may proceed
``DRAINING``   at least one subsystem still holds an obligation -> blocked, and the
               legacy engine keeps managing what it already owns
``UNAVAILABLE`` the state could not be read -> blocked, because an unreadable
               subsystem is not an empty one
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Cutover readiness states. Only ``READY`` permits new Paper-v2 entries.
DRAIN_READY = "READY"
DRAIN_DRAINING = "DRAINING"
DRAIN_UNAVAILABLE = "UNAVAILABLE"

__all__ = [
    "CanonicalWriterEvidence",
    "CutoverEvidence",
    "CutoverReadinessReport",
    "DRAIN_DRAINING",
    "DRAIN_READY",
    "DRAIN_UNAVAILABLE",
    "DirectionCoverage",
    "EVIDENCE_BLOCKED",
    "EVIDENCE_DRAINING",
    "EVIDENCE_READY",
    "EVIDENCE_UNAVAILABLE",
    "EVIDENCE_UNKNOWN",
    "LegacyDrainStatus",
    "ModeEvidence",
    "PAPER_V2_PENDING_MANDATE",
    "PROTECTION_INDEPENDENT_OF_DISCOVERY",
    "PendingMandate",
    "ProtectionEvidence",
    "READINESS_NOT_READY",
    "READINESS_READY",
    "READINESS_UNAVAILABLE",
    "RollbackEvidence",
    "SelectorHandoff",
    "UniverseGateEvidence",
    "cutover_readiness_report",
    "evaluate_cutover_readiness",
    "evaluate_legacy_drain",
    "observe_canonical_writer_evidence",
    "observe_direction_coverage",
    "observe_mode_evidence",
    "observe_pending_mandate",
    "observe_protection_evidence",
    "observe_rollback_evidence",
    "observe_selector_handoff",
    "observe_universe_gate_evidence",
]


@dataclass(frozen=True)
class LegacyDrainStatus:
    """Whether both legacy paper engines are provably drained.

    Carries the exact counts that drove the decision so an operator sees *why*
    cutover is blocked rather than only that it is.
    """

    status: str
    reason: str
    freqtrade_open_trades: int = 0
    freqtrade_pending_entries: int = 0
    paper_v1_pending_entries: int = 0
    paper_v1_open_positions: int = 0
    paper_v1_unresolved_trades: int = 0
    paper_v1_reserved_capital: float = 0.0
    legacy_control_enabled: bool = False

    @property
    def ready(self) -> bool:
        return self.status == DRAIN_READY

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "reason": self.reason,
            "freqtrade_open_trades": self.freqtrade_open_trades,
            "freqtrade_pending_entries": self.freqtrade_pending_entries,
            "paper_v1_pending_entries": self.paper_v1_pending_entries,
            "paper_v1_open_positions": self.paper_v1_open_positions,
            "paper_v1_unresolved_trades": self.paper_v1_unresolved_trades,
            "paper_v1_reserved_capital": self.paper_v1_reserved_capital,
            "legacy_control_enabled": self.legacy_control_enabled,
        }


def evaluate_legacy_drain(*, starting_equity: float) -> LegacyDrainStatus:
    """Compute cutover readiness from the legacy subsystems' own state.

    Fails closed: any read failure or malformed state yields ``UNAVAILABLE``, which
    blocks the cutover. It never raises, so a caller can always report a status
    instead of accidentally continuing on an exception.
    """
    try:
        from app.services.freqtrade_result_ingest import freqtrade_dry_run_status
        from app.services.freqtrade_signal_bridge import outstanding_admitted_signals
        from app.services.paper_trade_control import paper_trade_enabled
        from app.services.paper_trade_registry import account_summary

        control_enabled = bool(paper_trade_enabled())

        freqtrade = freqtrade_dry_run_status()
        if str(freqtrade.get("status") or "") != "OK":
            return LegacyDrainStatus(
                status=DRAIN_UNAVAILABLE,
                reason=(
                    "Freqtrade dry-run status is "
                    f"{freqtrade.get('status') or 'UNKNOWN'}; legacy exposure is "
                    "unproven"
                ),
                legacy_control_enabled=control_enabled,
            )
        open_trades = int(freqtrade.get("open_trades") or 0)
        pending_entries = len(outstanding_admitted_signals())

        summary = account_summary(float(starting_equity))
        v1_pending = int(summary.pending_entries)
        v1_open = int(summary.open_positions)
        # An UNRESOLVED lifecycle is a legacy obligation: its outcome is not proven,
        # so the drain cannot be READY while one exists.
        v1_unresolved = int(getattr(summary, "unresolved_trades", 0) or 0)
        v1_reserved = float(summary.reserved_capital)
    except Exception as exc:  # noqa: BLE001 - unreadable state must block cutover
        return LegacyDrainStatus(
            status=DRAIN_UNAVAILABLE,
            reason=f"legacy paper state is unreadable: {type(exc).__name__}: {exc}",
        )

    outstanding_total = (
        open_trades + pending_entries + v1_pending + v1_open + v1_unresolved
    )
    # A READY drain proves every legacy obligation cleared *and* no legacy capital
    # remains reserved. Retained reserved capital with no counted obligation (for
    # example an unresolved or quarantined lifecycle) is not drained.
    if outstanding_total > 0 or v1_reserved > 1e-9:
        return LegacyDrainStatus(
            status=DRAIN_DRAINING,
            reason=(
                "legacy paper obligations remain: "
                f"freqtrade open={open_trades} pending={pending_entries}, "
                f"paper v1 pending={v1_pending} open={v1_open} "
                f"unresolved={v1_unresolved} reserved_capital={v1_reserved:.8f}"
            ),
            freqtrade_open_trades=open_trades,
            freqtrade_pending_entries=pending_entries,
            paper_v1_pending_entries=v1_pending,
            paper_v1_open_positions=v1_open,
            paper_v1_unresolved_trades=v1_unresolved,
            paper_v1_reserved_capital=v1_reserved,
            legacy_control_enabled=control_enabled,
        )

    return LegacyDrainStatus(
        status=DRAIN_READY,
        reason="no outstanding legacy paper obligation and no reserved legacy capital",
        paper_v1_unresolved_trades=v1_unresolved,
        paper_v1_reserved_capital=v1_reserved,
        legacy_control_enabled=control_enabled,
    )


# ===========================================================================
# R4-A — bounded live evidence and the aggregate cutover-readiness verdict
# ===========================================================================
#
# This section answers a different question from ``evaluate_legacy_drain``. The
# drain evaluator answers "may the cutover proceed?"; this section answers
# "what does the readiness evidence show, and which independently verifiable
# technical gates are still open?".
#
# READINESS IS NOT ACTIVATION. A ``READY_FOR_OWNER_ACTIVATION`` verdict means every
# *technical* gate this increment can prove is satisfied. It never selects Paper v2,
# never sets ``OPIP_PAPER_V2_MODE``, and never wires the selector as the admission
# source - those are explicit owner actions in a later increment. Activation
# prerequisites that require an owner decision (mode, selector admission authority)
# are reported as evidence and are deliberately NOT counted as blockers, so the
# verdict is meaningful before activation and remains ``NOT_READY`` only for genuine
# technical gaps.
#
# Every observation is bounded and typed. The probe never dumps the environment,
# never reads arbitrary configuration, and never prints a secret; it reports only
# the specific typed facts the cutover gates name, and a fact it cannot read fails
# closed as ``UNAVAILABLE`` rather than defaulting to a favorable value.

# --- live-evidence states ---------------------------------------------------
EVIDENCE_READY = "READY"
EVIDENCE_DRAINING = "DRAINING"
EVIDENCE_UNAVAILABLE = "UNAVAILABLE"
EVIDENCE_BLOCKED = "BLOCKED"
EVIDENCE_UNKNOWN = "UNKNOWN"

# --- overall readiness verdicts --------------------------------------------
READINESS_READY = "READY_FOR_OWNER_ACTIVATION"
READINESS_NOT_READY = "NOT_READY"
READINESS_UNAVAILABLE = "UNAVAILABLE"

# --- machine-readable reason codes -----------------------------------------
REASON_MODE_UNAVAILABLE = "PAPER_V2_MODE_EVIDENCE_UNAVAILABLE"
REASON_LEGACY_DRAIN_DRAINING = "LEGACY_DRAIN_DRAINING"
REASON_LEGACY_DRAIN_UNAVAILABLE = "LEGACY_DRAIN_UNAVAILABLE"
REASON_PROTECTION_UNAVAILABLE = "PROTECTION_EVIDENCE_UNAVAILABLE"
REASON_PROTECTION_UNSAFE = "PROTECTION_UNSAFE"
REASON_PROTECTION_NOT_INDEPENDENT = "PROTECTION_DEPENDS_ON_DISCOVERY"
REASON_UNIVERSE_GATE_ABSENT = "UNIVERSE_METADATA_GATE_ABSENT"
REASON_UNIVERSE_NOT_OBSERVED = "UNIVERSE_METADATA_NOT_OBSERVED"
REASON_LONG_AUTHORITY_MISSING = "LONG_AUTHORITY_MISSING"
REASON_SHORT_AUTHORITY_MISSING = "SHORT_AUTHORITY_MISSING"
REASON_PENDING_MANDATE_UNKNOWN = "PENDING_MANDATE_UNKNOWN"
REASON_SELECTOR_HANDOFF_INCOMPLETE = "SELECTOR_HANDOFF_INCOMPLETE"
REASON_SELECTOR_HANDOFF_UNAVAILABLE = "SELECTOR_HANDOFF_UNAVAILABLE"
REASON_CANONICAL_WRITER_UNAVAILABLE = "CANONICAL_WRITER_UNAVAILABLE"
REASON_ROLLBACK_UNAVAILABLE = "ROLLBACK_PATH_UNAVAILABLE"

#: The current Paper-v2 paper mandate. The frozen paper-v2 contract supports only
#: immediately-executable entries; a LONG that is not immediately actionable is a
#: WAIT, and there is deliberately no pending-entry state machine. This is a
#: documented contract fact (see ``app/services/paper_v2_scan_router`` and the F8
#: conformance row), so readiness reports it rather than inventing a pending engine.
PAPER_V2_PENDING_MANDATE = "IMMEDIATE_ONLY_NO_PENDING_STATE_MACHINE_V1"

#: Paper-v2 protection is wired into the unified cycle's protection phase by this
#: increment, so it no longer depends on opportunity discovery. The wiring is
#: proven by test (``run_cycle`` invokes the sweep before discovery); this constant
#: is the readiness report's record of that wiring, not a substitute for the proof.
PROTECTION_INDEPENDENT_OF_DISCOVERY = True

#: Paper-v2 mints ``OPIPC:`` candidate identities through the qualification funnel,
#: while the F7 selector mints ``PCAND:`` identities. The candidate-identity bridge
#: between them is a required R4-B handoff and is intentionally not implemented here.
REQUIRED_SELECTOR_CANDIDATE_BRIDGE = "PCAND_TO_OPIPC"


@dataclass(frozen=True)
class ModeEvidence:
    """The observed Paper-v2 activation mode, or that it could not be observed."""

    status: str
    mode: str | None = None
    reason_code: str | None = None

    @property
    def active(self) -> bool:
        return self.mode == "active"


@dataclass(frozen=True)
class ProtectionEvidence:
    """Paper-v2 protection independence and health.

    ``independent_of_discovery`` records the wiring fact; ``status`` records whether
    the protection projection is readable and healthy. An unreadable projection is
    ``UNAVAILABLE`` and never reads as "nothing to protect".
    """

    status: str
    independent_of_discovery: bool
    open_exposure_count: int = 0
    reason_code: str | None = None


@dataclass(frozen=True)
class UniverseGateEvidence:
    """Whether the universe-metadata admission gate is present and fail-closed.

    Universe metadata is a per-scan condition, not durable state, so readiness
    proves the gate is *enforced fail-closed* rather than observing one scan's
    metadata. ``observed`` reports an explicit observation when a caller has one.
    """

    status: str
    enforced_fail_closed: bool
    observed: bool = False
    observed_asset_count: int = 0
    reason_code: str | None = None


@dataclass(frozen=True)
class DirectionCoverage:
    """Which directions the active Paper-v2 slice can authoritatively paper."""

    long_covered: bool
    short_covered: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class PendingMandate:
    """Whether the current paper mandate requires a pending-limit lifecycle."""

    status: str
    immediate_only: bool
    requires_pending_lifecycle: bool
    reason_code: str | None = None


@dataclass(frozen=True)
class SelectorHandoff:
    """The F7 -> Paper-v2 handoff contract readiness."""

    status: str
    fields_compatible: bool
    admission_source_active: bool
    missing_fields: tuple[str, ...] = ()
    reason_code: str | None = None


@dataclass(frozen=True)
class CanonicalWriterEvidence:
    """Canonical writer / ledger health, observed through a bounded read."""

    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class RollbackEvidence:
    """Whether the legacy authority remains available as a rollback."""

    status: str
    reason_code: str | None = None


@dataclass(frozen=True)
class CutoverEvidence:
    """The complete, typed evidence bundle the verdict is derived from."""

    mode: ModeEvidence
    drain: LegacyDrainStatus
    protection: ProtectionEvidence
    universe: UniverseGateEvidence
    direction: DirectionCoverage
    pending: PendingMandate
    selector: SelectorHandoff
    canonical_writer: CanonicalWriterEvidence
    rollback: RollbackEvidence

    def to_dict(self) -> dict:
        return {
            "mode": _as_dict(self.mode),
            "drain": _drain_evidence(self.drain),
            "protection": _as_dict(self.protection),
            "universe": _as_dict(self.universe),
            "direction": _as_dict(self.direction),
            "pending": _as_dict(self.pending),
            "selector": _as_dict(self.selector),
            "canonical_writer": _as_dict(self.canonical_writer),
            "rollback": _as_dict(self.rollback),
        }


@dataclass(frozen=True)
class CutoverReadinessReport:
    """The deterministic aggregate readiness verdict. Never a granted authority."""

    overall: str
    reason_codes: tuple[str, ...]
    activation_prerequisites: tuple[str, ...]
    evidence: CutoverEvidence

    @property
    def ready(self) -> bool:
        return self.overall == READINESS_READY

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "reason_codes": list(self.reason_codes),
            "activation_prerequisites": list(self.activation_prerequisites),
            "evidence": self.evidence.to_dict(),
        }


def _as_dict(record: Any) -> dict:
    return {
        field_name: getattr(record, field_name)
        for field_name in record.__dataclass_fields__
    }


#: Fixed, status-derived drain reason codes for the report. The drain evaluator's own
#: ``reason`` is deliberately excluded from report serialization because it can embed
#: raw exception text (for example a failed numeric conversion quoting a persisted
#: value), and the report job prints its output.
_DRAIN_REASON_CODES = {
    DRAIN_READY: "LEGACY_DRAIN_READY",
    DRAIN_DRAINING: "LEGACY_DRAIN_DRAINING",
    DRAIN_UNAVAILABLE: "LEGACY_DRAIN_UNAVAILABLE",
}


def _drain_evidence(drain: LegacyDrainStatus) -> dict:
    """A bounded drain view: status-derived code and counts, never free-form text."""
    return {
        "status": drain.status,
        "reason_code": _DRAIN_REASON_CODES.get(drain.status, "LEGACY_DRAIN_UNKNOWN"),
        "freqtrade_open_trades": drain.freqtrade_open_trades,
        "freqtrade_pending_entries": drain.freqtrade_pending_entries,
        "paper_v1_pending_entries": drain.paper_v1_pending_entries,
        "paper_v1_open_positions": drain.paper_v1_open_positions,
        "paper_v1_unresolved_trades": drain.paper_v1_unresolved_trades,
        "paper_v1_reserved_capital": drain.paper_v1_reserved_capital,
        "legacy_control_enabled": drain.legacy_control_enabled,
    }


def observe_mode_evidence(settings: Any | None = None) -> ModeEvidence:
    """Observe the effective Paper-v2 mode, distinguishing a value from a default.

    A value is evidence only when it is read from an explicit settings source. If no
    source can be read, the result is ``UNAVAILABLE`` rather than the module default:
    a default is not live evidence, and the cutover must not select an engine from a
    default.
    """
    from app.services.paper_v2_activation import (
        PAPER_V2_MODES,
        resolve_paper_v2_mode,
    )

    try:
        source = settings if settings is not None else _process_settings()
        if source is None:
            return ModeEvidence(
                status=EVIDENCE_UNAVAILABLE, reason_code=REASON_MODE_UNAVAILABLE
            )
        raw = getattr(source, "opip_paper_v2_mode", None)
        if not isinstance(raw, str) or raw not in PAPER_V2_MODES:
            # Unknown or malformed is not the same as the canonical default.
            return ModeEvidence(
                status=EVIDENCE_UNAVAILABLE, reason_code=REASON_MODE_UNAVAILABLE
            )
        if not _mode_is_explicit(source):
            # The value came from the field default, not from an observed settings
            # source. A repository default is not live evidence.
            return ModeEvidence(
                status=EVIDENCE_UNAVAILABLE, reason_code=REASON_MODE_UNAVAILABLE
            )
        mode = resolve_paper_v2_mode(source)
        return ModeEvidence(status=EVIDENCE_READY, mode=mode)
    except Exception:  # noqa: BLE001 - unreadable mode must not read as a default
        return ModeEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_MODE_UNAVAILABLE
        )


def _mode_is_explicit(source: Any) -> bool:
    """Whether the mode was explicitly provided rather than filled from a default.

    A pydantic ``Settings`` exposes ``model_fields_set``. When the field is absent
    from that set, the observed value is the repository default and is therefore not
    live evidence; the probe must fail closed rather than claim the default as
    production fact. A plain object that carries the attribute is treated as an
    explicit observation, because a caller passing a settings double is asserting
    the value (and the production Compose file sets no ``OPIP_PAPER_V2_MODE``).
    """
    fields_set = getattr(source, "model_fields_set", None)
    if fields_set is None:
        return True
    return "opip_paper_v2_mode" in fields_set


def _process_settings() -> Any | None:
    try:
        from app.core.config import get_settings

        return get_settings()
    except Exception:  # noqa: BLE001 - unreadable configuration is not evidence
        return None


def observe_protection_evidence(client: Any | None) -> ProtectionEvidence:
    """Read the bounded protection-work projection to prove protection is healthy.

    Read-only. A missing client or unreadable projection is ``UNAVAILABLE`` and never
    reads as healthy; that is what withholds new Paper-v2 admissions.
    """
    if client is None:
        return ProtectionEvidence(
            status=EVIDENCE_UNAVAILABLE,
            independent_of_discovery=PROTECTION_INDEPENDENT_OF_DISCOVERY,
            reason_code=REASON_PROTECTION_UNAVAILABLE,
        )
    try:
        projection = client.get_paper_v2_protection_work()
    except Exception:  # noqa: BLE001 - unreadable work is not healthy
        return ProtectionEvidence(
            status=EVIDENCE_UNAVAILABLE,
            independent_of_discovery=PROTECTION_INDEPENDENT_OF_DISCOVERY,
            reason_code=REASON_PROTECTION_UNAVAILABLE,
        )
    status = str(getattr(projection, "status", "") or "")
    if status != "OK":
        return ProtectionEvidence(
            status=EVIDENCE_UNAVAILABLE,
            independent_of_discovery=PROTECTION_INDEPENDENT_OF_DISCOVERY,
            reason_code=REASON_PROTECTION_UNAVAILABLE,
        )
    items = tuple(getattr(projection, "items", ()) or ())
    if any(not _protection_item_is_safe(item) for item in items):
        # A readable projection is not the same as healthy protection: a positive
        # exposure with no committed plan, or in a state the runtime would refuse to
        # arm or trigger, is exactly what withholds new admissions. Readiness must
        # block it rather than report the store as healthy.
        return ProtectionEvidence(
            status=EVIDENCE_BLOCKED,
            independent_of_discovery=PROTECTION_INDEPENDENT_OF_DISCOVERY,
            open_exposure_count=len(items),
            reason_code=REASON_PROTECTION_UNSAFE,
        )
    open_count = sum(1 for item in items if not bool(getattr(item, "final_verified", False)))
    return ProtectionEvidence(
        status=EVIDENCE_READY,
        independent_of_discovery=PROTECTION_INDEPENDENT_OF_DISCOVERY,
        open_exposure_count=open_count,
    )


def _protection_item_is_safe(item: Any) -> bool:
    """Read-only mirror of the protection runtime's unsafe-exposure rule.

    Mirrors ``paper_v2_protection_runtime._advance_protection_item`` without advancing
    anything: a terminal or flat item is safe, while a positive exposure with no
    committed protection plan, or in a state the runtime would not arm or trigger, is
    unsafe. It never mutates canonical evidence.
    """
    try:
        from app.opip.contracts.paper_execution import ProtectionState
        from app.services.paper_v2_protection_runtime import QUANTITY_TOLERANCE
    except Exception:  # noqa: BLE001 - an unprovable rule must fail closed
        return False
    if bool(getattr(item, "final_verified", False)):
        return True
    remaining = float(getattr(item, "remaining_quantity", 0.0) or 0.0)
    if remaining <= QUANTITY_TOLERANCE:
        return True
    if getattr(item, "protection_plan", None) is None:
        return False
    state = str(getattr(item, "protection_state", "") or "")
    return state in {
        ProtectionState.PLANNED.value,
        ProtectionState.ACTIVE.value,
        ProtectionState.TRIGGERED.value,
    }


def observe_canonical_writer_evidence(client: Any | None) -> CanonicalWriterEvidence:
    """Prove the canonical ledger answers a bounded read, or report it unavailable."""
    if client is None:
        return CanonicalWriterEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_CANONICAL_WRITER_UNAVAILABLE
        )
    try:
        projection = client.get_paper_v2_active_exposures()
    except Exception:  # noqa: BLE001 - unreadable ledger is not healthy
        return CanonicalWriterEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_CANONICAL_WRITER_UNAVAILABLE
        )
    if str(getattr(projection, "status", "") or "") != "OK":
        return CanonicalWriterEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_CANONICAL_WRITER_UNAVAILABLE
        )
    return CanonicalWriterEvidence(status=EVIDENCE_READY)


def observe_universe_gate_evidence(
    observed_assets: tuple[Any, ...] | None = None,
) -> UniverseGateEvidence:
    """Report the universe-metadata admission gate, fail-closed by construction.

    The gate is the scan's own rule: when the exact observed ``AssetPairs`` metadata
    is absent, no Paper-v2 execution is attempted rather than re-requesting it. When
    a caller supplies an explicit observation, that observation is reported; when an
    empty observation is supplied the gate reads ``UNAVAILABLE``.
    """
    if observed_assets is None:
        return UniverseGateEvidence(
            status=EVIDENCE_READY,
            enforced_fail_closed=True,
            observed=False,
        )
    count = len(observed_assets)
    if count == 0:
        return UniverseGateEvidence(
            status=EVIDENCE_UNAVAILABLE,
            enforced_fail_closed=True,
            observed=True,
            observed_asset_count=0,
            reason_code=REASON_UNIVERSE_NOT_OBSERVED,
        )
    return UniverseGateEvidence(
        status=EVIDENCE_READY,
        enforced_fail_closed=True,
        observed=True,
        observed_asset_count=count,
    )


def observe_direction_coverage() -> DirectionCoverage:
    """Report the authoritative direction coverage from the frozen router contract."""
    try:
        from app.services.paper_v2_scan_router import SUPPORTED_DIRECTION

        long_covered = str(SUPPORTED_DIRECTION).upper() == "LONG"
    except Exception:  # noqa: BLE001 - an unreadable contract is not coverage
        return DirectionCoverage(
            long_covered=False,
            short_covered=False,
            reason_code=REASON_LONG_AUTHORITY_MISSING,
        )
    # The frozen slice is long-only: there is no short engine, and a SHORT is
    # refused rather than mapped onto a BUY. Full cutover therefore remains blocked
    # on short authority until a future increment supplies it or an owner ratifies a
    # long-only mandate. When even LONG is not covered the gap is larger still, so the
    # reason names the missing long authority instead of claiming no gap.
    return DirectionCoverage(
        long_covered=long_covered,
        short_covered=False,
        reason_code=(
            REASON_SHORT_AUTHORITY_MISSING
            if long_covered
            else REASON_LONG_AUTHORITY_MISSING
        ),
    )


def observe_pending_mandate() -> PendingMandate:
    """Report whether the current paper mandate requires a pending-limit lifecycle."""
    return PendingMandate(
        status=PAPER_V2_PENDING_MANDATE,
        immediate_only=True,
        requires_pending_lifecycle=False,
    )


def observe_selector_handoff() -> SelectorHandoff:
    """Verify the F7 -> Paper-v2 handoff contract fields, without wiring F7.

    Structural only: it proves the frozen F7 records expose the fields a Paper-v2
    handoff needs, and reports that F7 is not yet the live admission source and that
    the candidate-identity bridge is not yet present. It does not run the selector,
    wire it, or mutate anything.
    """
    required = {
        "decision.allocations": ("allocations",),
        "decision.selected_candidate_ids": ("selected_candidate_ids",),
        "allocation.candidate_id": ("candidate_id",),
        "allocation.episode_id": ("episode_id",),
        "allocation.symbol": ("symbol",),
        "allocation.direction": ("direction",),
        "allocation.allocated_capital": ("allocated_capital",),
        "candidate.episode_id": ("episode_id",),
        "candidate.feasibility_decision_id": ("feasibility_decision_id",),
        "candidate.forecast_decision_id": ("forecast_decision_id",),
        "candidate.symbol": ("symbol",),
        "candidate.direction": ("direction",),
    }
    try:
        from app.opip.contracts.portfolio import (
            PortfolioAllocation,
            PortfolioCandidate,
            PortfolioDecision,
        )

        owners = {
            "decision": PortfolioDecision,
            "allocation": PortfolioAllocation,
            "candidate": PortfolioCandidate,
        }
    except Exception:  # noqa: BLE001 - an unreadable contract is not compatible
        return SelectorHandoff(
            status=EVIDENCE_UNAVAILABLE,
            fields_compatible=False,
            admission_source_active=False,
            reason_code=REASON_SELECTOR_HANDOFF_UNAVAILABLE,
        )

    missing: list[str] = []
    for name, path in required.items():
        owner = owners[name.split(".", 1)[0]]
        attribute = path[0]
        if not (
            attribute in getattr(owner, "__dataclass_fields__", {})
            or isinstance(getattr(owner, attribute, None), property)
        ):
            missing.append(name)

    if missing:
        return SelectorHandoff(
            status=EVIDENCE_BLOCKED,
            fields_compatible=False,
            admission_source_active=False,
            missing_fields=tuple(missing),
            reason_code=REASON_SELECTOR_HANDOFF_INCOMPLETE,
        )
    # The contracts expose every field the handoff needs. F7 is still not the live
    # admission source: wiring it (and the PCAND->OPIPC candidate-identity bridge) is
    # the R4-B activation step, reported as a prerequisite rather than a field gap.
    return SelectorHandoff(
        status=EVIDENCE_READY,
        fields_compatible=True,
        admission_source_active=False,
    )


def observe_rollback_evidence() -> RollbackEvidence:
    """Prove the legacy authority remains available as the rollback path.

    Rollback is the activation switch itself: the repository default is ``off`` and a
    non-active mode resolves to the legacy authority, so no one-way migration is
    introduced. If the default cannot be proven, rollback is unavailable.
    """
    try:
        from app.core.config import Settings

        default = Settings.model_fields["opip_paper_v2_mode"].default
    except Exception:  # noqa: BLE001 - an unprovable rollback is unavailable
        return RollbackEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_ROLLBACK_UNAVAILABLE
        )
    if default != "off":
        return RollbackEvidence(
            status=EVIDENCE_UNAVAILABLE, reason_code=REASON_ROLLBACK_UNAVAILABLE
        )
    return RollbackEvidence(status=EVIDENCE_READY)


def evaluate_cutover_readiness(evidence: CutoverEvidence) -> CutoverReadinessReport:
    """Derive the deterministic verdict from typed evidence.

    Only independently verifiable technical gates are blockers. Owner activation
    prerequisites (setting the mode active, wiring the selector as admission
    authority) are reported separately as ``activation_prerequisites`` so this
    increment can prove readiness without activating anything.
    """
    reasons: list[str] = []
    prerequisites: list[str] = []

    # --- mode evidence ------------------------------------------------------
    if evidence.mode.status != EVIDENCE_READY:
        reasons.append(REASON_MODE_UNAVAILABLE)
    elif not evidence.mode.active:
        # Not a technical blocker: the owner selects the mode during activation.
        prerequisites.append("PAPER_V2_MODE_NOT_ACTIVE")

    # --- legacy drain -------------------------------------------------------
    if evidence.drain.status == DRAIN_UNAVAILABLE:
        reasons.append(REASON_LEGACY_DRAIN_UNAVAILABLE)
    elif evidence.drain.status == DRAIN_DRAINING:
        reasons.append(REASON_LEGACY_DRAIN_DRAINING)

    # --- protection ---------------------------------------------------------
    if not evidence.protection.independent_of_discovery:
        reasons.append(REASON_PROTECTION_NOT_INDEPENDENT)
    if evidence.protection.status == EVIDENCE_BLOCKED:
        reasons.append(REASON_PROTECTION_UNSAFE)
    elif evidence.protection.status != EVIDENCE_READY:
        reasons.append(REASON_PROTECTION_UNAVAILABLE)

    # --- universe metadata gate --------------------------------------------
    if not evidence.universe.enforced_fail_closed:
        reasons.append(REASON_UNIVERSE_GATE_ABSENT)
    elif evidence.universe.status != EVIDENCE_READY:
        reasons.append(REASON_UNIVERSE_NOT_OBSERVED)

    # --- direction authority coverage --------------------------------------
    if not evidence.direction.long_covered:
        reasons.append(REASON_LONG_AUTHORITY_MISSING)
    if not evidence.direction.short_covered:
        reasons.append(REASON_SHORT_AUTHORITY_MISSING)

    # --- pending-entry mandate ---------------------------------------------
    if evidence.pending.requires_pending_lifecycle:
        reasons.append(REASON_PENDING_MANDATE_UNKNOWN)

    # --- selector handoff ---------------------------------------------------
    if evidence.selector.status == EVIDENCE_UNAVAILABLE:
        reasons.append(REASON_SELECTOR_HANDOFF_UNAVAILABLE)
    elif not evidence.selector.fields_compatible:
        reasons.append(REASON_SELECTOR_HANDOFF_INCOMPLETE)
    if not evidence.selector.admission_source_active:
        # Owner activation prerequisite: R4-B wires the selector as admission source.
        prerequisites.append("SELECTOR_ADMISSION_NOT_ACTIVE")

    # --- canonical writer ---------------------------------------------------
    if evidence.canonical_writer.status != EVIDENCE_READY:
        reasons.append(REASON_CANONICAL_WRITER_UNAVAILABLE)

    # --- rollback -----------------------------------------------------------
    if evidence.rollback.status != EVIDENCE_READY:
        reasons.append(REASON_ROLLBACK_UNAVAILABLE)

    deduped = tuple(dict.fromkeys(reasons))
    if deduped:
        overall = READINESS_NOT_READY
    else:
        overall = READINESS_READY
    return CutoverReadinessReport(
        overall=overall,
        reason_codes=deduped,
        activation_prerequisites=tuple(dict.fromkeys(prerequisites)),
        evidence=evidence,
    )


def cutover_readiness_report(
    settings: Any | None = None,
    *,
    client: Any | None = None,
    observed_universe_assets: tuple[Any, ...] | None = None,
) -> CutoverReadinessReport:
    """Observe bounded live evidence and derive the aggregate readiness verdict.

    Read-only and fail-closed: each fact is observed independently and an unreadable
    fact becomes ``UNAVAILABLE``. The function never activates Paper v2 and never
    mutates canonical evidence.
    """
    if client is None:
        client = _default_writer_client()
    equity = _starting_equity(settings)
    if equity is None:
        # Matching the scan's authority resolver: an unprovable starting equity is
        # not a reason to evaluate legacy drain optimistically. Fail closed.
        drain = LegacyDrainStatus(
            status=DRAIN_UNAVAILABLE,
            reason=(
                "starting equity is unavailable, so the legacy drain cannot be "
                "proven; the scan resolver would also block the cutover"
            ),
        )
    else:
        drain = evaluate_legacy_drain(starting_equity=equity)
    return evaluate_cutover_readiness(
        CutoverEvidence(
            mode=observe_mode_evidence(settings),
            drain=drain,
            protection=observe_protection_evidence(client),
            universe=observe_universe_gate_evidence(observed_universe_assets),
            direction=observe_direction_coverage(),
            pending=observe_pending_mandate(),
            selector=observe_selector_handoff(),
            canonical_writer=observe_canonical_writer_evidence(client),
            rollback=observe_rollback_evidence(),
        )
    )


def _starting_equity(settings: Any | None) -> float | None:
    """The starting equity the drain evaluator needs, or ``None`` when unprovable.

    Matches ``scan_opportunities._legacy_drain_status``: a missing, zero, negative or
    malformed equity is not evidence that the legacy subsystems are empty, so it must
    block rather than be replaced by a default. ``None`` means "unavailable".
    """
    try:
        source = settings if settings is not None else _process_settings()
        equity = float(getattr(source, "paper_trade_starting_equity", 0.0) or 0.0)
    except Exception:  # noqa: BLE001 - unreadable equity is not evidence
        return None
    return equity if equity > 0 else None


def _default_writer_client() -> Any | None:
    try:
        from app.services.paper_v2_scan_router import _writer_client

        return _writer_client()
    except Exception:  # noqa: BLE001 - an unbuildable writer is reported unavailable
        return None
