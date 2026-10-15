"""F10-B first slice acceptance tests: the page read model, its read router and its asset.

Increment: ``ATDD-R5-F10-dashboard-parity-implementation``.

These tests prove the *implementation* slice only: which panel values the page read model
may publish, where the read router mounts the declared surface, what the served page asset
may hold, and that every criterion the census plans for this increment is either approved
here or carried forward verbatim as deferred. They claim no parity: the eight-page parity
predicate stays ``NOT_VERIFIED`` with zero verified pages, and the panel criteria the census
plans for this increment stay deferred rather than satisfied. Nothing here widens an
exposure or creates an authority; the mounting this increment performs is proved by the
exposure tests it narrows rather than relaxes.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

import pytest

from app.api import cockpit_semantic as read_router
from app.api import cockpit_service
from app.opip.cockpit import semantic
from app.opip.cockpit.ledger import (
    EconomicResult,
    ExecutionResult,
    ExitMechanism,
    LifecycleStatus,
    PaperLedger,
    ReconciledPaperTrade,
)
from app.opip.cockpit.semantic import SemanticReadService
from app.opip.cockpit.semantic_pages import (
    INDEX_COUNTS_BASIS,
    MOUNTED_SURFACE,
    PAGE_READ_MODEL_VERSION,
    SLICE_PAGES,
    PageReadModel,
    PageReadModelError,
    UnsupportedSlicePage,
)
from app.opip.cockpit.trust import unavailable

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

CONTRACT = (
    APP_ROOT
    / "docs"
    / "atdd"
    / "scope-contracts"
    / "ATDD-R5-F10-dashboard-parity-implementation.md"
)
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
PARITY_SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"
PAGE_ASSET = APP_ROOT / "app" / "api" / "cockpit_semantic.html"
ROUTER_SOURCE = APP_ROOT / "app" / "api" / "cockpit_semantic.py"
EXPOSURE_TEST = APP_ROOT / "tests" / "test_bc4_cockpit_exposure_v1.py"
READ_SERVICE_TEST = APP_ROOT / "tests" / "test_opip_r5_f10_semantic_read_service.py"

INCREMENT = "ATDD-R5-F10-dashboard-parity-implementation"

#: The scope contract's frozen section vocabulary. The contract must stay parseable in it,
#: in this order, so a reader and the scope checker see the same document.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "INCREMENT",
    "OWNER-APPROVED INTENT",
    "ARCHITECTURE REFERENCES",
    "APPROVED ACCEPTANCE CRITERIA",
    "EXPLICITLY OUT OF SCOPE",
    "FROZEN BOUNDARIES",
    "ACCEPTANCE TEST TRACEABILITY",
    "IMPLEMENTATION MAP",
    "DEFERRED DISCOVERIES",
    "UNAPPROVED SCOPE CHANGES",
)

#: The read-only paths this plane mounted before the slice. The slice adds exactly its own
#: declared surface on top of these and nothing else.
LEGACY_READ_PATHS: tuple[str, ...] = (
    "/cockpit",
    "/api/cockpit/overview",
    "/api/cockpit/trades",
    "/api/cockpit/trades/{paper_trade_id}",
)

#: What the served asset must never hold: browser persistence, an order path, or a second
#: read surface module.
FORBIDDEN_ASSET_TOKENS: tuple[str, ...] = (
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

#: Module families the read router must never reach.
FORBIDDEN_ROUTER_MODULES: tuple[str, ...] = (
    "apscheduler",
    "telegram",
    "sqlite3",
    "app.exchange",
    "app.api.telegram",
    "app.opip.exchange",
    "app.opip.learning",
    "app.opip.cockpit.canonical",
)

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)


def _row(paper_trade_id: str, net_pnl: float = 10.0) -> ReconciledPaperTrade:
    """One settled reconciled row carrying only the fields this slice can read."""
    return ReconciledPaperTrade(
        paper_trade_id=paper_trade_id,
        native_symbol="BTC/USD",
        quote_currency="USD",
        policy_version="POLICY:1",
        lifecycle_status=LifecycleStatus.CLOSED,
        last_exit_fill_at=NOW,
        entry_quantity=1.0,
        exited_quantity=1.0,
        remaining_quantity=0.0,
        gross_pnl=net_pnl + 1.0,
        execution_costs=1.0,
        net_pnl=net_pnl,
        reserved_capital=0.0,
        net_pnl_definitive=True,
        economic_result=EconomicResult.WIN,
        execution_result=ExecutionResult.FULL_FILL,
        exit_mechanism=ExitMechanism.TARGET,
        terminal_reconciliation_state="FINAL_VERIFIED",
        event_ids=(f"EV:{paper_trade_id}",),
    )


def _model(*, unreadable: bool = False) -> PageReadModel:
    """The slice read model over a synthetic reconciled ledger."""
    entries = (_row("PT:1"), _row("PT:2", -4.0))
    ledger = (
        PaperLedger(entries=entries, trust=unavailable("CANONICAL_READ_RETRYABLE"))
        if unreadable
        else PaperLedger(entries=entries)
    )
    return PageReadModel(
        SemanticReadService(ledger, as_of=NOW, starting_equity=1000.0, identity="TEST")
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
    tree = ast.parse(ROUTER_SOURCE.read_text(encoding="utf-8"))
    return sum(
        1
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for decorator in node.decorator_list
        if isinstance(decorator, ast.Call)
        and isinstance(decorator.func, ast.Attribute)
        and decorator.func.attr == "get"
    )


def module_source(path: pathlib.Path) -> str:
    """One file's own source text, read as UTF-8."""
    return path.read_text(encoding="utf-8")


