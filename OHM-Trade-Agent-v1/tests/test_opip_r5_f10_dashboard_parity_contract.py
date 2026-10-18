"""F10 dashboard parity contract: the requirements, negative cases and RED state.

Increment: `ATDD-R5-F10-dashboard-parity-contract`. The authority for what each
assertion means is `docs/atdd/scope-contracts/ATDD-R5-F10-dashboard-parity-contract.md`
and the requirements it prepares are recorded in
`docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md`.

How to read these tests (RED, deliberately). This increment prepares a contract;
it does not implement dashboard parity and it changes no runtime behaviour. Every
target criterion is therefore asserted in its *unproven* direction: the eight
pages exist and are frozen, parity is `NOT_VERIFIED` for all of them, no
canonical projection authority is declared yet, no counts-and-P&L reconciliation
is recorded yet, and no numeric threshold is ratified. Each guard fails closed
if that gap is closed silently - a page cannot be flipped to verified without a
committed follower increment contract, a threshold cannot appear without an owner
decision, and the legacy outcomes cockpit cannot be removed before verified
parity. These tests are the acceptance criteria for the follower increment and
are superseded, not deleted, when it lands.

What these tests do not do: they never read the global `ACTIVE_INCREMENT`
pointer, they add no marker, no dependency and no runtime import, and they grant
no trading, admission, Telegram or deployment authority.
"""

from __future__ import annotations

import pathlib
import re

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-F10-dashboard-parity-contract"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"
LEDGER = APP_ROOT / "docs" / "architecture" / "OPIP_CONFORMANCE_LEDGER.md"
RETIREMENT_LEDGER = APP_ROOT / "docs" / "architecture" / "OPIP_RETIREMENT_LEDGER.md"
SCOPE_CONTRACTS = APP_ROOT / "docs" / "atdd" / "scope-contracts"

#: The adopted eight pages, in the adopted order. Frozen by the F10 requirement:
#: adding, removing, renaming, splitting or merging a page needs a new increment.
ADOPTED_PAGES: tuple[tuple[str, str], ...] = (
    ("P1_SYSTEM_PULSE", "System Pulse"),
    ("P2_DISCOVERY_REGIME", "Discovery and Regime"),
    ("P3_DECISION_TRACE", "Decision Trace"),
    ("P4_PAPER_PORTFOLIO", "Paper Portfolio"),
    ("P5_QUALITY_MISSED_OPPORTUNITY", "Quality and Missed Opportunity"),
    ("P6_COMMITTEE_VALUE_AI_COST", "Committee Value and AI Cost"),
    ("P7_LEARNING_RELEASES", "Learning and Releases"),
    ("P8_INCIDENTS_INFRASTRUCTURE", "Incidents and Infrastructure"),
)

NEGATIVE_CASES = tuple(f"NC-{index:02d}" for index in range(1, 11))

#: Owner decision recorded by this increment. Retention, not removal.
RETENTION_DECISION = (
    "OWNER_DECISION_RETENTION: The legacy outcomes cockpit is preserved until "
    "verified dashboard parity."
)

#: A ratified measurement would look like this; the policy section must not.
_MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ms|s|sec|secs|second|seconds|min|mins|minute|minutes|"
    r"hour|hours|day|days|week|weeks|%|bps|usd)\b",
    re.IGNORECASE,
)


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase check does not depend on line wrapping."""
    return " ".join(text.split())


def _page_blocks(text: str) -> list[dict[str, str]]:
    """Return the spec's `PAGE_ID` records as ordered field dictionaries."""
    pages: list[dict[str, str]] = []
    for chunk in text.split("```text")[1:]:
        fenced = chunk.split("```")[0]
        fields: dict[str, str] = {}
        for line in fenced.splitlines():
            key, separator, value = line.partition(":")
            if not separator:
                continue
            fields[key.strip()] = value.strip()
        if "PAGE_ID" in fields:
            pages.append(fields)
    return pages


