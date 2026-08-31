from __future__ import annotations

from app.exchanges.kraken_private import KrakenPrivateAPIError, KrakenPrivateClient
from app.services.kraken_credential_audit import (
    FAIL,
    PASS,
    UNVERIFIED,
    audit_kraken_read_only,
)


def _client() -> KrakenPrivateClient:
    return KrakenPrivateClient(api_key="key", api_secret="c2VjcmV0")


def test_positively_verified_read_only_is_pass(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: {
            "apiKeyName": "ohm-read-only",
            "permissions": ["query-funds", "query-open-trades", "query-closed-trades"],
        },
    )
    audit = audit_kraken_read_only(client)
    assert audit.status == PASS
    assert audit.is_positively_read_only is True
    assert audit.key_info is not None
    assert audit.key_info.is_read_only is True


def test_positively_verified_write_capable_is_fail(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: {
            "apiKeyName": "ohm-unsafe",
            "permissions": ["query-funds", "modify-trades"],
        },
    )
    audit = audit_kraken_read_only(client)
    assert audit.status == FAIL
    assert audit.is_positively_read_only is False


def test_api_failure_is_not_pass(monkeypatch):
    client = _client()

    def _raise(endpoint, params=None):
        raise KrakenPrivateAPIError("Kraken private request failed: boom")

    monkeypatch.setattr(client, "_post", _raise)
    audit = audit_kraken_read_only(client)
    assert audit.status != PASS
    assert audit.status == UNVERIFIED


def test_missing_permission_information_is_not_pass(monkeypatch):
    client = _client()
    # The API response omits the permissions field entirely -- this must
    # never be treated as an empty (and therefore safe) permission set.
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: {"apiKeyName": "ohm-no-permissions-field"},
    )
    audit = audit_kraken_read_only(client)
    assert audit.status != PASS
    assert audit.status == UNVERIFIED
    assert "PERMISSIONS_NOT_REPORTED" in audit.reason


def test_malformed_response_is_not_pass(monkeypatch):
    client = _client()
    # A response whose permissions field cannot be interpreted as a
    # collection of strings must not be silently coerced into PASS.
    monkeypatch.setattr(
        client,
        "_post",
        lambda endpoint, params=None: (_ for _ in ()).throw(
            TypeError("unexpected response shape")
        ),
    )
    audit = audit_kraken_read_only(client)
    assert audit.status != PASS
    assert audit.status == UNVERIFIED


def test_timeout_unavailable_is_not_pass(monkeypatch):
    client = _client()

    def _timeout(endpoint, params=None):
        raise KrakenPrivateAPIError("Kraken private request failed: timed out")

    monkeypatch.setattr(client, "_post", _timeout)
    audit = audit_kraken_read_only(client)
    assert audit.status != PASS
    assert audit.status == UNVERIFIED


def test_credentials_unavailable_is_not_pass():
    client = KrakenPrivateClient(api_key="", api_secret="")
    audit = audit_kraken_read_only(client)
    assert audit.status != PASS
    assert audit.status == UNVERIFIED
    assert audit.reason == "KRAKEN_CREDENTIALS_UNAVAILABLE"


def test_audit_never_raises_on_unexpected_client_exception(monkeypatch):
    client = _client()

    def _raise(endpoint, params=None):
        raise MemoryError("unexpected failure")

    monkeypatch.setattr(client, "_post", _raise)
    audit = audit_kraken_read_only(client)
    assert audit.status == UNVERIFIED
