"""Bounded credential authority for the V2 release boundary.

Compose's checked-in ``environment:`` mapping is not the whole authority
surface: ``env_file: .env`` injects the same variable NAMES into the running
core and into every one-shot container started from that service. The release
boundary must therefore judge the credential set the container actually
receives, not only the reviewed Compose file.

This module inspects NAMES only. A value is never read, compared, hashed,
logged, printed, passed as a command argument or written into a receipt.

Owner-approved policy:

* no funded credential present -- PASS; there is no funded authority to bound.
* an authority-bearing NAME present -- REFUSE, always. A read-only audit cannot
  bound a key that carries withdrawal, transfer, funding or order authority.
* an incomplete read-only pair -- REFUSE; a half-set pair cannot be verified.
* a complete read-only pair -- PASS only when the existing Kraken permission
  audit *positively* confirms read-only permissions. FAIL, UNVERIFIED and any
  transport error stop the release; they are never treated as permission.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Callable, Mapping

PASS = "PASS"
REFUSED = "REFUSED"

#: Credential NAMES that carry authority no audit can bound to a query. Their
#: presence is refused outright, whatever their value.
AUTHORITY_BEARING_KEYS: tuple[str, ...] = (
    "KRAKEN_PRIVATE_KEY",
    "KRKN_API_KEY",
    "LIVE_TRADING_KEY",
)

#: The single bounded query-only credential the approved cutover may carry.
READ_ONLY_KEYS: tuple[str, ...] = (
    "KRAKEN_API_KEY",
    "KRAKEN_API_SECRET",
)

_REASON_LIMIT = 200


def _one_line(value: object, limit: int = _REASON_LIMIT) -> str:
    """Bound a reason to one safe line. Names and statuses only, never a value."""
    text = "".join(
        character if character.isalnum() or character in " .,:/_+-" else " "
        for character in str(value or "")
    )
    return " ".join(text.split())[:limit]


def _present_names(environment: Mapping[str, Any]) -> set[str]:
    """Credential NAMES that carry a non-empty value. Values are never returned."""
    return {
        str(name)
        for name, value in environment.items()
        if isinstance(value, str) and value
    }


def live_read_only_audit() -> tuple[str, str]:
    """Run the existing Kraken permission audit and return ``(status, reason)``.

    Imported lazily so the hermetic release-architecture gate keeps working in
    environments that install only its own dependency.
    """
    from app.exchanges.kraken_private import KrakenPrivateClient
    from app.services.kraken_credential_audit import audit_kraken_read_only

    result = audit_kraken_read_only(KrakenPrivateClient())
    return str(result.status), str(result.reason)


def evaluate_credential_authority(
    environment: Mapping[str, Any],
    audit: Callable[[], tuple[str, str]] | None = None,
) -> tuple[str, dict[str, str]]:
    """Return ``(verdict, report)`` for the effective credential names.

    ``report`` carries only non-secret facts and is safe to log or receipt.
    """
    present = _present_names(environment)
    report = {
        "read_only_credential": "ABSENT",
        "authority_bearing": "NONE",
        "audit_status": "NOT_RUN",
        "audit_reason": "no funded credential present",
    }

    authority = sorted(present.intersection(AUTHORITY_BEARING_KEYS))
    if authority:
        report["authority_bearing"] = ",".join(authority)
        report["audit_reason"] = "authority-bearing credential present"
        return REFUSED, report

    read_only = sorted(present.intersection(READ_ONLY_KEYS))
    if not read_only:
        return PASS, report

    report["read_only_credential"] = "PRESENT"
    if len(read_only) != len(READ_ONLY_KEYS):
        report["audit_reason"] = "incomplete read-only credential pair"
        return REFUSED, report

    if audit is None:
        report["audit_status"] = "UNVERIFIED"
        report["audit_reason"] = "live read-only verification is unavailable"
        return REFUSED, report

    try:
        status, reason = audit()
    except Exception as exc:  # noqa: BLE001 - an unverifiable key is not permission
        report["audit_status"] = "UNVERIFIED"
        report["audit_reason"] = f"read-only verification failed: {type(exc).__name__}"
        return REFUSED, report

    report["audit_status"] = _one_line(status)
    report["audit_reason"] = _one_line(reason)
    if str(status) != "PASS":
        return REFUSED, report
    return PASS, report


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001 - fixed CLI surface
    verdict, report = evaluate_credential_authority(
        os.environ,
        live_read_only_audit,
    )
    print(f"CREDENTIAL_AUTHORITY={verdict}")
    for key in ("read_only_credential", "authority_bearing", "audit_status", "audit_reason"):
        print(f"{key.upper()}={_one_line(report[key]) or 'NONE'}")
    return 0 if verdict == PASS else 3


if __name__ == "__main__":
    sys.exit(main())
