"""V2 technical cutover contracts: no implicit V1 fallback or mixed thresholds."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.scanner.directional_candidates import (
    qualifying_long_alternatives, select_directional_candidates,
)
from app.scanner.models import MarketSnapshot
from app.services.technical_scoring_comparison import build_scoring_comparison
from app.jobs.scan_opportunities import _select_production_candidates


def snapshot(symbol="TESTUSD", **kw):
    state = dict(
        symbol=symbol,
        last_price=90, ema20=92, ema50=95, ema200=100,
        rsi=45, macd_line=-2, macd_signal=-1, macd_histogram=-1,
        atr=2, atr_pct=2.22, volume_ratio=1.6, technical_score=10,
        trend="bearish", underlying_asset=symbol.removesuffix("USD"),
        v2_long_score=82.0, v2_short_score=78.0,
        v2_feature_status="READY",
    )
    state.update(kw)
    return MarketSnapshot(**state)


@pytest.mark.acceptance
def test_v2_selection_uses_v2_long_and_short_not_v1_scores():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-006: V2 direction is authoritative."""
    selected = select_directional_candidates([snapshot()], scoring_version="v2", min_score=75)
    assert len(selected) == 1
    assert selected[0].trade_direction == "LONG"
    assert selected[0].technical_score == 82
    assert selected[0].directional_long_score == 82
    assert selected[0].directional_short_score == 78
    assert snapshot().technical_score == 10


@pytest.mark.acceptance
def test_missing_v2_features_are_unavailable_not_v1_fallback():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-006: unavailable features cannot admit."""
    called = []
    missing = snapshot(technical_score=100, v2_feature_status="INSUFFICIENT_FEATURES",
                       v2_long_score=None, v2_short_score=None)
    selected = select_directional_candidates(
        [missing], min_score=75, scoring_version="v2",
        on_evaluated=lambda s, l, sh, direction: called.append((l, sh, direction)),
    )
    assert not selected
    assert called == [(None, None, None)]
    assert not qualifying_long_alternatives([missing], min_score=75, scoring_version="v2")


@pytest.mark.acceptance
def test_v2_margin_fallback_requires_independent_v2_long_qualification():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-006: V2 fallback cannot borrow V1."""
    misses = snapshot(symbol="NOUSD", v2_long_score=74.9, technical_score=100)
    hits = snapshot(symbol="YESUSD", v2_long_score=82.1)
    result = qualifying_long_alternatives(
        [misses, hits], scoring_version="v2", min_score=75
    )
    assert [x.symbol for x in result] == ["YESUSD"]
    assert result[0].technical_score == 82


@pytest.mark.acceptance
def test_selector_and_screening_rows_share_v2_threshold():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-006: threshold evidence is consistent."""
    rows = []
    selected = _select_production_candidates(
        [snapshot()], scoring_version="v2", min_score=75,
        telemetry_enabled=True, screening_rows=rows,
        observed_at=datetime.now(timezone.utc), scan_id="unit-test", universe_count=1,
    )
    assert len(selected) == 1
    assert rows
    assert rows[0]["metadata"]["applicable_technical_threshold"] == 75


@pytest.mark.acceptance
def test_comparison_relabels_authority_correctly_on_v2_cutover():
    """ATDD-OPIP-CONFIGURABLE-SCORING-v1/AC-006: V1 becomes evidence shadow."""
    sample = snapshot()
    selected = select_directional_candidates([sample], scoring_version="v2", min_score=75)
    row = build_scoring_comparison(
        [sample], selected, scan_id="unit", observed_at=datetime.now(timezone.utc),
        production_version="v2", v2_threshold=75,
    )
    assert row["model_authority"] == "V2_PRODUCTION_V1_OBSERVATION"
    assert row["v2_top"][0]["score"] == 82
    # The same bearish snapshot independently qualifies in V1 as SHORT.
    # V1 is recorded as shadow, not silently dropped or used as V2 authority.
    assert row["v1_top"][0]["direction"] == "SHORT"
    assert row["v2_top"][0]["direction"] == "LONG"


def test_v2_scoring_rejects_invalid_version():
    with pytest.raises(ValueError, match="UNSUPPORTED_SCORING_VERSION"):
        select_directional_candidates([snapshot()], scoring_version="v3")
