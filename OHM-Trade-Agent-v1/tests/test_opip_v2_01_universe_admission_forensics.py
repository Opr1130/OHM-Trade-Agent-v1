"""Discovery V2-01 universe admission forensics.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.jobs.scan_opportunities import (
    _persist_broad_screening_fail_open,
    _select_production_candidates,
)
from app.opip.discovery.admission import (
    build_callback_evaluation,
    finalize_broad_search_evaluations,
    observation_join_id,
    unavailable_instrument_evaluations,
)
from app.opip.discovery.attribution import (
    attribute_stage0_observation,
    attribution_record,
    attributions_are_exclusive,
)
from app.opip.discovery.constants import (
    ATTRIBUTION_ADMITTED,
    ATTRIBUTION_BELOW_THRESHOLD,
    ATTRIBUTION_NOT_OBSERVED,
    ATTRIBUTION_RANKED_OUTSIDE_BUDGET,
    DISCOVERY_MARKET_OPPORTUNITY_DEFINITION,
    DISCOVERY_OUTCOME_DEFINITION,
    DISCOVERY_V1_MIN_ADVERSE_PCT,
    DISCOVERY_V1_MIN_FAVORABLE_PCT,
    EXCLUSION_PER_DIRECTION_CAP,
    PENDING_FINALIZATION,
    STAGE0_ATTRIBUTION_CATEGORIES,
    WINNER_INCOMPLETE,
    WINNER_NON_WINNER,
    WINNER_WINNER,
)
from app.opip.discovery.earliness import earliness_metrics
from app.opip.discovery.features import decision_features_from_snapshot
from app.opip.discovery.maturation import latest_discovery_outcomes_by_observation
from app.opip.discovery.outcomes import (
    directional_return_pct,
    discovery_v1_barriers,
    label_screening_observation,
)
from app.opip.discovery.replay import (
    forensic_admission_report,
    production_shortlist_fingerprint,
)
from app.opip.discovery.telemetry import ScanComputeTracker, process_rss_bytes
from app.opip.early.point_in_time import LookaheadError, assert_point_in_time_safe
from app.scanner.candidates import MAX_CANDIDATES, MIN_TECHNICAL_SCORE
from app.scanner.directional_candidates import MAX_PER_DIRECTION, select_directional_candidates
from app.scanner.models import MarketSnapshot
from app.services.signal_features import ObservationSnapshot
from app.services.signal_quality_phase2 import ReplayObservation, SymbolTimeline


NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _snapshot(**overrides) -> MarketSnapshot:
    values = dict(
        symbol="BTCUSD",
        last_price=100.0,
        ema20=99.0,
        ema50=98.0,
        ema200=90.0,
        rsi=60.0,
        macd_line=1.0,
        macd_signal=0.5,
        macd_histogram=0.5,
        atr=2.0,
        atr_pct=2.0,
        volume_ratio=2.0,
        technical_score=90,
        trend="bullish",
        primary_pair="XXBTZUSD",
        underlying_asset="BTC",
        ticker_last=100.0,
        recent_24h_high=110.0,
        recent_24h_low=90.0,
        momentum_6h_pct=1.5,
        momentum_24h_pct=3.0,
        momentum_72h_pct=5.0,
        combined_24h_liquidity_usd=1_000_000.0,
        movement_data_status="UNAVAILABLE",
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def _timeline(symbol: str, points: list[tuple[int, float]]) -> SymbolTimeline:
    rows = []
    for minutes, price in points:
        at = NOW + timedelta(minutes=minutes)
        rows.append(
            ReplayObservation(
                at,
                symbol,
                ObservationSnapshot(
                    observed_at=at,
                    last_price=price,
                    volume_24h=1000.0,
                    notional_24h_usd_approx=price * 1000.0,
                    high_24h=price,
                    low_24h=price,
                    lift_from_24h_low_pct=0.0,
                    distance_from_24h_high_pct=0.0,
                ),
            )
        )
    return SymbolTimeline(rows)


def test_min_technical_score_unchanged():
    assert MIN_TECHNICAL_SCORE == 80


def test_max_candidates_and_per_direction_cap_unchanged():
    assert MAX_CANDIDATES == 8
    assert MAX_PER_DIRECTION == 5


def test_discovery_package_cannot_authorize_trade_or_alert():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    forbidden = (
        "place_order",
        "cancel_order",
        "AddOrder",
        "send_trade_plan",
        "qualified_alerts",
        "apply_action_gate",
        "telegram_bot_token",
    )
    files = list((root / "app" / "opip" / "discovery").glob("*.py"))
    files.append(root / "app" / "jobs" / "build_discovery_forward_outcomes.py")
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)
    for token in forbidden:
        assert token not in combined
    assert "MEASUREMENT ONLY" in combined
    assert "NO PRODUCTION DECISION AUTHORITY" in combined


def test_same_snapshots_produce_identical_production_shortlist():
    snapshots = [_snapshot(symbol=f"A{i:03d}USD", primary_pair=f"A{i:03d}USD", underlying_asset=f"A{i:03d}", technical_score=70 + i) for i in range(12)]
    first = production_shortlist_fingerprint(snapshots)
    second = production_shortlist_fingerprint(snapshots)
    assert first == second
    assert len(first) <= MAX_CANDIDATES


def test_hostile_callback_does_not_change_shortlist():
    snapshots = [_snapshot(technical_score=91), _snapshot(symbol="ETHUSD", primary_pair="ETHUSD", underlying_asset="ETH", technical_score=70)]

    def hostile(captured, *_args):
        captured.technical_score = -1
        raise RuntimeError("telemetry down")

    selected = select_directional_candidates(list(snapshots), on_evaluated=hostile)
    control = select_directional_candidates(list(snapshots))
    assert [(item.symbol, item.technical_score, item.trade_direction) for item in selected] == [
        (item.symbol, item.technical_score, item.trade_direction) for item in control
    ]


def test_persist_exception_does_not_alter_candidates(monkeypatch):
    selected = [_snapshot()]
    captured = list(selected)

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "app.jobs.scan_opportunities.append_screening_evaluations",
        boom,
    )
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.finalize_broad_search_evaluations",
        boom,
    )
    _persist_broad_screening_fail_open(
        rows=[],
        selected=selected,
        scan=SimpleNamespace(failures=[], skips=[], data_quality_rejections=[]),
        observed_at=NOW,
        scan_id="OPIPS:test",
        universe_count=1,
        telemetry_enabled=True,
    )
    assert selected[0].technical_score == captured[0].technical_score
    assert selected[0].symbol == captured[0].symbol


def test_discovery_modules_have_no_trade_or_alert_authority():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app" / "opip" / "discovery"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "MEASUREMENT ONLY" in text
        lower = text.lower()
        assert "place_order" not in lower
        assert "send_trade_plan" not in lower
        assert "qualified_alerts" not in lower


def test_below_threshold_asset_is_persisted():
    weak = _snapshot(technical_score=40, rsi=20, trend="bearish", macd_line=-1, volume_ratio=0.4)
    rows = []
    selected = select_directional_candidates(
        [weak],
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="S1", universe_count=1
            )
        ),
    )
    assert selected == []
    finalized = finalize_broad_search_evaluations(rows, selected=selected, observed_at=NOW, universe_count=1)
    assert len(finalized) == 1
    assert finalized[0]["outcome"] == "BELOW_THRESHOLD"
    assert finalized[0]["metadata"]["production_admission_result"] == "BELOW_THRESHOLD"
    assert finalized[0]["long_score"] == 40


def test_rank_cap_is_distinguishable_from_below_threshold():
    snapshots = [
        _snapshot(
            symbol=f"Q{i:02d}USD",
            primary_pair=f"Q{i:02d}USD",
            underlying_asset=f"Q{i:02d}",
            technical_score=99 - i,
        )
        for i in range(10)
    ]
    rows = []
    selected = select_directional_candidates(
        snapshots,
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="S1", universe_count=10
            )
        ),
    )
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=10
    )
    by_outcome = {}
    for row in finalized:
        by_outcome.setdefault(row["outcome"], []).append(row)
    assert "COARSE_RANK_LIMIT" in by_outcome
    assert "ADVANCED" in by_outcome
    assert {row["metadata"]["production_admission_result"] for row in by_outcome["COARSE_RANK_LIMIT"]} == {
        "RANKED_OUTSIDE_BUDGET"
    }
    assert {row["metadata"]["production_admission_result"] for row in by_outcome["ADVANCED"]} == {
        "ADMITTED"
    }
    assert "BELOW_THRESHOLD" not in by_outcome or not by_outcome["BELOW_THRESHOLD"]


def test_admitted_asset_and_long_short_scores_retained():
    snapshot = _snapshot(technical_score=91)
    rows = []
    selected = select_directional_candidates(
        [snapshot],
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="S1", universe_count=1
            )
        ),
    )
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=1
    )
    assert finalized[0]["outcome"] == "ADVANCED"
    assert finalized[0]["long_score"] == 91
    assert finalized[0]["short_score"] is not None
    assert finalized[0]["advanced_direction"] in {"LONG", "SHORT"}
    assert finalized[0]["metadata"]["production_admission_result"] == "ADMITTED"


def test_missing_movement_feature_is_unavailable_not_zero():
    snapshot = _snapshot(movement_data_status="UNAVAILABLE", atr_percentile=100.0)
    features = decision_features_from_snapshot(snapshot, observed_at=NOW, decision_at=NOW)
    payload = features.as_dict()
    assert payload["atr_percentile"] is None
    assert payload["bandwidth_pct"] is None
    assert "atr_percentile" in payload["unavailable_features"]
    assert 0 not in (payload["atr_percentile"], payload["bandwidth_pct"])
    assert_point_in_time_safe(payload)


def test_observation_identity_stable_across_capture_and_outcome_join():
    snapshot = _snapshot()
    row = build_callback_evaluation(
        snapshot, 91, 20, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    obs_id = row["metadata"]["observation_id"]
    expected = observation_join_id(
        scan_id="S1",
        scanner_type="BROAD_SEARCH",
        venue_instrument_id=row["venue_instrument_id"],
        observed_at=row["observed_at"],
    )
    assert obs_id == expected
    timeline = _timeline(row["venue_instrument_id"], [(0, 100.0), (60, 101.0)])
    labeled = label_screening_observation(row, timeline, labeled_at=NOW)
    assert labeled["observation_id"] == obs_id
    assert labeled["scan_id"] == "S1"


def test_future_fields_cannot_enter_decision_metadata():
    with pytest.raises(LookaheadError):
        assert_point_in_time_safe({"mfe_pct": 1.0})
    with pytest.raises(LookaheadError):
        assert_point_in_time_safe({"forward_return": 1.0})
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    assert_point_in_time_safe(row["metadata"])
    assert "mfe_pct" not in row["metadata"]
    assert "discovery_outcome_v1" not in row["metadata"]


def test_incomplete_horizon_is_not_zero_return():
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    timeline = _timeline(row["venue_instrument_id"], [(0, 100.0), (10, 100.5)])
    labeled = label_screening_observation(row, timeline, labeled_at=NOW)
    twelve = labeled["horizons"]["12h"]
    assert twelve["window_complete"] is False
    assert labeled["discovery_outcome_v1"] == WINNER_INCOMPLETE
    assert twelve["horizon_observed"] is True
    assert twelve["horizon_return_pct"] == pytest.approx(0.5)


def test_no_lookahead_from_future_sample():
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    timeline = _timeline(
        row["venue_instrument_id"],
        [(0, 100.0), (30, 101.0), (13 * 60, 180.0)],
    )
    labeled = label_screening_observation(row, timeline, labeled_at=NOW)
    assert labeled["horizons"]["1h"]["mfe_pct"] is not None
    assert labeled["horizons"]["1h"]["mfe_pct"] < 50
    assert labeled["horizons"]["12h"]["mfe_pct"] < 50


def test_long_mfe_mae_math():
    assert directional_return_pct(130.0, 100.0, "LONG") == pytest.approx(30.0)
    assert directional_return_pct(90.0, 100.0, "LONG") == pytest.approx(-10.0)
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    timeline = _timeline(
        row["venue_instrument_id"],
        [(0, 100.0), (30, 90.0), (60, 130.0), (12 * 60, 120.0)],
    )
    labeled = label_screening_observation(row, timeline, labeled_at=NOW + timedelta(hours=13))
    twelve = labeled["horizons"]["12h"]
    assert twelve["mfe_pct"] == pytest.approx(30.0)
    assert twelve["mae_pct"] == pytest.approx(-10.0)


def test_short_mfe_mae_math():
    assert directional_return_pct(90.0, 100.0, "SHORT") == pytest.approx(10.0)
    assert directional_return_pct(120.0, 100.0, "SHORT") == pytest.approx(-20.0)
    row = build_callback_evaluation(
        _snapshot(), 10, 91, "SHORT", observed_at=NOW, scan_id="S1", universe_count=1
    )
    finalized = finalize_broad_search_evaluations(
        [row], selected=[_snapshot(technical_score=91, trade_direction="SHORT")], observed_at=NOW, universe_count=1
    )
    finalized[0]["metadata"]["stronger_direction"] = "SHORT"
    timeline = _timeline(
        finalized[0]["venue_instrument_id"],
        [(0, 100.0), (30, 120.0), (60, 85.0), (12 * 60, 90.0)],
    )
    labeled = label_screening_observation(finalized[0], timeline, labeled_at=NOW + timedelta(hours=13))
    twelve = labeled["horizons"]["12h"]
    assert twelve["mfe_pct"] == pytest.approx(15.0)
    assert twelve["mae_pct"] == pytest.approx(-20.0)


def test_target_before_stop_and_time_to_barriers():
    favorable, adverse, _basis = discovery_v1_barriers(atr_pct=None)
    assert favorable == DISCOVERY_V1_MIN_FAVORABLE_PCT
    assert adverse == -DISCOVERY_V1_MIN_ADVERSE_PCT
    row = build_callback_evaluation(
        _snapshot(atr_pct=0.1), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    timeline = _timeline(
        row["venue_instrument_id"],
        [(0, 100.0), (20, 104.0), (40, 97.0), (12 * 60, 103.0)],
    )
    labeled = label_screening_observation(row, timeline, labeled_at=NOW + timedelta(hours=13))
    twelve = labeled["horizons"]["12h"]
    assert twelve["target_before_stop"] is True
    assert twelve["time_to_favorable_barrier_seconds"] == pytest.approx(20 * 60)
    assert twelve["time_to_adverse_barrier_seconds"] == pytest.approx(40 * 60)
    assert labeled["outcome_definition"] == DISCOVERY_OUTCOME_DEFINITION
    assert labeled["discovery_outcome_v1"] == WINNER_WINNER


def test_insufficient_future_observations_are_explicit():
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    labeled = label_screening_observation(row, None, labeled_at=NOW)
    assert labeled["discovery_outcome_v1"] == WINNER_INCOMPLETE
    assert labeled["horizons"]["1h"]["horizon_return_pct"] is None
    assert labeled["horizons"]["1h"]["horizon_observed"] is False


def test_each_observation_has_exactly_one_stage0_attribution():
    snapshots = [
        _snapshot(symbol="WINUSD", primary_pair="WINUSD", underlying_asset="WIN", technical_score=99),
        _snapshot(symbol="CAPUSD", primary_pair="CAPUSD", underlying_asset="CAP", technical_score=81),
            _snapshot(symbol="LOWUSD", primary_pair="LOWUSD", underlying_asset="LOW", technical_score=40, rsi=20, trend="bearish", volume_ratio=0.4, macd_line=-1),
    ]
    # Force a cap by adding seven more high scores.
    snapshots.extend(
        _snapshot(
            symbol=f"H{i}USD",
            primary_pair=f"H{i}USD",
            underlying_asset=f"H{i}",
            technical_score=98 - i,
        )
        for i in range(7)
    )
    rows = []
    selected = select_directional_candidates(
        snapshots,
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="S1", universe_count=len(snapshots)
            )
        ),
    )
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=len(snapshots)
    )
    categories = [attribute_stage0_observation(row) for row in finalized]
    assert len(categories) == len(finalized)
    assert all(categories)
    by_id = {row["venue_instrument_id"]: attribute_stage0_observation(row) for row in finalized}
    assert ATTRIBUTION_BELOW_THRESHOLD in by_id.values()
    assert ATTRIBUTION_RANKED_OUTSIDE_BUDGET in by_id.values()
    assert ATTRIBUTION_ADMITTED in by_id.values()
    for row in finalized:
        result = row["metadata"]["production_admission_result"]
        category = attribute_stage0_observation(row)
        assert result in STAGE0_ATTRIBUTION_CATEGORIES
        assert category == result
        assert attributions_are_exclusive([category])


def test_downstream_safety_rejection_is_not_an_admission_miss():
    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    finalized = finalize_broad_search_evaluations(
        [row], selected=[_snapshot()], observed_at=NOW, universe_count=1
    )
    category = attribute_stage0_observation(
        finalized[0],
        downstream_rejection="ECONOMIC_QUALITY",
    )
    assert category == ATTRIBUTION_ADMITTED
    record = attribution_record(
        finalized[0],
        downstream_rejection="EXECUTION_INVALID",
    )
    assert record["stage0_attribution"] == ATTRIBUTION_ADMITTED
    assert record["downstream_rejection_ignored"] == "EXECUTION_INVALID"
    assert attribute_stage0_observation(None) == ATTRIBUTION_NOT_OBSERVED


def test_scan_compute_envelope_and_soft_process_metrics(monkeypatch):
    tracker = ScanComputeTracker.start()
    scan = SimpleNamespace(requested=200, analyzed=197, skipped=2, failed=1, data_quality_rejected=0, universe=None)
    envelope = tracker.finish(scan=scan, shortlist=[_snapshot()], scan_id="S1")["compute_envelope"]
    assert envelope["duration_ms"] >= 0
    assert envelope["measurement_scope"] == "BROAD_DISCOVERY_AND_SELECTION"
    assert envelope["broad_discovery_and_selection_duration_ms"] == envelope["duration_ms"]
    assert envelope["universe_requested"] == 200
    assert envelope["instruments_analyzed"] == 197
    assert envelope["shortlist_count"] == 1
    assert envelope["measurement_only"] is True

    def boom():
        raise OSError("no proc")

    monkeypatch.setattr("app.opip.discovery.telemetry.process_rss_bytes", boom)
    monkeypatch.setattr("app.opip.discovery.telemetry.process_cpu_seconds", boom)
    tracker = ScanComputeTracker.start()
    payload = tracker.finish(scan=scan, shortlist=[], scan_id="S1")["compute_envelope"]
    assert payload["rss_status"] == "UNAVAILABLE"
    assert payload["cpu_status"] == "UNAVAILABLE"
    assert payload.get("rss_bytes") is None


def test_telemetry_storage_failure_cannot_fail_production_scan(monkeypatch, caplog):
    selected = select_directional_candidates([_snapshot()])

    def boom(*_args, **_kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr("app.jobs.scan_opportunities.append_screening_evaluations", boom)
    _persist_broad_screening_fail_open(
        rows=[{"broken": True}],
        selected=selected,
        scan=SimpleNamespace(failures=["ZZZUSD: timeout"], skips=[], data_quality_rejections=[]),
        observed_at=NOW,
        scan_id="S1",
        universe_count=1,
        telemetry_enabled=True,
    )
    assert selected[0].technical_score >= MIN_TECHNICAL_SCORE
    assert "failed open" in caplog.text


def test_unavailable_scan_rows_are_data_unavailable():
    scan = SimpleNamespace(
        failures=["MISSUSD: boom"],
        skips=["SKIPUSD: insufficient history (10 candles)"],
        data_quality_rejections=[],
    )
    rows = unavailable_instrument_evaluations(
        scan=scan,
        observed_at=NOW,
        scan_id="S1",
        universe_count=3,
    )
    outcomes = {row["outcome"] for row in rows}
    assert "DATA_UNAVAILABLE" in outcomes


def test_synthetic_universe_attribution_for_three_winner_classes():
    snapshots = []
    for i in range(197):
        score = 40
        if i < 9:
            score = 99 - i
        elif i == 9:
            score = 40
        snapshots.append(
            _snapshot(
                symbol=f"U{i:03d}USD",
                primary_pair=f"U{i:03d}USD",
                underlying_asset=f"U{i:03d}",
                technical_score=score,
                rsi=20 if score < 80 else 60,
                trend="bearish" if score < 80 else "bullish",
            )
        )
    before = production_shortlist_fingerprint(snapshots)
    rows = []
    selected = select_directional_candidates(
        snapshots,
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap,
                long_s,
                short_s,
                direction,
                observed_at=NOW,
                scan_id="SYN",
                universe_count=197,
            )
        ),
    )
    after = production_shortlist_fingerprint(snapshots)
    assert before == after
    assert 5 <= len(selected) <= MAX_CANDIDATES
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=197
    )
    qualifiers = [row for row in finalized if row["metadata"]["threshold_passed"]]
    assert len(qualifiers) > 8
    assert len(finalized) == 197
    report = forensic_admission_report(finalized)
    by_symbol = {row["venue_instrument_id"]: row for row in finalized}
    admitted = by_symbol["U000USD"]
    capped = by_symbol["U008USD"]
    below = by_symbol["U009USD"]
    assert attribute_stage0_observation(admitted) == ATTRIBUTION_ADMITTED
    assert attribute_stage0_observation(capped) == ATTRIBUTION_RANKED_OUTSIDE_BUDGET
    assert attribute_stage0_observation(below) == ATTRIBUTION_BELOW_THRESHOLD
    assert report["stage0_attributions"][admitted["metadata"]["observation_id"]] == ATTRIBUTION_ADMITTED
    assert MIN_TECHNICAL_SCORE == 80


def test_earliness_unavailable_when_history_missing():
    metrics = earliness_metrics([], direction="LONG", favorable_price=None)
    assert metrics["price_at_first_observation"] is None
    assert "price_at_first_observation" in metrics["unavailable_metrics"]
    assert metrics["percent_of_move_consumed_at_admission"] is None


def test_winner_definition_constants_are_explicit():
    favorable, adverse, basis = discovery_v1_barriers(atr_pct=4.0)
    assert basis == "ATR_NORMALIZED"
    assert favorable == pytest.approx(max(3.0, 1.5 * 4.0))
    assert adverse == pytest.approx(-max(2.0, 1.0 * 4.0))
    assert process_rss_bytes() is None or process_rss_bytes() > 0


def test_discovery_job_loads_forward_observations_past_decision_time(tmp_path):
    """The maturation window must extend past decision time (Phase 3C grace)."""
    import json

    from app.jobs.build_discovery_forward_outcomes import build_discovery_outcomes_bounded

    snapshot = _snapshot()
    row = build_callback_evaluation(
        snapshot, 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    finalized = finalize_broad_search_evaluations(
        [row], selected=[snapshot], observed_at=NOW, universe_count=1
    )
    screening_path = tmp_path / "screening_evaluations.jsonl"
    observation_path = tmp_path / "full_market_observations.jsonl"
    output_dir = tmp_path / "discovery"
    screening_path.write_text(json.dumps(finalized[0]) + "\n", encoding="utf-8")
    venue_id = finalized[0]["venue_instrument_id"]
    later = NOW + timedelta(hours=2)
    observation = {
        "record_type": "FULL_MARKET_OBSERVATION",
        "observed_at": later.isoformat(),
        "symbol": venue_id,
        "last_price": 108.0,
        "volume_24h": 1000.0,
        "notional_24h_usd_approx": 108000.0,
        "high_24h": 108.0,
        "low_24h": 100.0,
        "lift_from_24h_low_pct": 8.0,
        "distance_from_24h_high_pct": 0.0,
    }
    observation_path.write_text(json.dumps(observation) + "\n", encoding="utf-8")
    summary = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13),
    )
    assert summary["evaluated"] == 1
    assert summary["trade_authority_changed"] is False
    labeled = json.loads(
        (output_dir / "forward_outcomes.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert labeled["observation_id"] == finalized[0]["metadata"]["observation_id"]
    assert labeled["horizons"]["12h"]["horizon_observed"] is True
    assert labeled["horizons"]["12h"]["mfe_pct"] == pytest.approx(8.0)
    assert "mfe_pct" not in (finalized[0]["metadata"] or {})


def _observation(venue_id: str, at: datetime, price: float) -> dict:
    return {
        "record_type": "FULL_MARKET_OBSERVATION",
        "observed_at": at.isoformat(),
        "symbol": venue_id,
        "last_price": price,
        "volume_24h": 1000.0,
        "notional_24h_usd_approx": price * 1000.0,
        "high_24h": price,
        "low_24h": price,
        "lift_from_24h_low_pct": 0.0,
        "distance_from_24h_high_pct": 0.0,
    }


def _write_jsonl(path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _append_jsonl(path, rows) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _finalize_snapshot(snapshot, *, scan_id: str = "S1") -> dict:
    rows = []
    selected = select_directional_candidates(
        [snapshot],
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap,
                long_s,
                short_s,
                direction,
                observed_at=NOW,
                scan_id=scan_id,
                universe_count=1,
            )
        ),
    )
    return finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=1
    )[0]


def test_incomplete_outcome_matures_to_completed_revision(tmp_path):
    from app.jobs.build_discovery_forward_outcomes import build_discovery_outcomes_bounded

    snapshot = _snapshot()
    finalized = _finalize_snapshot(snapshot)
    observation_id = finalized["metadata"]["observation_id"]
    venue_id = finalized["venue_instrument_id"]
    screening_path = tmp_path / "screening_evaluations.jsonl"
    observation_path = tmp_path / "full_market_observations.jsonl"
    output_dir = tmp_path / "discovery"
    _write_jsonl(screening_path, [finalized])
    _write_jsonl(observation_path, [_observation(venue_id, NOW + timedelta(minutes=5), 101.0)])

    early = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(minutes=10),
    )
    assert early["evaluated"] == 1
    assert early["still_incomplete"] == 1
    first_rows = [
        json.loads(line)
        for line in (output_dir / "forward_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    first = first_rows[0]
    assert first["observation_id"] == observation_id
    assert first["window_complete"] is False
    assert first["horizons"]["12h"]["window_complete"] is False
    assert first["outcome_revision"] == 1

    _append_jsonl(
        observation_path,
        [
            _observation(venue_id, NOW + timedelta(minutes=30), 102.0),
            _observation(venue_id, NOW + timedelta(hours=2), 108.0),
            _observation(venue_id, NOW + timedelta(hours=5), 107.0),
            _observation(venue_id, NOW + timedelta(hours=12), 106.0),
            _observation(venue_id, NOW + timedelta(hours=13), 106.0),
        ],
    )
    later = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13),
    )
    assert later["evaluated"] == 1
    assert later["completed"] == 1
    all_rows = [
        json.loads(line)
        for line in (output_dir / "forward_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    latest = latest_discovery_outcomes_by_observation(all_rows)[observation_id]
    assert latest["outcome_revision"] == 2
    assert latest["window_complete"] is True
    assert latest["horizons"]["1h"]["window_complete"] is True
    assert latest["horizons"]["4h"]["window_complete"] is True
    assert latest["horizons"]["12h"]["window_complete"] is True
    assert latest["horizons"]["1h"]["mfe_pct"] == pytest.approx(2.0)
    assert latest["horizons"]["4h"]["mfe_pct"] == pytest.approx(8.0)
    assert latest["horizons"]["12h"]["mfe_pct"] == pytest.approx(8.0)
    assert latest["horizons"]["12h"]["mae_pct"] is not None
    incomplete = [row for row in all_rows if int(row.get("outcome_revision") or 0) == 1][0]
    assert incomplete["window_complete"] is False
    assert latest["outcome_record_id"] != incomplete["outcome_record_id"]

    rerun = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13, minutes=10),
    )
    assert rerun["evaluated"] == 0
    rerun_rows = [
        json.loads(line)
        for line in (output_dir / "forward_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rerun_rows) == len(all_rows)
    assert latest_discovery_outcomes_by_observation(rerun_rows)[observation_id][
        "outcome_revision"
    ] == 2


def test_attribution_persist_failure_retries_on_reused_outcome(tmp_path, monkeypatch):
    from app.jobs.build_discovery_forward_outcomes import build_discovery_outcomes_bounded
    import app.opip.discovery.maturation as maturation_mod

    snapshot = _snapshot()
    finalized = _finalize_snapshot(snapshot)
    observation_id = finalized["metadata"]["observation_id"]
    venue_id = finalized["venue_instrument_id"]
    screening_path = tmp_path / "screening_evaluations.jsonl"
    observation_path = tmp_path / "full_market_observations.jsonl"
    output_dir = tmp_path / "discovery"
    _write_jsonl(screening_path, [finalized])
    _write_jsonl(
        observation_path,
        [
            _observation(venue_id, NOW + timedelta(minutes=30), 102.0),
            _observation(venue_id, NOW + timedelta(hours=2), 108.0),
            _observation(venue_id, NOW + timedelta(hours=5), 107.0),
            _observation(venue_id, NOW + timedelta(hours=12), 106.0),
            _observation(venue_id, NOW + timedelta(hours=13), 106.0),
        ],
    )

    calls = {"n": 0}
    real_persist = maturation_mod.persist_discovery_attributions

    def flaky_persist(rows, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return set(), 0
        return real_persist(rows, **kwargs)

    monkeypatch.setattr(
        maturation_mod, "persist_discovery_attributions", flaky_persist
    )
    first = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13),
    )
    assert first["completed"] == 1
    assert first.get("attribution_persist_incomplete") is True
    attr_path = output_dir / "attributions.jsonl"
    assert not attr_path.exists() or not attr_path.read_text(encoding="utf-8").strip()

    second = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13, minutes=10),
    )
    assert second["reused_current_revision"] == 1
    assert second.get("attribution_persist_incomplete") is not True
    assert second["written_attributions"] == 1
    attrs = [
        json.loads(line)
        for line in attr_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(row.get("observation_id") == observation_id for row in attrs)

    third = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13, minutes=20),
    )
    assert third["evaluated"] == 0


def _counted_selector(real, calls):
    def counted(
        snapshots,
        *,
        min_score=MIN_TECHNICAL_SCORE,
        limit=MAX_CANDIDATES,
        on_evaluated=None,
        scan_id=None,
    ):
        calls.append({"on_evaluated": on_evaluated is not None, "scan_id": scan_id})
        return real(
            snapshots,
            min_score=min_score,
            limit=limit,
            on_evaluated=on_evaluated,
            scan_id=scan_id,
        )

    return counted


def test_selector_called_exactly_once_telemetry_off(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.select_candidates",
        _counted_selector(select_directional_candidates, calls),
    )
    selected = _select_production_candidates(
        [_snapshot(technical_score=91)],
        telemetry_enabled=False,
        screening_rows=[],
        observed_at=NOW,
        scan_id="S1",
        universe_count=1,
    )
    assert len(calls) == 1
    assert calls[0]["on_evaluated"] is False
    assert len(selected) == 1


def test_selector_called_exactly_once_telemetry_on(monkeypatch):
    calls = []
    rows = []
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.select_candidates",
        _counted_selector(select_directional_candidates, calls),
    )
    selected = _select_production_candidates(
        [_snapshot(technical_score=91)],
        telemetry_enabled=True,
        screening_rows=rows,
        observed_at=NOW,
        scan_id="S1",
        universe_count=1,
    )
    assert len(calls) == 1
    assert calls[0]["on_evaluated"] is True
    assert len(selected) == 1
    assert rows
    assert rows[0]["outcome"] == PENDING_FINALIZATION


def test_hostile_callback_selector_called_once_same_shortlist(monkeypatch):
    snapshots = [
        _snapshot(technical_score=91),
        _snapshot(symbol="ETHUSD", primary_pair="ETHUSD", underlying_asset="ETH", technical_score=70),
    ]
    calls = []
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.select_candidates",
        _counted_selector(select_directional_candidates, calls),
    )

    def boom(*_args, **_kwargs):
        raise RuntimeError("telemetry down")

    monkeypatch.setattr("app.jobs.scan_opportunities.build_callback_evaluation", boom)
    selected = _select_production_candidates(
        list(snapshots),
        telemetry_enabled=True,
        screening_rows=[],
        observed_at=NOW,
        scan_id="S1",
        universe_count=2,
    )
    control = select_directional_candidates(list(snapshots))
    assert len(calls) == 1
    assert [(item.symbol, item.technical_score, item.trade_direction) for item in selected] == [
        (item.symbol, item.technical_score, item.trade_direction) for item in control
    ]


def test_selector_exception_parity_telemetry_on_and_off(monkeypatch):
    def boom(*_args, **_kwargs):
        raise RuntimeError("selector defect")

    monkeypatch.setattr("app.jobs.scan_opportunities.select_candidates", boom)
    for enabled in (False, True):
        with pytest.raises(RuntimeError, match="selector defect"):
            _select_production_candidates(
                [_snapshot()],
                telemetry_enabled=enabled,
                screening_rows=[],
                observed_at=NOW,
                scan_id="S1",
                universe_count=1,
            )


def test_finalize_failure_does_not_persist_provisional_rows(monkeypatch):
    selected = [_snapshot(technical_score=91)]
    captured = list(selected)
    provisional = build_callback_evaluation(
        selected[0], 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    assert provisional["outcome"] == PENDING_FINALIZATION
    persisted = []
    dead = []

    def boom(*_args, **_kwargs):
        raise RuntimeError("finalize exploded")

    monkeypatch.setattr(
        "app.jobs.scan_opportunities.finalize_broad_search_evaluations",
        boom,
    )
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.append_screening_evaluations",
        lambda rows, **_kwargs: persisted.extend(list(rows)) or len(list(rows)),
    )
    monkeypatch.setattr(
        "app.jobs.scan_opportunities.append_qualification_dead_letter",
        lambda rows, **_kwargs: dead.extend(list(rows)) or len(list(rows)),
    )
    _persist_broad_screening_fail_open(
        rows=[provisional],
        selected=selected,
        scan=SimpleNamespace(failures=[], skips=[], data_quality_rejections=[]),
        observed_at=NOW,
        scan_id="S1",
        universe_count=1,
        telemetry_enabled=True,
    )
    assert selected[0].technical_score == captured[0].technical_score
    assert persisted == []
    assert any(row.get("record_type") == "SCREENING_FINALIZATION_FAILED" for row in dead)
    assert attribute_stage0_observation(provisional) == PENDING_FINALIZATION
    assert PENDING_FINALIZATION not in STAGE0_ATTRIBUTION_CATEGORIES
    assert not attributions_are_exclusive([PENDING_FINALIZATION])


def test_below_threshold_wrong_direction_market_winner():
    snapshot = _snapshot(technical_score=60, rsi=20, trend="bearish", macd_line=-1, volume_ratio=0.4)
    row = build_callback_evaluation(
        snapshot, 60, 59, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    finalized = finalize_broad_search_evaluations(
        [row], selected=[], observed_at=NOW, universe_count=1
    )[0]
    assert finalized["metadata"]["production_preferred_direction"] == "LONG"
    assert finalized["metadata"]["production_admission_result"] == "BELOW_THRESHOLD"
    timeline = _timeline(
        finalized["venue_instrument_id"],
        [(15, 90.0), (60, 89.0), (240, 88.0), (720, 87.0), (780, 87.0)],
    )
    labeled = label_screening_observation(finalized, timeline, labeled_at=NOW + timedelta(hours=13))
    assert labeled["production_preferred_direction"] == "LONG"
    assert labeled["production_discovery_outcome_v1"] == WINNER_NON_WINNER
    assert labeled["market_discovery_opportunity_v1"] == WINNER_WINNER
    assert labeled["discovery_outcome_v1"] == WINNER_WINNER
    assert labeled["realized_opportunity_direction"] == "SHORT"
    assert labeled["long_target_before_stop"] is False
    assert labeled["short_target_before_stop"] is True
    assert labeled["short_mfe_pct"] == pytest.approx(13.0)
    assert labeled["market_opportunity_definition"] == DISCOVERY_MARKET_OPPORTUNITY_DEFINITION
    assert labeled["outcome_definition"] == DISCOVERY_OUTCOME_DEFINITION


def test_skipped_worker_backlog_recovers_all_observation_ids(tmp_path):
    from app.jobs.build_discovery_forward_outcomes import build_discovery_outcomes_bounded

    screening_path = tmp_path / "screening_evaluations.jsonl"
    observation_path = tmp_path / "full_market_observations.jsonl"
    output_dir = tmp_path / "discovery"
    screening_path.write_text("", encoding="utf-8")
    observation_path.write_text("", encoding="utf-8")

    def arrive(start: int, count: int) -> list[str]:
        ids = []
        rows = []
        observations = []
        for index in range(start, start + count):
            snapshot = _snapshot(
                symbol=f"B{index:03d}USD",
                primary_pair=f"B{index:03d}USD",
                underlying_asset=f"B{index:03d}",
                technical_score=90,
            )
            finalized = _finalize_snapshot(snapshot, scan_id=f"SCAN{index}")
            rows.append(finalized)
            ids.append(finalized["metadata"]["observation_id"])
            venue_id = finalized["venue_instrument_id"]
            observations.extend(
                [
                    _observation(venue_id, NOW + timedelta(minutes=30), 104.0),
                    _observation(venue_id, NOW + timedelta(hours=12), 105.0),
                    _observation(venue_id, NOW + timedelta(hours=13), 105.0),
                ]
            )
        _append_jsonl(screening_path, rows)
        _append_jsonl(observation_path, observations)
        return ids

    first_wave = arrive(0, 5)
    first = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        max_rows=3,
        now=NOW + timedelta(hours=13),
    )
    assert first["evaluated"] == 3
    skipped_wave = arrive(5, 5)
    # One outcomes cycle is skipped here on purpose.
    for _ in range(8):
        summary = build_discovery_outcomes_bounded(
            screening_path=screening_path,
            observation_path=observation_path,
            output_dir=output_dir,
            max_rows=3,
            now=NOW + timedelta(hours=13, minutes=20),
        )
        if summary["evaluated"] == 0:
            break
    all_rows = [
        json.loads(line)
        for line in (output_dir / "forward_outcomes.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    latest = latest_discovery_outcomes_by_observation(all_rows)
    expected = set(first_wave + skipped_wave)
    assert set(latest) == expected
    assert all(row["window_complete"] is True for row in latest.values())
    assert all(int(row["outcome_revision"] or 0) >= 1 for row in latest.values())


def test_per_direction_cap_exclusion_is_not_global_cutoff():
    longs = [
        _snapshot(
            symbol=f"L{i}USD",
            primary_pair=f"L{i}USD",
            underlying_asset=f"L{i}",
            technical_score=99 - i,
        )
        for i in range(6)
    ]
    shorts = [
        _snapshot(
            symbol=f"S{i}USD",
            primary_pair=f"S{i}USD",
            underlying_asset=f"S{i}",
            technical_score=40,
            last_price=80.0,
            ema20=85.0,
            ema50=90.0,
            ema200=100.0,
            rsi=60.0,
            macd_line=-1.0,
            macd_signal=0.0,
            macd_histogram=-1.0,
            volume_ratio=2.0,
            atr_pct=2.0,
            trend="bearish",
        )
        for i in range(3)
    ]
    snapshots = longs + shorts
    rows = []
    selected = select_directional_candidates(
        snapshots,
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="MIX", universe_count=9
            )
        ),
    )
    selected_ids = {item.symbol for item in selected}
    assert "L5USD" not in selected_ids
    assert any(item.symbol.startswith("S") for item in selected)
    assert any(item.trade_direction == "SHORT" for item in selected)
    long5 = next(item for item in selected if item.symbol == "L0USD")
    assert long5.technical_score > 90
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=9
    )
    by_id = {row["venue_instrument_id"]: row for row in finalized}
    excluded = by_id["L5USD"]
    admitted_short = next(
        row for row in finalized if row["venue_instrument_id"].startswith("S") and row["outcome"] == "ADVANCED"
    )
    assert excluded["metadata"]["production_admission_result"] == "RANKED_OUTSIDE_BUDGET"
    assert excluded["metadata"]["production_exclusion_reason"] == EXCLUSION_PER_DIRECTION_CAP
    assert excluded["long_score"] > admitted_short["short_score"]
    assert admitted_short["metadata"]["production_admission_result"] == "ADMITTED"


def test_measured_v2_01_screening_row_bytes_keep_conservative_budget():
    from app.opip.decision.store import (
        BROAD_SEARCH_SCANS_PER_DAY,
        SCREENING_P95_ROW_BYTES,
        STAGE0_CAPACITY_SAFETY_FACTOR,
        STAGE0_REQUIRED_RECOVERY_DAYS,
    )

    snapshots = []
    for i in range(12):
        score = 99 - i if i < 9 else 40
        snapshots.append(
            _snapshot(
                symbol=f"Z{i:02d}USD",
                primary_pair=f"Z{i:02d}USD",
                underlying_asset=f"Z{i:02d}",
                technical_score=score,
                rsi=20 if score < 80 else 60,
                trend="bearish" if score < 80 else "bullish",
            )
        )
    rows = []
    selected = select_directional_candidates(
        snapshots,
        on_evaluated=lambda snap, long_s, short_s, direction: rows.append(
            build_callback_evaluation(
                snap, long_s, short_s, direction, observed_at=NOW, scan_id="SIZE", universe_count=12
            )
        ),
    )
    finalized = finalize_broad_search_evaluations(
        rows, selected=selected, observed_at=NOW, universe_count=12
    )
    encoded = [
        len(json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        for row in finalized
    ]
    encoded.sort()
    median = encoded[len(encoded) // 2]
    p95_index = max(0, int(round(0.95 * (len(encoded) - 1))))
    p95 = encoded[p95_index]
    assert median > 0
    assert p95 >= median
    assert SCREENING_P95_ROW_BYTES >= p95
    daily_200 = 200 * BROAD_SEARCH_SCANS_PER_DAY * SCREENING_P95_ROW_BYTES
    daily_250 = 250 * BROAD_SEARCH_SCANS_PER_DAY * SCREENING_P95_ROW_BYTES
    footprint_14d_200 = int(
        STAGE0_CAPACITY_SAFETY_FACTOR * daily_200 * STAGE0_REQUIRED_RECOVERY_DAYS
    )
    footprint_14d_250 = int(
        STAGE0_CAPACITY_SAFETY_FACTOR * daily_250 * STAGE0_REQUIRED_RECOVERY_DAYS
    )
    assert footprint_14d_250 >= footprint_14d_200
    assert footprint_14d_200 > 0


def test_pending_finalization_is_not_a_terminal_attribution():
    from app.opip.discovery.attribution import attributions_are_exclusive

    row = build_callback_evaluation(
        _snapshot(), 91, 10, "LONG", observed_at=NOW, scan_id="S1", universe_count=1
    )
    assert attribute_stage0_observation(row) == PENDING_FINALIZATION
    assert PENDING_FINALIZATION not in STAGE0_ATTRIBUTION_CATEGORIES
    assert not attributions_are_exclusive([PENDING_FINALIZATION])
    record = attribution_record(row, labeled_at=NOW)
    assert record["stage0_attribution"] == PENDING_FINALIZATION
    assert record["exclusive"] is False
    assert record["canonical_terminal"] is False



def test_attribution_persistence_is_idempotent_per_observation(tmp_path, monkeypatch):
    import app.opip.discovery.store as store_mod

    first_row = _finalize_snapshot(
        _snapshot(
            symbol="ATTR1USD",
            primary_pair="ATTR1USD",
            underlying_asset="ATTR1",
        ),
        scan_id="ATTR1",
    )
    second_row = _finalize_snapshot(
        _snapshot(
            symbol="ATTR2USD",
            primary_pair="ATTR2USD",
            underlying_asset="ATTR2",
        ),
        scan_id="ATTR2",
    )
    first = attribution_record(first_row, labeled_at=NOW)
    first_retry = attribution_record(
        first_row, labeled_at=NOW + timedelta(minutes=10)
    )
    second = attribution_record(second_row, labeled_at=NOW)
    assert first["attribution_record_id"] == first_retry["attribution_record_id"]
    assert first["attribution_record_id"] != second["attribution_record_id"]

    target = tmp_path / "attributions.jsonl"
    real_append = store_mod.append_discovery_attributions
    calls = {"n": 0}

    def partial_once(rows, **kwargs):
        materialized = list(rows)
        calls["n"] += 1
        if calls["n"] == 1:
            return real_append(materialized[:1], **kwargs)
        return real_append(materialized, **kwargs)

    monkeypatch.setattr(
        store_mod, "append_discovery_attributions", partial_once
    )
    persisted, written = store_mod.persist_discovery_attributions(
        [first, second], path=target
    )
    assert persisted == {first["attribution_record_id"]}
    assert written == 1

    persisted_retry, written_retry = store_mod.persist_discovery_attributions(
        [first_retry, second], path=target
    )
    assert persisted_retry == {
        first["attribution_record_id"],
        second["attribution_record_id"],
    }
    assert written_retry == 1
    logical = store_mod.read_discovery_attributions(path=target)
    assert len(logical) == 2
    assert {
        row["attribution_record_id"] for row in logical
    } == persisted_retry


def test_locked_outcome_append_isolates_invalid_rows(tmp_path):
    from app.opip.discovery.store import (
        append_discovery_forward_outcomes_locked,
    )

    target = tmp_path / "forward_outcomes.jsonl"
    rejected = set()
    rows = [
        {
            "observation_id": "OBS:GOOD",
            "outcome_record_id": "DOUT:GOOD",
            "outcome_revision": 1,
            "value": 1.0,
        },
        {
            "observation_id": "OBS:BAD",
            "outcome_record_id": "DOUT:BAD",
            "outcome_revision": 1,
            "value": float("nan"),
        },
    ]
    written = append_discovery_forward_outcomes_locked(
        rows,
        path=target,
        rejected_observation_ids=rejected,
    )
    assert written == 1
    assert rejected == {"OBS:BAD"}
    stored = [
        json.loads(line)
        for line in target.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["observation_id"] for row in stored] == ["OBS:GOOD"]
    dead = tmp_path / "discovery_dead_letter.jsonl"
    assert dead.exists()
    assert "OBS:BAD" in dead.read_text(encoding="utf-8")


def test_invalid_outcome_does_not_block_valid_maturation_batch(
    tmp_path, monkeypatch
):
    import app.opip.discovery.maturation as maturation_mod
    from app.jobs.build_discovery_forward_outcomes import (
        build_discovery_outcomes_bounded,
    )

    good = _finalize_snapshot(
        _snapshot(
            symbol="GOODUSD",
            primary_pair="GOODUSD",
            underlying_asset="GOOD",
        ),
        scan_id="GOOD",
    )
    bad = _finalize_snapshot(
        _snapshot(
            symbol="BADUSD",
            primary_pair="BADUSD",
            underlying_asset="BAD",
        ),
        scan_id="BAD",
    )
    screening_path = tmp_path / "screening_evaluations.jsonl"
    observation_path = tmp_path / "full_market_observations.jsonl"
    output_dir = tmp_path / "discovery"
    _write_jsonl(screening_path, [good, bad])
    observations = []
    for row in (good, bad):
        venue_id = row["venue_instrument_id"]
        observations.extend(
            [
                _observation(
                    venue_id, NOW + timedelta(minutes=30), 104.0
                ),
                _observation(
                    venue_id, NOW + timedelta(hours=12), 105.0
                ),
                _observation(
                    venue_id, NOW + timedelta(hours=13), 105.0
                ),
            ]
        )
    _write_jsonl(observation_path, observations)

    real_label = maturation_mod.label_screening_observation

    def label_with_one_bad(row, timeline, *, labeled_at=None):
        payload = real_label(row, timeline, labeled_at=labeled_at)
        if row["venue_instrument_id"] == bad["venue_instrument_id"]:
            payload["mfe_pct"] = float("nan")
        return payload

    monkeypatch.setattr(
        maturation_mod, "label_screening_observation", label_with_one_bad
    )
    summary = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13),
    )
    assert summary["evaluated"] == 2
    assert summary["invalid_outcomes_dead_lettered"] == 1
    stored = [
        json.loads(line)
        for line in (
            output_dir / "forward_outcomes.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {row["observation_id"] for row in stored} == {
        good["metadata"]["observation_id"]
    }

    rerun = build_discovery_outcomes_bounded(
        screening_path=screening_path,
        observation_path=observation_path,
        output_dir=output_dir,
        now=NOW + timedelta(hours=13, minutes=10),
    )
    assert rerun["evaluated"] == 0
