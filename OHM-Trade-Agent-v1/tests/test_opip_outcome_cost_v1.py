"""ATDD-OPIP-OUTCOME-COST-V1: deterministic outcome adapter and USD cost estimate."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.opip.analytics import ai_cost_estimate as cost
from app.opip.analytics import outcome_adapter as oa
from app.opip.analytics.scorecard import score_outcomes

pytestmark = pytest.mark.acceptance

T0 = 1_800_000_000.0
H = 3_600.0


def sig(direction="LONG", entry=100.0, stop=95.0, target=110.0, **extra):
    if direction == "SHORT":
        stop, target = (105.0, 90.0) if (stop, target) == (95.0, 110.0) else (stop, target)
    return {"signal_id": "s1", "direction": direction, "entry": entry, "stop": stop,
            "target": target, "ts": T0, "strategy_version": "v1", **extra}


def pts(*pairs):
    return [{"ts": T0 + offset, "price": price} for offset, price in pairs]


def measure(signal, path, **kw):
    kw.setdefault("horizon_seconds", H)
    return oa.measure_outcome(signal, path, **kw)


def test_ac_001_first_level_reached_decides_long_and_short():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-001"""
    up = measure(sig(), pts((300, 101), (600, 111), (900, 94)))
    assert (up.outcome, up.hit, up.exit_price) == (oa.TARGET_HIT, True, 110.0)
    assert up.gross_return_pct == pytest.approx(10.0)
    down = measure(sig(), pts((300, 99), (600, 94), (900, 112)))
    assert (down.outcome, down.hit) == (oa.STOP_HIT, False)
    assert down.gross_return_pct == pytest.approx(-5.0)
    short_win = measure(sig("SHORT"), pts((300, 99), (600, 89)))
    assert (short_win.outcome, short_win.hit) == (oa.TARGET_HIT, True)
    assert short_win.gross_return_pct == pytest.approx(10.0)
    short_loss = measure(sig("SHORT"), pts((300, 101), (600, 106)))
    assert short_loss.outcome == oa.STOP_HIT


def test_ac_001_a_expiry_needs_full_horizon_coverage_and_net_costs_apply():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-001"""
    path = pts((600, 101), (1200, 102), (1800, 103), (2400, 102), (3000, 103), (3600, 104))
    expired = measure(sig(), path, fee_bps=5, slippage_bps=5)
    assert expired.outcome == oa.EXPIRED_NO_HIT and expired.hit is False
    assert expired.gross_return_pct == pytest.approx(4.0)
    assert expired.net_return_pct == pytest.approx(4.0 - 0.2)
    short_path = measure(sig(), path[:3])
    assert (short_path.outcome, short_path.reason) == (oa.INSUFFICIENT_EVIDENCE, oa.R_SHORT_PATH)


def test_ac_001_c_expiry_requires_observed_coverage_through_the_horizon():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-001"""
    quiet = [(i * 600, 101) for i in range(1, 6)]  # last observation at 3000 of 3600
    stale_tail = measure(sig(), pts(*quiet))
    assert (stale_tail.outcome, stale_tail.reason, stale_tail.hit) == (
        oa.INSUFFICIENT_EVIDENCE, oa.R_SHORT_PATH, None)
    assert oa.to_scorecard_record(sig(), stale_tail) is None
    covered = measure(sig(), pts(*quiet, (3700, 101)))
    assert (covered.outcome, covered.hit) == (oa.EXPIRED_NO_HIT, False)
    assert covered.exit_price is None and covered.net_return_pct is None, "no stale price as an exit"
    too_late = measure(sig(), pts(*quiet, (4500, 101)))
    assert too_late.reason == oa.R_SHORT_PATH
    at_edge = measure(sig(), pts(*quiet, (3600, 101)))
    assert at_edge.outcome == oa.EXPIRED_NO_HIT and at_edge.exit_price == 101.0


