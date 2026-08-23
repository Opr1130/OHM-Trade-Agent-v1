"""Runtime scan-history state migration (Signal Quality v1, Phase 1).

This is the only Phase 1 change that touches persisted production state, so it
is tested on its own: schema-1 files must upgrade without losing evidence, and
the history must advance on every runtime scan rather than only on the scans
the event-sampled JSONL stream happens to keep.
"""

from datetime import datetime, timedelta, timezone

from app.services.full_market_observation import (
    DEFAULT_HISTORY_SCANS,
    HISTORY_SCHEMA_VERSION,
    _append_history,
    load_history_state,
    process_full_market_observations,
)
from app.services.registry_io import load_json
from app.services.signal_features import ObservationSnapshot


BASE_TIME = datetime(2026, 8, 22, tzinfo=timezone.utc)


def _snapshot(*, price=100.0, notional=1_000_000.0, at=BASE_TIME):
    return ObservationSnapshot(
        observed_at=at,
        last_price=price,
        volume_24h=notional / price,
        notional_24h_usd_approx=notional,
        high_24h=price,
        low_24h=price * 0.95,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=0.0,
    )


class FakeKraken:
    """Two markets: one thin, one liquid. Mirrors the existing fixture style."""

    def __init__(self, low_price=1.01):
        self.low_price = low_price

    def get_asset_pairs(self):
        return {
            "LOWUSD": {"altname": "LOWUSD", "wsname": "LOW/USD"},
            "BIGUSD": {"altname": "BIGUSD", "wsname": "BIG/USD"},
        }

    def get_tickers(self, pairs):
        data = {
            "LOWUSD": {
                "last": self.low_price,
                "high_24h": 1.06,
                "low_24h": 1.00,
                "volume_24h": 2_000.0,
            },
            "BIGUSD": {
                "last": 12.0,
                "high_24h": 12.1,
                "low_24h": 10.0,
                "volume_24h": 100_000.0,
            },
        }
        return {pair: data[pair] for pair in pairs if pair in data}


def test_schema_1_state_migrates_without_discarding_existing_evidence():
    legacy_state = {
        "latest_by_symbol": {
            "BIGUSD": {
                "last_price": 12.0,
                "volume_24h": 100_000.0,
                "notional_24h_usd_approx": 1_200_000.0,
                "high_24h": 12.1,
                "low_24h": 10.0,
                "lift_from_24h_low_pct": 20.0,
                "distance_from_24h_high_pct": 0.83,
                "recorded_at": BASE_TIME.isoformat(),
            }
        },
        "last_scan_at": BASE_TIME.isoformat(),
    }

    history = load_history_state(legacy_state, history_scans=DEFAULT_HISTORY_SCANS)

    assert "BIGUSD" in history
    assert len(history["BIGUSD"]) == 1
    assert history["BIGUSD"][0].last_price == 12.0
    assert history["BIGUSD"][0].observed_at == BASE_TIME
    # Migration is additive: the schema-1 block is left exactly as it was.
    assert legacy_state["latest_by_symbol"]["BIGUSD"]["last_price"] == 12.0


def test_migration_skips_unparseable_rows_instead_of_failing_the_scan():
    state = {
        "latest_by_symbol": {
            "GOODUSD": {
                "last_price": 5.0,
                "notional_24h_usd_approx": 500_000.0,
                "volume_24h": 100_000.0,
                "high_24h": 5.0,
                "low_24h": 4.0,
                "lift_from_24h_low_pct": 25.0,
                "distance_from_24h_high_pct": 0.0,
                "recorded_at": BASE_TIME.isoformat(),
            },
            "NOTIMEUSD": {"last_price": 5.0},
            "BADUSD": "not-a-row",
        }
    }

    history = load_history_state(state)

    assert set(history) == {"GOODUSD"}


