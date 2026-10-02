"""R4-B1 runtime-integration freeze: acceptance guards for the dormant wiring contract.

This module proves the freeze artifact for the runtime-integration increment, not
new production behavior. The wiring itself is a later commit of the same
increment; these guards assert the frozen boundary, posture, authority, rollback
and exclusions, and that this freeze PR introduces no authority and no second
scheduler.

It deliberately does NOT read mutable repository configuration (Compose modes).
The current posture lives in the replaceable ``tests/test_opip_current_runtime_posture.py``
guard, so a later owner-authorized activation cannot invalidate this freeze.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
ATDD = APP_ROOT / "docs" / "atdd"
SCOPE_CONTRACTS = ATDD / "scope-contracts"
CONTRACT = SCOPE_CONTRACTS / "ATDD-R4-B1-runtime-integration-dormant.md"
RETRY_FREEZE = SCOPE_CONTRACTS / "ATDD-R4-B1-contract-freeze.md"
CRON_DIR = APP_ROOT / "deploy" / "cron.d"

UNIFIED_CYCLE_MODULE = "app.jobs.run_cycle"
EXPECTED_SCHEDULER_FILE = "ohm-unified-cycle"

#: The frozen contract surface this acceptance module may import. It proves the
#: freeze against the frozen contracts only; any other application import (in
#: particular an exchange, order or Committee surface) fails closed.
ALLOWED_APP_IMPORT_PREFIXES = (
    "app.opip.contracts.",
    "app.opip.decision.",
    "app.services.paper_v2_",
)

_UNIFIED_CYCLE_COMMAND = re.compile(r"\bapp\.jobs\.run_cycle\b")
_CRON_ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _pointer() -> str:
    return (ATDD / "ACTIVE_INCREMENT").read_text(encoding="utf-8").strip()


def _contract_text() -> str:
    return CONTRACT.read_text(encoding="utf-8")


def _frozen_boundaries() -> str:
    """The FROZEN BOUNDARIES section only, so authority assertions cannot pass
    on the AC criterion text itself."""
    text = _contract_text()
    start = text.index("FROZEN BOUNDARIES:")
    end = text.index("ACCEPTANCE TEST TRACEABILITY:")
    assert start < end
    return text[start:end]


def _active_cron_commands(path: Path) -> list[str]:
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
    invocations: list[tuple[str, str]] = []
    for path in sorted(CRON_DIR.iterdir()):
        if not path.is_file():
            continue
        for command in _active_cron_commands(path):
            if _UNIFIED_CYCLE_COMMAND.search(command):
                invocations.append((path.name, command))
    return invocations


def test_ac_001_contract_and_pointer_are_consistent():
    """ATDD-R4-B1-runtime-integration-dormant/AC-001: the contract exists, declares its own increment, and the movable pointer resolves to an existing contract without being pinned to this increment."""
    assert CONTRACT.is_file(), CONTRACT
    text = _contract_text()
    assert "INCREMENT:\nATDD-R4-B1-runtime-integration-dormant" in text
    pointer = _pointer()
    assert (SCOPE_CONTRACTS / f"{pointer}.md").is_file(), pointer


def test_ac_002_single_orchestration_boundary():
    """ATDD-R4-B1-runtime-integration-dormant/AC-002: exactly one active scheduler command invokes the unified cycle, it is in the expected file, and the contract forbids a second scheduler."""
    text = _contract_text()
    assert "app.jobs.run_cycle" in text
    assert "deploy/cron.d/ohm-unified-cycle" in text
    assert "is not modified" in text
    assert "No second scheduler, timer, cron entry, daemon or orchestration loop" in text

    invocations = _unified_cycle_invocations()
    assert len(invocations) == 1, invocations
    assert invocations[0][0] == EXPECTED_SCHEDULER_FILE
    assert "python -m app.jobs.run_cycle" in invocations[0][1]


def test_ac_003_gate_posture_and_composition_seam():
    """ATDD-R4-B1-runtime-integration-dormant/AC-003: the contract freezes an off-by-default gate that fails closed, states OFF is inert and legacy-unchanged, and states the composition terminates at the admission seam with no writer, reservation or execution authority."""
    text = _contract_text()
    assert "off by default" in text
    assert "accepting only `off` and `shadow`" in text
    assert "fails Settings parsing rather than silently enabling the path" in text
    assert "When the gate is `off` the hook is not invoked at all" in text
    assert "terminates at the Paper-v2 admission seam" in text
    assert "holds no writer, reservation or execution authority" in text


def test_ac_004_authority_boundary():
    """ATDD-R4-B1-runtime-integration-dormant/AC-004: the FROZEN BOUNDARIES section enumerates each authority that must remain unchanged and each authority class this increment may not create."""
    frozen = _frozen_boundaries()
    for authority in (
        "funded trading",
        "funded exchange order authority",
        "Committee runtime authority",
        "dashboard trading authority",
        "Telegram trading authority",
        "second scheduler",
        "a second reservation, allocation, execution, outcome or evidence authority",
    ):
        assert authority in frozen, authority


def test_ac_005_rollback_and_exclusions():
    """ATDD-R4-B1-runtime-integration-dormant/AC-005: the contract names an exact rollback and lists the explicit exclusions."""
    text = _contract_text()
    assert "Rollback." in text
    assert "remove the `run_cycle` hook and the composition module" in text
    assert "no canonical-data migration" in text
    frozen = _frozen_boundaries()
    assert "Exclusions." in frozen
    for exclusion in (
        "Feature Bus activation",
        "Paper-v2 activation",
        "F7 admission authority",
        "Committee runtime authority",
        "funded or exchange authority",
        "Kraken order authority",
        "legacy retirement or deletion",
        "F11 bypass",
        "hidden mode activation",
    ):
        assert exclusion in frozen, exclusion


def test_ac_006_current_posture_and_no_authority_import():
    """ATDD-R4-B1-runtime-integration-dormant/AC-006: the contract records the completion posture and the retry-freeze authority, exactly one unified-cycle invocation exists, and this acceptance module imports only the frozen contract surface."""
    text = _contract_text()
    assert "Feature Bus `off`" in text
    assert "Paper-v2 unset" in text
    assert "`ATDD-R4-B1-contract-freeze`" in text
    assert RETRY_FREEZE.is_file()
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
