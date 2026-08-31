from __future__ import annotations

from pathlib import Path

import pytest

from app.services.opip_ml_evidence_capture import (
    EvidenceLine,
    _write_snapshot_chunk_atomic,
)


def _batch() -> list[EvidenceLine]:
    return [
        EvidenceLine(
            line_number=0,
            start_offset=0,
            end_offset=10,
            raw=b'{"record_type":"CANONICAL_EPISODE_SNAPSHOT"}\n',
        )
    ]


def _wrappers() -> list[dict[str, object]]:
    return [
        {
            "record_type": "OPIP_ML_FEATURE_SNAPSHOT",
            "ml_snapshot_id": "MLSNAP:test",
        }
    ]


def test_new_chunk_does_not_report_success_when_directory_sync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new chunk must not permit checkpoint advancement without dir durability."""

    def fail_directory_open(*args, **kwargs):
        raise OSError("simulated directory sync failure")

    monkeypatch.setattr("app.services.opip_ml_evidence_capture.os.open", fail_directory_open)

    with pytest.raises(OSError, match="directory sync failure"):
        _write_snapshot_chunk_atomic(
            tmp_path / "snapshots",
            wrappers=_wrappers(),
            batch=_batch(),
        )


def test_existing_chunk_retry_requires_directory_sync_before_checkpoint_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Crash-retry reuse must also prove the published directory entry is durable."""

    snapshot_dir = tmp_path / "snapshots"
    created, destination = _write_snapshot_chunk_atomic(
        snapshot_dir,
        wrappers=_wrappers(),
        batch=_batch(),
    )
    assert created is True
    assert destination is not None and destination.exists()

    def fail_directory_open(*args, **kwargs):
        raise OSError("simulated directory sync failure")

    monkeypatch.setattr("app.services.opip_ml_evidence_capture.os.open", fail_directory_open)

    with pytest.raises(OSError, match="directory sync failure"):
        _write_snapshot_chunk_atomic(
            snapshot_dir,
            wrappers=_wrappers(),
            batch=_batch(),
        )
