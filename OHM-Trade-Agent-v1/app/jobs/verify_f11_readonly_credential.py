"""Read-only F11 protection-credential verification.

Prints one bounded ``OPIP_F11_READONLY_*`` marker per line and exits 0 only when
ALL FIVE facts below hold in the same invocation:

1. ``GetApiKeyInfo`` authenticates and reports the dedicated key name;
2. its scopes are exactly ``query-funds`` + ``query-open-trades``;
3. its IP allowlist is exactly the confirmed Trading droplet;
4. ``Balance`` succeeds;
5. ``OpenPositions`` succeeds.

``OPIP_F11_READONLY_VERDICT`` is therefore the *whole* verdict: it reads ``PASS``
only when every one of the five holds, and ``FAIL`` otherwise. The identity
audit is reported separately as ``OPIP_F11_READONLY_IDENTITY`` so a caller can
still tell "right key, unreadable account" from "wrong key" - but no caller may
treat the identity alone as success. The process exits 0 if and only if the
verdict is ``PASS``, and the deploy controller requires BOTH the exit code and
the explicit ``PASS`` (never a masked exit status).

It is used in three places, all read-only, so the same proof is available to the
release preflight, to the live core, and to the Tradevisor activation gate:

* the deploy controller runs it in the candidate preflight container;
* the deploy controller runs it again inside the recreated core and compares the
  fingerprint, proving preflight/runtime credential parity;
* the Tradevisor gate runs it in the running core before enabling ingress, so an
  approved read-only protection exception is independently verified rather than
  silently ignored.

No key, secret or signed header is ever read into the output: the only
credential-derived value emitted is the one-way fingerprint.
"""

from __future__ import annotations

import re
import sys

from app.exchanges.kraken_private import KrakenPrivateClient
from app.services.kraken_credential_audit import (
    PASS,
    audit_protection_identity,
)

#: Bounded token vocabulary for marker values; anything else becomes NONE.
_TOKEN = re.compile(r"^[A-Za-z0-9._:/-]{1,96}$")
_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")

_EXIT_NOT_VERIFIED = 3

_VERDICT_PASS = "PASS"
_VERDICT_FAIL = "FAIL"

_MARKER_ORDER = (
    "OPIP_F11_READONLY_VERDICT",
    "OPIP_F11_READONLY_IDENTITY",
    "OPIP_F11_READONLY_CREDENTIAL_SOURCE",
    "OPIP_F11_READONLY_REASON",
    "OPIP_F11_READONLY_KEY_NAME",
    "OPIP_F11_READONLY_PERMISSIONS",
    "OPIP_F11_READONLY_IP_ALLOWLIST",
    "OPIP_F11_READONLY_FINGERPRINT",
    "OPIP_F11_READONLY_BALANCE",
    "OPIP_F11_READONLY_OPEN_POSITIONS",
)


def _token(value: object) -> str:
    text = str(value or "").strip()
    return text if _TOKEN.fullmatch(text) else "NONE"


def _code(value: object, limit: int = 64) -> str:
    text = str(value or "").strip()
    head = text.split(":", 1)[0].strip()
    return head[:limit] if _CODE.fullmatch(head[:limit]) else "NONE"


def _permissions(values: object) -> str:
    entries = sorted(
        str(item)
        for item in (values or ())
        if _TOKEN.fullmatch(str(item).strip())
    )
    return ",".join(entries[:20]) or "NONE"


def _credential_source(client: KrakenPrivateClient) -> str:
    """Where the resolved pair came from, bounded to a known token.

    ``NONE`` means no credential is configured at all. ``INVALID`` means a
    configuration fault (for example a half pair), which is NOT the same as
    absent: it must refuse instead of degrading to the unconfigured posture.
    """

    source = str(getattr(client, "credential_source", "") or "").strip()
    if source:
        return _token(source)
    if str(getattr(client, "credential_problem", "") or "").strip():
        return "INVALID"
    return "NONE"


