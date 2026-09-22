"""Regression: the Cockpit must survive normal canonical-replica rotation.

The learning bridge retains only the active generation plus one fallback. A
long-lived Cockpit therefore cannot pin the immutable generation that happened
to be current at deployment time; after enough syncs that directory is pruned.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.api import cockpit
from app.opip.learning.canonical_replica import CANONICAL_RELATIVE


def _generation(root: Path, generation_id: str) -> Path:
    db = root / "generations" / generation_id / CANONICAL_RELATIVE
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"replica")
    return db


def test_cockpit_follows_current_after_old_generation_is_pruned(tmp_path, monkeypatch):
    first_id = "gen-first"
    second_id = "gen-second"
    first = _generation(tmp_path, first_id)
    second = _generation(tmp_path, second_id)
    current = tmp_path / "current"

    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))

    current.write_text(first_id + "\n", encoding="utf-8")
    assert cockpit._replica_db_path() == first

    # Normal sync atomically advances current, then retention is free to prune
    # the no-longer-protected old generation.
    current.write_text(second_id + "\n", encoding="utf-8")
    shutil.rmtree(first.parent.parent.parent)

    assert not first.exists()
    assert cockpit._replica_db_path() == second


def test_cockpit_still_accepts_an_explicit_direct_bundle_root(tmp_path, monkeypatch):
    db = tmp_path / CANONICAL_RELATIVE
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"replica")
    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))

    assert cockpit._replica_db_path() == db
