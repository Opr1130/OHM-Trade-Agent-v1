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

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Protection admission stamp (ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1, slice P2)
#
# F11 keeps sole computation authority. This module only publishes the verdict
# it already computed and reads it back for consumers in other containers. The
# stamp can never grant authority, relax an F11 decision or override an
# incident.
#
# The freshness bound is DERIVED from successful authoritative observations -
# not hardcoded, and not taken from cron configuration. Below STAMP_MIN_SAMPLES
# the bound is unknown and every consumer fails closed: twenty samples give a
# preliminary operating baseline, they do NOT establish a reliable p99.
# ---------------------------------------------------------------------------

STAMP_CONTRACT_VERSION = "opip-protection-admission-stamp-v1"
STAMP_MIN_SAMPLES = 20
STAMP_P99_MULTIPLIER = 2.0
STAMP_ABSOLUTE_CEILING_SECONDS = 3600.0

REASON_STAMP_UNKNOWN = "PROTECTION_ADMISSIONS_SUSPENDED_UNKNOWN"
REASON_STAMP_SUSPENDED = "PROTECTION_ADMISSIONS_SUSPENDED"


@dataclass(frozen=True)
class ProtectionAdmissionStamp:
    """Read-only view of an F11 admission verdict. Grants nothing."""

    admissions_suspended: bool
    fresh: bool
    reason: str | None
    observed_at: datetime | None = None
    freshness_bound_s: float | None = None
    sample_count: int = 0
    sequence: int | None = None
    coverage_complete: bool | None = None
    incidents_healthy: bool | None = None
    contract_version: str | None = None

    @property
    def usable(self) -> bool:
        """True only when the stamp may be used to avoid a refusal."""
        return self.fresh and not self.admissions_suspended


def _unknown_stamp() -> ProtectionAdmissionStamp:
    return ProtectionAdmissionStamp(
        admissions_suspended=True, fresh=False, reason=REASON_STAMP_UNKNOWN
    )


def derive_freshness_bound(
    observation_times: Sequence[datetime],
    *,
    min_samples: int = STAMP_MIN_SAMPLES,
    p99_multiplier: float = STAMP_P99_MULTIPLIER,
    ceiling_seconds: float = STAMP_ABSOLUTE_CEILING_SECONDS,
) -> float | None:
    """Derive the freshness bound from SUCCESSFUL observation timestamps.

    Returns ``None`` while fewer than ``min_samples`` successful observations
    exist, which every consumer must treat as fail-closed. The ceiling is a
    safety clamp so a corrupt stamp cannot widen its own authority.
    """
    ordered = sorted(observation_times)
    if len(ordered) < max(2, int(min_samples)):
        return None
    gaps = [
        (ordered[i + 1] - ordered[i]).total_seconds()
        for i in range(len(ordered) - 1)
    ]
    gaps = sorted(gap for gap in gaps if gap > 0)
    if not gaps:
        return None
    index = min(len(gaps) - 1, max(0, int(round(0.99 * (len(gaps) - 1)))))
    bound = max(gaps[index] * float(p99_multiplier), gaps[0])
    return min(float(ceiling_seconds), bound)


