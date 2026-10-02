"""R4-B1 contract freeze: acceptance guards for the frozen R4-B1 contract.

This module proves the freeze artifact, not new production behavior. It asserts
that the R4-B1 scope contract exists, declares its own increment, freezes the
retry/requalification semantics and the orchestration and authority boundaries,
and that this increment granted no authority.

Where a frozen claim is a statement about existing code, the test proves it
against that code (the disposition-identity derivation and the admission-request
fact set) rather than only matching prose, so the freeze cannot silently drift
away from the code it freezes.

This module deliberately does NOT read mutable repository configuration
(Compose modes). The B1 completion posture is asserted as exact contract text;
the *current* posture lives in the replaceable
``tests/test_opip_current_runtime_posture.py`` guard, so a later owner-authorized
activation cannot invalidate this freeze.
"""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from pathlib import Path

import pytest

from app.opip.contracts.paper_execution import ENGINE_OPIP_PAPER_V2
from app.opip.contracts.paper_execution_runtime import PaperAdmissionRequest
from app.opip.contracts.serialization import stable_hash
from app.services.paper_v2_execution import build_disposition_id

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
ATDD = APP_ROOT / "docs" / "atdd"
SCOPE_CONTRACTS = ATDD / "scope-contracts"
CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-B1-contract-freeze.md"
CRON_DIR = APP_ROOT / "deploy" / "cron.d"

UNIFIED_CYCLE_MODULE = "app.jobs.run_cycle"
EXPECTED_SCHEDULER_FILE = "ohm-unified-cycle"

#: The frozen contract surface this acceptance module may import. It proves the
#: freeze against the frozen Paper-v2 contracts only; any other application
#: import (in particular an exchange, order or Committee surface) fails closed.
ALLOWED_APP_IMPORT_PREFIXES = (
    "app.opip.contracts.",
    "app.opip.decision.",
    "app.services.paper_v2_",
)

#: A unified-cycle invocation must be a whole module reference: ``app.jobs.run_cycle``
#: and not a longer module such as ``app.jobs.run_cycle_backfill``.
_UNIFIED_CYCLE_COMMAND = re.compile(r"\bapp\.jobs\.run_cycle\b")

#: An active cron command line is anything that is not blank, not a comment and
#: not an environment assignment such as ``SHELL=/bin/bash``.
_CRON_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

#: The closed classification vocabulary frozen by the contract.
CLASSIFICATION_VOCABULARY = frozenset(
    {
        "EXACT_RETRY",
        "REQUALIFIED_NEW_DECISION",
        "CONFLICTING_ATTEMPT",
        "NOT_AN_ADMISSION_RETRY",
        "DISTINCT_OPPORTUNITY",
        "FAIL_CLOSED",
    }
)

#: The exact trigger -> (owning stage, classification) mapping the contract must
#: carry. Asserting the full mapping means a reassignment such as moving
#: ``snapshot changes`` from REQUALIFIED_NEW_DECISION to EXACT_RETRY fails.
EXPECTED_CLASSIFICATION = {
    "identical admission request (retry facts byte or semantically identical)": (
        "admission",
        "EXACT_RETRY",
    ),
    "qualification facts differ": ("decision context", "REQUALIFIED_NEW_DECISION"),
    "snapshot changes": ("decision context", "REQUALIFIED_NEW_DECISION"),
    "evidence cutoff changes": ("decision context", "REQUALIFIED_NEW_DECISION"),
    "evaluation time changes": ("decision context", "REQUALIFIED_NEW_DECISION"),
    "policy or version changes": ("decision context", "REQUALIFIED_NEW_DECISION"),
    "decision context ancestry changes": (
        "decision context",
        "REQUALIFIED_NEW_DECISION",
    ),
    "requested capital changes": ("admission", "CONFLICTING_ATTEMPT"),
    "requested reservation amount changes": ("admission", "CONFLICTING_ATTEMPT"),
    "other admission-request facts change": ("admission", "CONFLICTING_ATTEMPT"),
    "requested notional changes": ("entry intent", "NOT_AN_ADMISSION_RETRY"),
    "quantity changes": ("entry intent", "NOT_AN_ADMISSION_RETRY"),
    "stop changes": ("protection plan", "NOT_AN_ADMISSION_RETRY"),
    "targets change": ("protection plan", "NOT_AN_ADMISSION_RETRY"),
    "execution geometry changes": ("protection plan", "NOT_AN_ADMISSION_RETRY"),
    "an earlier terminal stop already exists": ("execution", "EXACT_RETRY"),
    "a committed reservation exists": ("admission", "EXACT_RETRY"),
    "direction changes": ("disposition identity", "DISTINCT_OPPORTUNITY"),
    "canonical progress is temporarily unreadable": ("retry", "FAIL_CLOSED"),
}