def test_history_ring_buffer_is_bounded_and_ordered():
    history: dict[str, list[ObservationSnapshot]] = {}
    for index in range(12):
        _append_history(
            history,
            "BIGUSD",
            _snapshot(price=100.0 + index, at=BASE_TIME + timedelta(minutes=10 * index)),
            history_scans=8,
        )

    rows = history["BIGUSD"]
    assert len(rows) == 8
    assert [row.last_price for row in rows] == [104.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0]
    assert rows == sorted(rows, key=lambda row: row.observed_at)


def test_replayed_scan_replaces_rather_than_fabricating_an_interval():
    history: dict[str, list[ObservationSnapshot]] = {}
    _append_history(history, "BIGUSD", _snapshot(price=100.0, at=BASE_TIME), history_scans=8)
    _append_history(history, "BIGUSD", _snapshot(price=101.0, at=BASE_TIME), history_scans=8)

    assert len(history["BIGUSD"]) == 1
    assert history["BIGUSD"][0].last_price == 101.0


def test_quiet_scan_still_advances_history_even_when_not_persisted(tmp_path):
    """Learning persistence is not feature persistence.

    The second scan here moves too little for _should_persist to append a JSONL
    event. The runtime history must record it anyway, or persistence and
    continuity would be counted in event-sampled rows.
    """
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"

    first = process_full_market_observations(
        client=FakeKraken(low_price=1.01),
        now=BASE_TIME,
        observation_file=observation_file,
        state_file=state_file,
    )
    assert first.persisted_events == 2

    # A move far below every _should_persist threshold.
    second = process_full_market_observations(
        client=FakeKraken(low_price=1.0102),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
    )
    assert second.persisted_events == 0

    state = load_json(state_file)
    assert state["schema_version"] == HISTORY_SCHEMA_VERSION
    assert len(state["history_by_symbol"]["LOWUSD"]) == 2

    history = load_history_state(state)
    assert [row.last_price for row in history["LOWUSD"]] == [1.01, 1.0102]


def test_history_is_capped_and_drops_markets_that_leave_the_universe(tmp_path):
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"

    for index in range(11):
        process_full_market_observations(
            client=FakeKraken(low_price=1.0 + index * 0.0001),
            now=BASE_TIME + timedelta(minutes=10 * index),
            observation_file=observation_file,
            state_file=state_file,
        )

    state = load_json(state_file)
    assert len(state["history_by_symbol"]["LOWUSD"]) <= DEFAULT_HISTORY_SCANS

    class OnlyBig(FakeKraken):
        def get_asset_pairs(self):
            return {"BIGUSD": {"altname": "BIGUSD", "wsname": "BIG/USD"}}

    process_full_market_observations(
        client=OnlyBig(),
        now=BASE_TIME + timedelta(minutes=200),
        observation_file=observation_file,
        state_file=state_file,
    )

    state = load_json(state_file)
    assert set(state["history_by_symbol"]) == {"BIGUSD"}


def test_legacy_transition_path_is_unchanged_by_the_migration(tmp_path):
    """The schema-1 consumers must not notice schema 2."""
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"

    process_full_market_observations(
        client=FakeKraken(low_price=1.01),
        now=BASE_TIME,
        observation_file=observation_file,
        state_file=state_file,
    )
    second = process_full_market_observations(
        client=FakeKraken(low_price=1.05),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
    )

    low = next(item for item in second.transition_alerts if item.symbol == "LOWUSD")
    assert low.pattern == "COMPRESSION_RELEASE"
    assert low.alert_tier == "WATCH_ONLY"
    assert low.trade_authority_changed is False
    assert low.production_execution_gate_changed is False

    state = load_json(state_file)
    assert "latest_by_symbol" in state
    assert state["latest_by_symbol"]["LOWUSD"]["last_price"] == 1.05


def test_signal_quality_ships_dark_by_default(tmp_path):
    result = process_full_market_observations(
        client=FakeKraken(),
        now=BASE_TIME,
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
    )

    assert result.signal_quality_enabled is False
    assert result.signal_quality_candidates == ()
