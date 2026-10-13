"""F10-0 source census: the F10-0 deliverable's own structural acceptance tests.

Increment: `ATDD-R5-F10-source-census`. The authority for what each assertion
means is `docs/atdd/scope-contracts/ATDD-R5-F10-source-census.md`, and the
document under test is `docs/architecture/OPIP_F10_SOURCE_CENSUS.md`.

How to read these tests. This increment implements no product behaviour. It
records a verified census, so the tests assert the census against the repository
rather than against itself: every existing surface it names must exist, every
absence it declares must be absent, every metric it names must be a registered
metric, every gap it declares must be a registered gap, and every page it lists
must be one of the frozen eight. That makes the census fail closed if it is edited
to describe a repository that is not there, or to claim a gap is closed, a
threshold is ratified, or a page is verified.

What these tests do not do: they never read the global `ACTIVE_INCREMENT`
pointer, they add no marker, dependency or runtime import beyond the two contract
modules they verify against, and they grant no trading, admission, Telegram or
deployment authority.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.opip.contracts.paper_metrics import (
    PAPER_METRIC_REGISTRY_VERSION,
    PAPER_METRICS,
)
from app.opip.profit_intelligence.semantics import EVIDENCE_GAPS

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-F10-source-census"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
CONTRACT = APP_ROOT / "docs" / "atdd" / "scope-contracts" / f"{INCREMENT}.md"
PARITY_SPEC = APP_ROOT / "docs" / "architecture" / "OPIP_F10_DASHBOARD_PARITY_SPEC.md"
RETIREMENT_LEDGER = APP_ROOT / "docs" / "architecture" / "OPIP_RETIREMENT_LEDGER.md"

#: The frozen eight-page inventory. Adding, removing, renaming, splitting or
#: merging a page needs its own increment, so it is pinned here as well as in the
#: parity specification.
PAGE_IDS: tuple[str, ...] = (
    "P1_SYSTEM_PULSE",
    "P2_DISCOVERY_REGIME",
    "P3_DECISION_TRACE",
    "P4_PAPER_PORTFOLIO",
    "P5_QUALITY_MISSED_OPPORTUNITY",
    "P6_COMMITTEE_VALUE_AI_COST",
    "P7_LEARNING_RELEASES",
    "P8_INCIDENTS_INFRASTRUCTURE",
)

STATUSES: tuple[str, ...] = (
    "EXISTING_AND_VERIFIED",
    "EXISTING_NEEDS_EXTENSION",
    "MISSING",
    "BLOCKED_BY_UPSTREAM_EVIDENCE",
    "OWNER_DECISION_REQUIRED",
)

MATRIX_COLUMNS = (
    "REQUIREMENT_ID",
    "ARCHITECTURE_CLAUSE",
    "CURRENT_IMPLEMENTATION",
    "MISSING_CAPABILITY",
    "CANONICAL_PRODUCER",
    "REQUIRED_READ_MODEL",
    "API",
    "UI",
    "ATDD",
    "EVIDENCE",
    "STATUS",
)

#: Matrix columns whose content is a claim about something that already exists.
#: A path named in one of these columns must resolve in this repository. Columns
#: describing the planned follower work are deliberately exempt.
EXISTING_CLAIM_COLUMNS = ("CURRENT_IMPLEMENTATION", "CANONICAL_PRODUCER", "EVIDENCE")

_PATH_TOKEN = re.compile(
    r"(?:OHM-Trade-Agent-v1/)?(?:app|tests|docs|deploy)/[A-Za-z0-9_./-]+"
    r"\.(?:py|md|json|html|sh)"
)
_METRIC_TOKEN = re.compile(r"\bpaper\.[a-z_]+")
_GAP_LINE = re.compile(r"^GAP:\s*([A-Z_]+)\s*\|")
_ABSENT_LINE = re.compile(r"^ABSENT\s*\|\s*(\S+)\s*\|")
_ABSENT_SYMBOL_LINE = re.compile(r"^ABSENT_SYMBOL\s*\|\s*(\S+)\s*\|")
_FOLLOWER_LINE = re.compile(r"^FOLLOWER:\s*(\S+)\s*\|")
_SOURCE_LINE = re.compile(r"^(OHM-Trade-Agent-v1/\S+)\s*\|")
_MATRIX_ROW = re.compile(r"^\|\s*(F10-[A-Z0-9]+-\d+)\s*\|")
_PANEL_ROW = re.compile(r"^\|\s*(P[1-8]-PNL-\d+)\s*\|")
_PAGE_ROW = re.compile(r"^\|\s*(P[1-8]_[A-Z0-9_]+)\s*\|")
_PLANNED = re.compile(r"^PLANNED:(\S+)/(AC-\d{3})$")


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Collapse whitespace so a phrase check does not depend on line wrapping."""
    return " ".join(text.split())