#: The precedence/partition rule itself, asserted verbatim.
PRECEDENCE_RULE = (
    "Classification rule (mutually exclusive and exhaustive; precedence is "
    "evaluated in this order)."
)
PRECEDENCE_TIE_BREAK = "Step 2 takes precedence over step 3"

VOCABULARY_LINE = (
    "`EXACT_RETRY | REQUALIFIED_NEW_DECISION | CONFLICTING_ATTEMPT | "
    "NOT_AN_ADMISSION_RETRY | DISTINCT_OPPORTUNITY | FAIL_CLOSED`"
)


def _pointer() -> str:
    return (ATDD / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _active_cron_commands(path: Path) -> list[str]:
    """Executable cron command lines, ignoring blank lines, comments and env assignments."""
    commands: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if _CRON_ENV_ASSIGNMENT.match(stripped):
            continue
        commands.append(stripped)
    return commands


def _unified_cycle_invocations() -> list[tuple[str, str]]:
    """Every active scheduler command that invokes the unified cycle module."""
    invocations: list[tuple[str, str]] = []
    for path in sorted(CRON_DIR.iterdir()):
        if not path.is_file():
            continue
        for command in _active_cron_commands(path):
            if _UNIFIED_CYCLE_COMMAND.search(command):
                invocations.append((path.name, command))
    return invocations


def _classification_rows(text: str) -> dict[str, tuple[str, str]]:
    """Parse the trigger classification table into trigger -> (stage, classification)."""
    rows: dict[str, tuple[str, str]] = {}
    in_table = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("|"):
            in_table = False
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if not in_table:
            if "Trigger case" in cells and "Classification" in cells:
                in_table = True
            continue
        if len(cells) < 3 or set(cells[0]) <= {"-", " "}:
            continue
        rows[cells[0]] = (cells[1], cells[2])
    return rows


def test_ac_001_contract_and_pointer_are_consistent():
    """ATDD-R4-B1-contract-freeze/AC-001: the contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT.is_file(), CONTRACT
    text = _contract_text()
    # The contract declares its own increment. The pointer is expected to move
    # later, so it is not pinned here; the scope checker enforces that the active
    # pointer matches the checked increment.
    assert "INCREMENT:\nATDD-R4-B1-contract-freeze" in text
    pointer = _pointer()
    assert (SCOPE_CONTRACTS / f"{pointer}.md").is_file(), pointer


def test_ac_002_retry_and_requalification_semantics_are_frozen():
    """ATDD-R4-B1-contract-freeze/AC-002: the freeze states a mutually exclusive precedence rule, maps every enumerated trigger to exactly one owning stage and classification from the closed vocabulary, and states immutable ancestry and economics with fail-closed ambiguity - proved against the frozen code."""
    text = _contract_text()

    # The precedence / partition rule itself.
    assert PRECEDENCE_RULE in text
    assert PRECEDENCE_TIE_BREAK in text
    assert VOCABULARY_LINE in text

    # Every trigger carries exactly one owning stage and one classification.
    rows = _classification_rows(text)
    assert rows == EXPECTED_CLASSIFICATION
    for trigger, (stage, classification) in rows.items():
        assert classification in CLASSIFICATION_VOCABULARY, (trigger, classification)
        assert " " not in classification, (trigger, classification)
        assert stage, trigger
    # All three retry classifications are represented.
    classifications = {classification for _stage, classification in rows.values()}
    assert {
        "EXACT_RETRY",
        "REQUALIFIED_NEW_DECISION",
        "CONFLICTING_ATTEMPT",
    } <= classifications

    assert "admission ancestry is immutable" in text
    assert "Ambiguity fails closed" in text
    assert "duplicate trade, duplicate reservation or duplicate disposition" in text

    # Code-anchored: the frozen identity description matches the derivation.
    legacy = stable_hash(
        "PDISP",
        {
            "episode_id": "EP:1",
            "native_symbol": "SOLUSD",
            "engine": ENGINE_OPIP_PAPER_V2,
        },
    )
    assert (
        build_disposition_id(episode_id="EP:1", native_symbol="SOLUSD", direction="LONG")
        == legacy
    ), "a LONG must keep the historical direction-less disposition identity"
    assert (
        build_disposition_id(episode_id="EP:1", native_symbol="SOLUSD", direction="SHORT")
        != legacy
    ), "a SHORT must have a direction-distinct disposition identity"
    assert "only for a non-LONG direction" in text

    # Code-anchored: only admission-request facts participate in the identity;
    # quantity and notional are ENTRY order-intent facts, not admission facts.
    admission_fields = {field.name for field in fields(PaperAdmissionRequest)}
    assert {
        "decision_context_id",
        "requested_capital",
        "requested_reservation_amount",
    } <= admission_fields
    assert "requested_quantity" not in admission_fields
    assert "requested_notional" not in admission_fields


def test_ac_003_single_orchestration_boundary():
    """ATDD-R4-B1-contract-freeze/AC-003: exactly one active scheduler command invokes the unified cycle, that boundary is named by the contract, and no second scheduler is permitted."""
    text = _contract_text()
    assert "deploy/cron.d/ohm-unified-cycle" in text
    assert UNIFIED_CYCLE_MODULE in text
    assert "must not add a second scheduler" in text
    assert "timer, cron entry, daemon or orchestration loop" in text

    # Exactly one ACTIVE invocation exists across the scheduler set, and it is in
    # the expected file. This inspects command lines, not filenames, so a second
    # invocation inside the same file would still fail.
    invocations = _unified_cycle_invocations()
    assert len(invocations) == 1, invocations
    assert invocations[0][0] == EXPECTED_SCHEDULER_FILE
    assert "python -m app.jobs.run_cycle" in invocations[0][1]


def test_ac_004_dormant_posture_and_authority_boundary():
    """ATDD-R4-B1-contract-freeze/AC-004: the contract states the exact B1 completion posture and the authority boundary, without reading mutable repository configuration."""
    text = _contract_text()
    assert "runtime wiring PRESENT" in text
    assert "NON-AUTHORITATIVE" in text
    # Exact frozen completion posture, not substring matching on a mode name.
    assert "- `OPIP_FEATURE_BUS_MODE=off`" in text
    assert "- `OPIP_PAPER_V2_MODE` is unset" in text
    assert "the target F3-F7 path is NON-AUTHORITATIVE (runtime wiring PRESENT)" in text
    assert "F7 is not the admission authority" in text
    for authority in (
        "funded trading",
        "funded exchange order authority",
        "Committee runtime authority",
        "dashboard trading authority",
        "Telegram trading authority",
    ):
        assert authority in text, authority
    # The freeze must not pin mutable configuration: the exact posture is contract
    # text, and the contract says a later activation replaces the current guard.
    assert "replaces the mutable current-posture guard rather than this freeze" in text


def test_ac_005_rollback_and_exclusions_are_explicit():
    """ATDD-R4-B1-contract-freeze/AC-005: the contract names an exact rollback to the pre-R4-B1 orchestration and lists the explicit exclusions."""
    text = _contract_text()
    assert "Rollback." in text
    assert "remove the R4-B1 orchestration hook" in text
    assert "never leave two allocation authorities" in text
    for exclusion in (
        "R4-B2 activation",
        "funded or exchange authority",
        "Committee runtime authority",
        "legacy retirement",
        "hidden mode activation",
    ):
        assert exclusion in text, exclusion
    assert "F11" in text


def test_ac_006_no_authority_and_single_scheduler_entry():
    """ATDD-R4-B1-contract-freeze/AC-006: the contract records the B1 completion posture, exactly one unified-cycle invocation exists, and this acceptance module imports only the frozen Paper-v2 contract surface, never an exchange, order or Committee authority."""
    text = _contract_text()
    assert "- `OPIP_FEATURE_BUS_MODE=off`" in text
    assert "- `OPIP_PAPER_V2_MODE` is unset" in text
    assert len(_unified_cycle_invocations()) == 1

    modules: set[str] = set()
    for node in ast.walk(ast.parse(Path(__file__).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    for name in modules:
        lowered = name.lower()
        for forbidden in (
            "kraken_private",
            "exchanges",
            "committee",
            "order",
            "scan_opportunities",
        ):
            assert forbidden not in lowered, (name, forbidden)
        if name.startswith("app."):
            assert name.startswith(ALLOWED_APP_IMPORT_PREFIXES), name
