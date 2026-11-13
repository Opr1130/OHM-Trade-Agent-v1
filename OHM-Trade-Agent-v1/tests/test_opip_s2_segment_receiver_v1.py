"""S2 gzip-segment receiver: verification, durability, reserve gates and crash recovery."""

import ast
import gzip
import hashlib
import json
import tracemalloc
from pathlib import Path

import pytest

from app.opip.storage import segment_receiver as sr
from app.opip.storage.segment_receiver import SegmentReceiver

GIB = 1024**3
BIG = 100 * GIB


def _make(tmp: Path, rows: list[bytes], *, stream="screening", name="seg-0001", row_count=None):
    incoming = tmp / "incoming"
    incoming.mkdir(exist_ok=True)
    gz = incoming / f"{name}.jsonl.gz"
    with gzip.open(gz, "wb") as handle:
        for row in rows:
            handle.write(row + b"\n")
    manifest = incoming / f"{name}.manifest.json"
    manifest.write_text(json.dumps({
        "stream": stream,
        "sha256": hashlib.sha256(gz.read_bytes()).hexdigest(),
        "size_bytes": gz.stat().st_size,
        "row_count": len(rows) if row_count is None else row_count,
    }), encoding="utf-8")
    return gz, manifest


def _rows(n=5):
    return [json.dumps({"scan_id": f"s{i}", "venue_instrument": "KRAKEN:BTCUSD", "score": i}).encode() for i in range(n)]


def _receiver(tmp: Path, free=BIG, **kwargs):
    return SegmentReceiver(tmp / "store", free_bytes=lambda _p: free, **kwargs)


def _store_files(tmp: Path):
    return sorted(p.relative_to(tmp / "store").as_posix() for p in (tmp / "store").rglob("*") if p.is_file())


