"""F10-C acceptance tests: the Paper Portfolio and Quality pages over the same read model.

Increment: ``ATDD-R5-F10-C-paper-portfolio-quality``.

These tests prove the *implementation* slice only: that the one page read model serves the
two frozen pages whose facts the analytics plane can reach, that it reads a registered
metric through the metric read the service declares for it and every other declared fact
through the population read, that a currency-scoped fact without its currency is a refusal
with the producer's own reason rather than a wrong number or a failed page, and that the
first slice's behaviour, the mounted surface and the parity posture are unchanged.

They claim no parity and they settle no projection authority: the eight-page parity
predicate stays ``NOT_VERIFIED`` with zero verified pages, per-page projection authority
stays ``UNDECIDED``, the counts-and-P&L reconciliation stays ``NOT_RECORDED`` and the
threshold policy stays an owner decision. Serving a page is not proving it.
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
    DELIVERED_PAGES,
    MOUNTED_SURFACE,
    SLICE_PAGES,
    WITHHELD_SECTIONS,
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

INCREMENT = "ATDD-R5-F10-C-paper-portfolio-quality"
PRECEDING_INCREMENT = "ATDD-R5-F10-dashboard-parity-implementation"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
PRECEDING_CONTRACT = CONTRACT_DIR / f"{PRECEDING_INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
PARITY_SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"
READ_MODEL_SOURCE = APP_ROOT / "app" / "opip" / "cockpit" / "semantic_pages.py"
EXPOSURE_TEST = APP_ROOT / "tests" / "test_bc4_cockpit_exposure_v1.py"

LEGACY_READ_PATHS: tuple[str, ...] = (
    "/cockpit",
    "/api/cockpit/overview",
    "/api/cockpit/trades",
    "/api/cockpit/trades/{paper_trade_id}",
)

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.acceptance


def _row(
    paper_trade_id: str,
    *,
    status: LifecycleStatus = LifecycleStatus.CLOSED,
    remaining: float = 0.0,
    net_pnl: float = 10.0,
) -> ReconciledPaperTrade:
    """One reconciled row carrying only the fields these two pages read."""
    return ReconciledPaperTrade(
        paper_trade_id=paper_trade_id,
        native_symbol="BTC/USD",
        quote_currency="USD",
        policy_version="POLICY:1",
        lifecycle_status=status,
        last_exit_fill_at=NOW,
        entry_quantity=1.0,
        exited_quantity=1.0 - remaining,
        remaining_quantity=remaining,
        gross_pnl=net_pnl + 1.0,
        execution_costs=1.0,
        net_pnl=net_pnl,
        reserved_capital=5.0 if status is LifecycleStatus.OPEN else 0.0,
        net_pnl_definitive=status is LifecycleStatus.CLOSED,
        economic_result=EconomicResult.WIN,
        execution_result=ExecutionResult.FULL_FILL,
        exit_mechanism=ExitMechanism.TARGET,
        terminal_reconciliation_state="FINAL_VERIFIED",
        event_ids=(f"EV:{paper_trade_id}",),
    )


def _model(
    *,
    pages: tuple[str, ...] = DELIVERED_PAGES,
    unreadable: bool = False,
) -> PageReadModel:
    """The read model over a synthetic reconciled ledger, with a settled and an open row."""
    entries = (
        _row("PT:1"),
        _row("PT:2", net_pnl=-4.0),
        _row("PT:3", status=LifecycleStatus.OPEN, remaining=0.5),
    )
    ledger = (
        PaperLedger(entries=entries, trust=unavailable("CANONICAL_READ_RETRYABLE"))
        if unreadable
        else PaperLedger(entries=entries)
    )
    return PageReadModel(
        SemanticReadService(ledger, as_of=NOW, starting_equity=1000.0, identity="TEST"),
        pages=pages,
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
    tree = ast.parse(read_router.__file__ and pathlib.Path(read_router.__file__).read_text(encoding="utf-8"))
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


def _panel_bindings(page_id: str):
    """The frozen registry's panel bindings for one page, in registry order."""
    return tuple(
        semantic.PANEL_BY_ID[panel_id]
        for panel_id in semantic.PAGE_BY_ID[page_id].panels
    )


