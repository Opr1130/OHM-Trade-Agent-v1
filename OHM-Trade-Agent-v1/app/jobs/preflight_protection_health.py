"""Candidate-version read-only protection preflight.

One JSON document on stdout. It composes the shared read-only protection
observation (``build_observation``), which keeps the non-mutating trade loader,
the zero unmanaged-notional floor, the single non-mutating incident read, the
non-mutating exposure resolver, the strict F11 decision and the AC-026
EVIDENCE_SHADOW readiness decision. This module does not classify exposure
itself and it does not close, clear, or settle incidents.

For the qualified ``EVIDENCE_SHADOW`` profile the release decision is the
EVIDENCE_SHADOW readiness verdict, NOT strict F11. Strict F11 still runs and is
reported, with its own fields and markers unchanged, so a blocked owner can see
why strict protection is non-healthy even when shadow readiness is READY.

Exit 0 only when the candidate profile/SHA is qualified AND EVIDENCE_SHADOW
readiness is READY. Every other outcome, including an unreadable observation,
exits 76 so the deploy controller can refuse before it mutates production.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any, Iterable

from app.jobs.report_protection_health import (
    ProtectionObservation,
    build_observation,
)
from app.services.evidence_shadow_readiness import evaluate_evidence_shadow_readiness
from app.services.protection_health import REASON_HEALTHY, STATE_HEALTHY

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROFILE = "EVIDENCE_SHADOW"
_REASON_LIMIT = 240
_EXIT_BLOCKED = 76

#: Bound for the comma-separated marker fields. Symbols are exposure identities
#: already present in the protection report; the marker only makes them visible
#: through the bounded deploy receipt, so it is capped in both item count and
#: total length and it drops anything that is not a plain exposure symbol.
_SYMBOL = re.compile(r"^[A-Za-z0-9._/-]{1,24}$")
#: Reason codes are the bounded ``UPPER_SNAKE`` machine vocabulary.
_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
#: Incident identities are ``SYSTEM_HEALTH:<scope>`` keys; they carry a colon and
#: are never free-form incident reason text.
_INCIDENT_IDENTITY = re.compile(r"^[A-Za-z0-9._:/-]{1,96}$")
_MARKER_MAX_ITEMS = 20
_MARKER_MAX_LEN = 400
_MARKER_EMPTY = "NONE"


def _one_line(value: object, limit: int = _REASON_LIMIT) -> str:
    text = "".join(
        ch if ch.isalnum() or ch in " .,:/_+-" else " "
        for ch in str(value or "")
    )
    return " ".join(text.split())[:limit]


def _bounded(cleaned: list[str], *, strip_chars: str) -> str:
    if not cleaned:
        return _MARKER_EMPTY
    joined = ",".join(cleaned[:_MARKER_MAX_ITEMS])
    if len(joined) > _MARKER_MAX_LEN:
        joined = joined[:_MARKER_MAX_LEN].rstrip(strip_chars)
    return joined or _MARKER_EMPTY


def format_marker_symbols(symbols: Iterable[object] | None) -> str:
    """Bounded, sanitized comma-separated exposure symbols for the deploy receipt.

    Only plain exposure symbols survive. The result is deterministic, capped, and
    contains no whitespace, so it is safe to emit as a single deploy-log marker.
    """
    cleaned: list[str] = []
    for raw in symbols or ():
        text = str(raw or "").strip()
        if not _SYMBOL.fullmatch(text) or text in cleaned:
            continue
        cleaned.append(text)
    return _bounded(cleaned, strip_chars=".,/-_")


def format_marker_codes(codes: Iterable[object] | None) -> str:
    """Bounded, sorted, machine-vocabulary reason codes for the receipt."""
    cleaned: list[str] = []
    for raw in codes or ():
        text = str(raw or "").strip()
        if not _CODE.fullmatch(text) or text in cleaned:
            continue
        cleaned.append(text)
    return _bounded(sorted(cleaned), strip_chars=".,/-_")


def format_marker_incidents(identities: Iterable[object] | None) -> str:
    """Bounded incident identities (keys) for the receipt.

    Incident identities keep their canonical ``SYSTEM_HEALTH:<scope>`` shape.
    Only bounded identities survive; no free-form incident reason text is ever
    emitted into a parser-oriented marker.
    """
    cleaned: list[str] = []
    for raw in identities or ():
        text = str(raw or "").strip()
        if not _INCIDENT_IDENTITY.fullmatch(text) or text in cleaned:
            continue
        cleaned.append(text)
    return _bounded(sorted(cleaned), strip_chars=".,/-_:")


def format_marker_token(value: object) -> str:
    """A single bounded machine token (for example ``READY`` / ``UNAVAILABLE``)."""
    text = str(value or "").strip()
    return text if _CODE.fullmatch(text) else _MARKER_EMPTY


def format_incident_health(value: bool | None) -> str:
    """``true`` / ``false`` / ``UNPROVEN`` for the bounded incident-health marker."""
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "UNPROVEN"


def diagnostic_classes(report: dict[str, Any]) -> list[str]:
    """Owner-facing labels derived from the existing report. Not a second gate."""
    codes = {str(code) for code in report.get("reason_codes") or []}
    blob = " ".join(
        str(report.get(key) or "")
        for key in ("resolution_reason", "resolution_error")
    ).lower()
    classes: list[str] = []
    if "EXPOSURE_COVERAGE_INCOMPLETE" in codes or "pricing unavailable" in blob:
        classes.append("PRICING_OR_COVERAGE")
    if "UNMANAGED_EXPOSURE_REQUIRES_REVIEW" in codes:
        classes.append("UNMANAGED_EXPOSURE")
    if "EXPOSURE_VERIFICATION_UNCERTAIN" in codes:
        classes.append("UNCERTAIN_EXPOSURE")
    if (
        "PROTECTION_INCIDENT_OPEN" in codes
        or "PROTECTION_INCIDENT_HEALTH_UNPROVEN" in codes
    ):
        classes.append("OPEN_OR_UNPROVEN_INCIDENT")
    if (
        "private" in blob
        or "credentials" in blob
        or "account state unavailable" in blob
        or "direct snapshot unavailable" in blob
    ):
        classes.append("PRIVATE_KRAKEN_READ_FAILURE")
    if "public pair discovery" in blob:
        classes.append("PUBLIC_PAIR_DISCOVERY_FAILURE")
    if "active trade registry" in blob:
        classes.append("ACTIVE_TRADE_REGISTRY_FAILURE")
    return classes


def protection_is_ready(protection: dict[str, Any]) -> bool:
    """True only for the existing HEALTHY strict F11 decision."""
    return (
        protection.get("state") == STATE_HEALTHY
        and protection.get("admissions_suspended") is False
        and protection.get("coverage_complete") is True
        and protection.get("reason_codes") == [REASON_HEALTHY]
    )


def _strict_projection(report: dict[str, Any]) -> dict[str, Any]:
    protection = {
        "state": report.get("state"),
        "admissions_suspended": report.get("admissions_suspended"),
        "coverage_complete": report.get("coverage_complete"),
        "reason_codes": list(report.get("reason_codes") or []),
        "silent_holdings": list(report.get("silent_holdings") or []),
        "geometry_invalid_exposures": list(report.get("geometry_invalid_exposures") or []),
        "unmanaged_exposures": list(report.get("unmanaged_exposures") or []),
        "uncertain_exposures": list(report.get("uncertain_exposures") or []),
        "resolution_reason": _one_line(report.get("resolution_reason")),
    }
    if report.get("resolution_error"):
        protection["resolution_error"] = _one_line(report.get("resolution_error"))
    return protection


def build_preflight_document(
    observation: ProtectionObservation,
    *,
    candidate_sha: str,
    release_profile: str,
    checked_at_utc: str,
) -> dict[str, Any]:
    """Project ONE read-only observation into the bounded preflight receipt."""
    strict = _strict_projection(observation.report)
    shadow = observation.shadow
    incidents_healthy = observation.incidents_healthy

    profile_ok = release_profile == _PROFILE and bool(_SHA.fullmatch(candidate_sha))
    ready = profile_ok and shadow.ready
    if ready:
        reason = "EVIDENCE_SHADOW readiness is READY for the candidate release"
    elif not profile_ok:
        reason = "candidate release profile or SHA is not the qualified EVIDENCE_SHADOW target"
    else:
        reason = "EVIDENCE_SHADOW readiness is BLOCKED"

    return {
        "schema_version": 1,
        "checked_at_utc": checked_at_utc,
        "candidate_sha": candidate_sha,
        "release_profile": release_profile,
        "read_only": True,
        # Legacy strict F11 projection. Its meaning is unchanged: these fields
        # are strict protection health, never shadow readiness.
        "protection": strict,
        # Explicit AC-026 strict projection, derived from the same strict report.
        "strict_f11": {
            "state": strict["state"],
            "healthy": strict["state"] == STATE_HEALTHY,
            "admissions_suspended": strict["admissions_suspended"],
            "coverage_complete": strict["coverage_complete"],
            "reason_codes": list(strict["reason_codes"]),
        },
        # AC-026 EVIDENCE_SHADOW readiness projection.
        "evidence_shadow": shadow.to_dict(),
        "incidents": {
            "health": incidents_healthy,
            "open_incident_count": None,
            "open_incidents": None,
        },
        "markers": {
            # Legacy AC-024 markers: strictly strict-protection facts.
            "unmanaged_exposures": format_marker_symbols(strict["unmanaged_exposures"]),
            "uncertain_exposures": format_marker_symbols(strict["uncertain_exposures"]),
            "silent_holdings": format_marker_symbols(strict["silent_holdings"]),
            "incident_health": format_incident_health(incidents_healthy),
            # Explicit AC-026 markers.
            "strict_f11_state": format_marker_token(strict["state"]),
            "strict_f11_reason_codes": format_marker_codes(strict["reason_codes"]),
            "evidence_shadow_readiness": format_marker_token(shadow.state),
            "evidence_shadow_blocking_reason_codes": format_marker_codes(
                shadow.blocking_reason_codes
            ),
            "evidence_shadow_advisory_reason_codes": format_marker_codes(
                shadow.advisory_reason_codes
            ),
            "evidence_shadow_unmanaged_exposures": format_marker_symbols(
                shadow.unmanaged_exposures
            ),
            "evidence_shadow_candidate_recoverable_incidents": format_marker_incidents(
                shadow.candidate_recoverable_incidents
            ),
            "evidence_shadow_blocking_incidents": format_marker_incidents(
                shadow.blocking_incidents
            ),
        },
        "diagnostics": {"classes": diagnostic_classes(strict)},
        "verdict": {"ready": ready, "reason": reason},
    }


def _unavailable_observation(error: str) -> ProtectionObservation:
    """Fail-closed observation for an unreadable preflight composition."""
    report = {
        "state": "UNAVAILABLE",
        "admissions_suspended": True,
        "coverage_complete": False,
        "reason_codes": ["UNAVAILABLE"],
        "silent_holdings": [],
        "geometry_invalid_exposures": [],
        "unmanaged_exposures": [],
        "uncertain_exposures": [],
        "resolution_reason": _one_line(error),
    }
    shadow = evaluate_evidence_shadow_readiness(
        (),
        coverage_complete=None,
        open_incidents=None,
    )
    report["evidence_shadow"] = shadow.to_dict()
    return ProtectionObservation(
        report=report,
        incidents_healthy=None,
        shadow=shadow,
    )


def evaluate_preflight(*, candidate_sha: str, release_profile: str) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        # One read-only observation drives strict F11 and shadow readiness.
        observation = build_observation()
    except Exception as exc:  # noqa: BLE001 - an unreadable preflight is not ready
        observation = _unavailable_observation(f"{type(exc).__name__}: {exc}")
    return build_preflight_document(
        observation,
        candidate_sha=candidate_sha,
        release_profile=release_profile,
        checked_at_utc=checked_at,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--release-profile", required=True)
    args = parser.parse_args(argv)
    document = evaluate_preflight(
        candidate_sha=str(args.candidate_sha),
        release_profile=str(args.release_profile),
    )
    print("O'Pip protection preflight - READ ONLY", file=sys.stderr)
    print("Trading authority: NONE", file=sys.stderr)
    print(json.dumps(document, indent=2, sort_keys=True))
    return 0 if document["verdict"]["ready"] is True else _EXIT_BLOCKED


if __name__ == "__main__":
    sys.exit(main())
