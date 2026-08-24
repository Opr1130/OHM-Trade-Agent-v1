import json
from datetime import datetime, timezone
from types import SimpleNamespace

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
