"""Focused V2 scoring contract: pure, config-driven, fail-closed, no authority."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.scanner.technical_score_v2 import V2Features, load_v2_config, score_long_v2


def snapshot(**changes):
    values = dict(last_price=102.0, ema20=100.0, ema50=90.0, ema200=80.0,
                  atr=2.0, rsi=56.0, volume_ratio=2.0, macd_line=0.5, macd_signal=0.3)
    values.update(changes)
    return SimpleNamespace(**values)


def features(**changes):
    values = dict(higher_timeframe_ema50_rising=True, adx=30.0,
                  candle_close_location=0.9, macd_histogram_rising=True, atr_percentile=50.0)
    values.update(changes)
    return V2Features(**values)


def test_default_config_is_shadow_and_weights_sum_to_100():
    cfg = load_v2_config()
    assert cfg.mode == "shadow"
    assert cfg.threshold == 75
    assert sum(cfg.weights.values()) == 100


def test_ideal_candidate_scores_100():
    result = score_long_v2(snapshot(), features(), load_v2_config())
    assert result.status == "READY"
    assert result.score == 100.0
    assert result.threshold_met is True


def test_extended_candidate_penalized_without_changing_other_evidence():
    cfg = load_v2_config()
    normal = score_long_v2(snapshot(), features(), cfg)
    extended = score_long_v2(snapshot(last_price=108), features(), cfg)
    assert normal.score > extended.score
    assert extended.components["ema_stack_extension"] == 0


def test_unavailable_htf_is_not_assumed_positive():
    result = score_long_v2(snapshot(), features(higher_timeframe_ema50_rising=None), load_v2_config())
    assert result.status == "INSUFFICIENT_FEATURES"
    assert result.score is None
    assert not result.threshold_met


def test_nonfinite_or_invalid_input_is_not_scored():
    for bad in (float("nan"), -3, 0):
        result = score_long_v2(snapshot(atr=bad), features(), load_v2_config())
        assert result.score is None


def test_bad_config_rejected(tmp_path: Path):
    path = Path(__file__).parents[1] / "app" / "scanner" / "technical_score_v2.json"
    doc = json.loads(path.read_text())
    doc["weights"]["rsi"] = 99
    altered = tmp_path / "bad.json"
    altered.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="V2_CONFIG_WEIGHT_TOTAL"):
        load_v2_config(altered)


def test_production_authority_from_json_rejected(tmp_path: Path):
    path = Path(__file__).parents[1] / "app" / "scanner" / "technical_score_v2.json"
    doc = json.loads(path.read_text())
    doc["mode"] = "production"
    altered = tmp_path / "unsafe.json"
    altered.write_text(json.dumps(doc))
    with pytest.raises(ValueError, match="V2_CONFIG_AUTHORITY"):
        load_v2_config(altered)
