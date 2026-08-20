from app.services.movement_discovery_v2_2_shadow import (
    CURRENT_MOMENTUM,
    EARLY_ACCELERATION,
    REACCELERATION,
    _candidate_for_cohort,
    discover_v22_shadow_candidates,
)


def _ticker(last, high24, low24, high_today, low_today, volume):
    return {
        "last": last,
        "high_24h": high24,
        "low_24h": low24,
        "high_today": high_today,
        "low_today": low_today,
        "volume_24h": volume,
    }


def test_current_momentum_caps_completed_move_in_score():
    moderate = _candidate_for_cohort(
        cohort=CURRENT_MOMENTUM,
        base="MOD",
        quote="USD",
        display_pair="MODUSD",
        ticker=_ticker(20, 20.5, 10, 20.5, 18, 100_000),
    )
    extreme = _candidate_for_cohort(
        cohort=CURRENT_MOMENTUM,
        base="EXT",
        quote="USD",
        display_pair="EXTUSD",
        ticker=_ticker(200, 201, 10, 201, 180, 10_000),
    )
    assert moderate is not None and extreme is not None
    assert extreme.mover.lift_from_24h_low_pct > 1000
    assert extreme.challenger_score < 200


def test_early_acceleration_accepts_smaller_24h_move_with_intraday_lift():
    row = _candidate_for_cohort(
        cohort=EARLY_ACCELERATION,
        base="EARLY",
        quote="USD",
        display_pair="EARLYUSD",
        ticker=_ticker(10.5, 11.0, 10.0, 10.6, 10.2, 100_000),
    )
    assert row is not None
    assert row.cohort == EARLY_ACCELERATION
    assert row.mover.lift_from_24h_low_pct < 15
    assert row.lift_today_pct > 1.5


def test_reacceleration_accepts_pullback_above_midpoint():
    row = _candidate_for_cohort(
        cohort=REACCELERATION,
        base="REACC",
        quote="USD",
        display_pair="REACCUSD",
        ticker=_ticker(11.0, 12.2, 8.0, 11.3, 10.5, 100_000),
    )
    assert row is not None
    assert 6 < row.mover.distance_from_24h_high_pct <= 15


class FakeKraken:
    def get_asset_pairs(self):
        return {
            "EARLYUSD": {"altname": "EARLYUSD", "wsname": "EARLY/USD", "status": "online"},
            "REACCUSD": {"altname": "REACCUSD", "wsname": "REACC/USD", "status": "online"},
            "MOMUSD": {"altname": "MOMUSD", "wsname": "MOM/USD", "status": "online"},
        }

    def get_tickers(self, pair_ids):
        rows = {
            "EARLYUSD": _ticker(10.5, 11.0, 10.0, 10.6, 10.2, 100_000),
            "REACCUSD": _ticker(11.0, 12.2, 8.0, 11.3, 10.5, 100_000),
            "MOMUSD": _ticker(10.0, 10.1, 8.0, 10.1, 9.5, 100_000),
        }
        return {key: rows[key] for key in pair_ids if key in rows}


def test_shadow_selector_diversifies_cohorts_without_duplicates():
    rows = discover_v22_shadow_candidates(FakeKraken(), total_candidates=40)
    assets = [row.mover.base_asset for row in rows]
    assert len(assets) == len(set(assets))
    cohorts = {row.cohort for row in rows}
    assert EARLY_ACCELERATION in cohorts
    assert REACCELERATION in cohorts
    assert CURRENT_MOMENTUM in cohorts
