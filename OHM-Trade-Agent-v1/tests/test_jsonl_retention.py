from app.services.jsonl_retention import compact_jsonl_recent


def test_compact_jsonl_recent_keeps_latest_complete_lines(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text("".join(f'{{"n": {index}}}\n' for index in range(10)), encoding="utf-8")

    compacted = compact_jsonl_recent(path, max_bytes=10, keep_lines=3)

    assert compacted is True
    assert path.read_text(encoding="utf-8").splitlines() == [
        '{"n": 7}',
        '{"n": 8}',
        '{"n": 9}',
    ]


def test_compact_jsonl_recent_is_noop_below_size_cap(tmp_path):
    path = tmp_path / "ledger.jsonl"
    original = '{"n": 1}\n{"n": 2}\n'
    path.write_text(original, encoding="utf-8")

    compacted = compact_jsonl_recent(path, max_bytes=10_000, keep_lines=1)

    assert compacted is False
    assert path.read_text(encoding="utf-8") == original
