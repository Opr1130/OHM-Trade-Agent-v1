"""ATDD: owner-gated transition away from an unprovable legacy checkpoint."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.opip.discovery.maturation import open_discovery_state
from app.opip.discovery.stream_ingest import load_checkpoint
from app.services.screening_stream_transition import (
    AUTHORIZE_TOKEN,
    TransitionRefused,
    transition_legacy_checkpoint,
)

NOW = datetime(2026, 10, 23, 1, 0, tzinfo=timezone.utc)
STREAM_ID = hashlib.sha256(b"opip:v1:screening_evaluations:epoch").hexdigest()
FRAME_SHA = hashlib.sha256(b"frame").hexdigest()


def _legacy_db(tmp_path: Path) -> Path:
    path = tmp_path / "screening.state.sqlite3"
    connection = open_discovery_state(path)
    try:
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("screening_indexed_offset", "61049583"),
        )
        connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
            ("screening_anchor_sha256", "a" * 64),
        )
        connection.commit()
    finally:
        connection.close()
    return path


def test_transition_requires_explicit_authorization(tmp_path):
    state_db = _legacy_db(tmp_path)
    with pytest.raises(TransitionRefused, match="owner acknowledgement"):
        transition_legacy_checkpoint(
            state_db=state_db,
            lineage_id="LINEAGE-B-V2-GENESIS",
            stream_id=STREAM_ID,
            verified_frame_sha256=FRAME_SHA,
            absolute_offset=1024,
            archive_root=tmp_path / "archive",
            authorize="yes",
            now=NOW,
        )


def test_transition_records_gap_and_sets_framed_checkpoint(tmp_path):
    state_db = _legacy_db(tmp_path)
    receipt = transition_legacy_checkpoint(
        state_db=state_db,
        lineage_id="LINEAGE-B-V2-GENESIS",
        stream_id=STREAM_ID,
        verified_frame_sha256=FRAME_SHA,
        absolute_offset=1024,
        archive_root=tmp_path / "archive",
        authorize=AUTHORIZE_TOKEN,
        now=NOW,
    )
    assert receipt["status"] == "TRANSITIONED"
    assert receipt["lossless_historical_recovery"] is False
    assert receipt["historical_evidence_modified"] is False
    assert receipt["reason"] == "LEGACY_COORDINATE_UNPROVEN"
    assert receipt["legacy_checkpoint"]["screening_indexed_offset"] == "61049583"

    snapshot = Path(receipt["legacy_state_snapshot"])
    assert snapshot.is_file()
    assert hashlib.sha256(snapshot.read_bytes()).hexdigest() == (
        receipt["legacy_state_snapshot_sha256"]
    )

    gaps = list((tmp_path / "archive" / "legacy-coordinate-gaps").glob("*.gap.json"))
    assert len(gaps) == 1
    gap = json.loads(gaps[0].read_text(encoding="utf-8"))
    assert gap["reason"] == "LEGACY_COORDINATE_UNPROVEN"
    assert gap["dataset_eligible"] is False
    assert gap["uncertainty"] == "EXACT_BOUNDARY_UNKNOWN"

    connection = open_discovery_state(state_db)
    try:
        checkpoint = load_checkpoint(connection, lineage_id="LINEAGE-B-V2-GENESIS")
    finally:
        connection.close()
    assert checkpoint is not None
    assert checkpoint["absolute_offset"] == 1024
    assert checkpoint["stream_id"] == STREAM_ID


def test_transition_is_idempotent(tmp_path):
    state_db = _legacy_db(tmp_path)
    kwargs = dict(
        state_db=state_db,
        lineage_id="LINEAGE-B-V2-GENESIS",
        stream_id=STREAM_ID,
        verified_frame_sha256=FRAME_SHA,
        absolute_offset=1024,
        archive_root=tmp_path / "archive",
        authorize=AUTHORIZE_TOKEN,
        now=NOW,
    )
    first = transition_legacy_checkpoint(**kwargs)
    second = transition_legacy_checkpoint(**kwargs)
    assert first["status"] == "TRANSITIONED"
    assert second["status"] in {"TRANSITIONED", "ALREADY_TRANSITIONED"}
    assert second.get("lossless_historical_recovery") is False
    assert len(list((tmp_path / "archive" / "legacy-coordinate-gaps").glob("*.gap.json"))) == 1
