"""ATDD — Control Center Round 2 source-backed read surface.

ATDD-SCANNER-V2-EVIDENCE-M01/AC-005. These are source-level contract checks on
the shipped read-only surfaces: they exist because the previous overview showed
hard-coded statuses, the qualification and intelligence tabs dumped raw JSON,
and the credential was persisted in browser storage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INCREMENT = "ATDD-SCANNER-V2-EVIDENCE-M01"
APP_ROOT = Path(__file__).resolve().parents[1]
UI = APP_ROOT / "control-center-v2"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_control_center_render_helpers_are_source_backed():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-005: Every displayed figure comes from a recorded payload or is unverified."""
    api = _read(UI / "src" / "api.mjs")

    # The read shapes that replaced the raw-JSON dumps and hard-coded cards.
    for helper in (
        "export function utcWithLocal",
        "export function qualificationFunnel",
        "export function rejectionBreakdown",
        "export function aiOperatingState",
        "export function configAdminState",
    ):
        assert helper in api, helper

    # An unreturned field is unverified; it is never coerced to zero.
    assert "function _count(value)" in api
    assert "return typeof value==='number'&&Number.isFinite(value)?value:null;" in api

    # Six distinguishable AI states: never reached, no invocation, unavailable,
    # exhausted budget, failed invocation and invoked.
    for state in (
        "NO_EVIDENCE",
        "NOT_REACHED",
        "NO_INVOCATION",
        "UNAVAILABLE",
        "BUDGET_EXHAUSTED",
        "FAILED_INVOCATION",
        "INVOKED",
    ):
        assert state in api, state

    # A 404 configuration capability is a deployment fact, not a credential
    # failure and not an empty form.
    assert "NOT_AVAILABLE" in api and "CREDENTIAL_REFUSED" in api

    view = _read(UI / "src" / "main.jsx")
    # Qualification renders the funnel and the rejection breakdown, and JSON is
    # only an optional advanced view.
    assert "qualificationFunnel(records.zero)" in view
    assert "rejectionBreakdown(records.zero)" in view
    assert "<details><summary>Advanced JSON evidence</summary>" in view
    # Config admin cannot present a usable form when the capability is absent.
    assert "if(availability!=='READY')" in view
    assert "configAdminState" in view


@pytest.mark.acceptance(f"{INCREMENT}/AC-005")
def test_control_center_never_persists_credentials_and_labels_utc():
    """ATDD-SCANNER-V2-EVIDENCE-M01/AC-005: The credential is memory-only, collapses after a read, and time is UTC."""
    view = _read(UI / "src" / "main.jsx")
    scoring = _read(APP_ROOT / "app" / "api" / "scoring_comparison.html")

    for surface in (view, scoring):
        # Usage, not the word: a comment may legitimately say the credential is
        # never written to browser storage, but no read or write may exist.
        for forbidden in (
            "localStorage.setItem",
            "localStorage.getItem",
            "sessionStorage.setItem",
            "sessionStorage.getItem",
            "window.localStorage",
            "window.sessionStorage",
            "document.cookie",
            "indexedDB.open",
        ):
            assert forbidden not in surface, forbidden

    # Collapse after an accepted read, with an explicit re-entry path.
    assert "credentialLocked" in view
    assert ">Change credential</button>" in view
    for change in ("Change credential", "lock(false)", "credential=\"\""):
        assert change in scoring, change

    # UTC is labelled and the local rendering is shown beside it, never instead.
    assert "utcWithLocal(loadedAt)" in view
    assert "Today (UTC day)" in view
    assert "window boundaries are UTC" in view
    assert "Every timestamp is UTC." in scoring
