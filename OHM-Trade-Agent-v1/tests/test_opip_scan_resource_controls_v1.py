"""ATDD — scanner resource controls (R04/R05/R07).

Focused behavioural tests for the bounded-work changes: configurable
concurrency, bounded telemetry capture, bounded JSONL reads and explicit scan
outcomes. No test here needs Docker or a live provider.
"""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.storage import bounded_jsonl
from app.scanner import market_scanner
from app.services import operations_analytics as oa


def _settings(**overrides):
    base = {"opip_scan_max_workers": 4}
    base.update(overrides)
    return SimpleNamespace(**base)


# --- AT-R04-01: concurrency is configurable and may only tighten -----------


def test_configured_max_workers_defaults_to_four(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: _settings(opip_scan_max_workers=4)
    )
    assert market_scanner.configured_max_workers() == 4


def test_configured_max_workers_cannot_exceed_historical_ceiling(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: _settings(opip_scan_max_workers=99)
    )
    assert market_scanner.configured_max_workers() == market_scanner.MAX_WORKERS


def test_configured_max_workers_never_returns_less_than_one(monkeypatch):
    monkeypatch.setattr(
        "app.core.config.get_settings", lambda: _settings(opip_scan_max_workers=0)
    )
    assert market_scanner.configured_max_workers() == 1


def test_configured_max_workers_falls_back_when_settings_unavailable(monkeypatch):
    def _boom():
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr("app.core.config.get_settings", _boom)
    assert market_scanner.configured_max_workers() == market_scanner.MAX_WORKERS


# --- AT-R04-02: telemetry capture is bounded ------------------------------


def test_bounded_capture_keeps_head_and_tail_and_counts_drops():
    capture = oa._BoundedCapture(max_lines=100, max_bytes=1_000_000)
    for index in range(1_000):
        capture.write(f"line-{index}\n")
    text = capture.getvalue()

    assert "line-0" in text, "head must survive so summary fields stay parseable"
    assert "line-999" in text, "tail must survive so terminal failures stay visible"
    assert oa._BoundedCapture.TRUNCATION_MARKER in text
    assert capture.dropped_lines > 0
    # Bounded: head (100 lines) + marker + tail (25 lines) only.
    assert len(text.splitlines()) <= 100 + 1 + 25


def test_bounded_capture_respects_byte_budget():
    capture = oa._BoundedCapture(max_lines=10_000, max_bytes=2_000)
    for index in range(5_000):
        capture.write(f"{index:05d}-" + "x" * 40 + "\n")
    assert len(capture.getvalue().encode("utf-8")) < 20_000
    assert capture.dropped_lines > 0


def test_bounded_capture_preserves_scanner_summary_for_parser():
    capture = oa._BoundedCapture(max_lines=50, max_bytes=100_000)
    capture.write("===== OHM UNIVERSE =====\n")
    capture.write("Requested: 200\n")
    capture.write("Analyzed: 197\n")
    capture.write("Skipped: 1\n")
    capture.write("Failed: 2\n")
    for index in range(500):
        capture.write(f"noise-{index}\n")

    parsed = oa.parse_scan_output(capture.getvalue())
    assert parsed["requested"] == 200
    assert parsed["analyzed"] == 197
    assert parsed["failed"] == 2


def test_tee_write_returns_length_for_textio_contract():
    sink = io.StringIO()
    tee = oa._Tee(sink)
    assert tee.write("abc") == 3
    assert sink.getvalue() == "abc"


# --- AT-R07-01: partial scans are never recorded as complete --------------


def _scan_result(**overrides):
    base = {"requested": 10, "analyzed": 10, "failed": 0, "skipped": 0}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_complete_scan_is_completed():
    assert oa.derive_scan_outcome(_scan_result()) == "COMPLETED"


