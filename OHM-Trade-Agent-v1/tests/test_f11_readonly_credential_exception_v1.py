"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-014 - the F11 read-only credential exception.

The reported production failure this pins: the release-tree core consumes
``.env.core``, whose generator removes the legacy funded names, so the deployed
F11 runtime had no credential at all. Protection preflight passed (it read the
owner's ``.env``, which still had one), the runtime could not read the account,
and the release rolled back. F11 then recorded the durable
``KRAKEN:READ_ONLY_AUTH`` incident and could never clear it, because that scope
is ``AUTH_CONFIG``-owned and not monitor-owned.

The correction is one narrow, explicit exception - a dedicated read-only key
delivered to the core under its own two names, independently verified by
identity, exact scopes and IP allowlist - plus an operator-owned AUTH_CONFIG
recovery that refuses to close the incident on anything but fresh proof.

Every negative case is a stub. No live Kraken key is used, revoked, or placed in
an order, and no production state is touched.
"""

from __future__ import annotations

import base64
import contextlib
import io
import pathlib
import shutil
import subprocess

import pytest

from app.exchanges.kraken_private import (
    KrakenKeyIdentityError,
    KrakenPermissionError,
    KrakenPermissionUnverifiable,
    KrakenPrivateAPIError,
    KrakenPrivateClient,
    KrakenProtectionScopeError,
)
from app.exchanges.kraken_protection_credentials import (
    PROTECTION_HOST,
    PROTECTION_IP_ALLOWLIST_ENTRY,
    PROTECTION_KEY_ENV,
    PROTECTION_KEY_NAME,
    PROTECTION_PERMISSIONS,
    PROTECTION_SECRET_ENV,
    SOURCE_LEGACY,
    SOURCE_PROTECTION,
    CredentialPairIncomplete,
    allowlist_is_confirmed,
    normalize_ip_allowlist,
    protection_fingerprint,
    resolve_credential_pair,
)

APP = pathlib.Path(__file__).resolve().parents[1]
LIBRARY = APP / "deploy" / "remote" / "lib" / "opip_release_tree.sh"
DEPLOY = APP / "deploy" / "remote" / "ohm-deploy"
TRADEVISOR = APP / "deploy" / "tradevisor" / "activate-tradevisor-advisory.sh"
RESOLVER = APP / "app" / "services" / "kraken_exposure_resolver.py"
VERIFIER = APP / "app" / "services" / "kraken_position_verification.py"
RECOVERY = APP / "app" / "jobs" / "recover_f11_readonly_auth_incident.py"
BASH = shutil.which("bash")

pytestmark = [pytest.mark.acceptance]

#: The bash-dependent cases below are marked individually, so the Python-only
#: contract tests still run on a host without bash (and every case runs in CI).
requires_bash = pytest.mark.skipif(BASH is None, reason="bash is required")

#: Exit statuses that mean "bash could not start at all" on this host, not "the
#: script failed" (Windows raises 0xC0000142 when the shell cannot initialise).
_BASH_UNAVAILABLE_CODES = {3221225794, -1073741502, 3221225477}


_SECRET = "stub-secret-not-a-real-key"


class _StubClient(KrakenPrivateClient):
    """A read-only client double: no network, no ambient environment."""

    def __init__(self, *, result=None, error=None, balances=None, positions=None):
        super().__init__(
            api_key="stub-api-key",
            api_secret=base64.b64encode(_SECRET.encode()).decode(),
        )
        self._result = result
        self._error = error
        self._balances = {} if balances is None else balances
        self._positions = {} if positions is None else positions
        self.calls: list[str] = []

    def _post(self, endpoint, params=None):
        self.calls.append(endpoint)
        if self._error is not None:
            raise self._error
        if endpoint == "GetApiKeyInfo":
            return self._result
        if endpoint == "Balance":
            return dict(self._balances)
        if endpoint == "OpenPositions":
            return dict(self._positions)
        raise AssertionError(f"unexpected endpoint: {endpoint}")


def _result(**overrides):
    """A valid live-shaped (snake_case) GetApiKeyInfo result."""

    payload = {
        "api_key_name": PROTECTION_KEY_NAME,
        "permissions": sorted(PROTECTION_PERMISSIONS),
        "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
    }
    payload.update(overrides)
    return payload


def _skip_if_bash_cannot_fork(result) -> None:
    """Git-bash on Windows cannot always fork; that is an environment fault.

    The same degradation the repository's other bash harnesses skip on, so a
    Windows developer run reports "skipped" instead of a false generator
    failure. Linux CI executes these for real.
    """

    text = f"{result.stderr}{result.stdout}"
    if result.returncode in _BASH_UNAVAILABLE_CODES or (
        result.returncode != 0
        and (
            "fork: Resource temporarily unavailable" in text
            or "dofork" in text
        )
    ):
        pytest.skip("bash cannot fork reliably in this environment")


def _run_generator(app_root: pathlib.Path, state: pathlib.Path):
    script = f"""
set -Eeuo pipefail
APP_ROOT={app_root.as_posix()}
STATE_DIR={state.as_posix()}
source {LIBRARY.as_posix()}
opip_write_core_env
"""
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )
    _skip_if_bash_cannot_fork(result)
    return result


@pytest.fixture()
def generator_sandbox(tmp_path: pathlib.Path):
    app_root = tmp_path / "app"
    app_root.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    (app_root / ".env").write_text(
        "OPIP_RELEASE_PROFILE=EVIDENCE_SHADOW\n"
        "KRAKEN_API_KEY=funded-key-value\n"
        "KRAKEN_API_SECRET=funded-secret-value\n"
        f"{PROTECTION_KEY_ENV}=protection-key-value\n"
        f"{PROTECTION_SECRET_ENV}=protection-secret-value\n"
        "KRAKEN_RECONCILIATION_ENABLED=false\n",
        encoding="utf-8",
    )
    return app_root, state



# ---------------------------------------------------------------------------
# Credential resolution: which pair the core is allowed to use.
# ---------------------------------------------------------------------------


def test_resolver_prefers_the_dedicated_pair_over_the_legacy_pair() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: when the dedicated protection pair is
    configured it is the only pair used, even while the legacy funded pair is
    still present in the same environment."""
    pair = resolve_credential_pair(
        {
            PROTECTION_KEY_ENV: "protection-key",
            PROTECTION_SECRET_ENV: "protection-secret",
            "KRAKEN_API_KEY": "funded-key",
            "KRAKEN_API_SECRET": "funded-secret",
        }
    )
    assert pair is not None
    assert (pair.key, pair.secret) == ("protection-key", "protection-secret")
    assert pair.source == SOURCE_PROTECTION
    assert pair.is_protection_pair is True


def test_partial_protection_pair_is_refused_and_never_completed() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a half protection pair is a broken
    rotation - it refuses rather than degrading, and it is never completed from
    the legacy funded names that are present alongside it."""
    with pytest.raises(CredentialPairIncomplete):
        resolve_credential_pair(
            {
                PROTECTION_KEY_ENV: "protection-key",
                "KRAKEN_API_KEY": "funded-key",
                "KRAKEN_API_SECRET": "funded-secret",
            }
        )
    with pytest.raises(CredentialPairIncomplete):
        resolve_credential_pair({PROTECTION_SECRET_ENV: "protection-secret"})
    # A half legacy pair refuses too: the same rule, no exceptions.
    with pytest.raises(CredentialPairIncomplete):
        resolve_credential_pair({"KRAKEN_API_KEY": "funded-key"})


def test_legacy_pair_is_used_only_when_no_protection_pair_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: SAFE_BASELINE and Git-worktree hosts
    keep working through the legacy names, and the client reports that
    provenance instead of pretending it holds the protection pair."""
    pair = resolve_credential_pair(
        {"KRAKEN_API_KEY": "funded-key", "KRAKEN_API_SECRET": "funded-secret"}
    )
    assert pair is not None
    assert pair.source == SOURCE_LEGACY
    assert pair.is_protection_pair is False

    monkeypatch.delenv(PROTECTION_KEY_ENV, raising=False)
    monkeypatch.delenv(PROTECTION_SECRET_ENV, raising=False)
    monkeypatch.setenv("KRAKEN_API_KEY", "funded-key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "funded-secret")
    client = KrakenPrivateClient()
    assert client.enabled is True
    assert client.is_protection_credential is False


def test_no_credential_configuration_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: an unconfigured host is a degraded
    posture, not a crash: resolution returns nothing and the client stays
    disabled, which is the pre-existing SAFE_BASELINE behaviour."""
    assert resolve_credential_pair({}) is None

    for name in (
        PROTECTION_KEY_ENV,
        PROTECTION_SECRET_ENV,
        "KRAKEN_API_KEY",
        "KRAKEN_API_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    client = KrakenPrivateClient()
    assert client.enabled is False
    assert client.credential_problem == ""


def test_partial_pair_disables_the_client_with_the_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the client never raises from its
    constructor on a broken pair - it stays disabled and carries the reason, so
    the audit and assert seams refuse with an explanation rather than a benign
    "not configured" state."""
    monkeypatch.delenv(PROTECTION_SECRET_ENV, raising=False)
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    monkeypatch.setenv(PROTECTION_KEY_ENV, "protection-key")
    client = KrakenPrivateClient()
    assert client.enabled is False
    assert PROTECTION_SECRET_ENV in client.credential_problem
    with pytest.raises(KrakenPrivateAPIError):
        client.assert_protection_read_only()


# ---------------------------------------------------------------------------
# GetApiKeyInfo parsing: both documented spellings, never a guess.
# ---------------------------------------------------------------------------


def test_live_snake_case_key_info_is_parsed() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the live Kraken response is
    snake_case, so the key name, last use and allowlist are read from it - the
    deployed client previously parsed only camelCase and therefore reported an
    empty key name for a named key."""
    client = _StubClient(
        result={
            "api_key_name": PROTECTION_KEY_NAME,
            "permissions": sorted(PROTECTION_PERMISSIONS),
            "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
            "last_used": "2026-10-27T21:00:00Z",
        }
    )
    info = client.get_api_key_info()
    assert info.name == PROTECTION_KEY_NAME
    assert info.permissions == PROTECTION_PERMISSIONS
    assert info.ip_allowlist == (PROTECTION_HOST,)
    assert info.ip_allowlist_reported is True
    assert info.last_used == "2026-10-27T21:00:00Z"
    assert info.is_protection_key is True


def test_camel_case_key_info_remains_supported() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the previously shipped camelCase
    spelling stays accepted, so a pinned fixture or an older response shape does
    not become a false identity failure."""
    client = _StubClient(
        result={
            "apiKeyName": PROTECTION_KEY_NAME,
            "permissions": sorted(PROTECTION_PERMISSIONS),
            "ipAllowlist": [PROTECTION_HOST],
            "lastUsed": 1729000000,
        }
    )
    info = client.assert_protection_read_only()
    assert info.name == PROTECTION_KEY_NAME
    assert info.ip_allowlist == (PROTECTION_HOST,)
    assert info.last_used == "1729000000"


def test_conflicting_key_name_representations_are_refused() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a response that reports the same fact
    twice with different values is refused - identity can never be ambiguous."""
    client = _StubClient(
        result={
            "api_key_name": PROTECTION_KEY_NAME,
            "apiKeyName": "SOME-OTHER-KEY",
            "permissions": sorted(PROTECTION_PERMISSIONS),
            "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
        }
    )
    with pytest.raises(KrakenKeyIdentityError):
        client.get_api_key_info()


# ---------------------------------------------------------------------------
# The protection identity gate: name, exact scopes, confirmed allowlist.
# ---------------------------------------------------------------------------


def test_protection_identity_requires_the_exact_key_name() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a read-only key that is not the
    dedicated protection key is the wrong key for F11, so it refuses."""
    client = _StubClient(result=_result(api_key_name="SOME-OTHER-READONLY-KEY"))
    with pytest.raises(KrakenKeyIdentityError):
        client.assert_protection_read_only()


def test_protection_identity_requires_a_reported_key_name() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a response with no key name is
    unproven identity, not an acceptable anonymous key."""
    client = _StubClient(
        result={
            "permissions": sorted(PROTECTION_PERMISSIONS),
            "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
        }
    )
    with pytest.raises(KrakenKeyIdentityError):
        client.assert_protection_read_only()


def test_protection_identity_accepts_the_two_permission_set() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: exactly query-funds and
    query-open-trades verify - the two scopes Balance and OpenPositions need."""
    info = _StubClient(result=_result()).assert_protection_read_only()
    assert info.permissions == frozenset({"query-funds", "query-open-trades"})
    assert info.is_protection_key is True


@pytest.mark.parametrize(
    "extra",
    ["query-ledger", "query-closed-trades", "export-data", "create-ws-token"],
)
def test_protection_identity_refuses_extra_read_only_scopes(extra: str) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a broader read-only key is refused
    too - ledger, closed trades, export and WebSocket scopes are not F11's."""
    client = _StubClient(
        result=_result(permissions=sorted(PROTECTION_PERMISSIONS | {extra}))
    )
    with pytest.raises(KrakenProtectionScopeError):
        client.assert_protection_read_only()


@pytest.mark.parametrize("missing", ["query-funds", "query-open-trades"])
def test_protection_identity_refuses_a_missing_required_scope(missing: str) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a key missing either required scope
    cannot support F11's two reads, so it refuses before any read is attempted."""
    client = _StubClient(
        result=_result(permissions=sorted(PROTECTION_PERMISSIONS - {missing}))
    )
    with pytest.raises(KrakenProtectionScopeError):
        client.assert_protection_read_only()


@pytest.mark.parametrize("dangerous", ["modify-trades", "withdraw-funds"])
def test_protection_identity_refuses_trading_and_funding_permissions(
    dangerous: str,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a funded/write-capable credential is
    reported as such and never reaches the scope comparison."""
    client = _StubClient(
        result=_result(permissions=sorted(PROTECTION_PERMISSIONS | {dangerous}))
    )
    with pytest.raises(KrakenPermissionError) as captured:
        client.assert_protection_read_only()
    assert not isinstance(captured.value, KrakenProtectionScopeError)


@pytest.mark.parametrize(
    "allowlist", [[PROTECTION_IP_ALLOWLIST_ENTRY], [PROTECTION_HOST]]
)
def test_protection_identity_requires_the_confirmed_allowlist(allowlist) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the confirmed droplet host is
    required, and the equivalent single-host representation is accepted."""
    info = _StubClient(
        result=_result(ip_allowlist=allowlist)
    ).assert_protection_read_only()
    assert info.ip_allowlist == (PROTECTION_HOST,)
    assert allowlist_is_confirmed(allowlist) is True


@pytest.mark.parametrize(
    "allowlist",
    [["0.0.0.0/0"], [f"{PROTECTION_HOST}/24"], ["10.116.0.3"], []],
)
def test_protection_identity_refuses_a_wider_or_foreign_allowlist(allowlist) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a wider network, another host or an
    empty allowlist is not the confirmed restriction and refuses."""
    client = _StubClient(result=_result(ip_allowlist=allowlist))
    with pytest.raises(KrakenKeyIdentityError):
        client.assert_protection_read_only()


def test_protection_identity_refuses_an_unreported_allowlist() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: an allowlist the response omits cannot
    be confirmed, so it is unverifiable rather than assumed acceptable."""
    client = _StubClient(
        result={
            "api_key_name": PROTECTION_KEY_NAME,
            "permissions": sorted(PROTECTION_PERMISSIONS),
        }
    )
    with pytest.raises(KrakenPermissionUnverifiable):
        client.assert_protection_read_only()


@pytest.mark.parametrize("permissions", ["query-funds", [123, "query-funds"], None])
def test_protection_identity_refuses_malformed_permissions(permissions) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a malformed permission list is
    unverifiable, never "no dangerous permissions"."""
    client = _StubClient(result=_result(permissions=permissions))
    with pytest.raises(KrakenPermissionUnverifiable):
        client.assert_protection_read_only()


def test_revoked_or_failed_key_info_is_never_verified() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a revoked key, an API error, a
    timeout or an unexpected response shape is refused and never falls back to
    another credential."""
    import httpx

    from app.services.kraken_credential_audit import PASS, audit_protection_identity

    for error in (
        KrakenPrivateAPIError("Kraken API error: EAPI:Invalid key"),
        KrakenPrivateAPIError(
            "Kraken private request failed: " + str(httpx.ReadTimeout("timeout"))
        ),
    ):
        client = _StubClient(error=error)
        with pytest.raises(KrakenPrivateAPIError):
            client.assert_protection_read_only()
        audit = audit_protection_identity(client)
        assert audit.status != PASS
        assert audit.key_info is None

    malformed = _StubClient(result=["not", "a", "mapping"])
    assert audit_protection_identity(malformed).status != PASS


# ---------------------------------------------------------------------------
# Wiring: F11's own consumers, and the scopes they must not need.
# ---------------------------------------------------------------------------


def test_f11_consumers_use_the_protection_gate() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: both F11 consumers gate on the
    dedicated protection identity, not on the weaker generic read-only check."""
    resolver = RESOLVER.read_text(encoding="utf-8")
    verifier = VERIFIER.read_text(encoding="utf-8")
    assert "assert_protection_read_only()" in resolver
    assert "assert_protection_read_only()" in verifier
    assert ".assert_read_only()" not in resolver
    assert ".assert_read_only()" not in verifier


def test_f11_permissions_do_not_include_history_ledger_or_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: F11 needs only Balance and
    OpenPositions scopes, reconciliation stays disabled, and nothing on the F11
    path requires closed-trade, ledger or export permission."""
    from app.services.kraken_reconciliation import reconciliation_enabled

    assert PROTECTION_PERMISSIONS == {"query-funds", "query-open-trades"}
    for forbidden in (
        "query-closed-trades",
        "query-ledger",
        "export-data",
        "create-ws-token",
    ):
        assert forbidden not in PROTECTION_PERMISSIONS

    monkeypatch.delenv("KRAKEN_RECONCILIATION_ENABLED", raising=False)
    assert reconciliation_enabled() is False


# ---------------------------------------------------------------------------
# The generated core environment: the pair crosses whole, funded names never.
# ---------------------------------------------------------------------------


def test_protection_credential_names_match_the_python_authority() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the controller's protection name
    literal and the Python authority list exactly the same pair, so neither can
    drift into a different (or wider) core credential name."""
    import re

    text = LIBRARY.read_text(encoding="utf-8")
    match = re.search(
        r'^OPIP_F11_READONLY_CREDENTIAL_KEYS="([^"]*)"$', text, re.MULTILINE
    )
    assert match, "the protection credential name literal is missing from the provider"
    assert tuple(match.group(1).split()) == (PROTECTION_KEY_ENV, PROTECTION_SECRET_ENV)


@requires_bash
def test_generator_carries_the_pair_and_still_excludes_funded_names(
    generator_sandbox,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the filtered core environment carries
    the dedicated protection pair and still removes every funded name, with the
    same mode 0600 and atomic write the isolation contract requires."""
    from app.services.release_profiles import FUNDED_CREDENTIAL_KEYS

    app_root, state = generator_sandbox
    source_before = (app_root / ".env").read_bytes()
    result = _run_generator(app_root, state)
    assert result.returncode == 0, result.stderr

    dest = app_root / ".env.core"
    text = dest.read_text(encoding="utf-8")
    assert f"{PROTECTION_KEY_ENV}=protection-key-value" in text
    assert f"{PROTECTION_SECRET_ENV}=protection-secret-value" in text
    for funded in FUNDED_CREDENTIAL_KEYS:
        assert f"{funded}=" not in text
    assert "KRAKEN_RECONCILIATION_ENABLED=false" in text
    assert "f11_readonly_pair=PRESENT" in result.stdout
    assert oct(dest.stat().st_mode & 0o777) == "0o600"
    assert (app_root / ".env").read_bytes() == source_before


@requires_bash
def test_generator_refuses_a_partial_pair(generator_sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a half protection pair never reaches
    the core - the generator refuses and leaves no filtered file behind."""
    app_root, state = generator_sandbox
    source = app_root / ".env"
    source.write_text(
        "\n".join(
            line
            for line in source.read_text(encoding="utf-8").splitlines()
            if not line.startswith(f"{PROTECTION_SECRET_ENV}=")
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_generator(app_root, state)
    assert result.returncode != 0
    assert "F11 read-only credential pair is incomplete" in result.stderr
    assert not (app_root / ".env.core").exists()


@requires_bash
def test_generator_never_prints_a_value(generator_sandbox) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: regeneration after a rotation reports
    only names, counts and presence - never a credential value."""
    app_root, state = generator_sandbox
    assert _run_generator(app_root, state).returncode == 0

    source = app_root / ".env"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "protection-key-value", "rotated-protection-key-value"
        ),
        encoding="utf-8",
    )
    result = _run_generator(app_root, state)
    assert result.returncode == 0, result.stderr
    combined = result.stdout + result.stderr
    for value in (
        "protection-key-value",
        "rotated-protection-key-value",
        "protection-secret-value",
        "funded-key-value",
        "funded-secret-value",
    ):
        assert value not in combined
    assert "OPIP_CORE_ENV=WRITTEN" in result.stdout


# ---------------------------------------------------------------------------
# Preflight / runtime parity and the Tradevisor isolation gate.
# ---------------------------------------------------------------------------


def _library_call(body: str):
    """Source the shipped provider library and run a snippet against it."""

    script = (
        "set -Eeuo pipefail\n"
        "APP_ROOT=/nonexistent-app-root\n"
        "STATE_DIR=/nonexistent-state-dir\n"
        f"source {LIBRARY.as_posix()}\n"
        f"{body}\n"
    )
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )
    _skip_if_bash_cannot_fork(result)
    return result


def _compose_tree(root: pathlib.Path, core_block: str) -> pathlib.Path:
    tree = root / "candidate"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "docker-compose.yml").write_text(
        "services:\n"
        "  other-service:\n"
        "    image: example/other\n"
        "    env_file:\n"
        "      - .env.other\n"
        "  ohm-trade-agent:\n"
        f"{core_block}"
        "  third-service:\n"
        "    env_file:\n"
        "      - .env.third\n",
        encoding="utf-8",
    )
    return tree


