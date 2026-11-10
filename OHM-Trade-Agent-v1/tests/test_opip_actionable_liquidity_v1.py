"""ATDD - ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1 slice P0.

Verified depth measurement, tri-state evidence and execution-feasibility
separation. Pure unit tests over constructed books: no clock, no network, no
Telegram, no AI, no registry writes.
"""

from __future__ import annotations

import pytest

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.exchanges.kraken import BookLevel, PreTradeBook
from app.scanner import execution_validation as ev

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
MID = 100.0
BAND = 0.50
ACTIONABLE = 600_000.0
MONITORING = 500_000.0

# Ask levels sit at 100.00 (inside the 100.50 boundary); bid levels at 99.60
# (inside the 99.50 boundary) and strictly below the ask, so the book is never
# crossed. Unit quantity 1000 keeps every product an exact short decimal.
ASK_PX = 100.0
BID_PX = 99.6
UNIT_QTY = 1000.0


def _side(px: float, target_usd: float) -> list[BookLevel]:
    """Levels worth ``target_usd`` at ``px``, exact for short-decimal targets."""
    unit = px * UNIT_QTY
    levels: list[BookLevel] = []
    remaining = target_usd
    while remaining >= unit - 1e-9 and len(levels) < 400:
        levels.append(BookLevel(price=px, quantity=UNIT_QTY, publication_timestamp=None))
        remaining -= unit
    if remaining > 1e-9:
        levels.append(
            BookLevel(price=px, quantity=round(remaining / px, 8),
                      publication_timestamp=None)
        )
    return levels


def _book(bid_usd: float, ask_usd: float, *, covered: bool = False,
          requested: int | None = None) -> PreTradeBook:
    bids = _side(BID_PX, bid_usd)
    asks = _side(ASK_PX, ask_usd)
    if covered:
        bids.append(BookLevel(price=99.0, quantity=1.0, publication_timestamp=None))
        asks.append(BookLevel(price=101.0, quantity=1.0, publication_timestamp=None))
    if requested is not None:
        while len(bids) < requested:
            bids.append(BookLevel(price=BID_PX, quantity=0.0, publication_timestamp=None))
        while len(asks) < requested:
            asks.append(BookLevel(price=ASK_PX, quantity=0.0, publication_timestamp=None))
    return PreTradeBook(symbol="TESTUSD", bids=bids, asks=asks)


def _depth(book, **overrides):
    kwargs = dict(
        mid=MID,
        band_pct=BAND,
        threshold_usd=ACTIONABLE,
        quote_rate=1.0,
        observed_at=NOW - timedelta(seconds=5),
        now=NOW,
        max_age_seconds=900,
        levels_requested=(max(len(book.bids), len(book.asks)) if book is not None else 0),
    )
    kwargs.update(overrides)
    return ev.depth_evidence(book, **kwargs)

