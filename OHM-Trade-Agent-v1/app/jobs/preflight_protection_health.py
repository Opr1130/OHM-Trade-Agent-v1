"""Candidate-version read-only protection preflight.

One JSON document on stdout. It calls the same read-only protection report the
live observer uses (``build_report``), which keeps the non-mutating trade loader,
the zero unmanaged-notional floor, and the non-mutating incident read. This
module does not classify exposure itself and it does not recover, repair, or
clear incidents.

Exit 0 only when that report is the existing HEALTHY decision. Every other
outcome, including an unreadable report, exits 76 so the deploy controller can
refuse before it mutates production.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.jobs.report_protection_health import (
    build_report,
    protection_incidents_healthy,
)
from app.services.protection_health import REASON_HEALTHY, STATE_HEALTHY

_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROFILE = "EVIDENCE_SHADOW"
_REASON_LIMIT = 240
_EXIT_BLOCKED = 76


def _one_line(value: object, limit: int = _REASON_LIMIT) -> str:
    text = "".join(
        ch if ch.isalnum() or ch in " .,:/_+-" else " "
        for ch in str(value or "")
    )
    return " ".join(text.split())[:limit]


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
    """True only for the existing HEALTHY protection decision."""
    return (
        protection.get("state") == STATE_HEALTHY
        and protection.get("admissions_suspended") is False
        and protection.get("coverage_complete") is True
        and protection.get("reason_codes") == [REASON_HEALTHY]
    )


def build_preflight_document(
    report: dict[str, Any],
    *,
    incidents_healthy: bool | None,
    candidate_sha: str,
    release_profile: str,
    checked_at_utc: str,
) -> dict[str, Any]:
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
    profile_ok = release_profile == _PROFILE and bool(_SHA.fullmatch(candidate_sha))
    ready = profile_ok and protection_is_ready(protection)
    if ready:
        reason = "protection is HEALTHY for the candidate release"
    elif not profile_ok:
        reason = "candidate release profile or SHA is not the qualified EVIDENCE_SHADOW target"
    else:
        reason = "protection is not HEALTHY"
    return {
        "schema_version": 1,
        "checked_at_utc": checked_at_utc,
        "candidate_sha": candidate_sha,
        "release_profile": release_profile,
        "read_only": True,
        "protection": protection,
        "incidents": {
            "health": incidents_healthy,
            "open_incident_count": None,
            "open_incidents": None,
        },
        "diagnostics": {"classes": diagnostic_classes(protection)},
        "verdict": {"ready": ready, "reason": reason},
    }


def evaluate_preflight(*, candidate_sha: str, release_profile: str) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        incidents_healthy = protection_incidents_healthy()
        report = build_report()
    except Exception as exc:  # noqa: BLE001 - an unreadable preflight is not ready
        report = {
            "state": "UNAVAILABLE",
            "admissions_suspended": True,
            "coverage_complete": False,
            "reason_codes": ["UNAVAILABLE"],
            "resolution_reason": _one_line(f"{type(exc).__name__}: {exc}"),
        }
        incidents_healthy = None
    return build_preflight_document(
        report,
        incidents_healthy=incidents_healthy,
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