def _cells(line: str) -> list[str]:
    """Split a markdown table row into its cell values."""
    stripped = line.strip()
    assert stripped.startswith("|") and stripped.endswith("|"), line
    return [cell.strip() for cell in stripped[1:-1].split("|")]


def _census_lines(prefix_re: re.Pattern[str]) -> list[str]:
    return [
        line.strip()
        for line in _read(CENSUS).splitlines()
        if prefix_re.match(line.strip())
    ]


def _resolve(token: str) -> pathlib.Path:
    """Resolve a repository-root or application-root relative path token."""
    if token.startswith("OHM-Trade-Agent-v1/"):
        return REPO_ROOT / token
    return APP_ROOT / token


@pytest.mark.acceptance
def test_ac_001_census_declares_the_frozen_inventory_and_contract():
    """ATDD-R5-F10-source-census/AC-001: identity, vocabulary, pages, followers.

    GIVEN the F10-0 census document
    WHEN its identity, classification vocabulary, frozen page inventory and
    follower increments are inspected
    THEN it declares the increment, records the five permitted status values and
    no sixth, lists exactly the eight frozen pages in the adopted order, and
    names the follower increments without authorizing any of them
    """
    assert CENSUS.is_file(), CENSUS
    census = _read(CENSUS)
    assert "Increment: `ATDD-R5-F10-source-census`" in census
    assert "Authority granted by this document: `NONE`" in census
    assert "Status: `CENSUS_RECORDED_NOT_IMPLEMENTED`" in census

    # The vocabulary is closed: every declared value is present and every matrix
    # row uses one of them.
    flat = _flat(census)
    for status in STATUSES:
        assert status in flat, status

    rows = [
        _cells(line)
        for line in census.splitlines()
        if _MATRIX_ROW.match(line.strip())
    ]
    assert len(rows) >= 40, len(rows)
    bad = [row for row in rows if len(row) != len(MATRIX_COLUMNS)]
    assert not bad, bad
    requirement_ids = [row[0] for row in rows]
    assert len(requirement_ids) == len(set(requirement_ids)), requirement_ids
    for row in rows:
        assert row[-1] in STATUSES, row

    # The page inventory is exactly the frozen eight, in the adopted order.
    page_rows = [
        _cells(line) for line in census.splitlines() if _PAGE_ROW.match(line.strip())
    ]
    assert [row[0] for row in page_rows] == list(PAGE_IDS), page_rows
    for row in page_rows:
        assert row[-1] in STATUSES, row
    parity_spec = _read(PARITY_SPEC)
    for page_id in PAGE_IDS:
        assert page_id in parity_spec, page_id

    # Every panel is attributed to a frozen page and to a planned criterion.
    panel_rows = [
        _cells(line) for line in census.splitlines() if _PANEL_ROW.match(line.strip())
    ]
    assert len(panel_rows) >= 24, len(panel_rows)
    panel_ids = [row[0] for row in panel_rows]
    assert len(panel_ids) == len(set(panel_ids)), panel_ids
    followers = {
        match.group(1)
        for match in map(_FOLLOWER_LINE.match, _census_lines(_FOLLOWER_LINE))
    }
    assert followers, "census declares no follower increment"
    for row in panel_rows:
        assert len(row) == 8, row
        planned = _PLANNED.fullmatch(row[-1])
        assert planned is not None, row
        assert planned.group(1) in followers, row

    # The contract exists and declares the same increment.
    contract = _read(CONTRACT)
    assert contract.splitlines()[1].strip() == INCREMENT
    assert "UNAPPROVED SCOPE CHANGES:\nNONE" in contract


