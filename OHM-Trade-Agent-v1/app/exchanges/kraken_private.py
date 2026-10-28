from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from app.exchanges.kraken_protection_credentials import (
    PROTECTION_HOST,
    PROTECTION_IP_ALLOWLIST_ENTRY,
    PROTECTION_KEY_NAME,
    PROTECTION_PERMISSIONS,
    SOURCE_PROTECTION,
    KrakenCredentialError,
    allowlist_is_confirmed,
    normalize_ip_allowlist,
    protection_fingerprint,
    resolve_credential_pair,
)


KRAKEN_API_BASE = "https://api.kraken.com"
READ_ONLY_PERMISSIONS = {
    "query-funds",
    "query-open-trades",
    "query-closed-trades",
    "query-ledger",
    "export-data",
    "create-ws-token",
}
DANGEROUS_PERMISSIONS = {
    "modify-trades",
    "close-trades",
    "withdraw-funds",
    "add-funds",
    "earn-funds",
    "add-withdraw-address",
    "update-withdraw-address",
}

#: Documented spellings of the ``GetApiKeyInfo`` result fields. Kraken's live
#: response is snake_case (``api_key_name`` / ``last_used`` / ``ip_allowlist``);
#: the previously shipped client and its pinned fixtures use camelCase. Both are
#: accepted, and a response that reports one fact twice with different values is
#: refused rather than guessed - identity must never be ambiguous.
_KEY_NAME_FIELDS = ("api_key_name", "apiKeyName")
_LAST_USED_FIELDS = ("last_used", "lastUsed")
_IP_ALLOWLIST_FIELDS = ("ip_allowlist", "ipAllowlist")


def _reported_field(
    result: dict[str, Any], fields: tuple[str, ...]
) -> tuple[bool, Any]:
    """Return ``(reported, value)`` for the first documented field spelling.

    Conflicting representations raise :class:`KrakenKeyIdentityError`: two
    spellings of one fact that disagree cannot be trusted for identity.
    """

    values = [(name, result[name]) for name in fields if name in result]
    if not values:
        return False, None
    first_name, first_value = values[0]
    for name, value in values[1:]:
        if value != first_value:
            raise KrakenKeyIdentityError(
                f"GetApiKeyInfo reported conflicting {first_name}/{name} values"
            )
    return True, first_value



class KrakenPrivateAPIError(RuntimeError):
    """Raised when an authenticated Kraken request fails."""


class KrakenPermissionError(KrakenPrivateAPIError):
    """Raised when the configured key has permissions OHM must not use."""


class KrakenPermissionUnverifiable(KrakenPrivateAPIError):
    """Raised when the API response does not report key permissions at all.

    This is distinct from a positively-confirmed empty permission set: it
    means read-only status could not be determined, not that it was checked
    and found safe.
    """


class KrakenKeyIdentityError(KrakenPrivateAPIError):
    """Raised when the configured key is not the key this caller requires.

    Identity is the key's reported *name* and its confirmed IP allowlist. A
    read-only key that is not the dedicated protection key is still the wrong
    key for F11, so it must refuse rather than degrade.
    """


class KrakenProtectionScopeError(KrakenPermissionError):
    """Raised when the protection key's reported scopes are not exactly F11's.

    Both a wider read-only set (ledger, closed trades, export, WebSocket) and a
    missing required scope land here: least privilege is part of the exception.
    """


class KrakenCredentialConfiguration(KrakenPrivateAPIError):
    """Raised for a locally broken credential pair (for example a half pair).

    Never completed from a different name: a broken rotation fails closed.
    """


@dataclass(frozen=True)
class KrakenKeyInfo:
    name: str
    permissions: frozenset[str]
    last_used: str | None = None
    # False when the API response omitted the permissions field entirely, so
    # an empty `permissions` frozenset here is never confused with a
    # positively-confirmed empty permission set.
    permissions_reported: bool = True
    # Reported IP allowlist entries, verbatim. Empty when the response omitted
    # the field; `ip_allowlist_reported` distinguishes that from an empty list.
    ip_allowlist: tuple[str, ...] = ()
    ip_allowlist_reported: bool = False

    @property
    def is_read_only(self) -> bool:
        return not bool(self.permissions & DANGEROUS_PERMISSIONS)

    @property
    def is_protection_key(self) -> bool:
        """True only for the dedicated F11 key with its exact bounded scopes."""

        return (
            self.permissions_reported
            and self.name == PROTECTION_KEY_NAME
            and self.permissions == PROTECTION_PERMISSIONS
            and self.ip_allowlist_reported
            and allowlist_is_confirmed(self.ip_allowlist)
        )