def _acceptance_test_count(path: pathlib.Path) -> int:
    """How many acceptance-marked test functions one module declares."""
    return len(re.findall(r"@pytest\.mark\.acceptance", module_source(path)))


def _section(text: str, name: str) -> str:
    """One scope-contract section body, by its frozen heading."""
    padded = "\n" + text
    start = padded.index(f"\n{name}:\n") + len(name) + 3
    rest = padded[start:]
    ends = [
        rest.index(f"\n{other}:\n")
        for other in REQUIRED_SECTIONS
        if f"\n{other}:\n" in rest
    ]
    return rest[: min(ends)] if ends else rest


# --------------------------------------------------------------------------- #
# AC-001: one honest page read model over the semantic read service
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_the_read_model_publishes_a_value_only_where_the_service_allows_one():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-001: values, never verdicts.

    GIVEN the semantic read service over a synthetic reconciled ledger and the page read
    model built over it
    WHEN every panel of every page of the slice is read, with and without values
    THEN a panel the service does not declare served is withheld with the service's own
    reason and evidence gap and with no value, a served panel carries a value only when its
    readings are all available, and no panel or index entry substitutes a zero, an empty
    collection or an omitted key for evidence this plane cannot produce
    """
    model = _model()
    assert PAGE_READ_MODEL_VERSION == "cockpit-semantic-pages-v1"
    with pytest.raises(PageReadModelError):
        PageReadModel(object())

    for page_id in SLICE_PAGES:
        reading = model.page(page_id)
        declared = list(model.service.page(page_id).descriptor["panels"])
        assert [panel.panel_id for panel in reading.panels] == declared
        for panel in reading.panels:
            binding = semantic.PANEL_BY_ID[panel.panel_id]
            assert panel.page_id == page_id
            if not binding.carries_value:
                assert panel.serving is False
                assert panel.value is None and panel.carries_value is False
                assert tuple(panel.reasons) == tuple(binding.unserved_reasons)
                assert panel.reasons, panel.panel_id
                assert panel.gap_id == binding.gap_id
                assert panel.facts == ()
            else:
                assert panel.serving is True
                assert tuple(panel.facts) == tuple(binding.served)
                assert panel.carries_value is (panel.value is not None)
            if panel.value is None:
                assert panel.state not in semantic.SERVED_STATES, panel.panel_id
                assert panel.reasons, panel.panel_id
                assert panel.to_dict().get("value") is None
                serialized = json.dumps(panel.to_dict(), default=str)
                for stand_in in (
                    '"value": 0,',
                    '"value": 0.0,',
                    '"value": [],',
                    '"value": {},',
                    '"value": ""',
                ):
                    assert stand_in not in serialized, panel.panel_id

    index = model.index()
    assert [entry.page_id for entry in index] == list(SLICE_PAGES)
    for entry in index:
        reading = model.page(entry.page_id, with_values=False)
        assert entry.state is reading.state
        assert entry.panel_count == len(reading.panels)
        assert entry.panel_count == len(model.service.page(entry.page_id).descriptor["panels"])
        assert entry.served_panels + entry.withheld_panels == entry.panel_count
        assert entry.served_panels == sum(1 for panel in reading.panels if panel.serving)
        assert entry.withheld_panels == sum(1 for panel in reading.panels if not panel.serving)
        assert entry.counts_basis == INDEX_COUNTS_BASIS


@pytest.mark.acceptance
def test_ac_001_a_slice_page_keeps_the_services_own_state_and_is_refused_outside_the_slice():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-001: the service's verdict, never ours.

    GIVEN the slice read model and the semantic read service's own page readings
    WHEN a slice page, an out-of-slice page, an out-of-slice panel and an unreadable
    projection are read
    THEN the page state, reasons and claimability are the service's own, a page or panel
    outside the slice is refused rather than half-rendered, and an unreadable projection still
    renders every panel without a value instead of dropping the page
    """
    model = _model()
    for page_id in SLICE_PAGES:
        reading = model.page(page_id)
        service_reading = model.service.page(page_id)
        assert reading.state is service_reading.state
        assert reading.claimable is (reading.state in semantic.SERVED_STATES)
        assert reading.claimable is False, page_id
        assert tuple(reading.reasons) == tuple(service_reading.reasons)
        assert reading.descriptor["follower_increment"] == INCREMENT
        assert reading.descriptor["follower_increment"] == semantic.FOLLOWER_INCREMENT
        assert reading.withheld_sections, page_id

    with pytest.raises(UnsupportedSlicePage):
        model.page("P4_PAPER_PORTFOLIO")
    with pytest.raises(UnsupportedSlicePage):
        model.panel("P8-PNL-01")

    unreadable = _model(unreadable=True)
    for page_id in SLICE_PAGES:
        reading = unreadable.page(page_id)
        assert reading.claimable is False, page_id
        assert reading.panels, page_id
        for panel in reading.panels:
            assert panel.carries_value is (panel.value is not None)


