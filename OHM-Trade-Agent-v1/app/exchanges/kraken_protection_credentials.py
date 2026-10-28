"""The one approved F11 read-only credential exception.

Why this module exists
----------------------
The core container must never receive a *funded* exchange credential: that is
the Tradevisor isolation contract (``release_profiles.FUNDED_CREDENTIAL_KEYS``),
and it is correct. But F11 protection health is not optional either: it reads the
live account through ``Balance`` and ``OpenPositions``, and without a credential
it can only report ``UNAVAILABLE`` with admissions suspended.

The two requirements are reconciled here, and only here, by a name pair that is
deliberately NOT one of the funded names and is not configurable:

``OPIP_F11_KRAKEN_READONLY_API_KEY`` / ``OPIP_F11_KRAKEN_READONLY_API_SECRET``

Resolution rules (fail closed, never widen):

* both protection names present  -> use exactly that pair;
* exactly one present            -> refuse; a half pair is never completed from
  anywhere else, so a rotation can never silently mix two credentials;
* neither present, legacy funded pair present -> use the legacy pair, which is
  how a Git-worktree host, ``SAFE_BASELINE`` and the local test seam keep
  working unchanged;
* neither pair, or one legacy name -> no credential (the pre-existing
  "credentials are not configured" posture), never a guessed substitute.

The names are literals. There is no ``*_API_KEY``-suffix alias search, so no new
environment variable can be introduced to bypass the checks: an unknown name is
simply not read.

Read-only alone is not enough
-----------------------------
Possession of a credential proves nothing about *which* credential it is. The
identity requirements below are what the live ``GetApiKeyInfo`` response is
checked against before F11 is allowed to treat the account as observed:

* key name exactly ``OPIP-V2-PROTECTION-READONLY``;
* permissions exactly ``query-funds`` + ``query-open-trades`` - the two scopes
  F11's own calls need, and nothing else (no closed-trade, ledger, export,
  WebSocket, trading or funding scope);
* IP allowlist exactly the confirmed Trading-droplet host.

Anything else refuses, and refusing never falls back to another credential.

This module holds no authority: it resolves names and describes requirements. It
never places, cancels or modifies an order, and it never reads the network.
"""

from __future__ import annotations

import hashlib
import ipaddress
import os
from dataclasses import dataclass
from typing import Iterable, Mapping

#: Protection credential NAMES. Literals, never derived from a suffix search.
PROTECTION_KEY_ENV = "OPIP_F11_KRAKEN_READONLY_API_KEY"
# This is an environment variable NAME, never a secret value.
PROTECTION_SECRET_ENV = "OPIP_F11_KRAKEN_READONLY_API_SECRET"  # nosec B105

#: The pre-existing funded pair. Never removed: it stays the legacy source for a
#: Git-worktree host, and it stays forbidden in the core container.
LEGACY_KEY_ENV = "KRAKEN_API_KEY"
# This is an environment variable NAME, never a secret value.
LEGACY_SECRET_ENV = "KRAKEN_API_SECRET"  # nosec B105

#: Provenance markers. ``OPIP_F11_READONLY`` means the core received the
#: dedicated protection pair; ``LEGACY_FUNDED_NAME`` means the legacy pair was
#: read directly (never the case in a filtered core environment).
SOURCE_PROTECTION = "OPIP_F11_READONLY"
SOURCE_LEGACY = "LEGACY_FUNDED_NAME"

#: The dedicated, owner-created Kraken key this exception is scoped to.
PROTECTION_KEY_NAME = "OPIP-V2-PROTECTION-READONLY"

#: Exactly the scopes F11 needs: ``query-funds`` for ``Balance`` and
#: ``query-open-trades`` for ``OpenPositions``. A broader read-only set is
#: refused too - least privilege is the point of a dedicated key.
PROTECTION_PERMISSIONS: frozenset[str] = frozenset(
    {"query-funds", "query-open-trades"}
)

#: The confirmed outbound IPv4 of the Trading droplet. A single-host allowlist


class KrakenCredentialError(RuntimeError):
    """Raised when the protection credential pair cannot be resolved safely."""


class CredentialPairIncomplete(KrakenCredentialError):
    """Raised when exactly one half of a credential pair is configured.

    Deliberately distinct from "no credential": a half pair is a broken
    rotation, so it must refuse rather than degrade, and it must never be
    completed from a different name.
    """


