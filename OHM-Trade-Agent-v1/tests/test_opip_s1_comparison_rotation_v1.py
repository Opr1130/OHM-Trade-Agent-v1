"""Scoring-comparison evidence is never overwritten, lost or silently dropped."""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from app.services import technical_scoring_comparison as cmp

INCREMENT = "ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1"


def _row(i: int, pad: int = 300) -> dict:
    return {"scan_id": f"scan-{i}", "pad": "x" * pad}


def _all_ids(directory: Path) -> list[str]:
    ids: list[str] = []
    for item in sorted(directory.glob("scoring_comparison*.jsonl")):
        for line in item.read_bytes().splitlines():
            try:
                ids.append(json.loads(line)["scan_id"])
            except (ValueError, KeyError):
                continue
    return ids


@pytest.fixture()
def hot(tmp_path, monkeypatch):
    monkeypatch.setattr(cmp, "MAX_BYTES", 2000)
    return tmp_path / "scoring_comparison.jsonl"


@pytest.mark.acceptance
def test_rotation_keeps_every_archive_and_never_overwrites(hot):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-001: repeated rotations keep all records under unique archive names."""
    for i in range(40):
        assert cmp.record_comparison_fail_soft(_row(i), hot) is True
    archives = [p for p in hot.parent.glob("scoring_comparison.*.jsonl")]
    assert len(archives) >= 3
    assert len({p.name for p in archives}) == len(archives)
    assert sorted(_all_ids(hot.parent), key=lambda x: int(x.split("-")[1])) == [f"scan-{i}" for i in range(40)]


@pytest.mark.acceptance
def test_legacy_dot_one_file_is_never_touched(hot):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-001: an existing legacy .1.jsonl stays byte-identical through rotation."""
    legacy = hot.with_suffix(".1.jsonl")
    legacy.write_bytes(b'{"scan_id":"legacy-0"}\n')
    before = legacy.read_bytes()
    for i in range(30):
        cmp.record_comparison_fail_soft(_row(i), hot)
    assert legacy.read_bytes() == before