@pytest.mark.acceptance
def test_ac_002_named_surfaces_exist_and_declared_absences_are_absent():
    """ATDD-R5-F10-source-census/AC-002: named surfaces and absences are real.

    GIVEN the census's source list, its per-row claims about existing surfaces and
    its declared absences
    WHEN every named existing surface is resolved and every declared absence is
    probed
    THEN each named surface exists, each declared absence is genuinely absent, and
    each matrix row that names an existing implementation or evidence path has all
    eleven required columns and a status drawn from the permitted vocabulary
    """
    census = _read(CENSUS)

    sources = _census_lines(_SOURCE_LINE)
    assert len(sources) >= 30, len(sources)
    for line in sources:
        match = _SOURCE_LINE.match(line)
        assert match is not None, line
        path = _resolve(match.group(1))
        assert path.is_file(), path

    absences = _census_lines(_ABSENT_LINE)
    assert len(absences) >= 4, absences
    for line in absences:
        match = _ABSENT_LINE.match(line)
        assert match is not None, line
        assert not _resolve(match.group(1)).exists(), match.group(1)

    application = sorted((APP_ROOT / "app").rglob("*.py"))
    assert application, "no application sources found"
    symbols = _census_lines(_ABSENT_SYMBOL_LINE)
    assert len(symbols) >= 2, symbols
    for line in symbols:
        match = _ABSENT_SYMBOL_LINE.match(line)
        assert match is not None, line
        symbol = match.group(1)
        offenders = [
            path.name
            for path in application
            if symbol in path.read_text(encoding="utf-8")
        ]
        assert not offenders, f"{symbol} appears in {offenders}"

    # Every claim about something that already exists must resolve.
    rows = [
        _cells(line)
        for line in census.splitlines()
        if _MATRIX_ROW.match(line.strip())
    ]
    for row in rows:
        for column in EXISTING_CLAIM_COLUMNS:
            value = row[MATRIX_COLUMNS.index(column)]
            for token in _PATH_TOKEN.findall(value):
                assert _resolve(token).exists(), (row[0], column, token)


@pytest.mark.acceptance
def test_ac_003_metric_ids_come_from_the_versioned_registry():
    """ATDD-R5-F10-source-census/AC-003: one metric authority, no restatement.

    GIVEN the metric identifiers the census names
    WHEN they are resolved against the versioned metric registry
    THEN every identifier exists in the registry, the census names that registry
    as the only metric-semantics authority and declares that no formula is
    restated outside it
    """
    census = _read(CENSUS)
    assert PAPER_METRIC_REGISTRY_VERSION == "paper-metrics-v1"

    named = sorted(set(_METRIC_TOKEN.findall(census)))
    assert len(named) >= 8, named
    missing = [metric_id for metric_id in named if metric_id not in PAPER_METRICS]
    assert not missing, missing

    assert (
        "versioned metric registry (paper-metrics-v1) - the only metric-semantics "
        "authority" in _flat(census)
    )
    assert "the formula is never restated in a read model" in _flat(census)

    # Exactly one module in the application defines a versioned metric registry.
    offenders = sorted(
        path.name
        for path in (APP_ROOT / "app").rglob("*.py")
        if "METRIC_REGISTRY_VERSION" in path.read_text(encoding="utf-8")
        and path.name != "paper_metrics.py"
    )
    assert not offenders, offenders