@dataclass(frozen=True)
class CredentialPair:
    """A resolved key/secret pair and where it came from (never a value)."""

    key: str
    secret: str
    source: str

    @property
    def is_protection_pair(self) -> bool:
        return self.source == SOURCE_PROTECTION


def _text(environ: Mapping[str, object], name: str) -> str:
    value = environ.get(name, "")
    return value.strip() if isinstance(value, str) else ""


def resolve_credential_pair(
    environ: Mapping[str, object] | None = None,
) -> CredentialPair | None:
    """Resolve the one pair the read-only client may use, or ``None``.

    ``None`` means "no credential configured" - a state that must degrade, not
    crash. A half pair raises :class:`CredentialPairIncomplete` instead, because
    a half pair is a configuration fault that must fail closed.
    """

    source = os.environ if environ is None else environ

    protection_key = _text(source, PROTECTION_KEY_ENV)
    protection_secret = _text(source, PROTECTION_SECRET_ENV)
    if protection_key or protection_secret:
        if not (protection_key and protection_secret):
            raise CredentialPairIncomplete(
                f"{PROTECTION_KEY_ENV} and {PROTECTION_SECRET_ENV} must be set "
                "together; a half pair is refused"
            )
        return CredentialPair(
            key=protection_key, secret=protection_secret, source=SOURCE_PROTECTION
        )

    legacy_key = _text(source, LEGACY_KEY_ENV)
    legacy_secret = _text(source, LEGACY_SECRET_ENV)
    if legacy_key or legacy_secret:
        if not (legacy_key and legacy_secret):
            raise CredentialPairIncomplete(
                f"{LEGACY_KEY_ENV} and {LEGACY_SECRET_ENV} must be set together; "
                "a half pair is refused"
            )
        return CredentialPair(
            key=legacy_key, secret=legacy_secret, source=SOURCE_LEGACY
        )

    return None


def normalize_ip_allowlist(entries: Iterable[object] | None) -> tuple[str, ...]:
    """Normalize reported allowlist entries to bare IPv4 hosts.

    ``178.128.147.125``, ``178.128.147.125/32`` and a ``/32`` network object all
    normalize to the same host, so an equivalent single-host representation is
    accepted. A wider network, a hostname or an unparseable entry is preserved
    verbatim, which can never equal the required host - so it refuses.
    """

    normalized: list[str] = []
    for raw in entries or ():
        if isinstance(raw, bool):
            normalized.append(str(raw))
            continue
        try:
            text = str(raw).strip()
        except Exception:  # noqa: BLE001 - a value that cannot be read is not proof
            # Fail closed: an unreadable entry becomes empty and is dropped, so
            # the allowlist can never match on unproven evidence.
            text = ""
        if not text:
            continue
        try:
            network = ipaddress.ip_network(text, strict=False)
        except ValueError:
            normalized.append(text)
            continue
        if isinstance(network, ipaddress.IPv4Network) and network.prefixlen == 32:
            normalized.append(str(network.network_address))
        else:
            normalized.append(str(network))
    return tuple(normalized)


def allowlist_is_confirmed(entries: Iterable[object] | None) -> bool:
    """True only for an allowlist that is exactly the confirmed droplet host."""

    return normalize_ip_allowlist(entries) == (PROTECTION_HOST,)


def protection_fingerprint(
    *,
    api_key: str,
    key_name: str,
    permissions: Iterable[object],
    ip_allowlist: Iterable[object],
) -> str:
    """A stable, one-way credential fingerprint for equality proofs.

    It identifies *which* credential and scope set a runtime resolved, so a
    preflight and a live core can be proven to have used the same one. It is a
    digest: it is never reversible into a key, and it is the only credential-
    derived value any receipt or log may carry.
    """

    digest = hashlib.sha256()
    digest.update(str(api_key or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(str(key_name or "").encode("utf-8"))
    digest.update(b"\x00")
    digest.update(
        ",".join(sorted(str(item) for item in permissions or ())).encode("utf-8")
    )
    digest.update(b"\x00")
    digest.update(
        ",".join(sorted(normalize_ip_allowlist(ip_allowlist))).encode("utf-8")
    )
    return digest.hexdigest()[:16]

#: may be reported as ``host`` or ``host/32``; both normalize to this host.
PROTECTION_HOST = "178.128.147.125"
PROTECTION_IP_ALLOWLIST_ENTRY = f"{PROTECTION_HOST}/32"