# --------------------------------------------------------------------------- #
# AC-002: the mounted read surface is declared, GET-only and fail-closed
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_the_read_router_mounts_only_the_declared_get_surface():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-002: declared, then mounted.

    GIVEN the read router, the semantic read service's declared route set and the service
    module's own mounting posture
    WHEN the router's mounted paths, route methods and imports are inspected
    THEN it mounts exactly the declared read routes plus its page asset, every route is a
    GET, the service module still mounts nothing of its own, and the router reaches no
    exchange, order, Telegram, scheduler, learning or writer module
    """
    declared = tuple(route.split(" ", 1)[1] for route in semantic.DECLARED_ROUTES)
    assert read_router.MOUNTED_PATHS == declared
    assert set(read_router.ACCEPTED_PARAMETERS) == set(semantic.SUPPORTED_FILTERS) | {
        "limit",
        "cursor",
    }
    methods = read_router.route_methods()
    assert set(methods) == set(declared) | {read_router.PAGE_ROUTE}
    assert all(verbs == {"GET"} for verbs in methods.values())
    assert _router_get_count() == len(declared) + 1
    assert semantic.MOUNTED is False
    assert semantic.MOUNTED_PATHS == ()

    reached = _imports(ROUTER_SOURCE)
    assert "app.api.cockpit" in reached
    assert "app.opip.cockpit.semantic" in reached
    assert "app.opip.cockpit.semantic_pages" in reached
    for banned in FORBIDDEN_ROUTER_MODULES:
        assert not any(
            name == banned or name.startswith(banned + ".") for name in reached
        ), banned


@pytest.mark.acceptance
def test_ac_002_unsupported_filters_and_subjects_fail_closed():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-002: refused, never dropped.

    GIVEN the read router's typed filter contract and the semantic read service's refusals
    WHEN an unsupported query parameter, an unknown subject and a malformed filter value are
    presented
    THEN the unsupported parameter is refused instead of silently ignored, and every refusal
    is answered as a client error rather than as an empty or favorable reading
    """
    with pytest.raises(read_router.UnsupportedQueryFilter):
        read_router._filters({"limit": 5, "bogus": "x"})
    assert read_router._filters({"limit": 5}) == {"limit": 5}

    assert read_router._refuse(semantic.UnsupportedPage("P9_X")).status_code == 404
    assert read_router._refuse(semantic.UnsupportedFact("FACT:X")).status_code == 404
    assert read_router._refuse(semantic.InvalidFilterValue("bogus")).status_code == 400
    assert read_router._refuse(UnsupportedSlicePage("P9_X")).status_code == 404


