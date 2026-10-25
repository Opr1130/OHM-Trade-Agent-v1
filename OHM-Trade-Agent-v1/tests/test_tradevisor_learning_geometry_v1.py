"""ATDD: learning measurement geometry (N-03 / MR-01 / MR-02 family).

Increment: ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1

A corrupt or structurally incoherent candle must never produce a definitive
measured learning outcome. The native market-data owner
(`app/scanner/market_data_validation.py`) already rejects impossible geometry;
this increment makes the Tradevisor measurement path agree with it, without
importing scanner policy or granting any authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.services import tradevisor_learning as learning

pytestmark = pytest.mark.acceptance

INCREMENT = "ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1"
DECIDED = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
WINDOW_END = DECIDED + timedelta(hours=24)
NOW = WINDOW_END + timedelta(hours=2)


@dataclass
class Bar:
    """A candle carrying a full OHLC set plus an explicit close time."""

    open_time: datetime
    open: float
    high: float
    low: float
    close: float
    close_time: datetime | None = None


def record() -> dict:
    return {
        "dedup_id": "geometry-1",
        "decision": "WATCH",
        "signal": {"market": "BTCUSD", "bar_closed_at": DECIDED.isoformat()},
        "decision_record": {
            "decision": "WATCH",
            "rejection_code": "ADVISORY_EVIDENCE_INCOMPLETE",
            "dedup_id": "geometry-1",
            "market": "BTCUSD",
            "instrument": "BTC/USD",
            "horizon": "SWING",
            "market_regime": "BULLISH",
            "evaluated_at": DECIDED.isoformat(),
            "plan": {
                "entry_low": 99.0,
                "entry_high": 100.0,
                "stop_price": 96.5,
                "target_1": 105.5,
                "target_2": 108.5,
            },
            "allocation": {"recommended_capital": 1_000.0, "risk_dollars": 35.0},
            "evidence": {
                "advisory_qualification": {"status": "SHADOW_ONLY", "missing": []},
                "shadow_proposed_action": "ENTER_NOW",
            },
        },
    }


def hour(index: int) -> datetime:
    return DECIDED + timedelta(hours=index + 1)


def healthy_bar(index: int, **over) -> Bar:
    values = dict(
        open_time=hour(index), open=100.0, high=101.0, low=99.0, close=100.5
    )
    values.update(over)
    return Bar(**values)


def window(bars: int = 24) -> list[Bar]:
    return [healthy_bar(index) for index in range(bars)]


def measure(bars):
    return learning.evaluate_outcome(
        record(),
        now=NOW,
        candles_provider=lambda *_args: list(bars),
        candle_interval=timedelta(hours=1),
    )


def replace(bars: list[Bar], index: int, bar: Bar) -> list[Bar]:
    updated = list(bars)
    updated[index] = bar
    return updated


# AC-001 ----------------------------------------------------------------------
@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_ac_001_non_finite_prices_are_never_measured(field, value):
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-001: non-finite OHLC refuses measurement."""
    bars = replace(window(), 4, healthy_bar(4, **{field: value}))
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"


# AC-002 ----------------------------------------------------------------------
@pytest.mark.parametrize("value", [True, False, "not-a-price", None, object()])
def test_ac_002_non_numeric_and_boolean_prices_are_never_measured(value):
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-002: non-numeric prices refuse measurement."""
    bars = replace(window(), 4, healthy_bar(4, open=value))
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"


# AC-003 ----------------------------------------------------------------------
@pytest.mark.parametrize("value", [0.0, -0.01, -100.0])
def test_ac_003_non_positive_prices_are_never_measured(value):
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-003: non-positive prices refuse measurement."""
    bars = replace(window(), 4, healthy_bar(4, close=value))
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"


# AC-004 ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "geometry",
    [
        {"high": 98.0, "low": 99.0},          # high < low
        {"high": 101.0, "open": 102.0},        # high < open
        {"high": 101.0, "close": 102.0},       # high < close
        {"low": 99.0, "open": 98.0},           # low > open
        {"low": 99.0, "close": 98.0},          # low > close
    ],
)
def test_ac_004_impossible_geometry_is_never_measured(geometry):
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-004: impossible OHLC geometry refuses measurement."""
    bars = replace(window(), 4, healthy_bar(4, **geometry))
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"
    assert outcome["coverage_status"] == "PARTIAL"


# AC-005 ----------------------------------------------------------------------
def test_ac_005_every_candle_invalid_is_a_partial_coverage_non_measurement():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-005: an all-invalid window never measures."""
    bars = [
        healthy_bar(index, open=105.0, high=101.0, low=99.0, close=100.5)
        for index in range(24)
    ]
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"
    assert outcome["status"] not in {
        learning.OUTCOME_MEASURED,
        learning.OUTCOME_NO_ENTRY_TOUCH,
    }


# AC-006 ----------------------------------------------------------------------
def test_ac_006_invalid_out_of_window_data_cannot_corrupt_a_valid_measurement():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-006: out-of-window corruption is ignored."""
    bars = window()
    bars.append(Bar(DECIDED - timedelta(hours=1), 1e9, 101.0, 99.0, 100.5))  # pre-decision
    bars.insert(3, Bar(WINDOW_END, 100.0, 90.0, 110.0, 100.0))                # at the boundary
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_MEASURED
    assert outcome["coverage_status"] == "COMPLETE"


# AC-007 ----------------------------------------------------------------------
def test_ac_007_contradictory_provider_close_time_is_refused():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-007: a contradictory close time refuses measurement."""
    contradictory = healthy_bar(4, close_time=hour(4) + timedelta(hours=3))
    outcome = measure(replace(window(), 4, contradictory))
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "CLOSE_TIME_CONTRADICTION"


