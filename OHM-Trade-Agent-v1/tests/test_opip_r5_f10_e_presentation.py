"""F10-E presentation acceptance tests: the served asset's interaction and accessibility contract.

Increment: ``ATDD-R5-F10-E-operator-interaction-presentation``.

These are **structural assertions about the served asset**, exercised against the bytes the
router actually serves. They are deliberately not labelled as operator validation: no headless
browser runs here, so what is proven is that the asset carries the semantics an operator and an
assistive technology depend on, not that a human completed a journey in a real browser. The
journey half of the interaction contract is proven separately, over HTTP, by
``test_opip_r5_f10_e_operator_interaction.py``.

They claim no parity and change no authority: the asset is a presentation of readings, and none
of its controls can act on a position, a policy, a release or a deployment.
"""

from __future__ import annotations

import pathlib
import re
import sys

import pytest

from app.api import cockpit_semantic as read_router

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)

INCREMENT = "ATDD-R5-F10-E-operator-interaction-presentation"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
ASSET = APP_ROOT / "app" / "api" / "cockpit_semantic.html"

#: What the asset must never hold: browser persistence, an order path, or a second surface.
FORBIDDEN: tuple[str, ...] = (
    "localStorage",
    "sessionStorage",
    "document.cookie",
    "indexedDB",
    "place_order",
    "cancel_order",
    "modify_order",
    "create_order",
    "CanonicalWriter",
    "include_router",
    "app.exchange",
    "api.telegram",
)

pytestmark = pytest.mark.acceptance


def _asset() -> str:
    return ASSET.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# AC-001: the served asset is the reviewed file, storage-free and declaration-driven
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_the_served_asset_is_the_reviewed_file_and_drives_no_state_of_its_own():
    """ATDD-R5-F10-E-operator-interaction-presentation/AC-001: a view, not a store.

    GIVEN the page asset the read router serves and the reviewed file on disk
    WHEN the bytes served, the asset text and its sources of truth are inspected
    THEN the asset is served as one static GET of exactly the reviewed file, it persists
    nothing in the browser, it holds no order, exchange or mounting control, and its filter and
    preset controls are built from the declarations the read model published rather than from a
    list the asset keeps
    """
    text = _asset()
    assert text.strip()
    for token in FORBIDDEN:
        assert token not in text, token
    assert "/api/cockpit/semantic" in text
    assert not re.search(r"method\s*:\s*['\"](post|put|patch|delete)['\"]", text, re.I)

    served = read_router.semantic_page_asset()
    assert pathlib.Path(served.path) == read_router.SEMANTIC_PAGE_FILE == ASSET
    assert pathlib.Path(served.path).read_text(encoding="utf-8") == text
    assert read_router.route_methods()[read_router.PAGE_ROUTE] == {"GET"}

    # Declaration-driven: the controls are built from the published catalog, and no control is
    # built from a dimension name the asset wrote down for itself. Row *field* names do appear,
    # because a table column is a field name; a *filter* control never is.
    assert "index.filters" in text
    assert "index.presets" in text
    assert "entry.dimension" in text
    assert 'control.name = entry.dimension' in text
    for dimension in (
        "quote_currency",
        "instrument",
        "lifecycle_status",
        "economic_result",
        "execution_result",
        "exit_mechanism",
        "policy_version",
        "window_start",
        "window_end",
    ):
        assert f"filter-{dimension}" not in text, dimension
    # The credential is sent as a header and never becomes part of a link or a query string.
    assert text.count("x-webhook-secret") == 1
    assert "params.append(\"secret\"" not in text
    assert "secret" in text


# --------------------------------------------------------------------------- #
# AC-002: navigation, breadcrumbs and evidence links are real, labelled elements
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_navigation_breadcrumbs_and_evidence_links_are_labelled_elements():
    """ATDD-R5-F10-E-operator-interaction-presentation/AC-002: navigable, not decorative.

    GIVEN the served asset and the navigation the read model publishes
    WHEN the asset's landmarks, tab semantics, breadcrumb and drill link are inspected
    THEN the page has a named navigation landmark and a content landmark reachable by a skip
    link, the page tabs declare themselves as a tablist and state which tab is current, the
    breadcrumb names the location, and each panel's evidence link is a real anchor built from
    the drill target the read model published
    """
    text = _asset()

    assert re.search(r'<a class="skip" href="#main">', text)
    assert re.search(r'<nav id="crumbs"[^>]*aria-label=', text)
    assert re.search(r'role="tablist"[^>]*aria-label=', text) or re.search(
        r'aria-label="[^"]*"[^>]*role="tablist"', text
    )
    assert 'setAttribute("role", "tab")' in text
    assert 'setAttribute("aria-selected"' in text
    assert "crumbs.setAttribute" in text
    assert 'document.getElementById("crumbs")' in text
    assert "page.descriptor.adopted_name" in text

    # The evidence link is an anchor built from the published drill target, so it can be opened,
    # copied or fetched directly.
    assert '<a href="' in text
    assert "panel.drill.route" in text
    assert "panel.drill.query" in text
    assert "queryString(panel.drill.query)" in text

    # Tables keep their relationships and state their own purpose.
    assert "<caption>" in text
    assert 'scope="col"' in text
    assert "data-label" in text
    assert "renderTable(value.entries.slice(0, 25), columns)" in text


