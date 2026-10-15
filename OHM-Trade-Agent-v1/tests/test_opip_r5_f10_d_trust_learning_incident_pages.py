"""F10-D acceptance tests: the Committee, Learning and Incidents pages are served, not claimed.

Increment: ``ATDD-R5-F10-D-trust-learning-incident-pages``.

These tests prove the *implementation* slice only: that the one page read model serves all
eight frozen pages, that the three pages this increment adds publish only the readings the
service's own dispositions allow and publish their unproducible sections with the producer's
own reason and registered evidence gap, that the two earlier page sets behave exactly as they
were delivered, and that the mounted surface and the parity posture are unchanged.

They claim no parity, settle no projection authority and add no producer: Committee, learning,
incident and host-runtime facts are not on this plane, so they are withheld rather than
invented.
"""

from __future__ import annotations

import ast
import pathlib
import re
import sys
from datetime import datetime, timezone

import pytest

from app.api import cockpit_semantic as read_router
from app.api import cockpit_service
from app.opip.cockpit import semantic
from app.opip.cockpit.ledger import PaperLedger
from app.opip.cockpit.semantic import SemanticReadService
from app.opip.cockpit.semantic_pages import (
    ALL_PAGES,
    DELIVERED_PAGES,
    MOUNTED_SURFACE,
    SLICE_PAGES,
    WITHHELD_SECTIONS,
    PageReadModel,
    UnsupportedSlicePage,
)

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tests.atdd_scope import (  # noqa: E402
    check_scope,
    load_acceptance_index,
    load_scope_contracts,
    read_increment_pointer,
)
from tests.test_atdd_scope_control import (  # noqa: E402
    global_pointer_pin_violations,
)

INCREMENT = "ATDD-R5-F10-D-trust-learning-incident-pages"
PRECEDING_INCREMENT = "ATDD-R5-F10-dashboard-parity-implementation"
PAPER_INCREMENT = "ATDD-R5-F10-C-paper-portfolio-quality"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
PRECEDING_CONTRACT = CONTRACT_DIR / f"{PRECEDING_INCREMENT}.md"
PAPER_CONTRACT = CONTRACT_DIR / f"{PAPER_INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
EXPOSURE_TEST = APP_ROOT / "tests" / "test_bc4_cockpit_exposure_v1.py"

ADDED_PAGES: tuple[str, ...] = (
    "P6_COMMITTEE_VALUE_AI_COST",
    "P7_LEARNING_RELEASES",
    "P8_INCIDENTS_INFRASTRUCTURE",
)

LEGACY_READ_PATHS: tuple[str, ...] = (
    "/cockpit",
    "/api/cockpit/overview",
    "/api/cockpit/trades",
    "/api/cockpit/trades/{paper_trade_id}",
)

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.acceptance


def _model(*, pages: tuple[str, ...] = ALL_PAGES) -> PageReadModel:
    """The read model over an empty reconciled ledger: this slice reads no paper evidence."""
    service = SemanticReadService(
        PaperLedger(entries=()), as_of=NOW, starting_equity=1000.0, identity="TEST"
    )
    return PageReadModel(service, pages=pages)


def _panel_bindings(page_id: str):
    """The frozen registry's panel bindings for one page, in registry order."""
    return tuple(
        semantic.PANEL_BY_ID[panel_id]
        for panel_id in semantic.PAGE_BY_ID[page_id].panels
    )


def _imports(path: pathlib.Path) -> set[str]:
    """Every module name imported by one source file."""
    reached: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)
    return reached


def _router_get_count() -> int:
    """How many GET route decorators the read router declares."""
    tree = ast.parse(pathlib.Path(read_router.__file__).read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
    )


