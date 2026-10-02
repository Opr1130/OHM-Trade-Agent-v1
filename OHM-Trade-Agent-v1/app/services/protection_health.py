"""F11 protection health: one deterministic, read-only protection decision.

F11 ("Safety / Monitoring") must prove, before any target paper cutover, that
protection is *known* and that no exposure is silently unheld. The individual
producers already exist and stay authoritative in their own domains:

* ``app/services/kraken_exposure_resolver`` resolves the live (Kraken/operator)
  exposure domain into typed ``ResolvedExposure`` rows.
* ``app/services/paper_v2_protection_runtime`` owns the target (Paper-v2)
  protection sweep and already fails closed through ``new_admissions_allowed``.
* ``app/services/system_incidents`` owns the incident lifecycle for degraded
  connectivity, pricing and position verification.

What was missing is a single, explicit, fail-closed *decision* over the live
exposure domain: whether protection is proven, detection of silent holdings, and
suspension of new admissions when protection cannot be proven.

Scope of the current view
-------------------------

It evaluates the live (Kraken/operator) exposure domain, resolved by
``kraken_exposure_resolver``, and accepts two caller-supplied verdicts: the
target-domain verdict (``target_protection_allows_admissions``) and the
protection-incident verdict (``incidents_healthy``). It does not itself read the
Paper-v2 protection runtime or the incident store; a caller that has those
verdicts supplies them. While the target path is dormant the report passes no
target verdict, so a target-domain objection is not yet observed here - that
consumption is deferred and owned by the existing target
``new_admissions_allowed`` gate.

Ownership boundaries (what F11 is NOT)
--------------------------------------

* It NEVER mutates a position, registry, reservation or canonical record, so it
  is not a protection authority.
* It is pure: no clock, network, database, environment or AI.
* It observes *now*. It owns NO suspension latch and NO human-resume decision:
  the durable suspension/resumption authority is the incident lifecycle
  (``system_incidents``) and the target ``new_admissions_allowed`` gate. F11 only
  reports an instantaneous decision and cannot grant authority it does not own.
* It is deterministic and fails closed: anything not provably healthy - including
  unproven incident health - suspends admissions.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

#: Protection-health states. ``HEALTHY`` is the only state that permits new
#: admissions; every other state withholds them.
STATE_HEALTHY = "HEALTHY"
STATE_UNSAFE = "UNSAFE"
STATE_UNAVAILABLE = "UNAVAILABLE"

#: Machine-readable reason codes.
REASON_HEALTHY = "PROTECTION_PROVEN"
REASON_SILENT_HOLDING = "SILENT_HOLDING_UNPROTECTED_EXPOSURE"
REASON_UNMANAGED_EXPOSURE = "UNMANAGED_EXPOSURE_REQUIRES_REVIEW"
REASON_EXPOSURE_UNCERTAIN = "EXPOSURE_VERIFICATION_UNCERTAIN"
REASON_COVERAGE_INCOMPLETE = "EXPOSURE_COVERAGE_INCOMPLETE"
REASON_GEOMETRY_INVALID = "PROTECTION_GEOMETRY_INVALID"
REASON_TARGET_PROTECTION_UNHEALTHY = "TARGET_PROTECTION_UNHEALTHY"
REASON_INCIDENTS_UNHEALTHY = "PROTECTION_INCIDENT_OPEN"
REASON_INCIDENTS_UNPROVEN = "PROTECTION_INCIDENT_HEALTH_UNPROVEN"

#: Statuses a resolved exposure may carry.
_STATUS_MANAGED = "VERIFIED_MANAGED"
_STATUS_UNMANAGED = "VERIFIED_UNMANAGED"
_STATUS_ABSENT = "ABSENT"
_STATUS_DEGRADED = "DEGRADED"
_STATUS_UNKNOWN = "UNKNOWN"

_DIRECTION_LONG = "LONG"
_DIRECTION_SHORT = "SHORT"


def _positive_finite(value: object) -> bool:
    """A finite, strictly positive number, or False. Never raises."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, ValueError, TypeError):
        # An oversized int (for example 10**400) must fail closed, not raise.
        return False
    return math.isfinite(number) and number > 0


def _protection_geometry_proven(trade: Any) -> bool:
    """The canonical protection plan for a live holding is proven.

    A stop is only protection when it is finite, positive and on the correct side
    of the entry, matching the direction-bound geometry the live protection and
    Paper-v2 protection contracts require: a LONG stops below its entry, a SHORT
    stops above it. A stop that is merely finite and positive - but on the wrong
    side, or equal to the entry - is not a protection plan. Targets are not
    represented on the live lifecycle record, so they are not asserted here.
    """
    if trade is None:
        return False
    direction = str(getattr(trade, "direction", "") or "").upper()
    entry = getattr(trade, "entry_price", None)
    stop = getattr(trade, "stop_price", None)
    if not _positive_finite(entry) or not _positive_finite(stop):
        return False
    entry_value = float(entry)
    stop_value = float(stop)
    if direction == _DIRECTION_LONG:
        return stop_value < entry_value
    if direction == _DIRECTION_SHORT:
        return stop_value > entry_value
    return False


def _known_direction(trade: Any) -> bool:
    return str(getattr(trade, "direction", "") or "").upper() in {
        _DIRECTION_LONG,
        _DIRECTION_SHORT,
    }