# --------------------------------------------------------------------------- #
# AC-003: keyboard operation, visible focus and announced state
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_keyboard_focus_and_announced_state():
    """ATDD-R5-F10-E-operator-interaction-presentation/AC-003: operable without a pointer.

    GIVEN the served asset
    WHEN its focus styling, control types, tablist keyboard handling and live regions are
    inspected
    THEN focus is visible rather than implied, every control is a native control that the
    platform activates, the tablist responds to arrow keys, and the status and error regions are
    announced instead of being silent text
    """
    text = _asset()

    assert ":focus-visible" in text
    assert "outline" in text
    assert ".skip:focus" in text

    # Native controls: the platform supplies activation, focus order and roles.
    assert 'createElement("button")' in text
    assert 'createElement("input")' in text
    assert 'createElement("select")' in text
    assert 'button.type = "button"' in text
    assert 'apply.type = "submit"' in text

    # Tabs answer arrow keys and only the current tab is in the tab order, which is what a
    # tablist promises a keyboard.
    assert 'event.key !== "ArrowRight" && event.key !== "ArrowLeft"' in text
    assert "tabIndex = entry.page_id === active ? 0 : -1" in text
    assert ".focus()" in text

    # State is announced. The status region is polite, the error region is an alert, the
    # content landmark takes focus after a page loads, and the filter form is a named search.
    assert 'role="status"' in text
    assert 'aria-live="polite"' in text
    assert 'role="alert"' in text
    assert 'role="search"' in text
    assert 'role="group"' in text
    assert '<main id="main" tabindex="-1">' in text
    assert 'document.getElementById("main").focus()' in text

    # Labels are associated with their controls, so a label is not decoration.
    assert 'label.setAttribute("for", "filter-" + entry.dimension)' in text
    assert 'control.id = "filter-" + entry.dimension' in text
    assert "sr-only" in text


# --------------------------------------------------------------------------- #
# AC-004: responsive rendering, explicit failure and session-only view state
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_responsive_failure_and_session_only_view_state():
    """ATDD-R5-F10-E-operator-interaction-presentation/AC-004: narrow screens and honest failure.

    GIVEN the served asset and the states the read model can report
    WHEN its layout rules, failure handling, withheld rendering and view state are inspected
    THEN the layout reflows on a narrow viewport instead of forcing sideways scrolling, a failed
    read replaces the view with an explicit statement rather than leaving a stale one, a withheld
    panel is rendered as withheld with its reason and gap, and the view state lives only in the
    tab with the non-secret question in the address bar
    """
    text = _asset()

    assert "@media" in text
    assert "max-width" in text
    assert "flex-wrap: wrap" in text

    # Failure is explicit and cannot leave a stale rendering standing as the answer.
    assert "could not be read" in text
    assert "failed to load" in text
    assert 'document.getElementById("error")' in text
    assert "error.textContent = String(exc)" in text

    # Withheld and unavailable are rendered, never silently omitted or zeroed.
    assert "WITHHELD - no value is published for this panel" in text
    assert "panel.panel_reasons" in text
    assert "panel.evidence_gap" in text
    assert "unresolved_filters" in text

    # Session-only view state, a bookmarkable non-secret question, and no credential in a URL.
    assert "let viewState = { pageId: null, filters: {} }" in text
    assert "new URLSearchParams(window.location.search)" in text
    assert 'history.replaceState(null, "", "?page="' in text
    assert "queryString(viewState.filters)" in text
    assert "secret" not in re.findall(r"history\.replaceState\([^)]*\)", text)[0]


# --------------------------------------------------------------------------- #
# AC-005: the report, the pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-E-operator-interaction-presentation/AC-005: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer and
    the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records this increment with the base head it stacks on, its own contract and
    its own test count, the pointer resolves to a declared contract, every approved criterion is
    traced to the test that cites it and mapped to a path, and the parity row stays OUTSTANDING
    """
    contracts = load_scope_contracts(CONTRACT_DIR)
    declared = {item.increment for item in contracts}
    assert INCREMENT in declared
    assert read_increment_pointer(POINTER) in declared
    assert (CONTRACT_DIR / f"{read_increment_pointer(POINTER)}.md").is_file()

    ours = [item for item in contracts if item.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id
        for path in contract.implementation_map[ac_id]:
            assert path.startswith("OHM-Trade-Agent-v1/"), path

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", source))
    assert cited == set(contract.acceptance_criteria)

    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    assert "pull/376" in report and "pull/377" in report and "pull/378" in report
    assertions = len(re.findall(r"@pytest\.mark\.acceptance", source))
    assert f"{assertions} structural assertions" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report