class KrakenPrivateClient:
    """Minimal authenticated Spot REST client for read-only account reconciliation.

    OHM intentionally exposes no order-placement, cancellation, deposit, or
    withdrawal methods here. The client is an observation surface only.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_secret: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.credential_source = ""
        self.credential_problem = ""
        if api_key is not None or api_secret is not None:
            # Explicit injection (tests, one-off operator probes) never consults
            # the environment, so a probe can never silently pick up a second
            # pair alongside the one it was given.
            self.api_key = api_key or ""
            self.api_secret = api_secret or ""
            self.credential_source = "EXPLICIT"
        else:
            try:
                pair = resolve_credential_pair(os.environ)
            except KrakenCredentialError as exc:
                # A half pair is a configuration fault: stay disabled and keep
                # the reason, so the assert/audit seam refuses with it instead of
                # reporting a benign "not configured" state.
                pair = None
                self.credential_problem = str(exc)
            self.api_key = pair.key if pair is not None else ""
            self.api_secret = pair.secret if pair is not None else ""
            self.credential_source = pair.source if pair is not None else ""
        self.timeout_seconds = timeout_seconds
        self._last_nonce = 0

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    @property
    def is_protection_credential(self) -> bool:
        """True when the resolved pair is the dedicated F11 protection pair."""

        return self.credential_source == SOURCE_PROTECTION

    def _nonce(self) -> int:
        candidate = time.time_ns() // 1_000_000
        self._last_nonce = max(candidate, self._last_nonce + 1)
        return self._last_nonce

    def _signature(self, url_path: str, postdata: str, nonce: int) -> str:
        encoded = (str(nonce) + postdata).encode()
        message = url_path.encode() + hashlib.sha256(encoded).digest()
        try:
            secret = base64.b64decode(self.api_secret)
        except Exception as exc:  # pragma: no cover - defensive configuration error
            raise KrakenPrivateAPIError("KRAKEN_API_SECRET is not valid base64") from exc
        digest = hmac.new(secret, message, hashlib.sha512).digest()
        return base64.b64encode(digest).decode()

    def _post(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        if not self.enabled:
            raise KrakenPrivateAPIError(
                self.credential_problem
                or "Kraken private API credentials are not configured"
            )
        url_path = f"/0/private/{endpoint}"
        payload = dict(params or {})
        nonce = self._nonce()
        payload["nonce"] = nonce

        # Kraken signs the exact form-encoded POST body. Build it once and use
        # the same bytes for both the signature and the HTTP request so bools,
        # ordering, and future parameter types can never encode differently.
        postdata = urlencode(payload)
        headers = {
            "API-Key": self.api_key,
            "API-Sign": self._signature(url_path, postdata, nonce),
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            response = httpx.post(
                f"{KRAKEN_API_BASE}{url_path}",
                content=postdata.encode(),
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise KrakenPrivateAPIError(f"Kraken private request failed: {exc}") from exc
        body = response.json()
        errors = body.get("error", [])
        if errors:
            raise KrakenPrivateAPIError(f"Kraken API error: {', '.join(errors)}")
        return body.get("result")

    def get_api_key_info(self) -> KrakenKeyInfo:
        result = self._post("GetApiKeyInfo") or {}
        if not isinstance(result, dict):
            return KrakenKeyInfo(
                name="",
                permissions=frozenset(),
                permissions_reported=False,
            )

        raw_permissions = result.get("permissions")
        permissions_valid = isinstance(raw_permissions, list) and all(
            isinstance(value, str) for value in raw_permissions
        )
        permissions_reported = "permissions" in result and permissions_valid
        permissions = (
            frozenset(raw_permissions)
            if permissions_valid
            else frozenset()
        )

        name_seen, name_value = _reported_field(result, _KEY_NAME_FIELDS)
        last_used_seen, last_used_value = _reported_field(result, _LAST_USED_FIELDS)
        allowlist_seen, allowlist_value = _reported_field(
            result, _IP_ALLOWLIST_FIELDS
        )
        allowlist_valid = isinstance(allowlist_value, list) and all(
            isinstance(entry, str) for entry in allowlist_value
        )

        return KrakenKeyInfo(
            name=str(name_value) if name_seen and isinstance(name_value, str) else "",
            permissions=permissions,
            last_used=(
                str(last_used_value)
                if last_used_seen and last_used_value is not None
                else None
            ),
            permissions_reported=permissions_reported,
            ip_allowlist=(
                normalize_ip_allowlist(allowlist_value) if allowlist_valid else ()
            ),
            ip_allowlist_reported=bool(allowlist_seen and allowlist_valid),
        )

    def assert_read_only(self) -> KrakenKeyInfo:
        info = self.get_api_key_info()
        if not info.permissions_reported:
            # Unknown/unverified permission state must never be represented
            # as read-only-safe. Fail closed rather than defaulting an
            # unreported permission set to "no dangerous permissions".
            raise KrakenPermissionUnverifiable(
                "Kraken API response did not report key permissions"
            )
        dangerous = sorted(info.permissions & DANGEROUS_PERMISSIONS)
        if dangerous:
            raise KrakenPermissionError(
                "OHM requires a read-only Kraken key; remove permissions: " + ", ".join(dangerous)
            )
        return info

    def assert_protection_read_only(self) -> KrakenKeyInfo:
        """F11's gate: the dedicated protection key, its exact scopes, its host.

        Order matters. The established read-only refusal runs first, so a funded
        key is reported as *funded* rather than merely as "wrong name". Every
        later check refuses rather than degrading, none of them falls back to
        another credential, and none of them can widen authority: this method
        only ever reads key metadata.
        """

        info = self.assert_read_only()
        if info.name != PROTECTION_KEY_NAME:
            raise KrakenKeyIdentityError(
                "F11 requires the dedicated protection key "
                f"{PROTECTION_KEY_NAME!r}; the configured key reports "
                f"{info.name!r}"
            )
        if info.permissions != PROTECTION_PERMISSIONS:
            unexpected = sorted(info.permissions - PROTECTION_PERMISSIONS)
            missing = sorted(PROTECTION_PERMISSIONS - info.permissions)
            raise KrakenProtectionScopeError(
                "F11 protection key scopes must be exactly "
                f"{sorted(PROTECTION_PERMISSIONS)}; unexpected={unexpected} "
                f"missing={missing}"
            )
        if not info.ip_allowlist_reported:
            raise KrakenPermissionUnverifiable(
                "Kraken reported no IP allowlist for the protection key, so "
                f"{PROTECTION_IP_ALLOWLIST_ENTRY} cannot be confirmed"
            )
        if not allowlist_is_confirmed(info.ip_allowlist):
            raise KrakenKeyIdentityError(
                "F11 protection key is not restricted to the confirmed Trading "
                f"droplet {PROTECTION_HOST}"
            )
        return info

    def protection_fingerprint(self, info: KrakenKeyInfo) -> str:
        """One-way equality proof for the resolved credential and scope set.

        Used to prove a preflight and a live runtime resolved the *same*
        credential without either of them carrying a secret value.
        """

        return protection_fingerprint(
            api_key=self.api_key,
            key_name=info.name,
            permissions=info.permissions,
            ip_allowlist=info.ip_allowlist,
        )

    def get_balance(self) -> dict[str, float]:
        result = self._post("Balance") or {}
        return {str(asset): float(amount) for asset, amount in result.items()}

    def get_open_orders(self) -> dict[str, dict[str, Any]]:
        result = self._post("OpenOrders", {"trades": "true"}) or {}
        rows = result.get("open", {}) if isinstance(result, dict) else {}
        return rows if isinstance(rows, dict) else {}

    def get_trades_history(self, *, start: int | None = None) -> dict[str, dict[str, Any]]:
        params: dict[str, Any] = {"trades": "true"}
        if start is not None:
            params["start"] = int(start)
        result = self._post("TradesHistory", params) or {}
        rows = result.get("trades", {}) if isinstance(result, dict) else {}
        return rows if isinstance(rows, dict) else {}

    def get_open_positions(self) -> dict[str, dict[str, Any]]:
        result = self._post("OpenPositions", {"docalcs": "true"}) or {}
        return result if isinstance(result, dict) else {}
