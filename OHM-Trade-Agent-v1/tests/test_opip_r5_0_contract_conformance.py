"""R5-0 conformance guards for the requirements register and the contract freeze.

This module proves the artifacts of the `ATDD-R5-0-contract-conformance`
increment, not new production behaviour: the requirements register, the
structural-contract freeze and the increment's own scope contract. It asserts
that every adopted requirement has exactly one conformance row in the declared
field order, that every clause citation resolves to a pinned source, that
inherited `PA-*`/`SQ-*` titles are carried verbatim, that the frozen record keys
occur in the clauses each contract adopts, that dependencies are declared,
resolvable and acyclic, that no numeric policy is defaulted, that the increment
maps no runtime, deployment or Compose path, and that the release-profile
safeguards hold (`EVIDENCE_SHADOW` active, `TARGET_PAPER` blocked, Compose
`OPIP_PAPER_V2_MODE=off`).

The tests are read-only. They parse committed documents and the existing
release-profile contract; they never write runtime state, never call an
exchange, and never read the movable `docs/atdd/ACTIVE_INCREMENT` pointer. This
increment proves its identity from its own scope contract instead.

Where a frozen claim is a statement about a pinned source, the test resolves the
claim against that source (the v1.5.0 section and clause surface, the v1.4.4
appendix clause registers) rather than only matching prose, so the freeze cannot
drift away from the clauses it cites.
"""

from __future__ import annotations

import hashlib
import re
import sys
from functools import lru_cache
from pathlib import Path

import pytest
import yaml

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.services.release_profiles import (  # noqa: E402
    evaluate_architecture_gate,
    get_release_profiles,
    render_profile_environment,
    render_release_verdict,
    resolve_release_profile,
    validate_profile_contract,
)
from tests.atdd_scope import (  # noqa: E402
    REQUIRED_SECTIONS,
    behavior_label_problems,
    check_scope,
    load_acceptance_index,
    normalize_repo_path,
    parse_scope_contract,
)

pytestmark = pytest.mark.acceptance

REPO_ROOT = APP_ROOT.parent
DOCS = APP_ROOT / "docs" / "architecture"
ATDD_DOCS = APP_ROOT / "docs" / "atdd"
CONTRACT = ATDD_DOCS / "scope-contracts" / "ATDD-R5-0-contract-conformance.md"
REGISTER = DOCS / "OPIP_R5_REQUIREMENTS_REGISTER.md"
FREEZE = DOCS / "OPIP_R5_0_CONTRACT_FREEZE.md"
V150_ARCH = DOCS / "v1.5.0" / "ARCHITECTURE.md"
V144_ARCH = DOCS / "v1.4.4" / "ARCHITECTURE.md"
COMPOSE = APP_ROOT / "docker-compose.yml"
CORE_SERVICE = "ohm-trade-agent"

INCREMENT = "ATDD-R5-0-contract-conformance"
ADOPTED_SHA256 = "69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2"
INHERITED_SHA256 = "9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768"

AC_IDS = tuple(f"AC-{index:03d}" for index in range(1, 13))
FEATURE_IDS = tuple(f"F{index}" for index in range(1, 13))
INHERITED_IDS = tuple(f"PA-{index:02d}" for index in range(1, 21)) + tuple(
    f"SQ-{index:02d}" for index in range(1, 11)
)
FEATURE_INHERITED_IDS = FEATURE_IDS + INHERITED_IDS
DELIVERY_IDS = ("R5-0", "R5-A", "R5-B", "R5-C", "R5-D", "R5-E", "R5-F", "R6", "R7")
CONTRACT_IDS = tuple(f"SC-{index:02d}" for index in range(1, 14))
GATE_IDS = tuple(f"G{index}" for index in range(1, 7))
OPEN_INPUT_IDS = tuple(f"OQ-{index:02d}" for index in range(1, 11))
#: The register's declared row order.
ROW_ORDER = FEATURE_INHERITED_IDS + DELIVERY_IDS + GATE_IDS + OPEN_INPUT_IDS

REGISTER_FIELDS = (
    "ID",
    "Requirement",
    "Source",
    "Clause",
    "Status",
    "Evidence",
    "Authority",
    "Depends on",
    "Contract",
    "Open input",
    "Owner",
    "Gate",
    "Note",
)
STATUS_VOCAB = (
    "IMPLEMENTED_BASELINE",
    "PARTIAL",
    "NOT_IMPLEMENTED",
    "PROPOSED_NOT_FROZEN",
    "OWNER_DECISION_REQUIRED",
    "RETIRED",
    "OUT_OF_SCOPE",
    "IMPLEMENTED_VERIFIED",
)
AUTHORITY_VOCAB = ("NONE", "SHADOW", "PAPER_RESEARCH", "TARGET_PAPER_GATED", "LIVE")
SOURCE_VOCAB = ("v1.5.0", "v1.4.4", "v1.5.0+v1.4.4")
REUSE_VOCAB = ("REUSE_EXISTING_MODULE", "EXTEND_EXISTING_MODULE", "NO_MODULE_CHANGE")
OWNER_VOCAB = ("UNASSIGNED", "OWNER")
OPEN_STATUS = "OWNER_DECISION_REQUIRED"

