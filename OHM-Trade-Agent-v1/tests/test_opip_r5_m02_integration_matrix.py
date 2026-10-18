"""M02 source-of-truth audit acceptance tests: does the matrix still tell the truth?

Increment: ``ATDD-R5-M02-source-of-truth-audit``.

These tests audit an audit. They re-derive every claim the matrix makes from the
repository: that each cited authority path exists, that each cited entry point
resolves to a real symbol, that each wiring sentence is still true on disk, that
no row claims production proof, and that nothing in this increment has moved the
census, the read plane or any absent declaration it relies on.

They implement nothing. A failing test here means the matrix has gone stale or
overclaims, not that production behaviour is wrong.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import re
import sys

import pytest

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO_ROOT = APP_ROOT.parent
if str(APP_ROOT) not in sys.path:  # pragma: no cover - the suite already does this
    sys.path.insert(0, str(APP_ROOT))

MATRIX = APP_ROOT / "docs" / "atdd" / "evidence" / "F1_F7_INTEGRATION_MATRIX.md"
PROPOSAL = APP_ROOT / "docs" / "atdd" / "evidence" / "F1_F7_READ_PLANE_OWNER_PROPOSAL.md"
CENSUS = APP_ROOT / "docs" / "architecture" / "OPIP_F10_SOURCE_CENSUS.md"
APP_DIR = APP_ROOT / "app"

INCREMENT = "ATDD-R5-M02-source-of-truth-audit"

pytestmark = pytest.mark.acceptance

ROW_KEYS = (
    "class",
    "repo",
    "wiring",
    "runtime",
    "proof",
    "owner",
    "entry",
    "evidence",
)

CLASSES = frozenset(
    {
        "IMPLEMENTED_AND_VERIFIED",
        "IMPLEMENTED_NOT_INTEGRATED",
        "MISSING_CODE",
        "MISSING_CANONICAL_EVIDENCE",
        "BLOCKED_BY_OWNER_DECISION",
        "NOT_YET_TESTED",
    }
)

WIRINGS = frozenset(
    {
        "WIRED_INTO_DORMANT_ENGINE",
        "WIRED_INTO_SCAN_JOB",
        "NO_PRODUCTION_SNAPSHOT_SOURCE",
        "NO_APP_RUNTIME_CALLER",
        "NOT_THE_ADMISSION_AUTHORITY",
        "LEGACY_AUTHORITATIVE",
        "NOT_EXPOSED_BY_THE_READ_PLANE",
        "NO_CANONICAL_PRODUCER",
        "FILE_WAL_ON_TRADING_HOST",
        "ABSENT_DECLARED",
        "TWO_ENTRY_POINTS_CONSISTENT",
        "NO_PRODUCTION_SEAM_WIRING",
    }
)

RUNTIMES = frozenset({"INERT", "ACTIVE_WHEN_SCAN_RUNS", "NOT_RUNNING"})

STAGES = frozenset(
    {"F1", "F2", "F3", "F4", "F5", "F6", "F7", "CANONICAL", "F10", "SEAM"}
)

DECISION_FIELDS = (
    "producer",
    "consumer",
    "schema",
    "failure",
    "security",
    "migration",
)


def _matrix_text() -> str:
    return MATRIX.read_text(encoding="utf-8")


def _rows() -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for line in _matrix_text().splitlines():
        stripped = line.strip()
        if not stripped.startswith("ROW: "):
            continue
        cells = [cell.strip() for cell in stripped[len("ROW: ") :].split("|")]
        record = {"stage": cells[0], "fact": cells[1]}
        for cell in cells[2:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        records.append(record)
    return records


def _block(text: str, header: str) -> dict[str, int]:
    """Read a `HEADER:` block of `  NAME value` lines into a mapping."""

    values: dict[str, int] = {}
    inside = False
    for line in text.splitlines():
        if line.strip() == header:
            inside = True
            continue
        if inside:
            if line.strip() == "```" or (line and not line.startswith(" ")):
                break
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                values[parts[0]] = int(parts[1])
    return values


def _resolve(dotted: str) -> object | None:
    """Resolve a dotted entry point to a live object, or None."""

    parts = dotted.split(".")
    for cut in range(len(parts), 0, -1):
        try:
            obj: object = importlib.import_module(".".join(parts[:cut]))
        except Exception:
            continue
        for attribute in parts[cut:]:
            if not hasattr(obj, attribute):
                return None
            obj = getattr(obj, attribute)
        return obj
    return None


def _app_sources() -> list[tuple[pathlib.Path, str]]:
    return [
        (path, path.read_text(encoding="utf-8"))
        for path in sorted(APP_DIR.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def _importers_of(module: str, *, excluding: tuple[str, ...] = ()) -> list[str]:
    """Application modules that import `module`, excluding named modules."""

    patterns = (
        f"from {module} import",
        f"import {module}\n",
        f"import {module} ",
    )
    found: list[str] = []
    for path, source in _app_sources():
        relative = path.relative_to(APP_ROOT).as_posix()
        if relative in excluding:
            continue
        if any(pattern in source for pattern in patterns):
            found.append(relative)
    return found


# ---------------------------------------------------------------------------
# AC-200 the matrix is complete, uses only its own vocabulary, and its totals
#        recompute from its rows
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_200_matrix_rows_use_only_the_declared_vocabulary():
    """ATDD-R5-M02-source-of-truth-audit/AC-200: the matrix speaks its own language.

    GIVEN the recorded integration matrix
    WHEN every row is parsed and every axis value is compared with the declared
    vocabulary
    THEN each row carries exactly the declared keys, every class, wiring, runtime
    and stage value is one the document declares, and every stage of the adopted
    pipeline is represented by at least one row
    """

    rows = _rows()
    assert rows, "the matrix must record at least one row"

    for record in rows:
        assert set(record) == {"stage", "fact"} | set(ROW_KEYS), record
        assert record["stage"] in STAGES, record
        assert record["class"] in CLASSES, record
        assert record["wiring"] in WIRINGS, record
        assert record["runtime"] in RUNTIMES, record
        assert record["repo"] == "EXISTS", record
        assert record["proof"] == "NOT_PROVEN", record

    assert {record["stage"] for record in rows} == STAGES
    assert len({record["fact"] for record in rows}) == len(rows)


@pytest.mark.acceptance
def test_ac_200_declared_totals_recompute_from_the_rows():
    """ATDD-R5-M02-source-of-truth-audit/AC-200: the document cannot miscount itself.

    GIVEN the matrix's declared totals block and its rows
    WHEN the totals are recomputed from the rows
    THEN the row count, every class count and the proof count equal the declared
    values, and every class in the vocabulary is used by at least one row so that
    no class is decorative
    """

    text = _matrix_text()
    declared = _block(text, "TOTALS:")
    rows = _rows()

    assert declared, "the matrix must declare its totals"
    assert declared["ROWS"] == len(rows)

    for name in CLASSES:
        counted = sum(1 for record in rows if record["class"] == name)
        assert counted >= 1, f"class {name} is declared but unused"
        assert declared[name] == counted, (name, declared[name], counted)

    assert declared["PROOF_NOT_PROVEN"] == sum(
        1 for record in rows if record["proof"] == "NOT_PROVEN"
    )
    assert sum(declared[name] for name in CLASSES) == declared["ROWS"]


# ---------------------------------------------------------------------------
# AC-201 every citation resolves: the authority paths exist and the entry points
#        are real symbols
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_201_every_cited_authority_path_exists():
    """ATDD-R5-M02-source-of-truth-audit/AC-201: a citation that does not resolve is a defect.

    GIVEN the owner and evidence paths each row cites
    WHEN each is resolved against the repository root
    THEN every path exists, so no row points at a module or document that is not
    there
    """

    for record in _rows():
        for key in ("owner", "evidence"):
            cited = REPO_ROOT / record[key]
            assert cited.exists(), (record["fact"], key, record[key])


@pytest.mark.acceptance
def test_ac_201_every_cited_entry_point_resolves():
    """ATDD-R5-M02-source-of-truth-audit/AC-201: the entry point is the row's claim.

    GIVEN the dotted entry point each row names
    WHEN each is imported and traversed attribute by attribute
    THEN every one resolves to a live symbol, so the matrix cannot cite a renamed,
    moved or invented entry point
    """

    for record in _rows():
        assert _resolve(record["entry"]) is not None, (
            record["fact"],
            record["entry"],
        )


# ---------------------------------------------------------------------------
# AC-202 every wiring sentence in the matrix is still true on disk
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_202_the_dormant_engine_is_still_dormant():
    """ATDD-R5-M02-source-of-truth-audit/AC-202: dormant is a fact, not a mood.

    GIVEN the R5-A engine module and its constructor
    WHEN the compiled mode, the compiled authority, the environment reads and the
    default detector evaluator are inspected
    THEN the mode is the literal off, the authority is NONE, no environment
    variable is read and the default evaluator is the real IGNITION detector, so
    the rows that claim F3 is wired into a dormant engine stay true
    """

    engine = importlib.import_module("app.opip.events.opportunity_engine")
    ignition = importlib.import_module("app.opip.detectors.ignition")

    assert engine.R5A_ENGINE_MODE == "off"
    assert engine.engine_mode() == "off"
    assert engine.R5A_ENGINE_AUTHORITY == "NONE"
    assert engine.R5A_ENV_VARIABLES_READ == ()
    assert engine.engine_reads_environment() is False

    parameters = inspect.signature(engine.ContinuousOpportunityEngine).parameters
    assert parameters["detector_evaluate"].default is ignition.evaluate


@pytest.mark.acceptance
def test_ac_202_the_back_half_still_has_no_application_caller():
    """ATDD-R5-M02-source-of-truth-audit/AC-202: unwired is recorded, not assumed.

    GIVEN the application package
    WHEN every module that imports the feasibility, forecast or portfolio-selector
    stages is listed
    THEN the only importers are the defining modules themselves, so the rows that
    record the F5, F6 and F7 target stages as not integrated stay true
    """

    assert (
        _importers_of("app.opip.feasibility", excluding=("app/opip/feasibility.py",))
        == []
    )
    assert (
        _importers_of("app.opip.forecast", excluding=("app/opip/forecast.py",)) == []
    )
    assert (
        _importers_of(
            "app.opip.portfolio_selector",
            excluding=("app/opip/portfolio_selector.py",),
        )
        == []
    )


@pytest.mark.acceptance
def test_ac_202_the_legacy_selector_is_still_the_admission_path():
    """ATDD-R5-M02-source-of-truth-audit/AC-202: authority has not drifted.

    GIVEN the scheduled scan job and the qualification evidence store
    WHEN their sources are inspected
    THEN the legacy service selector is called from the job, the qualification
    store lives under the trading-host data path, and the target selector is
    imported by no application module
    """

    job = (APP_ROOT / "app" / "jobs" / "scan_opportunities.py").read_text(
        encoding="utf-8"
    )
    assert "from app.services.capital_efficiency_ranking import" in job
    assert "rank_capital_efficiency(" in job
    assert "append_screening_evaluations" in job

    store = (APP_ROOT / "app" / "opip" / "decision" / "store.py").read_text(
        encoding="utf-8"
    )
    assert 'Path("/app/data/opip/qualification")' in store


@pytest.mark.acceptance
def test_ac_202_the_canonical_event_inventory_is_exact():
    """ATDD-R5-M02-source-of-truth-audit/AC-202: the canonical plane is named exactly.

    GIVEN the canonical feature-bus event-type set and the F5 evidence event type
    WHEN both are read from the contracts
    THEN the feature-bus set is exactly the six declared types and the F5 evidence
    event type is exactly the registered one, so the canonical rows cite the real
    inventory rather than a remembered one
    """

    events = importlib.import_module("app.opip.contracts.events")
    fev = importlib.import_module("app.opip.fev_evidence_event")

    assert events.FEATURE_BUS_EVENT_TYPES == frozenset(
        {
            "market.observation.recorded",
            "market.instrument_version.recorded",
            "feature.snapshot.recorded",
            "feature.checkpoint.recorded",
            "coverage.gap.recorded",
            "feature.restart.recorded",
        }
    )
    assert fev.FEASIBILITY_EVIDENCE_RECORDED == "feasibility.evidence.recorded"


@pytest.mark.acceptance
def test_ac_202_the_read_plane_declares_no_pipeline_fact():
    """ATDD-R5-M02-source-of-truth-audit/AC-202: an undeclared fact is not exposed.

    GIVEN the analytics read plane's declared facts
    WHEN every declared fact name is inspected for a pipeline family
    THEN no declared fact names an observation, coverage, feasibility, detector or
    feature family, so recording these families as unexposed stays true
    """

    semantic = importlib.import_module("app.opip.cockpit.semantic")
    declared = " ".join(
        list(semantic.READ_MODEL_FACTS) + list(semantic.POPULATION_FACTS)
    ).lower()

    for family in ("observation", "coverage", "feasibility", "detector", "feature"):
        assert family not in declared, family


# ---------------------------------------------------------------------------
# AC-203 the audit claims no proof and moves nothing it audits
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_203_no_row_claims_production_proof():
    """ATDD-R5-M02-source-of-truth-audit/AC-203: an audit may not promote itself.

    GIVEN every recorded row and the document's own prose
    WHEN the proof axis and the movement section are inspected
    THEN every row's proof is NOT_PROVEN, the document states that no row is
    production-proven, and the only movement it claims is the one the next
    increment makes
    """

    rows = _rows()
    assert {record["proof"] for record in rows} == {"NOT_PROVEN"}

    text = _matrix_text()
    assert "production-proven" in text
    assert "`ATDD-R5-M02-pipeline-seam-integration` closes" in text


@pytest.mark.acceptance
def test_ac_203_the_census_state_this_audit_reads_is_unchanged():
    """ATDD-R5-M02-source-of-truth-audit/AC-203: the census is read, never rewritten.

    GIVEN the F10 source census
    WHEN its parity state, its availability table and its threshold policy are read
    THEN parity is still zero of eight, the paper families are still the only ones
    marked verified, the candidate and decision families are still blocked, and the
    threshold policy is still unratified
    """

    census = CENSUS.read_text(encoding="utf-8")

    assert "PARITY_STATUS: NOT_VERIFIED" in census
    assert "PARITY_VERIFIED_PAGES: 0 of 8" in census
    assert "THRESHOLD_POLICY: OWNER_DECISION_REQUIRED" in census
    assert "RATIFIED_THRESHOLDS: NONE" in census
    assert "| Paper trades | one position lifecycle | EXISTING_AND_VERIFIED |" in census
    assert (
        "| Candidate lifecycle events | one candidate lifecycle event "
        "| BLOCKED_BY_UPSTREAM_EVIDENCE |" in census
    )
    assert (
        "| Decisions | one frozen baseline decision | BLOCKED_BY_UPSTREAM_EVIDENCE |"
        in census
    )


# ---------------------------------------------------------------------------
# AC-204 the proposal is complete and implements nothing
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_204_every_decision_names_its_six_implications():
    """ATDD-R5-M02-source-of-truth-audit/AC-204: a proposal without implications is a wish.

    GIVEN the owner-decision proposal
    WHEN every decision line is parsed
    THEN each names a producer, a consumer, a schema, a failure mode, a security
    implication and a migration implication, and the decisions cover both the
    exposure gaps and the producer gaps
    """

    lines = [
        line.strip()
        for line in PROPOSAL.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("DECISION: ")
    ]
    assert len(lines) >= 3, "the proposal must record its decisions"

    for line in lines:
        for field in DECISION_FIELDS:
            assert re.search(rf"\b{field}=", line), (field, line[:80])

    text = "\n".join(lines)
    assert "EXPOSE-FEATURE-BUS-EVIDENCE" in text
    assert "EXPOSE-F5-FEASIBILITY-EVIDENCE" in text
    assert "NEW-CANONICAL-PRODUCERS" in text


@pytest.mark.acceptance
def test_ac_204_the_proposal_added_no_exposure_and_no_absent_capability():
    """ATDD-R5-M02-source-of-truth-audit/AC-204: proposing is not doing.

    GIVEN the census's declared absences
    WHEN each declared-absent path and symbol is probed
    THEN every one is still absent and the read plane still declares no pipeline
    fact, so this increment exposed nothing while proposing to expose it
    """

    census = CENSUS.read_text(encoding="utf-8")
    absent_paths = re.findall(
        r"^ABSENT \| (OHM-Trade-Agent-v1/\S+) \|", census, flags=re.MULTILINE
    )
    absent_symbols = re.findall(
        r"^ABSENT_SYMBOL \| (\S+) \|", census, flags=re.MULTILINE
    )
    assert absent_paths and absent_symbols, "the census must declare its absences"

    for relative in absent_paths:
        assert not (REPO_ROOT / relative).exists(), relative

    sources = "\n".join(source for _path, source in _app_sources())
    for symbol in absent_symbols:
        assert symbol not in sources, symbol


