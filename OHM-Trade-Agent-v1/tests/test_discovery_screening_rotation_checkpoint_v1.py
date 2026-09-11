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
from app.opip.discovery.constants import DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE
from app.opip.discovery.maturation import (
    open_discovery_state,
    reconcile_screening_queue,
    mature_discovery_outcomes_bounded,
    _list_manifest_segment_rows,
    _load_recovery_cursor,
    _state_int,
    _state_json_list,
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
        # Consume first row for real, then rotate.
        first_line = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        hot.write_bytes(first_line)
        reconcile_screening_queue(state, hot, now=NOW)
        assert rows[0]["metadata"]["observation_id"] in _queue_ids(state)
        with hot.open("ab") as handle:
            for row in rows[1:]:
                handle.write(
                    (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                        "utf-8"
                    )
                )

        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None

        reconcile_screening_queue(state, hot, now=NOW)
        ids = _queue_ids(state)
        assert rows[0]["metadata"]["observation_id"] in ids
        assert rows[1]["metadata"]["observation_id"] in ids
        assert rows[2]["metadata"]["observation_id"] in ids
        assert _state_text(state, "screening_checkpoint_schema") == "generation_v2"
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
        _set_hot_generation(state, hot, durable_offset=len(first_line))
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


def test_same_generation_anchor_divergence_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(maturation_mod, "DISCOVERY_HOT_GENERATION_PREFIX_BYTES", 64)
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row(f"SCAN:D{i}", f"D{i}USD") for i in range(8)
    ]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        offset = _state_int(state, "screening_indexed_offset", 0)
        gen_bytes = _state_int(state, "screening_hot_generation_bytes", 0)
        payload = bytearray(hot.read_bytes())
        corrupt_at = min(offset - 1, gen_bytes + 8)
        assert corrupt_at >= gen_bytes
        payload[corrupt_at] = (payload[corrupt_at] + 1) % 256
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
    _write_hot(hot, rows[:1])
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        with hot.open("ab") as handle:
            for row in rows[1:]:
                handle.write(
                    (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                        "utf-8"
                    )
                )
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
        assert _state_text(state, "screening_checkpoint_schema") == "generation_v2"
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
    _write_hot(hot, rows[:1])
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        with hot.open("ab") as handle:
            for row in rows[1:]:
                handle.write(
                    (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                        "utf-8"
                    )
                )
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


def _manifest_shas(hot: Path) -> list[str]:
    archive = screening_evaluations_archive(hot)
    return [str(row["sha256"]) for row in _list_manifest_segment_rows(archive)]