#: The structural contracts that own a logical record, and the record they own.
RECORD_OWNERS = (
    ("SC-02", "AttentionTrigger", "OHM-Trade-Agent-v1/app/opip/events/contract.py"),
    ("SC-04", "OpportunityThesis", "OHM-Trade-Agent-v1/app/opip/contracts/temporal.py"),
    ("SC-06", "CapitalSnapshot", "OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py"),
    ("SC-08", "PortfolioPlan", "OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py"),
    ("SC-09", "RotationGroup", "OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py"),
    ("SC-10", "Expectation and Outcome", "OHM-Trade-Agent-v1/app/opip/learning/evaluation.py"),
    ("SC-12", "CanonicalTemporalRecord", "OHM-Trade-Agent-v1/app/opip/canonical/writer.py"),
)
RECORD_CONTRACTS = tuple(contract for contract, _, _ in RECORD_OWNERS)
#: Contracts whose structural surface is owner-gated, so the freeze is `OPEN`.
OPEN_CONTRACTS = ("SC-03", "SC-13")

CONTRACT_LABELS = (
    "Register rows",
    "Adopted clauses",
    "Freeze status",
    "Open owner input",
    "Frozen record",
    "Record keys",
    "Owning module",
    "Reuse status",
)
CORE_LABELS = CONTRACT_LABELS[:4]
RECORD_LABELS = CONTRACT_LABELS[4:]
FREEZE_STATUS_VOCAB = ("FROZEN", "OPEN")

#: The frozen boundary the adopted clauses restate, quoted so a widening fails.
FROZEN_BOUNDARY_STATEMENTS = (
    "no new event store",
    "no second outcome engine",
    "no second position registry",
    "no second scheduler",
    "no second deployment control plane",
)
#: The authority statements that every R5-0 artifact must keep at zero.
ZERO_AUTHORITY_STATEMENTS = (
    "PRODUCTION TRADE AUTHORITY GRANTED BY THIS FREEZE = NONE",
    "FUNDED OR EXCHANGE ORDER AUTHORITY GRANTED BY THIS FREEZE = NONE",
    "DEPLOYMENT OR RELEASE AUTHORITY GRANTED BY THIS FREEZE = NONE",
    "NUMERIC POLICY RATIFIED BY THIS FREEZE = NONE",
)
#: The register's own zero-authority statements, which AC-005 keeps true.
REGISTER_AUTHORITY_STATEMENTS = (
    "Authority granted by this document: `NONE`",
    "This register records no `LIVE` row",
    "conveys no trading, deployment or release authority",
)
#: The freeze's authority statements, which AC-010 keeps true.
FREEZE_AUTHORITY_STATEMENTS = (
    "No row of this freeze carries `LIVE` authority",
    "zero trading authority",
    "zero deployment authority",
    "zero release authority",
)
#: Register integrity rules the register declares about itself.
REGISTER_INTEGRITY_STATEMENTS = (
    "exactly thirteen fields per row",
    "no `LIVE` authority row",
    "every evidence path exists in the repository",
)
#: Freeze statements that keep numeric policy with the OWNER (AC-009).
NUMERIC_POLICY_STATEMENTS = (
    "no threshold, duration, budget, cap or calibration value is chosen",
    "NUMERIC POLICY RATIFIED BY THIS FREEZE = NONE",
    "Every open input below is an `OWNER_DECISION_REQUIRED` row",
)
#: The rows allowed to declare gated instead of zero authority.
GATED_AUTHORITY_IDS = ("PA-20", "G6")
#: The adopted delivery order of v1.5.0 section 14.
DELIVERY_ORDER = ("R5-0", "R5-A", "R5-B", "R5-C", "R5-D", "R5-E", "R5-F")
#: Freeze tables whose shape and rows are part of the frozen surface.
FREEZE_OWNER_HEADER = ("Logical record", "Owning module", "Reuse status", "Governing contract")
FREEZE_CONTRACT_HEADER = ("Contract", "Register rows")
FREEZE_OPEN_INPUT_HEADER = ("Open input", "Gate", "Structural contracts", "Subject")
#: The modes `EVIDENCE_SHADOW` must keep shadow, and the ones it must keep off.
SHADOW_MODES = (
    "OPIP_FEATURE_BUS_MODE",
    "OPIP_CANONICAL_WRITER_MODE",
    "OPIP_TARGET_SPINE_MODE",
)
OFF_MODES = ("OPIP_PAPER_V2_MODE", "OPIP_COMMITTEE_MODE")

#: Numeric and ownership policy the adopted body leaves to the OWNER. Each entry
#: pairs an open-input row with the subject keywords it must keep declaring.
OPEN_INPUT_SUBJECTS = (
    ("OQ-01", (r"owner",)),
    ("OQ-02", (r"instrument identity", r"corporate-action")),
    ("OQ-03", (r"horizon boundar", r"holding range")),
    ("OQ-04", (r"latency", r"workload")),
    ("OQ-05", (r"advantage", r"cooldown", r"turnover budget")),
    ("OQ-06", (r"sleeve",)),
    ("OQ-07", (r"threshold",)),
    ("OQ-08", (r"r4", r"closure")),
    ("OQ-09", (r"registry", r"consumption disposition")),
    ("OQ-10", (r"questionnaire",)),
)

