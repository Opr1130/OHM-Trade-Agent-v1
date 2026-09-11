"""Discovery screening checkpoint must survive verified bounded-JSONL rotation.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from app.opip.decision.store import screening_evaluations_archive
from app.opip.discovery import maturation as maturation_mod
from app.opip.discovery.admission import observation_join_id
from app.opip.discovery.maturation import (
    open_discovery_state,
    reconcile_screening_queue,
    _state_int,
    _state_text,
    _set_state_checkpoint,
    _set_state_int,
    _set_state_text,
    _set_hot_generation,
)


NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _screening_row(scan_id: str, symbol: str, *, observed_at: str | None = None) -> dict:
    stamp = observed_at or NOW.isoformat()
    observation_id = observation_join_id(
        scan_id=scan_id,
        scanner_type="BROAD_SEARCH",
        venue_instrument_id=symbol,
        observed_at=stamp,
    )
    return {
        "observed_at": stamp,
        "scan_id": scan_id,
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": symbol,
        "outcome": "ADVANCED",
        "long_score": 85,
        "short_score": 12,
        "metadata": {
            "observation_id": observation_id,
            "production_admission_result": "ADMITTED",
            "production_preferred_direction": "LONG",
            "shortlist_selected": True,
        },
    }


def _write_hot(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as handle:
        for row in rows:
            handle.write(
                (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
            )


def _queue_ids(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute("SELECT observation_id FROM observation_queue")
    }


def _compact_hot(path: Path, *, keep_lines: int = 1, max_bytes: int = 64) -> Path | None:
    archive = screening_evaluations_archive(path)
    # Override bounds for deterministic tiny rotations in tests.
    archive.max_bytes = int(max_bytes)
    archive.keep_lines = int(keep_lines)
    return archive.compact_locked()


def test_normal_append_resumes_from_offset_without_replay(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:A1", "A1USD"),
        _screening_row("SCAN:A2", "A2USD"),
    ]
    _write_hot(hot, rows[:1])
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        offset_after_first = _state_int(state, "screening_indexed_offset", 0)
        assert offset_after_first > 0
        assert _queue_ids(state) == {rows[0]["metadata"]["observation_id"]}

        with hot.open("ab") as handle:
            handle.write(
                (json.dumps(rows[1], sort_keys=True, allow_nan=False) + "\n").encode(
                    "utf-8"
                )
            )
        reconcile_screening_queue(state, hot, now=NOW)
        assert _state_int(state, "screening_indexed_offset", 0) > offset_after_first
        assert _queue_ids(state) == {
            rows[0]["metadata"]["observation_id"],
            rows[1]["metadata"]["observation_id"],
        }
    finally:
        state.close()


def test_verified_rotation_indexes_archived_remainder_and_new_hot(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:R1", "R1USD"),
        _screening_row("SCAN:R2", "R2USD"),
        _screening_row("SCAN:R3", "R3USD"),
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        # Partially consume first row only.
        first_line = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        _set_state_checkpoint(state, hot, "screening", len(first_line))
        _set_hot_generation(state, hot)
        _set_state_text(state, "screening_checkpoint_schema", "generation_v1")
        state.commit()

        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None
        assert hot.stat().st_size < len(first_line) + 20 or hot.stat().st_size < _state_int(
            state, "screening_indexed_offset", 0
        )

        reconcile_screening_queue(state, hot, now=NOW)
        ids = _queue_ids(state)
        assert rows[0]["metadata"]["observation_id"] in ids
        assert rows[1]["metadata"]["observation_id"] in ids
        assert rows[2]["metadata"]["observation_id"] in ids
        assert _state_text(state, "screening_checkpoint_schema") == "generation_v1"
    finally:
        state.close()


def test_rotation_after_fully_consumed_hot_starts_new_generation(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:F1", "F1USD"),
        _screening_row("SCAN:F2", "F2USD"),
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        before = _queue_ids(state)
        assert len(before) == 2
        offset = _state_int(state, "screening_indexed_offset", 0)
        assert offset == hot.stat().st_size

        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None
        new_row = _screening_row("SCAN:F3", "F3USD")
        with hot.open("ab") as handle:
            handle.write(
                (json.dumps(new_row, sort_keys=True, allow_nan=False) + "\n").encode(
                    "utf-8"
                )
            )

        reconcile_screening_queue(state, hot, now=NOW)
        ids = _queue_ids(state)
        assert before.issubset(ids)
        assert new_row["metadata"]["observation_id"] in ids
        # No duplicate queue rows for the same observation_id.
        count = state.execute("SELECT COUNT(*) FROM observation_queue").fetchone()[0]
        assert count == len(ids)
    finally:
        state.close()


def test_repeated_verified_rotations_index_each_observation_once(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row(f"SCAN:M{i}", f"M{i}USD") for i in range(5)
    ]
    _write_hot(hot, rows[:3])
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        _compact_hot(hot, keep_lines=1, max_bytes=16)
        with hot.open("ab") as handle:
            for row in rows[3:4]:
                handle.write(
                    (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                        "utf-8"
                    )
                )
        reconcile_screening_queue(state, hot, now=NOW)
        _compact_hot(hot, keep_lines=1, max_bytes=16)
        with hot.open("ab") as handle:
            handle.write(
                (json.dumps(rows[4], sort_keys=True, allow_nan=False) + "\n").encode(
                    "utf-8"
                )
            )
        reconcile_screening_queue(state, hot, now=NOW)
        ids = _queue_ids(state)
        expected = {row["metadata"]["observation_id"] for row in rows}
        assert ids == expected
        count = state.execute("SELECT COUNT(*) FROM observation_queue").fetchone()[0]
        assert count == len(expected)
    finally:
        state.close()


def test_missing_predecessor_archive_fails_closed(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [_screening_row("SCAN:X1", "X1USD"), _screening_row("SCAN:X2", "X2USD")]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        # Simulate unexplained truncation: shrink HOT without archive/manifest.
        _write_hot(hot, rows[1:])
        with pytest.raises(RuntimeError, match="DISCOVERY_SCREENING_LEDGER_TRUNCATED"):
            reconcile_screening_queue(state, hot, now=NOW)
    finally:
        state.close()


def test_bad_archive_checksum_fails_closed(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:B1", "B1USD"),
        _screening_row("SCAN:B2", "B2USD"),
        _screening_row("SCAN:B3", "B3USD"),
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        first_line = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        _set_state_checkpoint(state, hot, "screening", len(first_line))
        _set_hot_generation(state, hot)
        _set_state_text(state, "screening_checkpoint_schema", "generation_v1")
        state.commit()
        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None
        checksum = archived.with_suffix(archived.suffix + ".sha256")
        checksum.write_text("0" * 64 + f"  {archived.name}\n", encoding="utf-8")
        with pytest.raises(RuntimeError, match="DISCOVERY_SCREENING_ARCHIVE_CHECKSUM"):
            reconcile_screening_queue(state, hot, now=NOW)
    finally:
        state.close()


def test_same_generation_truncation_still_fails_closed(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [_screening_row("SCAN:T1", "T1USD"), _screening_row("SCAN:T2", "T2USD")]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        # Truncate without creating a verified archive generation.
        hot.write_bytes(hot.read_bytes()[:10])
        with pytest.raises(RuntimeError, match="DISCOVERY_SCREENING_LEDGER_TRUNCATED"):
            reconcile_screening_queue(state, hot, now=NOW)
    finally:
        state.close()


def test_same_generation_anchor_divergence_fails_closed(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [_screening_row("SCAN:D1", "D1USD"), _screening_row("SCAN:D2", "D2USD")]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        offset = _state_int(state, "screening_indexed_offset", 0)
        # Rewrite bytes behind the checkpoint without changing size.
        payload = bytearray(hot.read_bytes())
        if offset > 8:
            payload[0] = (payload[0] + 1) % 256
            hot.write_bytes(bytes(payload))
        with pytest.raises(RuntimeError, match="DISCOVERY_SCREENING_LEDGER_DIVERGED"):
            reconcile_screening_queue(state, hot, now=NOW)
    finally:
        state.close()


def test_crash_between_archive_ingest_and_checkpoint_is_idempotent(tmp_path, monkeypatch):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:C1", "C1USD"),
        _screening_row("SCAN:C2", "C2USD"),
        _screening_row("SCAN:C3", "C3USD"),
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        first_line = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        _set_state_checkpoint(state, hot, "screening", len(first_line))
        _set_hot_generation(state, hot)
        _set_state_text(state, "screening_checkpoint_schema", "generation_v1")
        state.commit()
        assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None

        calls = {"n": 0}
        original = maturation_mod._index_hot_from_offset

        def _boom(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated_crash_after_archive")
            return original(*args, **kwargs)

        monkeypatch.setattr(maturation_mod, "_index_hot_from_offset", _boom)
        with pytest.raises(RuntimeError, match="simulated_crash_after_archive"):
            reconcile_screening_queue(state, hot, now=NOW)

        monkeypatch.setattr(maturation_mod, "_index_hot_from_offset", original)
        reconcile_screening_queue(state, hot, now=NOW)
        ids = _queue_ids(state)
        assert {row["metadata"]["observation_id"] for row in rows} == ids
        count = state.execute("SELECT COUNT(*) FROM observation_queue").fetchone()[0]
        assert count == len(ids)
    finally:
        state.close()


def test_existing_offset_only_state_migrates_safely(tmp_path):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [_screening_row("SCAN:MIG", "MIGUSD")]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        # Plant legacy offset-only checkpoint (pre-generation schema) at zero.
        _set_state_int(state, "screening_indexed_offset", 0)
        _set_state_int(state, "screening_anchor_start", 0)
        _set_state_int(state, "screening_anchor_size", 0)
        _set_state_text(state, "screening_anchor_sha256", "")
        state.commit()

        reconcile_screening_queue(state, hot, now=NOW)
        assert _state_text(state, "screening_checkpoint_schema") == "generation_v1"
        assert _state_int(state, "screening_hot_offset", -1) >= 0
        assert rows[0]["metadata"]["observation_id"] in _queue_ids(state)
    finally:
        state.close()


def test_archive_indexing_is_streaming_not_read_text(tmp_path, monkeypatch):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:S1", "S1USD"),
        _screening_row("SCAN:S2", "S2USD"),
        _screening_row("SCAN:S3", "S3USD"),
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        first_line = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        _set_state_checkpoint(state, hot, "screening", len(first_line))
        _set_hot_generation(state, hot)
        _set_state_text(state, "screening_checkpoint_schema", "generation_v1")
        state.commit()
        assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None

        original = Path.read_text

        def guarded(self, *args, **kwargs):
            name = str(self)
            if name.endswith(".jsonl") or name.endswith(".jsonl.gz"):
                raise AssertionError(f"unbounded Path.read_text for JSONL: {self}")
            return original(self, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", guarded)
        reconcile_screening_queue(state, hot, now=NOW)
        assert len(_queue_ids(state)) == 3
    finally:
        state.close()