@pytest.mark.acceptance
def test_name_collision_gets_a_new_suffix_and_never_replaces(hot, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-001: an archive name that already exists is skipped, not replaced."""
    monkeypatch.setattr(cmp.os, "replace", lambda *a, **k: (_ for _ in ()).throw(AssertionError("replace used")))
    class _Fixed(cmp.datetime):
        @classmethod
        def now(cls, tz=None):
            return cmp.datetime(2026, 11, 13, 1, 0, 0, tzinfo=tz)
    monkeypatch.setattr(cmp, "datetime", _Fixed)
    for i in range(40):
        cmp.record_comparison_fail_soft(_row(i), hot)
    names = sorted(p.name for p in hot.parent.glob("scoring_comparison.2026*.jsonl"))
    assert len(names) == len(set(names)) >= 3
    assert len(_all_ids(hot.parent)) == 40


def _worker(directory: str, start: int, count: int) -> None:
    cmp.MAX_BYTES = 20_000
    cmp.COMPARISON_LOCK_TIMEOUT_S = 60.0
    path = Path(directory) / "scoring_comparison.jsonl"
    for i in range(start, start + count):
        cmp.record_comparison_fail_soft(_row(i), path)


@pytest.mark.acceptance
def test_concurrent_writers_lose_no_record_across_rotations(tmp_path):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-002: parallel writers across several rotations keep exactly every record once."""
    ctx = multiprocessing.get_context("fork")
    procs = [ctx.Process(target=_worker, args=(str(tmp_path), k * 1000, 250)) for k in range(4)]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(120)
        assert proc.exitcode == 0
    ids = _all_ids(tmp_path)
    assert len(ids) == 1000
    assert len(set(ids)) == 1000
    assert len(list(tmp_path.glob("scoring_comparison.*.jsonl"))) >= 3


@pytest.mark.acceptance
def test_crash_between_link_and_unlink_recovers_without_loss(hot, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-002: a rotation that died after the link is completed on the next append."""
    for i in range(6):
        cmp.record_comparison_fail_soft(_row(i), hot)
    real_unlink = os.unlink
    def boom(p, *a, **k):
        raise OSError("simulated crash before unlink")
    monkeypatch.setattr(cmp.os, "unlink", boom)
    for i in range(6, 12):
        cmp.record_comparison_fail_soft(_row(i), hot)
    monkeypatch.setattr(cmp.os, "unlink", real_unlink)
    for i in range(12, 16):
        assert cmp.record_comparison_fail_soft(_row(i), hot) is True
    ids = _all_ids(hot.parent)
    assert set(ids) >= {f"scan-{i}" for i in range(0, 6)} | {f"scan-{i}" for i in range(12, 16)}
    assert len(ids) == len(set(ids))


@pytest.mark.acceptance
def test_torn_tail_is_preserved_on_its_own_line(hot):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-002: a partial last line is kept and the next record stays intact."""
    hot.write_bytes(b'{"scan_id":"scan-0","pad":"x"}\n{"scan_id":"torn')
    cmp.MAX_BYTES = 10_000_000
    assert cmp.record_comparison_fail_soft(_row(1), hot) is True
    lines = hot.read_bytes().split(b"\n")
    assert lines[1] == b'{"scan_id":"torn'
    assert json.loads(lines[2])["scan_id"] == "scan-1"


@pytest.mark.acceptance
def test_short_writes_and_eintr_still_write_the_whole_record(tmp_path, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-003: short writes and EINTR are retried until the record is complete."""
    hot = tmp_path / "scoring_comparison.jsonl"
    real_write = os.write
    state = {"n": 0}
    def flaky(fd, data):
        state["n"] += 1
        if state["n"] == 1:
            raise InterruptedError()
        return real_write(fd, bytes(data)[:7])
    monkeypatch.setattr(cmp.os, "write", flaky)
    assert cmp.record_comparison_fail_soft(_row(1, pad=50), hot) is True
    monkeypatch.undo()
    assert json.loads(hot.read_bytes())["scan_id"] == "scan-1"


@pytest.mark.acceptance
def test_write_failure_is_counted_and_never_raises(tmp_path, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-003: ENOSPC increments the drop counter and does not raise into the scan."""
    hot = tmp_path / "scoring_comparison.jsonl"
    before = cmp.comparison_write_stats()["dropped"]
    def enospc(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(cmp.os, "fsync", enospc)
    assert cmp.record_comparison_fail_soft(_row(1), hot) is False
    stats = cmp.comparison_write_stats()
    assert stats["dropped"] == before + 1
    assert stats["last_error"] == "OSError"


@pytest.mark.acceptance
def test_lock_timeout_is_bounded_and_counted(tmp_path, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-003: a held lock costs at most the timeout and the drop is counted."""
    import time
    from app.services.registry_io import registry_lock
    hot = tmp_path / "scoring_comparison.jsonl"
    monkeypatch.setattr(cmp, "COMPARISON_LOCK_TIMEOUT_S", 0.3)
    before = cmp.comparison_write_stats()["dropped"]
    with registry_lock(hot.with_name(hot.name + ".lock"), timeout=1.0):
        started = time.monotonic()
        assert cmp.record_comparison_fail_soft(_row(1), hot) is False
        assert time.monotonic() - started < 2.0
    assert cmp.comparison_write_stats()["dropped"] == before + 1
    assert not hot.exists() or hot.stat().st_size == 0


def test_hot_file_mode_stays_private(hot):
    cmp.record_comparison_fail_soft(_row(1), hot)
    assert (hot.stat().st_mode & 0o777) == 0o600


@pytest.mark.acceptance
def test_link_failure_never_renames_over_an_intruder_and_keeps_every_record(hot, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-001: when hard links are unavailable nothing is renamed or replaced and no record is lost."""
    monkeypatch.setattr(cmp, "MAX_BYTES", 2000)

    def no_links(src, dst, *a, **k):
        raise OSError("hard links unavailable")

    monkeypatch.setattr(os, "link", no_links)
    renames = []
    real_rename = os.rename
    monkeypatch.setattr(os, "rename", lambda *a, **k: renames.append(a) or real_rename(*a, **k))
    before = cmp.comparison_write_stats()
    for i in range(20):
        assert cmp.record_comparison_fail_soft(_row(i), hot)
    after = cmp.comparison_write_stats()
    assert renames == []
    assert _all_ids(hot.parent) == [f"scan-{i}" for i in range(20)]
    assert after["rotation_unavailable"] > before["rotation_unavailable"]
    assert after["dropped"] == before["dropped"]


@pytest.mark.acceptance
def test_archive_stats_report_count_and_bytes(hot, monkeypatch):
    """ATDD-OPIP-S1-COMPARISON-EVIDENCE-v1/AC-003: archive growth is observable without reading any record."""
    monkeypatch.setattr(cmp, "MAX_BYTES", 1500)
    for i in range(12):
        cmp.record_comparison_fail_soft(_row(i), hot)
    stats = cmp.comparison_archive_stats(hot)
    assert stats["archive_count"] >= 1
    assert stats["archive_bytes"] > 0 and stats["hot_bytes"] > 0
    assert stats["max_bytes"] == 1500