#: Paths a documentation-and-test increment may map; anything else is a runtime,
#: deployment, workflow or Compose change and fails AC-010.
MAPPED_PATH_PREFIXES = ("OHM-Trade-Agent-v1/docs/", "OHM-Trade-Agent-v1/tests/")
#: Prefixes a documentation-and-test increment may never map, because a mapped
#: path is inside the increment's authorized changed-file set.
FORBIDDEN_PATH_PREFIXES = (
    "OHM-Trade-Agent-v1/app/",
    "OHM-Trade-Agent-v1/deploy/",
    "OHM-Trade-Agent-v1/freqtrade/",
    "OHM-Trade-Agent-v1/scripts/",
    "OHM-Trade-Agent-v1/docker-compose",
    "OHM-Trade-Agent-v1/pyproject.toml",
    ".github/",
)

# -- document parsers -------------------------------------------------------

_LABEL_LINE = re.compile(r"^-\s+([A-Za-z][A-Za-z ]*?):\s*(.*)$")
_SECTION_TOKEN = re.compile(r"^§(\d+)$")
_CLAUSE_TOKEN = re.compile(r"^[A-Z]{2}-\d{2}$")
_APPENDIX_TOKEN = re.compile(r"^v(\d+\.\d+\.\d+) ([AB])$")


