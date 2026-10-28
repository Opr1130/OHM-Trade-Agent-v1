"""ATDD-RELEASE-TREE-DEPLOY-v1/AC-014 - the pre-deploy incident-recovery deadlock.

The deadlock this pins:

* ``KRAKEN:READ_ONLY_AUTH`` is ``AUTH_CONFIG``-owned - only a fresh authoritative
  credential probe may close it, and the active-trade monitor may not;
* while the row is OPEN, the EVIDENCE_SHADOW readiness gate reports
  ``BLOCK_INCIDENT_OPEN`` and the release protection preflight refuses BEFORE
  mutation;
* the in-release recovery job only exists in the release that the blocked
  preflight is trying to install.

``deploy/remote/recover-f11-readonly-auth-incident.sh`` breaks it without
inventing authority: it runs inside the CURRENTLY DEPLOYED core image, proves the
dedicated read-only credential with a fresh GetApiKeyInfo + Balance +
OpenPositions, and then performs the single existing
``system_incidents.observe_recovery(AUTH_CONFIG)`` transition for exactly this
scope.

Every case here is a stub. No live Kraken key is used, no order is placed, and no
production state is touched: the incident registry is a temporary file.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import pathlib
import re
import runpy
import shutil
import subprocess
import sys

import pytest

from app.exchanges.kraken_private import KrakenPrivateAPIError, KrakenPrivateClient
from app.exchanges.kraken_protection_credentials import (
    PROTECTION_HOST,
    PROTECTION_IP_ALLOWLIST_ENTRY,
    PROTECTION_KEY_NAME,
    PROTECTION_PERMISSIONS,
    protection_fingerprint,
)

APP = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = APP / "deploy" / "remote" / "recover-f11-readonly-auth-incident.sh"
BASH = shutil.which("bash")

pytestmark = [pytest.mark.acceptance]

#: The wrapper-execution cases are marked individually: the probe's own
#: behaviour is Python and runs everywhere, the shell cases run in CI.
requires_bash = pytest.mark.skipif(BASH is None, reason="bash is required")

#: Exit statuses that mean "bash could not start at all" on this host, not "the
#: script failed" (Windows raises 0xC0000142 when the shell cannot initialise).
_BASH_UNAVAILABLE_CODES = {3221225794, -1073741502, 3221225477}


def _skip_if_bash_unusable(result) -> None:
    text = f"{result.stderr}{result.stdout}"
    if result.returncode in _BASH_UNAVAILABLE_CODES or (
        result.returncode != 0
        and (
            "fork: Resource temporarily unavailable" in text
            or "dofork" in text
        )
    ):
        pytest.skip("bash cannot run reliably in this environment")


_SECRET = "stub-secret-not-a-real-key"
_KEY = "stub-api-key"


class _ProbeClient(KrakenPrivateClient):
    """A read-only client double: no network, no ambient environment."""

    def __init__(self, *, result=None, balance_error=None, positions_error=None):
        super().__init__(
            api_key=_KEY, api_secret=base64.b64encode(_SECRET.encode()).decode()
        )
        self._result = result
        self._balance_error = balance_error
        self._positions_error = positions_error
        self.calls: list[str] = []

    def _post(self, endpoint, params=None):
        self.calls.append(endpoint)
        if endpoint == "GetApiKeyInfo":
            return self._result
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def get_balance(self):
        self.calls.append("Balance")
        if self._balance_error is not None:
            raise self._balance_error
        return {"ZUSD": 1.0}

    def get_open_positions(self):
        self.calls.append("OpenPositions")
        if self._positions_error is not None:
            raise self._positions_error
        return {}


def _result(**overrides):
    payload = {
        "api_key_name": PROTECTION_KEY_NAME,
        "permissions": sorted(PROTECTION_PERMISSIONS),
        "ip_allowlist": [PROTECTION_IP_ALLOWLIST_ENTRY],
    }
    payload.update(overrides)
    return payload


def _payload_text() -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"<<'PY'\n(.*?)\nPY\n", text, re.S)
    assert match, "the embedded recovery probe payload is missing"
    return match.group(1)


def _payload_path(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "recovery_probe.py"
    path.write_text(_payload_text(), encoding="utf-8")
    return path


def _open_incident(state_file: pathlib.Path):
    from app.services import system_incidents as incidents

    incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE,
        scope=incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH,
        reason="Kraken read-only credentials are not configured",
        state_file=state_file,
    )
    return incidents.incident_key(incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH)


def _rows(state_file: pathlib.Path) -> dict[str, dict]:
    from app.services import system_incidents as incidents

    return {
        row["incident_key"]: row
        for row in incidents.read_incidents(
            state_file=state_file, include_recovered=True, include_archived=False
        )
    }


def _state_path(tmp_path: pathlib.Path) -> pathlib.Path:
    return tmp_path / "system_incidents.json"


def _run_payload(
    monkeypatch, tmp_path, *, mode: str, client, state_file: pathlib.Path
) -> tuple[int, str]:
    """Execute the SHIPPED probe payload against a temporary registry."""

    from app.exchanges import kraken_private as private
    from app.services import system_incidents as incidents

    monkeypatch.setattr(incidents, "STATE_FILE", state_file)
    monkeypatch.setattr(private, "KrakenPrivateClient", lambda **_: client)
    monkeypatch.setenv("OPIP_F11_KRAKEN_READONLY_API_KEY", _KEY)
    monkeypatch.setenv("OPIP_F11_KRAKEN_READONLY_API_SECRET", _SECRET)
    monkeypatch.setattr(sys, "argv", ["recovery_probe", mode])

    captured = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(captured):
            runpy.run_path(str(_payload_path(tmp_path)), run_name="__main__")
    except SystemExit as exc:  # the payload reports through its exit status
        code = int(exc.code or 0)
    return code, captured.getvalue()


def _markers(stdout: str) -> dict[str, str]:
    return dict(
        line.split("=", 1) for line in stdout.splitlines() if "=" in line
    )


# ---------------------------------------------------------------------------
# The fresh proof, and the single sanctioned transition.
# ---------------------------------------------------------------------------


def test_probe_recovers_only_the_matching_incident_from_fresh_proof(
    monkeypatch, tmp_path
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a fresh, fully verified proof closes
    exactly the durable row this scope owns, through the existing AUTH_CONFIG
    recovery authority - and records only non-secret evidence."""

    state_file = _state_path(tmp_path)
    key = _open_incident(state_file)
    client = _ProbeClient(result=_result())

    code, stdout = _run_payload(
        monkeypatch, tmp_path, mode="apply", client=client, state_file=state_file
    )
    markers = _markers(stdout)

    assert code == 0
    assert markers["OPIP_F11_RECOVERY"] == "RECOVERED"
    assert markers["OPIP_F11_RECOVERY_INCIDENT_KEY"] == key
    assert markers["OPIP_F11_RECOVERY_INCIDENT_SCOPE"] == "KRAKEN:READ_ONLY_AUTH"
    assert markers["OPIP_F11_RECOVERY_IDENTITY"] == "PASS"
    assert markers["OPIP_F11_RECOVERY_KEY_NAME"] == PROTECTION_KEY_NAME
    assert markers["OPIP_F11_RECOVERY_BALANCE"] == "OK:1"
    assert markers["OPIP_F11_RECOVERY_OPEN_POSITIONS"] == "OK:0"
    assert markers["OPIP_F11_RECOVERY_INCIDENT_STATE_AFTER"] == "RECOVERED"
    # The probe reads the key, then the two F11 queries - and nothing else.
    assert client.calls == ["GetApiKeyInfo", "Balance", "OpenPositions"]

    row = _rows(state_file)[key]
    assert row["state"] == "RECOVERED"
    assert row["recovered_at"]
    raw = json.loads(state_file.read_text(encoding="utf-8"))["incidents"][key]
    assert raw["recovery_authority"] == "AUTH_CONFIG"
    assert markers["OPIP_F11_RECOVERY_FINGERPRINT"] in raw["recovery_evidence"]
    assert _KEY not in json.dumps(raw)
    assert _SECRET not in stdout