def _section(text: str, heading: str) -> str:
    """Return the body of one `## <heading>` section, up to the next heading."""
    marker = f"## {heading}"
    start = text.index(marker)
    rest = text[start + len(marker) :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def _implementation_map(contract: str) -> tuple[str, ...]:
    """Return every path the contract authorizes, in declared order."""
    lines = contract.split("IMPLEMENTATION MAP:")[1].split("DEFERRED DISCOVERIES:")[0]
    paths: list[str] = []
    for line in lines.splitlines():
        if "->" not in line:
            continue
        paths.append(line.split("->", 1)[1].strip())
    return tuple(paths)


def _adopted_page_names_in_ledger(ledger: str) -> tuple[str, ...]:
    row = ledger[ledger.index("Eight pages:") + len("Eight pages:") :]
    row = row[: row.index("Shared semantic model")]
    return tuple(name.strip() for name in row.rstrip(". ").split(",") if name.strip())

@pytest.mark.acceptance
def test_ac_001_eight_page_inventory_is_frozen_and_complete():
    """ATDD-R5-F10-dashboard-parity-contract/AC-001: the inventory is the frozen eight.

    GIVEN the F10 dashboard request and the frozen inventory this increment prepares
    WHEN the page set is inspected
    THEN it holds exactly the eight adopted pages with stable identifiers, and
    adding, removing, renaming, splitting or merging a page requires a new
    authorized increment
    """
    spec = _read(SPEC)
    pages = _page_blocks(spec)
    assert [(page["PAGE_ID"], page["ADOPTED_NAME"]) for page in pages] == list(
        ADOPTED_PAGES
    )
    assert len({page["PAGE_ID"] for page in pages}) == len(ADOPTED_PAGES)
    inventory = _section(spec, "3. The eight pages (frozen inventory)")
    assert "frozen" in inventory.lower()
    assert "requires a new authorized increment" in inventory


@pytest.mark.acceptance
def test_ac_001_page_set_matches_the_adopted_conformance_ledger():
    """ATDD-R5-F10-dashboard-parity-contract/AC-001: the set is the adopted one.

    GIVEN the adopted conformance ledger's F10 row
    WHEN the eight pages are compared with the prepared inventory
    THEN the inventory names the same eight pages in the adopted wording, so no
    page is invented, dropped or renamed by this increment
    """
    ledger = _read(LEDGER)
    assert "Eight pages:" in ledger
    assert _adopted_page_names_in_ledger(ledger) == tuple(
        name for _page_id, name in ADOPTED_PAGES
    )


@pytest.mark.acceptance
def test_ac_002_parity_is_not_verified_for_every_page():
    """ATDD-R5-F10-dashboard-parity-contract/AC-002: parity is unproven today.

    GIVEN the eight prepared pages and the parity predicate
    WHEN the recorded parity state is inspected
    THEN every page is `NOT_VERIFIED`, the verdict records zero verified pages,
    and no page may be presented as parity
    """
    spec = _read(SPEC)
    pages = _page_blocks(spec)
    assert "Status: `CONTRACT_PREPARED_NOT_VERIFIED`" in spec
    assert "PARITY_VERDICT: NOT_VERIFIED (0 of 8 pages verified)" in spec
    assert [page["PARITY_STATUS"] for page in pages] == ["NOT_VERIFIED"] * len(
        ADOPTED_PAGES
    )


@pytest.mark.acceptance
def test_ac_002_every_page_declares_source_proof_and_follower():
    """ATDD-R5-F10-dashboard-parity-contract/AC-002: each page records the gap.

    GIVEN every prepared page
    WHEN its record is read
    THEN it carries the operator question, the required facts, the adopted
    extension, an explicit canonical-projection authority field that is declared
    undecided rather than guessed, an unrecorded counts-and-P&L reconciliation,
    the proof that would close it, and the follower increment that must close it
    """
    pages = _page_blocks(_read(SPEC))
    for page in pages:
        assert page["OPERATOR_QUESTION"].endswith("?"), page["PAGE_ID"]
        assert page["REQUIRED_FACTS"], page["PAGE_ID"]
        assert page["ADOPTED_EXTENSION"], page["PAGE_ID"]
        assert page["CANONICAL_PROJECTION_AUTHORITY"], page["PAGE_ID"]
        assert page["COUNTS_AND_PNL_RECONCILIATION"] == "NOT_RECORDED", page["PAGE_ID"]
        assert "reconciliation of counts and P&L" in page["PROOF_REQUIRED"], page[
            "PAGE_ID"
        ]
        assert page["FOLLOWER_INCREMENT"], page["PAGE_ID"]
    assert {page["CANONICAL_PROJECTION_AUTHORITY"] for page in pages} == {"UNDECIDED"}


@pytest.mark.acceptance
def test_ac_003_parity_cannot_flip_without_a_committed_follower_contract():
    """ATDD-R5-F10-dashboard-parity-contract/AC-003: the flip is gated, not editorial.

    GIVEN the prepared parity states and the follower increment
    WHEN a page is marked as anything other than `NOT_VERIFIED`
    THEN a committed follower increment contract must exist and must itself
    declare that page's parity criterion, so a silent green flip fails closed
    """
    spec = _read(SPEC)
    assert (
        "FOLLOWER_INCREMENT: ATDD-R5-F10-dashboard-parity-implementation "
        "(PROPOSED_NOT_AUTHORIZED)" in spec
    )
    for page in _page_blocks(spec):
        if page["PARITY_STATUS"] == "NOT_VERIFIED":
            continue
        follower = SCOPE_CONTRACTS / f"{page['FOLLOWER_INCREMENT']}.md"
        assert follower.is_file(), page["PAGE_ID"]
        contract = _read(follower)
        assert page["PAGE_ID"] in contract, page["PAGE_ID"]
        assert "PARITY" in contract, page["PAGE_ID"]



@pytest.mark.acceptance
def test_ac_004_missing_evidence_can_never_produce_a_favorable_page():
    """ATDD-R5-F10-dashboard-parity-contract/AC-004: missing evidence is never favorable.

    GIVEN the negative cases and the existing honesty laws this plane already
    enforces
    WHEN a fact is missing, unreadable, unevaluated or partial
    THEN none of it may be rendered as a favorable page state, and the surviving
    gaps are stated explicitly rather than implied to be fine
    """
    spec = _read(SPEC)
    for case in NEGATIVE_CASES:
        assert f"{case}:" in spec, case
    negative = _flat(_section(spec, "6. Negative cases (fail-closed)"))
    assert "unreadable projection is not" in negative
    assert "empty incident set" in negative
    assert "unevaluated trust dimension is not healthy" in negative
    assert "counts-and-P&L reconciliation" in negative
    parsed = _flat(_section(spec, "5. Parity predicate (exact)"))
    assert "never as `0`, `healthy`, `complete`" in parsed

    trust = _read(APP_ROOT / "app" / "opip" / "cockpit" / "trust.py")
    assert "UNKNOWN" in trust
    assert "never rendered as" in trust
    semantics = _read(
        APP_ROOT / "app" / "opip" / "profit_intelligence" / "semantics.py"
    )
    for vocabulary in ("KNOWN", "DERIVED", "UNAVAILABLE", "NOT_APPLICABLE"):
        assert vocabulary in semantics, vocabulary


@pytest.mark.acceptance
def test_ac_005_no_numeric_threshold_is_ratified():
    """ATDD-R5-F10-dashboard-parity-contract/AC-005: no threshold is invented.

    GIVEN the open owner inputs for gate thresholds and usefulness
    WHEN the prepared threshold policy is inspected
    THEN it ratifies no threshold and states the decision is the owner's, so no
    freshness, latency, tolerance, capacity, sample-size or cost bound is
    smuggled in as a measurement
    """
    policy = _section(_read(SPEC), "7. Threshold policy")
    assert "THRESHOLD_POLICY: OWNER_DECISION_REQUIRED" in policy
    assert "RATIFIED_THRESHOLDS: NONE" in policy
    measurement = _MEASUREMENT.search(policy)
    assert measurement is None, measurement.group(0) if measurement else ""


@pytest.mark.acceptance
def test_ac_006_legacy_outcomes_cockpit_retention_is_preserved():
    """ATDD-R5-F10-dashboard-parity-contract/AC-006: the legacy cockpit is kept.

    GIVEN the owner decision to preserve the legacy outcomes cockpit until
    verified dashboard parity
    WHEN the recorded decision, the mounted legacy surfaces and the retirement
    ledger's own cutover conditions are inspected
    THEN the decision is recorded, the legacy route and its analytics consumers
    are still present, its removal still needs verified parity and the census,
    and this increment authorizes no runtime, workflow or application path
    """
    spec = _read(SPEC)
    assert RETENTION_DECISION in spec

    main = _read(APP_ROOT / "app" / "main.py")
    assert "app.include_router(dashboard_router)" in main
    assert "B/C-4 Cockpit analytics router is deliberately NOT mounted here" in main

    dashboard = _read(APP_ROOT / "app" / "api" / "dashboard.py")
    assert '@router.get("/api/analytics/summary")' in dashboard
    assert '@router.get("/api/analytics/intelligence")' in dashboard
    assert '@router.get("/dashboard", response_class=HTMLResponse)' in dashboard

    ledger = _read(RETIREMENT_LEDGER)
    for condition in (
        "Route stays until cutover",
        "R5 pages serve the same questions",
        "Route removed after census",
    ):
        assert condition in ledger, condition

    for path in _implementation_map(_read(CONTRACT)):
        assert not path.startswith(
            (
                "OHM-Trade-Agent-v1/app/",
                "OHM-Trade-Agent-v1/.github/",
                "OHM-Trade-Agent-v1/deploy/",
            )
        ), path

