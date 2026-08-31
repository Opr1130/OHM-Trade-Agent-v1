import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import decision_telemetry
from app.services.canonical_episode_capture import (
    append_canonical_episode_snapshots,
    build_canonical_episode_snapshots,
)
from app.services.p1_shadow_outbox import drain_outbox_to_evidence_ledger
from app.services.phase3c_outcomes import build_forward_outcome_labels
from app.services.signal_quality_phase2 import ReplayObservation
from app.services.signal_quality_phase3c import (
    build_phase3c_report,
    deduplicate_first_per_episode,
    join_point_in_time_evidence,
)


NOW = datetime(2026, 8, 27, 4, 30, tzinfo=timezone.utc)


def observation(symbol, price, *, base=None):
    return SimpleNamespace(
        symbol=symbol,
        base_asset=base or symbol.removesuffix("USD"),
        kraken_public_symbol=f"{base or symbol.removesuffix('USD')}/USD",
        last_price=float(price),
        volume_24h=100_000.0,
        notional_24h_usd_approx=float(price) * 100_000.0,
        high_24h=float(price) * 1.05,
        low_24h=float(price) * 0.95,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )


def candidate(symbol, *, suppressed=False):
    return SimpleNamespace(
        symbol=symbol,
        universe_size=3,
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
        reasons=("TEST_REASON",) if suppressed else (),
        components={"near_high": 75.0, "bad": float("nan")},
    )


def replay_observation(minutes, price, symbol="BUSD"):
    return ReplayObservation(
        observed_at=NOW + timedelta(minutes=minutes),
        symbol=symbol,
        snapshot=SimpleNamespace(last_price=float(price)),
    )


def test_canonical_capture_records_every_observed_pair_and_explicit_status():
    observations = [
        observation("BUSD", 2.0, base="B"),
        observation("AUSD", 1.0, base="A"),
        observation("CUSD", 3.0, base="C"),
    ]
    rows = build_canonical_episode_snapshots(
        observations,
        candidates=[
            candidate("AUSD"),
            candidate("CUSD", suppressed=True),
        ],
        decision_at=NOW,
        signal_quality_enabled=True,
    )

    assert [row["symbol"] for row in rows] == ["AUSD", "BUSD", "CUSD"]
    assert len(rows) == len(observations)
    assert len({row["cohort_id"] for row in rows}) == 1
    assert len({row["episode_id"] for row in rows}) == 3
    assert len({row["snapshot_id"] for row in rows}) == 3
    assert all(row["cohort_size"] == 3 for row in rows)

    by_symbol = {row["symbol"]: row for row in rows}
    assert by_symbol["AUSD"]["decision_status"] == "SCORED_ELIGIBLE"
    assert by_symbol["AUSD"]["candidate_rank"] == 1
    assert by_symbol["AUSD"]["components"]["bad"] is None
    assert by_symbol["BUSD"]["decision_status"] == "NOT_SCORED"
    assert by_symbol["BUSD"]["candidate_rank"] is None
    assert by_symbol["BUSD"]["stage"] == "NOT_SCORED"
    assert by_symbol["BUSD"]["reasons"] == ["NO_SIGNAL_QUALITY_CANDIDATE"]
    assert by_symbol["CUSD"]["decision_status"] == "SCORED_SUPPRESSED"
    assert by_symbol["CUSD"]["suppressed"] is True

    assert all(row["affects_ranking"] is False for row in rows)
    assert all(row["affects_telegram"] is False for row in rows)
    assert all(row["affects_pending_setup"] is False for row in rows)
    assert all(row["trade_authority_changed"] is False for row in rows)


def test_canonical_identity_is_deterministic_and_observation_order_independent():
    observations = [
        observation("AUSD", 1.0, base="A"),
        observation("BUSD", 2.0, base="B"),
    ]
    candidates = [candidate("AUSD")]
    first = build_canonical_episode_snapshots(
        observations,
        candidates=candidates,
        decision_at=NOW,
        signal_quality_enabled=True,
    )
    second = build_canonical_episode_snapshots(
        list(reversed(observations)),
        candidates=candidates,
        decision_at=NOW,
        signal_quality_enabled=True,
    )
    assert first == second


def test_canonical_producer_is_dark_unless_explicitly_enabled(tmp_path):
    target = tmp_path / "p1.jsonl"
    written = append_canonical_episode_snapshots(
        [observation("AUSD", 1.0, base="A")],
        decision_at=NOW,
        signal_quality_enabled=False,
        path=target,
        enabled=False,
    )
    assert written == 0
    assert not target.exists()


def test_canonical_rows_use_existing_outbox_and_existing_idempotent_ledger(tmp_path):
    outbox = tmp_path / "p1.jsonl"
    ledger = tmp_path / "ledger.jsonl"
    checkpoint = tmp_path / "checkpoint.json"
    dead = tmp_path / "dead.jsonl"

    written = append_canonical_episode_snapshots(
        [
            observation("AUSD", 1.0, base="A"),
            observation("BUSD", 2.0, base="B"),
        ],
        candidates=[candidate("AUSD")],
        decision_at=NOW,
        signal_quality_enabled=True,
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    )
    assert written == 2

    first = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    assert first.processed == 2
    ledger_rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert {row["record_type"] for row in ledger_rows} == {
        "CANONICAL_EPISODE_SNAPSHOT"
    }
    assert {row["decision_status"] for row in ledger_rows} == {
        "SCORED_ELIGIBLE",
        "NOT_SCORED",
    }

    checkpoint.unlink()
    second = drain_outbox_to_evidence_ledger(
        outbox_path=outbox,
        evidence_path=ledger,
        checkpoint_path=checkpoint,
        dead_letter_path=dead,
    )
    assert second.duplicates == 2
    assert len(ledger.read_text().splitlines()) == 2


