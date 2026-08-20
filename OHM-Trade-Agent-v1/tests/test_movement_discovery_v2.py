from app.scanner.models import MarketSnapshot
from app.services.movement_discovery_v2 import (
    ENTRY_BREAKOUT,
    ENTRY_HIGH_RISK,
    ENTRY_TOO_EXTENDED,
    MOMENTUM_ACCELERATING,
    MOMENTUM_DECELERATING,
    READY,
    WATCH,
    CoarseMover,
    SocialEvidence,
    WhaleEvidence,
    append_detection_snapshots,
    discover_coarse_movers,
    evaluate_early_mover,
)


def pair(base, quote="USD"):
    return {"altname": f"{base}{quote}", "wsname": f"{base}/{quote}", "status": "online"}


def ticker(last, high, low, volume):
    return {"last": last, "bid": last * 0.999, "ask": last * 1.001,
            "volume_24h": volume, "high_24h": high, "low_24h": low}


class FakeKraken:
    def __init__(self):
        self.pairs = {"FASTUSD": pair("FAST"), "SLOWUSD": pair("SLOW"), "DUSTUSD": pair("DUST")}
        self.tickers = {
            "FASTUSD": ticker(10.0, 10.1, 8.0, 100_000),
            "SLOWUSD": ticker(10.0, 12.0, 9.9, 100_000),
            "DUSTUSD": ticker(1.0, 1.01, 0.70, 100),
        }
        self.calls = []

    def get_asset_pairs(self):
        return self.pairs

    def get_tickers(self, pair_ids):
        self.calls.append(list(pair_ids))
        return {key: self.tickers[key] for key in pair_ids if key in self.tickers}


def snapshot(**overrides):
    values = dict(
        symbol="FASTUSD", last_price=10.0, ema20=9.5, ema50=9.0, ema200=8.0,
        rsi=65, macd_line=1, macd_signal=0.5, macd_histogram=0.5, atr=0.3,
        atr_pct=3.0, volume_ratio=2.8, technical_score=90, trend="bullish",
        movement_data_status="AVAILABLE", movement_volume_ratio=2.8,
        confirmed_price_change_1h_pct=2.2, momentum_6h_pct=5.0,
        momentum_24h_pct=9.0, distance_to_24h_high_pct=0.8,
    )
    values.update(overrides)
    return MarketSnapshot(**values)


def coarse(notional=1_000_000):
    return CoarseMover("FAST", "FASTUSD", "FAST/USD", 10, 100_000, notional,
                       10.1, 8.0, 25.0, 1.0, 100.0)


def test_coarse_discovery_sweeps_all_pairs_and_finds_active_mover():
    client = FakeKraken()
    result = discover_coarse_movers(client, max_candidates=40)
    assert [item.base_asset for item in result] == ["FAST"]
    assert set(client.calls[0]) == {"FASTUSD", "SLOWUSD", "DUSTUSD"}


def test_fresh_acceleration_can_be_ready_and_breakout_eligible():
    signal = evaluate_early_mover(snapshot(), coarse())
    assert signal is not None
    assert signal.stage == READY
    assert signal.momentum_state == MOMENTUM_ACCELERATING
    assert signal.continuation_confidence >= 65
    assert signal.entry_quality >= 65
    assert signal.entry_recommendation == ENTRY_BREAKOUT
    assert signal.alert_eligible is True
    assert signal.actionable is False
    assert signal.continuation_confidence_is_probability is False


def test_weak_volume_penalizes_and_blocks_normal_ready():
    signal = evaluate_early_mover(
        snapshot(confirmed_price_change_1h_pct=3.0, momentum_6h_pct=8.0,
                 momentum_24h_pct=12.0, movement_volume_ratio=0.25, volume_ratio=0.25),
        coarse(),
    )
    assert signal is not None
    assert signal.stage == WATCH
    assert signal.alert_eligible is False
    assert signal.continuation_confidence < signal.discovery_score


def test_low_liquidity_never_becomes_normal_ready_alert():
    signal = evaluate_early_mover(snapshot(), coarse(notional=20_000))
    assert signal is not None
    assert signal.stage == WATCH
    assert signal.entry_recommendation == ENTRY_HIGH_RISK
    assert signal.alert_eligible is False


def test_extended_decelerating_mover_is_not_treated_like_fresh_entry():
    signal = evaluate_early_mover(
        snapshot(confirmed_price_change_1h_pct=0.0, momentum_6h_pct=6.0,
                 momentum_24h_pct=32.0, movement_volume_ratio=1.7, volume_ratio=1.7),
        coarse(),
    )
    assert signal is not None
    assert signal.extended_move is True
    assert signal.momentum_state == MOMENTUM_DECELERATING
    assert signal.entry_recommendation == ENTRY_TOO_EXTENDED
    assert signal.alert_eligible is False


def test_optional_whale_and_social_evidence_strengthen_but_are_not_required():
    base_snapshot = snapshot(confirmed_price_change_1h_pct=1.1, momentum_6h_pct=3.0,
                             momentum_24h_pct=5.0, movement_volume_ratio=1.1, volume_ratio=1.1)
    baseline = evaluate_early_mover(base_snapshot, coarse())
    enriched = evaluate_early_mover(
        base_snapshot, coarse(),
        whale_evidence=WhaleEvidence(available=True, bias="BULLISH", strength=8, source="public-api"),
        social_evidence=SocialEvidence(available=True, bias="BULLISH", strength=6, source="x"),
    )
    assert baseline is not None and enriched is not None
    assert baseline.whale_confirmation == "UNAVAILABLE"
    assert enriched.continuation_confidence > baseline.continuation_confidence
    assert enriched.whale_confirmation == "BULLISH"
    assert enriched.social_confirmation == "BULLISH"


def test_detection_snapshot_capture_is_prospective(tmp_path):
    signal = evaluate_early_mover(snapshot(), coarse())
    assert signal is not None
    path = tmp_path / "movement.jsonl"
    assert append_detection_snapshots([signal], path=str(path)) == 1
    text = path.read_text()
    assert '"record_type": "DETECTION"' in text
    assert "future" not in text.lower()


def test_weak_movement_is_not_promoted():
    signal = evaluate_early_mover(
        snapshot(confirmed_price_change_1h_pct=0.1, momentum_6h_pct=0.3,
                 momentum_24h_pct=0.5, movement_volume_ratio=1.0, volume_ratio=1.0,
                 trend="neutral", distance_to_24h_high_pct=5.0),
        coarse(),
    )
    assert signal is None
