"""F10-A semantic read service: what the service declares, and what it refuses.

Increment: ``ATDD-R5-F10-semantic-read-service``. The authority for what each
assertion means is ``docs/atdd/scope-contracts/ATDD-R5-F10-semantic-read-service.md``;
what each page, panel and fact *is* is governed by
``docs/architecture/OPIP_F10_SOURCE_CENSUS.md``.

How to read these tests. The service is declaration-only: it mounts nothing and it
owns no store, so the tests assert its behaviour over an injected reconciled ledger
and its declarations against the versioned metric registry, the frozen page
inventory and the registered evidence gaps. They fail closed if the service is edited
to restate a formula, to serve a blocked fact as a zero, to ignore a filter, to widen
the mounted surface, or to claim parity or a ratified threshold.

What these tests do not do: they never read the global ``ACTIVE_INCREMENT`` pointer,
they add no dependency and no runtime import beyond the projections and contract
modules they verify against, and they grant no trading, admission, Telegram,
promotion, release or deployment authority.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.cockpit import semantic
from app.opip.cockpit.ledger import (
    EconomicResult,
    ExecutionResult,
    ExitMechanism,
    LifecycleStatus,
    PaperLedger,
    ReconciledPaperTrade,
)
from app.opip.cockpit.semantic import (
    METRIC_BINDINGS,
    PAGE_BY_ID,
    PAGE_IDS,
    PANEL_BINDINGS,
    PANEL_PAGE,
    POPULATION_FACTS,
    REGISTERED_GAPS,
    UNSERVED_STATES,
    InvalidFilterValue,
    SemanticReadService,
    UnsupportedFact,
    UnsupportedFilter,
    UnsupportedMetric,
    UnsupportedPage,
)
from app.opip.cockpit.trust import unavailable
from app.opip.contracts.paper_metrics import (
    PAPER_METRIC_REGISTRY_VERSION,
    PAPER_METRICS,
    metric_definition,
)

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
API_DIR = APP_ROOT / "app" / "api"
SEMANTIC_SOURCE = APP_ROOT / "app" / "opip" / "cockpit" / "semantic.py"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
PARITY_SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"

pytestmark = pytest.mark.acceptance

NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
WINDOW_START = NOW - timedelta(days=7)
WINDOW_END = NOW + timedelta(days=1)

#: The frozen eight-page inventory, pinned here as well as in the census so a page
#: cannot be added, removed, renamed, split or merged by editing one file.
FROZEN_PAGES: tuple[str, ...] = (
    "P1_SYSTEM_PULSE",
    "P2_DISCOVERY_REGIME",
    "P3_DECISION_TRACE",
    "P4_PAPER_PORTFOLIO",
    "P5_QUALITY_MISSED_OPPORTUNITY",
    "P6_COMMITTEE_VALUE_AI_COST",
    "P7_LEARNING_RELEASES",
    "P8_INCIDENTS_INFRASTRUCTURE",
)

#: Values that must never stand in for missing evidence.
FORBIDDEN_STAND_INS = (0, 0.0, False, "", [], {})


def _row(
    paper_trade_id: str,
    *,
    currency: str | None = "USD",
    status: LifecycleStatus = LifecycleStatus.CLOSED,
    net_pnl: float = 10.0,
    execution_costs: float = 1.0,
    reserved_capital: float = 0.0,
    definitive: bool = True,
    close: datetime | None = NOW,
    outcome: EconomicResult = EconomicResult.WIN,
    reconciliation_state: str | None = "FINAL_VERIFIED",
    policy_version: str = "POLICY:1",
    instrument: str = "BTC/USD",
) -> ReconciledPaperTrade:
    """One reconciled row carrying only the fields these tests exercise."""
    settled = status is LifecycleStatus.CLOSED
    return ReconciledPaperTrade(
        paper_trade_id=paper_trade_id,
        native_symbol=instrument,
        quote_currency=currency,
        policy_version=policy_version,
        lifecycle_status=status,
        last_exit_fill_at=close,
        entry_quantity=1.0,
        exited_quantity=1.0 if settled else 0.0,
        remaining_quantity=0.0 if settled else 1.0,
        gross_pnl=net_pnl + execution_costs,
        execution_costs=execution_costs,
        net_pnl=net_pnl,
        reserved_capital=reserved_capital,
        net_pnl_definitive=definitive and settled,
        economic_result=outcome,
        execution_result=ExecutionResult.FULL_FILL,
        exit_mechanism=ExitMechanism.TARGET,
        terminal_reconciliation_state=reconciliation_state,
        event_ids=(f"EV:{paper_trade_id}",),
    )


def _rows() -> tuple[ReconciledPaperTrade, ...]:
    """Two settled USD rows, one open and one unresolved USD row, one USDT row.

    The open and unresolved rows carry reservations so capital occupancy is not zero,
    and one settled row deliberately has no provable close instant, so the settled
    population and the equity-series population differ by one row.
    """
    return (
        _row("PT:1", net_pnl=10.0, execution_costs=1.0),
        _row("PT:2", net_pnl=-4.0, execution_costs=0.5, outcome=EconomicResult.LOSS),
        _row("PT:3", net_pnl=3.0, close=None),
        _row(
            "PT:4",
            status=LifecycleStatus.OPEN,
            definitive=False,
            close=None,
            reserved_capital=25.0,
            reconciliation_state="OPEN",
            outcome=EconomicResult.UNRESOLVED,
        ),
        _row(
            "PT:5",
            status=LifecycleStatus.UNRESOLVED,
            definitive=False,
            close=None,
            reserved_capital=5.0,
            reconciliation_state="UNRESOLVED_EVIDENCE",
            outcome=EconomicResult.UNRESOLVED,
        ),
        _row("PT:6", currency="USDT", net_pnl=7.0, execution_costs=2.0),
    )


def _service(
    rows: tuple[ReconciledPaperTrade, ...] | None = None,
    *,
    unreadable: bool = False,
    starting_equity: float | None = 1000.0,
) -> SemanticReadService:
    """A service over a synthetic reconciled ledger."""
    entries = _rows() if rows is None else rows
    ledger = (
        PaperLedger(entries=entries, trust=unavailable("CANONICAL_READ_RETRYABLE"))
        if unreadable
        else PaperLedger(entries=entries)
    )
    return SemanticReadService(
        ledger,
        as_of=NOW,
        starting_equity=starting_equity,
        identity="TEST",
    )


def _source() -> str:
    return SEMANTIC_SOURCE.read_text(encoding="utf-8")


def _flat(path: pathlib.Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


# --------------------------------------------------------------------------- #
# AC-001: one semantic model over one metric registry
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_one_versioned_model_over_the_frozen_inventory_and_registry():
    """ATDD-R5-F10-semantic-read-service/AC-001: one model, one registry.

    GIVEN the service's declared contract and the versioned metric registry
    WHEN the frozen page inventory, the metric set and a served metric response are
    inspected
    THEN the service declares exactly the eight frozen pages and exactly the
    registry's metrics, references the registry without restating a formula, and
    pins the versions it was built against
    """
    service = _service()
    contract = service.contract()
    assert tuple(contract["pages"]) == FROZEN_PAGES == PAGE_IDS
    assert set(PAGE_BY_ID) == set(FROZEN_PAGES)
    assert contract["semantic_version"] == PAPER_METRIC_REGISTRY_VERSION
    assert semantic.SEMANTIC_VERSION == PAPER_METRIC_REGISTRY_VERSION

    # One registry: this module binds every identifier the registry publishes and no
    # others, so a metric cannot be added or dropped in one place only.
    assert set(METRIC_BINDINGS) == set(PAPER_METRICS)
    assert len(service.metrics()) == len(PAPER_METRICS)
    for binding in METRIC_BINDINGS.values():
        definition = metric_definition(binding.metric_id)
        assert definition.definition_version == PAPER_METRIC_REGISTRY_VERSION
        if binding.population_fact is not None:
            assert binding.population_fact in POPULATION_FACTS
        if binding.gap_id is not None:
            assert binding.gap_id in REGISTERED_GAPS

    # Every declared panel belongs to exactly one page, and no page owns an
    # undeclared panel.
    declared_panels = {panel.panel_id for panel in PANEL_BINDINGS}
    assert set(PANEL_PAGE) == declared_panels
    assert len(PANEL_BINDINGS) == sum(
        len(page.panels) for page in semantic.PAGE_REGISTRY
    )

    # The definition stays in the registry: the response references it and no
    # registered formula text appears anywhere in the response.
    reading = service.metric("paper.realized_net_pnl", quote_currency="USD")
    serialized = json.dumps(reading.to_dict())
    for definition in PAPER_METRICS.values():
        assert definition.formula not in serialized, definition.metric_id
    reference = reading.definition_reference or {}
    assert reference["metric_id"] == "paper.realized_net_pnl"
    assert reference["definition_version"] == PAPER_METRIC_REGISTRY_VERSION
    assert reference["formula_restated_in_read_model"] is False
    assert reference["eligible_population"] == (
        PAPER_METRICS["paper.realized_net_pnl"].eligible_population
    )
    assert reference["unit"] == PAPER_METRICS["paper.realized_net_pnl"].unit.value
    assert reading.grain == semantic.SERVED_GRAIN
    assert reading.unit == PAPER_METRICS["paper.realized_net_pnl"].unit.value


# --------------------------------------------------------------------------- #
# AC-002: missing evidence is never favorable evidence
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_unserved_facts_and_pages_are_explicit_never_favorable():
    """ATDD-R5-F10-semantic-read-service/AC-002: no zero for a non-answer.

    GIVEN a healthy reconciled ledger and an unreadable one
    WHEN every registered metric, every page and the blocked panels are read
    THEN a fact this plane cannot produce carries an explicit state, a reason and,
    where one is registered, a gap identifier - never a value, an empty collection or
    a favourable stand-in
    """
    service = _service()
    for metric_id in sorted(PAPER_METRICS):
        reading = service.metric(metric_id, quote_currency="USD")
        payload = reading.to_dict()
        assert payload["state"] in {state.value for state in semantic.FactState}
        if reading.state in UNSERVED_STATES:
            assert payload["value"] is None, metric_id
            assert reading.reasons, metric_id
            assert payload["value"] not in FORBIDDEN_STAND_INS, metric_id
            assert payload["rows"] == []
            assert payload["reasons"], metric_id
        elif reading.state is semantic.FactState.SERVED_WITHHELD:
            assert payload["value"] is None, metric_id
            assert reading.reasons, metric_id

    # A blocked metric names a registered gap; an absent panel names its reason.
    capture = service.metric("paper.capture_efficiency", quote_currency="USD")
    assert capture.state is semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE
    assert capture.gap_id in REGISTERED_GAPS
    assert capture.value is None
    ai_cost = service.panel("P6-PNL-04")
    assert ai_cost.state is semantic.FactState.ABSENT
    assert ai_cost.unserved_reasons == (semantic.REASON_NO_CANONICAL_AI_COST,)
    assert ai_cost.to_dict()["value"] is None

    # An unreadable projection answers with a state, never an empty healthy result.
    broken = _service(unreadable=True)
    for reading in (
        broken.metric("paper.realized_net_pnl", quote_currency="USD"),
        broken.population("paper.population.rows"),
        broken.population("ledger.row.identity"),
        broken.population("read_model.trust"),
    ):
        assert reading.state is semantic.FactState.UNAVAILABLE
        assert reading.value is None
        assert semantic.REASON_UNREADABLE in reading.reasons

    # A page whose required facts are not producible serves no page value, even when
    # some of its panels happen to be readable.
    page = service.page("P4_PAPER_PORTFOLIO")
    payload = page.to_dict()
    assert page.state is semantic.FactState.BLOCKED_BY_UPSTREAM_EVIDENCE
    assert page.reason == semantic.REASON_BLOCKED_PAGE
    assert payload["value"] is None
    assert payload["page_value_claimed"] is False
    assert payload["unmet_requirements"]
    assert any(
        family["availability"] != "EXISTING_AND_VERIFIED"
        for family in payload["required_fact_families"]
    )
    served = {fact for state in payload["panel_states"] for fact in state["served"]}
    assert served, "a blocked page must still report which facts remain reachable"

    # Every frozen page: an explicit reason, no claim, every panel declared.
    for reading in service.pages():
        assert reading.state in UNSERVED_STATES
        assert reading.reason == semantic.REASON_BLOCKED_PAGE
        assert reading.descriptor["page_reason"] == semantic.REASON_BLOCKED_PAGE
        assert len(reading.panel_states) == len(
            PAGE_BY_ID[reading.subject_id].panels
        )
        assert reading.to_dict()["value"] is None

    # Undeclared subjects are refused, never approximated.
    with pytest.raises(UnsupportedFact):
        service.population("paper.population.not_declared")
    with pytest.raises(UnsupportedMetric):
        service.metric("paper.not_a_registered_metric")
    with pytest.raises(UnsupportedPage):
        service.page("P9_NOT_A_PAGE")
    with pytest.raises(UnsupportedFact):
        service.panel("P9-PNL-99")


# --------------------------------------------------------------------------- #
# AC-003: the typed filter contract
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_typed_filters_are_enforced_and_never_silently_ignored():
    """ATDD-R5-F10-semantic-read-service/AC-003: a filter is applied or refused.

    GIVEN the declared filter allowlist, its vocabularies, its aliases and the
    declared row field subsets
    WHEN supported filters, an undeclared filter, a declared-but-unsupported filter
    and out-of-vocabulary values are sent, and a row population is paged
    THEN supported filters narrow the population and are echoed as effective, a
    window excludes and counts rows it cannot place, and everything else is refused
    rather than ignored
    """
    service = _service()

    # A declared alias resolves to its canonical dimension, and the effective filter
    # list reports the dimension and the row field it constrains.
    request = semantic.FilterRequest.parse(status="CLOSED")
    assert request.lifecycle_status == "CLOSED"
    assert request.effective() == (
        {
            "dimension": "lifecycle_status",
            "value": "CLOSED",
            "constrains": "lifecycle_status",
        },
        {
            "dimension": "limit",
            "value": semantic.DEFAULT_PAGE_SIZE,
            "constrains": "PAGINATION",
        },
    )

    # The declared filter vocabulary covers every filter the panel map declares, and a
    # panel rendering never drops a declared filter.
    declared_names = (
        set(semantic.SUPPORTED_FILTERS)
        | set(semantic.DECLARED_FILTER_ALIASES)
        | set(semantic.UNSUPPORTED_DECLARED_FILTERS)
    )
    for panel in PANEL_BINDINGS:
        assert set(panel.declared_filters) <= declared_names, panel.panel_id
        assert len(panel.to_dict()["unresolved_filters"]) == len(
            panel.unresolved_filters
        ), panel.panel_id
    assert (
        semantic.FilterRequest.parse(
            **{"lifecycle status": "CLOSED"}
        ).lifecycle_status
        == "CLOSED"
    )
    assert semantic.PANEL_BY_ID["P2-PNL-03"].resolved_filters == (
        "lifecycle_status",
        "window_start",
        "window_end",
    )
    assert semantic.PANEL_BY_ID["P2-PNL-01"].resolved_filters == (
        "instrument",
        "window_start",
        "window_end",
    )

    # A supported filter narrows the population it names.
    every = service.population("paper.population.rows")
    open_only = service.population("paper.population.rows", status="OPEN")
    assert every.value == 6
    assert open_only.value == 1
    assert every.state is open_only.state is semantic.FactState.SERVED
    assert any(
        item["dimension"] == "lifecycle_status"
        for item in open_only.to_dict()["effective_filters"]
    )

    # A complete scan that matches no row is a measured zero, and is reported as one:
    # "nothing matched this filter" is evidence, unlike "this cannot be read here".
    none_matched = service.population("paper.population.rows", instrument="ETH/USD")
    assert none_matched.state is semantic.FactState.SERVED
    assert none_matched.value == 0
    assert none_matched.observations == 0

    # A window cannot place a row without a provable close instant: it excludes it and
    # says so, instead of dropping it silently.
    windowed = service.population(
        "paper.population.rows", window_start=WINDOW_START, window_end=WINDOW_END
    )
    assert windowed.observations == 3
    assert semantic.EXCLUSION_WITHOUT_CLOSE_INSTANT in windowed.exclusions

    # Row fact publications carry exactly the declared field subset, and every
    # declared field exists on the ledger row.
    ledger_fields = {field.name for field in dataclasses.fields(ReconciledPaperTrade)}
    for fields in semantic.ROW_FACT_FIELDS.values():
        assert set(fields) <= ledger_fields
    first_page = service.population("ledger.row.identity", limit=2)
    assert set(first_page.rows[0]) == set(
        semantic.ROW_FACT_FIELDS["ledger.row.identity"]
    )
    assert first_page.value == 2
    assert first_page.observations == 6
    assert first_page.next_cursor
    second_page = service.population(
        "ledger.row.identity", limit=2, cursor=first_page.next_cursor
    )
    assert second_page.value == 2
    assert [row["paper_trade_id"] for row in first_page.rows] != [
        row["paper_trade_id"] for row in second_page.rows
    ]

    # Refusals: undeclared, declared-but-unsupported, out-of-vocabulary, malformed.
    with pytest.raises(UnsupportedFilter):
        service.population("paper.population.rows", regime="TREND")
    with pytest.raises(UnsupportedFilter) as severity:
        service.population("paper.population.rows", severity="HIGH")
    assert semantic.REASON_NO_CANONICAL_INCIDENT_STORE in str(severity.value)
    with pytest.raises(UnsupportedFilter):
        service.population(
            "paper.population.rows", **{"time window": "2026-05-01T00:00:00Z"}
        )
    with pytest.raises(InvalidFilterValue):
        service.metric("paper.realized_net_pnl", quote_currency="EUR")
    with pytest.raises(InvalidFilterValue):
        service.population("paper.population.rows", status="NOT_A_STATUS")
    with pytest.raises(InvalidFilterValue):
        service.population("paper.population.rows", limit=0)
    with pytest.raises(InvalidFilterValue):
        service.population("paper.population.rows", limit=semantic.MAX_PAGE_SIZE + 1)
    with pytest.raises(InvalidFilterValue):
        service.population(
            "paper.population.rows", window_start=datetime(2026, 5, 1)
        )
    with pytest.raises(InvalidFilterValue):
        service.population(
            "paper.population.rows", window_start=WINDOW_END, window_end=WINDOW_START
        )
    with pytest.raises(InvalidFilterValue):
        service.metric("paper.realized_net_pnl")
    with pytest.raises(InvalidFilterValue) as conflicting:
        service.population(
            "paper.population.rows",
            quote_currency="USD",
            **{"quote currency": "USDT"},
        )
    assert "conflicting" in str(conflicting.value)
    with pytest.raises(InvalidFilterValue):
        service.population("ledger.row.identity", cursor="not-a-cursor")

    # A cursor is bound to the ledger it was minted against: replaying it elsewhere
    # would resume somewhere other than where the client believes it is.
    other = _service(rows=(_row("PT:99"),))
    with pytest.raises(InvalidFilterValue):
        other.population("ledger.row.identity", cursor=first_page.next_cursor)


# --------------------------------------------------------------------------- #
# AC-004: declaration is not activation
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_declaration_mounts_nothing_and_adds_no_authority():
    """ATDD-R5-F10-semantic-read-service/AC-004: declared routes stay unmounted.

    GIVEN the declared read routes, the analytics API package and the service source
    WHEN the mounted surface and the source are inspected
    THEN nothing is mounted, no API module names or mounts a semantic route, and the
    source holds no HTTP, write, order, exchange or release-authority path
    """
    assert semantic.MOUNTED is False
    assert semantic.MOUNTED_PATHS == ()
    assert semantic.DECLARED_ROUTES, "the surface is declared even while unmounted"

    api_sources = {
        path: path.read_text(encoding="utf-8") for path in API_DIR.glob("*.py")
    }
    assert api_sources, "the analytics API package must exist"
    for path, text in api_sources.items():
        assert "cockpit import semantic" not in text, path
        assert "cockpit.semantic" not in text, path
        for route in semantic.DECLARED_ROUTES:
            assert route.split(" ", 1)[1] not in text, (path, route)

    source = _source()
    # No HTTP surface and no route decorator.
    for forbidden in ("fastapi", "APIRouter", "@router", "HTTPException"):
        assert forbidden not in source, forbidden
    # No write path, and not the production writer's RPC seam.
    for forbidden in (
        "CanonicalWriter",
        "for_writes",
        "get_paper_v2_ledger",
        "INSERT",
        "UPDATE ",
        "DELETE ",
        ".commit(",
    ):
        assert forbidden not in source, forbidden
    # No exchange or order authority.
    for forbidden in (
        "kraken",
        "place_order",
        "cancel_order",
        "modify_order",
        "create_order",
    ):
        assert forbidden not in source, forbidden
    # The service imports the read-only seams and the standard library, nothing else:
    # a notification, promotion, release or deployment module cannot be reached from
    # here because it is not imported at all. Producer *names* the census declares
    # (for example another plane's delivery module) remain strings in the panel map.
    allowed_imports = {
        "__future__",
        "base64",
        "json",
        "dataclasses",
        "datetime",
        "enum",
        "types",
        "typing",
        "app.opip.cockpit.ledger",
        "app.opip.cockpit.portfolio",
        "app.opip.contracts.paper_execution",
        "app.opip.contracts.paper_metrics",
        "app.opip.contracts.paper_outcome",
        "app.opip.profit_intelligence.semantics",
    }
    import_targets = {
        line.split()[1]
        for line in source.splitlines()
        if line.startswith(("from ", "import "))
    }
    assert import_targets <= allowed_imports, sorted(import_targets - allowed_imports)
    # Declared absences of the census stay absent: no cross-currency total and no
    # saved-view store may appear here.
    assert "CROSS_CURRENCY_TOTAL" not in source
    assert "SAVED_VIEW_STORE" not in source
    # Existing dormancy guards assert that no application module outside the owning
    # modules names the shadow detector package or the plan record class, so this
    # service must cite those blocked producers without the guarded symbols.
    assert "opip.detectors" not in source
    assert "PortfolioPlan" not in source

    # A reading advertises the surface state and the authority it does not hold.
    reading = _service().metric("paper.realized_net_pnl", quote_currency="USD")
    assert reading.to_dict()["mounted"] is False
    assert reading.to_dict()["authority"] == semantic.AUTHORITY_UNDECIDED


# --------------------------------------------------------------------------- #
# AC-005: counts reconcile with the projection, per currency
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_counts_reconcile_with_the_projection_per_currency():
    """ATDD-R5-F10-semantic-read-service/AC-005: recount, or report the difference.

    GIVEN a reconciled ledger and the portfolio projection built from it
    WHEN the declared populations and the currency-scoped metrics are read, and a
    projection count is made to disagree with its own rows
    THEN every count reconciles at one watermark per currency, a disagreement is
    published as PROJECTION_COUNT_DOES_NOT_RECONCILE, and USD and USDT stay separate
    """
    service = _service()

    settled = service.population("paper.population.settled")
    report = settled.reconciliation or {}
    assert report["status"] == "RECONCILED"
    assert report["mismatches"] == ()
    assert settled.value == 4
    assert settled.unit == "COUNT"
    assert set(report["recounted"]) == {"USD", "USDT"}
    assert (report["recounted"]["USD"] or {})["settled"] == 3
    assert (report["recounted"]["USDT"] or {})["settled"] == 1

    # A settled row with no provable close instant is settled but not placeable on the
    # equity series, and both populations are reported rather than merged.
    assert service.population("paper.population.settled").value == 4
    assert service.population("paper.population.settled_in_equity_series").value == 3

    for fact_id in sorted(POPULATION_FACTS):
        reading = service.population(
            fact_id,
            **(
                {"quote_currency": "USD"}
                if fact_id == "paper.population.reserved_capital"
                else {}
            ),
        )
        assert (reading.reconciliation or {})["status"] == "RECONCILED", fact_id

    # Currencies are separate portfolios: the same metric is answered per currency and
    # never as one figure.
    usd = service.metric("paper.realized_net_pnl", quote_currency="USD")
    usdt = service.metric("paper.realized_net_pnl", quote_currency="USDT")
    assert usd.value == 9.0
    assert usdt.value == 7.0
    reserved = service.population(
        "paper.population.reserved_capital", quote_currency="USD"
    )
    assert reserved.value == 30.0
    assert reserved.unit == "QUOTE_CURRENCY"
    with pytest.raises(InvalidFilterValue):
        service.population("paper.population.reserved_capital")

    # A row with no recorded currency groups under UNKNOWN rather than being folded
    # into a currency that was never recorded.
    unknown = _service(rows=(_row("PT:UNK", currency=None),) + _rows())
    unknown_report = unknown.population("paper.population.rows").reconciliation or {}
    assert "UNKNOWN" in unknown_report["recounted"]
    assert unknown_report["status"] == "RECONCILED"

    # A projection count that disagrees with the rows it was built from is published,
    # not reconciled away: the projection stays the declared authority and the
    # difference is the finding.
    drift = _service()
    overview = drift.overview
    portfolios = tuple(
        dataclasses.replace(item, settled_trades=item.settled_trades + 1)
        if item.quote_currency == "USD"
        else item
        for item in overview.portfolios
    )
    drift._overview = dataclasses.replace(overview, portfolios=portfolios)
    mismatched = drift.population("paper.population.settled")
    assert mismatched.state is semantic.FactState.SERVED_PARTIAL
    assert semantic.REASON_POPULATION_MISMATCH in mismatched.reasons
    assert "USD:settled" in (mismatched.reconciliation or {})["mismatches"]

    # No cross-currency total exists, in this module or in any response it produces.
    assert "CROSS_CURRENCY_TOTAL" not in _source()
    assert "CROSS_CURRENCY_TOTAL" not in json.dumps(
        service.population("paper.population.rows").to_dict()
    )


# --------------------------------------------------------------------------- #
# AC-006: preserved parity, threshold and authority state
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_006_thresholds_parity_and_authority_are_preserved():
    """ATDD-R5-F10-semantic-read-service/AC-006: nothing is ratified or claimed.

    GIVEN the service's contract, every kind of reading it can produce, and the
    census and parity documents of the preceding increment
    WHEN the threshold policy, the parity state and the authority fields are inspected
    THEN no threshold is ratified, parity stays NOT_VERIFIED with zero verified pages,
    projection authority stays UNDECIDED, and the preceding documents are unchanged
    """
    service = _service()
    contract = service.contract()
    assert semantic.THRESHOLD_POLICY == "OWNER_DECISION_REQUIRED"
    assert semantic.RATIFIED_THRESHOLDS == ()
    assert contract["ratified_thresholds"] == []
    assert contract["thresholds"] == semantic.THRESHOLD_POLICY
    assert semantic.PARITY_STATUS_NOT_VERIFIED == "NOT_VERIFIED"
    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert contract["parity_verified_pages"] == 0
    assert semantic.AUTHORITY_UNDECIDED == "UNDECIDED"
    assert contract["authority"] == semantic.AUTHORITY_UNDECIDED
    assert semantic.COUNTS_AND_PNL_RECONCILIATION == "NOT_RECORDED"
    assert contract["counts_and_pnl_reconciliation"] == "NOT_RECORDED"
    assert contract["proof_required"] == semantic.PROOF_REQUIRED
    assert contract["follower_increment"] == semantic.FOLLOWER_INCREMENT

    for reading in (
        service.metric("paper.realized_net_pnl", quote_currency="USD"),
        service.population("paper.population.rows"),
        service.population("read_model.reconciliation_posture"),
        service.page("P4_PAPER_PORTFOLIO"),
        service.panel("P4-PNL-02"),
    ):
        payload = reading.to_dict()
        assert payload["authority"] == semantic.AUTHORITY_UNDECIDED
        assert payload["parity_status"] == semantic.PARITY_STATUS_NOT_VERIFIED
        assert payload["parity_verified_pages"] == 0
        assert payload["thresholds"] == semantic.THRESHOLD_POLICY
        assert payload["mounted"] is False

    # The increment this one follows keeps its recorded state.
    census = _flat(CENSUS)
    assert "PARITY_STATUS: NOT_VERIFIED" in census
    assert "PARITY_VERIFIED_PAGES: 0 of 8" in census
    assert "THRESHOLD_POLICY: OWNER_DECISION_REQUIRED" in census
    assert "RATIFIED_THRESHOLDS: NONE" in census
    assert "RUNTIME_ACTIVE: FALSE" in census
    parity = _flat(PARITY_SPEC)
    assert "Parity verdict: `NOT_VERIFIED` (0 of 8 pages verified)" in parity
    assert "PARITY_STATUS: VERIFIED" not in parity


# --------------------------------------------------------------------------- #
# Negative and boundary coverage for AC-001 .. AC-006
#
# The tests above prove the six approved criteria on the paths they exercise. These
# add the edges those paths stop short of: the frozen inventory totals, the whole
# declaration surface, out-of-range pagination, window and limit boundaries that are
# excluded rather than rounded, an empty projection, an unattributed quote currency,
# and the source's own ambient-authority and version-restatement surface. None of
# them relaxes an existing assertion: an addition that would need a service change to
# pass is a defect report, not a licence to loosen the contract.
# --------------------------------------------------------------------------- #

#: Every reason code this module declares, plus the contract-defect reason for a
#: declared filter the vocabulary does not resolve. A panel, metric or reading may
#: cite these and nothing else.
DECLARED_REASON_CODES: frozenset[str] = frozenset(
    [
        value
        for name, value in vars(semantic).items()
        if name.startswith("REASON_") and isinstance(value, str)
    ]
    + [semantic.UNDECLARED_FILTER_REASON]
)


def test_ac_001_inventory_totals_and_panel_totality_are_frozen():
    """ATDD-R5-F10-semantic-read-service/AC-001: the frozen inventory, in total.

    GIVEN the service's declared contract and the versioned metric registry
    WHEN the page, panel and metric inventories are counted and every panel is
    checked against the census panel map
    THEN the totals are the frozen ones (eight pages, thirty-four panels, every
    registered metric bound exactly once), every panel has one owning page, and every
    panel's planned acceptance test is the census's own citation
    """
    service = _service()
    contract = service.contract()

    # Totals. Pinned as literals as well as against the registries, so a silent
    # addition or removal fails here and not only in a rendered page later.
    assert len(semantic.PAGE_REGISTRY) == 8
    assert len(semantic.PANEL_BINDINGS) == 34
    assert len(semantic.METRIC_BINDINGS) == len(PAPER_METRICS) == 13
    assert len(contract["panels"]) == 34
    assert len(contract["metric_bindings"]) == 13

    # Every panel is declared once, owns exactly one page, and that page lists it.
    panel_ids = [panel.panel_id for panel in PANEL_BINDINGS]
    assert len(panel_ids) == len(set(panel_ids))
    assert set(PANEL_PAGE) == set(panel_ids)
    for panel in PANEL_BINDINGS:
        owner = PANEL_PAGE[panel.panel_id]
        assert panel.panel_id in PAGE_BY_ID[owner].panels
        assert panel.panel_id.startswith(owner.split("_", 1)[0] + "-PNL-")
        assert panel.operator_question and panel.fact_declaration and panel.trust_rule
        assert panel.drill_target and panel.criterion and panel.producer

    # Every panel's planned test is the census's own citation, and the criteria are
    # distinct: a copied criterion cannot make two panels look planned at once.
    criteria = [panel.criterion for panel in PANEL_BINDINGS]
    assert len(set(criteria)) == len(criteria)
    census = _flat(CENSUS)
    pattern = re.compile(
        rf"^PLANNED:{re.escape(semantic.FOLLOWER_INCREMENT)}/AC-\d{{3}}$"
    )
    for criterion in criteria:
        assert pattern.match(criterion), criterion
        assert criterion in census, criterion

    # Panel disposition stays inside the declared vocabularies: a panel is served from a
    # declared fact or a registry metric, and from nothing else.
    declared_states = {state.value for state in semantic.FactState}
    servable_names = semantic.SERVED_FACT_NAMES | frozenset(semantic.METRIC_BINDINGS)
    for panel in PANEL_BINDINGS:
        assert panel.state.value in declared_states, panel.panel_id
        assert set(panel.served) <= servable_names, panel.panel_id
        assert set(panel.unserved_reasons) <= DECLARED_REASON_CODES, panel.panel_id
        if panel.gap_id is not None:
            assert panel.gap_id in REGISTERED_GAPS, panel.panel_id
        assert panel.carries_value is (
            panel.state in semantic.SERVED_STATES and bool(panel.served)
        ), panel.panel_id


def test_ac_001_the_projection_is_built_at_most_once_per_service(monkeypatch):
    """ATDD-R5-F10-semantic-read-service/AC-001: one projection build per service.

    GIVEN a service over an injected ledger and a counted portfolio projection builder
    WHEN several readings of different kinds are answered from it
    THEN the projection is built exactly once and every reading is answered from that
    one build, so no reading can be served from a different aggregation than another
    """
    real_build = semantic.build_overview
    builds: list[int] = []

    def counted(*args, **kwargs):
        builds.append(1)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(semantic, "build_overview", counted)
    service = _service()
    assert builds == []
    service.metric("paper.realized_net_pnl", quote_currency="USD")
    service.metric("paper.unresolved_count")
    service.population("read_model.attention")
    service.page("P1_SYSTEM_PULSE")
    service.panel("P1-PNL-03")
    service.metrics()
    assert len(builds) == 1
    assert service.overview is service.overview
    assert service.portfolios == service.portfolios



def test_ac_002_a_served_state_is_only_ever_reached_with_its_value():
    """ATDD-R5-F10-semantic-read-service/AC-002: a served state is never a non-answer.

    GIVEN a binding whose declared outcome is a served one and a projection that
    publishes the field its ``value_source`` names
    WHEN each such metric is read with a request that satisfies its declared requirement
    THEN the reading carries that value with the qualification the binding declares, so
    a served state is only ever reached with data behind it - and the same metric is
    withheld with the binding's own ``withheld_when`` reason when the field itself does
    not exist, rather than served as ``None``
    """
    service = _service()
    served_outcomes = (
        semantic.FactState.SERVED,
        semantic.FactState.SERVED_PARTIAL,
    )
    for metric_id, binding in sorted(METRIC_BINDINGS.items()):
        if binding.outcome not in served_outcomes:
            continue
        # A served binding names where its value comes from; a served reading that
        # carried nothing would be a measured nothing wearing a served state.
        assert binding.value_source is not None, metric_id
        reading = (
            service.metric(metric_id, quote_currency="USD")
            if binding.requires_quote_currency
            else service.metric(metric_id)
        )
        assert reading.state in served_outcomes, metric_id
        assert reading.value is not None, metric_id
        assert reading.carries_value, metric_id
        assert set(reading.reasons) <= DECLARED_REASON_CODES, metric_id

    # The qualified served form: the realized percentage is the projection's own field,
    # served with the mark-evidence qualification the binding declares.
    drawdown_id = "paper.realized_equity_drawdown_pct"
    binding = METRIC_BINDINGS[drawdown_id]
    assert binding.withheld_when == semantic.REASON_NO_STARTING_EQUITY
    drawdown = service.metric(drawdown_id, quote_currency="USD")
    portfolio = service.portfolios["USD"]
    assert drawdown.state is semantic.FactState.SERVED_PARTIAL
    assert drawdown.value == portfolio.drawdown.realized_max_drawdown_pct
    assert semantic.REASON_NO_MARK_EVIDENCE in drawdown.reasons
    assert drawdown.observations == 2

    # Without a declared starting equity the percentage has no base: withheld with the
    # reason the binding itself names, and the withholding stays explicit - a null value
    # beside the population it would have been computed over, never a zero and never a
    # silently empty served state.
    no_base = _service(starting_equity=None).metric(
        drawdown_id, quote_currency="USD"
    )
    assert no_base.state is semantic.FactState.SERVED_WITHHELD
    assert no_base.to_dict()["value"] is None
    assert no_base.value is None
    assert semantic.REASON_NO_STARTING_EQUITY in no_base.reasons
    assert no_base.observations == 2


def test_ac_005_a_metric_free_of_a_currency_requirement_is_answered_without_one():
    """ATDD-R5-F10-semantic-read-service/AC-005: only the declared requirement applies.

    GIVEN the ledger-recount metric whose binding declares no quote-currency requirement
    WHEN it is read without a currency and per currency
    THEN it is served in every case, its value is the recount over exactly the rows the
    request covers, and a currency the binding does not require is never demanded of the
    caller as a condition of an answer
    """
    service = _service()
    binding = METRIC_BINDINGS["paper.unresolved_count"]
    assert binding.requires_quote_currency is False
    assert binding.outcome is semantic.FactState.SERVED

    unscoped = service.metric("paper.unresolved_count")
    usd = service.metric("paper.unresolved_count", quote_currency="USD")
    usdt = service.metric("paper.unresolved_count", quote_currency="USDT")
    for reading in (unscoped, usd, usdt):
        assert reading.state is semantic.FactState.SERVED
        assert reading.value is not None
        assert reading.carries_value
        # The requirement this metric does not declare is never imposed on the caller.
        assert semantic.REASON_QUOTE_CURRENCY_REQUIRED not in reading.reasons

    # One row in this ledger is unreconciled (PT:5). The currency-free answer counts the
    # rows of every currency, because a row count is declared at the ledger grain, while
    # the per-currency answers never reach past their own portfolio.
    assert usd.value == 1
    assert usdt.value == 0
    assert unscoped.value == usd.value + usdt.value
    assert unscoped.observations == 6
    assert "paper.population.rows" in unscoped.population_scope


def test_ac_002_an_empty_projection_withholds_rather_than_zeroes_a_portfolio():
    """ATDD-R5-F10-semantic-read-service/AC-002: an absent portfolio is not a zero.

    GIVEN a service over a reconciled ledger that carries no rows at all
    WHEN the declared populations and the currency-scoped metrics are read
    THEN a complete scan of no rows is served as a measured zero, while a metric whose
    value lives in a per-currency portfolio this projection does not show is withheld
    with an explicit reason, a null value and a scope naming the absent portfolio -
    never served as 0.0, and never served with nothing behind it
    """
    service = _service(rows=())
    assert service.is_readable
    assert service.portfolios == {}

    # A count over a complete scan of an empty ledger is a measured zero: "nothing is
    # here" is evidence, exactly as it is when a filter matches nothing.
    for fact_id in ("paper.population.rows", "paper.population.settled"):
        reading = service.population(fact_id)
        assert reading.state is semantic.FactState.SERVED, fact_id
        assert reading.value == 0, fact_id
        assert reading.observations == 0, fact_id
        assert reading.unit == "COUNT", fact_id
        assert (reading.reconciliation or {})["status"] == "RECONCILED", fact_id

    # The ledger-grain count needs no currency, and is measured the same way.
    unresolved = service.metric("paper.unresolved_count")
    assert unresolved.state is semantic.FactState.SERVED
    assert unresolved.value == 0
    assert unresolved.observations == 0

    # A portfolio-scoped metric has no portfolio to read. It is withheld with a null
    # value, the reason and the scope that names what is missing - so an empty ledger
    # cannot be read as a flat account, and the withheld served state is still not a
    # measured zero.
    for metric_id in (
        "paper.realized_net_pnl",
        "paper.execution_cost",
        "paper.realized_equity_drawdown_pct",
    ):
        assert METRIC_BINDINGS[metric_id].requires_quote_currency, metric_id
        reading = service.metric(metric_id, quote_currency="USD")
        assert reading.state is semantic.FactState.SERVED_WITHHELD, metric_id
        assert reading.value is None, metric_id
        assert reading.to_dict()["value"] is None, metric_id
        assert reading.value not in FORBIDDEN_STAND_INS, metric_id
        assert semantic.REASON_QUOTE_CURRENCY_REQUIRED in reading.reasons, metric_id
        assert "USD portfolio" in reading.population_scope, metric_id

    # Every registered metric is still answerable against an empty projection: the
    # registry is fully reachable and none of it raises in place of answering.
    assert len(service.metrics()) == len(PAPER_METRICS)
    # And no page becomes claimable because a ledger happens to be empty.
    assert service.page("P4_PAPER_PORTFOLIO").to_dict()["page_value_claimed"] is False