# ---------------------------------------------------------------- A - boundaries
@pytest.mark.acceptance
def test_ac_a2_exactly_six_hundred_thousand_passes_inclusive():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-101.

    AC-A2: exactly 600,000.00 on the weaker side passes (inclusive)."""
    result = _depth(_book(700_000.0, ACTIONABLE))
    assert result.status == ev.DEPTH_PASS
    assert result.weaker_side_usd == Decimal("600000")


def test_ac_a1_below_threshold_is_refused():
    """AC-A1: 599,999.99 on the weaker side is refused when coverage is proven."""
    result = _depth(_book(700_000.0, 599_999.99, covered=True))
    assert result.status == ev.DEPTH_FAIL
    assert result.weaker_side_usd < Decimal("600000")


def test_ac_a3_the_weaker_side_governs():
    """AC-A3: a deep bid cannot rescue a shallow ask."""
    result = _depth(_book(900_000.0, 450_000.0, covered=True))
    assert result.status == ev.DEPTH_FAIL
    assert result.weaker_side_usd == result.ask_usd


def test_ac_a4_monitoring_threshold_is_inclusive_and_never_alerts():
    """AC-A4/A5: 500K is monitoring only; the actionable floor is strictly higher."""
    at = _depth(_book(600_000.0, MONITORING), threshold_usd=MONITORING)
    below = _depth(_book(600_000.0, 499_999.99, covered=True), threshold_usd=MONITORING)
    assert at.status == ev.DEPTH_PASS
    assert below.status == ev.DEPTH_FAIL
    assert ev.DEPTH_ACTIONABLE_USD > ev.DEPTH_MONITORING_USD


# ---------------------------------------------------------------- B - completeness
def test_ac_b3_returned_equals_requested_does_not_prove_coverage():
    """AC-B3: exhausting the request is not the same as covering the band."""
    book = _book(700_000.0, 700_000.0, requested=8)
    result = _depth(book, threshold_usd=10_000_000.0)
    assert result.covered_ask is False
    assert result.status == ev.DEPTH_UNKNOWN


def test_ac_b2_fewer_levels_than_requested_proves_exhaustion():
    """AC-B2: a short response means the book ended, so coverage is proven."""
    book = _book(10_000.0, 10_000.0)
    result = _depth(book, levels_requested=500)
    assert result.covered_bid is True
    assert result.status == ev.DEPTH_FAIL


def test_ac_b8_an_outlier_level_cannot_inflate_band_depth():
    """AC-B8: dust price levels outside the band are excluded."""
    book = _book(10_000.0, 10_000.0, covered=True)
    baseline = _depth(book)
    inflated = PreTradeBook(
        symbol="TESTUSD",
        bids=list(book.bids) + [BookLevel(price=0.01, quantity=10_000_000.0,
                                          publication_timestamp=None)],
        asks=list(book.asks) + [BookLevel(price=100_000.0, quantity=10_000_000.0,
                                          publication_timestamp=None)],
    )
    assert _depth(inflated).weaker_side_usd == baseline.weaker_side_usd


def test_ac_b11_an_empty_book_is_unknown():
    """EC-04: venue maintenance yields UNKNOWN, not a fabricated FAIL."""
    result = _depth(PreTradeBook(symbol="TESTUSD", bids=[], asks=[]))
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.reason in {ev.REASON_BOOK_EMPTY, ev.REASON_BOOK_ONE_SIDED}


def test_ac_b12_a_one_sided_book_is_unknown():
    """EC-06: a missing side is undefined, never zero."""
    result = _depth(PreTradeBook(symbol="TESTUSD", bids=[], asks=_book(1, 1).asks))
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.reason in {ev.REASON_BOOK_ONE_SIDED, ev.REASON_BOOK_EMPTY}


def test_ac_b6_malformed_levels_invalidate_the_book():
    """AC-B6: non-finite levels make the book UNKNOWN."""
    bad = PreTradeBook(
        symbol="TESTUSD",
        bids=[BookLevel(price=BID_PX, quantity=float("nan"),
                        publication_timestamp=None)],
        asks=_book(1, 1).asks,
    )
    assert _depth(bad).status == ev.DEPTH_UNKNOWN


def test_ac_b7_a_crossed_book_is_never_usable():
    """AC-B7: best_bid >= best_ask is UNKNOWN."""
    crossed = PreTradeBook(
        symbol="TESTUSD",
        bids=[BookLevel(price=101.0, quantity=1.0, publication_timestamp=None)],
        asks=[BookLevel(price=100.0, quantity=1.0, publication_timestamp=None)],
    )
    result = _depth(crossed)
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.reason == ev.REASON_BOOK_CROSSED

# ---------------------------------------------------------------- C - marginal bound
def test_ac_c1_depth_uses_the_price_boundary_not_the_average_price():
    """AC-C1: depth beyond the boundary is excluded even when the book is deep."""
    book = PreTradeBook(
        symbol="TESTUSD",
        bids=[BookLevel(price=BID_PX, quantity=10_000.0, publication_timestamp=None)],
        asks=[
            BookLevel(price=100.3, quantity=3_000.0, publication_timestamp=None),
            # far outside the band; a 600K order's average price would still look fine
            BookLevel(price=103.0, quantity=6_000.0, publication_timestamp=None),
        ],
    )
    result = _depth(book, levels_requested=2)
    assert result.ask_usd < Decimal("600000")
    assert result.status == ev.DEPTH_FAIL
    assert result.marginal_ask_price is not None


@pytest.mark.acceptance
def test_ac_c5_depth_and_execution_feasibility_are_independent_contracts():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-112.

    AC-C5: depth and execution feasibility are separate verdicts."""
    book = _book(900_000.0, 900_000.0)
    depth = _depth(book)
    execution = ev.evaluate_execution(
        book=book, validation_notional_usd=2_500.0, ticker_last=MID, now=NOW
    )
    assert depth.status == ev.DEPTH_PASS
    assert execution.status == ev.VALID
    assert not hasattr(depth, "spread_bps")
    assert not hasattr(execution, "weaker_side_usd")


