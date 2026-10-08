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

Scope note: this report observes the live (Kraken/operator) exposure domain and
the protection-incident verdict. It does not read the Paper-v2 protection
runtime, so a target-domain objection is not observed here; the target admission
seam already fails closed on its own ``new_admissions_allowed`` gate.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from app.services import system_incidents
from app.services.active_trade_registry import read_active_trades_without_mutation
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


def protection_incidents_healthy() -> bool | None:
    """True when no protection incident is open, False when one is, None if unproven.

    Read-only: uses the non-mutating JSON seam, so a corrupt incident file is
    reported as unproven (None) rather than quarantined.
    """
    try:
        payload = read_json_without_quarantine(system_incidents.STATE_FILE)
    except RegistryIOError:
        return None
    incidents = payload.get("incidents")
    if not isinstance(incidents, dict):
        return None
    for row in incidents.values():
        if not isinstance(row, dict):
            # A structurally corrupt incident record cannot be proven recovered.
            return None
        if (
            str(row.get("scope") or "") in _PROTECTION_INCIDENT_SCOPES
            and str(row.get("state") or "") != system_incidents.STATE_RECOVERED
        ):
            return False
    return True


def _read_only_resolver() -> KrakenExposureResolver:
    return KrakenExposureResolver(
        trade_loader=read_active_trades_without_mutation,
        minimum_unmanaged_notional_usd=0.0,
    )


def _resolve_report(incidents_healthy: bool | None) -> dict[str, Any]:
    """Derive the protection decision from ONE supplied incident verdict."""
    try:
        resolution = _read_only_resolver().resolve()
    except Exception as exc:  # noqa: BLE001 - an unreadable exposure is not healthy
        return evaluate_protection_health(
            (), coverage_complete=False, incidents_healthy=incidents_healthy
        ).to_dict() | {"resolution_error": f"{type(exc).__name__}: {exc}"}

    report = evaluate_protection_health(
        resolution.exposures,
        coverage_complete=bool(resolution.coverage_complete),
        incidents_healthy=incidents_healthy,
    ).to_dict()
    resolution_reason = getattr(resolution, "reason", "")
    if resolution_reason:
        report["resolution_reason"] = resolution_reason
    return report


def build_report_with_incidents() -> tuple[dict[str, Any], bool | None]:
    """Resolve the exposure once and return ``(report, incidents_healthy)``.

    The incident store is observed exactly once, so a caller that also needs the
    raw verdict cannot cause a second, possibly different, observation. This is
    the shared read-only seam used by both the live report and the release
    preflight; it evaluates no protection logic of its own.
    """
    incidents_healthy = protection_incidents_healthy()
    return _resolve_report(incidents_healthy), incidents_healthy


def build_report() -> dict[str, Any]:
    """Resolve the live exposure and derive the protection decision, read-only."""
    report, _ = build_report_with_incidents()
    return report


def main() -> None:
    # Human-readable banners stay on stderr so stdout is exactly one JSON document.
    print("O'Pip F11 Protection Health - READ ONLY", file=sys.stderr)
    print("Trading authority: NONE", file=sys.stderr)
    print(json.dumps(build_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
