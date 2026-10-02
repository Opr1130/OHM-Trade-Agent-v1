"""R4-B1 dormant target-spine runtime integration.

Wires the already-proven F3-F7 target composition (R4-B0) into the unified cycle
as a NON-AUTHORITATIVE, off-by-default shadow path.

What this module is
-------------------

A thin runtime seam. It resolves an explicit mode gate, asks a read-only
snapshot provider for feature snapshots, and - only when the gate is ``shadow``
and a source is available - composes each snapshot through the *same production
functions* the R4-B0 composition proof already exercises, then reports a
machine-readable summary.

What this module is NOT
-----------------------

It composes and reports only. It never admits, reserves, orders, fills, protects
or alerts, and it writes no canonical evidence. It imports no exchange, order,
Committee or funded surface, and it holds no writer, reservation or execution
authority. The target path is not the admission authority: legacy Top-8 plus
profit-ranking and Freqtrade dry-run plus Paper-v1 remain authoritative.

Posture
-------

``off`` (the default) means the unified cycle does not invoke this module at all.
``shadow`` means it is invoked, but with no snapshot source it is a recorded
no-op. Activating a real source is an owner decision and is out of scope here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

#: The only modes this gate accepts. ``off`` is inert; ``shadow`` is the
#: non-authoritative composition. There is deliberately no ``active`` mode.
TARGET_SPINE_MODES = frozenset({"off", "shadow"})

#: Reason codes for an inert run, so a no-op is distinguishable from a failure.
REASON_MODE_OFF = "TARGET_SPINE_MODE_OFF"
REASON_NO_SNAPSHOT_SOURCE = "TARGET_SPINE_NO_SNAPSHOT_SOURCE"

#: Composition dispositions this summary reports. They mirror the frozen F5/F6/F7
#: vocabulary and never collapse veto, abstention and cash/no-trade.
DISPOSITION_SELECTED = "SELECTED"
DISPOSITION_ABSTAINED = "INSUFFICIENT_EVIDENCE"
DISPOSITION_VETOED = "VETO"
DISPOSITION_CASH_NO_TRADE = "CASH_NO_TRADE"
DISPOSITION_ERROR = "ERROR"


def resolve_target_spine_mode(settings: Any | None = None) -> str:
    """Resolve the gate fail-closed to ``off`` for an unknown or missing value."""
    if settings is not None:
        mode = str(getattr(settings, "opip_target_spine_mode", "off") or "off")
    else:
        try:
            from app.core.config import get_settings

            mode = str(get_settings().opip_target_spine_mode or "off")
        except Exception:
            mode = "off"
    mode = mode.strip().lower()
    return mode if mode in TARGET_SPINE_MODES else "off"


def target_spine_enabled(settings: Any | None = None) -> bool:
    """True only for ``shadow``: the cycle may invoke the composition."""
    return resolve_target_spine_mode(settings) == "shadow"


@dataclass(frozen=True)
class TargetSpineDisposition:
    """One snapshot's composed outcome. Reporting only, never an action."""

    snapshot_id: str
    disposition: str
    selected: bool = False
    handoff_built: bool = False
    detail: str | None = None


@dataclass(frozen=True)
class TargetSpineSummary:
    """Operator-observable outcome of one dormant composition pass."""

    mode: str
    inert: bool = True
    considered: int = 0
    selected: int = 0
    abstained: int = 0
    vetoed: int = 0
    cash_no_trade: int = 0
    errors: int = 0
    handoffs_built: int = 0
    reason: str | None = None
    details: tuple[str, ...] = ()


def _tally(dispositions: Sequence[TargetSpineDisposition]) -> TargetSpineSummary:
    selected = sum(1 for item in dispositions if item.disposition == DISPOSITION_SELECTED)
    abstained = sum(
        1 for item in dispositions if item.disposition == DISPOSITION_ABSTAINED
    )
    vetoed = sum(1 for item in dispositions if item.disposition == DISPOSITION_VETOED)
    cash = sum(1 for item in dispositions if item.disposition == DISPOSITION_CASH_NO_TRADE)
    errors = sum(1 for item in dispositions if item.disposition == DISPOSITION_ERROR)
    return TargetSpineSummary(
        mode="shadow",
        inert=False,
        considered=len(dispositions),
        selected=selected,
        abstained=abstained,
        vetoed=vetoed,
        cash_no_trade=cash,
        errors=errors,
        handoffs_built=sum(1 for item in dispositions if item.handoff_built),
        details=tuple(
            f"{item.disposition} {item.snapshot_id}"
            + (f": {item.detail}" if item.detail else "")
            for item in dispositions
        ),
    )


def run_target_spine_cycle(
    *,
    settings: Any,
    snapshots: Sequence[Any] = (),
    compose: Callable[[Any], TargetSpineDisposition] | None = None,
) -> TargetSpineSummary:
    """Compose the target spine over the available snapshots. Writes nothing.

    The caller supplies the snapshots (a read-only source) and the composition
    callable. With no source - which is the production posture while the Feature
    Bus is ``off`` - the pass is a recorded no-op, not a failure.
    """
    mode = resolve_target_spine_mode(settings)
    if mode == "off":
        return TargetSpineSummary(mode="off", inert=True, reason=REASON_MODE_OFF)
    if compose is None or len(snapshots) == 0:
        return TargetSpineSummary(
            mode=mode, inert=True, reason=REASON_NO_SNAPSHOT_SOURCE
        )
    dispositions: list[TargetSpineDisposition] = []
    for snapshot in snapshots:
        try:
            dispositions.append(compose(snapshot))
        except Exception as exc:  # noqa: BLE001 - one snapshot must not stop others
            snapshot_id = str(getattr(snapshot, "snapshot_id", "UNKNOWN"))
            dispositions.append(
                TargetSpineDisposition(
                    snapshot_id=snapshot_id,
                    disposition=DISPOSITION_ERROR,
                    detail=f"{type(exc).__name__}: {exc}",
                )
            )
    return _tally(dispositions)


__all__ = [
    "DISPOSITION_ABSTAINED",
    "DISPOSITION_CASH_NO_TRADE",
    "DISPOSITION_ERROR",
    "DISPOSITION_SELECTED",
    "DISPOSITION_VETOED",
    "REASON_MODE_OFF",
    "REASON_NO_SNAPSHOT_SOURCE",
    "TARGET_SPINE_MODES",
    "TargetSpineDisposition",
    "TargetSpineSummary",
    "resolve_target_spine_mode",
    "run_target_spine_cycle",
    "target_spine_enabled",
]