# --------------------------------------------------------------------------- #
# AC-001: the Paper Portfolio page publishes only what the service allows
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_the_paper_portfolio_page_publishes_only_what_the_service_allows():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-001: served, qualified or refused.

    GIVEN the page read model over the semantic read service, the Paper Portfolio page of
    the frozen registry and a reconciled paper ledger
    WHEN the page is read with and without the quote currency its currency-scoped panels
    declare, and its panels are read directly
    THEN the five declared panels are published in the registry's own order, a served panel
    carries a value assembled only from its declared served facts, a currency-scoped panel
    publishes no value while that requirement is unmet and reports the registered reason
    instead, and nothing is substituted for the missing evidence
    """
    model = _model()
    descriptor = model.service.page("P4_PAPER_PORTFOLIO").descriptor
    assert list(descriptor["panels"]) == [
        binding.panel_id for binding in _panel_bindings("P4_PAPER_PORTFOLIO")
    ]

    page = model.page("P4_PAPER_PORTFOLIO", quote_currency="USD")
    assert [panel.panel_id for panel in page.panels] == list(descriptor["panels"])
    assert page.state is model.service.page("P4_PAPER_PORTFOLIO").state
    assert page.claimable is False
    assert page.reasons == ("PAGE_REQUIRED_FACTS_NOT_PRODUCIBLE",)

    by_id = {panel.panel_id: panel for panel in page.panels}
    assert set(by_id) == {"P4-PNL-01", "P4-PNL-02", "P4-PNL-03", "P4-PNL-04", "P4-PNL-05"}

    counts = by_id["P4-PNL-03"]
    assert counts.state is semantic.FactState.SERVED
    assert counts.carries_value
    assert set(counts.value) == set(semantic.PANEL_BY_ID["P4-PNL-03"].served)
    assert counts.value["paper.population.rows"]["value"] == 3

    earned = by_id["P4-PNL-02"]
    assert earned.carries_value
    assert set(earned.value) == {"paper.realized_net_pnl", "paper.execution_cost"}
    assert earned.value["paper.realized_net_pnl"]["value"] == 6.0

    drawdown = by_id["P4-PNL-04"]
    assert drawdown.state is semantic.FactState.SERVED_PARTIAL
    assert drawdown.value["paper.realized_equity_drawdown_pct"]["state"] == "SERVED_PARTIAL"
    assert semantic.REASON_NO_MARK_EVIDENCE in drawdown.reasons
    assert drawdown.gap_id == "UNREALIZED_MARK_TO_MARKET_PNL"

    currencyless = model.page("P4_PAPER_PORTFOLIO")
    withheld = {panel.panel_id: panel for panel in currencyless.panels}
    for panel_id in ("P4-PNL-01", "P4-PNL-02", "P4-PNL-04"):
        panel = withheld[panel_id]
        assert panel.state is semantic.FactState.UNAVAILABLE, panel_id
        assert panel.carries_value is False, panel_id
        assert panel.value is None, panel_id
        assert semantic.REASON_QUOTE_CURRENCY_REQUIRED in panel.reasons, panel_id
    assert withheld["P4-PNL-03"].carries_value
    assert withheld["P4-PNL-05"].carries_value

    for panel in currencyless.panels:
        if panel.carries_value:
            continue
        # A refusal is never a zero, an empty collection or an omitted key.
        payload = panel.to_dict()
        assert "value" in payload
        assert payload["value"] is None, panel.panel_id


# --------------------------------------------------------------------------- #
# AC-002: the Quality page withholds its blocked panels with the producer's reason
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_the_quality_page_withholds_its_blocked_panels_with_the_producers_reason():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-002: withheld, never empty.

    GIVEN the Quality and Missed Opportunity page, whose four census-declared upstream
    panels this plane cannot produce, and its one partly served panel
    WHEN that page is read with the delivered page set and with the currency filter its
    served panel declares
    THEN the four blocked panels keep the service's own state, reason and registered
    evidence gap with no value at all, and the partly served panel publishes only its
    declared served facts and keeps its own declared qualification reasons
    """
    model = _model()
    page = model.page("P5_QUALITY_MISSED_OPPORTUNITY", quote_currency="USD")
    assert [panel.panel_id for panel in page.panels] == list(
        model.service.page("P5_QUALITY_MISSED_OPPORTUNITY").descriptor["panels"]
    )
    assert page.state is semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE
    assert page.claimable is False

    by_id = {panel.panel_id: panel for panel in page.panels}
    blocked = ("P5-PNL-01", "P5-PNL-02", "P5-PNL-03", "P5-PNL-05")
    for panel_id in blocked:
        binding = semantic.PANEL_BY_ID[panel_id]
        panel = by_id[panel_id]
        assert panel.state is binding.state, panel_id
        assert panel.state is semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE, panel_id
        assert panel.carries_value is False, panel_id
        assert panel.value is None, panel_id
        assert panel.reasons == tuple(binding.unserved_reasons), panel_id
        assert panel.gap_id == binding.gap_id, panel_id
        assert panel.to_dict()["evidence_gap"] == binding.gap_id, panel_id
    assert by_id["P5-PNL-01"].gap_id == "QUALIFICATION_FUNNEL_PROGRESSION"
    assert by_id["P5-PNL-02"].gap_id == "MISSED_PROFITABLE_OPPORTUNITY"
    assert by_id["P5-PNL-03"].gap_id == "FORWARD_OUTCOME_HORIZON_EVIDENCE"

    served = by_id["P5-PNL-04"]
    binding = semantic.PANEL_BY_ID["P5-PNL-04"]
    assert served.state is semantic.FactState.SERVED_PARTIAL
    assert served.carries_value
    assert set(served.value) == set(binding.served) == {
        "paper.realized_net_pnl",
        "paper.unresolved_count",
    }
    for reason in binding.unserved_reasons:
        assert reason in served.reasons, reason
    assert served.gap_id == "FORWARD_OUTCOME_HORIZON_EVIDENCE"

    # Without its currency the served panel refuses too, rather than counting a censored
    # cohort as a settled one.
    currencyless = {panel.panel_id: panel for panel in model.page("P5_QUALITY_MISSED_OPPORTUNITY").panels}
    assert currencyless["P5-PNL-04"].carries_value is False
    assert currencyless["P5-PNL-04"].value is None
    assert currencyless["P5-PNL-04"].state is semantic.FactState.UNAVAILABLE