# --------------------------------------------------------------------------- #
# AC-003: the served page asset is static, secret-free and authority-free
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_the_page_asset_is_static_secret_free_and_authority_free():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-003: a page, not a control surface.

    GIVEN the page asset the read router serves and the reviewed file on disk
    WHEN the asset text, its route and the bytes served are inspected
    THEN it is served as one static GET of exactly the reviewed file, it persists nothing in
    the browser, it names only this plane's own read prefix, and it holds no order, exchange
    or mounting control
    """
    asset = PAGE_ASSET.read_text(encoding="utf-8")
    assert asset.strip()
    for token in FORBIDDEN_ASSET_TOKENS:
        assert token not in asset, token
    assert not re.search(r"method\s*:\s*['\"](post|put|patch|delete)['\"]", asset, re.I)
    assert "/api/cockpit/semantic" in asset

    served = read_router.semantic_page_asset()
    assert pathlib.Path(served.path) == read_router.SEMANTIC_PAGE_FILE
    assert read_router.SEMANTIC_PAGE_FILE == PAGE_ASSET
    assert pathlib.Path(served.path).read_text(encoding="utf-8") == asset
    assert read_router.route_methods()[read_router.PAGE_ROUTE] == {"GET"}


# --------------------------------------------------------------------------- #
# AC-004: the census, this contract and this module agree on what is in scope
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_every_census_criterion_of_this_increment_is_approved_or_deferred():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-004: no silent drop, no overclaim.

    GIVEN the frozen source census, this increment's scope contract, the parity
    specification and this module
    WHEN the criteria the census plans for this increment are compared with the criteria the
    contract approves, defers and traces
    THEN every planned criterion is either approved here or carried forward verbatim as
    deferred, no approved criterion claims a planned one, every approved criterion is traced
    to its own acceptance test and implementation entry, and parity stays NOT_VERIFIED with
    zero verified pages
    """
    census_text = CENSUS.read_text(encoding="utf-8")
    planned = set(
        re.findall(r"PLANNED:" + re.escape(INCREMENT) + r"/(AC-\d{3})", census_text)
    )
    assert planned, "the census plans no criterion for this increment"

    contract_text = CONTRACT.read_text(encoding="utf-8")
    for name in REQUIRED_SECTIONS:
        assert f"\n{name}:\n" in "\n" + contract_text, name
    assert _section(contract_text, "INCREMENT").strip() == INCREMENT
    assert _section(contract_text, "UNAPPROVED SCOPE CHANGES").strip() == "NONE"

    approved = set(re.findall(r"^(AC-\d{3}):$", contract_text, re.M))
    assert approved, "the contract approves no criterion"
    assert approved.isdisjoint(planned)

    deferred = set(
        re.findall(
            r"PLANNED:" + re.escape(INCREMENT) + r"/(AC-\d{3})",
            _section(contract_text, "EXPLICITLY OUT OF SCOPE"),
        )
    )
    assert deferred == planned
    assert deferred.isdisjoint(approved)

    trace = _section(contract_text, "ACCEPTANCE TEST TRACEABILITY")
    implementation = _section(contract_text, "IMPLEMENTATION MAP")
    for ac_id in sorted(approved):
        targets = re.findall(rf"^{ac_id} -> (\S+)$", trace, re.M)
        assert targets, ac_id
        assert all(
            target.startswith("tests/") and "::test_" in target for target in targets
        ), ac_id
        paths = re.findall(rf"^{ac_id} -> (\S+)$", implementation, re.M)
        assert paths, ac_id
        assert all(path.startswith("OHM-Trade-Agent-v1/") for path in paths), ac_id

    cited = set(
        re.findall(
            re.escape(INCREMENT) + r"/(AC-\d{3})",
            pathlib.Path(__file__).read_text(encoding="utf-8"),
        )
    )
    assert cited == approved

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.FOLLOWER_INCREMENT == INCREMENT
    assert "NOT_VERIFIED" in PARITY_SPEC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# AC-005: the exposure widening is pinned and the mount posture is preserved
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_the_mounted_path_set_and_the_mount_posture_stay_pinned():
    """ATDD-R5-F10-dashboard-parity-implementation/AC-005: pinned, not merely widened.

    GIVEN the analytics-plane app, the read router's declared mounting posture and the
    exposure tests that own the pinned path set
    WHEN the mounted paths, the declared posture and the owning pins are inspected
    THEN the mounted set is exactly the legacy read paths plus this slice's declared surface
    and its page asset, the service module still declares itself unmounted with no verified
    page, and the owning exposure tests pin the widened set and the added router
    """
    mounted = set(cockpit_service.app.openapi().get("paths", {}) or {})
    expected = set(LEGACY_READ_PATHS) | set(read_router.MOUNTED_PATHS) | {read_router.PAGE_ROUTE}
    assert mounted == expected

    assert semantic.MOUNTED is False
    assert semantic.MOUNTED_PATHS == ()
    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
    assert MOUNTED_SURFACE["service_module_mounts"] is False
    assert MOUNTED_SURFACE["mounting_module"] == "app.api.cockpit_semantic"
    assert MOUNTED_SURFACE["authorized_by_increment"] == INCREMENT
    assert list(MOUNTED_SURFACE["mounted_routes"]) == list(semantic.DECLARED_ROUTES)

    exposure = EXPOSURE_TEST.read_text(encoding="utf-8")
    for path in (*read_router.MOUNTED_PATHS, read_router.PAGE_ROUTE):
        assert path in exposure, path
    assert "app.api.cockpit_semantic" in exposure

    service_test = READ_SERVICE_TEST.read_text(encoding="utf-8")
    assert 'mounting_module = "cockpit_semantic.py"' in service_test
    assert "MOUNTED_PATHS == ()" in service_test


