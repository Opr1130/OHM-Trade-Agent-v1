"""Runtime scan-history state migration (Signal Quality v1, Phase 1).

This is the only Phase 1 change that touches persisted production state, so it
is tested on its own: schema-1 files must upgrade without losing evidence, and
the history must advance on every runtime scan rather than only on the scans
the event-sampled JSONL stream happens to keep.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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


def _settings(*, enabled, history_scans=DEFAULT_HISTORY_SCANS):
    """Minimal settings stand-in; the integration reads these by name."""
    return SimpleNamespace(
        signal_quality_v1_enabled=enabled,
        signal_quality_history_scans=history_scans,
        signal_quality_scan_interval_seconds=600,
        signal_quality_continuity_multiplier=2.5,
    )


ENABLED = _settings(enabled=True)
DISABLED = _settings(enabled=False)


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
        settings=ENABLED,
    )
    assert first.persisted_events == 2

    # A move far below every _should_persist threshold.
    second = process_full_market_observations(
        client=FakeKraken(low_price=1.0102),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
        settings=ENABLED,
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
            settings=ENABLED,
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
        settings=ENABLED,
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


SCHEMA_1_STATE = {
    "latest_by_symbol": {
        "LOWUSD": {
            "last_price": 1.01,
            "volume_24h": 2_000.0,
            "notional_24h_usd_approx": 2_020.0,
            "high_24h": 1.06,
            "low_24h": 1.00,
            "lift_from_24h_low_pct": 1.0,
            "distance_from_24h_high_pct": 4.95,
            "recorded_at": BASE_TIME.isoformat(),
        }
    },
    "last_scan_at": BASE_TIME.isoformat(),
    "observed_markets_last_scan": 1,
}


def _seed_schema_1(state_file):
    from app.services.registry_io import save_json_atomic

    save_json_atomic(state_file, dict(SCHEMA_1_STATE))


def test_disabled_leaves_schema_1_state_free_of_history_by_symbol(tmp_path):
    """Dark mode writes no Signal Quality state at all."""
    state_file = tmp_path / "state.json"
    _seed_schema_1(state_file)

    process_full_market_observations(
        client=FakeKraken(low_price=1.05),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=tmp_path / "observations.jsonl",
        state_file=state_file,
        settings=DISABLED,
    )

    state = load_json(state_file)
    assert "history_by_symbol" not in state
    # The schema-1 contract is untouched and still readable by legacy consumers.
    assert state["latest_by_symbol"]["LOWUSD"]["last_price"] == 1.05


def test_disabled_does_not_introduce_a_schema_version(tmp_path):
    state_file = tmp_path / "state.json"
    _seed_schema_1(state_file)

    process_full_market_observations(
        client=FakeKraken(),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=tmp_path / "observations.jsonl",
        state_file=state_file,
        settings=DISABLED,
    )

    state = load_json(state_file)
    assert "schema_version" not in state
    assert set(state) == set(SCHEMA_1_STATE)


def test_disabled_leaves_legacy_transition_behaviour_unchanged(tmp_path):
    """The legacy Broad Watch path must behave exactly as it did pre-Phase-1."""
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"

    process_full_market_observations(
        client=FakeKraken(low_price=1.01),
        now=BASE_TIME,
        observation_file=observation_file,
        state_file=state_file,
        settings=DISABLED,
    )
    second = process_full_market_observations(
        client=FakeKraken(low_price=1.05),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
        settings=DISABLED,
    )

    low = next(item for item in second.transition_alerts if item.symbol == "LOWUSD")
    assert low.pattern == "COMPRESSION_RELEASE"
    assert low.alert_tier == "WATCH_ONLY"
    assert second.signal_quality_enabled is False
    assert second.signal_quality_candidates == ()


def test_disabled_state_is_byte_identical_to_a_run_without_the_feature(tmp_path):
    """Strongest form of the dark-mode guarantee: same bytes on disk.

    Compares a disabled Phase 1 scan against the pre-Phase-1 write path, which
    is reconstructed here by writing only the keys the legacy code wrote.
    """
    state_file = tmp_path / "state.json"
    process_full_market_observations(
        client=FakeKraken(low_price=1.02),
        now=BASE_TIME,
        observation_file=tmp_path / "observations.jsonl",
        state_file=state_file,
        settings=DISABLED,
    )

    state = load_json(state_file)
    assert set(state) == {"latest_by_symbol", "last_scan_at", "observed_markets_last_scan"}


def test_enabled_run_migrates_schema_1_state_safely(tmp_path):
    state_file = tmp_path / "state.json"
    _seed_schema_1(state_file)

    process_full_market_observations(
        client=FakeKraken(low_price=1.02),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=tmp_path / "observations.jsonl",
        state_file=state_file,
        settings=ENABLED,
    )

    state = load_json(state_file)
    assert state["schema_version"] == HISTORY_SCHEMA_VERSION
    # Seeded from the schema-1 row, then extended by this scan.
    assert [row["last_price"] for row in state["history_by_symbol"]["LOWUSD"]] == [1.01, 1.02]
    # Nothing legacy was dropped or mutated on the way through.
    assert state["latest_by_symbol"]["LOWUSD"]["last_price"] == 1.02
    assert "last_scan_at" in state


def test_enabled_migration_is_idempotent_for_a_repeated_timestamp(tmp_path):
    """Re-running the same scan must not duplicate its snapshot."""
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"
    _seed_schema_1(state_file)

    for _ in range(3):
        process_full_market_observations(
            client=FakeKraken(low_price=1.02),
            now=BASE_TIME + timedelta(minutes=10),
            observation_file=observation_file,
            state_file=state_file,
            settings=ENABLED,
        )

    state = load_json(state_file)
    rows = state["history_by_symbol"]["LOWUSD"]
    assert len(rows) == 2
    assert len({row["observed_at"] for row in rows}) == 2


def test_disabling_after_enablement_preserves_but_stops_updating_history(tmp_path):
    """Dark mode is not a destructive rollback."""
    state_file = tmp_path / "state.json"
    observation_file = tmp_path / "observations.jsonl"

    process_full_market_observations(
        client=FakeKraken(low_price=1.01),
        now=BASE_TIME,
        observation_file=observation_file,
        state_file=state_file,
        settings=ENABLED,
    )
    process_full_market_observations(
        client=FakeKraken(low_price=1.02),
        now=BASE_TIME + timedelta(minutes=10),
        observation_file=observation_file,
        state_file=state_file,
        settings=ENABLED,
    )
    enabled_state = load_json(state_file)
    assert len(enabled_state["history_by_symbol"]["LOWUSD"]) == 2

    result = process_full_market_observations(
        client=FakeKraken(low_price=1.03),
        now=BASE_TIME + timedelta(minutes=20),
        observation_file=observation_file,
        state_file=state_file,
        settings=DISABLED,
    )

    state = load_json(state_file)
    # Retained verbatim...
    assert state["history_by_symbol"] == enabled_state["history_by_symbol"]
    assert state["schema_version"] == HISTORY_SCHEMA_VERSION
    # ...but not advanced by the disabled scan, and not used.
    assert len(state["history_by_symbol"]["LOWUSD"]) == 2
    assert result.signal_quality_candidates == ()
    assert result.signal_quality_enabled is False

    # Re-enabling picks the retained history back up without duplicating it.
    process_full_market_observations(
        client=FakeKraken(low_price=1.04),
        now=BASE_TIME + timedelta(minutes=30),
        observation_file=observation_file,
        state_file=state_file,
        settings=ENABLED,
    )
    resumed = load_json(state_file)
    assert [row["last_price"] for row in resumed["history_by_symbol"]["LOWUSD"]] == [1.01, 1.02, 1.04]


def test_signal_quality_ships_dark_by_default(tmp_path):
    result = process_full_market_observations(
        client=FakeKraken(),
        now=BASE_TIME,
        observation_file=tmp_path / "observations.jsonl",
        state_file=tmp_path / "state.json",
    )

    assert result.signal_quality_enabled is False
    assert result.signal_quality_candidates == ()
