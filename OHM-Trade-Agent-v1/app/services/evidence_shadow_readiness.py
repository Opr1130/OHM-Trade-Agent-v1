"""AC-026 EVIDENCE_SHADOW readiness: a pure, read-only cutover decision.

This module answers a single question, deterministically and without side
effects: *may the EVIDENCE_SHADOW profile be activated right now?*

It is deliberately separate from F11 strict protection health
(:mod:`app.services.protection_health`). F11 answers "is protection proven for
the observed exposure set?" and fails closed on any unmanaged holding. The
EVIDENCE_SHADOW profile is a *shadow* observation mode: it does not place
orders, so a known external ``VERIFIED_UNMANAGED`` holding is not a reason to
withhold activation. It remains visible, remains part of strict F11, and is
reported here as advisory rather than blocking.

Ownership boundaries (what this module is NOT)
----------------------------------------------

* It NEVER mutates a position, registry, reservation, incident or canonical
  record. It is pure: no clock, network, database, environment or AI.
* It does NOT recover incidents. It only *classifies* an open incident as
  candidate-recoverable when the caller supplies fresh candidate evidence that
  proves the scope-specific recovery predicate. The durable recovery authority
  remains :mod:`app.services.system_incidents`.
* It does NOT reclassify, filter or whitelist ``VERIFIED_UNMANAGED`` exposure.
  The exposure keeps its status; only its *effect on this decision* is advisory.
* It does NOT duplicate the coverage-recovery mapping. It consumes the Phase 1
  canonical mapping via :func:`system_incidents.coverage_recovery_authority`.

Fail-closed posture
-------------------

Anything not provably healthy blocks activation: incomplete coverage, uncertain
exposure, a silent managed holding, invalid protection geometry, a current
pricing or position-verification gap, malformed/unproven evidence, a blocking
incident, an unknown incident, an auth/connectivity/rate-limit incident, or any
incident without an exact proven recovery predicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Collection, Mapping, Sequence

from app.services.protection_health import (
    _STATUS_ABSENT,
    _STATUS_MANAGED,
    _STATUS_UNMANAGED,
    _known_direction,
    _positive_finite,
    _protection_geometry_proven,
)
from app.services.system_incidents import (
    RecoveryAuthority,
    SystemIncidentScope,
    coverage_recovery_authority,
)

#: Readiness states. ``READY`` is the only state that permits activation.
STATE_READY = "READY"
STATE_BLOCKED = "BLOCKED"

#: Blocking reason codes. Every one of these withholds activation.
BLOCK_COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
BLOCK_EXPOSURE_UNCERTAIN = "EXPOSURE_UNCERTAIN"
BLOCK_SILENT_HOLDING = "SILENT_HOLDING"
BLOCK_GEOMETRY_INVALID = "GEOMETRY_INVALID"
BLOCK_PRICING_GAP = "PRICING_GAP"
BLOCK_POSITION_VERIFICATION_GAP = "POSITION_VERIFICATION_GAP"
BLOCK_EVIDENCE_MALFORMED = "EVIDENCE_MALFORMED"
BLOCK_INCIDENT_OPEN = "INCIDENT_OPEN"
BLOCK_INCIDENT_UNKNOWN = "INCIDENT_UNKNOWN"
BLOCK_INCIDENT_UNPROVEN = "INCIDENT_UNPROVEN"
#: The durable incident store could not be read/proven at all. This is distinct
#: from "no incident": an unreadable store is never an empty incident set.
BLOCK_INCIDENT_UNREADABLE = "INCIDENT_UNREADABLE"

#: Advisory reason codes. These are visible but do not withhold activation.
ADVISORY_UNMANAGED_EXPOSURE = "UNMANAGED_EXPOSURE"
ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT = "CANDIDATE_RECOVERABLE_INCIDENT"

#: The only scopes that may be candidate-recoverable, keyed by the canonical
#: Phase 1 coverage authority. Any other scope is blocking.
_CANDIDATE_RECOVERABLE_AUTHORITIES: frozenset[RecoveryAuthority] = frozenset(
    {
        RecoveryAuthority.PRICING_COVERAGE,
        RecoveryAuthority.POSITION_COVERAGE,
    }
)

#: Scope -> blocking reason code when the scope's predicate is not proven.
_SCOPE_BLOCKING_REASON: dict[str, str] = {
    SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value: BLOCK_PRICING_GAP,
    SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value: (
        BLOCK_POSITION_VERIFICATION_GAP
    ),
}

#: Every scope the canonical enum recognizes. Used to distinguish a recognized
#: non-coverage scope (auth/connectivity/rate-limit) from a completely unknown
#: scope, without maintaining a second hand-written list.
_KNOWN_SCOPES: frozenset[str] = frozenset(scope.value for scope in SystemIncidentScope)


@dataclass(frozen=True)
class EvidenceShadowReadiness:
    """One deterministic EVIDENCE_SHADOW readiness decision."""

    ready: bool
    blocking_reason_codes: tuple[str, ...]
    advisory_reason_codes: tuple[str, ...]
    unmanaged_exposures: tuple[str, ...] = ()
    candidate_recoverable_incidents: tuple[str, ...] = ()
    blocking_incidents: tuple[str, ...] = ()
    coverage_complete: bool = False
    uncertain_exposures: tuple[str, ...] = ()
    silent_holdings: tuple[str, ...] = ()
    geometry_invalid_exposures: tuple[str, ...] = ()

    @property
    def state(self) -> str:
        return STATE_READY if self.ready else STATE_BLOCKED

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "ready": self.ready,
            "blocking_reason_codes": list(self.blocking_reason_codes),
            "advisory_reason_codes": list(self.advisory_reason_codes),
            "unmanaged_exposures": list(self.unmanaged_exposures),
            "candidate_recoverable_incidents": list(
                self.candidate_recoverable_incidents
            ),
            "blocking_incidents": list(self.blocking_incidents),
            "coverage_complete": self.coverage_complete,
            "uncertain_exposures": list(self.uncertain_exposures),
            "silent_holdings": list(self.silent_holdings),
            "geometry_invalid_exposures": list(self.geometry_invalid_exposures),
        }


def _scope_of(incident: Any) -> str:
    """Return the canonical scope string for an incident-shaped object.

    Accepts a mapping (``{"scope": ...}``) or an object with a ``scope``
    attribute. Returns an empty string when the scope cannot be read, which the
    caller treats as unknown/blocking.
    """

    if isinstance(incident, Mapping):
        raw = incident.get("scope")
    else:
        raw = getattr(incident, "scope", None)
    return str(getattr(raw, "value", raw) or "")


def _incident_key_of(incident: Any) -> str:
    if isinstance(incident, Mapping):
        raw = incident.get("incident_key") or incident.get("scope")
    else:
        raw = getattr(incident, "incident_key", None) or getattr(
            incident, "scope", None
        )
    return str(getattr(raw, "value", raw) or "")


def _coverage_predicate_proven(
    scope: str,
    *,
    coverage_complete: bool,
    current_degraded_scopes: Collection[str] | None,
) -> bool:
    """Whether the canonical coverage-recovery predicate is proven for ``scope``.

    This mirrors, exactly, the predicate the active-trade monitor uses to close a
    coverage-owned incident (see ``_reconcile_system_incident_recovery``):

    1. the scope resolves through the canonical Phase-1
       :func:`coverage_recovery_authority` to a coverage authority, AND
    2. candidate coverage is exactly ``True``, AND
    3. the scope is *not* in the current cycle's degraded set.

    ``current_degraded_scopes`` must be a *proven* collection: ``None`` means the
    current degradation evidence is unproven, so the predicate is not proven and
    the incident remains blocking. An empty collection means "proven: no relevant
    scope is currently degraded". No incident age, id or free-form reason text is
    consulted.
    """

    authority = coverage_recovery_authority(scope)
    if authority is None or authority not in _CANDIDATE_RECOVERABLE_AUTHORITIES:
        return False
    if coverage_complete is not True:
        return False
    if current_degraded_scopes is None:
        return False
    return scope not in current_degraded_scopes


def evaluate_evidence_shadow_readiness(
    exposures: Sequence[Any] = (),
    *,
    coverage_complete: bool | None = None,
    open_incidents: Sequence[Any] | None = (),
    current_degraded_scopes: Collection[str] | None = None,
) -> EvidenceShadowReadiness:
    """Decide whether the EVIDENCE_SHADOW profile may be activated.

    ``exposures`` are ``ResolvedExposure``-shaped (``status``, ``symbol``,
    ``trade``). ``coverage_complete`` is the caller's coverage verdict; only
    ``True`` proves coverage. ``open_incidents`` are incident-shaped objects or
    mappings carrying a ``scope``; ``None`` means the durable incident evidence
    could not be read/proven, which blocks (it is never treated as an empty
    incident set). ``current_degraded_scopes`` is the *proven* set of scopes
    degraded in the current candidate observation; ``None`` means that evidence
    is unproven and any coverage-owned incident stays blocking.

    Fails closed: anything not provably healthy blocks activation. A known
    external ``VERIFIED_UNMANAGED`` holding is advisory only -- it stays visible
    and stays part of strict F11, but does not block this shadow decision.
    """

    coverage_proven = coverage_complete is True
    silent: list[str] = []
    geometry_invalid: list[str] = []
    unmanaged: list[str] = []
    uncertain: list[str] = []
    blocking: list[str] = []
    advisory: list[str] = []

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
                silent.append(symbol)
            elif _protection_geometry_proven(trade):
                continue
            else:
                geometry_invalid.append(symbol)
        elif status == _STATUS_UNMANAGED:
            unmanaged.append(symbol)
        elif status == _STATUS_ABSENT:
            continue
        else:
            uncertain.append(symbol)

    if not coverage_proven:
        blocking.append(BLOCK_COVERAGE_INCOMPLETE)
    if silent:
        blocking.append(BLOCK_SILENT_HOLDING)
    if geometry_invalid:
        blocking.append(BLOCK_GEOMETRY_INVALID)
    if uncertain:
        blocking.append(BLOCK_EXPOSURE_UNCERTAIN)
    if unmanaged:
        advisory.append(ADVISORY_UNMANAGED_EXPOSURE)

    candidate_recoverable: list[str] = []
    blocking_incidents: list[str] = []
    if open_incidents is None:
        # The durable incident evidence could not be read/proven. That is not
        # "no incident": block rather than decide on absent evidence.
        blocking.append(BLOCK_INCIDENT_UNREADABLE)
    for incident in open_incidents or ():
        scope = _scope_of(incident)
        key = _incident_key_of(incident) or scope
        if not scope:
            # Missing/malformed scope: fail closed as malformed evidence.
            blocking_incidents.append(key)
            blocking.append(BLOCK_EVIDENCE_MALFORMED)
            continue
        if scope not in _KNOWN_SCOPES:
            # A scope the canonical enum does not recognize: unknown, blocking.
            blocking_incidents.append(key)
            blocking.append(BLOCK_INCIDENT_UNKNOWN)
            continue
        authority = coverage_recovery_authority(scope)
        if authority is None or authority not in _CANDIDATE_RECOVERABLE_AUTHORITIES:
            # A recognized non-coverage scope (auth/connectivity/rate-limit):
            # blocking, but not "unknown".
            blocking_incidents.append(key)
            blocking.append(BLOCK_INCIDENT_OPEN)
            continue
        if _coverage_predicate_proven(
            scope,
            coverage_complete=coverage_proven,
            current_degraded_scopes=current_degraded_scopes,
        ):
            candidate_recoverable.append(key)
            advisory.append(ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT)
        else:
            blocking_incidents.append(key)
            blocking.append(
                _SCOPE_BLOCKING_REASON.get(scope, BLOCK_INCIDENT_UNPROVEN)
            )

    ready = not blocking
    return EvidenceShadowReadiness(
        ready=ready,
        blocking_reason_codes=tuple(sorted(set(blocking))),
        advisory_reason_codes=tuple(sorted(set(advisory))),
        unmanaged_exposures=tuple(sorted(set(unmanaged))),
        candidate_recoverable_incidents=tuple(sorted(set(candidate_recoverable))),
        blocking_incidents=tuple(sorted(set(blocking_incidents))),
        coverage_complete=coverage_proven,
        uncertain_exposures=tuple(sorted(set(uncertain))),
        silent_holdings=tuple(sorted(set(silent))),
        geometry_invalid_exposures=tuple(sorted(set(geometry_invalid))),
    )


__all__ = [
    "ADVISORY_CANDIDATE_RECOVERABLE_INCIDENT",
    "ADVISORY_UNMANAGED_EXPOSURE",
    "BLOCK_COVERAGE_INCOMPLETE",
    "BLOCK_EVIDENCE_MALFORMED",
    "BLOCK_EXPOSURE_UNCERTAIN",
    "BLOCK_GEOMETRY_INVALID",
    "BLOCK_INCIDENT_OPEN",
    "BLOCK_INCIDENT_UNKNOWN",
    "BLOCK_INCIDENT_UNPROVEN",
    "BLOCK_INCIDENT_UNREADABLE",
    "BLOCK_POSITION_VERIFICATION_GAP",
    "BLOCK_PRICING_GAP",
    "BLOCK_SILENT_HOLDING",
    "EvidenceShadowReadiness",
    "STATE_BLOCKED",
    "STATE_READY",
    "evaluate_evidence_shadow_readiness",
]