def test_legacy_suffix_recovery_ignores_pre_checkpoint_archives(tmp_path):
    """A0/A1 exist before checkpoint; only A2 (post-checkpoint) + HOT recover."""
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    a0 = _screening_row("SCAN:A0", "A0USD")
    a1 = _screening_row("SCAN:A1", "A1USD")
    keep = _screening_row("SCAN:KEEP", "KEEPUSD")
    cp = _screening_row("SCAN:CP", "CPUSD")
    post = _screening_row("SCAN:POST", "POSTUSD")

    _write_hot(hot, [a0, keep])
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    shas_after_a0 = _manifest_shas(hot)
    assert len(shas_after_a0) == 1
    a0_sha = shas_after_a0[0]

    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(a1, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    shas_after_a1 = _manifest_shas(hot)
    assert len(shas_after_a1) == 2
    a1_sha = [s for s in shas_after_a1 if s != a0_sha][0]

    # Build checkpointed HOT content that will become A2.
    _write_hot(hot, [cp, post])
    cp_line = (json.dumps(cp, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        # Legacy offset-only state: no generation schema / known archives.
        _set_state_checkpoint(state, hot, "screening", len(cp_line))
        state.commit()
        assert _state_text(state, "screening_checkpoint_schema") is None

        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None
        shas = _manifest_shas(hot)
        assert len(shas) == 3
        a2_sha = [s for s in shas if s not in {a0_sha, a1_sha}][0]

        stats = reconcile_screening_queue(state, hot, now=NOW)
        assert (
            stats.archive_stats["archive_segments_expensive_unique"]
            <= DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE
        )
        assert stats.recovery_pending is False
        assert stats.continuity_proven is True
        ids = _queue_ids(state)
        # Offset already advanced past cp; only post-checkpoint remainder is queued.
        assert cp["metadata"]["observation_id"] not in ids
        assert post["metadata"]["observation_id"] in ids
        assert a0["metadata"]["observation_id"] not in ids
        assert a1["metadata"]["observation_id"] not in ids
        consumed = set(_state_json_list(state, "screening_consumed_archives"))
        assert a2_sha in consumed
        assert a0_sha not in consumed
        assert a1_sha not in consumed
    finally:
        state.close()


def test_warm_to_cold_identity_does_not_look_like_new_rotation(tmp_path):
    """Checkpoint while WARM → move to COLD → real rotation recovers only the new archive."""
    import os
    import shutil

    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:W1", "W1USD"),
        _screening_row("SCAN:W2", "W2USD"),
        _screening_row("SCAN:W3", "W3USD"),
    ]
    _write_hot(hot, rows[:1])
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        with hot.open("ab") as handle:
            for row in rows[1:]:
                handle.write(
                    (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                        "utf-8"
                    )
                )

        warm_archive = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert warm_archive is not None
        warm_sha = _manifest_shas(hot)[0]
        warm_rel = _list_manifest_segment_rows(screening_evaluations_archive(hot))[0][
            "relative_path"
        ]

        reconcile_screening_queue(state, hot, now=NOW)
        assert warm_sha in set(_state_json_list(state, "screening_consumed_archives"))
        ids_after_warm = _queue_ids(state)

        archive = screening_evaluations_archive(hot)
        from app.opip.discovery.maturation import _resolve_segment_path

        current_rel = _list_manifest_segment_rows(archive)[0]["relative_path"]
        current_path = _resolve_segment_path(archive, current_rel)
        checksum = current_path.with_suffix(current_path.suffix + ".sha256")
        # Simulate WARM→COLD without Windows MAX_PATH tempfile prefixes.
        cold_segment = archive.cold_archive_dir / "c" / "s"
        cold_segment.mkdir(parents=True, exist_ok=True)
        dest = cold_segment / current_path.name
        shutil.copy2(current_path, dest)
        shutil.copy2(checksum, dest.with_suffix(dest.suffix + ".sha256"))
        verification = archive.verify_archive_file(dest, tier="COLD")
        assert verification.sha256 == warm_sha
        archive.update_manifest_locked(verification)
        current_path.unlink()
        checksum.unlink()

        cold_rows = _list_manifest_segment_rows(archive)
        assert len(cold_rows) == 1
        assert cold_rows[0]["sha256"] == warm_sha
        assert cold_rows[0]["relative_path"] != warm_rel
        assert "cold" in str(cold_rows[0]["relative_path"]).replace("\\", "/")

        reconcile_screening_queue(state, hot, now=NOW)
        assert set(_state_json_list(state, "screening_consumed_archives")) == {warm_sha}
        assert _queue_ids(state) == ids_after_warm

        new_row = _screening_row("SCAN:W4", "W4USD")
        with hot.open("ab") as handle:
            handle.write(
                (json.dumps(new_row, sort_keys=True, allow_nan=False) + "\n").encode(
                    "utf-8"
                )
            )
        new_archive = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert new_archive is not None
        shas = _manifest_shas(hot)
        assert len(shas) == 2
        new_sha = [s for s in shas if s != warm_sha][0]

        reconcile_screening_queue(state, hot, now=NOW)
        consumed = set(_state_json_list(state, "screening_consumed_archives"))
        assert warm_sha in consumed
        assert new_sha in consumed
        assert new_row["metadata"]["observation_id"] in _queue_ids(state)
    finally:
        state.close()


def test_manifest_growth_preserves_recovery_progress(tmp_path, monkeypatch):
    """Budget 1 across 3+ segment recovery; mid-flight archive extends cursor only."""
    import gzip
    import hashlib
    import os

    monkeypatch.setattr(
        maturation_mod, "DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE", 1
    )
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    parts = [_screening_row(f"SCAN:G{i}", f"G{i}USD") for i in range(6)]
    _write_hot(hot, parts[0:2])
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(parts[2], sort_keys=True, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(parts[3], sort_keys=True, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    assert len(_manifest_shas(hot)) == 3

    archive = screening_evaluations_archive(hot)
    rows = _list_manifest_segment_rows(archive)
    from app.opip.discovery.maturation import _decompressed_size, _resolve_segment_path

    virtual = b""
    for row in rows:
        path = _resolve_segment_path(archive, str(row["relative_path"]))
        with gzip.open(path, "rb") as handle:
            virtual += handle.read()
    virtual += hot.read_bytes()
    offset = len(virtual)
    anchor = virtual[-32:]
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        _set_state_int(state, "screening_indexed_offset", offset)
        _set_state_int(state, "screening_anchor_start", offset - len(anchor))
        _set_state_int(state, "screening_anchor_size", len(anchor))
        _set_state_text(
            state, "screening_anchor_sha256", hashlib.sha256(anchor).hexdigest()
        )
        state.commit()

        progress_sizes: list[int] = []
        stats1 = reconcile_screening_queue(state, hot, now=NOW)
        assert stats1.archive_stats["archive_segments_expensive_unique"] <= 1
        assert stats1.recovery_pending is True
        assert stats1.continuity_proven is False
        cursor1 = _load_recovery_cursor(state)
        progress_sizes.append(len(cursor1.get("segment_sizes") or {}))
        assert cursor1.get("active_end_sha")
        active_end = str(cursor1["active_end_sha"])
        assert len(cursor1.get("sequence") or []) == 3

        # Inject an extra verified archive into the manifest without touching HOT.
        extra_row = _screening_row("SCAN:GX", "GXUSD")
        payload = (json.dumps(extra_row, sort_keys=True, allow_nan=False) + "\n").encode(
            "utf-8"
        )
        extra_name = "screening_evaluations-20990101T000000Z-extra.jsonl.gz"
        extra_path = archive.archive_dir / extra_name
        with gzip.open(extra_path, "wb") as handle:
            handle.write(payload)
        digest = archive._sha256_file(extra_path)
        checksum = extra_path.with_suffix(extra_path.suffix + ".sha256")
        checksum.write_text(f"{digest}  {extra_name}\n", encoding="utf-8")
        verification = archive.verify_archive_file(extra_path, tier="WARM")
        archive.update_manifest_locked(verification)
        assert len(_manifest_shas(hot)) == 4

        stats2 = reconcile_screening_queue(state, hot, now=NOW)
        assert stats2.archive_stats["archive_segments_expensive_unique"] <= 1
        assert stats2.recovery_pending is True
        cursor2 = _load_recovery_cursor(state)
        progress_sizes.append(len(cursor2.get("segment_sizes") or {}))
        assert cursor2.get("active_end_sha") == active_end
        assert len(cursor2.get("sequence") or []) == 4
        assert progress_sizes[-1] >= progress_sizes[0]

        for _ in range(10):
            stats = reconcile_screening_queue(state, hot, now=NOW)
            assert stats.archive_stats["archive_segments_expensive_unique"] <= 1
            cursor = _load_recovery_cursor(state)
            n_sizes = len(cursor.get("segment_sizes") or {})
            if not stats.recovery_pending and stats.continuity_proven:
                progress_sizes.append(n_sizes)
                break
            assert n_sizes >= progress_sizes[-1]
            progress_sizes.append(n_sizes)
        else:
            raise AssertionError("recovery did not complete under budget=1")

        assert not _load_recovery_cursor(state).get("sequence")
        assert _state_text(state, "screening_checkpoint_schema") == "generation_v2"
        assert _state_int(state, "screening_indexed_offset", 0) == hot.stat().st_size
    finally:
        state.close()


def test_recovery_pending_surfaces_through_outcomes_cycle(tmp_path, monkeypatch):
    """Bounded recovery must be ERROR / FAILED_RETRYABLE, not silent OK."""
    import gzip
    import hashlib

    import app.jobs.run_opportunity_intelligence_cycle as cycle
    import app.opip.learning.job_disposition as jd

    monkeypatch.setattr(
        maturation_mod, "DISCOVERY_SCREENING_ARCHIVE_SEGMENTS_PER_CYCLE", 1
    )
    monkeypatch.setattr(cycle, "_DEFAULT_DATA_ROOT", tmp_path)

    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    parts = [_screening_row(f"SCAN:P{i}", f"P{i}USD") for i in range(6)]
    _write_hot(hot, parts[0:2])
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(parts[2], sort_keys=True, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(parts[3], sort_keys=True, allow_nan=False) + "\n").encode(
                "utf-8"
            )
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None

    archive = screening_evaluations_archive(hot)
    rows = _list_manifest_segment_rows(archive)
    from app.opip.discovery.maturation import _resolve_segment_path

    virtual = b""
    for row in rows:
        path = _resolve_segment_path(archive, str(row["relative_path"]))
        with gzip.open(path, "rb") as handle:
            virtual += handle.read()
    virtual += hot.read_bytes()
    offset = len(virtual)
    anchor = virtual[-32:]
    state = open_discovery_state(
        tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3"
    )
    try:
        _set_state_int(state, "screening_indexed_offset", offset)
        _set_state_int(state, "screening_anchor_start", offset - len(anchor))
        _set_state_int(state, "screening_anchor_size", len(anchor))
        _set_state_text(
            state, "screening_anchor_sha256", hashlib.sha256(anchor).hexdigest()
        )
        state.commit()
    finally:
        state.close()

    (tmp_path / "full_market_observations.jsonl").write_text("", encoding="utf-8")

    monkeypatch.setattr(
        cycle,
        "advance_accountability_handoff_backfill",
        lambda **kwargs: {
            "batch_rows": 0,
            "enqueued_handoff": 0,
            "terminalized_coverage_discontinuity": 0,
            "complete": True,
            "already_complete": True,
        },
    )
    monkeypatch.setattr(cycle, "pending_accountability_outcomes", lambda: [])
    monkeypatch.setattr(cycle, "build_outcomes_bounded", lambda: [])
    monkeypatch.setattr(
        cycle,
        "build_incremental_from_outcomes",
        lambda outcomes, replica_mode=True: {
            "population": {},
            "opportunity_capture_rate_pct": None,
        },
    )
    monkeypatch.setattr(cycle, "resolved_accountability_outcomes", lambda outcomes: [])
    monkeypatch.setattr(cycle, "acknowledge_accountability_outcomes", lambda resolved: 0)

    # Cycle 1 — recovery pending.
    cycle.main()
    summary = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary is not None
    assert summary["status"] == "ERROR"
    assert summary["discovery_job_status"] == "ERROR"
    assert summary["disposition"] == jd.FAILED_RETRYABLE
    assert summary["discovery_failure_nonfatal"] is True
    assert (
        summary["discovery_outcomes"]["error"]
        == "DISCOVERY_SCREENING_ROTATION_RECOVERY_PENDING"
    )
    assert summary["discovery_outcomes"]["rotation_recovery_pending"] is True

    # Drain recovery under budget=1 until continuity proven.
    for _ in range(12):
        result = mature_discovery_outcomes_bounded(
            screening_path=hot,
            observation_path=tmp_path / "full_market_observations.jsonl",
            output_dir=tmp_path / "opip/discovery",
            now=NOW,
        )
        if not result.get("rotation_recovery_pending"):
            assert "error" not in result
            break
    else:
        raise AssertionError("recovery never cleared")

    cycle.main()
    summary2 = jd.read_consumption_summary(tmp_path, "outcomes")
    assert summary2 is not None
    assert summary2["status"] == "OK"
    assert summary2["discovery_job_status"] == "OK"
    assert summary2["disposition"] in {jd.CONSUMED_OK, jd.CONSUMED_EMPTY}
    assert not (summary2.get("discovery_outcomes") or {}).get(
        "rotation_recovery_pending"
    )


def test_partial_jsonl_tail_repair_keeps_checkpoint_generation(tmp_path):
    from app.opip.storage.bounded_jsonl import repair_truncated_tail

    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [
        _screening_row("SCAN:TA", "TAUSD"),
        _screening_row("SCAN:TB", "TBUSD"),
    ]
    complete = b"".join(
        (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        for row in rows
    )
    hot.parent.mkdir(parents=True, exist_ok=True)
    hot.write_bytes(complete + b'{"partial": true')  # no newline

    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        result = reconcile_screening_queue(state, hot, now=NOW)
        assert result.continuity_proven is True
        assert result.recovery_pending is False
        offset = _state_int(state, "screening_indexed_offset", 0)
        assert offset == len(complete)
        assert _queue_ids(state) == {
            rows[0]["metadata"]["observation_id"],
            rows[1]["metadata"]["observation_id"],
        }
        gen_bytes = _state_int(state, "screening_hot_generation_bytes", 0)
        assert gen_bytes == min(offset, 65536)
        assert gen_bytes <= offset

        repair_truncated_tail(hot)
        assert hot.read_bytes() == complete

        result2 = reconcile_screening_queue(state, hot, now=NOW)
        assert result2.recovery_pending is False
        assert result2.continuity_proven is True
        assert _state_int(state, "screening_indexed_offset", 0) == len(complete)

        new_row = _screening_row("SCAN:TC", "TCUSD")
        with hot.open("ab") as handle:
            handle.write(
                (json.dumps(new_row, sort_keys=True, allow_nan=False) + "\n").encode(
                    "utf-8"
                )
            )
        result3 = reconcile_screening_queue(state, hot, now=NOW)
        assert result3.recovery_pending is False
        assert new_row["metadata"]["observation_id"] in _queue_ids(state)
    finally:
        state.close()


def test_ambiguous_archive_generation_filename_fails_closed(tmp_path):
    import gzip

    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    rows = [_screening_row("SCAN:AG", "AGUSD")]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        archive = screening_evaluations_archive(hot)
        archive.archive_dir.mkdir(parents=True, exist_ok=True)
        bad_name = "screening_evaluations-notastamp-deadbeef.jsonl.gz"
        bad_path = archive.archive_dir / bad_name
        payload = (
            json.dumps(rows[0], sort_keys=True, allow_nan=False) + "\n"
        ).encode("utf-8")
        with gzip.open(bad_path, "wb") as handle:
            handle.write(payload)
        digest = archive._sha256_file(bad_path)
        bad_path.with_suffix(bad_path.suffix + ".sha256").write_text(
            f"{digest}  {bad_name}\n", encoding="utf-8"
        )
        verification = archive.verify_archive_file(bad_path, tier="WARM")
        archive.update_manifest_locked(verification)
        with pytest.raises(
            RuntimeError, match="DISCOVERY_SCREENING_ARCHIVE_GENERATION_AMBIGUOUS"
        ):
            reconcile_screening_queue(state, hot, now=NOW)
    finally:
        state.close()


def test_archive_physical_order_ignores_observed_at(tmp_path):
    """Later physical generation stays after earlier even if observed_at goes backward."""
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    early_obs = "2026-09-11T12:00:00+00:00"
    late_obs = "2026-09-11T11:30:00+00:00"
    g1 = _screening_row("SCAN:O1", "O1USD", observed_at=early_obs)
    keep = _screening_row("SCAN:OK", "OKUSD", observed_at=early_obs)
    g2 = _screening_row("SCAN:O2", "O2USD", observed_at=late_obs)

    _write_hot(hot, [g1, keep])
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    with hot.open("ab") as handle:
        handle.write(
            (json.dumps(g2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        )
    assert _compact_hot(hot, keep_lines=1, max_bytes=16) is not None
    rows = _list_manifest_segment_rows(screening_evaluations_archive(hot))
    assert len(rows) == 2
    assert rows[0]["generation_timestamp"] <= rows[1]["generation_timestamp"]
    # Physical order is filename stamp order, not observed_at (12:00 then 11:30).
    assert "12:00" in g1["observed_at"]
    assert "11:30" in g2["observed_at"]


def test_hot_generation_lineage_detects_compaction_prefix_collision(tmp_path, monkeypatch):
    """Manifest-head lineage changes on compaction even if HOT prefix bytes collide."""
    monkeypatch.setattr(maturation_mod, "DISCOVERY_HOT_GENERATION_PREFIX_BYTES", 32)
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    # Craft many identical-prefix-friendly rows then rotate so retained HOT can
    # share leading bytes with an earlier generation fingerprint window.
    rows = [_screening_row(f"SCAN:L{i}", f"L{i}USD") for i in range(4)]
    _write_hot(hot, rows)
    state = open_discovery_state(tmp_path / "opip/discovery/.forward_outcomes.jsonl.state.sqlite3")
    try:
        reconcile_screening_queue(state, hot, now=NOW)
        head_before = _state_text(state, "screening_hot_generation_manifest_head_sha")
        assert head_before == ""
        offset_before = _state_int(state, "screening_indexed_offset", 0)
        assert offset_before == hot.stat().st_size

        archived = _compact_hot(hot, keep_lines=1, max_bytes=16)
        assert archived is not None
        head_after_archive = _manifest_shas(hot)[-1]
        assert head_after_archive != ""

        # Without lineage, a colliding prefix could look like same generation.
        # With lineage, compacting published a new manifest head → recovery path.
        result = reconcile_screening_queue(state, hot, now=NOW)
        assert result.recovery_pending is False
        assert result.continuity_proven is True
        head_stored = _state_text(state, "screening_hot_generation_manifest_head_sha")
        assert head_stored == head_after_archive
        assert head_stored != head_before
    finally:
        state.close()
