"""Regression: Cockpit survives same-release replica rotation and rejects drift.

The learning bridge retains only the active generation plus one fallback. A
long-lived Cockpit cannot pin the immutable generation that was current at
deployment time. It may follow `current` only while the selected generation is
bound to the Cockpit release, so an independently advanced production release
fails closed until the Cockpit rollout catches up.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from app.api import cockpit
from app.opip.learning.canonical_replica import CANONICAL_RELATIVE, MANIFEST_FILENAME

RELEASE_A = "a" * 40
RELEASE_B = "b" * 40


def _generation(root: Path, generation_id: str, release_sha: str) -> Path:
    generation = root / "generations" / generation_id
    db = generation / CANONICAL_RELATIVE
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"replica")
    (generation / MANIFEST_FILENAME).write_text(
        json.dumps({"source_release_sha": release_sha}) + "\n",
        encoding="utf-8",
    )
    return db


def test_cockpit_follows_same_release_current_after_old_generation_is_pruned(
    tmp_path, monkeypatch
):
    first_id = "gen-first"
    second_id = "gen-second"
    first = _generation(tmp_path, first_id, RELEASE_A)
    second = _generation(tmp_path, second_id, RELEASE_A)
    current = tmp_path / "current"

    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))
    monkeypatch.setenv("OPIP_COCKPIT_RELEASE_SHA", RELEASE_A)

    current.write_text(first_id + "\n", encoding="utf-8")
    assert cockpit._replica_db_path() == first

    # Normal sync advances current atomically. Retention may then prune the old
    # generation; the long-lived Cockpit follows the new same-release generation.
    current.write_text(second_id + "\n", encoding="utf-8")
    shutil.rmtree(first.parent.parent.parent)

    assert not first.exists()
    assert cockpit._replica_db_path() == second


def test_cockpit_rejects_current_from_a_different_release(tmp_path, monkeypatch):
    old = _generation(tmp_path, "gen-old", RELEASE_A)
    new = _generation(tmp_path, "gen-new-release", RELEASE_B)
    current = tmp_path / "current"

    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))
    monkeypatch.setenv("OPIP_COCKPIT_RELEASE_SHA", RELEASE_A)

    current.write_text("gen-old\n", encoding="utf-8")
    assert cockpit._replica_db_path() == old

    # Production/learning may advance independently. The old Cockpit must not
    # consume a generation that was never release-bound to its own image.
    current.write_text("gen-new-release\n", encoding="utf-8")
    assert new.is_file()
    assert cockpit._replica_db_path() is None


def test_repository_mode_fails_closed_without_cockpit_release_binding(
    tmp_path, monkeypatch
):
    _generation(tmp_path, "gen-current", RELEASE_A)
    (tmp_path / "current").write_text("gen-current\n", encoding="utf-8")
    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))
    monkeypatch.delenv("OPIP_COCKPIT_RELEASE_SHA", raising=False)

    assert cockpit._replica_db_path() is None


def test_cockpit_still_accepts_an_explicit_direct_bundle_root(tmp_path, monkeypatch):
    db = tmp_path / CANONICAL_RELATIVE
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"replica")
    monkeypatch.setenv("OPIP_CANONICAL_REPLICA_ROOT", str(tmp_path))
    monkeypatch.delenv("OPIP_COCKPIT_RELEASE_SHA", raising=False)

    assert cockpit._replica_db_path() == db
