import json
from datetime import datetime, timezone
from types import SimpleNamespace

import app.services.p1_shadow_outbox as p1_outbox

from app.services.p1_shadow_outbox import (
    append_live_scan_snapshots,
    drain_outbox_to_evidence_ledger,
    outbox_health,
    p1_shadow_outbox_enabled,
)


NOW = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)


def candidate(symbol, *, suppressed=False):
    return SimpleNamespace(
        symbol=symbol,
        universe_size=200,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78,
        explosion_potential_score=74,
        tradeability_score=72,
        pattern_strength_score=80,
        volume_acceleration_score=70,
        relative_strength_score=88,
        persistence_scans=3,
        exhaustion_penalty=10,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        liquidity_24h_usd_approx=2_000_000.0,
        suppressed=suppressed,
        reasons=(),
        components={},
    )


def test_outbox_is_dark_by_default(tmp_path):
    target = tmp_path / "outbox.jsonl"
    assert p1_shadow_outbox_enabled({}) is False
    assert append_live_scan_snapshots(
        [candidate("AUSD")],
        decision_at=NOW,
        path=target,
        enabled=False,
    ) == 0
    assert not target.exists()


def test_outbox_preserves_rank_and_suppressed_candidates(tmp_path):
    target = tmp_path / "outbox.jsonl"
    written = append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD", suppressed=True)],
        decision_at=NOW,
        reference_prices={"AUSD": 1.0, "BUSD": 2.0},
        path=target,
        enabled=True,
    )
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert written == 2
    assert [row["candidate_rank"] for row in rows] == [1, 2]
    assert rows[1]["suppressed"] is True
    assert rows[0]["decision_at_utc"] == NOW.isoformat()
    assert all(row["affects_ranking"] is False for row in rows)


def test_drain_is_checkpointed_and_ledger_idempotent(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"
    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    assert first.processed == 2
    assert outbox_health(outbox_path=outbox, checkpoint_path=checkpoint)["backlog_rows"] == 0

    checkpoint.unlink()
    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    assert second.duplicates == 2
    assert len(ledger.read_text().splitlines()) == 2


def test_malformed_row_goes_to_dead_letter_and_does_not_block(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"
    outbox.write_text("{bad json}\n", encoding="utf-8")

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    assert result.malformed == 1
    assert result.stopped_on_error is False
    assert json.loads(dead.read_text().strip())["measurement_only"] is True


def test_partial_trailing_row_is_left_pending_not_dead_lettered(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"
    outbox.write_text('{"snapshot_id":"S1"', encoding="utf-8")

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )

    assert result.processed == 0
    assert result.malformed == 0
    assert not checkpoint.exists()
    assert not dead.exists()
    assert outbox_health(outbox_path=outbox, checkpoint_path=checkpoint)["backlog_rows"] == 1


def test_processor_failure_does_not_advance_failing_line(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    append_live_scan_snapshots(
        [candidate("AUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    def boom(row):
        raise TimeoutError("test")

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        checkpoint_path=checkpoint,
        processor=boom,
    )
    assert result.stopped_on_error is True
    assert result.error_type == "TimeoutError"
    assert outbox_health(outbox_path=outbox, checkpoint_path=checkpoint)["backlog_rows"] == 1


def test_checkpoint_v2_persists_bounded_byte_cursor_and_anchor(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert first.processed == 1

    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["next_line"] == 1
    assert 0 < saved["byte_offset"] < outbox.stat().st_size
    assert saved["anchor_size"] > 0
    assert len(saved["anchor_sha256"]) == 64
    assert saved["source_size"] == outbox.stat().st_size
    assert saved["source_tail_size"] > 0
    assert len(saved["source_tail_sha256"]) == 64

    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert second.processed == 1
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 2


def test_checkpoint_anchor_rejects_rewritten_processed_prefix(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert first.processed == 1

    payload = outbox.read_bytes()
    assert b"AUSD" in payload
    outbox.write_bytes(payload.replace(b"AUSD", b"ZUSD", 1))

    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert second.stopped_on_error is True
    assert second.error_type == "CHECKPOINT_SOURCE_DIVERGED"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_legacy_line_checkpoint_migrates_without_full_file_materialization(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )
    checkpoint.write_text('{"next_line": 1}\n', encoding="utf-8")

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert result.processed == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    assert saved["next_line"] == 2
    assert saved["byte_offset"] == outbox.stat().st_size


def test_checkpoint_rejects_truncate_and_regrow_after_cursor(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert first.processed == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    cursor = int(saved["byte_offset"])
    prior_size = int(saved["source_size"])

    original = outbox.read_bytes()
    prefix = original[:cursor]
    # Preserve every byte already processed, but replace the unprocessed
    # generation and regrow past the prior size. Cursor-only continuity would
    # accept this; the prior-source tail anchor must reject it.
    replacement = prefix + (b'{"snapshot_id":"REPLACEMENT"}\n' * 200)
    assert len(replacement) > prior_size
    outbox.write_bytes(replacement)

    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert second.stopped_on_error is True
    assert second.error_type == "CHECKPOINT_SOURCE_DIVERGED"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_ledger_partial_tail_is_repaired_before_next_append(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    append_live_scan_snapshots(
        [candidate("AUSD"), candidate("BUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )
    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert first.processed == 1

    with ledger.open("ab") as handle:
        handle.write(b'{"snapshot_id":"BROKEN"')

    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        batch_limit=1,
    )
    assert second.processed == 1
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert all(row.get("snapshot_id") != "BROKEN" for row in rows)


def test_checkpoint_save_failure_returns_stopped_result(tmp_path, monkeypatch):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    append_live_scan_snapshots(
        [candidate("AUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )

    def fail_save(*args, **kwargs):
        raise FileNotFoundError("simulated source replacement")

    monkeypatch.setattr(p1_outbox, "_save_checkpoint_state", fail_save)

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
    )
    assert result.stopped_on_error is True
    assert result.error_type == "FileNotFoundError"
    assert result.remaining_from_line == 0
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1


def test_rewritten_ledger_rebuilds_disk_dedup_index(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"

    append_live_scan_snapshots(
        [candidate("AUSD")],
        decision_at=NOW,
        path=outbox,
        enabled=True,
    )
    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
    )
    assert first.processed == 1

    original = json.loads(ledger.read_text(encoding="utf-8").strip())
    replacement = {
        **original,
        "snapshot_id": "REPLACEMENT-" + str(original["snapshot_id"]),
    }
    ledger.write_text(
        json.dumps(replacement, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checkpoint.unlink()

    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
    )
    assert second.processed == 1
    assert second.duplicates == 0
    rows = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
