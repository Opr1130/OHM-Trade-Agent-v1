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

#: Cutover readiness states. Only ``READY`` permits new Paper-v2 entries.
DRAIN_READY = "READY"
DRAIN_DRAINING = "DRAINING"
DRAIN_UNAVAILABLE = "UNAVAILABLE"

__all__ = [
    "DRAIN_DRAINING",
    "DRAIN_READY",
    "DRAIN_UNAVAILABLE",
    "LegacyDrainStatus",
    "evaluate_legacy_drain",
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
        v1_reserved = float(summary.reserved_capital)
    except Exception as exc:  # noqa: BLE001 - unreadable state must block cutover
        return LegacyDrainStatus(
            status=DRAIN_UNAVAILABLE,
            reason=f"legacy paper state is unreadable: {type(exc).__name__}: {exc}",
        )

    outstanding_total = open_trades + pending_entries + v1_pending + v1_open
    if outstanding_total > 0:
        return LegacyDrainStatus(
            status=DRAIN_DRAINING,
            reason=(
                "legacy paper obligations remain: "
                f"freqtrade open={open_trades} pending={pending_entries}, "
                f"paper v1 pending={v1_pending} open={v1_open}"
            ),
            freqtrade_open_trades=open_trades,
            freqtrade_pending_entries=pending_entries,
            paper_v1_pending_entries=v1_pending,
            paper_v1_open_positions=v1_open,
            paper_v1_reserved_capital=v1_reserved,
            legacy_control_enabled=control_enabled,
        )

    return LegacyDrainStatus(
        status=DRAIN_READY,
        reason="no outstanding legacy paper obligation in either subsystem",
        paper_v1_reserved_capital=v1_reserved,
        legacy_control_enabled=control_enabled,
    )