def test_ac_001_b_hit_exactly_on_boundary_and_horizon_edge():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-001"""
    assert measure(sig(), pts((10, 110))).outcome == oa.TARGET_HIT
    assert measure(sig(), pts((10, 95))).outcome == oa.STOP_HIT
    edge = pts(*[(i * 600, 101) for i in range(1, 6)], (H, 110))
    assert measure(sig(), edge).outcome == oa.TARGET_HIT
    assert measure(sig(), pts((H, 110))).reason == oa.R_GAP
    late = measure(sig(), pts((H + 1, 110)))
    assert (late.outcome, late.reason) == (oa.INSUFFICIENT_EVIDENCE, oa.R_NO_PATH)
    before = measure(sig(), [{"ts": T0 - 5, "price": 111}])
    assert before.reason == oa.R_NO_PATH


def test_ac_002_missing_ordering_is_insufficient_evidence_never_a_hit():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-002"""
    candle = [{"ts": T0 + 300, "low": 94.0, "high": 111.0}]
    both = measure(sig(), candle)
    assert (both.outcome, both.reason, both.hit) == (oa.INSUFFICIENT_EVIDENCE, oa.R_AMBIGUOUS, None)
    gap = measure(sig(), pts((100, 101), (2000, 111)))
    assert (gap.outcome, gap.reason) == (oa.INSUFFICIENT_EVIDENCE, oa.R_GAP)
    assert measure(sig(), []).reason == oa.R_NO_PATH
    assert measure(sig(), pts((100, 101), (200, 102))).reason in (oa.R_GAP, oa.R_SHORT_PATH)
    for bad in ({"direction": None}, {"direction": "FLAT"}):
        assert measure({**sig(), **bad}, pts((10, 111))).reason == oa.R_BAD_DIRECTION
    for field in ("entry", "stop", "target"):
        assert measure({**sig(), field: None}, pts((10, 111))).reason == oa.R_BAD_LEVELS
        assert measure({**sig(), field: float("nan")}, pts((10, 111))).reason == oa.R_BAD_LEVELS
        assert measure({**sig(), field: True}, pts((10, 111))).reason == oa.R_BAD_LEVELS
    assert measure(sig(stop=105.0, target=110.0), pts((10, 111))).reason == oa.R_LEVEL_ORDER
    assert measure({**sig(), "ts": None}, pts((10, 111))).reason == oa.R_BAD_SIGNAL_TIME
    assert measure(sig(), pts((10, 111)), horizon_seconds=None).reason == oa.R_BAD_HORIZON
    assert measure(sig(), [{"ts": "2026-11-14T00:00:00", "price": 111}]).reason == oa.R_BAD_OBSERVATION
    assert measure(sig(), [{"ts": T0 + 5, "price": -1}]).reason == oa.R_BAD_OBSERVATION


def test_ac_002_b_equal_timestamps_touching_both_levels_are_ambiguous_in_either_order():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-002"""
    up_first = pts((300, 110), (300, 95))
    down_first = pts((300, 95), (300, 110))
    for path in (up_first, down_first):
        got = measure(sig(), path)
        assert (got.outcome, got.reason, got.hit) == (oa.INSUFFICIENT_EVIDENCE, oa.R_AMBIGUOUS, None)
    same_side = measure(sig(), pts((300, 110), (300, 112)))
    assert same_side.outcome == oa.TARGET_HIT
    distinct = measure(sig(), pts((300, 95), (301, 110)))
    assert distinct.outcome == oa.STOP_HIT


def test_ac_002_a_scorecard_never_receives_an_insufficient_record():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-002"""
    win = measure(sig(), pts((10, 111)))
    loss = measure(sig(), pts((10, 94)))
    unknown = measure(sig(), [])
    records = [to for to in (oa.to_scorecard_record(sig(), m) for m in (win, loss, unknown)) if to]
    assert len(records) == 2 and all(isinstance(r["hit"], bool) for r in records)
    assert oa.to_scorecard_record(sig(), unknown) is None
    assert score_outcomes(records)["v1"].count == 2
    summary = oa.summarize([win, loss, unknown])
    assert summary["total"] == 3 and summary["determinate"] == 2
    assert summary["counts"][oa.INSUFFICIENT_EVIDENCE] == 1
    assert summary["hit_rate"] == 0.5
    assert oa.summarize([unknown])["hit_rate"] is None
    assert all(r["evidence_class"] == oa.EVIDENCE_CLASS_PRICE_PATH for r in records)