@pytest.mark.acceptance
def test_verified_segment_is_stored_once_and_acknowledged(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-001: a verified segment is stored by sha256 with its manifest and acknowledged."""
    gz, manifest = _make(tmp_path, _rows())
    outcome = _receiver(tmp_path).accept(gz, manifest)
    sha = json.loads(manifest.read_text())["sha256"]
    assert outcome.result == sr.ACCEPTED and outcome.segment_id == sha
    stored = tmp_path / "store/segments/screening" / sha[:2] / f"{sha}.jsonl.gz"
    assert stored.read_bytes() == gz.read_bytes()
    assert json.loads((tmp_path / f"store/acks/{sha}.ack").read_text())["row_count"] == 5
    assert (tmp_path / "store/receiver-status.json").is_file()
    assert json.loads((tmp_path / "store/receiver-status.json").read_text())["state"] == sr.OK


@pytest.mark.acceptance
def test_acknowledgement_is_written_only_after_the_segment_is_durable(tmp_path, monkeypatch):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-002: the segment and manifest are renamed into place before the ack."""
    order = []
    real = sr.os.replace

    def spy(src, dst):
        order.append(Path(dst).name)
        return real(src, dst)

    monkeypatch.setattr(sr.os, "replace", spy)
    gz, manifest = _make(tmp_path, _rows())
    _receiver(tmp_path).accept(gz, manifest)
    kinds = [("ack" if n.endswith(".ack") else "manifest" if n.endswith(".manifest.json")
              else "segment" if n.endswith(".jsonl.gz") else "status") for n in order]
    assert kinds.index("segment") < kinds.index("ack")
    assert kinds.index("manifest") < kinds.index("ack")


@pytest.mark.acceptance
def test_repeated_delivery_is_a_no_op(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-003: at-least-once delivery, exactly-once logical acceptance."""
    gz, manifest = _make(tmp_path, _rows())
    receiver = _receiver(tmp_path)
    assert receiver.accept(gz, manifest).result == sr.ACCEPTED
    before = _store_files(tmp_path)
    again = receiver.accept(gz, manifest)
    assert again.result == sr.DUPLICATE
    assert _store_files(tmp_path) == before


@pytest.mark.acceptance
def test_bad_checksum_size_or_rows_are_rejected_without_writes(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-004: checksum, size, row_count and JSON failures store nothing and send no ack."""
    gz, manifest = _make(tmp_path, _rows(), name="a")
    gz.write_bytes(gz.read_bytes() + b"x")
    assert _receiver(tmp_path).accept(gz, manifest).result == sr.REJECTED_CHECKSUM

    gz2, manifest2 = _make(tmp_path, _rows(), name="b", row_count=9)
    assert _receiver(tmp_path).accept(gz2, manifest2).result == sr.REJECTED_CONTENT

    gz3, manifest3 = _make(tmp_path, [b"not json"], name="c")
    assert _receiver(tmp_path).accept(gz3, manifest3).result == sr.REJECTED_CONTENT

    bad = tmp_path / "incoming/d.manifest.json"
    bad.write_text("{}", encoding="utf-8")
    assert _receiver(tmp_path).accept(gz, bad).result == sr.REJECTED_MANIFEST
    assert [f for f in _store_files(tmp_path) if "segments" in f or f.startswith("acks/")] == []


@pytest.mark.acceptance
def test_credential_like_rows_fail_closed(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-005: lossless shipment refuses rows that look like credentials."""
    for index, secret in enumerate((
        # Built at runtime so no credential-shaped literal sits in source (secret scan).
        b'{"api_' + b'key": "' + b"abcdef" + b"0123456789abcdef" + b'"}',
        b'{"note": "' + b"123456789" + b":" + b"A" * 36 + b'"}',
        b'{"pem": "-----BEGIN ' + b"RSA PRIVATE" + b' KEY-----"}',
    )):
        gz, manifest = _make(tmp_path, _rows(2) + [secret], name=f"s{index}")
        assert _receiver(tmp_path).accept(gz, manifest).result == sr.REJECTED_CREDENTIAL
    assert [f for f in _store_files(tmp_path) if "segments" in f or f.startswith("acks/")] == []


@pytest.mark.acceptance
def test_reserve_pauses_at_three_gib_and_fails_closed_at_two(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-006: projected free below 3 GiB pauses, below 2 GiB is CRITICAL, before any write."""
    gz, manifest = _make(tmp_path, _rows())
    size = gz.stat().st_size
    paused = _receiver(tmp_path, free=3 * GIB + size - 1).accept(gz, manifest)
    assert (paused.result, paused.reason) == (sr.REFUSED_RESERVE, sr.PAUSED_RESERVE)
    assert json.loads((tmp_path / "store/receiver-status.json").read_text())["state"] == sr.PAUSED_RESERVE
    critical = _receiver(tmp_path, free=2 * GIB + size - 1).accept(gz, manifest)
    assert (critical.result, critical.reason) == (sr.REFUSED_RESERVE, sr.CRITICAL)
    assert json.loads((tmp_path / "store/receiver-status.json").read_text())["state"] == sr.CRITICAL
    assert [f for f in _store_files(tmp_path) if "segments" in f or f.startswith("acks/")] == []
    exactly = _receiver(tmp_path, free=3 * GIB + size).accept(gz, manifest)
    assert exactly.result == sr.ACCEPTED


@pytest.mark.acceptance
def test_free_space_falling_below_the_floor_mid_write_aborts_and_cleans_up(tmp_path, monkeypatch):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-006: a drop below the floor during the copy stops it, leaving no temp, segment or ack."""
    monkeypatch.setattr(sr, "FLOOR_RECHECK_BYTES", 1)
    calls = {"n": 0}

    def free(_path):
        calls["n"] += 1
        return BIG if calls["n"] == 1 else GIB

    gz, manifest = _make(tmp_path, _rows(50))
    receiver = SegmentReceiver(tmp_path / "store", free_bytes=free)
    outcome = receiver.accept(gz, manifest)
    assert (outcome.result, outcome.reason) == (sr.REFUSED_RESERVE, sr.CRITICAL)
    assert [f for f in _store_files(tmp_path) if "segments" in f or f.startswith("acks/")] == []


@pytest.mark.acceptance
def test_crash_before_ack_is_recovered_without_duplicates(tmp_path, monkeypatch):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-007: a crash after the segment is stored but before the ack is recovered exactly once."""
    gz, manifest = _make(tmp_path, _rows())
    receiver = _receiver(tmp_path)

    def boom(self, segment_id, manifest_doc):
        raise RuntimeError("simulated crash before ack")

    monkeypatch.setattr(SegmentReceiver, "_write_ack", boom)
    with pytest.raises(RuntimeError):
        receiver.accept(gz, manifest)
    monkeypatch.undo()
    sha = json.loads(manifest.read_text())["sha256"]
    assert not (tmp_path / f"store/acks/{sha}.ack").exists()
    (tmp_path / "store/segments/screening/leftover").mkdir(parents=True, exist_ok=True)
    (tmp_path / "store/segments/screening/leftover/.tmp-partial-1").write_bytes(b"half")

    fresh = _receiver(tmp_path)
    recovered = fresh.recover()
    assert [o.result for o in recovered] == [sr.RECOVERED]
    assert (tmp_path / f"store/acks/{sha}.ack").is_file()
    assert not list((tmp_path / "store").rglob(".tmp-*")) or all(
        p.name.startswith(".tmp-receiver-status") for p in (tmp_path / "store").rglob(".tmp-*")
    )
    assert fresh.accept(gz, manifest).result == sr.DUPLICATE
    assert len(list((tmp_path / "store/segments").rglob("*.jsonl.gz"))) == 1


@pytest.mark.acceptance
def test_corrupt_stored_segment_is_left_in_place_and_reported(tmp_path, monkeypatch):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-007: recovery never deletes or acknowledges evidence that fails verification."""
    gz, manifest = _make(tmp_path, _rows())
    monkeypatch.setattr(SegmentReceiver, "_write_ack", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("crash")))
    with pytest.raises(RuntimeError):
        _receiver(tmp_path).accept(gz, manifest)
    monkeypatch.undo()
    stored = next((tmp_path / "store/segments").rglob("*.jsonl.gz"))
    stored.write_bytes(b"corrupted")
    outcomes = _receiver(tmp_path).recover()
    assert [o.result for o in outcomes] == [sr.REJECTED_CHECKSUM]
    assert stored.exists() and not list((tmp_path / "store/acks").iterdir())


@pytest.mark.acceptance
def test_source_files_are_never_modified_or_removed(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-008: accepted and rejected sources are left exactly as delivered."""
    good_gz, good_manifest = _make(tmp_path, _rows(), name="good")
    bad_gz, bad_manifest = _make(tmp_path, _rows(2) + [b'{"pass' + b'word": "' + b"abcdef" + b"0123456789abcd" + b'"}'], name="bad")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (good_gz, good_manifest, bad_gz, bad_manifest)}
    receiver = _receiver(tmp_path)
    receiver.accept(good_gz, good_manifest)
    receiver.accept(bad_gz, bad_manifest)
    _receiver(tmp_path, free=GIB).accept(good_gz, good_manifest)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


@pytest.mark.acceptance
def test_receiver_is_files_only_with_no_database_network_or_process_imports():
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-009: no Postgres, SQLite, socket, HTTP or subprocess in the receiver."""
    tree = ast.parse(Path(sr.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & {"sqlite3", "psycopg", "psycopg2", "asyncpg", "sqlalchemy",
                           "socket", "http", "urllib", "requests", "subprocess", "ssl"}
    source = Path(sr.__file__).read_text(encoding="utf-8")
    assert "rmtree" not in source and "os.remove" not in source


@pytest.mark.acceptance
def test_large_segment_is_verified_in_bounded_memory(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-010: verification and storage stream; peak memory stays far below the 128 MiB ceiling."""
    row = json.dumps({"scan_id": "x" * 40, "venue_instrument": "KRAKEN:ETHUSD", "pad": "y" * 60}).encode()
    gz, manifest = _make(tmp_path, [row] * 120_000)
    receiver = _receiver(tmp_path)
    tracemalloc.start()
    outcome = receiver.accept(gz, manifest)
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert outcome.result == sr.ACCEPTED
    assert peak < 16 * 1024 * 1024, peak


@pytest.mark.acceptance
def test_oversized_uncompressed_stream_is_rejected(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-010: a decompression bomb is refused instead of expanded."""
    gz, manifest = _make(tmp_path, _rows(2000))
    outcome = _receiver(tmp_path, max_uncompressed_bytes=1024).accept(gz, manifest)
    assert outcome.result == sr.REJECTED_CONTENT and "uncompressed" in outcome.reason
    assert [f for f in _store_files(tmp_path) if "segments" in f] == []


@pytest.mark.acceptance
def test_same_size_corruption_is_caught_by_the_sha256_check(tmp_path):
    """ATDD-OPIP-S2-SEGMENT-RECEIVER-v1/AC-004: a same-size change that still decompresses is rejected by the checksum alone."""
    gz, manifest = _make(tmp_path, _rows())
    data = bytearray(gz.read_bytes())
    data[5] ^= 0xFF  # gzip header mtime byte: the stream stays valid, only the sha256 changes
    gz.write_bytes(bytes(data))
    assert len(data) == json.loads(manifest.read_text())["size_bytes"]
    outcome = _receiver(tmp_path).accept(gz, manifest)
    assert outcome.result == sr.REJECTED_CHECKSUM
    assert [f for f in _store_files(tmp_path) if "segments" in f or f.startswith("acks/")] == []
