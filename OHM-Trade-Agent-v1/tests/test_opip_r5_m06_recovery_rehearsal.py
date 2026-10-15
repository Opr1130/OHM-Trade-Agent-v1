"""Mission 06 recovery rehearsal: isolated, fixture-based backup, restore and rollback.

Increment: ``ATDD-R5-M06-recovery-rehearsal``.

This is the rehearsal Workstream B asks for. It runs entirely in ``tmp_path``: no
production write, no service change, no mode change, no destructive cleanup and no
touched repository state. It exercises the **real** durability primitives from
``app.opip.storage.bounded_jsonl`` rather than a re-implementation, because backup and
restore correctness lives in those primitives:

- ``write_atomic_lines`` — temp-file write, fsync, ``os.replace``, directory fsync
- ``sha256_file`` — the checksum the retirement ledger requires before any delete
- ``read_lines`` — the read-back used for reconciliation
- ``repair_truncated_tail`` — quarantine-then-truncate recovery of an interrupted append

The paper ledger is modelled as a fixture with the row shapes a paper book actually has
(cash, position, fill, outstanding obligation). The fixture is named as a model; nothing
here claims to be the production Paper-v2 ledger file.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.opip.storage.bounded_jsonl import (
    encode_row,
    read_lines,
    repair_truncated_tail,
    sha256_file,
    write_atomic_lines,
)

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-R5-M06-recovery-rehearsal"

BACKUP = "state.backup.jsonl"
LIVE = "state.jsonl"


def _paper_book() -> list[dict]:
    """A deterministic book: one cash row, two positions, three fills, one obligation."""

    return [
        {"kind": "cash", "currency": "USD", "available_microunits": 5_000_000_000},
        {"kind": "position", "instrument": "AAA", "quantity": 5, "avg_price": 100.0},
        {"kind": "position", "instrument": "BBB", "quantity": 2, "avg_price": 250.0},
        {"kind": "fill", "fill_id": "f1", "instrument": "AAA", "quantity": 5, "price": 100.0},
        {"kind": "fill", "fill_id": "f2", "instrument": "BBB", "quantity": 1, "price": 250.0},
        {"kind": "fill", "fill_id": "f3", "instrument": "BBB", "quantity": 1, "price": 250.0},
        {
            "kind": "obligation",
            "obligation_id": "o1",
            "instrument": "AAA",
            "reason": "PROTECTIVE_EXIT_PENDING",
        },
    ]


def _write(path: pathlib.Path, rows: list[dict]) -> None:
    write_atomic_lines(path, [encode_row(row) for row in rows])


def _rows(path: pathlib.Path) -> list[dict]:
    return [json.loads(line) for line in read_lines(path) if line.strip()]


def _open_obligations(rows: list[dict]) -> list[str]:
    return sorted(row["obligation_id"] for row in rows if row["kind"] == "obligation")


def _notional_microunits(rows: list[dict]) -> int:
    """Reconcile the book: fills are authoritative, so this is the reconciled notional."""

    total = 0
    for row in rows:
        if row["kind"] == "fill":
            total += int(round(row["quantity"] * row["price"] * 1_000_000))
    return total


def _backup_and_restore(tmp_path: pathlib.Path, rows: list[dict]):
    """Write the book, snapshot it, restore it, and return (live, backup, pre, post)."""

    live = tmp_path / LIVE
    backup = tmp_path / BACKUP
    _write(live, rows)
    # The snapshot is a byte-for-byte copy, exactly as an archival backup is.
    write_atomic_lines(backup, read_lines(live))
    pre = sha256_file(live)
    assert sha256_file(backup) == pre, "the snapshot must verify before restore"
    write_atomic_lines(live, read_lines(backup))
    return live, backup, pre, sha256_file(live)


# ---------------------------------------------------------------------------
# AC-430 artifact completeness and checksums
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_430_the_snapshot_is_complete_and_checksum_verified(tmp_path):
    """ATDD-R5-M06-recovery-rehearsal/AC-430: a snapshot is provably complete.

    GIVEN a paper book written through the atomic writer
    WHEN the snapshot is taken and both files are checksummed
    THEN the snapshot digest equals the live digest, the recovered row count equals the
    written row count, and the snapshot round-trips byte-identically
    """

    rows = _paper_book()
    live, backup, pre, post = _backup_and_restore(tmp_path, rows)

    assert pre == post, "restore changed the book"
    assert sha256_file(backup) == pre
    assert len(_rows(live)) == len(rows)
    assert read_lines(live) == read_lines(backup)


# ---------------------------------------------------------------------------
# AC-431 recovery of cash, positions, fills, obligations and reconciliation
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_431_the_book_recovers_intact_and_reconciles_before_and_after(tmp_path):
    """ATDD-R5-M06-recovery-rehearsal/AC-431: recovery reproduces the economic state.

    GIVEN a book carrying cash, positions, fills and an outstanding obligation
    WHEN it is snapshotted and restored
    THEN cash, positions, fills and open obligations all recover, and the reconciled
    notional is identical before and after restoration
    """

    rows = _paper_book()
    before = _notional_microunits(rows)
    live, _backup, _pre, _post = _backup_and_restore(tmp_path, rows)
    recovered = _rows(live)

    for kind in ("cash", "position", "fill"):
        assert [r for r in recovered if r["kind"] == kind] == [
            r for r in rows if r["kind"] == kind
        ], kind
    assert _open_obligations(recovered) == ["o1"]
    assert _notional_microunits(recovered) == before


# ---------------------------------------------------------------------------
# AC-432 recovery after an interrupted write, without losing evidence
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_432_an_interrupted_append_is_recovered_and_quarantined(tmp_path):
    """ATDD-R5-M06-recovery-rehearsal/AC-432: an interrupted write is repaired, not lost.

    GIVEN a book whose final append was cut mid-row, leaving an unterminated fragment
    WHEN the tail is repaired
    THEN every complete row survives, the file ends at a row boundary, the fragment is
    preserved byte-for-byte in a quarantine file rather than discarded, and the repaired
    book reconciles to the same economic state as the complete one
    """

    rows = _paper_book()
    live = tmp_path / LIVE
    _write(live, rows)
    complete = sha256_file(live)
    complete_notional = _notional_microunits(_rows(live))

    # Simulate a crash mid-append: a real fragment, with no terminator.
    fragment = b'{"kind":"fill","fill_id":"f4","instr'
    with live.open("ab") as handle:
        handle.write(fragment)
    assert not live.read_bytes().endswith(b"\n")

    repair_truncated_tail(live)

    quarantined = sorted(tmp_path.glob(f"{LIVE}.truncated-*.bin"))
    assert len(quarantined) == 1, quarantined
    assert quarantined[0].read_bytes() == fragment, "the fragment must be preserved intact"

    repaired = _rows(live)
    assert len(repaired) == len(rows)
    assert live.read_bytes().endswith(b"\n"), "the file must end at a row boundary"
    assert sha256_file(live) == complete, "the complete prefix must be untouched"
    assert _notional_microunits(repaired) == complete_notional


# ---------------------------------------------------------------------------
# AC-433 idempotent restoration and no duplicate execution or accounting
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_433_restoring_twice_is_idempotent_and_duplicates_nothing(tmp_path):
    """ATDD-R5-M06-recovery-rehearsal/AC-433: a second restore changes nothing.

    GIVEN a book restored once from a verified snapshot
    WHEN the restore and the tail repair are each performed again
    THEN the digest is unchanged, no second quarantine file appears, the row count is
    unchanged, and no fill or obligation is duplicated or double-counted
    """

    rows = _paper_book()
    live, backup, pre, post = _backup_and_restore(tmp_path, rows)
    assert pre == post
    first = _rows(live)

    for _ in range(2):
        write_atomic_lines(live, read_lines(backup))
        repair_truncated_tail(live)

    second = _rows(live)
    assert sha256_file(live) == post
    assert sorted(tmp_path.glob(f"{LIVE}.truncated-*.bin")) == []
    assert second == first
    fill_ids = [r["fill_id"] for r in second if r["kind"] == "fill"]
    assert sorted(fill_ids) == ["f1", "f2", "f3"]
    assert len(fill_ids) == len(set(fill_ids))
    assert _open_obligations(second) == ["o1"]
    assert _notional_microunits(second) == _notional_microunits(rows)


# ---------------------------------------------------------------------------
# AC-434 immutable history, atomic replacement and isolation from AI
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_ac_434_restore_preserves_the_snapshot_and_leaves_no_partial_file(tmp_path):
    """ATDD-R5-M06-recovery-rehearsal/AC-434: history is preserved; writes are atomic.

    GIVEN a verified snapshot and a live book
    WHEN the restore replaces the live file
    THEN the snapshot is byte-identical to its pre-restore digest, no temporary file
    survives the atomic replacement, and every byte of the live file is a complete row
    """

    rows = _paper_book()
    live, backup, pre, post = _backup_and_restore(tmp_path, rows)

    assert sha256_file(backup) == pre, "restore modified the immutable snapshot"
    assert pre == post
    assert sorted(tmp_path.glob("*.tmp")) == []
    assert sorted(tmp_path.glob(".*tmp*")) == []
    for line in read_lines(live):
        assert json.loads(line)["kind"] in {"cash", "position", "fill", "obligation"}


@pytest.mark.acceptance
def test_ac_434_the_recovery_path_has_no_ai_or_notification_dependency():
    """ATDD-R5-M06-recovery-rehearsal/AC-434: recovery cannot be blocked by the Committee.

    GIVEN the storage module that implements recovery
    WHEN its imports are inspected
    THEN it imports no committee, AI, network or notification surface, so a protective
    recovery can always complete while those components are unavailable
    """

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app"
        / "opip"
        / "storage"
        / "bounded_jsonl.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "opip.committee",
        "opip.decision_intelligence",
        "requests",
        "httpx",
        "telegram",
        "openai",
        "anthropic",
    ):
        assert forbidden not in source, forbidden


