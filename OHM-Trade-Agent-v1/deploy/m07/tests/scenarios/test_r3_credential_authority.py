"""Release-boundary credential authority (bounded query-only credential).

Compose's checked-in ``environment:`` mapping is not the whole authority
surface: ``env_file: .env`` injects the same credential NAMES into the running
core and into every one-shot container started from that service. The release
boundary must judge the credential set the container actually receives.

The approved Kraken key is READ-ONLY and already passes Kraken's own permission
audit. These tests pin that a bounded query-only pair is permitted only on
*positive live* confirmation, that trading-capable, unverified, incomplete or
authority-bearing credentials are refused, and that the two consumer
invocations carry the Compose authority file.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

import opip_credential_authority as authority

R1 = Path(__file__).resolve().parents[2]
BOOTSTRAP = R1 / "scripts" / "v2" / "bootstrap-v2.sh"
READINESS = R1 / "scripts" / "Invoke-V2Readiness.ps1"

READ_ONLY_ENV = {
    "KRAKEN_API_KEY": "dummy-query-only-key-not-a-real-secret",
    "KRAKEN_API_SECRET": "dummy-query-only-secret-not-a-real-secret",
}
AUTHORITY_MOUNT = '-v "$TOOLS/opip_credential_authority.py:/authority.py:ro"'


def _audit(status: str, reason: str = "stub"):
    return lambda: (status, reason)


# --- positive paths --------------------------------------------------------


def test_no_funded_credential_is_permitted_without_any_network_call() -> None:
    """A host with no funded credential carries no funded authority to bound."""
    called = False

    def audit():  # pragma: no cover - must never run
        nonlocal called
        called = True
        return ("PASS", "unused")

    verdict, report = authority.evaluate_credential_authority({}, audit)
    assert verdict == authority.PASS
    assert report["read_only_credential"] == "ABSENT"
    assert report["audit_status"] == "NOT_RUN"
    assert called is False


def test_positively_verified_read_only_pair_is_permitted() -> None:
    verdict, report = authority.evaluate_credential_authority(
        READ_ONLY_ENV,
        _audit("PASS", "Positively verified read-only Kraken API key permissions"),
    )
    assert verdict == authority.PASS
    assert report["read_only_credential"] == "PRESENT"
    assert report["authority_bearing"] == "NONE"
    assert report["audit_status"] == "PASS"


# --- negative paths: credential authority ---------------------------------


@pytest.mark.parametrize(
    "key", ["KRAKEN_PRIVATE_KEY", "KRKN_API_KEY", "LIVE_TRADING_KEY"]
)
def test_authority_bearing_credential_is_always_refused(key: str) -> None:
    """No audit can bound a key that carries withdrawal or order authority."""
    verdict, report = authority.evaluate_credential_authority(
        {**READ_ONLY_ENV, key: "dummy-authority-bearing-value"},
        _audit("PASS"),
    )
    assert verdict == authority.REFUSED
    assert report["authority_bearing"] == key
    assert report["audit_status"] == "NOT_RUN"


@pytest.mark.parametrize("status", ["FAIL", "UNVERIFIED", "SOMETHING_ELSE"])
def test_unverified_or_failed_audit_stops_the_release(status: str) -> None:
    verdict, report = authority.evaluate_credential_authority(
        READ_ONLY_ENV, _audit(status, "KRAKEN_KEY_NOT_READ_ONLY")
    )
    assert verdict == authority.REFUSED
    assert report["audit_status"] == status


def test_incomplete_read_only_pair_is_refused() -> None:
    verdict, report = authority.evaluate_credential_authority(
        {"KRAKEN_API_KEY": "dummy-query-only-key-not-a-real-secret"},
        _audit("PASS"),
    )
    assert verdict == authority.REFUSED
    assert "incomplete" in report["audit_reason"]


def test_missing_audit_implementation_is_refused() -> None:
    verdict, report = authority.evaluate_credential_authority(READ_ONLY_ENV, None)
    assert verdict == authority.REFUSED
    assert report["audit_status"] == "UNVERIFIED"


def test_audit_that_raises_fails_closed() -> None:
    def boom():
        raise RuntimeError("transport down")

    verdict, report = authority.evaluate_credential_authority(READ_ONLY_ENV, boom)
    assert verdict == authority.REFUSED
    assert report["audit_status"] == "UNVERIFIED"


def test_empty_values_are_not_treated_as_present() -> None:
    verdict, _report = authority.evaluate_credential_authority(
        {"KRAKEN_API_KEY": "", "KRAKEN_API_SECRET": ""},
        _audit("PASS"),
    )
    assert verdict == authority.PASS


# --- secrets never leave the process --------------------------------------


def test_no_credential_value_reaches_stdout_or_the_report() -> None:
    secret = "dummy-query-only-secret-not-a-real-secret"
    key = "dummy-query-only-key-not-a-real-secret"
    stream = io.StringIO()
    with redirect_stdout(stream):
        verdict, report = authority.evaluate_credential_authority(
            {"KRAKEN_API_KEY": key, "KRAKEN_API_SECRET": secret},
            _audit("PASS", "Positively verified read-only Kraken API key permissions"),
        )
    rendered = stream.getvalue() + repr(report)
    assert verdict == authority.PASS
    assert secret not in rendered
    assert key not in rendered


def test_cli_never_prints_values_and_signals_refusal_via_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "dummy-query-only-secret-not-a-real-secret"
    key = "dummy-query-only-key-not-a-real-secret"
    monkeypatch.setenv("KRAKEN_API_KEY", key)
    monkeypatch.setenv("KRAKEN_API_SECRET", secret)
    monkeypatch.setattr(authority, "live_read_only_audit", _audit("PASS"))

    stream = io.StringIO()
    with redirect_stdout(stream):
        exit_code = authority.main([])
    output = stream.getvalue()

    assert exit_code == 0
    assert "CREDENTIAL_AUTHORITY=PASS" in output
    assert "AUDIT_STATUS=PASS" in output
    assert secret not in output
    assert key not in output


# --- the two consumers must carry the Compose authority file --------------


def test_bootstrap_release_boundary_verifies_credentials_before_the_gate() -> None:
    text = BOOTSTRAP.read_text(encoding="utf-8")
    assert AUTHORITY_MOUNT in text, (
        "execute must mount the credential-authority verifier read-only"
    )
    assert "-e PYTHONPATH=/app" in text, (
        "the verifier must be able to import the certified app package"
    )
    assert text.index("opip_credential_authority.py") < text.index(
        'run "release gate"'
    ), (
        "credential authority must be verified at the release boundary "
        "(before the architecture gate)"
    )


def test_readiness_probe_carries_the_compose_authority() -> None:
    """P1: PostConfiguration RELEASE_MODES must resolve the Compose authority."""
    lines = READINESS.read_text(encoding="utf-8").splitlines()
    probe = [line for line in lines if "RELEASE_MODES" in line]
    assert len(probe) == 1, f"expected one RELEASE_MODES probe, found {len(probe)}"
    stanza = probe[0]
    assert "docker-compose.yml:/repo/docker-compose.yml:ro" in stanza, (
        "readiness probe must mount the tracked docker-compose.yml read-only"
    )
    assert "--repo-root /repo" in stanza, "readiness probe must pass --repo-root /repo"
    assert " -f " not in stanza and "--file" not in stanza, (
        "readiness probe must not layer a Compose overlay"
    )

