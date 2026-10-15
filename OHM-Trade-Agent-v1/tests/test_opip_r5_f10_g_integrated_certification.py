"""F10-G acceptance tests: integrated certification across the eight pages and the read surface.

Increment: ``ATDD-R5-F10-G``.

These are **integration and contract tests over the mounted analytics-plane application** plus
**structural assertions about the served asset**. No headless browser runs in this repository, so
nothing here is labelled browser end-to-end; the distinction is stated in every test that could be
mistaken for one.

They certify no page. The six-clause parity predicate needs reconciled counts and P&L against a
declared projection authority at one watermark, and that proof is not recorded for any page, so
every page stays ``NOT_VERIFIED`` and this suite asserts that it does.
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
from app.opip.cockpit.semantic import SemanticReadService
from app.opip.cockpit.semantic_pages import ALL_PAGES
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

INCREMENT = "ATDD-R5-F10-G"

CONTRACT_DIR = APP_ROOT / "docs" / "atdd" / "scope-contracts"
CONTRACT = CONTRACT_DIR / f"{INCREMENT}.md"
POINTER = APP_ROOT / "docs" / "atdd" / "ACTIVE_INCREMENT"
BUILD_REPORT = APP_ROOT / "AUTONOMOUS_BUILD_REPORT.md"
MATRIX = APP_ROOT / "docs" / "atdd" / "evidence" / "F10_CRITERION_MATRIX.md"
ASSET = APP_ROOT / "app" / "api" / "cockpit_semantic.html"

SECRET = "expected-secret"
HEADERS = {"x-webhook-secret": SECRET}
NOW = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)

LEGACY_READ_PATHS: tuple[str, ...] = (
    "/cockpit",
    "/api/cockpit/overview",
    "/api/cockpit/trades",
    "/api/cockpit/trades/{paper_trade_id}",
)

pytestmark = pytest.mark.acceptance


def _row(
    paper_trade_id: str,
    *,
    symbol: str,
    currency: str,
    status: LifecycleStatus = LifecycleStatus.CLOSED,
    result: EconomicResult = EconomicResult.WIN,
    remaining: float = 0.0,
    net_pnl: float = 10.0,
) -> ReconciledPaperTrade:
    return ReconciledPaperTrade(
        paper_trade_id=paper_trade_id,
        native_symbol=symbol,
        quote_currency=currency,
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
        economic_result=result,
        execution_result=ExecutionResult.FULL_FILL,
        exit_mechanism=ExitMechanism.TARGET,
        terminal_reconciliation_state="FINAL_VERIFIED",
        event_ids=(f"EV:{paper_trade_id}",),
    )


def _entries() -> tuple[ReconciledPaperTrade, ...]:
    """Two currency partitions, one partial residual, one resolved and one open row."""
    return (
        _row("PT:1", symbol="BTC/USD", currency="USD", net_pnl=10.0),
        _row("PT:2", symbol="ETH/USD", currency="USD", result=EconomicResult.LOSS, net_pnl=-4.0),
        _row(
            "PT:3",
            symbol="BTC/USD",
            currency="USD",
            status=LifecycleStatus.OPEN,
            remaining=0.5,
        ),
        _row("PT:4", symbol="SOL/USDT", currency="USDT", net_pnl=7.0),
    )


def _service(*, unreadable: bool = False, empty: bool = False) -> SemanticReadService:
    entries = () if empty else _entries()
    ledger = (
        PaperLedger(entries=entries, trust=unavailable("CANONICAL_READ_RETRYABLE"))
        if unreadable
        else PaperLedger(entries=entries)
    )
    return SemanticReadService(ledger, as_of=NOW, starting_equity=1000.0, identity="TEST")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("OPIP_COCKPIT_SECRET", SECRET)
    monkeypatch.setattr(read_router, "_service", lambda: _service())
    return TestClient(cockpit_service.app)


def _page(client: TestClient, page_id: str, **params: object) -> dict:
    response = client.get(
        f"/api/cockpit/semantic/pages/{page_id}", headers=HEADERS, params=params
    )
    assert response.status_code == 200, (page_id, response.text)
    return response.json()


# --------------------------------------------------------------------------- #
# AC-110: accessibility and inspectable operator-usefulness evidence
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_110_accessibility_and_operator_usefulness_evidence(client: TestClient):
    """ATDD-R5-F10-G/AC-110: the eight-page experience is operable, and the evidence is inspectable.

    GIVEN the served asset, the mounted read surface and the recorded evidence
    WHEN every frozen page is reached, the asset's accessibility contract is inspected, and the
    evidence artifacts are located
    THEN every page is reachable and reports itself unclaimed, the asset carries keyboard, focus,
    labelling, table and viewport semantics, and the operator-usefulness evidence is inspectable
    in the repository rather than asserted in prose

    This is a structural assertion about the served asset plus an HTTP reachability check. It is
    **not** a browser end-to-end test: no headless browser runs here.
    """
    index = client.get("/api/cockpit/semantic/pages", headers=HEADERS).json()
    assert [entry["page_id"] for entry in index["pages"]] == list(ALL_PAGES)
    for page_id in ALL_PAGES:
        page = _page(client, page_id)
        assert page["page_id"] == page_id
        assert page["claimed"] is False, page_id
        assert page["panels"], page_id

    asset = ASSET.read_text(encoding="utf-8")
    for token in (
        ":focus-visible",
        'role="tablist"',
        'aria-selected',
        'role="status"',
        'aria-live="polite"',
        'role="alert"',
        "<caption>",
        'scope="col"',
        "@media",
        'class="skip"',
    ):
        assert token in asset, token

    # Inspectable evidence: the criterion matrix carries the per-criterion dispositions and the
    # build report carries the delivery record; neither is a prose claim.
    assert MATRIX.is_file()
    assert "CRITERIA_RECORDED: 54" in MATRIX.read_text(encoding="utf-8")
    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report


# --------------------------------------------------------------------------- #
# AC-128: one watermark and consistent populations across the eight pages
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_128_one_watermark_and_consistent_populations_across_pages(client: TestClient):
    """ATDD-R5-F10-G/AC-128: consistent watermarks and eligible populations.

    GIVEN the mounted read surface over one reconciled replica
    WHEN every page, the index and a row-grain fact are read
    THEN every reading reports the same watermark, a page's panel reading agrees with the row
    population, and the two quote currencies are answered separately rather than joined
    """
    index = client.get("/api/cockpit/semantic/pages", headers=HEADERS).json()
    watermarks = {index["watermark"]}
    for page_id in ALL_PAGES:
        watermarks.add(_page(client, page_id)["watermark"])
    assert len(watermarks) == 1, watermarks
    watermark = watermarks.pop()

    rows = client.get(
        "/api/cockpit/semantic/facts/paper.population.rows", headers=HEADERS
    ).json()["reading"]
    assert rows["watermark"] == watermark
    assert rows["value"] == 4

    # Aggregate/detail agreement where the service publishes both: the panel's own fact reading
    # and the population fact are the same population at the same watermark.
    paper = _page(client, "P4_PAPER_PORTFOLIO", quote_currency="USD")
    panel = next(item for item in paper["panels"] if item["panel_id"] == "P4-PNL-03")
    reading = panel["value"]["paper.population.rows"]
    assert reading["value"] == 3
    assert reading["watermark"] == watermark

    # Currency partition: USD and USDT are answered separately, never summed.
    usdt = client.get(
        "/api/cockpit/semantic/facts/paper.population.rows",
        headers=HEADERS,
        params={"quote_currency": "USDT"},
    ).json()["reading"]
    usd = client.get(
        "/api/cockpit/semantic/facts/paper.population.rows",
        headers=HEADERS,
        params={"quote_currency": "USD"},
    ).json()["reading"]
    assert usd["value"] == 3 and usdt["value"] == 1
    assert usd["value"] + usdt["value"] == rows["value"]
    assert usd["quote_currency"] == "USD" and usdt["quote_currency"] == "USDT"


# --------------------------------------------------------------------------- #
# AC-129: the route inventory, write refusal and legacy coexistence
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_129_route_inventory_write_refusal_and_legacy_coexistence(client: TestClient):
    """ATDD-R5-F10-G/AC-129: a read surface beside the legacy cockpit, not instead of it.

    GIVEN the mounted analytics-plane application and the legacy dashboard surface
    WHEN the mounted path set, the methods each path accepts and the legacy router are inspected
    THEN the mounted set is exactly the legacy cockpit reads plus the declared semantic reads and
    the page asset, every route is a GET, a write is refused, and the legacy cockpit is still
    declared and mounted where it always was
    """
    declared = tuple(route.split(" ", 1)[1] for route in semantic.DECLARED_ROUTES)
    mounted = set(cockpit_service.app.openapi().get("paths", {}) or {})
    assert mounted == set(LEGACY_READ_PATHS) | set(declared) | {read_router.PAGE_ROUTE}

    methods = read_router.route_methods()
    assert all(verbs == {"GET"} for verbs in methods.values())
    for path in (declared[0], read_router.PAGE_ROUTE):
        assert client.post(path, headers=HEADERS, json={}).status_code == 405, path

    # The legacy cockpit still exists and is still mounted by the trading entry point. The
    # analytics plane deliberately does not mount it, and nothing here replaced or re-pointed it.
    legacy = (APP_ROOT / "app" / "api" / "dashboard.py").read_text(encoding="utf-8")
    assert "/dashboard" in legacy
    assert "/api/analytics/" in legacy
    main_source = (APP_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "dashboard" in main_source
    service_source = (APP_ROOT / "app" / "api" / "cockpit_service.py").read_text(
        encoding="utf-8"
    )
    assert "app.api.dashboard" not in service_source


# --------------------------------------------------------------------------- #
# AC-130: uniform behaviour when evidence is missing, empty or unreadable
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_130_missing_evidence_is_uniform_and_never_favorable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    """ATDD-R5-F10-G/AC-130: no favorable state from missing, empty or unreadable evidence.

    GIVEN the mounted read surface and a replica that is empty, then unreadable
    WHEN every page is read in both states and an impossible filter and a missing credential are
    presented to each page
    THEN every page still renders with its declared panels, an empty valid population is a real
    zero while unreadable evidence is unavailable with no value at all, and both refusals hold for
    every page
    """
    monkeypatch.setattr(read_router, "_service", lambda: _service(empty=True))
    empty_rows = client.get(
        "/api/cockpit/semantic/facts/paper.population.rows", headers=HEADERS
    ).json()["reading"]
    assert empty_rows["state"] == "SERVED"
    assert empty_rows["value"] == 0  # an empty *valid* population is a real zero

    monkeypatch.setattr(read_router, "_service", lambda: _service(unreadable=True))
    for page_id in ALL_PAGES:
        page = _page(client, page_id)
        assert page["panels"], page_id
        assert page["claimed"] is False, page_id
        for panel in page["panels"]:
            assert panel["carries_value"] is (panel["value"] is not None), panel["panel_id"]
            assert panel["value"] != 0, panel["panel_id"]

    unreadable = client.get(
        "/api/cockpit/semantic/facts/paper.population.rows", headers=HEADERS
    ).json()["reading"]
    assert unreadable["state"] == "UNAVAILABLE"
    assert unreadable["value"] is None
    assert unreadable["reasons"]

    for page_id in ALL_PAGES:
        refused = client.get(
            f"/api/cockpit/semantic/pages/{page_id}",
            headers=HEADERS,
            params={"unsupported_dimension": "x"},
        )
        assert refused.status_code == 400, page_id
        assert client.get(f"/api/cockpit/semantic/pages/{page_id}").status_code == 401, page_id


# --------------------------------------------------------------------------- #
# AC-131: representative integrated journeys across the pages
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_131_representative_integrated_journeys(client: TestClient):
    """ATDD-R5-F10-G/AC-131: journeys an operator actually takes, end to end over HTTP.

    GIVEN the mounted read surface, the declared presets and the drill targets
    WHEN a settled-economics journey, a non-definitive-economics journey and a missing-evidence
    journey are taken in sequence
    THEN each journey reaches its page, publishes what the evidence supports, reports the rest as
    withheld or unavailable with reasons, and never presents an absence as a result
    """
    index = client.get("/api/cockpit/semantic/pages", headers=HEADERS).json()
    presets = {preset["preset_id"]: preset for preset in index["presets"]}

    # Journey 1: what did the isolated paper account earn in USD, net of cost?
    settled = presets["SETTLED_USD_ECONOMICS"]
    paper = _page(client, settled["page_id"], **settled["filters"])
    earnings = next(item for item in paper["panels"] if item["panel_id"] == "P4-PNL-02")
    assert earnings["carries_value"] is True
    assert earnings["value"]["paper.realized_net_pnl"]["value"] == 6.0
    assert earnings["value"]["paper.execution_cost"]["value"] == 2.0

    # Journey 2: which lifecycles have no settled economics yet? The question this plane cannot
    # answer in full reports its own withholding instead of a number.
    unresolved = presets["NON_DEFINITIVE_ECONOMICS"]
    quality = _page(client, unresolved["page_id"], **unresolved["filters"])
    serving = [item for item in quality["panels"] if item["carries_value"]]
    withheld = [item for item in quality["panels"] if not item["carries_value"]]
    assert serving or withheld
    for panel in withheld:
        assert panel["panel_reasons"] or panel["evidence_gap"], panel["panel_id"]

    # Journey 3: the missed-opportunity question has no population on this plane. It must resolve
    # to a page whose panels state their insufficiency rather than to a fabricated zero.
    missed = presets["MISSED_AND_LATE"]
    missed_page = _page(client, missed["page_id"], **missed["filters"])
    assert missed_page["claimed"] is False
    for panel in missed_page["panels"]:
        if panel["carries_value"]:
            continue
        assert panel["panel_reasons"] or panel["evidence_gap"], panel["panel_id"]

    # Journey 4: follow a decision-trace link from the paper page and land on the trace context,
    # still read-only and still unclaimed.
    trace = presets["SETTLED_USD_ECONOMICS"]
    drill = paper["drill"]
    followed = client.get(drill["route"], headers=HEADERS, params=drill["query"]).json()
    assert followed["page_id"] == trace["page_id"]
    assert followed["claimed"] is False
    trace_page = _page(client, "P3_DECISION_TRACE")
    assert trace_page["claimed"] is False
    assert any(item["panel_id"].startswith("P3-PNL-") for item in trace_page["panels"])


# --------------------------------------------------------------------------- #
# AC-132: the delivery record, the pointer and the criteria agree
# --------------------------------------------------------------------------- #


@pytest.mark.acceptance
def test_ac_132_the_report_the_pointer_and_the_criteria_agree():
    """ATDD-R5-F10-G/AC-132: recorded, never claimed.

    GIVEN the autonomous build report, this increment's scope contract, the movable pointer and
    the acceptance-test index
    WHEN the report, the contract, the pointer and the index are read together
    THEN the report records this increment with the exact base head it stacks on, its own contract
    and its own test count, the pointer resolves to a declared contract, every approved criterion
    is traced to the test that cites it and mapped to a path, and the parity row stays OUTSTANDING
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

    census = (
        APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
    ).read_text(encoding="utf-8")
    assert set(
        re.findall(r"PLANNED:" + re.escape(INCREMENT) + r"/(AC-\d{3})", census)
    ) == {"AC-110"}

    index = load_acceptance_index(APP_ROOT / "tests")
    result = check_scope(contract, index)
    assert result.status == "PASS", result.reasons

    source = pathlib.Path(__file__).read_text(encoding="utf-8")
    cited = set(re.findall(re.escape(INCREMENT) + r"/(AC-\d{3})", source))
    assert cited == set(contract.acceptance_criteria)

    report = BUILD_REPORT.read_text(encoding="utf-8")
    assert INCREMENT in report
    assert f"{INCREMENT}.md" in report
    cases = len(re.findall(r"@pytest\.mark\.acceptance", source))
    assert f"{cases} acceptance tests" in report
    assert "| F10 dashboard parity | OUTSTANDING |" in report
    assert "NOT_VERIFIED" in report
    assert semantic.PARITY_VERIFIED_PAGES == 0