@dataclass(frozen=True)
class ProtectionHealth:
    """One deterministic protection decision for the observed exposure set."""

    state: str
    admissions_suspended: bool
    coverage_complete: bool
    reason_codes: tuple[str, ...]
    silent_holdings: tuple[str, ...] = ()
    geometry_invalid_exposures: tuple[str, ...] = ()
    unmanaged_exposures: tuple[str, ...] = ()
    uncertain_exposures: tuple[str, ...] = ()

    @property
    def healthy(self) -> bool:
        return self.state == STATE_HEALTHY

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "admissions_suspended": self.admissions_suspended,
            "coverage_complete": self.coverage_complete,
            "reason_codes": list(self.reason_codes),
            "silent_holdings": list(self.silent_holdings),
            "geometry_invalid_exposures": list(self.geometry_invalid_exposures),
            "unmanaged_exposures": list(self.unmanaged_exposures),
            "uncertain_exposures": list(self.uncertain_exposures),
        }


def evaluate_protection_health(
    exposures: Sequence[Any] = (),
    *,
    coverage_complete: bool | None = None,
    target_protection_allows_admissions: bool | None = None,
    incidents_healthy: bool | None = None,
) -> ProtectionHealth:
    """Decide whether protection is proven for the observed exposure set.

    ``exposures`` are ``ResolvedExposure``-shaped (``status``, ``symbol``,
    ``observed_quantity``, ``trade``). ``target_protection_allows_admissions`` is
    the optional target-domain verdict (``None`` while the target path is
    dormant). ``incidents_healthy`` is the protection-incident verdict supplied by
    the incident store: ``True`` for no open protection incident, ``False`` for an
    open one, ``None`` when unproven.

    Fails closed:
    - coverage is proven only when the caller states ``coverage_complete=True``;
    - incident health is proven only when the caller states ``incidents_healthy=True``;
    - an unmanaged, unreadable, unheld or wrongly-geometry-protected exposure
      withholds admissions.

    It never mutates anything and owns no suspension latch; it returns a decision.
    """
    coverage_proven = coverage_complete is True
    incidents_proven = incidents_healthy is True
    silent: list[str] = []
    geometry_invalid: list[str] = []
    unmanaged: list[str] = []
    uncertain: list[str] = []
    reasons: list[str] = []

    for exposure in exposures:
        status = str(getattr(exposure, "status", "") or "")
        symbol = str(getattr(exposure, "symbol", "") or "")
        if status == _STATUS_MANAGED:
            trade = getattr(exposure, "trade", None)
            stop = getattr(trade, "stop_price", None) if trade is not None else None
            entry = getattr(trade, "entry_price", None) if trade is not None else None
            if not _known_direction(trade):
                uncertain.append(symbol)
            elif not _positive_finite(stop) or not _positive_finite(entry):
                # No provable protection plan: a missing or invalid stop or entry.
                silent.append(symbol)
            elif _protection_geometry_proven(trade):
                continue
            else:
                # A positive plan on the wrong side of, or equal to, the entry.
                geometry_invalid.append(symbol)
        elif status == _STATUS_UNMANAGED:
            unmanaged.append(symbol)
        elif status == _STATUS_ABSENT:
            continue
        else:
            # DEGRADED, UNKNOWN, or any unrecognized status is uncertainty.
            uncertain.append(symbol)

    if not coverage_proven:
        reasons.append(REASON_COVERAGE_INCOMPLETE)
    if silent:
        reasons.append(REASON_SILENT_HOLDING)
    if geometry_invalid:
        reasons.append(REASON_GEOMETRY_INVALID)
    if unmanaged:
        reasons.append(REASON_UNMANAGED_EXPOSURE)
    if uncertain:
        reasons.append(REASON_EXPOSURE_UNCERTAIN)
    if incidents_healthy is False:
        reasons.append(REASON_INCIDENTS_UNHEALTHY)
    if incidents_healthy is None:
        reasons.append(REASON_INCIDENTS_UNPROVEN)
    if target_protection_allows_admissions is False:
        reasons.append(REASON_TARGET_PROTECTION_UNHEALTHY)

    if not coverage_proven or not incidents_proven or uncertain:
        state = STATE_UNAVAILABLE
    elif (
        silent
        or geometry_invalid
        or unmanaged
        or target_protection_allows_admissions is False
    ):
        state = STATE_UNSAFE
    else:
        state = STATE_HEALTHY
        reasons = [REASON_HEALTHY]

    return ProtectionHealth(
        state=state,
        admissions_suspended=state != STATE_HEALTHY,
        coverage_complete=coverage_proven,
        reason_codes=tuple(reasons),
        silent_holdings=tuple(sorted(set(silent))),
        geometry_invalid_exposures=tuple(sorted(set(geometry_invalid))),
        unmanaged_exposures=tuple(sorted(set(unmanaged))),
        uncertain_exposures=tuple(sorted(set(uncertain))),
    )


__all__ = [
    "ProtectionHealth",
    "REASON_COVERAGE_INCOMPLETE",
    "REASON_EXPOSURE_UNCERTAIN",
    "REASON_GEOMETRY_INVALID",
    "REASON_HEALTHY",
    "REASON_INCIDENTS_UNHEALTHY",
    "REASON_INCIDENTS_UNPROVEN",
    "REASON_SILENT_HOLDING",
    "REASON_TARGET_PROTECTION_UNHEALTHY",
    "REASON_UNMANAGED_EXPOSURE",
    "STATE_HEALTHY",
    "STATE_UNAVAILABLE",
    "STATE_UNSAFE",
    "evaluate_protection_health",
]