@requires_bash
def test_core_env_mode_is_read_from_the_candidate_compose(tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the preflight's environment file is
    derived from the CANDIDATE core service, never assumed. A core that declares
    `.env.core` is FILTERED; one that declares the owner's `.env` is the only
    verified legacy shape; anything else (another service's `env_file`, no
    `env_file`, no core service, a symlinked file) is unprovable and refuses."""

    filtered = _compose_tree(
        tmp_path / "filtered", "    env_file:\n      - .env.core\n"
    )
    legacy = _compose_tree(tmp_path / "legacy", "    env_file:\n      - .env\n")
    other_only = _compose_tree(tmp_path / "other", "    image: example/core\n")
    no_core = tmp_path / "no-core"
    no_core.mkdir()
    (no_core / "docker-compose.yml").write_text(
        "services:\n  other-service:\n    env_file:\n      - .env\n",
        encoding="utf-8",
    )
    symlinked = tmp_path / "symlinked"
    symlinked.mkdir()
    (symlinked / "real.yml").write_text(
        "services:\n  ohm-trade-agent:\n    env_file:\n      - .env.core\n",
        encoding="utf-8",
    )
    try:
        (symlinked / "docker-compose.yml").symlink_to(symlinked / "real.yml")
    except (OSError, NotImplementedError):
        pytest.skip("this filesystem cannot create symlinks")

    for tree, expected in ((filtered, "FILTERED"), (legacy, "LEGACY_OWNER_ENV")):
        result = _library_call(f"opip_core_env_mode {tree.as_posix()}")
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected

    for tree in (other_only, no_core, symlinked, tmp_path / "missing"):
        result = _library_call(f"opip_core_env_mode {tree.as_posix()}")
        assert result.returncode != 0
        assert result.stdout.strip() == ""


def test_preflight_never_falls_back_to_the_owner_env() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the reported production mismatch was a
    preflight that read the owner's unfiltered `.env` while the runtime read
    `.env.core`. The fallback must be gone, and both proofs must run through the
    shared decision functions rather than a local re-interpretation."""

    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "OWNER_ENV_FALLBACK" not in deploy
    # The owner's `.env` is used ONLY inside the verified legacy branch.
    assert deploy.count('preflight_env="$APP_ROOT/.env"') == 1
    assert 'LEGACY_OWNER_ENV)\n      preflight_env="$APP_ROOT/.env"' in deploy
    assert 'FILTERED)\n      preflight_env="$APP_ROOT/.env.core"' in deploy
    assert '--env-file "$APP_ROOT/.env"' not in deploy
    assert 'core_env_mode="$(opip_core_env_mode "$candidate_tree"' in deploy
    assert "OPIP_PROTECTION_PREFLIGHT_ENV_SOURCE=$preflight_env_source" in deploy
    # A missing filtered file is a refusal, not a substitution.
    assert "refusing before any mutation" in deploy
    compose = (APP / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- .env.core" in compose

@requires_bash
def test_credential_state_never_verifies_a_masked_or_partial_result() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the single decision used by the
    preflight, the running core and the Tradevisor gate. VERIFIED needs the exit
    code AND the explicit PASS AND the PASS identity AND the dedicated-pair
    provenance AND a well-formed fingerprint together; anything less refuses."""

    good = ("0", "PASS", "PASS", "OPIP_F11_READONLY", "0123456789abcdef")
    cases = [
        (good, "VERIFIED"),
        # A masked exit status (`|| true`) is the defect this correction removes.
        (("1",) + good[1:], "REFUSED"),
        (("3",) + good[1:], "REFUSED"),
        # Identity verified, but Balance/OpenPositions did not: verdict FAIL.
        (("3", "FAIL", "PASS", "OPIP_F11_READONLY", "0123456789abcdef"), "REFUSED"),
        (("0", "FAIL", "PASS", "OPIP_F11_READONLY", "0123456789abcdef"), "REFUSED"),
        # A funded/legacy pair is not the approved exception.
        (("0", "PASS", "PASS", "LEGACY_FUNDED_NAME", "0123456789abcdef"), "REFUSED"),
        # Missing or malformed fingerprint proof.
        (("0", "PASS", "PASS", "OPIP_F11_READONLY", "NONE"), "REFUSED"),
        (("0", "PASS", "PASS", "OPIP_F11_READONLY", ""), "REFUSED"),
        (("0", "PASS", "PASS", "OPIP_F11_READONLY", "0123"), "REFUSED"),
        # The explicitly unconfigured posture, and its non-absence variants.
        (("3", "UNVERIFIED", "FAIL", "NONE", "NONE"), "ABSENT"),
        (("0", "UNVERIFIED", "FAIL", "NONE", "NONE"), "REFUSED"),
        (("3", "UNVERIFIED", "FAIL", "INVALID", "NONE"), "REFUSED"),
        (("not-a-code", "", "", "", ""), "REFUSED"),
    ]
    for values, expected in cases:
        body = "opip_f11_credential_state " + " ".join(f"'{v}'" for v in values)
        result = _library_call(body)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected, values


@requires_bash
def test_fingerprint_parity_requires_two_matching_well_formed_values() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: preflight/runtime parity is an equality
    of two verified one-way fingerprints. Missing proof, a truncated value or a
    mismatch must fail - a preflight that does not describe the runtime proves
    nothing about the runtime."""

    fingerprint = "0123456789abcdef"
    other = "fedcba9876543210"
    result = _library_call(
        f"opip_f11_fingerprint_parity {fingerprint} {fingerprint}"
    )
    assert result.returncode == 0, result.stderr
    for left, right in (
        (fingerprint, other),
        (fingerprint, "NONE"),
        ("NONE", fingerprint),
        ("", ""),
        (fingerprint, fingerprint[:8]),
        (fingerprint.upper(), fingerprint.upper()),
    ):
        result = _library_call(f"opip_f11_fingerprint_parity '{left}' '{right}'")
        assert result.returncode != 0, (left, right)


@requires_bash
def test_release_credential_gate_refuses_absent_for_evidence_shadow() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: EVIDENCE_SHADOW requires a verified
    read-only protection credential. The credential-less exception belongs to the
    SAFE_BASELINE rollback posture only and can never be borrowed as an
    EVIDENCE_SHADOW success."""

    for profile, state in (
        ("EVIDENCE_SHADOW", "VERIFIED"),
        ("SAFE_BASELINE", "ABSENT"),
    ):
        result = _library_call(
            f"opip_f11_release_credential_gate {profile} {state}"
        )
        assert result.returncode == 0, (profile, state)
    for profile, state in (
        ("EVIDENCE_SHADOW", "ABSENT"),
        ("EVIDENCE_SHADOW", "REFUSED"),
        ("SAFE_BASELINE", "VERIFIED"),
        ("UNPROVEN", "VERIFIED"),
        ("", ""),
    ):
        result = _library_call(
            f"opip_f11_release_credential_gate '{profile}' '{state}'"
        )
        assert result.returncode != 0, (profile, state)




def test_deploy_proves_the_credential_in_the_running_core() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the deployed core is proved with the
    same read-only verification job the preflight and the Tradevisor gate use.
    The proof must not be masked, must be read through the shared decision, and
    must reproduce the preflight's fingerprint before the release is accepted."""

    deploy = DEPLOY.read_text(encoding="utf-8")
    assert "python -m app.jobs.verify_f11_readonly_credential" in deploy
    # No masked verification: the exit status is captured on both outcomes.
    assert "verify_f11_readonly_credential 2>/dev/null || true" not in deploy
    assert 'F11_READONLY_RC=$?' in deploy
    assert (
        'F11_READONLY_STATE="$(opip_f11_credential_state "$F11_READONLY_RC"'
        in deploy
    )
    assert (
        'opip_f11_release_credential_gate "${CANDIDATE_RELEASE_PROFILE:-UNPROVEN}"'
        in deploy
    )
    assert (
        'opip_f11_fingerprint_parity "${PROTECTION_PREFLIGHT_F11_FINGERPRINT:-}"'
        in deploy
    )
    assert "OPIP_F11_READONLY_FINGERPRINT_PARITY=MATCH" in deploy
    # The proof must run against the RECREATED core, never before it.
    runtime_proof = deploy.index(
        "docker compose exec -T ohm-trade-agent \\\n"
        "  python -m app.jobs.verify_f11_readonly_credential"
    )
    assert deploy.index("docker compose up -d --remove-orphans ohm-trade-agent") < (
        runtime_proof
    )
    # ... and the preflight proof must run before the first runtime mutation.
    preflight_proof = deploy.index(
        "python -m app.jobs.verify_f11_readonly_credential 2>/dev/null)"
    )
    assert preflight_proof < runtime_proof
    assert preflight_proof < deploy.rindex("stop_paper_stack\n")
    # The preflight publishes the fingerprint the runtime must reproduce.
    assert 'PROTECTION_PREFLIGHT_F11_FINGERPRINT="$f11_fingerprint"' in deploy


def test_tradevisor_gate_verifies_the_exception_and_refuses_a_half_pair() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the Tradevisor gate recognises the
    approved read-only protection pair explicitly, verifies it independently from
    inside the core without masking the exit status, requires a VERIFIED state,
    refuses a half pair, and does all of it before any ingress is enabled - so a
    failed gate cannot leave Tradevisor half on."""

    gate = TRADEVISOR.read_text(encoding="utf-8")
    assert "OPIP_F11_KRAKEN_READONLY_API_KEY" in gate
    assert "OPIP_F11_KRAKEN_READONLY_API_SECRET" in gate
    assert "python -m app.jobs.verify_f11_readonly_credential" in gate
    assert "verify_f11_readonly_credential 2>/dev/null || true" not in gate
    assert 'f11_rc=$?' in gate
    assert (
        'f11_state="$(f11_credential_state "$f11_rc" "$f11_identity" '
        '"$f11_verdict"' in gate
    )
    assert '[ "$f11_state" == "VERIFIED" ]' in gate
    assert "pair is incomplete" in gate
    # An absent credential is NOT an EVIDENCE_SHADOW success.
    assert "EVIDENCE_SHADOW requires a verified one" in gate
    # Gate 6 runs inside main() before the ingress flip at line "  enable_ingress".
    assert gate.index("verify_f11_readonly_credential") < gate.index(
        "\n  enable_ingress\n"
    )


def _extract_bash_function(path: pathlib.Path, name: str) -> str:
    """Extract one shipped shell function verbatim, so tests exercise the real
    implementation instead of a re-implementation."""

    lines = path.read_text(encoding="utf-8").splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.startswith(f"{name}() {{"):
            start = index
            break
    assert start is not None, f"{name} is missing from {path.name}"
    end = start
    while end < len(lines) and lines[end] != "}":
        end += 1
    assert lines[end] == "}", f"{name} in {path.name} is not closed at column 0"
    return "\n".join(lines[start : end + 1])


@requires_bash
def test_tradevisor_gate_decision_matches_the_deploy_controller_decision() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the Tradevisor gate and the deploy
    controller ship the same credential decision. Running one scenario matrix
    through both shipped functions proves they cannot drift into a weaker
    reading of one verifier result."""

    gate_function = _extract_bash_function(TRADEVISOR, "f11_credential_state")
    library_function = _extract_bash_function(
        LIBRARY, "opip_f11_credential_state"
    )
    cases = (
        ("0", "PASS", "PASS", "OPIP_F11_READONLY", "0123456789abcdef"),
        ("1", "PASS", "PASS", "OPIP_F11_READONLY", "0123456789abcdef"),
        ("0", "FAIL", "PASS", "OPIP_F11_READONLY", "0123456789abcdef"),
        ("0", "PASS", "FAIL", "OPIP_F11_READONLY", "0123456789abcdef"),
        ("0", "PASS", "PASS", "LEGACY_FUNDED_NAME", "0123456789abcdef"),
        ("0", "PASS", "PASS", "OPIP_F11_READONLY", "NONE"),
        ("3", "FAIL", "UNVERIFIED", "NONE", "NONE"),
        ("0", "FAIL", "UNVERIFIED", "NONE", "NONE"),
        ("3", "FAIL", "UNVERIFIED", "INVALID", "NONE"),
    )
    for values in cases:
        args = " ".join(f"'{value}'" for value in values)
        script = (
            "set -Eeuo pipefail\n"
            f"{gate_function}\n"
            f"{library_function}\n"
            f"gate_state=\"$(f11_credential_state {args})\"\n"
            f"library_state=\"$(opip_f11_credential_state {args})\"\n"
            'printf "%s|%s\\n" "$gate_state" "$library_state"\n'
        )
        result = subprocess.run(
            [BASH, "-c", script], capture_output=True, text=True, check=False
        )
        _skip_if_bash_cannot_fork(result)
        assert result.returncode == 0, result.stderr
        gate_state, library_state = result.stdout.strip().split("|")
        assert gate_state == library_state, values



# ---------------------------------------------------------------------------
# AUTH_CONFIG incident recovery: fresh proof only, idempotent, own key only.
# ---------------------------------------------------------------------------

_PROOF_MARKERS = {
    "OPIP_F11_AUTH_PROOF_KEY_NAME": PROTECTION_KEY_NAME,
    "OPIP_F11_AUTH_PROOF_FINGERPRINT": "fingerprint-marker-not-a-secret",
    "OPIP_F11_AUTH_PROOF_BALANCE": "OK:2",
    "OPIP_F11_AUTH_PROOF_OPEN_POSITIONS": "OK:0",
}


def _incident_rows(state_file: pathlib.Path):
    from app.services import system_incidents as incidents

    return incidents.read_incidents(
        state_file=state_file, include_recovered=True, include_archived=False
    )


def _row_for(state_file: pathlib.Path, key: str):
    for row in _incident_rows(state_file):
        if row.get("incident_key") == key:
            return row
    return None


def _open_auth_incident(monkeypatch, tmp_path):
    from app.services import system_incidents as incidents

    state_file = tmp_path / "system_incidents.json"
    monkeypatch.setattr(incidents, "STATE_FILE", state_file)
    incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
        scope=incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        reason="Kraken private credentials are not configured",
    )
    return state_file


def test_recovery_requires_matching_evidence_and_is_idempotent(
    monkeypatch, tmp_path, capsys
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the AUTH_CONFIG recovery closes the
    row only from a fresh passing proof, prints a plan without mutating, and a
    second run is idempotent instead of inventing a second recovery."""
    from app.jobs import recover_f11_readonly_auth_incident as recovery
    from app.services import system_incidents as incidents

    state_file = _open_auth_incident(monkeypatch, tmp_path)
    key = incidents.incident_key(incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH)
    monkeypatch.setattr(recovery, "_proof", lambda: (True, "PROOF_PASS", dict(_PROOF_MARKERS)))

    assert recovery.run(apply=False) == 77
    assert _row_for(state_file, key)["state"] in ("OPEN", "CHANGED")

    assert recovery.run(apply=True) == 0
    recovered = _row_for(state_file, key)
    assert recovered["state"] == "RECOVERED"
    assert recovered["recovered_at"]

    capsys.readouterr()
    assert recovery.run(apply=True) == 0
    assert "OPIP_F11_AUTH_RECOVERY=ALREADY_RECOVERED" in capsys.readouterr().out


def test_recovery_refuses_when_the_proof_fails(monkeypatch, tmp_path, capsys) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: an unproven credential leaves the
    incident open and admissions suspended - the row is never closed on a
    request, a cached value or a partial success."""
    from app.jobs import recover_f11_readonly_auth_incident as recovery
    from app.services import system_incidents as incidents

    state_file = _open_auth_incident(monkeypatch, tmp_path)
    key = incidents.incident_key(incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH)
    monkeypatch.setattr(
        recovery, "_proof", lambda: (False, "BALANCE_UNPROVEN", dict(_PROOF_MARKERS))
    )

    assert recovery.run(apply=True) == 3
    assert "OPIP_F11_AUTH_RECOVERY=REFUSED" in capsys.readouterr().out
    assert _row_for(state_file, key)["state"] in ("OPEN", "CHANGED")
    assert _row_for(state_file, key)["recovered_at"] is None


def test_recovery_only_closes_its_own_incident_key(monkeypatch, tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: an unrelated open incident is not
    touched by this recovery - it closes one scope, and only that scope."""
    from app.jobs import recover_f11_readonly_auth_incident as recovery
    from app.services import system_incidents as incidents

    state_file = _open_auth_incident(monkeypatch, tmp_path)
    incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED,
        scope=incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT,
        reason="Kraken API rate limit reached",
    )
    other_key = incidents.incident_key(incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT)
    monkeypatch.setattr(recovery, "_proof", lambda: (True, "PROOF_PASS", dict(_PROOF_MARKERS)))

    assert recovery.run(apply=True) == 0
    other = _row_for(state_file, other_key)
    assert other["state"] != "RECOVERED"
    assert other["recovered_at"] is None


def test_fingerprint_is_stable_and_value_free() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the parity proof is a stable one-way
    fingerprint, so preflight and runtime can be compared without either of them
    carrying a secret value."""
    kwargs = {
        "api_key": "stub-api-key",
        "key_name": PROTECTION_KEY_NAME,
        "permissions": PROTECTION_PERMISSIONS,
        "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
    }
    first = protection_fingerprint(**kwargs)
    assert first == protection_fingerprint(**kwargs)
    assert len(first) == 16
    assert "stub-api-key" not in first
    # A different credential, or a different scope set, is a different identity.
    assert first != protection_fingerprint(**{**kwargs, "api_key": "other-key"})
    assert first != protection_fingerprint(
        **{**kwargs, "permissions": PROTECTION_PERMISSIONS | {"query-ledger"}}
    )
    # An equivalent single-host allowlist representation is the same identity.
    assert first == protection_fingerprint(
        **{**kwargs, "ip_allowlist": [PROTECTION_HOST]}
    )
    assert normalize_ip_allowlist([PROTECTION_IP_ALLOWLIST_ENTRY]) == (PROTECTION_HOST,)
    assert normalize_ip_allowlist([PROTECTION_HOST]) == (PROTECTION_HOST,)








# ---------------------------------------------------------------------------
# The verification job's own verdict.
#
# PASS is the WHOLE proof: identity AND Balance AND OpenPositions AND a
# successful process exit. These run the shipped `main()` against stub clients
# and pin every way the implementation could report PASS without proof.
# ---------------------------------------------------------------------------


class _UnconfiguredClient(_StubClient):
    """A client with no credential at all (the explicitly absent posture)."""

    def __init__(self, *, problem: str = ""):
        super().__init__(result=_result())
        # No pair at all: the client is disabled, as it is on a host that has
        # configured no protection credential.
        self.api_key = ""
        self.api_secret = ""
        self.credential_source = ""
        self.credential_problem = problem


class _BalanceUnreadable(_StubClient):
    def get_balance(self):
        raise KrakenPrivateAPIError("balance unavailable")


class _PositionsUnreadable(_StubClient):
    def get_open_positions(self):
        raise KrakenPrivateAPIError("open positions unavailable")


def _verifier_markers(monkeypatch, client) -> tuple[int, dict[str, str], str]:
    from app.jobs import verify_f11_readonly_credential as verifier

    monkeypatch.setattr(verifier, "KrakenPrivateClient", lambda: client)
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        code = verifier.main()
    stdout = captured.getvalue()
    markers = dict(
        line.split("=", 1) for line in stdout.splitlines() if "=" in line
    )
    return code, markers, stdout


def test_verifier_passes_only_when_identity_balance_and_positions_all_hold(
    monkeypatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the full proof - and only the full
    proof - exits 0 with VERDICT=PASS and carries the real fingerprint."""

    client = _StubClient(
        result=_result(), balances={"ZUSD": 1.0}, positions={"XBTUSD": {}}
    )
    client.credential_source = SOURCE_PROTECTION
    code, markers, _ = _verifier_markers(monkeypatch, client)

    assert code == 0
    assert markers["OPIP_F11_READONLY_VERDICT"] == "PASS"
    assert markers["OPIP_F11_READONLY_IDENTITY"] == "PASS"
    assert markers["OPIP_F11_READONLY_CREDENTIAL_SOURCE"] == SOURCE_PROTECTION
    assert markers["OPIP_F11_READONLY_BALANCE"] == "OK:1"
    assert markers["OPIP_F11_READONLY_OPEN_POSITIONS"] == "OK:1"
    assert markers["OPIP_F11_READONLY_FINGERPRINT"] == protection_fingerprint(
        api_key=client.api_key,
        key_name=PROTECTION_KEY_NAME,
        permissions=PROTECTION_PERMISSIONS,
        ip_allowlist=[PROTECTION_IP_ALLOWLIST_ENTRY],
    )


def test_verifier_never_reports_pass_when_balance_fails(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the identity is right but the account
    is unreadable. That is NOT a PASS and NOT a successful command - the defect
    was a PASS verdict with a non-zero exit that the controller then ignored."""

    client = _BalanceUnreadable(result=_result())
    client.credential_source = SOURCE_PROTECTION
    code, markers, stdout = _verifier_markers(monkeypatch, client)

    assert code != 0
    assert markers["OPIP_F11_READONLY_IDENTITY"] == "PASS"
    assert markers["OPIP_F11_READONLY_VERDICT"] == "FAIL"
    assert markers["OPIP_F11_READONLY_BALANCE"].startswith("FAIL:")
    assert markers["OPIP_F11_READONLY_REASON"] == "BALANCE_UNPROVEN"
    assert "OPIP_F11_READONLY_VERDICT=PASS" not in stdout


def test_verifier_never_reports_pass_when_open_positions_fail(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: Balance succeeding is not enough;
    OpenPositions is the second read F11 depends on."""

    client = _PositionsUnreadable(result=_result(), balances={"ZUSD": 1.0})
    client.credential_source = SOURCE_PROTECTION
    code, markers, stdout = _verifier_markers(monkeypatch, client)

    assert code != 0
    assert markers["OPIP_F11_READONLY_VERDICT"] == "FAIL"
    assert markers["OPIP_F11_READONLY_BALANCE"] == "OK:1"
    assert markers["OPIP_F11_READONLY_OPEN_POSITIONS"].startswith("FAIL:")
    assert markers["OPIP_F11_READONLY_REASON"] == "OPEN_POSITIONS_UNPROVEN"
    assert "OPIP_F11_READONLY_VERDICT=PASS" not in stdout


def test_verifier_reports_absent_without_any_credential(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the explicitly unconfigured posture is
    reported as absent (UNVERIFIED identity, source NONE) and still exits
    non-zero, so no caller can mistake it for a verified credential."""

    code, markers, stdout = _verifier_markers(monkeypatch, _UnconfiguredClient())

    assert code != 0
    assert markers["OPIP_F11_READONLY_IDENTITY"] == "UNVERIFIED"
    assert markers["OPIP_F11_READONLY_VERDICT"] == "FAIL"
    assert markers["OPIP_F11_READONLY_CREDENTIAL_SOURCE"] == "NONE"
    assert markers["OPIP_F11_READONLY_FINGERPRINT"] == "NONE"
    assert "OPIP_F11_READONLY_VERDICT=PASS" not in stdout


def test_verifier_reports_a_half_pair_as_invalid_not_absent(monkeypatch) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a broken rotation is a configuration
    fault. It must not degrade into the benign "no credential" posture."""

    client = _UnconfiguredClient(problem="OPIP_F11_KRAKEN_READONLY_API_KEY ...")
    code, markers, _ = _verifier_markers(monkeypatch, client)

    assert code != 0
    assert markers["OPIP_F11_READONLY_CREDENTIAL_SOURCE"] == "INVALID"
    assert markers["OPIP_F11_READONLY_VERDICT"] == "FAIL"


def test_verifier_refuses_a_key_that_is_not_the_dedicated_protection_key(
    monkeypatch,
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a read-only key that fails identity is
    a FAIL verdict with no fingerprint - never a PASS with a caveat."""

    client = _StubClient(result=_result(api_key_name="SOME-OTHER-READONLY-KEY"))
    client.credential_source = SOURCE_PROTECTION
    code, markers, _ = _verifier_markers(monkeypatch, client)

    assert code != 0
    assert markers["OPIP_F11_READONLY_IDENTITY"] == "FAIL"
    assert markers["OPIP_F11_READONLY_VERDICT"] == "FAIL"
    assert markers["OPIP_F11_READONLY_FINGERPRINT"] == "NONE"
    assert markers["OPIP_F11_READONLY_BALANCE"] == "NOT_RUN"