# --------------------------------------------------------------------------- #
# AC-006: the delivery record, the movable pointer and this contract agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_006_the_report_the_pointer_and_the_criteria_agree() -> None:
    """ATDD-R5-F10-dashboard-parity-implementation/AC-006: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer
    and the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records the two increments this slice stacks on by their own commits with
    the Linux CI result of the head it stacks on, records this increment as delivered with this
    module's own acceptance-test count and the Linux CI result of its own head, leaves the
    eight-page parity row OUTSTANDING with the parity predicate NOT_VERIFIED and R7 unstarted,
    the pointer resolves to a declared contract, and every approved criterion is traced to a
    matching test and mapped to a path
    """
    contracts = load_scope_contracts(CONTRACT.parent)
    declared = {item.increment for item in contracts}
    assert INCREMENT in declared
    assert read_increment_pointer(POINTER) in declared
    assert CONTRACT.name == f"{INCREMENT}.md"

    ours = [item for item in contracts if item.increment == INCREMENT]
    assert len(ours) == 1
    contract = ours[0]
    assert contract.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    for ac_id in contract.acceptance_criteria:
        assert ac_id in contract.traceability, ac_id
        assert ac_id in contract.implementation_map, ac_id

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons
    assert global_pointer_pin_violations(module_source(pathlib.Path(__file__))) == []

    assert BUILD_REPORT.exists()
    report = module_source(BUILD_REPORT)
    assert INCREMENT in report
    assert "16a61d476511ecc9de7ff73a14a55f6b5d0e27b0" in report
    assert "pull/376" in report
    assert "pull/377" in report
    assert "pull/378" in report
    assert "8203 passed, 9 skipped" in report
    assert "8211 passed, 9 skipped" in report
    assert "c565194f9746d270a3589b462c9ca5265aa61fee" in report
    assert f"{_acceptance_test_count(pathlib.Path(__file__))} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "| R7 governed retirement | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"




