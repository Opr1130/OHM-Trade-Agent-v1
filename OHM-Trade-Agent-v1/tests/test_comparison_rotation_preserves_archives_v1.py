"""Comparison-file rotation must never overwrite or lose captured evidence."""
import json
import os

from app.services import technical_scoring_comparison as tsc


def _rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def _write(path, count, start=0):
    for i in range(start, start + count):
        assert tsc.record_comparison_fail_soft({"n": i, "pad": "x" * 60}, path)


def test_repeated_rotations_keep_every_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(tsc, "MAX_BYTES", 300)
    hot = tmp_path / "scoring_comparison.jsonl"
    _write(hot, 40)

    archives = sorted(tmp_path.glob("scoring_comparison.*.jsonl"))
    assert len(archives) >= 3
    assert (tmp_path / "scoring_comparison.1.jsonl").exists()
    seen = []
    for path in sorted(archives, key=lambda p: int(p.name.split(".")[1])) + [hot]:
        seen.extend(row["n"] for row in _rows(path))
    assert seen == list(range(40))


def test_existing_first_archive_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(tsc, "MAX_BYTES", 300)
    hot = tmp_path / "scoring_comparison.jsonl"
    first = tmp_path / "scoring_comparison.1.jsonl"
    first.write_text('{"n":-1}\n')

    _write(hot, 12)

    assert _rows(first) == [{"n": -1}]
    assert (tmp_path / "scoring_comparison.2.jsonl").exists()


def test_rotation_failure_keeps_record_in_active_file(tmp_path, monkeypatch):
    hot = tmp_path / "scoring_comparison.jsonl"
    _write(hot, 3)
    monkeypatch.setattr(tsc, "MAX_BYTES", 200)

    def _boom(*_args, **_kwargs):
        raise OSError("link unsupported")

    monkeypatch.setattr(os, "link", _boom)
    assert tsc.record_comparison_fail_soft({"n": 99, "pad": "x" * 60}, hot)

    assert [row["n"] for row in _rows(hot)] == [0, 1, 2, 99]
    assert list(tmp_path.glob("scoring_comparison.*.jsonl")) == []
