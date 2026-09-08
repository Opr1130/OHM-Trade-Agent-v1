"""Legacy coverage discontinuity + post-boundary HOT-only learning regressions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from app.opip.decision.store import screening_evaluations_archive
from app.opip.learning.coverage_discontinuity import (
    DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY,
    WARNING_POST_BOUNDARY,
    WARNING_PRE_BOUNDARY,
    coverage_epoch_path,
    establish_coverage_discontinuity_epoch,
    load_coverage_epoch,
    oneshot_consumed_path,
)
from app.opip.learning.empty_export_attestation import (
    EMPTY_EXPORT_ATTESTATION_FILENAME,
)
from app.opip.learning.replica_archive_repair import (
    reconcile_qualification_replica_archives,
)
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_ARCHIVE_WINDOW_PAD,
    _iter_windowed_jsonl_sources,
    _window_archive_selection,
    build_incremental_from_outcomes,
)


NOW = datetime(2026, 9, 8, 14, 44, 43, tzinfo=timezone.utc)
BOUNDARY = NOW
PRE = BOUNDARY - timedelta(hours=2)
POST = BOUNDARY + timedelta(hours=2)
PROD_SHA = "75c469f1d84d2963e89c7d7c76b538ca8cfedd74"


def _orphan_incomplete_empty_index_state() -> dict:
    return {
        "schema_version": 1,
        "manifest_present": False,
        "manifest_size": 0,
        "manifest_mtime_ns": 0,
        "manifest_sha256": "",
        "complete": False,
        "coverage_start_day": None,
        "coverage_through_day": None,
        "coverage_day_count": 0,
        "shard_sha256": {},
        "updated_at_utc": "2026-09-03T05:14:33.012395+00:00",
    }


def _write_orphan_incomplete_empty_index(archive) -> bytes:
    archive.window_index_dir.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_orphan_incomplete_empty_index_state(), sort_keys=True) + "\n"
    archive.window_index_state_file.write_text(payload, encoding="utf-8")
    return archive.window_index_state_file.read_bytes()


def _write_manifest_env(data_root: Path, *, exported_at: str = NOW.isoformat()) -> None:
    (data_root / "manifest.env").write_text(
        f"production_deployed_sha={PROD_SHA}\n"
        f"exported_at_utc={exported_at}\n"
        "schema_version=4\n",
        encoding="utf-8",
    )


def _plant_condition_c(tmp_path: Path, *, hot_text: str = "{}\n"):
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    archive = screening_evaluations_archive(hot)
    archive.data_file.parent.mkdir(parents=True, exist_ok=True)
    archive.data_file.write_text(hot_text, encoding="utf-8")
    state_bytes = _write_orphan_incomplete_empty_index(archive)
    _write_manifest_env(tmp_path)
    return archive, state_bytes


def _establish(tmp_path: Path, archive, state_bytes: bytes):
    return establish_coverage_discontinuity_epoch(
        tmp_path,
        archive,
        expected_legacy_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
        boundary_at_utc=BOUNDARY,
        production_deployed_sha=PROD_SHA,
        exported_at_utc=NOW.isoformat(),
    )


def test_hot_present_complete_false_without_epoch_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, before = _plant_condition_c(tmp_path)

    with pytest.raises(RuntimeError, match="LEGACY_COVERAGE_DISCONTINUITY_REQUIRED"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert not coverage_epoch_path(tmp_path).exists()


def test_hot_present_complete_false_must_not_enter_empty_attestation(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, before = _plant_condition_c(tmp_path)
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    planted = {
        "schema_version": 1,
        "kind": "empty_export_attestation_v1",
        "archive_prefix": archive.archive_prefix,
        "hot_bytes": 0,
        "segment_count": 0,
        "manifest_present": False,
        "signature_present": False,
        "exported_at_utc": NOW.isoformat(),
        "production_deployed_sha": PROD_SHA,
    }
    (archive.archive_dir / EMPTY_EXPORT_ATTESTATION_FILENAME).write_text(
        json.dumps(planted, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="LEGACY_COVERAGE_DISCONTINUITY_REQUIRED"):
        reconcile_qualification_replica_archives(tmp_path)

    assert archive.window_index_state_file.read_bytes() == before
    assert not archive._window_index_state_proves_empty_archive_without_manifest()


def test_valid_epoch_returns_discontinuity_without_mutating_state(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, before = _plant_condition_c(tmp_path)
    _establish(tmp_path, archive, before)

    result = reconcile_qualification_replica_archives(tmp_path)

    assert result["screening"] == DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY
    assert archive.window_index_state_file.read_bytes() == before
    assert not archive.manifest_file.exists()
    assert not archive.manifest_signature_file.exists()


def test_legacy_state_bytes_unchanged_before_and_after_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, before = _plant_condition_c(tmp_path)
    digest_before = hashlib.sha256(before).hexdigest()
    _establish(tmp_path, archive, before)
    reconcile_qualification_replica_archives(tmp_path)
    after = archive.window_index_state_file.read_bytes()
    assert after == before
    assert hashlib.sha256(after).hexdigest() == digest_before


def test_window_entirely_after_epoch_allows_hot_only(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    post_row = {
        "observed_at": POST.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
        "scan_id": "scan-post",
        "long_score": 81.0,
    }
    archive, state = _plant_condition_c(
        tmp_path, hot_text=json.dumps(post_row) + "\n"
    )
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    selected = _window_archive_selection(
        archive.data_file,
        archive_dir=archive.archive_dir,
        start=POST - timedelta(minutes=5),
        through=POST + timedelta(minutes=5),
        kind="screening",
        replica_mode=True,
    )
    assert selected is not None
    _archive, selection = selected
    assert selection.complete is True
    assert selection.paths == ()
    assert WARNING_POST_BOUNDARY in selection.warnings

    rows = list(
        _iter_windowed_jsonl_sources(
            archive.data_file,
            archive_dir=archive.archive_dir,
            start=POST - timedelta(minutes=5),
            through=POST + timedelta(minutes=5),
            kind="screening",
            replica_mode=True,
        )
    )
    assert len(rows) == 1
    assert rows[0]["scan_id"] == "scan-post"


def test_window_starts_before_epoch_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    selected = _window_archive_selection(
        archive.data_file,
        archive_dir=archive.archive_dir,
        start=PRE,
        through=PRE + timedelta(minutes=30),
        kind="screening",
        replica_mode=True,
    )
    assert selected is not None
    _archive, selection = selected
    assert selection.complete is False
    assert WARNING_PRE_BOUNDARY in selection.warnings


def test_window_straddling_epoch_is_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    selected = _window_archive_selection(
        archive.data_file,
        archive_dir=archive.archive_dir,
        start=BOUNDARY - timedelta(minutes=30),
        through=BOUNDARY + timedelta(minutes=30),
        kind="screening",
        replica_mode=True,
    )
    assert selected is not None
    _archive, selection = selected
    assert selection.complete is False
    assert WARNING_PRE_BOUNDARY in selection.warnings


def test_pre_boundary_hot_row_never_becomes_post_boundary_learning_evidence(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    pre_row = {
        "observed_at": PRE.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "ETHUSD",
        "scan_id": "scan-pre",
        "long_score": 90.0,
    }
    post_row = {
        "observed_at": POST.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
        "scan_id": "scan-post",
        "long_score": 88.0,
    }
    archive, state = _plant_condition_c(
        tmp_path,
        hot_text=json.dumps(pre_row) + "\n" + json.dumps(post_row) + "\n",
    )
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    rows = list(
        _iter_windowed_jsonl_sources(
            archive.data_file,
            archive_dir=archive.archive_dir,
            start=POST - timedelta(minutes=5),
            through=POST + timedelta(minutes=5),
            kind="screening",
            replica_mode=True,
        )
    )
    assert [row["scan_id"] for row in rows] == ["scan-post"]


def test_no_lookahead_row_after_through_is_not_consumed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    through = POST + timedelta(minutes=10)
    in_window = {
        "observed_at": (through - timedelta(minutes=1)).isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
        "scan_id": "scan-in",
    }
    after = {
        "observed_at": (through + timedelta(minutes=1)).isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
        "scan_id": "scan-after",
    }
    archive, state = _plant_condition_c(
        tmp_path,
        hot_text=json.dumps(in_window) + "\n" + json.dumps(after) + "\n",
    )
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    rows = list(
        _iter_windowed_jsonl_sources(
            archive.data_file,
            archive_dir=archive.archive_dir,
            start=POST,
            through=through,
            kind="screening",
            replica_mode=True,
        )
    )
    assert [row["scan_id"] for row in rows] == ["scan-in"]


def test_pre_boundary_outcomes_unresolved_no_accepted_learning(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    # Funnel archive must also reconcile under replica mode defaults.
    funnel_hot = tmp_path / "opip/qualification/funnel_events.jsonl"
    funnel_hot.write_text("", encoding="utf-8")

    reference = PRE + ACCOUNTABILITY_ARCHIVE_WINDOW_PAD
    outcomes = [
        {
            "outcome_record_id": "out-pre",
            "snapshot_id": "snap-pre",
            "symbol": "BTCUSD",
            "reference_at": reference.isoformat(),
            "reference_price": 100.0,
            "canonical_episode_id": "ep-pre",
        }
    ]
    summary = build_incremental_from_outcomes(
        outcomes,
        screening_path=archive.data_file,
        screening_archive=archive.archive_dir,
        funnel_path=funnel_hot,
        funnel_archive=funnel_hot.parent / "funnel_events_archive",
        intelligence_event_path=tmp_path / "intelligence_learning/events.jsonl",
        ledger_path=tmp_path / "opip/opportunity_accountability.jsonl",
        summary_path=tmp_path / "opip/opportunity_accountability_summary.json",
        state_path=tmp_path / "opip/opportunity_accountability.state.sqlite3",
        replica_mode=True,
    )
    batch = summary["batch_disposition"]
    assert batch["accepted"] == 0
    assert batch["unresolved_coverage_discontinuity"] == 1


def test_post_boundary_outcomes_process_normally(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    reference = POST + ACCOUNTABILITY_ARCHIVE_WINDOW_PAD
    screening_row = {
        "observed_at": reference.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
        "scan_id": "scan-post",
        "long_score": 85.0,
        "snapshot_id": "snap-post",
    }
    archive, state = _plant_condition_c(
        tmp_path, hot_text=json.dumps(screening_row) + "\n"
    )
    _establish(tmp_path, archive, state)
    reconcile_qualification_replica_archives(tmp_path)

    funnel_hot = tmp_path / "opip/qualification/funnel_events.jsonl"
    funnel_hot.write_text("", encoding="utf-8")
    (funnel_hot.parent / "funnel_events_archive").mkdir(parents=True, exist_ok=True)

    outcomes = [
        {
            "outcome_record_id": "out-post",
            "snapshot_id": "snap-post",
            "symbol": "BTCUSD",
            "reference_at": reference.isoformat(),
            "reference_price": 100.0,
            "canonical_episode_id": "ep-post",
        }
    ]
    summary = build_incremental_from_outcomes(
        outcomes,
        screening_path=archive.data_file,
        screening_archive=archive.archive_dir,
        funnel_path=funnel_hot,
        funnel_archive=funnel_hot.parent / "funnel_events_archive",
        intelligence_event_path=tmp_path / "intelligence_learning/events.jsonl",
        ledger_path=tmp_path / "opip/opportunity_accountability.jsonl",
        summary_path=tmp_path / "opip/opportunity_accountability_summary.json",
        state_path=tmp_path / "opip/opportunity_accountability.state.sqlite3",
        replica_mode=True,
    )
    batch = summary["batch_disposition"]
    assert batch.get("unresolved_coverage_discontinuity", 0) == 0
    assert batch["accepted"] + batch["terminal_rejected"] >= 1


def test_epoch_for_screening_allows_empty_sibling_archives(tmp_path, monkeypatch):
    """Screening discontinuity must not break empty funnel/summaries reconcile."""
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    screening, state = _plant_condition_c(tmp_path)
    _establish(tmp_path, screening, state)
    for name in ("funnel_events", "scan_summaries"):
        path = tmp_path / f"opip/qualification/{name}.jsonl"
        path.write_text("", encoding="utf-8")

    result = reconcile_qualification_replica_archives(tmp_path)
    assert result["screening"] == DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY
    assert result["funnel"] in {
        "EMPTY_CERTIFIED",
        "EMPTY_CERTIFIED_FROM_EXPORT_ATTESTATION",
    }
    assert result["summaries"] in {
        "EMPTY_CERTIFIED",
        "EMPTY_CERTIFIED_FROM_EXPORT_ATTESTATION",
    }


def test_sibling_condition_c_without_matching_epoch_fails_closed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    screening, state = _plant_condition_c(tmp_path)
    _establish(tmp_path, screening, state)

    funnel_hot = tmp_path / "opip/qualification/funnel_events.jsonl"
    funnel_hot.write_text("{}\n", encoding="utf-8")
    from app.opip.decision.store import funnel_events_archive

    funnel = funnel_events_archive(funnel_hot)
    _write_orphan_incomplete_empty_index(funnel)
    (tmp_path / "opip/qualification/scan_summaries.jsonl").write_text(
        "", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="LEGACY_COVERAGE_DISCONTINUITY_REQUIRED"):
        reconcile_qualification_replica_archives(tmp_path)


def test_epoch_wrong_prefix_on_target_archive_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    epoch = _establish(tmp_path, archive, state)
    payload = epoch.to_dict()
    payload["archive_prefix"] = "funnel_events"
    coverage_epoch_path(tmp_path).write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )
    for name in ("funnel_events", "scan_summaries"):
        path = tmp_path / f"opip/qualification/{name}.jsonl"
        path.write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="LEGACY_COVERAGE_DISCONTINUITY_REQUIRED"):
        reconcile_qualification_replica_archives(tmp_path)


def test_invalid_epoch_wrong_legacy_sha_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    epoch = _establish(tmp_path, archive, state)
    payload = epoch.to_dict()
    payload["legacy_window_index_state_sha256"] = "0" * 64
    coverage_epoch_path(tmp_path).write_text(
        json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError):
        reconcile_qualification_replica_archives(tmp_path)


def test_malformed_epoch_schema_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    coverage_epoch_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    coverage_epoch_path(tmp_path).write_text(
        json.dumps({"schema_version": 99, "kind": "nope"}) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError):
        reconcile_qualification_replica_archives(tmp_path)
    assert archive.window_index_state_file.read_bytes() == state


def test_epoch_boundary_does_not_move_on_later_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    first = _establish(tmp_path, archive, state)
    later_export = (NOW + timedelta(days=1)).isoformat()
    _write_manifest_env(tmp_path, exported_at=later_export)
    second = establish_coverage_discontinuity_epoch(
        tmp_path,
        archive,
        expected_legacy_state_sha256=hashlib.sha256(state).hexdigest(),
        boundary_at_utc=NOW + timedelta(days=1),
        production_deployed_sha=PROD_SHA,
        exported_at_utc=later_export,
    )
    assert second.boundary_at_utc == first.boundary_at_utc
    loaded = load_coverage_epoch(tmp_path)
    assert loaded is not None
    assert loaded.boundary_at_utc == first.boundary_at_utc


def test_future_valid_archive_segment_uses_normal_manifest_path(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    hot = tmp_path / "opip/qualification/screening_evaluations.jsonl"
    archive = screening_evaluations_archive(hot)
    archive.data_file.parent.mkdir(parents=True, exist_ok=True)
    archive.data_file.write_text("", encoding="utf-8")
    archive.archive_dir.mkdir(parents=True, exist_ok=True)
    segment = archive.archive_dir / "screening_evaluations-post.jsonl.gz"
    row = {
        "observed_at": POST.isoformat(),
        "scanner_type": "BROAD_SEARCH",
        "venue_instrument_id": "BTCUSD",
    }
    with gzip.open(segment, "wb") as handle:
        handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))
    digest = hashlib.sha256(segment.read_bytes()).hexdigest()
    segment.with_suffix(segment.suffix + ".sha256").write_text(
        f"{digest}  {segment.name}\n",
        encoding="utf-8",
    )
    # Also plant funnel/summaries empty so reconcile returns for all.
    for name in ("funnel_events", "scan_summaries"):
        path = tmp_path / f"opip/qualification/{name}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    result = reconcile_qualification_replica_archives(tmp_path)
    assert result["screening"] == "RECONSTRUCTED_VERIFIED"
    assert archive.manifest_file.exists()
    assert archive.manifest_signature_file.exists()
    selection = archive.archive_paths_for_visible_window(
        start=POST - timedelta(minutes=1),
        through=POST + timedelta(minutes=1),
        max_segments=8,
    )
    assert selection.complete is True
    assert selection.paths == (segment,)


def test_oneshot_env_establishes_epoch_once(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_REPLICA_ARCHIVE_REPAIR", "true")
    archive, state = _plant_condition_c(tmp_path)
    expected = hashlib.sha256(state).hexdigest()
    monkeypatch.setenv("OPIP_LEARNING_ESTABLISH_COVERAGE_DISCONTINUITY", "1")
    monkeypatch.setenv(
        "OPIP_LEARNING_COVERAGE_DISCONTINUITY_ARCHIVE_PREFIX",
        "screening_evaluations",
    )
    monkeypatch.setenv(
        "OPIP_LEARNING_COVERAGE_DISCONTINUITY_EXPECTED_STATE_SHA",
        expected,
    )
    # Funnel/summaries empty certified path.
    for name in ("funnel_events", "scan_summaries"):
        path = tmp_path / f"opip/qualification/{name}.jsonl"
        path.write_text("", encoding="utf-8")

    result = reconcile_qualification_replica_archives(tmp_path)
    assert result["screening"] == DISPOSITION_LEGACY_COVERAGE_DISCONTINUITY
    assert coverage_epoch_path(tmp_path).is_file()
    assert oneshot_consumed_path(tmp_path).is_file()
    assert archive.window_index_state_file.read_bytes() == state


def test_diagnose_script_exposes_lock_owner_and_epoch_fields():
    root = Path(__file__).resolve().parents[1]
    diagnostics = (root / "deploy/remote/diagnose-opip-learning.sh").read_text(
        encoding="utf-8"
    )
    for needle in (
        "lock_owner_pid=",
        "lock_owner_ppid=",
        "lock_owner_start_time=",
        "lock_owner_elapsed=",
        "lock_owner_command=",
        "learning_coverage_epoch_status=",
        "learning_coverage_epoch_boundary_utc=",
        "learning_coverage_epoch_archive=",
        "learning_coverage_epoch_reason=",
        "Never kill the owner",
    ):
        assert needle in diagnostics
    assert 'rm -f "$HOST_CYCLE_LOCK"' not in diagnostics
    assert "kill $pid" not in diagnostics
    assert "kill -" not in diagnostics or "kill-after" in diagnostics