# ---------------------------------------------------------------- D - tri-state
def test_ac_d1_no_book_is_unknown_and_never_zero():
    """AC-D1: missing evidence is UNKNOWN with no numeric substitution."""
    result = _depth(None)
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.weaker_side_usd is None


@pytest.mark.acceptance
def test_ac_d2_partial_evidence_below_threshold_is_unknown_not_fail():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-102.

    AC-D2: an uncovered shortfall cannot be asserted as FAIL."""
    result = _depth(_book(700_000.0, 300_000.0, requested=8))
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.covered_ask is False


@pytest.mark.acceptance
def test_ac_d3_partial_evidence_at_or_above_threshold_is_pass():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-103.

    AC-D3: observed depth is a subset of truth, so >= threshold proves PASS."""
    result = _depth(_book(700_000.0, 700_000.0, requested=8))
    assert result.status == ev.DEPTH_PASS
    assert result.covered_ask is False


def test_ac_d4_stale_quotes_make_depth_unknown_even_when_deep():
    """AC-D4: staleness dominates; a large but old book cannot pass."""
    result = _depth(_book(2_000_000.0, 2_000_000.0),
                    observed_at=NOW - timedelta(seconds=901))
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.reason == ev.REASON_BOOK_STALE


def test_ec09_a_backwards_clock_is_untrustworthy_not_fresh():
    """EC-09: a future observation timestamp is UNKNOWN, never fresh."""
    result = _depth(_book(700_000.0, 700_000.0),
                    observed_at=NOW + timedelta(seconds=60))
    assert result.status == ev.DEPTH_UNKNOWN
    assert result.reason == ev.REASON_CLOCK_UNTRUSTED


@pytest.mark.acceptance
def test_ec01_exact_decimal_arithmetic_at_the_boundary():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-104.

    EC-01: accumulation is exact-decimal; float summation would fail this.

    331 x (0.1 * 0.1) is exactly 3.31 in decimal, but binary floats sum to
    3.3099999999999734 - which would refuse a plan that genuinely passes.
    """
    float_total = 0.0
    for _ in range(331):
        float_total += 0.1 * 0.1
    # NOTE: builtins.sum() is compensated on CPython 3.12+, so the naive
    # accumulator above is the honest demonstration of float drift.
    assert float_total < 3.31
    asks = [BookLevel(price=0.1, quantity=0.1, publication_timestamp=None)
            for _ in range(331)]
    bids = [BookLevel(price=0.0999, quantity=1.0, publication_timestamp=None)
            for _ in range(100)]
    book = PreTradeBook(symbol="TESTUSD", bids=bids, asks=asks)
    result = ev.depth_evidence(
        book, mid=0.09995, band_pct=BAND, threshold_usd=3.31, quote_rate=1.0,
        observed_at=NOW - timedelta(seconds=1), now=NOW, max_age_seconds=900,
        levels_requested=331,
    )
    assert result.status == ev.DEPTH_PASS
    assert result.weaker_side_usd == Decimal("3.31")

# ---------------------------------------------------------------- adapter
@pytest.mark.acceptance
def test_ac_b1_depth_request_is_clamped_and_returns_parsed_book():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-111.

    AC-B1: the count is clamped to the venue ceiling and rows are parsed."""
    from app.exchanges.kraken import KrakenClient

    seen = {}

    class _Stub:
        def request(self, endpoint, params, *, timeout_seconds):
            seen["endpoint"] = endpoint
            seen["params"] = dict(params)
            return {"XXBTZUSD": {
                "bids": [["100.0", "2.0", 1700000000]],
                "asks": [["100.5", "3.0", 1700000001]],
            }}

    client = KrakenClient(transport=_Stub())
    book = client.get_depth("XBTUSD", count=5000)
    assert seen["endpoint"] == "Depth"
    assert seen["params"]["count"] == 500
    assert book.bids[0].price == 100.0 and book.asks[0].quantity == 3.0
    assert book.bids[0].publication_timestamp is not None