@pytest.mark.parametrize(
    "result",
    [
        _scan_result(analyzed=8, failed=2),
        _scan_result(skipped=1),
        _scan_result(analyzed=9),
        _scan_result(requested=0, analyzed=0),
    ],
)
def test_incomplete_scan_is_partial(result):
    assert oa.derive_scan_outcome(result) == "PARTIAL"


def test_callable_without_counters_is_completed_only_because_it_returned():
    assert oa.derive_scan_outcome(None) == "COMPLETED"


# --- AT-R07-02: failures are recorded, not silently swallowed -------------


def test_run_scan_with_telemetry_records_failed_outcome_and_reraises(monkeypatch):
    recorded = {}

    def _record(text, *, started_at=None, outcome="UNAVAILABLE", dropped_lines=0):
        recorded["outcome"] = outcome
        recorded["text"] = text
        return {}

    monkeypatch.setattr(oa, "record_scan_output", _record)

    def _explode():
        print("Requested: 3")
        raise RuntimeError("scan blew up")

    with pytest.raises(RuntimeError):
        oa.run_scan_with_telemetry(_explode)

    assert recorded["outcome"] == "FAILED"
    assert "Requested: 3" in recorded["text"]


def test_run_scan_with_telemetry_records_partial_outcome(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        oa,
        "record_scan_output",
        lambda text, **kwargs: recorded.update(kwargs) or {},
    )
    oa.run_scan_with_telemetry(lambda: _scan_result(failed=1, analyzed=9))
    assert recorded["outcome"] == "PARTIAL"


# --- AT-R10-01: JSONL reads are bounded and semantics-preserving ----------


def test_read_lines_preserves_row_semantics(tmp_path: Path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":1}\n\n{"b":2}\n{"c":3}')

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("read_lines must not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", _forbidden)

    assert bounded_jsonl.read_lines(path) == [b'{"a":1}\n', b'{"b":2}\n', b'{"c":3}']


def test_iter_lines_streams_in_chunks(tmp_path: Path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    rows = [b'{"n":%d}\n' % index for index in range(50)]
    path.write_bytes(b"".join(rows))

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("iter_lines must not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", _forbidden)
    assert list(bounded_jsonl.iter_lines(path, chunk_size=17)) == rows


def test_read_tail_lines_is_bounded(tmp_path: Path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b"".join(b'{"n":%d}\n' % index for index in range(10_000)))

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("read_tail_lines must not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", _forbidden)

    tail = bounded_jsonl.read_tail_lines(path, 5)
    assert tail == [b'{"n":%d}\n' % index for index in range(9_995, 10_000)]


def test_repair_truncated_tail_quarantines_torn_row(tmp_path: Path, monkeypatch):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":1}\n{"b":2}\n{"torn":')

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("repair must seek, not read the whole file")

    monkeypatch.setattr(Path, "read_bytes", _forbidden)

    bounded_jsonl.repair_truncated_tail(path)

    def _raw(target: Path) -> bytes:
        with target.open("rb") as handle:
            return handle.read()

    assert _raw(path) == b'{"a":1}\n{"b":2}\n'
    quarantined = list(tmp_path.glob("rows.jsonl.truncated-*.bin"))
    assert len(quarantined) == 1
    assert _raw(quarantined[0]) == b'{"torn":'


def test_repair_truncated_tail_adds_missing_final_newline(tmp_path: Path):
    path = tmp_path / "rows.jsonl"
    path.write_bytes(b'{"a":1}')

    bounded_jsonl.repair_truncated_tail(path)

    assert path.read_bytes() == b'{"a":1}\n'


def test_repair_truncated_tail_noop_on_empty_and_clean_files(tmp_path: Path):
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"")
    bounded_jsonl.repair_truncated_tail(empty)
    assert empty.read_bytes() == b""

    clean = tmp_path / "clean.jsonl"
    clean.write_bytes(b'{"a":1}\n')
    bounded_jsonl.repair_truncated_tail(clean)
    assert clean.read_bytes() == b'{"a":1}\n'