def test_zero_candidate_or_signal_quality_disabled_scan_still_reaches_canonical_outbox(
    monkeypatch,
):
    calls = []

    def capture(observations, **kwargs):
        calls.append(
            {
                "symbols": [row.symbol for row in observations],
                "signal_quality_enabled": kwargs["signal_quality_enabled"],
            }
        )
        return len(observations)

    def should_not_run(*args, **kwargs):
        raise AssertionError("candidate-only Phase 3B path must not run")

    monkeypatch.setattr(
        decision_telemetry,
        "append_canonical_episode_snapshots",
        capture,
    )
    monkeypatch.setattr(
        decision_telemetry,
        "collect_phase3b_live_structure",
        should_not_run,
    )

    result = decision_telemetry.record_phase3b_shadow_for_decision(
        [],
        settings=SimpleNamespace(signal_quality_v1_enabled=False),
        decision_at=NOW,
        market_observations=[observation("AUSD", 1.0, base="A")],
    )
    assert result == 0
    assert calls == [
        {
            "symbols": ["AUSD"],
            "signal_quality_enabled": False,
        }
    ]


def test_forward_label_preserves_canonical_episode_for_rejected_or_unscored_row():
    snapshots = [
        {
            "snapshot_id": "SNAP:1",
            "episode_id": "EP:CANONICAL",
            "symbol": "BUSD",
            "decision_at_utc": NOW.isoformat(),
            "reference_price": 2.0,
            "stage": "NOT_SCORED",
            "suppressed": True,
        }
    ]
    labels = build_forward_outcome_labels(
        snapshots,
        [
            replay_observation(0, 2.0),
            replay_observation(15, 2.1),
            replay_observation(30, 2.2),
            replay_observation(60, 2.3),
            replay_observation(240, 2.4),
            replay_observation(1440, 2.5),
        ],
    )
    assert len(labels) == 1
    row = labels[0]
    assert row["episode_id"] == "EP:CANONICAL"
    assert row["canonical_episode_id"] == "EP:CANONICAL"
    assert row["signal_episode_id"] is None
    assert row["offline_label_only"] is True
    assert row["affects_ranking"] is False


def test_phase3c_uses_canonical_episode_without_outcome_and_reads_24h_when_mature():
    snapshot = {
        "snapshot_id": "SNAP:2",
        "episode_id": "EP:2",
        "decision_at_utc": NOW.isoformat(),
        "symbol": "BUSD",
        "candidate_rank": None,
        "reference_price": 2.0,
        "liquidity_24h_usd_approx": 200_000.0,
        "stage": "NOT_SCORED",
        "suppressed": False,
    }

    without_outcome = join_point_in_time_evidence([snapshot])
    assert without_outcome[0].episode_id == "EP:2"
    assert len(deduplicate_first_per_episode(without_outcome)) == 1

    outcome = {
        "symbol": "BUSD",
        "reference_at": NOW.isoformat(),
        "episode_id": "EP:2",
        "horizon_returns_pct": {
            "15m": 1.0,
            "30m": 2.0,
            "60m": 3.0,
            "4h": 4.0,
            "24h": 7.5,
        },
        "mfe_pct": 9.0,
        "max_adverse_excursion_pct": -2.0,
        "window_complete": True,
    }
    joined = join_point_in_time_evidence([snapshot], outcomes=[outcome])
    assert joined[0].return_24h_pct == 7.5

    report = build_phase3c_report(
        joined,
        min_bucket_episodes=1,
        min_holdout_episodes=1,
        bootstrap_resamples=10,
    )
    assert report["overall"]["returns"]["24h"]["mean"] == 7.5
    assert report["auto_promotion_allowed"] is False


def test_bad_market_row_does_not_drop_rest_of_cohort(tmp_path):
    outbox = tmp_path / "p1.jsonl"
    dead = tmp_path / "dead.jsonl"
    bad = SimpleNamespace(symbol="")
    good = observation("AUSD", 1.0, base="A")

    written = append_canonical_episode_snapshots(
        [bad, good],
        candidates=[candidate("AUSD")],
        decision_at=NOW,
        signal_quality_enabled=True,
        path=outbox,
        dead_letter_path=dead,
        enabled=True,
    )

    assert written == 1
    row = json.loads(outbox.read_text().strip())
    assert row["symbol"] == "AUSD"
    assert row["cohort_size"] == 2
    failed = json.loads(dead.read_text().strip())
    assert failed["dead_letter_source"] == "CANONICAL_EPISODE_PRODUCER"
    assert failed["cohort_size"] == 2
    assert failed["affects_live_decisions"] is False