def test_ec02_duplicate_price_levels_are_summed_not_overwritten():
    """EC-02: two levels at one price add together."""
    base = PreTradeBook(symbol="TESTUSD", bids=_side(BID_PX, 300_000.0),
                        asks=_side(ASK_PX, 700_000.0))
    merged = _side(BID_PX, 300_000.0)
    doubled = PreTradeBook(
        symbol="TESTUSD",
        bids=merged + [BookLevel(price=BID_PX, quantity=UNIT_QTY,
                                 publication_timestamp=None)],
        asks=_side(ASK_PX, 700_000.0),
    )
    assert _depth(doubled).bid_usd - _depth(base).bid_usd == \
        Decimal(str(BID_PX)) * Decimal(str(UNIT_QTY))

# ---------------------------------------------------------------------------
# F/G/H/I - guarded admission conditions
# ---------------------------------------------------------------------------
from app.services import actionable_admission as admission  # noqa: E402


WINDOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)


def _base_evidence(**overrides):
    """Evidence that satisfies every pre-existing condition (C1..C7)."""
    fields = dict(
        instrument="DOTUSD", venue="KRAKEN", venue_identity_valid=True,
        observed_at=WINDOW - timedelta(seconds=30),
        direction="LONG", entry_style="enter_now", valid_now=True,
        current_price=4.02, entry_price=4.04, entry_low=4.00, entry_high=4.04,
        chase_limit=4.10, market_liquidity_usd=5_000_000.0,
        position_capacity_usd=75_000.0, order_size_usd=500.0, spread_bps=4.0,
        size_slippage_pct=0.10, depth_covered=True, stop_price=3.90,
        target_1=4.40, target_2=4.60, invalidation="close below 3.90",
        economics_available=True, round_trip_fee_pct=0.52,
        deterministic_qualified=True, risk_gate_passed=True,
        ai_override_attempted=False, funded_execution_authorized=False,
        delivery_in_flight=False,
    )
    fields.update(overrides)
    return admission.ActionableEvidence(**fields)


def _policy(**overrides):
    base = dict(require_verified_depth=True, require_calibrated_probability=True,
                require_protection_admission=True, rolling_24h_max_new_entries=2)
    base.update(overrides)
    return admission.ActionableNotificationPolicy(**base)


def _admit(evidence, policy=None):
    return admission.evaluate_actionable_admission(
        evidence=evidence, policy=policy or _policy(), now=WINDOW
    )


def _passing(evidence=None, policy=None):
    """A plan satisfying every guard, used as the positive control."""
    fields = dict(
        depth_status="PASS",
        weaker_side_depth_usd=650_000.0,
        depth_band_pct=0.50,
        calibration_status="CALIBRATED",
        calibrated_probability=0.93,
        calibration_model_version="cal-shadow-v1",
        protection_stamp_fresh=True,
        protection_admissions_suspended=False,
        new_entry_admissions_last_24h=0,
    )
    fields.update(evidence or {})
    return _admit(_base_evidence(**fields), policy)


@pytest.mark.acceptance
def test_depth_pass_must_satisfy_the_active_policy():
    """A prior PASS at a weaker threshold or wider band cannot authorize a new entry."""
    assert _passing({"weaker_side_depth_usd": 599_999.99}).reason == \
        admission.REASON_DEPTH_INSUFFICIENT
    for fields in (
        {"weaker_side_depth_usd": None},
        {"depth_band_pct": None},
        {"depth_band_pct": 0.51},
        {"weaker_side_depth_usd": float("nan")},
    ):
        assert _passing(fields).reason == admission.REASON_DEPTH_UNKNOWN
    assert _passing({"weaker_side_depth_usd": 600_000.0, "depth_band_pct": 0.50}).admitted
    assert _passing({"weaker_side_depth_usd": 650_000.0, "depth_band_pct": 0.40}).admitted


@pytest.mark.acceptance
def test_ac_h1_a_fully_eligible_plan_is_admitted_exactly_once():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-106.

    AC-H1 / L1: the gate is not vacuously closed."""
    verdict = _passing({})
    assert verdict.admitted is True
    assert verdict.condition is None
    assert verdict.net_reward_to_risk is not None
    assert verdict.net_reward_to_risk >= 1.0
    assert verdict.expires_at is not None


def test_ac_h2_a_single_missing_mandatory_element_is_refused():
    """AC-H2: each omission yields its own attributable refusal."""
    cases = {
        "stop_price": admission.REASON_STOP_MISSING,
        "target_1": admission.REASON_TARGET_MISSING,
        "invalidation": admission.REASON_INVALIDATION_MISSING,
    }
    for field, reason in cases.items():
        verdict = _passing({field: None})
        assert verdict.admitted is False, field
        assert reason in verdict.reasons, (field, verdict.reasons)


@pytest.mark.acceptance
def test_feature_disabled_guards_change_nothing():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-105.

    Owner requirement: disabled feature state matches deployed policy."""
    off = admission.ActionableNotificationPolicy()
    assert off.require_verified_depth is False
    assert off.require_calibrated_probability is False
    assert off.require_protection_admission is False
    assert off.rolling_24h_max_new_entries == 0
    # sparse new evidence, guards OFF -> same verdict as the shipped gate
    verdict = _admit(_base_evidence(), policy=off)
    assert verdict.admitted is True


