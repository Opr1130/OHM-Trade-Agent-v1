"""F10-E acceptance tests: the operator interaction contract, exercised over HTTP.

Increment: ``ATDD-R5-F10-E-operator-interaction``.

These are **operator journeys through the mounted analytics-plane application**, not source-token
assertions: the secret gate, the routing, the typed filter parsing, the read model and the
serialization are all real, and only the replica read is injected so a journey can be stated over
known rows. They are **not** browser end-to-end tests, and they are not labelled as such: the
presentation half of the interaction contract (breadcrumbs, filter controls, presets, keyboard
accessibility, responsive rendering) is a later increment, and a structural assertion about an
asset is not an operator validation.

They claim no parity and settle no projection authority: serving a page honestly is not proving it.
"""

from __future__ import annotations

import pathlib
import re
import sys
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

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
from app.opip.cockpit.semantic import FilterRequest, OrValues, SemanticReadService
from app.opip.cockpit.semantic_pages import PRESETS, filter_catalog
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

INCREMENT = "ATDD-R5-F10-E-operator-interaction"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"

SECRET = "expected-secret"
HEADERS = {"x-webhook-secret": SECRET}
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

pytestmark = pytest.mark.acceptance


def _row(
    paper_trade_id: str,
    *,
    symbol: str,
    currency: str,
    status: LifecycleStatus,
    result: EconomicResult,
    execution: ExecutionResult = ExecutionResult.FULL_FILL,
    policy: str = "POLICY:1",
    remaining: float = 0.0,
    net_pnl: float = 10.0,
) -> ReconciledPaperTrade:
    """One reconciled row, so a journey can be stated over a known population."""
    return ReconciledPaperTrade(
        paper_trade_id=paper_trade_id,
        native_symbol=symbol,
        quote_currency=currency,
        policy_version=policy,
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
        economic_result=result,
        execution_result=execution,
        exit_mechanism=ExitMechanism.TARGET,
        terminal_reconciliation_state="FINAL_VERIFIED",
        event_ids=(f"EV:{paper_trade_id}",),
    )


def _entries() -> tuple[ReconciledPaperTrade, ...]:
    """Four rows over two instruments, two currencies, two lifecycles and two policies."""
    return (
        _row("PT:1", symbol="BTC/USD", currency="USD", status=LifecycleStatus.CLOSED,
             result=EconomicResult.WIN, net_pnl=10.0),
        _row("PT:2", symbol="ETH/USD", currency="USD", status=LifecycleStatus.CLOSED,
             result=EconomicResult.LOSS, net_pnl=-4.0),
        _row("PT:3", symbol="BTC/USD", currency="USD", status=LifecycleStatus.OPEN,
             result=EconomicResult.WIN, remaining=0.5),
        _row("PT:4", symbol="SOL/USDT", currency="USDT", status=LifecycleStatus.CLOSED,
             result=EconomicResult.WIN, policy="POLICY:2", net_pnl=7.0),
    )


