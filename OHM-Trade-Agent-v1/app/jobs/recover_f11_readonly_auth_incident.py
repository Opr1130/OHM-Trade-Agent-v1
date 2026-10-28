"""Operator-owned recovery of the durable ``KRAKEN:READ_ONLY_AUTH`` incident.

Why a dedicated operation exists
--------------------------------
``KRAKEN:READ_ONLY_AUTH`` is deliberately **not** monitor-owned: its recovery
authority is :data:`system_incidents.RecoveryAuthority.AUTH_CONFIG`, because only
a fresh authoritative credential probe can prove that the credential problem is
actually gone. The active-trade monitor may not close it, and editing or deleting
the durable row by hand would fabricate evidence.

This module is that owner-run operation. It performs the smallest existing
sanctioned transition - :func:`system_incidents.observe_recovery` - and only
after it has *itself* proven, in this same invocation, that:

1. ``GetApiKeyInfo`` authenticates the dedicated protection key;
2. the key name, the exact two scopes and the confirmed droplet allowlist hold;
3. ``Balance`` succeeds;
4. ``OpenPositions`` succeeds;
5. the durable row being closed is exactly this scope/class.

It is idempotent: with no open row it reports ``ALREADY_RECOVERED`` and writes
nothing. It only ever closes its own incident key, so an unrelated open incident
cannot be affected. Every failure path leaves the row ``OPEN``/``CHANGED`` and
admissions suspended - a recovery is never claimed from unproven evidence.

Run without ``--apply`` to get the plan and the fresh proof; ``--apply`` performs
the single transition. It holds no trading authority and prints no secret.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timezone

from app.exchanges.kraken_private import KrakenPrivateClient
from app.services import system_incidents
from app.services.kraken_credential_audit import PASS, audit_protection_identity

_TOKEN = re.compile(r"^[A-Za-z0-9._:/-]{1,96}$")
_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")

_EXIT_REFUSED = 3
_EXIT_PLAN = 77

_SCOPE = system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH
_CLASS = system_incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE
_KEY = system_incidents.incident_key(_SCOPE)
_OPEN_STATES = (
    system_incidents.STATE_OPEN,
    system_incidents.STATE_CHANGED,
    system_incidents.STATE_ESCALATED,
)


def _token(value: object) -> str:
    text = str(value or "").strip()
    return text if _TOKEN.fullmatch(text) else "NONE"


def _row() -> dict[str, object] | None:
    """The active durable row for this incident key, or ``None``."""

    for row in system_incidents.read_incidents(
        include_recovered=True, include_archived=False
    ):
        if str(row.get("incident_key") or "") == _KEY:
            return row
    return None


def _proof() -> tuple[bool, str, dict[str, str]]:
    """Fresh, non-cached proof. Returns ``(proven, reason, markers)``."""

    client = KrakenPrivateClient()
    audit = audit_protection_identity(client)
    markers = {
        "OPIP_F11_AUTH_PROOF_KEY_NAME": "NONE",
        "OPIP_F11_AUTH_PROOF_FINGERPRINT": "NONE",
        "OPIP_F11_AUTH_PROOF_BALANCE": "NOT_RUN",
        "OPIP_F11_AUTH_PROOF_OPEN_POSITIONS": "NOT_RUN",
    }
    if audit.status != PASS or audit.key_info is None:
        return False, f"IDENTITY_{audit.status}", markers

    info = audit.key_info
    markers["OPIP_F11_AUTH_PROOF_KEY_NAME"] = _token(info.name)
    markers["OPIP_F11_AUTH_PROOF_FINGERPRINT"] = _token(
        client.protection_fingerprint(info)
    )
    try:
        markers["OPIP_F11_AUTH_PROOF_BALANCE"] = f"OK:{len(client.get_balance())}"
    except Exception as exc:  # noqa: BLE001 - an unreadable account is not proof
        markers["OPIP_F11_AUTH_PROOF_BALANCE"] = f"FAIL:{type(exc).__name__}"
        return False, "BALANCE_UNPROVEN", markers
    try:
        markers["OPIP_F11_AUTH_PROOF_OPEN_POSITIONS"] = (
            f"OK:{len(client.get_open_positions())}"
        )
    except Exception as exc:  # noqa: BLE001
        markers["OPIP_F11_AUTH_PROOF_OPEN_POSITIONS"] = f"FAIL:{type(exc).__name__}"
        return False, "OPEN_POSITIONS_UNPROVEN", markers

    return True, "PROOF_PASS", markers


def _evidence(markers: dict[str, str]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "AUTH_CONFIG recovery: dedicated F11 read-only credential proven "
        f"(key={markers['OPIP_F11_AUTH_PROOF_KEY_NAME']} "
        f"fingerprint={markers['OPIP_F11_AUTH_PROOF_FINGERPRINT']} "
        f"balance={markers['OPIP_F11_AUTH_PROOF_BALANCE']} "
        f"open_positions={markers['OPIP_F11_AUTH_PROOF_OPEN_POSITIONS']} "
        f"at={stamp})"
    )



def run(*, apply: bool) -> int:
    proven, reason, markers = _proof()
    row = _row()
    state_before = _token(row.get("state")) if row else "NONE"

    print(f"OPIP_F11_AUTH_INCIDENT_KEY={_KEY}")
    print(f"OPIP_F11_AUTH_INCIDENT_STATE_BEFORE={state_before}")
    print(f"OPIP_F11_AUTH_PROOF={'PASS' if proven else 'FAIL'}")
    print(f"OPIP_F11_AUTH_REASON={reason}")
    for key in (
        "OPIP_F11_AUTH_PROOF_KEY_NAME",
        "OPIP_F11_AUTH_PROOF_FINGERPRINT",
        "OPIP_F11_AUTH_PROOF_BALANCE",
        "OPIP_F11_AUTH_PROOF_OPEN_POSITIONS",
    ):
        print(f"{key}={markers[key]}")

    if not proven:
        # No proof, no transition: the row keeps blocking strict F11.
        print("OPIP_F11_AUTH_RECOVERY=REFUSED")
        print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_before}")
        return _EXIT_REFUSED

    if row is None:
        # Nothing to close on this host: no evidence is invented either way.
        print("OPIP_F11_AUTH_RECOVERY=NO_OPEN_INCIDENT")
        print("OPIP_F11_AUTH_INCIDENT_STATE_AFTER=NONE")
        return 0

    if state_before == system_incidents.STATE_RECOVERED:
        # Idempotent: already a fact, so no second recovery is claimed.
        print("OPIP_F11_AUTH_RECOVERY=ALREADY_RECOVERED")
        print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_before}")
        return 0

    if str(row.get("incident_class") or "") != _CLASS.value:
        print("OPIP_F11_AUTH_RECOVERY=REFUSED")
        print("OPIP_F11_AUTH_REASON=INCIDENT_CLASS_MISMATCH")
        print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_before}")
        return _EXIT_REFUSED

    if state_before not in _OPEN_STATES:
        print("OPIP_F11_AUTH_RECOVERY=REFUSED")
        print("OPIP_F11_AUTH_REASON=INCIDENT_STATE_UNEXPECTED")
        print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_before}")
        return _EXIT_REFUSED

    if not apply:
        print("OPIP_F11_AUTH_RECOVERY=PLAN")
        print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_before}")
        return _EXIT_PLAN

    decision = system_incidents.observe_recovery(
        incident_class=_CLASS,
        scope=_SCOPE,
        evidence_source=system_incidents.RecoveryAuthority.AUTH_CONFIG,
        evidence=_evidence(markers),
        authoritative=True,
    )

    after = _row()
    state_after = _token(after.get("state")) if after else "NONE"
    recovered = state_after == system_incidents.STATE_RECOVERED
    print(f"OPIP_F11_AUTH_DECISION={_token(decision.reason) or 'NONE'}")
    print(f"OPIP_F11_AUTH_INCIDENT_STATE_AFTER={state_after}")
    print("OPIP_F11_AUTH_RECOVERY=" + ("RECOVERED" if recovered else "REFUSED"))
    return 0 if recovered else _EXIT_REFUSED


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recover the durable KRAKEN:READ_ONLY_AUTH incident from fresh, "
            "authoritative F11 read-only credential evidence (AUTH_CONFIG)."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the single incident transition (default: plan only)",
    )
    args = parser.parse_args(argv)
    return run(apply=bool(args.apply))


if __name__ == "__main__":
    sys.exit(main())
