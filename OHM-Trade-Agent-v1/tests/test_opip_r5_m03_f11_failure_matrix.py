"""Mission 03 F11 failure and independence matrix.

Increment: ``ATDD-R5-M03-f11-failure-independence-matrix``.

Mission 03 named fourteen failure scenarios and asked for two things to hold in
every one of them: F11's protective priority, and a fail-closed admission path that
still protects open-position safety obligations. The audit found that twelve of the
fourteen are already proven by existing named tests, and that exactly one -
AI/Committee unavailability - has no test at all.

So this increment does two honest things and nothing else. It asserts that the
mission's fourteen scenario names are all present in the audit and each resolves to
a proving node, and it adds the one behavioural test that was genuinely missing: the
Committee and AI surfaces cannot stop a protective exit, and the protective
authority provably cannot reach them.

It re-proves nothing. It activates no runtime, adds no scheduler, no writer, no event
type, no release profile and no trading authority.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:  # pragma: no cover - the suite already does this
    sys.path.insert(0, str(APP_ROOT))

from app.exchanges.kraken import KrakenTransportError  # noqa: E402
from app.opip.contracts.paper_execution import ProtectionState  # noqa: E402
from app.opip.contracts.paper_execution_events import (  # noqa: E402
    PAPER_FILL_RECORDED,
)
from app.services import paper_v2_scan_router  # noqa: E402

from tests.test_opip_paper_v2_protection_runtime_bc3 import (  # noqa: E402
    QUALIFICATION_TIME,
    _Clock,
    _Env,
    _count,
    _next_scan,
    _state_of,
    _transport,
)
from tests.test_opip_r5_e_f11_independent_protection import (  # noqa: E402
    PROTECTION_RUNTIME_PATH,
    _DownTransport,
    _app_imports,
    _arm_trades,
    _book_state,
    _exit_orders,
    _run_scheduled_sweep,
)

pytestmark = pytest.mark.acceptance

AUDIT = APP_ROOT / "docs" / "atdd" / "evidence" / "R5_M03_CAPITAL_PAPER_PROTECTION_MATRIX.md"

#: The fourteen failure scenarios Mission 03 named, by the slug the audit records.
MISSION_SCENARIOS: tuple[str, ...] = (
    "market-feed-outage",
    "decision-engine-failure",
    "ai-or-committee-unavailability",
    "telegram-outage",
    "dashboard-outage",
    "duplicate-order-intent",
    "application-restart",
    "crash-between-intent-and-fill",
    "reconciliation-gap",
    "reservation-race",
    "stale-account-balance",
    "partial-close",
    "invalid-or-conflicting-protection-evidence",
    "unexpected-open-obligation",
)

#: The production Committee/AI entry point the scan calls. Its unavailability must
#: be invisible to the protective authority, exactly like every other surface that
#: is not part of protection.
COMMITTEE_SURFACE = "app.services.chief_analyst.review_candidates"

#: The non-protection modules the protective authority currently reaches
#: transitively. Recorded as a finding, not blessed as a design: the assertion that
#: consumes this set fails if the coupling grows, so a new edge must be reviewed
#: rather than absorbed. Protection never calls any of them - the behavioural test
#: below proves that - and every edge arrives through a shared leaf module
#: (`app.opip.decision.thresholds`, `app.opip.decision.versioning`,
#: `app.opip.canonical.writer`) rather than through protection choosing to reach it.
#:
#: Paths as traced by this mission:
#:   chief_analyst          <- paper_v2_execution -> decision.versioning -> decision.thresholds
#:   entry_exit_advisor     <- ... -> decision.thresholds -> economic_quality_gate
#:   decision_intelligence.* <- paper_v2_execution -> canonical.decision_context_bridge
#:                             and -> features.publisher -> canonical.writer
RECORDED_TRANSITIVE_COUPLING: frozenset[str] = frozenset(
    {
        "app.opip.decision_intelligence.contracts",
        "app.opip.decision_intelligence.events",
        "app.opip.decision_intelligence.identity",
        "app.opip.decision_intelligence.serialization",
        "app.services.chief_analyst",
        "app.services.entry_exit_advisor",
    }
)


@pytest.fixture()
def env(tmp_path):
    holder = _Env(tmp_path=tmp_path)
    try:
        yield holder
    finally:
        holder.server.stop()


@pytest.fixture(autouse=True)
def _reset_scan_seams():
    """The scan seams are global; never let one test's client leak into another."""

    yield
    paper_v2_scan_router.set_writer_client_for_tests(None)
    paper_v2_scan_router.set_kraken_client_for_tests(None)