def _service(*, unreadable: bool = False) -> SemanticReadService:
    """The service over the journey's rows, or over an unreadable replica."""
    ledger = (
        PaperLedger(entries=_entries(), trust=unavailable("CANONICAL_READ_RETRYABLE"))
        if unreadable
        else PaperLedger(entries=_entries())
    )
    return SemanticReadService(ledger, as_of=NOW, starting_equity=1000.0, identity="TEST")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The mounted analytics-plane app, with the replica read injected and the gate real."""
    monkeypatch.setenv("OPIP_COCKPIT_SECRET", SECRET)
    service = _service()
    monkeypatch.setattr(read_router, "_service", lambda: service)
    return TestClient(cockpit_service.app)


def _value(body: dict) -> object:
    return body["reading"]["value"]


def _effective(body: dict) -> dict[str, object]:
    """The effective filters of a reading, without the transport dimensions.

    The paging bound is always echoed because the service declares a default page size; it is
    transport rather than evidence identity, so a scope comparison excludes it.
    """
    return {
        entry["dimension"]: entry["value"]
        for entry in body["reading"]["effective_filters"]
        if entry["dimension"] not in ("limit", "cursor")
    }


# --------------------------------------------------------------------------- #
# AC-001: AND across dimensions and OR within a dimension
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_001_and_across_dimensions_and_or_within_a_dimension(client: TestClient):
    """ATDD-R5-F10-E-operator-interaction/AC-001: the filter algebra an operator is promised.

    GIVEN the mounted read surface and a known population of four reconciled lifecycles
    WHEN populations are read unfiltered, with one value, with the same dimension stated twice,
    with two dimensions, and with both at once
    THEN the population narrows to exactly the rows the stated algebra selects, and every
    reading echoes the scope it was actually answered under
    """
    url = "/api/cockpit/semantic/facts/paper.population.rows"

    unfiltered = client.get(url, headers=HEADERS).json()
    assert _value(unfiltered) == 4
    assert _effective(unfiltered) == {}

    single = client.get(url, headers=HEADERS, params={"instrument": "BTC/USD"}).json()
    assert _value(single) == 2
    assert _effective(single) == {"instrument": "BTC/USD"}

    # OR within one dimension: the same dimension stated twice selects the union.
    either = client.get(
        url, headers=HEADERS, params=[("instrument", "BTC/USD"), ("instrument", "ETH/USD")]
    ).json()
    assert _value(either) == 3
    assert _effective(either) == {"instrument": ["BTC/USD", "ETH/USD"]}

    # AND across dimensions: both dimensions must hold.
    both = client.get(
        url,
        headers=HEADERS,
        params={"quote_currency": "USD", "lifecycle_status": "OPEN"},
    ).json()
    assert _value(both) == 1
    assert _effective(both) == {"quote_currency": "USD", "lifecycle_status": "OPEN"}

    # OR inside AND, on top of the currency partition: the USDT row stays out.
    combined = client.get(
        url,
        headers=HEADERS,
        params=[
            ("instrument", "BTC/USD"),
            ("instrument", "ETH/USD"),
            ("lifecycle_status", "CLOSED"),
        ],
    ).json()
    assert _value(combined) == 2
    assert _effective(combined) == {
        "instrument": ["BTC/USD", "ETH/USD"],
        "lifecycle_status": "CLOSED",
    }

    # The two currencies are never summed or joined: a USDT question is answered on its own.
    usdt = client.get(url, headers=HEADERS, params={"quote_currency": "USDT"}).json()
    assert _value(usdt) == 1
    unknown = client.get(url, headers=HEADERS, params={"quote_currency": "USD"}).json()
    assert _value(unknown) + _value(usdt) == 4


# --------------------------------------------------------------------------- #
# AC-002: a stated value is never silently dropped, and a repeat that cannot widen is refused
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_002_a_stated_value_is_never_silently_dropped(client: TestClient):
    """ATDD-R5-F10-E-operator-interaction/AC-002: the last value is not the only value.

    GIVEN the mounted read surface and the service's typed filter contract
    WHEN a dimension is stated more than once, when a dimension that cannot widen is stated
    twice, and when two different spellings of one dimension disagree
    THEN every stated value of a widen-capable dimension reaches the answer, a repeated
    non-widening dimension is refused rather than resolved to its last value, and an ambiguous
    pair of spellings is refused
    """
    url = "/api/cockpit/semantic/facts/paper.population.rows"

    # The defect this pins: reading a repeated dimension with ``query[name]`` keeps only the
    # last value, so ``instrument=BTC/USD&instrument=ETH/USD`` would have answered about ETH
    # alone and silently dropped BTC.
    repeated = client.get(
        url, headers=HEADERS, params=[("instrument", "BTC/USD"), ("instrument", "ETH/USD")]
    ).json()
    assert _effective(repeated)["instrument"] == ["BTC/USD", "ETH/USD"]

    for dimension, first, second in (
        ("limit", "1", "2"),
        ("window_start", "2026-01-01T00:00:00Z", "2026-02-01T00:00:00Z"),
    ):
        refused = client.get(
            url, headers=HEADERS, params=[(dimension, first), (dimension, second)]
        )
        assert refused.status_code == 400, (dimension, refused.text)
        assert "ORs" in refused.text

    # A repeated value that equals itself is not a widen and is answered once.
    idempotent = client.get(
        url, headers=HEADERS, params=[("instrument", "BTC/USD"), ("instrument", "BTC/USD")]
    ).json()
    assert _effective(idempotent)["instrument"] == "BTC/USD"
    assert _value(idempotent) == 2

    # Two spellings of one dimension that disagree stay refused as ambiguous. That is stated
    # where the second spelling is declared -- at the service -- because the HTTP surface's
    # allowlist admits canonical names only, and it refuses the alias as an undeclared name.
    with pytest.raises(semantic.InvalidFilterValue, match="conflicting"):
        FilterRequest.parse(**{"quote_currency": "USD", "quote currency": "USDT"})
    undeclared = client.get(url, headers=HEADERS, params={"quote currency": "USDT"})
    assert undeclared.status_code == 400
    assert "UNSUPPORTED_FILTER" in undeclared.text

    parsed = FilterRequest.parse(instrument=["BTC/USD", "ETH/USD"])
    assert isinstance(parsed.instrument, OrValues)
    assert parsed.instrument.values == ("BTC/USD", "ETH/USD")
    # One value collapses to the scalar, so nothing has to handle two spellings of one scope.
    assert FilterRequest.parse(instrument=["BTC/USD"]).instrument == "BTC/USD"
    with pytest.raises(semantic.InvalidFilterValue):
        FilterRequest.parse(limit=[1, 2])
    with pytest.raises(semantic.InvalidFilterValue):
        FilterRequest.parse(instrument=[])


# --------------------------------------------------------------------------- #
# AC-003: the catalog and the presets are published from the declarations themselves
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_003_the_filter_catalog_and_the_presets_come_from_the_declarations(client: TestClient):
    """ATDD-R5-F10-E-operator-interaction/AC-003: a control cannot offer what the service refuses.

    GIVEN the page index the mounted surface serves, the service's declared filter contract and
    the read model's declared presets
    WHEN the catalog and the presets are compared with those declarations, and every preset's
    drill link is followed
    THEN the catalog is exactly the declared dimensions with their declared vocabularies and
    widen-capability, every preset names declared dimensions, in-vocabulary values and declared
    facts, and following each preset's link resolves to a real page read
    """
    index = client.get("/api/cockpit/semantic/pages", headers=HEADERS).json()
    catalog = index["filters"]
    assert [entry["dimension"] for entry in catalog] == list(semantic.SUPPORTED_FILTERS)
    assert tuple(catalog) == filter_catalog()
    for entry in catalog:
        assert entry["or_capable"] is (entry["dimension"] in semantic.OR_CAPABLE_FILTERS)
        vocabulary = semantic.FILTER_VOCABULARIES.get(entry["dimension"])
        assert entry["values"] == (sorted(vocabulary) if vocabulary else None)
        assert entry["constrains"] == semantic.FILTER_ROW_FIELDS[entry["dimension"]]

    published = index["presets"]
    assert [preset["preset_id"] for preset in published] == [
        preset.preset_id for preset in PRESETS
    ]
    declared_facts = (
        set(semantic.SERVED_FACT_NAMES)
        | set(semantic.METRIC_BINDINGS)
        | set(semantic.POPULATION_FACTS)
        | set(semantic.READ_MODEL_FACTS)
    )
    for preset in published:
        assert preset["requires"], preset["preset_id"]
        assert set(preset["requires"]) <= declared_facts, preset["preset_id"]
        for dimension, value in preset["filters"].items():
            assert dimension in semantic.SUPPORTED_FILTERS, (preset["preset_id"], dimension)
            vocabulary = semantic.FILTER_VOCABULARIES.get(dimension)
            if vocabulary is not None:
                members = value if isinstance(value, list) else [value]
                assert set(members) <= vocabulary, (preset["preset_id"], dimension)
        assert preset["page_id"] in semantic.PAGE_BY_ID, preset["preset_id"]

        # Following the preset is how it is evaluated, and every one of them resolves.
        query = preset["filters"]
        followed = client.get(
            f"/api/cockpit/semantic/pages/{preset['page_id']}", headers=HEADERS, params=query
        )
        assert followed.status_code == 200, preset["preset_id"]
        page = followed.json()
        assert page["page_id"] == preset["page_id"]
        assert page["claimed"] is False
        # A preset never asserts a result: each of its panels either publishes a value or
        # states why it cannot.
        assert page["panels"], preset["preset_id"]
        for panel in page["panels"]:
            if panel["carries_value"]:
                continue
            assert panel["panel_reasons"] or panel["evidence_gap"], (
                preset["preset_id"],
                panel["panel_id"],
            )

    # The preset that needs an upstream-only population reports insufficiency rather than a
    # result, and the index says so without pretending the preset is unavailable.
    missed = next(item for item in published if item["preset_id"] == "MISSED_AND_LATE")
    assert missed["requires"] == ["paper.capture_efficiency"]
    assert missed["evaluated_by"].startswith("FOLLOW_THE_DRILL_LINK")


# --------------------------------------------------------------------------- #
# AC-004: cross-filter context, drill-down, deterministic paging and one watermark
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_004_drill_links_preserve_context_and_paging_stays_deterministic(client: TestClient):
    """ATDD-R5-F10-E-operator-interaction/AC-004: the operator stays in the question they asked.

    GIVEN a page read under a filter, the drill targets it publishes and a paged row fact
    WHEN the page's and a panel's drill links are followed, and the paged fact is walked twice
    THEN a followed link asks the same question under the same watermark, the evidence reading
    reports the same filters as the page it came from, and two identical walks return the same
    rows with no duplicate and no omission across pages
    """
    page_url = "/api/cockpit/semantic/pages/P4_PAPER_PORTFOLIO"
    index = client.get("/api/cockpit/semantic/pages", headers=HEADERS).json()
    page = client.get(page_url, headers=HEADERS, params={"quote_currency": "USD"}).json()
    unfiltered = client.get(page_url, headers=HEADERS).json()

    assert page["drill"]["route"] == page_url
    assert page["drill"]["query"] == {"quote_currency": "USD"}
    assert page["drill"]["kind"] == "PAGE"

    followed = client.get(page["drill"]["route"], headers=HEADERS, params=page["drill"]["query"]).json()
    assert followed["watermark"] == page["watermark"]
    assert followed["drill"] == page["drill"]

    panel = next(item for item in page["panels"] if item["panel_id"] == "P4-PNL-03")
    assert panel["drill"]["route"] == "/api/cockpit/semantic/panels/P4-PNL-03"
    assert panel["drill"]["query"] == {"quote_currency": "USD"}
    drilled = client.get(
        panel["drill"]["route"], headers=HEADERS, params=panel["drill"]["query"]
    ).json()["panel"]
    assert drilled["page_id"] == page["page_id"]
    assert drilled["drill"]["query"] == panel["drill"]["query"]

    # Context preservation, proved by the evidence rather than by the link text: the panel read
    # through its own link selects the USD population, and the same panel read without the
    # filter selects more. A link that dropped the filter would return the wider population.
    usd_rows = panel["value"]["paper.population.rows"]
    assert usd_rows["value"] == 3
    assert usd_rows["watermark"] == page["watermark"]
    other = next(item for item in unfiltered["panels"] if item["panel_id"] == "P4-PNL-03")
    assert other["value"]["paper.population.rows"]["value"] == 4
    assert drilled["value"]["paper.population.rows"]["value"] == 3

    # One watermark for one replica across the index, two pages and a drilled reading.
    first_page = client.get("/api/cockpit/semantic/pages/P1_SYSTEM_PULSE", headers=HEADERS).json()
    assert first_page["watermark"] == index["watermark"] == page["watermark"]

    # Deterministic paging: the same query and the same watermark return the same rows, and
    # walking the cursor covers the population exactly once.
    def walk() -> tuple[list[str], list[str]]:
        seen: list[str] = []
        cursors: list[str] = []
        params: dict[str, str] = {"limit": "2"}
        while True:
            body = client.get(
                "/api/cockpit/semantic/facts/ledger.row.identity", headers=HEADERS, params=params
            ).json()["reading"]
            seen.extend(row["paper_trade_id"] for row in body["rows"])
            cursors.append(body["watermark"])
            if not body["next_cursor"]:
                return seen, cursors
            params = {"limit": "2", "cursor": body["next_cursor"]}

    first, watermarks = walk()
    second, _ = walk()
    assert first == second
    assert len(first) == len(set(first)) == 4
    assert len(watermarks) > 1
    assert len(set(watermarks)) == 1


# --------------------------------------------------------------------------- #
# AC-005: every refusal and failure mode is explicit, and a failure is never a zero
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_005_every_refusal_and_failure_mode_is_explicit(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-R5-F10-E-operator-interaction/AC-005: no silent scope, no favorable failure.

    GIVEN the mounted read surface, its secret gate and the routes it declares
    WHEN a request arrives without the secret, with a wrong secret, with an unsupported
    dimension, with an out-of-vocabulary value, for an unknown subject, as a write, and when
    the replica is unreadable
    THEN each refusal is a client error that names its reason, no write reaches a read route,
    and an unreadable replica answers unavailable with no value rather than a zero
    """
    facts = "/api/cockpit/semantic/facts/paper.population.rows"

    assert client.get(facts).status_code == 401
    assert client.get(facts, headers={"x-webhook-secret": "wrong"}).status_code == 401

    for path, params in (
        (facts, {"bogus": "x"}),
        (facts, {"quote_currency": "XYZ"}),
        (facts, {"limit": "0"}),
        (facts, {"limit": "nope"}),
        (facts, {"window_start": "2026-01-01T00:00:00"}),
    ):
        refused = client.get(path, headers=HEADERS, params=params)
        assert refused.status_code == 400, (params, refused.text)

    for path in (
        "/api/cockpit/semantic/pages/P9_UNKNOWN",
        "/api/cockpit/semantic/panels/P9-PNL-01",
        "/api/cockpit/semantic/facts/not.a.fact",
        "/api/cockpit/semantic/metrics/not.a.metric",
    ):
        assert client.get(path, headers=HEADERS).status_code == 404, path

    # A read surface answers no write.
    assert client.post(facts, headers=HEADERS, json={}).status_code == 405

    # An unreadable replica is an explicit unavailable reading, never an empty or zero result.
    monkeypatch.setattr(read_router, "_service", lambda: _service(unreadable=True))
    degraded = client.get(facts, headers=HEADERS).json()["reading"]
    assert degraded["state"] == "UNAVAILABLE"
    assert degraded["value"] is None
    assert degraded["reasons"]

    # The page still renders with every panel present, so a failure cannot look like a
    # shortened page.
    degraded_page = client.get(
        "/api/cockpit/semantic/pages/P4_PAPER_PORTFOLIO", headers=HEADERS
    ).json()
    assert len(degraded_page["panels"]) == 5
    for panel in degraded_page["panels"]:
        assert panel["carries_value"] is (panel["value"] is not None)
        assert panel["value"] != 0


# --------------------------------------------------------------------------- #
# AC-006: the report, the pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_006_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-E-operator-interaction/AC-006: recorded, never claimed.

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
    assert "0526d973" in report
    assert "pull/376" in report and "pull/377" in report and "pull/378" in report
    # Counted by pattern so the assertion's own text cannot inflate the number it checks.
    journeys = len(re.findall(r"@pytest\.mark\.acceptance", source))
    assert f"{journeys} operator journeys" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report

    assert semantic.PARITY_VERIFIED_PAGES == 0
    assert semantic.PARITY_STATUS_NOT_VERIFIED == "NOT_VERIFIED"
