"""Mission 07 readiness evidence: effective owner resolution and recovery orchestration.

Increment: ``ATDD-R5-M07-readiness-evidence``.

Two contracts are proven here, both read-only and both against real repository files or
real application entry points:

1. **Effective owner resolution.** The append-only owner-decision record supplies the
   effective owner; the frozen requirements register keeps its pre-decision history. The
   resolution rule is exercised against both real files.
2. **Paper-v2 recovery orchestration.** The real
   ``app.services.paper_v2_execution.recover_outstanding_paper_v2_trades`` entry point is
   driven with deterministic fakes. Recovery is "already-authorized work, not new trading".

Nothing here activates anything, changes a mode, admits a trade or ratifies a threshold.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from app.services import paper_v2_execution as pv2

APP_ROOT = pathlib.Path(__file__).resolve().parents[1]
REGISTER = APP_ROOT / "docs" / "architecture" / "OPIP_R5_REQUIREMENTS_REGISTER.md"
DECISIONS = APP_ROOT / "docs" / "architecture" / "OPIP_OWNER_DECISIONS_REGISTER.md"

pytestmark = pytest.mark.acceptance

FEATURE_IDS = tuple(f"F{n}" for n in range(1, 13))
GATE_IDS = tuple(f"G{n}" for n in range(1, 7))


# ---------------------------------------------------------------------------
# the resolution rule, implemented here as the reference contract
# ---------------------------------------------------------------------------


def _decision_rows() -> dict[str, dict[str, str]]:
    """Read MAPPING rows from the append-only owner-decision record."""

    out: dict[str, dict[str, str]] = {}
    for line in DECISIONS.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped.startswith("MAPPING: "):
            continue
        cells = [c.strip() for c in stripped[len("MAPPING: ") :].split("|")]
        record = {}
        for cell in cells[1:]:
            key, _, value = cell.partition("=")
            record[key.strip()] = value.strip()
        out[cells[0]] = record
    return out


def _decisions_in_order() -> list[tuple[str, dict[str, dict[str, str]]]]:
    """Every decision in the record, in identifier order (append-only ordering)."""

    text = DECISIONS.read_text(encoding="utf-8")
    found: list[tuple[str, dict[str, dict[str, str]]]] = []
    for match in re.finditer(r"^## (OD-\d+) —", text, flags=re.MULTILINE):
        found.append((match.group(1), {}))
    return sorted(found, key=lambda pair: pair[0])


def _frozen_owner(row_id: str) -> str:
    """The frozen register's Owner cell: the preserved pre-decision value."""

    for line in REGISTER.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| {row_id} |"):
            cells = [c.strip() for c in line.split("|")]
            return cells[11]
    raise AssertionError(f"{row_id} is not a register row")


def _effective_owner(row_id: str, mappings: dict[str, dict[str, str]]) -> str:
    """Resolve the effective owner: a decision supersedes, it never erases.

    The frozen register supplies the source history; the latest applicable decision
    supplies the effective value. A row with no decision stays at its frozen value, and
    no frozen value is rewritten.
    """

    record = mappings.get(row_id)
    if record is None:
        return _frozen_owner(row_id)
    return record["owner"]


# ---------------------------------------------------------------------------
# AC-440 effective ownership resolves without erasing history
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_440_effective_ownership_resolves_and_frozen_history_is_retained():
    """ATDD-R5-M07-readiness-evidence/AC-440: resolution supersedes, never erases.

    GIVEN the frozen register and the append-only owner-decision record
    WHEN the effective owner is resolved for every feature and gate row
    THEN each resolves to the designated owner, the frozen cell each resolution
    supersedes still reads its original pre-decision value, and a row with no decision
    resolves to its frozen value rather than to an invented one
    """

    mappings = _decision_rows()
    assert set(mappings) == set(FEATURE_IDS) | set(GATE_IDS)

    for row_id in FEATURE_IDS + GATE_IDS:
        assert _effective_owner(row_id, mappings) == "OWNER", row_id
        # The frozen source history is retained exactly as recorded.
        assert _frozen_owner(row_id) == "UNASSIGNED", row_id

    # No decision means no supersession: the frozen value stands.
    assert _effective_owner("PA-01", {}) == _frozen_owner("PA-01")


@pytest.mark.acceptance
def test_ac_440_decisions_are_append_only_and_the_first_decision_is_intact():
    """ATDD-R5-M07-readiness-evidence/AC-440: OD-001 is not reinterpreted.

    GIVEN the owner-decision record
    WHEN its decisions are read in identifier order
    THEN OD-001 is present and is the first, its ratified-number statement is NONE, its
    scope is accountability only, and it stands alone because no later decision exists
    to supersede it
    """

    ordered = _decisions_in_order()
    assert [ident for ident, _ in ordered] == ["OD-001"], ordered

    flat = " ".join(DECISIONS.read_text(encoding="utf-8").split())
    assert "NUMERIC_POLICY_RATIFIED_BY_THIS_DECISION | `NONE`" in flat
    assert "accountability assignment only" in flat
    # The decision states plainly what it does not do.
    assert "It does not claim a second, independent human approval" in flat