# --------------------------------------------------------------------------- #
# AC-003: a metric fact is read through the metric read, and no formula is restated
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_a_metric_fact_is_read_through_the_metric_read_and_no_formula_is_restated():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-003: one authority, two declared doors.

    GIVEN the service's registered metric bindings, its declared fact names and the page
    read model that assembles panel values
    WHEN a metric-backed panel and a population-backed panel are read, and the read model's
    source is inspected
    THEN the metric is read through the service's metric read and the population through its
    population read, a fact the declared entry point refuses is never answered by the other,
    and the read model restates no formula, population, unit or exclusion and holds no
    second registry
    """
    model = _model()
    metric_fact = "paper.realized_net_pnl"
    population_fact = "paper.population.rows"
    assert metric_fact in semantic.METRIC_BINDINGS
    assert population_fact not in semantic.METRIC_BINDINGS

    # The population read refuses the metric's identifier, so a value for that panel could
    # only have come through the metric read.
    with pytest.raises(semantic.UnsupportedFact):
        model.service.population(metric_fact, quote_currency="USD")

    metric_reading = model._read_fact(metric_fact, {"quote_currency": "USD"})
    assert metric_reading.kind == "METRIC"
    assert metric_reading.subject_id == metric_fact
    assert metric_reading.value == 6.0
    assert metric_reading.definition_reference["registry"] == "paper-metrics-v1"
    assert metric_reading.definition_reference["metric_id"] == metric_fact

    population_reading = model._read_fact(population_fact, {})
    assert population_reading.kind == "FACT"
    assert population_reading.subject_id == population_fact
    assert population_reading.definition_reference is None

    served = model.panel("P4-PNL-02", quote_currency="USD")
    assert served.carries_value
    assert served.value[metric_fact]["value"] == metric_reading.value
    assert served.value[metric_fact]["unit"] == metric_reading.unit

    source = READ_MODEL_SOURCE.read_text(encoding="utf-8")
    assert "METRIC_BINDINGS" in source
    declared_facts = (
        set(semantic.SERVED_FACT_NAMES)
        | set(semantic.METRIC_BINDINGS)
        | set(semantic.POPULATION_FACTS)
        | set(semantic.READ_MODEL_FACTS)
        | set(semantic.ROW_FACT_FIELDS)
    )
    literals = set(re.findall(r'"(paper\.[a-z_.]+)"', source))
    assert literals <= declared_facts, literals - declared_facts
    assert "paper_metrics" not in source
    assert not any(
        name.startswith("app.opip.contracts") for name in _imports(READ_MODEL_SOURCE)
    )


# --------------------------------------------------------------------------- #
# AC-004: the delivered page set extends the slice without changing it
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_the_delivered_page_set_extends_the_slice_without_changing_it():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-004: widened, not narrowed.

    GIVEN the read model's delivered page set, the first slice's own page set and the frozen
    eight-page registry
    WHEN the read model is constructed both ways and asked for pages inside and outside each
    set, and for a panel of a page outside the delivered set
    THEN the default construction keeps the slice's three pages and still refuses P4, the
    delivered construction serves the five pages whose facts this plane can reach in registry
    order, an undelivered page is refused rather than half-rendered, and every served page
    keeps the service's own state
    """
    slice_model = _model(pages=SLICE_PAGES)
    assert slice_model.pages == SLICE_PAGES
    assert [entry.page_id for entry in slice_model.index()] == list(SLICE_PAGES)
    with pytest.raises(UnsupportedSlicePage):
        slice_model.page("P4_PAPER_PORTFOLIO")
    with pytest.raises(UnsupportedSlicePage):
        slice_model.panel("P4-PNL-01")

    delivered = _model()
    assert delivered.pages == DELIVERED_PAGES
    assert set(SLICE_PAGES) <= set(DELIVERED_PAGES)
    assert DELIVERED_PAGES == semantic.PAGE_IDS[:5]
    assert [entry.page_id for entry in delivered.index()] == list(DELIVERED_PAGES)
    assert list(DELIVERED_PAGES) == [
        "P1_SYSTEM_PULSE",
        "P2_DISCOVERY_REGIME",
        "P3_DECISION_TRACE",
        "P4_PAPER_PORTFOLIO",
        "P5_QUALITY_MISSED_OPPORTUNITY",
    ]

    for page_id in DELIVERED_PAGES:
        reading = delivered.page(page_id)
        service_reading = delivered.service.page(page_id)
        assert reading.state is service_reading.state, page_id
        assert reading.claimable is (reading.state in semantic.SERVED_STATES), page_id
        assert reading.claimable is False, page_id
        assert tuple(reading.reasons) == tuple(service_reading.reasons), page_id

    for undelivered in ("P6_COMMITTEE_VALUE_AI_COST", "P7_LEARNING_RELEASES", "P8_INCIDENTS_INFRASTRUCTURE"):
        assert undelivered in semantic.PAGE_BY_ID
        with pytest.raises(UnsupportedSlicePage):
            delivered.page(undelivered)
    with pytest.raises(UnsupportedSlicePage):
        delivered.panel("P8-PNL-01")
    with pytest.raises(UnsupportedSlicePage):
        delivered.page("P9_UNKNOWN")

    with pytest.raises(PageReadModelError):
        PageReadModel(delivered.service, pages=("P9_UNKNOWN",))
    with pytest.raises(PageReadModelError):
        PageReadModel(delivered.service, pages=("P4_PAPER_PORTFOLIO", "P4_PAPER_PORTFOLIO"))
    with pytest.raises(PageReadModelError):
        PageReadModel(object())  # type: ignore[arg-type]

    unreadable = _model(unreadable=True)
    for page_id in DELIVERED_PAGES:
        reading = unreadable.page(page_id)
        assert reading.claimable is False, page_id
        assert reading.panels, page_id
        for panel in reading.panels:
            assert panel.carries_value is (panel.value is not None), panel.panel_id


