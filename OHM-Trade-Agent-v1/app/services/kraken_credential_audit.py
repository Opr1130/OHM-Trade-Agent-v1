from __future__ import annotations

from dataclasses import dataclass

from app.exchanges.kraken_private import (
    KrakenCredentialConfiguration,
    KrakenKeyIdentityError,
    KrakenKeyInfo,
    KrakenPermissionError,
    KrakenPermissionUnverifiable,
    KrakenPrivateAPIError,
    KrakenPrivateClient,
    KrakenProtectionScopeError,
)


# Reuses the PASS/FAIL vocabulary already established elsewhere in the
# repository (e.g. app/opip/decision/models.py, app/scanner/market_data_validation.py)
# rather than inventing incompatible semantics.
PASS = "PASS"
FAIL = "FAIL"
UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class KrakenReadOnlyAudit:
    """Wave-0 read-only credential audit result.

    status is exactly one of PASS, FAIL, or UNVERIFIED. Unknown/unverified
    permission state (missing credentials, API failure, malformed response,
    timeout, or a response that omits the permissions field) is NEVER
    represented as PASS.
    """

    status: str
    reason: str
    key_info: KrakenKeyInfo | None = None

    @property
    def is_positively_read_only(self) -> bool:
        return self.status == PASS


def audit_kraken_read_only(client: KrakenPrivateClient) -> KrakenReadOnlyAudit:
    """Positively verify that the configured Kraken API key is read-only.

    This performs no mutation, places no orders, and does no destructive
    credential testing -- it only inspects the account's own reported key
    permissions. Fail-closed: any state short of positive confirmation of a
    safe read-only key returns UNVERIFIED or FAIL, never PASS.
    """
    if not client.enabled:
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason="KRAKEN_CREDENTIALS_UNAVAILABLE",
        )

    try:
        info = client.assert_read_only()
    except KrakenPermissionError as exc:
        return KrakenReadOnlyAudit(
            status=FAIL,
            reason=f"KRAKEN_KEY_NOT_READ_ONLY:{exc}",
        )
    except KrakenPermissionUnverifiable as exc:
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"KRAKEN_PERMISSIONS_NOT_REPORTED:{exc}",
        )
    except KrakenPrivateAPIError as exc:
        # Covers API failure, timeout, and transport-level malformed
        # responses (JSON decode issues surface here as well).
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"KRAKEN_AUDIT_API_ERROR:{type(exc).__name__}:{exc}",
        )
    except Exception as exc:  # noqa: BLE001 - audit must never crash the caller
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"KRAKEN_AUDIT_ERROR:{type(exc).__name__}:{exc}",
        )

    if not isinstance(info, KrakenKeyInfo) or not info.permissions_reported:
        # Defensive: assert_read_only() should already have raised for this,
        # but a malformed/mocked response must never be read as PASS.
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason="KRAKEN_AUDIT_MALFORMED_RESPONSE",
        )

    if info.is_read_only is not True:
        return KrakenReadOnlyAudit(
            status=FAIL,
            reason="KRAKEN_KEY_NOT_READ_ONLY",
            key_info=info,
        )

    return KrakenReadOnlyAudit(
        status=PASS,
        reason="Positively verified read-only Kraken API key permissions",
        key_info=info,
    )


def audit_protection_identity(client: KrakenPrivateClient) -> KrakenReadOnlyAudit:
    """Positively verify the dedicated F11 protection credential.

    Stricter than :func:`audit_kraken_read_only`, because F11 is the only
    consumer allowed inside the core container: it must be the *dedicated*
    protection key, with exactly the two scopes F11 needs, restricted to the
    confirmed Trading droplet. A read-only key that fails any of those is still
    refused, and nothing here ever falls back to another credential.

    Fail-closed vocabulary is shared with :func:`audit_kraken_read_only`: only
    positive, fully reported confirmation returns PASS.
    """

    if not client.enabled:
        problem = getattr(client, "credential_problem", "") or ""
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=(
                f"F11_CREDENTIAL_CONFIGURATION:{problem}"
                if problem
                else "KRAKEN_CREDENTIALS_UNAVAILABLE"
            ),
        )

    try:
        info = client.assert_protection_read_only()
    except KrakenProtectionScopeError as exc:
        return KrakenReadOnlyAudit(
            status=FAIL,
            reason=f"F11_SCOPE_MISMATCH:{exc}",
        )
    except KrakenKeyIdentityError as exc:
        return KrakenReadOnlyAudit(
            status=FAIL,
            reason=f"F11_KEY_IDENTITY_MISMATCH:{exc}",
        )
    except KrakenPermissionError as exc:
        return KrakenReadOnlyAudit(
            status=FAIL,
            reason=f"KRAKEN_KEY_NOT_READ_ONLY:{exc}",
        )
    except KrakenPermissionUnverifiable as exc:
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"F11_IDENTITY_UNVERIFIABLE:{exc}",
        )
    except KrakenCredentialConfiguration as exc:
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"F11_CREDENTIAL_CONFIGURATION:{exc}",
        )
    except KrakenPrivateAPIError as exc:
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"KRAKEN_AUDIT_API_ERROR:{type(exc).__name__}:{exc}",
        )
    except Exception as exc:  # noqa: BLE001 - the audit must never crash the caller
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason=f"F11_AUDIT_ERROR:{type(exc).__name__}:{exc}",
        )

    if not isinstance(info, KrakenKeyInfo) or not info.is_protection_key:
        # Defensive: assert_protection_read_only() should already have refused,
        # but a malformed or mocked response must never be read as PASS.
        return KrakenReadOnlyAudit(
            status=UNVERIFIED,
            reason="F11_IDENTITY_MALFORMED_RESPONSE",
        )

    return KrakenReadOnlyAudit(
        status=PASS,
        reason=(
            "Dedicated F11 protection key positively verified: identity, "
            "exact scopes and confirmed IP allowlist"
        ),
        key_info=info,
    )