def _markers(client: KrakenPrivateClient | None, reason: str = "") -> dict[str, str]:
    markers: dict[str, str] = {
        "OPIP_F11_READONLY_VERDICT": _VERDICT_FAIL,
        "OPIP_F11_READONLY_IDENTITY": "UNVERIFIED",
        "OPIP_F11_READONLY_CREDENTIAL_SOURCE": (
            _credential_source(client) if client is not None else "UNKNOWN"
        ),
        "OPIP_F11_READONLY_REASON": _code(reason) if reason else "NOT_RUN",
        "OPIP_F11_READONLY_KEY_NAME": "NONE",
        "OPIP_F11_READONLY_PERMISSIONS": "NONE",
        "OPIP_F11_READONLY_IP_ALLOWLIST": "NONE",
        "OPIP_F11_READONLY_FINGERPRINT": "NONE",
        "OPIP_F11_READONLY_BALANCE": "NOT_RUN",
        "OPIP_F11_READONLY_OPEN_POSITIONS": "NOT_RUN",
    }
    return markers


def _verify(client: KrakenPrivateClient) -> tuple[dict[str, str], bool]:
    """Run the five checks. Returns ``(markers, fully_verified)``."""

    audit = audit_protection_identity(client)
    markers = _markers(client)
    markers["OPIP_F11_READONLY_IDENTITY"] = _token(audit.status)
    markers["OPIP_F11_READONLY_REASON"] = _code(audit.reason)

    info = audit.key_info
    identity_ok = audit.status == PASS and info is not None
    if not identity_ok:
        # No identity -> nothing else is measured, and the verdict stays FAIL.
        return markers, False

    markers["OPIP_F11_READONLY_KEY_NAME"] = _token(info.name)
    markers["OPIP_F11_READONLY_PERMISSIONS"] = _permissions(info.permissions)
    allowlist = ",".join(info.ip_allowlist[:20]) or "NONE"
    markers["OPIP_F11_READONLY_IP_ALLOWLIST"] = _token(allowlist)
    markers["OPIP_F11_READONLY_FINGERPRINT"] = _token(
        client.protection_fingerprint(info)
    )

    # Prove the exact two calls F11 makes. Counts are emitted; amounts and asset
    # names are not, so the marker stays non-sensitive. Either failure is a FAIL
    # verdict, never a PASS verdict plus a non-zero exit.
    verified = True
    try:
        markers["OPIP_F11_READONLY_BALANCE"] = f"OK:{len(client.get_balance())}"
    except Exception as exc:  # noqa: BLE001 - an unreadable account is not proof
        markers["OPIP_F11_READONLY_BALANCE"] = f"FAIL:{type(exc).__name__}"
        markers["OPIP_F11_READONLY_REASON"] = "BALANCE_UNPROVEN"
        verified = False
    try:
        markers["OPIP_F11_READONLY_OPEN_POSITIONS"] = (
            f"OK:{len(client.get_open_positions())}"
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable account is not proof
        markers["OPIP_F11_READONLY_OPEN_POSITIONS"] = f"FAIL:{type(exc).__name__}"
        if verified:
            markers["OPIP_F11_READONLY_REASON"] = "OPEN_POSITIONS_UNPROVEN"
        verified = False

    markers["OPIP_F11_READONLY_VERDICT"] = (
        _VERDICT_PASS if verified else _VERDICT_FAIL
    )
    return markers, verified


def main() -> int:
    markers = _markers(None)
    verified = False
    try:
        client = KrakenPrivateClient()
        markers, verified = _verify(client)
    except Exception as exc:  # noqa: BLE001 - an unexpected fault is not proof
        markers = _markers(None)
        markers["OPIP_F11_READONLY_REASON"] = _code(
            f"UNEXPECTED_{type(exc).__name__}"
        )

    for key in _MARKER_ORDER:
        print(f"{key}={markers[key]}")

    if not verified or markers["OPIP_F11_READONLY_VERDICT"] != _VERDICT_PASS:
        return _EXIT_NOT_VERIFIED
    return 0


if __name__ == "__main__":
    sys.exit(main())
