"""Regression tests for exact-head review findings on the replica bridge."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from app.jobs import run_opip_ml_data_readiness as readiness_job
from app.opip.canonical.writer import CanonicalWriter
from app.opip.learning import canonical_replica
from app.opip.learning.canonical_replica import (
    MANIFEST_FILENAME,
    REASON_GENERATION_ID_COLLISION,
    ReplicaProvenanceError,
    export_replica_bundle,
    install_replica_generation,
    read_replica_manifest,
    resolve_current_generation,
)

RELEASE_SHA = "0cd0c30eba0d45fb97aa1032364bfd93be671657"
NOW = datetime(2026, 9, 17, 14, 0, tzinfo=timezone.utc)


def _stage_bundle(
    root: Path,
    *,
    generation_id: str,
    now: datetime,
    lifecycle_tag: str,
    malformed_lifecycle: bool = False,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    live = root / "live.sqlite3"
    CanonicalWriter(live).close()

    lifecycles: dict[str, object] = {
        "PAPER:" + "a" * 20: {
            "paper_trade_id": "PAPER:" + "a" * 20,
            "episode_id": "EP:1",
            "status": "CLOSED",
            "revision": 1,
            "tag": lifecycle_tag,
            "outcome_outbox": {"delivery": "COMMITTED"},
        }
    }
    if malformed_lifecycle:
        lifecycles["PAPER:BROKEN"] = "not-an-object"

    state = root / "state.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_only": True,
                "lifecycles": lifecycles,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    gap = root / "gap.json"
    gap.write_text(
        json.dumps({"unresolved": [], "updated_at": None}, sort_keys=True),
        encoding="utf-8",
    )

    staging = root / "staging"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=gap,
        generation_id=generation_id,
        now=now,
        backup_work_dir=root / "backup-work",
    )
    return staging


def test_readiness_main_passes_the_deployed_release_sha(monkeypatch, tmp_path, capsys):
    """The scheduled readiness entrypoint must bind verification to production."""
    captured: dict[str, object] = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return {"record_type": "TEST", "schema_version": 1}

    monkeypatch.setenv("OPIP_PRODUCTION_DEPLOYED_SHA", RELEASE_SHA)
    monkeypatch.setattr(readiness_job, "build_production_readiness_report", fake_build)
    monkeypatch.setattr(readiness_job, "READINESS_REPORT", tmp_path / "readiness.json")

    readiness_job.main()

    assert captured["expected_release_sha"] == RELEASE_SHA
    assert json.loads(capsys.readouterr().out)["record_type"] == "TEST"


def test_malformed_lifecycle_row_marks_verified_replica_incomplete(tmp_path):
    """Valid survivors cannot hide malformed rows in authority state."""
    staging = _stage_bundle(
        tmp_path / "malformed",
        generation_id="gen-review-malformed",
        now=NOW,
        lifecycle_tag="A",
        malformed_lifecycle=True,
    )

    outcomes, lifecycles, source_error, reasons, malformed = (
        readiness_job._verified_replica_inputs(
            root=staging,
            expected_release_sha=RELEASE_SHA,
            now=NOW,
        )
    )

    assert source_error is None
    assert outcomes == []
    assert len(lifecycles) == 1
    assert malformed == 1
    assert "PAPER_OUTCOME_LIFECYCLE_STATE_MALFORMED" in reasons


def test_same_generation_retry_reuses_verified_directory_without_recopy(
    monkeypatch, tmp_path
):
    """An exact retry must never delete/re-copy the active generation."""
    staging = _stage_bundle(
        tmp_path / "bundle", generation_id="gen-review-retry", now=NOW, lifecycle_tag="A"
    )
    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=staging,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    before = resolve_current_generation(host)
    before_manifest = read_replica_manifest(before / MANIFEST_FILENAME)

    def must_not_copy(*_args, **_kwargs):
        raise AssertionError("idempotent retry attempted to replace an installed generation")

    monkeypatch.setattr(canonical_replica.shutil, "copytree", must_not_copy)
    result = install_replica_generation(
        staging_dir=staging,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )

    after = resolve_current_generation(host)
    assert after == before
    assert after.is_dir()
    assert read_replica_manifest(after / MANIFEST_FILENAME) == before_manifest
    assert result["installed"] == "gen-review-retry"


def test_same_generation_id_with_different_content_fails_without_touching_current(tmp_path):
    """Generation ids are immutable: collision must fail, not overwrite current."""
    first = _stage_bundle(
        tmp_path / "first", generation_id="gen-review-collision", now=NOW, lifecycle_tag="A"
    )
    second = _stage_bundle(
        tmp_path / "second",
        generation_id="gen-review-collision",
        now=NOW + timedelta(seconds=1),
        lifecycle_tag="B",
    )
    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=first,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    before = resolve_current_generation(host)
    before_manifest = read_replica_manifest(before / MANIFEST_FILENAME)

    with pytest.raises(ReplicaProvenanceError) as exc:
        install_replica_generation(
            staging_dir=second,
            host_root=host,
            expected_source_release_sha=RELEASE_SHA,
            now=NOW + timedelta(seconds=1),
        )

    assert exc.value.reason == REASON_GENERATION_ID_COLLISION
    after = resolve_current_generation(host)
    assert after == before
    assert after.is_dir()
    assert read_replica_manifest(after / MANIFEST_FILENAME) == before_manifest