def _document(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _cells(line: str) -> tuple[str, ...]:
    return tuple(cell.strip() for cell in line.strip().strip("|").split("|"))


def _is_rule(cells: tuple[str, ...]) -> bool:
    return bool(cells) and all(cell and set(cell) <= set("-: ") for cell in cells)


def _tables(text: str) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    """Return ``(header, row)`` for every pipe-table data row in ``text``."""
    tables: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    header: tuple[str, ...] | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            header = None
            continue
        cells = _cells(stripped)
        if _is_rule(cells):
            continue
        if header is None:
            header = cells
            continue
        tables.append((header, cells))
    return tuple(tables)


def _blocks(text: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return ``(heading, body)`` for every ``##`` section of ``text``."""
    blocks: list[tuple[str, tuple[str, ...]]] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                blocks.append((heading, tuple(buffer)))
            heading = line[3:].strip()
            buffer = []
            continue
        if heading is not None:
            buffer.append(line)
    if heading is not None:
        blocks.append((heading, tuple(buffer)))
    return tuple(blocks)


def _labelled(body: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Return the leading ``- Label: value`` pairs of a contract section."""
    pairs: list[tuple[str, str]] = []
    for line in body:
        match = _LABEL_LINE.match(line.strip())
        if match is not None:
            pairs.append((match.group(1).strip(), match.group(2).strip()))
    return tuple(pairs)


def _tokens(value: str) -> tuple[str, ...]:
    """Split a comma/semicolon field into identifiers, dropping ``NONE``."""
    return tuple(
        token
        for token in (
            part.strip().strip("`").strip() for part in re.split(r"[,;]", value)
        )
        if token and token != "NONE"
    )


def _plain(value: str) -> str:
    return value.strip().strip("`").strip()


def _backticked(value: str) -> tuple[str, ...]:
    return tuple(re.findall(r"`([^`]+)`", value))


@lru_cache(maxsize=None)
def _register_table_rows() -> tuple[dict[str, str], ...]:
    return tuple(
        dict(zip(header, cells))
        for header, cells in _tables(_document(REGISTER))
        if header == REGISTER_FIELDS
    )


@lru_cache(maxsize=None)
def _register_headers() -> tuple[tuple[str, ...], ...]:
    return tuple(dict.fromkeys(header for header, _ in _tables(_document(REGISTER))))


@lru_cache(maxsize=None)
def _register_by_id() -> dict[str, dict[str, str]]:
    return {row["ID"]: row for row in _register_table_rows()}


@lru_cache(maxsize=None)
def _freeze_contract_values() -> dict[str, dict[str, str]]:
    """Map every ``## SC-nn`` section to its labelled lines."""
    contracts: dict[str, dict[str, str]] = {}
    for heading, body in _blocks(_document(FREEZE)):
        name = re.match(r"^(SC-\d{2})\b", heading)
        if name is not None:
            contracts[name.group(1)] = dict(_labelled(body))
    return contracts


@lru_cache(maxsize=None)
def _freeze_contract_labels() -> dict[str, tuple[str, ...]]:
    """Map every ``## SC-nn`` section to its labelled lines, in order."""
    contracts: dict[str, tuple[str, ...]] = {}
    for heading, body in _blocks(_document(FREEZE)):
        name = re.match(r"^(SC-\d{2})\b", heading)
        if name is not None:
            contracts[name.group(1)] = tuple(label for label, _ in _labelled(body))
    return contracts


@lru_cache(maxsize=None)
def _freeze_table(header: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    return tuple(
        dict(zip(found, cells))
        for found, cells in _tables(_document(FREEZE))
        if found == header
    )


@lru_cache(maxsize=None)
def _v150_sections() -> dict[int, str]:
    """Map every numbered top-level section of the v1.5.0 extraction."""
    lines = _document(V150_ARCH).splitlines()
    heads: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(\d+)\s+\S", line)
        if match is not None:
            heads.append((int(match.group(1)), index))
    sections: dict[int, str] = {}
    for position, (number, start) in enumerate(heads):
        end = heads[position + 1][1] if position + 1 < len(heads) else len(lines)
        sections[number] = "\n".join(lines[start:end])
    return sections


@lru_cache(maxsize=None)
def _v150_clauses() -> dict[str, str]:
    """Map clause identifiers to their definition in the v1.5.0 extraction.

    Section 17 rows (``AC-01 / A``) are acceptance scenarios rather than clause
    identifiers, so they are deliberately not part of the citation surface.
    """
    clauses: dict[str, str] = {}
    for line in _document(V150_ARCH).splitlines():
        match = re.match(r"^([A-Z]{2}-\d{2})[: ]\s*(\S.*)$", line)
        if match is None or match.group(1).startswith("AC-"):
            continue
        clauses.setdefault(match.group(1), match.group(2).strip())
    return clauses


@lru_cache(maxsize=None)
def _v144_appendix(letter: str) -> dict[str, str]:
    """Map the ``PA-*`` (Appendix A) or ``SQ-*`` (Appendix B) clause titles."""
    prefix = "PA" if letter == "A" else "SQ"
    lines = _document(V144_ARCH).splitlines()
    heads: list[tuple[str, int]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^Appendix ([ABC])\b", line)
        if match is not None:
            heads.append((match.group(1), index))
    start = next(index for name, index in heads if name == letter)
    later = [index for name, index in heads if name != letter and index > start]
    end = min(later) if later else len(lines)
    clauses: dict[str, str] = {}
    for line in lines[start:end]:
        match = re.match(rf"^{prefix}-(\d{{2}})\s+(\S.*)$", line)
        if match is not None:
            clauses.setdefault(f"{prefix}-{match.group(1)}", match.group(2).strip())
    return clauses


def _adopted_clauses(contract: str) -> tuple[str, ...]:
    return _tokens(_freeze_contract_values()[contract]["Adopted clauses"])


def _register_edges(row_id: str) -> tuple[str, ...]:
    row = _register_by_id().get(row_id)
    return () if row is None else _tokens(row["Depends on"])


def _declared_edges(marker: str) -> dict[str, tuple[str, ...]]:
    """Parse one ``X``←``A`,``B`` dependency bullet of the register."""
    edges: dict[str, tuple[str, ...]] = {}
    pattern = re.compile(r"`([A-Za-z0-9-]+)`←((?:`[A-Za-z0-9-]+`\s*,?\s*)+)")
    line = next(
        line for line in _document(REGISTER).splitlines() if line.startswith(marker)
    )
    for source, targets in pattern.findall(line):
        edges[source] = tuple(re.findall(r"`([A-Za-z0-9-]+)`", targets))
    return edges


def _cycle(edges: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    """Return one dependency cycle if the graph has one, else ``()``."""
    order: dict[str, int] = {}
    path: list[str] = []

    def visit(node: str) -> tuple[str, ...]:
        if order.get(node) == 1:
            return tuple(path[path.index(node) :] + [node])
        if order.get(node) == 2:
            return ()
        order[node] = 1
        path.append(node)
        for target in edges.get(node, ()):
            found = visit(target)
            if found:
                return found
        path.pop()
        order[node] = 2
        return ()

    for node in edges:
        found = visit(node)
        if found:
            return found
    return ()


def _cited_paths(text: str) -> tuple[str, ...]:
    """Return repository paths cited in backticks, expanding a bare suffix."""
    paths: list[str] = []
    previous: str | None = None
    for token in _backticked(text):
        if token.startswith("."):
            if previous is not None:
                paths.append(str(Path(previous).with_suffix("")) + token)
            continue
        if "/" in token:
            paths.append(token)
            previous = token
    return tuple(paths)


def _resolves(relative: str) -> bool:
    return (REPO_ROOT / relative).is_file() or (APP_ROOT / relative).is_file()


def test_ac_001_requirement_inventory_is_complete():
    """ATDD-R5-0-contract-conformance/AC-001: one register row per adopted requirement."""
    assert _register_headers() == (REGISTER_FIELDS,), (
        "the register must declare exactly one row shape"
    )
    rows = _register_table_rows()
    assert [row["ID"] for row in rows] == list(ROW_ORDER)
    assert len(set(ROW_ORDER)) == len(ROW_ORDER)
    assert len(_register_by_id()) == len(rows)
    text = _document(REGISTER)
    assert INCREMENT in text
    assert "thirteen" in text
    for row in rows:
        for field_name in REGISTER_FIELDS:
            assert row[field_name].strip(), f"{row['ID']} has an empty {field_name}"
        assert _plain(row["Owner"]) in OWNER_VOCAB, row["ID"]
    assert set(FEATURE_IDS) <= set(_register_by_id())
    assert set(OPEN_INPUT_IDS) <= set(_register_by_id())


def test_ac_002_clause_citations_resolve_to_adopted_sources():
    """ATDD-R5-0-contract-conformance/AC-002: every clause citation resolves to a pinned source."""
    register_text = _document(REGISTER)
    assert ADOPTED_SHA256 in register_text
    assert ADOPTED_SHA256 in _document(FREEZE)
    assert INHERITED_SHA256 in _document(CONTRACT)
    for relative, digest in (
        (
            "docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx",
            ADOPTED_SHA256,
        ),
        (
            "docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx",
            INHERITED_SHA256,
        ),
    ):
        artifact = APP_ROOT / relative
        assert artifact.is_file(), relative
        assert hashlib.sha256(artifact.read_bytes()).hexdigest() == digest, relative

    authority = next(
        body
        for heading, body in _blocks(register_text)
        if heading.startswith("Adopted authority")
    )
    scope = parse_scope_contract(_document(CONTRACT))
    cited = _cited_paths("\n".join(authority)) + _cited_paths(
        scope.sections["ARCHITECTURE REFERENCES"]
    )
    assert cited, "the adopted-authority block must cite its sources"
    for path in dict.fromkeys(cited):
        assert _resolves(path), f"cited source path does not exist: {path}"

    sections = _v150_sections()
    clauses = _v150_clauses()
    assert 10 in sections and 13 in sections and 18 in sections
    for row in _register_table_rows():
        assert row["Source"] in SOURCE_VOCAB, row["ID"]
        tokens = _tokens(row["Clause"])
        assert tokens, f"{row['ID']} cites no clause"
        for token in tokens:
            section = _SECTION_TOKEN.match(token)
            if section is not None:
                assert int(section.group(1)) in sections, (
                    f"{row['ID']} cites missing v1.5.0 section {token}"
                )
                continue
            appendix = _APPENDIX_TOKEN.match(token)
            if appendix is not None:
                assert appendix.group(2) in ("A", "B"), f"{row['ID']} cites {token}"
                assert _resolves(
                    f"docs/architecture/v{appendix.group(1)}/ARCHITECTURE.md"
                ), f"{row['ID']} cites an unknown source version {token}"
                continue
            assert _CLAUSE_TOKEN.match(token) is not None, (
                f"{row['ID']} cites an unparseable clause token {token!r}"
            )
            assert token in clauses, f"{row['ID']} cites missing clause {token}"


def test_ac_003_inherited_clauses_are_preserved():
    """ATDD-R5-0-contract-conformance/AC-003: PA-01-PA-20 and SQ-01-SQ-10 keep their v1.4.4 titles."""
    appendix_pa = _v144_appendix("A")
    appendix_sq = _v144_appendix("B")
    assert sorted(appendix_pa) == list(INHERITED_IDS[:20])
    assert sorted(appendix_sq) == list(INHERITED_IDS[20:])
    assert "remain applicable" in _document(V150_ARCH)
    assert "remain applicable" in _document(REGISTER)

    for row_id in INHERITED_IDS:
        row = _register_by_id()[row_id]
        letter = "A" if row_id.startswith("PA-") else "B"
        title = (_v144_appendix(letter))[row_id]
        tokens = _tokens(row["Clause"])
        assert row["Requirement"].strip() == title, f"{row_id} title drifted"
        assert row["Source"] == "v1.5.0+v1.4.4", row_id
        assert f"v1.4.4 {letter}" in tokens, row_id
        assert "§13" in tokens, f"{row_id} must cite the preserving section 13"
        assert "INHERITED" in row["Note"], row_id
    inherited = set(INHERITED_IDS)
    assert inherited.isdisjoint(FEATURE_IDS + DELIVERY_IDS + GATE_IDS + OPEN_INPUT_IDS)
    for row in _register_table_rows():
        if row["ID"] not in inherited:
            assert not row["ID"].startswith(("PA-", "SQ-")), row["ID"]


def test_ac_004_status_vocabulary_and_evidence_resolve():
    """ATDD-R5-0-contract-conformance/AC-004: closed status vocabulary with resolvable evidence."""
    rows = _register_table_rows()
    statuses = {row["Status"].strip("`").strip() for row in rows}
    assert statuses <= set(STATUS_VOCAB), sorted(statuses - set(STATUS_VOCAB))
    assert statuses
    document = _document(REGISTER)
    for status in STATUS_VOCAB:
        assert f"`{status}`" in document, status
    for row in rows:
        evidence = row["Evidence"].strip()
        assert evidence, row["ID"]
        if evidence == "NONE":
            continue
        for path in _tokens(evidence):
            assert path.startswith("OHM-Trade-Agent-v1/"), f"{row['ID']} cites {path}"
            assert (REPO_ROOT / path).is_file(), f"{row['ID']} cites missing {path}"
    claimed = {"IMPLEMENTED_BASELINE", "IMPLEMENTED_VERIFIED"}
    for row in rows:
        if row["Status"].strip("`").strip() in claimed:
            assert row["Evidence"].strip() != "NONE", (
                f"{row['ID']} claims implementation without evidence"
            )


def test_ac_005_authority_separation_holds():
    """ATDD-R5-0-contract-conformance/AC-005: shadow and live authority stay separated."""
    register_text = _document(REGISTER)
    for phrase in REGISTER_AUTHORITY_STATEMENTS:
        assert phrase in register_text, phrase
    for phrase in REGISTER_INTEGRITY_STATEMENTS:
        assert phrase in register_text, phrase

    authorities = set()
    gated = set()
    for row in _register_table_rows():
        value = _plain(row["Authority"])
        assert value in AUTHORITY_VOCAB, f"{row['ID']} authority {value!r}"
        assert value != "LIVE", f"{row['ID']} declares live authority"
        lowered = value.lower()
        assert "funded" not in lowered and "exchange" not in lowered, row["ID"]
        authorities.add(value)
        if value == "TARGET_PAPER_GATED":
            gated.add(row["ID"])
    assert authorities <= {"NONE", "SHADOW", "PAPER_RESEARCH", "TARGET_PAPER_GATED"}
    assert "SHADOW" in authorities and "NONE" in authorities
    assert gated == set(GATED_AUTHORITY_IDS), sorted(gated)
    assert "LIVE" in AUTHORITY_VOCAB, "the vocabulary must name the forbidden value"


def test_ac_006_single_owner_per_logical_record():
    """ATDD-R5-0-contract-conformance/AC-006: one owning module per logical record."""
    owner_table = _freeze_table(FREEZE_OWNER_HEADER)
    assert len(owner_table) == len(RECORD_OWNERS)
    by_record = {row["Logical record"]: row for row in owner_table}
    assert set(by_record) == {record for _, record, _ in RECORD_OWNERS}
    modules = [_plain(row["Owning module"]) for row in owner_table]
    assert len(set(modules)) == len(modules), "a module may own at most one record"

    contracts = _freeze_contract_values()
    for contract, record, module in RECORD_OWNERS:
        row = by_record[record]
        assert _plain(row["Governing contract"]) == contract, record
        assert _plain(row["Owning module"]) == module, record
        assert (REPO_ROOT / module).is_file(), module
        assert _plain(row["Reuse status"]) in REUSE_VOCAB, record
        section = contracts[contract]
        assert _plain(section["Frozen record"]) == record, contract
        assert _plain(section["Owning module"]) == module, contract
        assert _plain(section["Reuse status"]) == _plain(
            row["Reuse status"]
        ), contract
        assert "§10" in _adopted_clauses(contract), contract

    labels = _freeze_contract_labels()
    assert set(labels) == set(CONTRACT_IDS), sorted(set(labels) ^ set(CONTRACT_IDS))
    for name, found in labels.items():
        for label in RECORD_LABELS:
            assert (label in found) == (name in RECORD_CONTRACTS), (name, label)

    freeze_text = _document(FREEZE)
    for phrase in FROZEN_BOUNDARY_STATEMENTS:
        assert phrase in freeze_text, phrase
    register_text = _document(REGISTER)
    for phrase in ("new event store", "second outcome engine", "second position registry",
                   "second scheduler", "second deployment control plane"):
        assert phrase in register_text, phrase


def test_ac_007_dependencies_are_resolvable_and_acyclic():
    """ATDD-R5-0-contract-conformance/AC-007: dependencies resolve, stay acyclic and keep order."""
    rows = _register_table_rows()
    declared_ids = {row["ID"] for row in rows}
    edges: dict[str, tuple[str, ...]] = {}
    for row in rows:
        dependencies = _register_edges(row["ID"])
        edges[row["ID"]] = dependencies
        for dependency in dependencies:
            assert dependency in declared_ids, (
                f"{row['ID']} depends on undeclared {dependency}"
            )

    declared = dict(_declared_edges("- Edges are exactly those"))
    declared.update(_declared_edges("- Gates consume increments"))
    assert declared, "the register must declare its dependency edges"
    assert {node: deps for node, deps in edges.items() if deps} == declared

    cycle = _cycle(edges)
    assert cycle == (), f"dependency cycle: {' -> '.join(cycle)}"

    position = {node: index for index, node in enumerate(DELIVERY_ORDER)}
    assert set(DELIVERY_ORDER) <= declared_ids
    for increment in DELIVERY_ORDER[1:]:
        dependencies = edges[increment]
        assert dependencies, f"{increment} declares no dependency"
        for dependency in dependencies:
            assert position[dependency] < position[increment], (
                f"{increment} precedes its dependency {dependency}"
            )
    assert set(edges["R5-F"]) == {"R5-A", "R5-E"}

    freeze_text = _document(FREEZE)
    for pair in (("R5-A", "R5-B"), ("R5-B", "R5-C"), ("R5-C", "R5-D"), ("R5-D", "R5-E")):
        assert f"`{pair[0]}` precedes `{pair[1]}`" in freeze_text, pair
    assert "gates consume increments and never the reverse" in freeze_text


def test_ac_008_structural_contracts_derive_from_clauses():
    """ATDD-R5-0-contract-conformance/AC-008: frozen contracts derive from adopted clauses."""
    contract_blocks = _blocks(_document(FREEZE))
    headings = [
        name.group(1)
        for heading, _ in contract_blocks
        for name in [re.match(r"^(SC-\d{2})\b", heading)]
        if name is not None
    ]
    assert headings == list(CONTRACT_IDS)

    contracts = _freeze_contract_values()
    labels_by_contract = _freeze_contract_labels()
    assert set(contracts) == set(CONTRACT_IDS)
    open_contracts = set()
    for name in CONTRACT_IDS:
        section = contracts[name]
        labels = labels_by_contract[name]
        expected = CONTRACT_LABELS if name in RECORD_CONTRACTS else CORE_LABELS
        assert labels[: len(expected)] == expected, f"{name} labels drifted: {labels}"
        assert "Frozen invariants" in labels, name
        status = _plain(section["Freeze status"])
        assert status in FREEZE_STATUS_VOCAB, name
        if status == "OPEN":
            open_contracts.add(name)
        assert _tokens(section["Register rows"]), name
        owner_input = _plain(section["Open owner input"])
        assert owner_input, name
        if owner_input != "NONE":
            for token in _tokens(owner_input):
                assert token in OPEN_INPUT_IDS, (name, token)
    assert open_contracts == set(OPEN_CONTRACTS), sorted(open_contracts)

    sections = _v150_sections()
    clauses = _v150_clauses()
    for name in CONTRACT_IDS:
        tokens = _tokens(contracts[name]["Adopted clauses"])
        assert tokens, name
        for token in tokens:
            match = _SECTION_TOKEN.match(token)
            if match is not None:
                assert int(match.group(1)) in sections, (name, token)
                continue
            assert token in clauses, f"{name} adopts missing clause {token}"

    lineage = sections[10]
    for contract, record, _module in RECORD_OWNERS:
        keys = _backticked(contracts[contract]["Record keys"])
        assert keys, contract
        for key in keys:
            assert key in lineage, (
                f"{record} key {key!r} does not occur in the adopted v1.5.0 section 10"
            )

    table = _freeze_table(FREEZE_CONTRACT_HEADER)
    assert len(table) == len(CONTRACT_IDS)
    inverse = {row["Contract"]: _tokens(row["Register rows"]) for row in table}
    forward: dict[str, list[str]] = {name: [] for name in CONTRACT_IDS}
    for row in _register_table_rows():
        for contract in _tokens(row["Contract"]):
            assert contract in forward, f"{row['ID']} names undeclared {contract}"
            forward[contract].append(row["ID"])
    assert inverse == {name: tuple(ids) for name, ids in forward.items()}
    for name, ids in inverse.items():
        positions = [ROW_ORDER.index(row_id) for row_id in ids]
        assert positions == sorted(positions), f"{name} rows are out of declared order"


def test_ac_009_open_numeric_policy_is_recorded():
    """ATDD-R5-0-contract-conformance/AC-009: open numeric policy stays an owner decision."""
    freeze_text = _document(FREEZE)
    for phrase in NUMERIC_POLICY_STATEMENTS:
        assert phrase in freeze_text, phrase
    assert "No `SC-*` contract widens" in freeze_text

    rows = _register_by_id()
    open_rows = {
        row["ID"]
        for row in _register_table_rows()
        if _plain(row["Status"]) == OPEN_STATUS
    }
    assert open_rows == set(OPEN_INPUT_IDS), sorted(open_rows)
    for row_id, keywords in OPEN_INPUT_SUBJECTS:
        row = rows[row_id]
        assert _plain(row["Owner"]) == "OWNER", row_id
        assert _plain(row["Authority"]) == "NONE", row_id
        assert _plain(row["Open input"]) == "NONE", row_id
        assert _plain(row["Gate"]) in GATE_IDS, row_id
        contracts = _tokens(row["Contract"])
        assert contracts and set(contracts) <= set(CONTRACT_IDS), row_id
        requirement = row["Requirement"].lower()
        for keyword in keywords:
            assert re.search(keyword, requirement), (
                f"{row_id} must keep declaring {keyword!r}"
            )

    table = _freeze_table(FREEZE_OPEN_INPUT_HEADER)
    assert tuple(row["Open input"] for row in table) == OPEN_INPUT_IDS
    for entry in table:
        row_id = entry["Open input"]
        row = rows[row_id]
        assert _plain(entry["Gate"]) == _plain(row["Gate"]), row_id
        assert set(_tokens(entry["Structural contracts"])) <= set(
            _tokens(row["Contract"])
        ), row_id
        assert entry["Subject"].strip(), row_id


def test_ac_010_no_authority_path_is_mapped():
    """ATDD-R5-0-contract-conformance/AC-010: no runtime, deployment or Compose path is mapped."""
    scope = parse_scope_contract(_document(CONTRACT))
    assert scope.increment == INCREMENT
    mapped = sorted(
        {path for paths in scope.implementation_map.values() for path in paths}
    )
    assert mapped, "the increment must map the artifacts it delivers"
    for path in mapped:
        normalized = normalize_repo_path(path)
        assert (REPO_ROOT / normalized).is_file(), normalized
        assert normalized.startswith(MAPPED_PATH_PREFIXES), normalized
        for prefix in FORBIDDEN_PATH_PREFIXES:
            assert not normalized.startswith(prefix), f"{normalized} maps {prefix}"

    freeze_text = _document(FREEZE)
    for phrase in FREEZE_AUTHORITY_STATEMENTS + ZERO_AUTHORITY_STATEMENTS:
        assert phrase in freeze_text, phrase
    register_text = _document(REGISTER)
    for phrase in REGISTER_AUTHORITY_STATEMENTS:
        assert phrase in register_text, phrase
    for target in (REGISTER, FREEZE, CONTRACT):
        assert target.is_file(), str(target)


def test_ac_011_scope_contract_is_self_consistent():
    """ATDD-R5-0-contract-conformance/AC-011: the scope contract and its mapped paths resolve."""
    scope = parse_scope_contract(_document(CONTRACT))
    assert re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", scope.increment), scope.increment
    assert scope.increment == "ATDD-R5-0-contract-conformance"
    assert tuple(scope.sections) == REQUIRED_SECTIONS
    assert scope.sections["UNAPPROVED SCOPE CHANGES"] == "NONE"
    assert tuple(scope.acceptance_criteria) == AC_IDS
    assert tuple(scope.implementation_map) == AC_IDS

    for ac_id, body in scope.acceptance_criteria.items():
        assert behavior_label_problems(body) == (), ac_id
        assert ac_id in scope.traceability, f"{ac_id} has no acceptance test trace"
        targets = scope.traceability[ac_id]
        assert len(targets) == 1, f"{ac_id} traces to {targets}"
        node_id = targets[0]
        assert node_id.startswith(
            "tests/test_opip_r5_0_contract_conformance.py::test_ac_"
        ), node_id
        name = node_id.rsplit("::", 1)[1]
        assert re.fullmatch(r"test_ac_\d{3}_[a-z0-9_]+", name), name
        assert f"AC-{name[8:11]}" == ac_id, node_id

    acceptance = load_acceptance_index(APP_ROOT / "tests")
    for ac_id in AC_IDS:
        declared = set(scope.traceability[ac_id])
        live = set(acceptance.by_ac.get((scope.increment, ac_id), frozenset()))
        assert declared == live, f"{ac_id}: declared {declared} but found {live}"

    mapped = tuple(
        sorted({path for paths in scope.implementation_map.values() for path in paths})
    )
    result = check_scope(scope, acceptance, mapped)
    assert result.passed, result.reasons
    blocked = check_scope(
        scope, acceptance, mapped + ("OHM-Trade-Agent-v1/app/main.py",)
    )
    assert not blocked.passed and blocked.status == "SCOPE_CHANGE_REQUIRED"
    assert any("changed file not mapped" in reason for reason in blocked.scope_reasons)


def test_ac_012_release_profile_safeguards_hold():
    """ATDD-R5-0-contract-conformance/AC-012: EVIDENCE_SHADOW stays active and TARGET_PAPER blocked."""
    profiles = get_release_profiles()
    assert set(profiles) == {"SAFE_BASELINE", "EVIDENCE_SHADOW", "TARGET_PAPER"}
    assert sorted(profiles) == ["EVIDENCE_SHADOW", "SAFE_BASELINE", "TARGET_PAPER"]
    for name in sorted(profiles):
        assert set(profiles[name]["allowed_modes"]) == set(SHADOW_MODES + OFF_MODES) | {
            "OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD"
        }, name

    target = resolve_release_profile("TARGET_PAPER")
    assert target["status"] == "BLOCKED"
    with pytest.raises(ValueError):
        render_profile_environment("TARGET_PAPER")
    consistent, problems = validate_profile_contract("TARGET_PAPER")
    assert consistent is False and problems
    assert any("blocked" in problem for problem in problems)

    shadow = resolve_release_profile("EVIDENCE_SHADOW")
    assert shadow["status"] == "ACTIVE"
    shadow["allowed_modes"]["OPIP_PAPER_V2_MODE"] = "active"
    assert resolve_release_profile("EVIDENCE_SHADOW")["allowed_modes"][
        "OPIP_PAPER_V2_MODE"
    ] == "off", "resolved profiles must not be mutable in place"
    modes = render_profile_environment("EVIDENCE_SHADOW")
    for key in SHADOW_MODES:
        assert modes[key] == "shadow", key
    for key in OFF_MODES:
        assert modes[key] == "off", key
    consistent, problems = validate_profile_contract(
        "EVIDENCE_SHADOW", requested_modes=modes
    )
    assert consistent is True, problems
    drifted, problems = validate_profile_contract(
        "EVIDENCE_SHADOW", requested_modes={**modes, "OPIP_PAPER_V2_MODE": "active"}
    )
    assert drifted is False and problems

    compose = yaml.safe_load(_document(COMPOSE))
    environment = compose["services"][CORE_SERVICE]["environment"]
    assert str(environment["OPIP_RELEASE_PROFILE"]) == "EVIDENCE_SHADOW"
    assert str(environment["OPIP_PAPER_V2_MODE"]) == "off"
    for key in SHADOW_MODES + OFF_MODES:
        assert str(environment[key]) == modes[key], key

    gate = evaluate_architecture_gate("EVIDENCE_SHADOW", repo_root=APP_ROOT)
    assert gate["status"] == "PASS", sorted(
        name for name, ok in gate["checks"].items() if not ok
    )
    assert gate["profile"] == "EVIDENCE_SHADOW"
    assert gate["paper_v2"] == "OFF"
    assert gate["funded_authority"] == "ABSENT"
    assert gate["runtime"]["OPIP_PAPER_V2_MODE"] == "off"
    assert "ARCHITECTURE_GATE=PASS" in render_release_verdict(gate)

    freeze_text = _document(FREEZE)
    assert "`EVIDENCE_SHADOW` release profile remains the activated profile" in freeze_text
    assert "`TARGET_PAPER` remains `BLOCKED`" in freeze_text
    assert "OPIP_PAPER_V2_MODE=off" in freeze_text


# -- end of module
