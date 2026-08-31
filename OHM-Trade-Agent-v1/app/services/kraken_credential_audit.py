from __future__ import annotations

from dataclasses import dataclass

from app.exchanges.kraken_private import (
    KrakenKeyInfo,
    KrakenPermissionError,
    KrakenPermissionUnverifiable,
    KrakenPrivateAPIError,
    KrakenPrivateClient,
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