def _section(text: str, name: str) -> str:
    """One scope-contract section body, by its frozen heading."""
    start = f"\n{name}:\n"
    padded = "\n" + text
    body = padded[padded.index(start) + len(start):]
    ends = [body.index(f"\n{other}:\n") for other in (
        "INCREMENT", "OWNER-APPROVED INTENT", "ARCHITECTURE REFERENCES",
        "APPROVED ACCEPTANCE CRITERIA", "EXPLICITLY OUT OF SCOPE", "FROZEN BOUNDARIES",
        "ACCEPTANCE TEST TRACEABILITY", "IMPLEMENTATION MAP", "DEFERRED DISCOVERIES",
        "UNAPPROVED SCOPE CHANGES",
    ) if f"\n{other}:\n" in body]
    return body[: min(ends)] if ends else body


def _census_criteria_for(increment: str) -> set[str]:
    """Every criterion the frozen census plans for one increment."""
    text = CENSUS.read_text(encoding="utf-8")
    return set(re.findall(r"PLANNED:" + re.escape(increment) + r"/(AC-\d{3})", text))


def _acceptance_test_count(path: pathlib.Path) -> int:
    """How many acceptance-marked test functions one module declares."""
    return len(re.findall(r"@pytest\.mark\.acceptance", path.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------- #
# AC-001: the added pages publish only what the service allows
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_the_added_pages_publish_only_what_the_service_allows():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-001: served, not claimed.

    GIVEN the page read model over the semantic read service and the three frozen pages this
    increment adds to its served set
    WHEN each added page and its panels are read
    THEN every page is served with its panels in the registry's own order and the service's own
    state and claimability, the blocked and absent panels are withheld with the producer's own
    reason and registered gap and carry no value, and the one partly served panel publishes
    only its declared read-model posture facts
    """
    model = _model()
    served_values: list[str] = []
    for page_id in ADDED_PAGES:
        descriptor = model.service.page(page_id).descriptor
        page = model.page(page_id)
        assert [panel.panel_id for panel in page.panels] == list(descriptor["panels"]), page_id
        assert list(descriptor["panels"]) == [
            binding.panel_id for binding in _panel_bindings(page_id)
        ], page_id
        assert page.state is model.service.page(page_id).state, page_id
        assert page.claimable is False, page_id
        assert page.withheld_sections, page_id
        for panel in page.panels:
            binding = semantic.PANEL_BY_ID[panel.panel_id]
            if not binding.carries_value:
                assert panel.state is binding.state, panel.panel_id
                assert panel.state not in semantic.SERVED_STATES, panel.panel_id
                assert panel.carries_value is False, panel.panel_id
                assert panel.value is None, panel.panel_id
                assert panel.reasons == tuple(binding.unserved_reasons), panel.panel_id
                assert panel.gap_id == binding.gap_id, panel.panel_id
                assert panel.to_dict()["value"] is None, panel.panel_id
                continue
            served_values.append(panel.panel_id)
            assert panel.carries_value, panel.panel_id
            assert set(panel.value) == set(binding.served), panel.panel_id
            for reason in binding.unserved_reasons:
                assert reason in panel.reasons, panel.panel_id

    # Exactly one panel of the three added pages can publish anything at all, and it is the
    # read model's own posture about the projections this plane reads.
    assert served_values == ["P8-PNL-02"]
    assert set(semantic.PANEL_BY_ID["P8-PNL-02"].served) == {
        "read_model.trust",
        "read_model.reconciliation_posture",
    }

    # The pages this plane can produce nothing for publish nothing, and say so.
    for page_id in ("P6_COMMITTEE_VALUE_AI_COST", "P7_LEARNING_RELEASES"):
        page = model.page(page_id)
        assert page.serving_panels == 0, page_id
        assert page.served_panels == 0, page_id
        assert page.state is semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE, page_id

    unreadable = _model(pages=ADDED_PAGES)
    assert unreadable.pages == ADDED_PAGES
    for page_id in ADDED_PAGES:
        assert unreadable.page(page_id).claimable is False, page_id


# --------------------------------------------------------------------------- #
# AC-002: every unproducible section of the added pages is declared
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_every_unproducible_section_of_the_added_pages_is_declared():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-002: declared, never dropped.

    GIVEN the sections of the three added pages this plane cannot produce, and the frozen
    registry, census and registered reason vocabulary
    WHEN each added page's withheld sections are compared with its own panel declarations and
    its own census criteria
    THEN every unproducible section is declared with a criterion the census records for that
    page and a reason the service itself registers, every qualified panel has its qualification
    declared, and no declared reason or criterion is invented
    """
    census_text = CENSUS.read_text(encoding="utf-8")
    registered_reasons = {
        value
        for name, value in vars(semantic).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert registered_reasons

    prefixes = {
        "P6_COMMITTEE_VALUE_AI_COST": ("P6", 5),
        "P7_LEARNING_RELEASES": ("P7", 6),
        "P8_INCIDENTS_INFRASTRUCTURE": ("P8", 7),
    }
    for page_id, (prefix, expected) in prefixes.items():
        sections = WITHHELD_SECTIONS[page_id]
        assert sections, page_id
        for section, criterion, reason in sections:
            assert section.strip(), criterion
            assert re.fullmatch(r"AC-\d{3}", criterion), criterion
            assert reason in registered_reasons, (criterion, reason)

        census_criteria = set()
        for line in census_text.splitlines():
            if line.startswith(f"| F10-{prefix}-") or line.startswith(f"| {prefix}-PNL-"):
                census_criteria.update(re.findall(r"(AC-\d{3})", line))
        assert len(census_criteria) == expected, sorted(census_criteria)

        cited = {criterion for _name, criterion, _reason in sections}
        assert cited <= census_criteria, (page_id, cited - census_criteria)

        for binding in _panel_bindings(page_id):
            criterion = binding.criterion.rsplit("/", 1)[-1]
            if not binding.unserved_reasons:
                continue
            matching = [entry for entry in sections if entry[1] == criterion]
            assert matching, (page_id, binding.panel_id, criterion)
            assert any(entry[2] in binding.unserved_reasons for entry in matching), (
                binding.panel_id
            )

    # The sections every page declares are declared for pages the registry knows, and the
    # registry's own panel map agrees with each page's panel list.
    for page_id in ALL_PAGES:
        assert page_id in semantic.PAGE_BY_ID
        assert page_id in WITHHELD_SECTIONS, page_id
        bindings = _panel_bindings(page_id)
        assert [binding.panel_id for binding in bindings] == list(
            semantic.PAGE_BY_ID[page_id].panels
        )
        for binding in bindings:
            assert semantic.PANEL_PAGE[binding.panel_id] == page_id


# --------------------------------------------------------------------------- #
# AC-003: the eight-page set widens the earlier sets without changing them
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_the_eight_page_set_widens_the_earlier_sets_without_changing_them():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-003: eight pages, three page sets.

    GIVEN the read model's served page set, the frozen eight-page registry and the two page
    sets the earlier increments delivered
    WHEN the read model is constructed with each of the three sets and asked for a page inside
    and outside each one
    THEN the first slice still serves three pages, the second increment's set still serves its
    five, the widened set is the whole frozen registry in inventory order and claims nothing,
    and anything outside the registry is refused rather than half-rendered
    """
    slice_model = _model(pages=SLICE_PAGES)
    assert slice_model.pages == SLICE_PAGES
    with pytest.raises(UnsupportedSlicePage):
        slice_model.page("P4_PAPER_PORTFOLIO")
    with pytest.raises(UnsupportedSlicePage):
        slice_model.page("P8_INCIDENTS_INFRASTRUCTURE")

    paper_model = _model(pages=DELIVERED_PAGES)
    assert paper_model.pages == DELIVERED_PAGES
    assert [entry.page_id for entry in paper_model.index()] == list(DELIVERED_PAGES)
    with pytest.raises(UnsupportedSlicePage):
        paper_model.page("P6_COMMITTEE_VALUE_AI_COST")
    with pytest.raises(UnsupportedSlicePage):
        paper_model.panel("P8-PNL-02")

    model = _model()
    assert model.pages == ALL_PAGES == semantic.PAGE_IDS
    assert list(ALL_PAGES) == [
        "P1_SYSTEM_PULSE",
        "P2_DISCOVERY_REGIME",
        "P3_DECISION_TRACE",
        "P4_PAPER_PORTFOLIO",
        "P5_QUALITY_MISSED_OPPORTUNITY",
        "P6_COMMITTEE_VALUE_AI_COST",
        "P7_LEARNING_RELEASES",
        "P8_INCIDENTS_INFRASTRUCTURE",
    ]
    assert set(SLICE_PAGES) <= set(DELIVERED_PAGES) <= set(ALL_PAGES)
    assert [entry.page_id for entry in model.index()] == list(ALL_PAGES)
    assert len(model.index()) == 8

    for page_id in ALL_PAGES:
        reading = model.page(page_id)
        assert reading.state is model.service.page(page_id).state, page_id
        assert reading.claimable is (reading.state in semantic.SERVED_STATES), page_id
        assert reading.claimable is False, page_id
        assert reading.descriptor["operator_question"] == (
            model.service.page(page_id).descriptor["operator_question"]
        ), page_id

    with pytest.raises(UnsupportedSlicePage):
        model.page("P9_UNKNOWN")
    with pytest.raises(UnsupportedSlicePage):
        model.panel("P9-PNL-01")


# --------------------------------------------------------------------------- #
# AC-004: the mounted surface and the mount posture stay unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_the_mounted_surface_and_the_mount_posture_stay_unchanged():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-004: pages widened, surface not.

    GIVEN the analytics-plane application, the read router, the service's declared route set
    and the exposure regression suite that pins the mounted surface
    WHEN the mounted path set, the route methods, the accepted parameters, the service's own
    mounting declaration and the exposure pin are inspected
    THEN the mounted set is exactly what the first slice mounted, this increment adds no route,
    every route is a GET, the service module still mounts nothing, and the mounting stays
    attributed to the increment that authorized it
    """
    declared = tuple(route.split(" ", 1)[1] for route in semantic.DECLARED_ROUTES)
    assert read_router.MOUNTED_PATHS == declared
    assert set(read_router.ACCEPTED_PARAMETERS) == set(semantic.SUPPORTED_FILTERS) | {
        "limit",
        "cursor",
    }
    assert _router_get_count() == len(declared) + 1

    mounted = set(cockpit_service.app.openapi().get("paths", {}) or {})
    assert mounted == set(LEGACY_READ_PATHS) | set(declared) | {read_router.PAGE_ROUTE}

    methods = read_router.route_methods()
    assert set(methods) == set(declared) | {read_router.PAGE_ROUTE}
    assert all(verbs == {"GET"} for verbs in methods.values())

    assert semantic.MOUNTED is False
    assert semantic.MOUNTED_PATHS == ()
    assert MOUNTED_SURFACE["service_module_mounts"] is False
    assert MOUNTED_SURFACE["mounting_module"] == "app.api.cockpit_semantic"
    assert MOUNTED_SURFACE["authorized_by_increment"] == PRECEDING_INCREMENT
    assert list(MOUNTED_SURFACE["mounted_routes"]) == list(semantic.DECLARED_ROUTES)

    reached = _imports(pathlib.Path(read_router.__file__))
    assert "app.opip.cockpit.semantic" in reached
    assert "app.opip.cockpit.semantic_pages" in reached
    for banned in (
        "apscheduler",
        "telegram",
        "sqlite3",
        "app.exchange",
        "app.api.telegram",
        "app.opip.exchange",
        "app.opip.learning",
        "app.opip.committee",
        "app.opip.cockpit.canonical",
    ):
        assert not any(
            name == banned or name.startswith(banned + ".") for name in reached
        ), banned

    exposure = EXPOSURE_TEST.read_text(encoding="utf-8")
    for path in (*declared, read_router.PAGE_ROUTE):
        assert path in exposure, path
    assert "app.api.cockpit_semantic" in exposure

    # Serving the pages adds no reader: the read model still reaches no producer of its own.
    read_model = _imports(APP_ROOT / "app" / "opip" / "cockpit" / "semantic_pages.py")
    assert "app.opip.profit_intelligence.lineage" in read_model
    for banned in ("app.opip.committee", "app.opip.learning", "app.opip.detectors", "app.exchange"):
        assert not any(
            name == banned or name.startswith(banned + ".") for name in read_model
        ), banned


# --------------------------------------------------------------------------- #
# AC-005: the planned criteria, the parity and the threshold policy stay untouched
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_the_planned_criteria_the_parity_and_the_threshold_policy_stay_untouched():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-005: a successor, not a redefinition.

    GIVEN the first page increment's scope contract, the frozen census, the semantic read
    service and this contract
    WHEN the planned criteria, the approved criteria and the page and parity posture are
    compared
    THEN every criterion the census plans for the first page increment stays deferred verbatim
    there, this contract approves no census criterion, and the parity, authority,
    reconciliation and threshold posture is unchanged
    """
    planned = _census_criteria_for(PRECEDING_INCREMENT)
    assert len(planned) == 54, sorted(planned)

    preceding_text = PRECEDING_CONTRACT.read_text(encoding="utf-8")
    preceding_approved = set(re.findall(r"^(AC-\d{3}):$", preceding_text, re.M))
    assert preceding_approved == {f"AC-{n:03d}" for n in range(1, 7)}
    preceding_deferred = set(
        re.findall(
            r"PLANNED:" + re.escape(PRECEDING_INCREMENT) + r"/(AC-\d{3})",
            _section(preceding_text, "EXPLICITLY OUT OF SCOPE"),
        )
    )
    assert preceding_deferred == planned

    own_text = CONTRACT.read_text(encoding="utf-8")
    own_approved = set(re.findall(r"^(AC-\d{3}):$", own_text, re.M))
    assert own_approved == {f"AC-{n:03d}" for n in range(1, 7)}
    assert own_approved.isdisjoint(planned)
    assert _section(own_text, "UNAPPROVED SCOPE CHANGES").strip() == "NONE"
    assert _census_criteria_for(INCREMENT) == set()

    # The earlier page increments keep their own approved sets, and this one shares none of
    # their criterion identities.
    paper_text = PAPER_CONTRACT.read_text(encoding="utf-8")
    paper_approved = set(re.findall(r"^(AC-\d{3}):$", paper_text, re.M))
    assert paper_approved == {f"AC-{n:03d}" for n in range(1, 9)}
    assert {(INCREMENT, ac) for ac in own_approved}.isdisjoint(
        {(PAPER_INCREMENT, ac) for ac in paper_approved}
        | {(PRECEDING_INCREMENT, ac) for ac in preceding_approved}
    )
    assert _section(paper_text, "UNAPPROVED SCOPE CHANGES").strip() == "NONE"

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.AUTHORITY_UNDECIDED == "UNDECIDED"
    assert semantic.COUNTS_AND_PNL_RECONCILIATION == "NOT_RECORDED"
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
    assert semantic.RATIFIED_THRESHOLDS == ()
    assert "NOT_VERIFIED" in (
        APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"
    ).read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# AC-006: the delivery record, the movable pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_006_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-D-trust-learning-incident-pages/AC-006: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer
    and the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records this increment with the base head it stacks on, its own contract
    and its own test count, the pointer resolves to a declared contract, every approved
    criterion is traced to the test that cites it and mapped to a path, and the parity row
    stays OUTSTANDING
    """
    contracts = load_scope_contracts(CONTRACT_DIR)
    declared = {item.increment for item in contracts}
    assert INCREMENT in declared
    assert PRECEDING_INCREMENT in declared
    assert PAPER_INCREMENT in declared
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
    assert global_pointer_pin_violations(source) == []
    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", source))
    assert cited == set(contract.acceptance_criteria)

    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    assert "c9f4c7e0" in report or "19e3b3cef5623018a4d5870a322470067f314cbb" in report
    assert "pull/376" in report
    assert "pull/377" in report
    assert "pull/378" in report
    assert f"{_acceptance_test_count(pathlib.Path(__file__))} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "| R7 governed retirement | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
