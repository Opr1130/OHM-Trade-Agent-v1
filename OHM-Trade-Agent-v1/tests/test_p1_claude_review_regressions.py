import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.p1_shadow_outbox import (
    append_live_scan_snapshots,
    drain_outbox_to_evidence_ledger,
    outbox_health,
)
from app.services.signal_quality_phase3c import Phase3CRow, build_phase3c_report


NOW = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)
BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candidate(symbol, **overrides):
    fields = dict(
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
        suppressed=False,
        reasons=(),
        components={},
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


def phase3c_row(index, *, complete=True, return_4h=1.0):
    return Phase3CRow(
        episode_id=f"E{index}",
        snapshot_id=f"S{index}",
        symbol=f"C{index}USD",
        decision_at_utc=BASE + timedelta(hours=index),
        candidate_rank=1,
        reference_price=10.0,
        liquidity_24h_usd_approx=2_500_000.0,
        stage="BREAKOUT_CANDIDATE",
        opportunity_score=75,
        tradeability_score=70,
        explosion_potential_score=72,
        persistence_scans=3,
        exhaustion_penalty=10,
        suppressed=False,
        return_15m_pct=0.2,
        return_30m_pct=0.3,
        return_60m_pct=0.4,
        return_4h_pct=return_4h,
        mfe_24h_pct=1.5 if complete else None,
        mae_24h_pct=-0.5 if complete else None,
        window_complete=complete,
    )


def test_next_producer_append_repairs_partial_tail_and_preserves_good_snapshot(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"

    assert append_live_scan_snapshots(
        [candidate("AUSD")],
        decision_at=NOW,
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    ) == 1

    # Simulate a producer crash partway through the next JSON object.
    with outbox.open("ab") as handle:
        handle.write(b'{"snapshot_i')

    # The next valid append must first terminate the orphan tail. It must not
    # concatenate BUSD onto the damaged fragment.
    assert append_live_scan_snapshots(
        [candidate("BUSD")],
        decision_at=NOW + timedelta(minutes=10),
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    ) == 1

    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    evidence = [json.loads(line) for line in ledger.read_text().splitlines()]

    assert result.processed == 2
    assert result.malformed == 1
    assert [row["symbol"] for row in evidence] == ["AUSD", "BUSD"]


def test_non_finite_metrics_are_json_safe_and_do_not_truncate_ranked_batch(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    dead = tmp_path / "dead.jsonl"
    rows = [
        candidate("AUSD"),
        candidate("BUSD"),
        candidate("CUSD", liquidity_24h_usd_approx=float("nan")),
        candidate("DUSD"),
        candidate("EUSD"),
    ]

    written = append_live_scan_snapshots(
        rows,
        decision_at=NOW,
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    )
    persisted = [json.loads(line) for line in outbox.read_text().splitlines()]

    assert written == 5
    assert [row["symbol"] for row in persisted] == [
        "AUSD",
        "BUSD",
        "CUSD",
        "DUSD",
        "EUSD",
    ]
    assert persisted[2]["liquidity_24h_usd_approx"] is None


def test_one_bad_candidate_is_dead_lettered_without_losing_lower_ranks(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    dead = tmp_path / "dead.jsonl"
    rows = [
        candidate("AUSD"),
        candidate("BUSD"),
        candidate("BADUSD", opportunity_score="not-an-int"),
        candidate("DUSD"),
        candidate("EUSD"),
    ]

    written = append_live_scan_snapshots(
        rows,
        decision_at=NOW,
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    )
    persisted = [json.loads(line) for line in outbox.read_text().splitlines()]
    dead_rows = [json.loads(line) for line in dead.read_text().splitlines()]

    assert written == 4
    assert [row["symbol"] for row in persisted] == ["AUSD", "BUSD", "DUSD", "EUSD"]
    assert [row["candidate_rank"] for row in persisted] == [1, 2, 4, 5]
    assert dead_rows[-1]["dead_letter_source"] == "P1_OUTBOX_PRODUCER"
    assert dead_rows[-1]["candidate_rank"] == 3
    assert dead_rows[-1]["symbol"] == "BADUSD"


def test_gate0_rejects_observed_but_truncated_4h_holdout_outcomes():
    rows = [phase3c_row(i) for i in range(10)]
    # 60/20/20 chronological split => last two rows are holdout. Their 4h
    # values are non-None, but the full forward window is not complete.
    rows[-2] = phase3c_row(8, complete=False, return_4h=2.0)
    rows[-1] = phase3c_row(9, complete=False, return_4h=3.0)

    report = build_phase3c_report(
        rows,
        min_bucket_episodes=2,
        min_holdout_episodes=2,
        bootstrap_resamples=20,
    )

    assert report["split"]["test_episodes"] == 2
    assert report["data_quality"]["test_observed_4h_outcome_episodes"] == 2
    assert report["data_quality"]["test_primary_outcome_episodes"] == 0
    assert report["data_quality"]["test_truncated_4h_outcome_episodes"] == 2
    assert report["promotion_gate"]["gate0_ready"] is False
    # Incomplete rows are also excluded from the reported 4h metric itself.
    assert report["overall"]["returns"]["4h"]["n"] == 8


def test_checkpoint_ahead_of_rotated_outbox_is_explicitly_unhealthy(tmp_path):
    outbox = tmp_path / "outbox.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    ledger = tmp_path / "ledger.jsonl"
    dead = tmp_path / "dead.jsonl"

    outbox.write_text("{}\n{}\n{}\n", encoding="utf-8")
    checkpoint.write_text(json.dumps({"next_line": 5}), encoding="utf-8")

    health = outbox_health(outbox_path=outbox, checkpoint_path=checkpoint)
    result = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )

    assert health["checkpoint_ahead_of_outbox"] is True
    assert health["status"] == "CHECKPOINT_AHEAD_OF_OUTBOX"
    assert result.stopped_on_error is True
    assert result.error_type == "CHECKPOINT_AHEAD_OF_OUTBOX"