@pytest.mark.acceptance
def test_ac_g1_below_ninety_percent_is_refused():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-107.

    AC-G1: 0.8999 fails; AC-G2: 0.9000 passes the comparison."""
    assert _passing({"calibrated_probability": 0.8999}).reason == \
        admission.REASON_CONFIDENCE_BELOW
    assert _passing({"calibrated_probability": 0.9000}).admitted is True


def test_ac_g4_uncalibrated_evidence_is_refused():
    """AC-G4/G5: no artifact means uncalibrated, and nothing is synthesised."""
    for status in (None, "", "UNCALIBRATED", "HEURISTIC"):
        verdict = _passing({"calibration_status": status,
                            "calibrated_probability": 0.99})
        assert verdict.reason == admission.REASON_CONFIDENCE_NOT_CALIBRATED


def test_ac_g6_invalid_probability_values_are_refused():
    """AC-G6: NaN / out-of-range values are refused, never clamped."""
    for value in (float("nan"), float("inf"), -0.1, 1.5):
        verdict = _passing({"calibrated_probability": value})
        assert verdict.admitted is False
        assert verdict.reason == admission.REASON_CONFIDENCE_INVALID


def test_ac_g7_an_unversioned_artifact_is_untrusted():
    """AC-G7: a probability with no model version is not trustworthy."""
    verdict = _passing({"calibration_model_version": None})
    assert verdict.reason == admission.REASON_CALIBRATION_UNTRUSTED


@pytest.mark.acceptance
def test_ac_f1_suspended_protection_refuses():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-108.

    AC-F1: a fresh stamp reporting suspension refuses new entries."""
    verdict = _passing({"protection_admissions_suspended": True})
    assert verdict.reason == admission.REASON_PROTECTION_SUSPENDED
    assert verdict.condition == admission.CONDITION_PROTECTION


def test_ac_f2_f3_missing_or_stale_stamp_fails_closed():
    """AC-F2/F3: unknown freshness or unknown state refuses."""
    for overrides in ({"protection_stamp_fresh": False},
                      {"protection_stamp_fresh": None},
                      {"protection_admissions_suspended": None}):
        verdict = _passing(overrides)
        assert verdict.reason == admission.REASON_PROTECTION_UNKNOWN, overrides


def test_ac_f8_the_stamp_can_never_grant_authority():
    """AC-F8: a healthy stamp only removes a refusal."""
    verdict = _passing({"protection_admissions_suspended": False,
                        "stop_price": 4.50})
    assert verdict.admitted is False


@pytest.mark.acceptance
def test_ac_i1_rolling_cap_refuses_a_third_entry():
    """ATDD-OPIP-ACTIONABLE-LIQUIDITY-v1/AC-109.

    AC-I1: two admissions in the window exhaust the cap."""
    assert _passing({"new_entry_admissions_last_24h": 2}).reason == \
        admission.REASON_ROLLING_CAP
    assert _passing({"new_entry_admissions_last_24h": 1}).admitted is True


def test_ec17_an_unknown_window_count_fails_closed():
    """EC-17: an unreadable ledger must never be assumed to be zero."""
    verdict = _passing({"new_entry_admissions_last_24h": None})
    assert verdict.reason == admission.REASON_ROLLING_CAP_UNKNOWN


def test_ac_d5_depth_guards_refuse_on_fail_and_unknown():
    """AC-D1/D2: FAIL and UNKNOWN both refuse when the depth guard is on."""
    assert _passing({"depth_status": "FAIL"}).reason == \
        admission.REASON_DEPTH_INSUFFICIENT
    for status in (None, "UNKNOWN", "garbage"):
        assert _passing({"depth_status": status}).reason == \
            admission.REASON_DEPTH_UNKNOWN