# ---------------------------------------------------------------------------
# Paper-v2 application-level recovery orchestration
# ---------------------------------------------------------------------------


class _Projection:
    """A deterministic stand-in for the recoverable-execution projection."""

    def __init__(self, *, status: str, entries: list[dict], error_code: str | None = None):
        self.status = status
        self.error_code = error_code
        self.entries = entries


class _Ack:
    """A canonical writer acknowledgement carrying proven-commit identity."""

    def __init__(self, key: str, sequence: int):
        self.status = "COMMITTED"
        self.event_id = f"evt-{sequence}"
        self.history_epoch = 1
        self.local_sequence = sequence
        self.idempotency_key = key


class _Client:
    """Records every canonical intent the recovery path submits."""

    def __init__(self, projection: _Projection):
        self._projection = projection
        self.intents: list = []

    def get_paper_v2_recoverable_executions(self):
        return self._projection

    def submit(self, intent):
        self.intents.append(intent)
        return _Ack(intent.idempotency_key, len(self.intents))


def _entry(**overrides) -> dict:
    base = {"paper_trade_id": "pt-1"}
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AC-441 an unreadable projection is unavailable, never silently empty
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_441_an_unreadable_projection_is_unavailable_and_does_no_work():
    """ATDD-R5-M07-readiness-evidence/AC-441: unreadable evidence must not read as success.

    GIVEN a recoverable-execution projection that is not OK
    WHEN recovery is run
    THEN it reports UNAVAILABLE with the upstream error code, considers nothing,
    completes nothing and submits no canonical event, so an unreadable work list can
    never be mistaken for an empty one
    """

    client = _Client(
        _Projection(status="UNAVAILABLE", entries=[], error_code="PROJECTION_STALE")
    )
    summary = pv2.recover_outstanding_paper_v2_trades(client)

    assert summary["status"] == "UNAVAILABLE"
    assert summary["considered"] == 0
    assert summary["completed"] == 0
    assert summary["retryable"] == 0
    assert client.intents == [], "an unreadable projection must submit nothing"
    assert any("PROJECTION_STALE" in detail for detail in summary["details"])


@pytest.mark.acceptance
def test_ac_441_a_projection_that_raises_is_unavailable_not_a_crash():
    """ATDD-R5-M07-readiness-evidence/AC-441: a raising projection fails closed.

    GIVEN a projection accessor that raises
    WHEN recovery is run
    THEN it reports UNAVAILABLE naming the failure type and submits no canonical event
    """

    class _Raising:
        def get_paper_v2_recoverable_executions(self):
            raise RuntimeError("canonical store unreachable")

    summary = pv2.recover_outstanding_paper_v2_trades(_Raising())

    assert summary["status"] == "UNAVAILABLE"
    assert summary["considered"] == 0
    assert any("RuntimeError" in detail for detail in summary["details"])


# ---------------------------------------------------------------------------
# AC-442 outstanding obligations are retained, and one bad trade cannot stop the sweep
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_442_a_trade_that_cannot_safely_proceed_is_retryable_not_released():
    """ATDD-R5-M07-readiness-evidence/AC-442: unsafe work keeps its reservation.

    GIVEN a work list whose entries carry no committed payloads
    WHEN recovery is run
    THEN every entry is counted as considered, each is reported retryable with the real
    failure reason, nothing is completed, no canonical event is submitted, and a later
    entry is still considered even though an earlier one failed
    """

    client = _Client(
        _Projection(
            status="OK",
            entries=[
                _entry(execution_attempt=None, quote_evidence=None),
                _entry(
                    paper_trade_id="pt-2", execution_attempt=None, quote_evidence=None
                ),
            ],
        )
    )
    summary = pv2.recover_outstanding_paper_v2_trades(client)

    assert summary["status"] == "OK"
    assert summary["considered"] == 2, "one failing trade must not stop the sweep"
    assert summary["completed"] == 0
    assert summary["retryable"] == 2
    assert client.intents == [], "unsafe work must submit nothing"
    assert any("pt-1" in detail for detail in summary["details"])
    assert any("pt-2" in detail for detail in summary["details"])
    retryable = [
        detail for detail in summary["details"] if detail.startswith("retryable ")
    ]
    assert len(retryable) == 2
    for detail in retryable:
        assert "PaperV2ExecutionError" in detail, detail
        assert "missing committed payloads" in detail, detail