def test_probe_fingerprint_matches_the_release_fingerprint_helper(
    monkeypatch, tmp_path
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the probe's fingerprint is the SAME
    one-way digest the release records, so a recovery can be matched against the
    deployment receipt without either side holding a secret."""

    state_file = _state_path(tmp_path)
    _open_incident(state_file)
    code, stdout = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 0
    expected = protection_fingerprint(
        api_key=_KEY,
        key_name=PROTECTION_KEY_NAME,
        permissions=PROTECTION_PERMISSIONS,
        ip_allowlist=[PROTECTION_IP_ALLOWLIST_ENTRY],
    )
    assert _markers(stdout)["OPIP_F11_RECOVERY_FINGERPRINT"] == expected


def test_probe_plan_mode_writes_nothing(monkeypatch, tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: plan mode performs the full fresh
    proof and reports PLAN, and the durable row is untouched."""

    state_file = _state_path(tmp_path)
    key = _open_incident(state_file)
    before = state_file.read_bytes()

    code, stdout = _run_payload(
        monkeypatch,
        tmp_path,
        mode="plan",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 77
    assert _markers(stdout)["OPIP_F11_RECOVERY"] == "PLAN"
    assert _markers(stdout)["OPIP_F11_RECOVERY_IDENTITY"] == "PASS"
    assert state_file.read_bytes() == before
    assert _rows(state_file)[key]["recovered_at"] is None



def test_probe_refuses_when_the_proof_fails(monkeypatch, tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: no proof, no transition. Every failure
    mode leaves the row OPEN with admissions suspended."""

    state_file = _state_path(tmp_path)
    key = _open_incident(state_file)
    cases = {
        "IDENTITY_KEY_MISMATCH": _ProbeClient(
            result=_result(api_key_name="SOME-OTHER-KEY")
        ),
        "SCOPES_MISMATCH": _ProbeClient(
            result=_result(permissions=["query-funds", "query-ledger"])
        ),
        "IP_ALLOWLIST_MISMATCH": _ProbeClient(
            result=_result(ip_allowlist=["0.0.0.0/0"])
        ),
        "BALANCE_UNPROVEN": _ProbeClient(
            result=_result(), balance_error=KrakenPrivateAPIError("no balance")
        ),
        "OPEN_POSITIONS_UNPROVEN": _ProbeClient(
            result=_result(), positions_error=KrakenPrivateAPIError("no positions")
        ),
    }
    for expected_reason, client in cases.items():
        code, stdout = _run_payload(
            monkeypatch,
            tmp_path,
            mode="apply",
            client=client,
            state_file=state_file,
        )
        markers = _markers(stdout)
        assert code == 3, expected_reason
        assert markers["OPIP_F11_RECOVERY"] == "REFUSED"
        assert markers["OPIP_F11_RECOVERY_REASON"] == expected_reason
        row = _rows(state_file)[key]
        assert row["state"] in ("OPEN", "CHANGED")
        assert row["recovered_at"] is None


def test_probe_is_idempotent_when_the_row_is_already_recovered(
    monkeypatch, tmp_path
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: recovery is a fact, not an action. A
    second run reports ALREADY_RECOVERED instead of inventing another one."""

    state_file = _state_path(tmp_path)
    _open_incident(state_file)
    first = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )
    assert first[0] == 0

    code, stdout = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 0
    assert _markers(stdout)["OPIP_F11_RECOVERY"] == "ALREADY_RECOVERED"


def test_probe_never_closes_an_unrelated_incident(monkeypatch, tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the probe acts on one scope. An
    unrelated open incident keeps its own state and its own evidence."""

    from app.services import system_incidents as incidents

    state_file = _state_path(tmp_path)
    _open_incident(state_file)
    incidents.observe_degradation(
        incident_class=incidents.SystemIncidentClass.KRAKEN_RATE_LIMITED,
        scope=incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT,
        reason="Kraken API rate limit reached",
        state_file=state_file,
    )
    other_key = incidents.incident_key(incidents.SystemIncidentScope.KRAKEN_RATE_LIMIT)

    code, _ = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 0
    other = _rows(state_file)[other_key]
    assert other["state"] != "RECOVERED"
    assert other["recovered_at"] is None


def test_probe_refuses_a_row_that_is_not_this_incident_identity(
    monkeypatch, tmp_path
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the row must be this key AND this
    class. Anything else is refused, never "recovered" by association."""

    state_file = _state_path(tmp_path)
    key = _open_incident(state_file)
    payload = json.loads(state_file.read_text(encoding="utf-8"))
    payload["incidents"][key]["incident_class"] = "KRAKEN_READ_ONLY_CONNECTIVITY"
    state_file.write_text(json.dumps(payload), encoding="utf-8")

    code, stdout = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 3
    assert _markers(stdout)["OPIP_F11_RECOVERY_REASON"] == "INCIDENT_IDENTITY_MISMATCH"
    assert _rows(state_file)[key]["recovered_at"] is None


def test_probe_refuses_unreadable_durable_evidence(monkeypatch, tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: a registry that cannot be read is not
    "no incident". It refuses instead of claiming nothing to do."""

    state_file = _state_path(tmp_path)
    state_file.write_text("{not json", encoding="utf-8")

    code, stdout = _run_payload(
        monkeypatch,
        tmp_path,
        mode="apply",
        client=_ProbeClient(result=_result()),
        state_file=state_file,
    )

    assert code == 3
    assert _markers(stdout)["OPIP_F11_RECOVERY_REASON"] == "STATE_UNREADABLE"
    assert state_file.read_text(encoding="utf-8") == "{not json"



# ---------------------------------------------------------------------------
# The owner-run wrapper around the probe.
# ---------------------------------------------------------------------------


def _extract_bash_function(path: pathlib.Path, name: str) -> str:
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


def _run_probe_verdict(values: tuple[str, ...]) -> str:
    function = _extract_bash_function(SCRIPT, "probe_verdict")
    args = " ".join(f"'{value}'" for value in values)
    script = "set -Eeuo pipefail\n" + function + "\n" + f"probe_verdict {args}\n"
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, check=False
    )
    _skip_if_bash_unusable(result)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@requires_bash
@pytest.mark.parametrize(
    ("values", "expected"),
    [
        # (mode, exit code, outcome, state after, incident key)
        (("apply", "0", "RECOVERED", "RECOVERED", "KRAKEN:READ_ONLY_AUTH"),
         "RECOVERED"),
        (("plan", "77", "PLAN", "OPEN", "KRAKEN:READ_ONLY_AUTH"), "PLAN_ONLY"),
        (("apply", "0", "ALREADY_RECOVERED", "RECOVERED", "KRAKEN:READ_ONLY_AUTH"),
         "NOTHING_TO_RECOVER"),
        (("apply", "0", "NO_OPEN_INCIDENT", "NONE", "KRAKEN:READ_ONLY_AUTH"),
         "NOTHING_TO_RECOVER"),
        # A masked/absent exit status never reads as a recovery.
        (("apply", "1", "RECOVERED", "RECOVERED", "KRAKEN:READ_ONLY_AUTH"),
         "REFUSED"),
        (("apply", "", "RECOVERED", "RECOVERED", "KRAKEN:READ_ONLY_AUTH"),
         "REFUSED"),
        # The row must have been observed RECOVERED.
        (("apply", "0", "RECOVERED", "OPEN", "KRAKEN:READ_ONLY_AUTH"), "REFUSED"),
        (("apply", "0", "RECOVERED", "", "KRAKEN:READ_ONLY_AUTH"), "REFUSED"),
        # A different incident key is never "the one".
        (("apply", "0", "RECOVERED", "RECOVERED", "KRAKEN:RATE_LIMIT"), "REFUSED"),
        (("apply", "0", "NO_OPEN_INCIDENT", "NONE", "KRAKEN:RATE_LIMIT"),
         "REFUSED"),
        # Plan outcomes are only valid in plan mode, with the plan exit code.
        (("apply", "77", "PLAN", "OPEN", "KRAKEN:READ_ONLY_AUTH"), "REFUSED"),
        (("plan", "0", "PLAN", "OPEN", "KRAKEN:READ_ONLY_AUTH"), "REFUSED"),
        # An explicit refusal stays a refusal.
        (("apply", "3", "REFUSED", "OPEN", "KRAKEN:READ_ONLY_AUTH"), "REFUSED"),
        (("", "", "", "", ""), "REFUSED"),
    ],
)
def test_probe_verdict_never_reads_a_partial_result_as_success(
    values, expected
) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the wrapper requires the process exit
    status AND the explicit outcome AND a confirmed RECOVERED row, so a masked or
    truncated probe can never be reported as a recovery."""

    assert _run_probe_verdict(values) == expected



@requires_bash
def test_recovery_script_is_syntactically_valid() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: an owner pastes this onto a production
    host, so it must at least parse as bash, and it must be executable."""

    result = subprocess.run(
        [BASH, "-n", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    _skip_if_bash_unusable(result)
    assert result.returncode == 0, result.stderr
    assert SCRIPT.stat().st_mode & 0o111, "the recovery script must be executable"


def test_recovery_probe_only_receives_the_dedicated_pair() -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the probe runs in the RUNNING core
    image, read-only, with exactly the two dedicated names as its environment -
    never the owner's whole `.env` (which carries the funded pair)."""

    script = SCRIPT.read_text(encoding="utf-8")
    # The owner's `.env` is only read, never handed to the container.
    assert '--env-file "$ENV_SOURCE"' not in script
    assert '--env-file "$PAIR_FILE"' in script
    assert 'KEY_ENV="OPIP_F11_KRAKEN_READONLY_API_KEY"' in script
    assert 'SECRET_ENV="OPIP_F11_KRAKEN_READONLY_API_SECRET"' in script
    # Executed identity is the running release, not the candidate.
    assert "docker inspect --format '{{.Image}}' \"$CORE_SERVICE\"" in script
    assert '[[ "$CORE_IMAGE" =~ ^sha256:[0-9a-f]{64}$ ]]' in script
    # Read-only apart from the durable data directory the registry lives in.
    assert "--read-only" in script
    assert '-v "$DATA_ROOT:/app/data"' in script
    assert '-v "$DATA_ROOT:/app/data:ro"' not in script
    # The temporary pair file is removed on every exit path.
    assert "trap cleanup EXIT" in script
    assert 'rm -f -- "$PAIR_FILE"' in script
    # The embedded constants mirror the release authority exactly.
    assert f'KEY_NAME = "{PROTECTION_KEY_NAME}"' in script
    assert f'PROTECTION_HOST = "{PROTECTION_HOST}"' in script
    assert 'PERMISSIONS = frozenset({"query-funds", "query-open-trades"})' in script
    # The incident identity is DERIVED from the release authority, never typed:
    # the durable key carries the alert family.
    assert "SCOPE = system_incidents.SystemIncidentScope.KRAKEN_READ_ONLY_AUTH" in script
    assert "INCIDENT_KEY = system_incidents.incident_key(SCOPE)" in script
    assert (
        "INCIDENT_CLASS = "
        "system_incidents.SystemIncidentClass.KRAKEN_READ_ONLY_AUTH_FAILURE.value"
        in script
    )
    assert 'INCIDENT_SCOPE="KRAKEN:READ_ONLY_AUTH"' in script
    assert "RecoveryAuthority.AUTH_CONFIG" in script
    assert "observe_recovery(" in script
    # It must never claim another deployment or recovery authority.
    assert "ohm-deploy" not in script
    assert "docker compose up" not in script


def _is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid is not None and geteuid() == 0)


@requires_bash
def test_recovery_refuses_to_run_without_root(tmp_path) -> None:
    """ATDD-RELEASE-TREE-DEPLOY-v1/AC-014: the durable registry is root-owned, so
    a non-root invocation refuses (69) before touching anything."""

    if _is_root():
        pytest.skip("running as root: the non-root refusal cannot be observed")

    app_root = tmp_path / "app"
    app_root.mkdir()
    (app_root / ".env").write_text("", encoding="utf-8")
    result = subprocess.run(
        [BASH, str(SCRIPT), "--plan"],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "OPIP_APP_ROOT": str(app_root)},
    )
    _skip_if_bash_unusable(result)
    assert result.returncode == 69, result.stderr
    assert "run the F11 incident recovery as root" in result.stderr