# --------------------------------------------------------------------------- #
# AC-005: the mounted surface and the mount posture stay unchanged
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_the_mounted_surface_and_the_mount_posture_stay_unchanged():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-005: widened pages, not a widened surface.

    GIVEN the analytics-plane application, the read router, the service's declared route set
    and the exposure regression suite that pins the mounted surface
    WHEN the mounted path set, the route methods, the service's own mounting declaration and
    the exposure pin are inspected
    THEN the mounted set is exactly what the preceding increment mounted, this increment adds
    no route, every route is still a GET, the service module still mounts nothing, and the
    mounting stays attributed to the increment that authorized it
    """
    declared = tuple(route.split(" ", 1)[1] for route in semantic.DECLARED_ROUTES)
    assert read_router.MOUNTED_PATHS == declared
    assert set(read_router.ACCEPTED_PARAMETERS) == set(semantic.SUPPORTED_FILTERS) | {
        "limit",
        "cursor",
    }

    mounted = set(cockpit_service.app.openapi().get("paths", {}) or {})
    assert mounted == set(LEGACY_READ_PATHS) | set(declared) | {read_router.PAGE_ROUTE}

    methods = read_router.route_methods()
    assert set(methods) == set(declared) | {read_router.PAGE_ROUTE}
    assert all(verbs == {"GET"} for verbs in methods.values())
    assert _router_get_count() == len(declared) + 1

    assert semantic.MOUNTED is False
    assert semantic.MOUNTED_PATHS == ()
    assert MOUNTED_SURFACE["service_module_mounts"] is False
    assert MOUNTED_SURFACE["mounting_module"] == "app.api.cockpit_semantic"
    assert MOUNTED_SURFACE["authorized_by_increment"] == PRECEDING_INCREMENT
    assert list(MOUNTED_SURFACE["mounted_routes"]) == list(semantic.DECLARED_ROUTES)

    reached = _imports(pathlib.Path(read_router.__file__))
    assert "app.opip.cockpit.semantic" in reached
    assert "app.opip.cockpit.semantic_pages" in reached
    assert "app.api.cockpit" in reached
    for banned in (
        "apscheduler",
        "telegram",
        "sqlite3",
        "app.exchange",
        "app.api.telegram",
        "app.opip.exchange",
        "app.opip.learning",
        "app.opip.cockpit.canonical",
    ):
        assert not any(
            name == banned or name.startswith(banned + ".") for name in reached
        ), banned

    exposure = EXPOSURE_TEST.read_text(encoding="utf-8")
    for path in (*declared, read_router.PAGE_ROUTE):
        assert path in exposure, path
    assert "app.api.cockpit_semantic" in exposure


# --------------------------------------------------------------------------- #
# AC-006: the planned criteria, the parity and the threshold policy stay untouched
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_006_the_planned_criteria_the_parity_and_the_threshold_policy_stay_untouched():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-006: a successor, not a redefinition.

    GIVEN the preceding increment's scope contract, the frozen census, the read model and
    this contract
    WHEN the planned criteria, the approved criteria and the page and parity posture are
    compared
    THEN every criterion the census plans for the preceding increment stays deferred verbatim
    there, neither contract approves a census criterion, and the parity, authority,
    reconciliation and threshold posture is unchanged
    """
    planned = _census_criteria_for(PRECEDING_INCREMENT)
    assert len(planned) == 54, sorted(planned)

    preceding_text = PRECEDING_CONTRACT.read_text(encoding="utf-8")
    preceding_approved = set(re.findall(r"^(AC-\d{3}):$", preceding_text, re.M))
    assert preceding_approved == {f"AC-{n:03d}" for n in range(1, 7)}
    assert preceding_approved.isdisjoint(planned)
    preceding_deferred = set(
        re.findall(
            r"PLANNED:" + re.escape(PRECEDING_INCREMENT) + r"/(AC-\d{3})",
            _section(preceding_text, "EXPLICITLY OUT OF SCOPE"),
        )
    )
    assert preceding_deferred == planned

    own_text = CONTRACT.read_text(encoding="utf-8")
    own_approved = set(re.findall(r"^(AC-\d{3}):$", own_text, re.M))
    assert own_approved == {f"AC-{n:03d}" for n in range(1, 9)}
    assert own_approved.isdisjoint(planned)
    assert _section(own_text, "UNAPPROVED SCOPE CHANGES").strip() == "NONE"

    # The census plans nothing under this increment's own identifier: a criterion identity is
    # the pair (increment, criterion), so this contract's approved identities and the census's
    # planned identities are disjoint even though both number from AC-001.
    assert _census_criteria_for(INCREMENT) == set()
    planned_identities = set(
        re.findall(
            r"PLANNED:([A-Za-z0-9][A-Za-z0-9._-]*)/(AC-\d{3})",
            CENSUS.read_text(encoding="utf-8"),
        )
    )
    assert planned_identities
    assert {(INCREMENT, ac) for ac in own_approved}.isdisjoint(planned_identities)
    assert {(PRECEDING_INCREMENT, ac) for ac in preceding_approved}.isdisjoint(
        planned_identities
    )
    for census_criterion in planned:
        assert census_criterion not in own_approved

    assert semantic.PARITY_STATUS_NOT_VERIFIED == "NOT_VERIFIED"
    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.AUTHORITY_UNDECIDED == "UNDECIDED"
    assert semantic.COUNTS_AND_PNL_RECONCILIATION == "NOT_RECORDED"
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
    assert semantic.RATIFIED_THRESHOLDS == ()
    assert "NOT_VERIFIED" in PARITY_SPEC.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# AC-007: every unproducible section of the added pages is declared
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_007_every_unproducible_section_of_the_added_pages_is_declared():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-007: declared, never dropped.

    GIVEN the two pages this increment adds to the served set and the sections of those pages
    this plane cannot produce
    WHEN each added page's withheld sections are compared with the frozen registry, the
    census's own criteria for that page and the registered reason vocabulary
    THEN every unproducible section is declared with a census criterion of that page and a
    reason the service registers, every panel whose disposition is qualified has its
    qualification declared, and no declared reason is invented
    """
    census_text = CENSUS.read_text(encoding="utf-8")
    registered_reasons = {
        value
        for name, value in vars(semantic).items()
        if name.startswith("REASON_") and isinstance(value, str)
    }
    assert registered_reasons

    pages = {
        "P4_PAPER_PORTFOLIO": "P4",
        "P5_QUALITY_MISSED_OPPORTUNITY": "P5",
    }
    for page_id, prefix in pages.items():
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
        assert len(census_criteria) == 7, sorted(census_criteria)

        cited = {criterion for _section_name, criterion, _reason in sections}
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


# --------------------------------------------------------------------------- #
# AC-008: the delivery record, the movable pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_008_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-C-paper-portfolio-quality/AC-008: recorded, never claimed.

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
    assert read_increment_pointer(POINTER) in declared
    assert (CONTRACT_DIR / f"{read_increment_pointer(POINTER)}.md").is_file()
    assert CONTRACT.is_file()

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
    assert global_pointer_pin_violations(
        pathlib.Path(__file__).read_text(encoding="utf-8")
    ) == []

    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", pathlib.Path(__file__).read_text(encoding="utf-8")))
    assert cited == set(contract.acceptance_criteria)

    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    assert "19e3b3cef5623018a4d5870a322470067f314cbb" in report
    assert "pull/376" in report
    assert "pull/377" in report
    assert "pull/378" in report
    assert f"{_acceptance_test_count(pathlib.Path(__file__))} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "| R7 governed retirement | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