def read_admission_stamp(
    payload: Any,
    *,
    now: datetime,
    running_release_sha: str | None,
    contract_version: str = STAMP_CONTRACT_VERSION,
    previous_sequence: int | None = None,
) -> ProtectionAdmissionStamp:
    """Validate a stamp document. Every failure is fail-closed.

    ``payload`` is the already-parsed stamp mapping, or ``None`` when the file
    was missing or unreadable. No I/O happens here, so callers keep control of
    filesystem access.
    """
    if not isinstance(payload, dict):
        return _unknown_stamp()
    if payload.get("contract_version") != contract_version:
        return _unknown_stamp()
    if running_release_sha and payload.get("release_sha") != running_release_sha:
        return _unknown_stamp()

    try:
        observed = datetime.fromisoformat(
            str(payload.get("observed_at_utc")).replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return _unknown_stamp()
    if observed.tzinfo is None or observed.utcoffset() is None:
        return _unknown_stamp()
    observed = observed.astimezone(timezone.utc)

    try:
        bound = float(payload.get("freshness_bound_s"))
        samples = int(payload.get("sample_count") or 0)
    except (TypeError, ValueError):
        return _unknown_stamp()
    if not math.isfinite(bound) or bound <= 0:
        return _unknown_stamp()
    if bound > STAMP_ABSOLUTE_CEILING_SECONDS:
        return _unknown_stamp()
    if samples < STAMP_MIN_SAMPLES:
        return _unknown_stamp()

    age = (now - observed).total_seconds()
    if age < 0 or age > bound:
        return ProtectionAdmissionStamp(
            admissions_suspended=True, fresh=False, reason=REASON_STAMP_UNKNOWN,
            observed_at=observed, freshness_bound_s=bound, sample_count=samples,
        )

    sequence = payload.get("sequence")
    if previous_sequence is not None and sequence is not None:
        try:
            regressed = int(sequence) <= int(previous_sequence)
        except (TypeError, ValueError):
            return _unknown_stamp()
        if regressed:
            return ProtectionAdmissionStamp(
                admissions_suspended=True, fresh=False,
                reason=REASON_STAMP_UNKNOWN, observed_at=observed,
                freshness_bound_s=bound, sample_count=samples,
            )

    coverage = payload.get("coverage_complete")
    healthy = payload.get("incidents_healthy")
    reasons = payload.get("reasons") or []
    unhealthy = coverage is not True or healthy is not True or bool(reasons)
    suspended = bool(payload.get("admissions_suspended")) or unhealthy
    return ProtectionAdmissionStamp(
        admissions_suspended=bool(suspended),
        fresh=True,
        reason=REASON_STAMP_SUSPENDED if suspended else None,
        observed_at=observed,
        freshness_bound_s=bound,
        sample_count=samples,
        sequence=None if sequence is None else int(sequence),
        coverage_complete=None if coverage is None else bool(coverage),
        incidents_healthy=None if healthy is None else bool(healthy),
        contract_version=contract_version,
    )


def build_admission_stamp_payload(
    *,
    release_sha: str,
    policy_version: str,
    observed_at: datetime,
    sequence: int,
    admissions_suspended: bool,
    coverage_complete: bool | None,
    incidents_healthy: bool | None,
    reasons: Sequence[str],
    freshness_bound_s: float,
    sample_count: int,
    contract_version: str = STAMP_CONTRACT_VERSION,
) -> dict[str, Any]:
    """Serialise the verdict the protection phase already computed."""
    return {
        "contract_version": contract_version,
        "policy_version": policy_version,
        "release_sha": release_sha,
        "observed_at_utc": observed_at.astimezone(timezone.utc).isoformat(),
        "sequence": int(sequence),
        "admissions_suspended": bool(admissions_suspended),
        "coverage_complete": coverage_complete,
        "incidents_healthy": incidents_healthy,
        "reasons": list(reasons),
        "freshness_bound_s": float(freshness_bound_s),
        "sample_count": int(sample_count),
    }


#: Independently bounded policy for the published stamp. This configured
#: maximum is the safety authority; the derived p99 may only TIGHTEN it.
STAMP_MAX_FRESHNESS_ENV = "OPIP_PROTECTION_STAMP_MAX_FRESHNESS_SECONDS"
STAMP_DEFAULT_MAX_FRESHNESS_SECONDS = 300.0
STAMP_HISTORY_LIMIT = 200


def publish_admission_stamp(
    *,
    stamp_path: Path,
    history_path: Path,
    release_sha: str,
    policy_version: str,
    observed_at: datetime,
    admissions_suspended: bool,
    coverage_complete: bool | None,
    incidents_healthy: bool | None,
    reasons: Sequence[str],
    max_freshness_seconds: float | None = None,
) -> dict[str, Any]:
    """Publish the F11 verdict the protection phase already computed.

    The freshness bound is ``min(configured_max, 2 x p99(observed gaps))``.
    The configured maximum is the safety authority: a small sample can never
    widen it, and while fewer than ``STAMP_MIN_SAMPLES`` successful
    observations exist the stamp advertises an insufficient sample count so
    every consumer fails closed.

    Publication is atomic (temp file + ``os.replace``). Only successful
    observations enter the history ring that drives the derived bound.
    """
    limit = float(
        max_freshness_seconds
        if max_freshness_seconds is not None
        else STAMP_DEFAULT_MAX_FRESHNESS_SECONDS
    )
    if not math.isfinite(limit) or limit <= 0:
        limit = STAMP_DEFAULT_MAX_FRESHNESS_SECONDS
    limit = min(limit, STAMP_ABSOLUTE_CEILING_SECONDS)

    history = _load_stamp_history(history_path)
    successful = coverage_complete is True and incidents_healthy is True and not reasons
    if successful:
        history.append(observed_at)
    history = sorted(history)[-STAMP_HISTORY_LIMIT:]

    derived = derive_freshness_bound(history)
    bound = limit if derived is None else min(limit, derived)
    sequence = int(time.time() * 1000)

    payload = build_admission_stamp_payload(
        release_sha=release_sha,
        policy_version=policy_version,
        observed_at=observed_at,
        sequence=sequence,
        admissions_suspended=admissions_suspended,
        coverage_complete=coverage_complete,
        incidents_healthy=incidents_healthy,
        reasons=reasons,
        freshness_bound_s=bound,
        sample_count=len(history),
    )
    _atomic_write_json(stamp_path, payload)
    _atomic_write_json(
        history_path,
        {"observations": [moment.isoformat() for moment in history]},
    )
    return payload


def _load_stamp_history(path: Path) -> list[datetime]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    moments: list[datetime] = []
    for value in (raw.get("observations") or [])[-STAMP_HISTORY_LIMIT:]:
        try:
            moment = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moments.append(moment.astimezone(timezone.utc))
    return moments


def _atomic_write_json(path: Path, payload: Any) -> None:
    """Publish atomically so a reader never sees a partial document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)
