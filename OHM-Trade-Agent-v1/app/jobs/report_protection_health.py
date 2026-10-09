"""F11 read-only protection-health report.

Prints one machine-readable protection-health decision for the live exposure
domain. It is a pure observer: it activates nothing, mutates no registry,
reservation or canonical record, and holds no protection or trading authority.

Exactly one JSON document is written to stdout. Humans-readable banners go to
stderr, so the command can be piped into a JSON consumer.

It is genuinely read-only. The exposure resolver is constructed with:

* a non-mutating active-trade loader (the quarantining ``load_json`` path is never
  used, so a corrupt registry cannot be moved or rewritten by this observer);
* a zero materiality floor, so no economically positive holding is hidden from
  the health decision.

It fails closed: unreadable exposure, unproven incident health or unproven
coverage resolve to UNAVAILABLE with admissions suspended rather than healthy.

AC-026 adds a second, separate decision alongside the strict F11 verdict: the
EVIDENCE_SHADOW readiness projection. Both are derived from ONE read-only
observation -- a single exposure resolution and a single durable-incident
read -- so strict protection health and shadow readiness can never disagree
about what was actually observed. Strict F11 semantics are unchanged: this
module never marks an incident recovered and never makes candidate
recoverability part of strict incident health.

Scope note: this report observes the live (Kraken/operator) exposure domain and
the protection-incident verdict. It does not read the Paper-v2 protection
runtime, so a target-domain objection is not observed here; the target admission
seam already fails closed on its own ``new_admissions_allowed`` gate.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import Any

from app.services import system_incidents
from app.services.active_trade_registry import read_active_trades_without_mutation
from app.services.evidence_shadow_readiness import (
    EvidenceShadowReadiness,
    evaluate_evidence_shadow_readiness,
)
from app.services.kraken_exposure_resolver import KrakenExposureResolver
from app.services.protection_health import evaluate_protection_health
from app.services.registry_io import RegistryIOError, read_json_without_quarantine

#: Incident scopes whose open state means protection is not proven. These are the
#: connectivity, pricing and position-verification scopes.
_PROTECTION_INCIDENT_SCOPES = frozenset(
    {
        system_incidents.SystemIncidentScope.KRAKEN_PUBLIC.value,
        system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY.value,
        system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH.value,
        system_incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT.value,
        system_incidents.SystemIncidentScope.KRAKEN_HELD_ASSET_PRICING.value,
        system_incidents.SystemIncidentScope.KRAKEN_POSITION_VERIFICATION.value,
    }
)


@dataclass(frozen=True)
class IncidentProjection:
    """One read-only projection of the durable incident store.

    ``health`` is the strict F11 incident verdict (``True`` no open protection
    incident, ``False`` at least one open, ``None`` unproven/corrupt). The same
    single read also yields ``open_incidents``: the raw unresolved incident
    evidence, or ``None`` when the store could not be read or parsed as a whole.
    An unreadable store is never reported as an empty incident set, and a
    structurally corrupt record is never reported as "no incident".
    """

    health: bool | None
    open_incidents: tuple[Any, ...] | None


def read_incident_projection() -> IncidentProjection:
    """Read the durable incident store once, read-only, failing closed.

    This is the SINGLE incident observation for a report/preflight decision: the
    strict incident verdict and the shadow-readiness incident evidence both come
    from this one projection, so no second, possibly different, read is needed.

    Read-only: uses the non-mutating JSON seam, so a corrupt incident file is
    reported as unproven (``None``) rather than quarantined.
    """

    try:
        payload = read_json_without_quarantine(system_incidents.STATE_FILE)
    except RegistryIOError:
        return IncidentProjection(health=None, open_incidents=None)
    incidents = payload.get("incidents")
    if not isinstance(incidents, dict):
        return IncidentProjection(health=None, open_incidents=None)
    open_rows: list[Any] = []
    health: bool | None = True
    for row in incidents.values():
        if not isinstance(row, dict):
            # A structurally corrupt incident record cannot be proven recovered,
            # and it is not "no incident": the whole projection is unproven.
            return IncidentProjection(health=None, open_incidents=None)
        if str(row.get("state") or "") == system_incidents.STATE_RECOVERED:
            continue
        open_rows.append(row)
        if str(row.get("scope") or "") in _PROTECTION_INCIDENT_SCOPES:
            health = False
    return IncidentProjection(health=health, open_incidents=tuple(open_rows))


def protection_incidents_healthy() -> bool | None:
    """True when no protection incident is open, False when one is, None if unproven.

    Backwards-compatible strict verdict. It delegates to the single read-only
    projection, so it can never cause a second observation of the store.
    """

    return read_incident_projection().health


def _read_only_resolver() -> KrakenExposureResolver:
    return KrakenExposureResolver(
        trade_loader=read_active_trades_without_mutation,
        minimum_unmanaged_notional_usd=0.0,
    )


def _strict_report(
    resolution: Any,
    resolution_error: str,
    incidents_healthy: bool | None,
) -> dict[str, Any]:
    """Derive the strict F11 decision from ONE supplied resolution verdict."""
    if resolution is None:
        report = evaluate_protection_health(
            (), coverage_complete=False, incidents_healthy=incidents_healthy
        ).to_dict()
        report["resolution_error"] = resolution_error
        return report

    report = evaluate_protection_health(
        resolution.exposures,
        coverage_complete=bool(resolution.coverage_complete),
        incidents_healthy=incidents_healthy,
    ).to_dict()
    resolution_reason = getattr(resolution, "reason", "")
    if resolution_reason:
        report["resolution_reason"] = resolution_reason
    return report


def _shadow_readiness(
    resolution: Any,
    projection: IncidentProjection,
) -> EvidenceShadowReadiness:
    """Derive the EVIDENCE_SHADOW decision from the SAME resolution verdict.

    ``resolution.degraded_scopes`` is the same-cycle candidate degradation
    evidence: ``None`` (or a resolution double that does not carry it) is
    unproven and keeps any existing coverage incident blocking. An unreadable
    incident projection is passed through as ``None`` so the decision blocks
    rather than treating the store as empty.
    """
    if resolution is None:
        return evaluate_evidence_shadow_readiness(
            (),
            coverage_complete=None,
            open_incidents=projection.open_incidents,
        )
    coverage = getattr(resolution, "coverage_complete", None)
    return evaluate_evidence_shadow_readiness(
        getattr(resolution, "exposures", ()),
        coverage_complete=None if coverage is None else bool(coverage),
        open_incidents=projection.open_incidents,
        current_degraded_scopes=getattr(resolution, "degraded_scopes", None),
    )


@dataclass(frozen=True)
class ProtectionObservation:
    """One coherent read-only candidate protection observation.

    A single exposure resolution and a single durable-incident read feed BOTH
    the strict F11 report and the EVIDENCE_SHADOW readiness decision. ``report``
    is the strict F11 decision (with the shadow projection attached under
    ``evidence_shadow``); ``shadow`` is the same projection as a typed result.
    """

    report: dict[str, Any]
    incidents_healthy: bool | None
    shadow: EvidenceShadowReadiness


def build_observation() -> ProtectionObservation:
    """Compose one read-only observation: one incident read, one resolution.

    The exposure resolver is invoked exactly once and the incident store is read
    exactly once per observation. Both strict F11 and shadow readiness consume
    that same observation; no independent second observation is taken.
    """
    projection = read_incident_projection()
    try:
        resolution = _read_only_resolver().resolve()
        resolution_error = ""
    except Exception as exc:  # noqa: BLE001 - an unreadable exposure is not healthy
        resolution = None
        resolution_error = f"{type(exc).__name__}: {exc}"

    report = _strict_report(resolution, resolution_error, projection.health)
    shadow = _shadow_readiness(resolution, projection)
    report["evidence_shadow"] = shadow.to_dict()
    return ProtectionObservation(
        report=report,
        incidents_healthy=projection.health,
        shadow=shadow,
    )


def build_report_with_incidents() -> tuple[dict[str, Any], bool | None]:
    """Resolve the exposure once and return ``(report, incidents_healthy)``.

    Backwards-compatible public seam. The incident store is observed exactly
    once, so a caller that also needs the raw verdict cannot cause a second,
    possibly different, observation. The returned report also carries the
    AC-026 ``evidence_shadow`` projection. This evaluates no protection logic of
    its own.
    """
    observation = build_observation()
    return observation.report, observation.incidents_healthy


def build_report() -> dict[str, Any]:
    """Resolve the live exposure and derive the protection decision, read-only."""
    return build_observation().report


def main() -> None:
    # Human-readable banners stay on stderr so stdout is exactly one JSON document.
    print("O'Pip F11 Protection Health - READ ONLY", file=sys.stderr)
    print("Trading authority: NONE", file=sys.stderr)
    print(json.dumps(build_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
