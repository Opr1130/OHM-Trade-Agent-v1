"""The production readiness bundle must be read-only END TO END and honest about counts.

Each test cites its criterion in ATDD-OPIP-READINESS-BUNDLE-FIX-V1. The earlier static
check (AC-007 of the probe contract) read only the bundle's own text, so it could not
see that a script the bundle invoked reaches a loader that quarantines a malformed
registry with ``os.replace``. These tests run the bundle against a sandbox that contains
a booby-trapped callee and a corrupt registry, and compare bytes, names and mtimes.
"""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from app.opip.canonical.schema import initialize_schema
from app.opip.canonical.writer import CanonicalWriter

pytestmark = pytest.mark.acceptance

APP_ROOT = Path(__file__).resolve().parents[1]
BUNDLE = APP_ROOT / "tools" / "opip_readiness_bundle.sh"
requires_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash required")


def _code(raw: str) -> str:
    return "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))


def _tree_state(root: Path) -> dict[str, tuple[str, int, int]]:
    """Bytes, names and mtimes of everything under ``root``.

    ``*-shm`` is SQLite's volatile shared-memory index (reader marks change when ANY
    reader attaches, and it is rebuilt from the WAL); it holds no durable state.
    """
    state = {}
    for path in sorted(root.rglob("*")):
        if path.name.endswith("-shm"):
            continue
        rel = str(path.relative_to(root))
        if path.is_dir():
            state[rel] = ("dir", 0, 0)
        else:
            stat = path.stat()
            state[rel] = (path.read_bytes().hex(), stat.st_mtime_ns, stat.st_size)
    return state


def _store(path: Path, per_epoch: list[int]) -> None:
    """A real-schema canonical store holding ``per_epoch[i]`` events in epoch i+1,
    left in the state a supported restore leaves it: ``meta`` points at the LAST epoch
    with next_local_sequence reflecting only that epoch, earlier events kept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    initialize_schema(conn, now_iso="2026-01-01T00:00:00Z")
    for index, count in enumerate(per_epoch):
        epoch = index + 1
        for seq in range(1, count + 1):
            conn.execute(
                "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
                "local_sequence, recorded_at, idempotency_key, payload_json) "
                "VALUES (?, 1, 'x.y', ?, ?, '2026-01-01T00:00:00Z', ?, '{}')",
                (f"e{epoch}-{seq}", epoch, seq, f"k{epoch}-{seq}"),
            )
        conn.execute(
            "UPDATE meta SET history_epoch=?, next_local_sequence=? WHERE id=1",
            (epoch, count + 1),
        )
    conn.commit()
    conn.close()


def _run_bundle(root: Path, db: Path, lock: Path) -> str:
    env = {
        "PATH": os.environ["PATH"],
        "OPIP_APP_ROOT": str(root),
        "OPIP_CANONICAL_DB": str(db),
        "OPIP_CYCLE_LOCK": str(lock),
        "HOME": str(root),
    }
    done = subprocess.run(
        ["bash", str(BUNDLE)], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    return done.stdout + done.stderr


def test_ac_001_the_bundle_invokes_no_other_repository_script():
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-001: a callee can mutate indirectly, so the bundle runs only commands it shows."""
    text = _code(BUNDLE.read_text(encoding="utf-8"))
    assert "diagnose" not in text
    assert re.search(r"\b(bash|sh|source)\s+[\"']?\$\{?(APP_ROOT|OPIP_APP_ROOT)", text) is None
    assert re.search(r"^\s*\.\s+\S", text, flags=re.MULTILINE) is None
    assert re.search(r"\bflock\b", text) is None  # a probe acquires the lock it inspects