def test_ac_003_excursion_is_separate_and_cannot_produce_a_hit():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-003"""
    candle = [{"ts": T0 + 300, "low": 94.0, "high": 111.0}]
    exc = oa.hypothetical_excursion(sig(), candle, horizon_seconds=H)
    assert exc["status"] == "MEASURED" and exc["hit"] is None
    assert exc["evidence_class"] == oa.EVIDENCE_CLASS_EXCURSION
    assert exc["mfe_pct"] == pytest.approx(11.0) and exc["mae_pct"] == pytest.approx(6.0)
    assert oa.measure_outcome(sig(), candle, horizon_seconds=H).hit is None
    short = oa.hypothetical_excursion(sig("SHORT"), candle, horizon_seconds=H)
    assert short["mfe_pct"] == pytest.approx(6.0) and short["mae_pct"] == pytest.approx(11.0)
    none = oa.hypothetical_excursion(sig(), [], horizon_seconds=H)
    assert none["status"] == oa.INSUFFICIENT_EVIDENCE and none["mfe_pct"] is None


def entry(**over):
    base = dict(provider="OpenAI", model="m-1", input_usd_per_million="2", output_usd_per_million="10",
                effective_from="2026-01-01", source="provider pricing page (test fixture)",
                verified_on="2026-11-01")
    base.update(over)
    return cost.PriceEntry.build(**base)


def usage(**over):
    base = {"timestamp_utc": "2026-11-10T00:00:00+00:00", "provider": "openai", "model": "m-1",
            "input_tokens": 1_000_000, "output_tokens": 500_000, "cached_input_tokens": 0}
    base.update(over)
    return base


def test_ac_004_priced_estimate_is_exact_with_provenance_and_cache_handling():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-004"""
    catalog = cost.PriceCatalog([entry()])
    result = cost.estimate_call_cost(usage(), catalog)
    assert result.status == cost.PRICED and result.usd == Decimal("7")
    assert result.estimate is True and result.invoice is False
    assert result.price_source and result.price_verified_on == date(2026, 11, 1)
    tiny = cost.estimate_call_cost(usage(input_tokens=1, output_tokens=1), catalog)
    assert tiny.usd == Decimal("0.000012")
    cached = cost.estimate_call_cost(usage(cached_input_tokens=400_000), catalog)
    assert cached.usd == Decimal("7") and cached.flags == (cost.FLAG_CACHED_AT_UNCACHED_RATE,)
    discounted = cost.PriceCatalog([entry(cached_input_usd_per_million="0.5")])
    got = cost.estimate_call_cost(usage(cached_input_tokens=400_000), discounted)
    assert got.usd == Decimal("1.2") + Decimal("0.2") + Decimal("5") and got.flags == ()
    reasoning = cost.estimate_call_cost(usage(reasoning_tokens=400_000), catalog)
    assert reasoning.usd == Decimal("7")
    assert cost.estimate_call_cost({**usage(), "provider": None}, catalog, default_provider="openai").status == cost.PRICED


def test_ac_004_a_effective_dates_select_the_price():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-004"""
    catalog = cost.PriceCatalog([
        entry(effective_to="2026-06-30", input_usd_per_million="4"),
        entry(effective_from="2026-07-01"),
    ])
    old = cost.estimate_call_cost(usage(timestamp_utc="2026-06-30T23:59:59+00:00", output_tokens=0), catalog)
    new = cost.estimate_call_cost(usage(timestamp_utc="2026-07-01T00:00:00+00:00", output_tokens=0), catalog)
    before = cost.estimate_call_cost(usage(timestamp_utc="2025-12-31T23:00:00+00:00"), catalog)
    assert (old.usd, new.usd) == (Decimal("4"), Decimal("2"))
    assert (before.status, before.reason) == (cost.UNPRICED, cost.UNPRICED_NO_ENTRY)
    with pytest.raises(cost.PriceCatalogError):
        cost.PriceCatalog([entry(), entry(effective_from="2026-06-01")])


def test_ac_005_no_price_means_no_estimate_never_zero():
    """ATDD-OPIP-OUTCOME-COST-V1/AC-005"""
    catalog = cost.PriceCatalog([entry()])
    unknown = cost.estimate_call_cost(usage(model="other"), catalog)
    assert (unknown.status, unknown.usd) == (cost.UNPRICED, None)
    empty = cost.summarize_costs([usage()], cost.PriceCatalog())
    assert empty["estimated_usd"] == "0" and empty["priced_calls"] == 0
    assert empty["unpriced_calls"] == 1 and empty["complete"] is False
    rows = [usage(), usage(model="other"), usage(input_tokens=None), {**usage(), "timestamp_utc": "nope"},
            usage(input_tokens=5, cached_input_tokens=9), usage(input_tokens=True)]
    summary = cost.summarize_costs(rows, catalog)
    assert summary["priced_calls"] == 1 and summary["unpriced_calls"] == 5 and summary["complete"] is False
    assert summary["estimated_usd"] == "7" and summary["invoice"] is False and summary["estimate"] is True
    assert summary["unpriced_reasons"] == {cost.UNPRICED_NO_ENTRY: 1, cost.UNPRICED_NO_TIME: 1,
                                           cost.UNPRICED_NO_TOKENS: 3}
    for bad in (1.5, "x", "-1", True):
        with pytest.raises(cost.PriceCatalogError):
            entry(input_usd_per_million=bad)
    with pytest.raises(cost.PriceCatalogError):
        entry(effective_to="2025-01-01")
    with pytest.raises(TypeError):
        cost.summarize_costs("rows", catalog)