@pytest.mark.acceptance
def test_ac_004_declared_gaps_are_registered_gaps():
    """ATDD-R5-F10-source-census/AC-004: gaps are registered and never favorable.

    GIVEN the evidence gaps the census declares
    WHEN each gap is compared with the registered gap facts
    THEN every declared gap is a registered gap, and the census states that
    missing evidence is never rendered as a favorable state
    """
    census = _read(CENSUS)
    registered = {gap.fact for gap in EVIDENCE_GAPS}
    assert registered, "no registered evidence gaps"

    declared = {
        match.group(1)
        for match in map(_GAP_LINE.match, _census_lines(_GAP_LINE))
    }
    assert declared, "census declares no evidence gap"
    unregistered = sorted(declared - registered)
    assert not unregistered, unregistered
    assert declared == registered, sorted(registered - declared)

    # A declared gap must be registered as genuinely unavailable, so a census that
    # pretended one of them was observable evidence would fail here.
    for gap in EVIDENCE_GAPS:
        assert gap.availability.value == "UNAVAILABLE", gap.fact
        assert gap.producer.strip(), gap.fact

    flat = _flat(census)
    assert "No evidence is not a favorable result" in flat
    assert "UNKNOWN" in flat and "is not zero" in flat
    assert "must render an explicit unavailable or insufficient-evidence state" in flat


@pytest.mark.acceptance
def test_ac_005_no_threshold_is_ratified_and_parity_is_preserved():
    """ATDD-R5-F10-source-census/AC-005: no ratified threshold, parity untouched.

    GIVEN the threshold policy and parity state in the census
    WHEN both are inspected
    THEN the threshold policy requires an owner decision, no threshold is
    ratified, and every page's parity verdict remains NOT_VERIFIED
    """
    census = _read(CENSUS)
    assert "THRESHOLD_POLICY: OWNER_DECISION_REQUIRED" in census
    assert "RATIFIED_THRESHOLDS: NONE" in census
    assert "PARITY_STATUS: NOT_VERIFIED" in census
    assert "PARITY_VERIFIED_PAGES: 0 of 8" in census
    assert "Parity verdict: `NOT_VERIFIED` (0 of 8 pages verified)" in census
    assert "PARITY_STATUS: VERIFIED" not in census

    # The parity specification this census must not disturb still says the same.
    parity_spec = _read(PARITY_SPEC)
    assert "Parity verdict: `NOT_VERIFIED` (0 of 8 pages verified)" in parity_spec
    assert "PARITY_STATUS: NOT_VERIFIED" in parity_spec
    assert "PARITY_STATUS: VERIFIED" not in parity_spec

    # Retention of the legacy cockpit remains recorded and unweakened.
    assert "OWNER_DECISION_RETENTION" in parity_spec
    assert "Route stays until cutover" in _read(RETIREMENT_LEDGER)


@pytest.mark.acceptance
def test_ac_006_no_application_or_deployment_path_is_touched():
    """ATDD-R5-F10-source-census/AC-006: docs-only surface, no authority claimed.

    GIVEN this increment's implementation map and the census's authority
    statements
    WHEN the mapped paths are inspected
    THEN the increment changes no application, workflow or deployment path, and
    the census claims no trading, Telegram, admission, promotion, deployment or
    release authority
    """
    contract = _read(CONTRACT)
    section = contract.split("IMPLEMENTATION MAP:")[1].split("DEFERRED DISCOVERIES:")[0]
    mapped = sorted(
        line.split("->", 1)[1].strip()
        for line in section.splitlines()
        if "->" in line
    )
    assert mapped, "implementation map is empty"
    for path in mapped:
        assert not path.startswith(
            (
                "OHM-Trade-Agent-v1/app/",
                "OHM-Trade-Agent-v1/.github/",
                "OHM-Trade-Agent-v1/deploy/",
            )
        ), path
        assert path.startswith(
            (
                "OHM-Trade-Agent-v1/docs/",
                "OHM-Trade-Agent-v1/tests/test_opip_r5_f10_source_census.py",
            )
        ), path

    census = _flat(_read(CENSUS))
    assert "Authority granted by this document: `NONE`" in census
    assert "RUNTIME_ACTIVE: FALSE" in census
    assert "grants no trading, Telegram, admission, promotion, deployment or" in census
    assert "no trading, deployment, promotion or release authority" in census