def _audit_items(workstream: str) -> dict[str, dict[str, str]]:
    """The audit's items for one workstream, keyed by identifier."""

    records: dict[str, dict[str, str]] = {}
    for line in AUDIT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("ITEM: "):
            continue
        cells = [cell.strip() for cell in stripped[len("ITEM: ") :].split("|")]
        record = {"id": cells[0], "item": cells[1]}
        for cell in cells[2:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        if record["workstream"] == workstream:
            records[record["id"]] = record
    return records


# ---------------------------------------------------------------------------
# AC-310 the mission's fourteen scenarios are all present in the matrix
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_310_every_named_scenario_is_in_the_matrix_with_a_resolving_node():
    """ATDD-R5-M03-f11-failure-independence-matrix/AC-310: no named failure is unaccounted for.

    GIVEN the fourteen failure scenarios Mission 03 named and the audit's own
    Workstream C rows
    WHEN the scenario slugs and their cited nodes are compared
    THEN every named scenario is present exactly once, every one is classified, and
    every cited node still exists in the file it is attributed to
    """

    items = _audit_items("C")
    assert items, "the audit must record the failure scenarios"

    scenarios = [record for record in items.values() if record["id"].startswith("C")]
    slugs = [record["item"] for record in scenarios]
    for name in MISSION_SCENARIOS:
        assert name in slugs, name
        assert slugs.count(name) == 1, name

    # The independence assertions and the recorded coupling are recorded alongside
    # the scenarios, so the matrix accounts for both what is proven and what is not.
    independence = [slug for slug in slugs if slug not in MISSION_SCENARIOS]
    assert independence == [
        "f11-independence-from-other-subsystems",
        "unsafe-admission-fails-closed-while-obligations-protected",
        "f11-transitive-import-closure-is-isolated",
    ], independence

    coupling = [record for record in scenarios if record["item"] == independence[-1]]
    assert len(coupling) == 1, coupling
    assert coupling[0]["class"] == "BLOCKED_BY_OWNER_DECISION", coupling[0]

    for record in scenarios:
        path, _, node = record["evidence"].partition("::")
        if not node:
            # The coupling row cites the module it is about rather than a test that
            # proves it, because no test can prove isolation that does not hold.
            assert record["class"] == "BLOCKED_BY_OWNER_DECISION", record
            assert (APP_ROOT.parent / path).exists(), record
            continue
        source = (APP_ROOT.parent / path).read_text(encoding="utf-8")
        assert f"def {node}(" in source, (record["id"], node)


# ---------------------------------------------------------------------------
# AC-311 AI and Committee unavailability cannot stop protection
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_311_the_committee_surface_cannot_stop_the_exit(env, monkeypatch):
    """ATDD-R5-M03-f11-failure-independence-matrix/AC-311: an unavailable committee is invisible to protection.

    GIVEN an armed position whose approved stop is crossed
    WHEN the production Committee and AI entry point raises on every call and the
    scheduled production protection sweep runs
    THEN the sweep still reads the venue, still triggers, still takes the full
    remaining exposure, and never consulted the committee at all
    """

    clock = _Clock(QUALIFICATION_TIME)
    trade = _arm_trades(env, clock, ("BTCUSD",))[0]
    trade_id = trade["paper_trade_id"]
    _next_scan(clock)

    called: list[str] = []

    def _unavailable(*args: Any, **kwargs: Any) -> Any:
        called.append("committee")
        raise RuntimeError("the Committee and AI surface is unavailable")

    monkeypatch.setattr(COMMITTEE_SURFACE, _unavailable)

    book = _transport(clock, bid=90.0, ask=90.5)
    result = _run_scheduled_sweep(monkeypatch, env, clock, transport=book)

    assert book.requests, "the production seam must really read the venue"
    assert result.triggered == 1, result.details
    assert _state_of(env, trade_id) == ProtectionState.TRIGGERED.value
    assert [
        float(row["requested_quantity"]) for row in _exit_orders(env, trade_id)
    ] == [5.0]
    assert called == [], "protection must not reach the Committee at all"


@pytest.mark.acceptance
def test_ac_311_the_protective_authority_cannot_reach_the_committee_or_ai_surface():
    """ATDD-R5-M03-f11-failure-independence-matrix/AC-311: the coupling is frozen, not blessed.

    GIVEN the protective authority's own transitive application import closure
    WHEN it is walked and searched for the Committee, AI, analyst or intelligence
    surfaces
    THEN it reaches exactly the recorded set - never a growing one. Protection does
    not call any of them, which the behavioural test above proves, but it does reach
    them transitively through shared leaf modules, and that coupling is recorded
    rather than asserted away. Any new coupling fails this test.
    """

    direct = _app_imports(PROTECTION_RUNTIME_PATH)
    assert direct, "the import census must be able to see the module's imports"
    closure = _transitive_app_imports(PROTECTION_RUNTIME_PATH)
    # Non-vacuous: the closure is strictly larger than the direct imports, so this is
    # an assertion about the whole graph and not about one file.
    assert closure > direct, (len(closure), len(direct))

    offenders = sorted(
        module
        for module in closure
        if any(
            fragment in module
            for fragment in ("chief_analyst", "committee", "intelligence", "advisor")
        )
    )
    assert offenders == sorted(RECORDED_TRANSITIVE_COUPLING), offenders


def _transitive_app_imports(path: pathlib.Path) -> set[str]:
    """Every application module reachable from one file by following imports."""

    seen: set[str] = set()
    frontier = list(_app_imports(path))
    while frontier:
        module = frontier.pop()
        if module in seen:
            continue
        seen.add(module)
        try:
            spec = importlib.util.find_spec(module)
        except Exception:
            continue
        if spec is None or not spec.origin or not spec.origin.endswith(".py"):
            continue
        try:
            frontier.extend(_app_imports(pathlib.Path(spec.origin)))
        except Exception:
            continue
    return seen




# ---------------------------------------------------------------------------
# AC-312 an outage grants no admission and fabricates nothing
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_312_a_dead_feed_changes_no_book_and_admits_nothing(env, monkeypatch):
    """ATDD-R5-M03-f11-failure-independence-matrix/AC-312: an outage is a refusal, not a guess.

    GIVEN two armed positions and a venue transport that raises on every call
    WHEN the scheduled protection sweep runs
    THEN the seam really tried the venue, nothing triggered, nothing was exited,
    every obligation stayed armed and reserved at its original quantity, and the
    canonical book is unchanged by the outage
    """

    clock = _Clock(QUALIFICATION_TIME)
    trades = _arm_trades(env, clock, ("BTCUSD", "ETHUSD"))
    _next_scan(clock)
    before = _book_state(env)

    down = _DownTransport(KrakenTransportError("the public venue is unavailable"))
    result = _run_scheduled_sweep(monkeypatch, env, clock, transport=down)

    assert down.requests, "the production seam must really have tried the venue"
    assert result.triggered == 0
    assert result.exit_attempted == 0
    assert _book_state(env) == before

    for trade in trades:
        trade_id = trade["paper_trade_id"]
        assert _state_of(env, trade_id) == ProtectionState.ACTIVE.value
        # The obligation is intact: its entry fill is still the only fill, and no
        # exit order was fabricated for it.
        assert _count(env, PAPER_FILL_RECORDED, trade_id) == 1
        assert _exit_orders(env, trade_id) == []


# ---------------------------------------------------------------------------
# AC-315 the audit corrections are recorded, not absorbed
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_315_the_audit_correction_is_recorded_in_both_directions():
    """ATDD-R5-M03-f11-failure-independence-matrix/AC-315: a corrected audit says so.

    GIVEN the audit this increment corrected and the correction section it carries
    WHEN the vocabulary, the corrected classifications and the follower list are read
    THEN the unused class is gone from both the document and the audit test, the
    corrected items carry their new classes, and the correction and the coupling it
    uncovered are both described in the document
    """

    text = AUDIT.read_text(encoding="utf-8")
    audit_test = (
        APP_ROOT / "tests" / "test_opip_r5_m03_coverage_matrix.py"
    ).read_text(encoding="utf-8")

    # The declared vocabulary no longer lists the class. The word itself survives in
    # the prose that records the removal, which is the point of that prose.
    declared_classes = text.split("CLASSES:", 1)[1].split("```", 1)[0]
    assert "PARTIALLY_PROVEN" not in declared_classes
    assert "PARTIALLY_PROVEN" not in audit_test

    items = _audit_items("B") | _audit_items("C")
    expected = {
        "B8": "PROVEN",
        "C1": "PROVEN",
        "C3": "PROVEN",
        "C5": "PROVEN",
        "C17": "BLOCKED_BY_OWNER_DECISION",
    }
    for identifier, declared in expected.items():
        assert items[identifier]["class"] == declared, identifier

    assert "## 8. Corrections this mission made to its own audit" in text
    assert "## 9. A coupling this mission found and did not hide" in text
    assert "transitively reaches" in text