# AC-008 ----------------------------------------------------------------------
def test_ac_008_conflicting_open_restatement_is_a_conflict():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-008: a restated open is a conflicting duplicate."""
    bars = window()
    bars.append(healthy_bar(4, open=100.25))  # same bar, different open
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "CONFLICTING_DUPLICATE_CANDLES"


# AC-009 ----------------------------------------------------------------------
def test_ac_009_identical_restatement_still_measures_without_a_fill():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-009: a valid window measures and stays touch-only."""
    bars = window()
    bars.append(healthy_bar(4))
    outcome = measure(bars)
    assert outcome["status"] == learning.OUTCOME_MEASURED
    assert outcome["entry_touched"] is True
    assert outcome["fill_attribution"] == learning.FILL_ATTRIBUTION_NONE
    assert outcome["basis"] == learning.EXPECTANCY_BASIS


# AC-010 ----------------------------------------------------------------------
def test_ac_010_replay_is_deterministic():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-010: replay produces byte-identical evidence."""
    bars = replace(window(), 6, healthy_bar(6, open=98.0))
    first = measure(bars)
    second = measure(bars)
    assert first == second
    assert first["status"] == learning.OUTCOME_PARTIAL_COVERAGE



# AC-011 ----------------------------------------------------------------------
def test_ac_011_shared_predicate_agrees_with_the_native_owner():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-011: one predicate, no drift."""
    from types import SimpleNamespace

    from app.scanner.market_data_validation import validate_market_data
    from app.services import price_geometry

    bars = [
        (100.0, 101.0, 99.0, 100.5),   # coherent
        (105.0, 101.0, 99.0, 100.5),   # open above high
        (100.0, 98.0, 99.0, 100.0),    # high below low
        (100.0, 101.0, 99.0, 106.0),   # close above high
        (95.0, 101.0, 99.0, 100.5),    # open below low
        (0.0, 101.0, 99.0, 100.5),     # non-positive
        (float("nan"), 101.0, 99.0, 100.5),  # non-finite
    ]
    for index, (open_, high, low, close) in enumerate(bars):
        candle = SimpleNamespace(
            open=open_, high=high, low=low, close=close, volume=1.0, vwap=100.0
        )
        prices, problem = price_geometry.validated_ohlc(candle)
        native = validate_market_data(
            [
                SimpleNamespace(
                    timestamp=int((DECIDED + timedelta(hours=1)).timestamp()),
                    open=open_, high=high, low=low, close=close,
                    vwap=100.0, volume=1.0, trade_count=1,
                )
            ],
            ticker_last=None,
            interval_minutes=60,
            now=DECIDED + timedelta(hours=2),
        )
        native_rejects = native.invalid_ohlc_count > 0 or native.non_finite_value_count > 0
        assert (prices is None) == native_rejects, index
        if prices is None:
            assert problem is not None, index


def test_ac_011_the_native_owner_is_not_coupled_to_the_shared_predicate():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-011: equivalence is test-enforced, not coupling."""
    import ast
    from pathlib import Path

    from app.scanner import market_data_validation

    source = Path(market_data_validation.__file__).read_text(encoding="utf-8")
    modules = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    # The native owner keeps its own admission policy and must not be coupled to
    # the measurement predicate; the agreement is asserted by the previous test.
    assert "app.services.price_geometry" not in modules
    assert "price_geometry" not in source

    # The learning path really does share it at runtime.
    from app.services import tradevisor_learning

    learning_source = Path(tradevisor_learning.__file__).read_text(encoding="utf-8")
    assert "from app.services.price_geometry import" in learning_source


# AC-012 ----------------------------------------------------------------------
@pytest.mark.parametrize("missing", ["open", "high", "low", "close"])
def test_ac_012_a_missing_price_field_is_reported_as_absent(missing):
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-012: absent fields are diagnosed, not measured."""
    bar = healthy_bar(4)
    incomplete = Bar(
        open_time=bar.open_time,
        open=getattr(bar, "open") if missing != "open" else None,
        high=getattr(bar, "high") if missing != "high" else None,
        low=getattr(bar, "low") if missing != "low" else None,
        close=getattr(bar, "close") if missing != "close" else None,
    )
    outcome = measure(replace(window(), 4, incomplete))
    assert outcome["status"] == learning.OUTCOME_PARTIAL_COVERAGE
    assert outcome["coverage_reason"] == "INVALID_OHLC"
    assert outcome["ohlc_problem"] == "MISSING_FIELD"


def test_ac_012_the_problem_class_distinguishes_absent_from_corrupt():
    """ATDD-LEARNING-MEASUREMENT-GEOMETRY-v1/AC-012: absent data and corrupt prices differ."""
    corrupt = measure(replace(window(), 4, healthy_bar(4, open=105.0)))
    assert corrupt["ohlc_problem"] == "IMPOSSIBLE_RANGE"
    non_positive = measure(replace(window(), 4, healthy_bar(4, close=0.0)))
    assert non_positive["ohlc_problem"] == "NON_POSITIVE"
    non_finite = measure(replace(window(), 4, healthy_bar(4, high=float("inf"))))
    assert non_finite["ohlc_problem"] == "NON_FINITE"