@requires_bash
def test_ac_001_a_booby_trapped_callee_and_corrupt_registry_are_untouched(tmp_path):
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-001: running the bundle changes no byte, name or mtime anywhere in the sandbox."""
    root = tmp_path / "app"
    (root / "deploy" / "remote").mkdir(parents=True)
    sentinel = tmp_path / "TRAP_FIRED"
    for name in ("diagnose-opip-learning.sh", "ohm-deploy"):
        script = root / "deploy" / "remote" / name
        script.write_text(f"#!/usr/bin/env bash\ntouch {sentinel}\n", encoding="utf-8")
        script.chmod(0o755)
    registry = root / "data" / "active_trades.json"
    registry.parent.mkdir(parents=True)
    registry.write_bytes(b'{"corrupt": ')  # what load_json would quarantine via os.replace
    db = root / "data" / "opip" / "canonical" / "opip_canonical_v1.sqlite3"
    _store(db, [5])
    lock = tmp_path / "ohm-unified-cycle.lock"
    lock.write_bytes(b"")
    # Production condition: the writer is up, so its WAL and shm exist and stay open.
    live = sqlite3.connect(str(db))
    live.execute("PRAGMA wal_autocheckpoint=0")
    live.execute("UPDATE meta SET updated_at='2026-01-02T00:00:00Z' WHERE id=1")
    live.commit()  # a committed, uncheckpointed WAL frame, as on the running writer
    try:
        before = _tree_state(tmp_path)
        out = _run_bundle(root, db, lock)
        after = _tree_state(tmp_path)
    finally:
        live.close()
    assert "BUNDLE_COMPLETE" in out
    assert not sentinel.exists()
    assert after == before
    assert list(registry.parent.glob("*corrupt*")) == []


@requires_bash
def test_ac_001_the_cycle_lock_is_reported_without_being_opened_or_created(tmp_path):
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-001: an absent lock stays absent and a present one is not touched."""
    root = tmp_path / "app"
    root.mkdir()
    db = root / "missing.sqlite3"
    lock = tmp_path / "no-such.lock"
    out = _run_bundle(root, db, lock)
    assert "unified_cycle_host_lock=ABSENT" in out
    assert not lock.exists()


@requires_bash
def test_ac_002_a_restored_store_is_not_reported_as_a_total_and_gets_a_true_upper_bound(tmp_path):
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-002: after a restore next_local_sequence is a current-epoch position; the bundle prints the epoch and a sum over all epochs."""
    root = tmp_path / "app"
    root.mkdir()
    db = root / "c.sqlite3"
    _store(db, [40, 25, 3])  # three epochs; meta says next_local_sequence=4
    out = _run_bundle(root, db, tmp_path / "x.lock")
    assert "history_epoch=3 next_local_sequence=4" in out
    assert "NOT a total" in out
    assert "total events + 1" not in out
    assert "EVENT_TOTAL_UPPER_BOUND=68 across 3 epoch(s)" in out
    # The old, wrong inference would have cleared a 68-event store AND a 4-event one alike.
    assert 4 < 68


@requires_bash
def test_ac_002_an_unreadable_store_is_unverified_never_zero(tmp_path):
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-002: when the count cannot be proven the gate stays UNVERIFIED."""
    root = tmp_path / "app"
    root.mkdir()
    out = _run_bundle(root, root / "absent.sqlite3", tmp_path / "x.lock")
    assert "EVENT_TOTAL_UPPER_BOUND=UNVERIFIED" in out


def test_ac_002_a_supported_restore_keeps_old_events_and_resets_the_sequence(tmp_path):
    """ATDD-OPIP-READINESS-BUNDLE-FIX-V1/AC-002: pins the writer behaviour the bundle's wording depends on."""
    db = tmp_path / "w.sqlite3"
    writer = CanonicalWriter(db)
    writer.close()
    conn = sqlite3.connect(str(db))
    for seq in range(1, 8):
        conn.execute(
            "INSERT INTO events (event_id, schema_version, event_type, history_epoch, "
            "local_sequence, recorded_at, idempotency_key, payload_json) "
            "VALUES (?, 1, 'x.y', 1, ?, '2026-01-01T00:00:00Z', ?, '{}')",
            (f"e{seq}", seq, f"k{seq}"),
        )
    conn.execute("UPDATE meta SET next_local_sequence=8 WHERE id=1")
    conn.commit()
    conn.close()
    restored = CanonicalWriter(db)
    try:
        restored.advance_history_epoch_for_restore()
    finally:
        restored.close()
    conn = sqlite3.connect(str(db))
    try:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 7
        epoch, seq = conn.execute("SELECT history_epoch, next_local_sequence FROM meta WHERE id=1").fetchone()
        assert (epoch, seq) == (2, 1)
    finally:
        conn.close()
