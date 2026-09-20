"""B/C-3 Increment 6B: the Paper-v2 mode split at the scan boundary.

Proves the exclusive authority split through the real scan orchestrator: with the
mode off the existing Freqtrade dry-run and legacy Paper-v1 paths are called
exactly as before and Paper v2 is never reached; with the mode active neither
legacy authority is called and the real router/producer path runs instead. Also
proves the mode-aware lineage metadata and that the default configuration remains
``off``.

Paper v2 is not activated anywhere here: the settings object is a test double and
the repository default is asserted to stay ``off``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.server import CanonicalWriterServer
from app.scanner.market_scanner import ScanResult
from app.scanner.models import MarketSnapshot
from app.scanner.universe import UniverseAsset, UniverseBuildResult
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_v2_scan_router import (
    PaperV2RouterSummary,
    set_kraken_client_for_tests,
    set_writer_client_for_tests,
)

from app.jobs import scan_opportunities

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
CTX_EVENT = "decision_intelligence.context.recorded"
#: The execution instant the router's clock is frozen to. Later than qualification
#: and later than the book's source timestamp, which is the real ordering.
EXECUTION_NOW = NOW + timedelta(seconds=10)
#: Source publication instant of the fixture book. Fresh relative to the frozen
#: execution clock above.
QUOTE_PUBLISHED_AT = NOW - timedelta(seconds=1)


class _Settings:
    """Test settings double. Not production configuration."""

    def __init__(self, *, paper_v2_mode: str = "off", equity: float = 10_000.0) -> None:
        self.opip_paper_v2_mode = paper_v2_mode
        self.account_equity = equity
        self.telegram_bot_token = None
        self.telegram_chat_id = None
        self.paper_v2_quote_max_age_seconds = 15
        self.paper_trade_fee_rate = 0.004
        self.paper_trade_slippage_bps = 10.0
        self.paper_v2_tp1_fraction = 0.5
        self.paper_v2_max_hold_seconds = 86_400
        self.price_movement_mode = "shadow"
        # Read via getattr(settings, ..., default) in the scan, so they must be
        # real attributes rather than absent.
        self.max_margin_leverage = 3.0
        self.openai_model = "test-model"
        self.openai_api_key = None
        self.margin_execution_venue = "BITNOMIAL"
        self.telegram_price_movement_enabled = False
        self.early_watch_alerts_enabled = False
        self.opip_canonical_writer_mode = "off"
        self.opip_feature_bus_mode = "off"


class _EchoTransport:
    def __init__(self, *, requests: list) -> None:
        self._requests = requests

    def request(self, endpoint, params, timeout_seconds):
        self._requests.append((endpoint, params.get("symbol")))
        # Published just before the read returns, relative to real time. The scan's
        # decision boundary comes from the real wall clock (``main`` reads it), so the
        # book must be contemporaneous with it rather than pinned to a fixture time.
        ts = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).replace(microsecond=0).isoformat()
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": 99.9, "qty": 10.0, "publication_ts": ts}],
            "asks": [{"price": 100.0, "qty": 12.0, "publication_ts": ts}],
        }

    def telemetry_snapshot(self):
        return {}


def _universe_empty() -> UniverseBuildResult:
    return UniverseBuildResult(
        assets=[],
        eligible_usd_markets=0,
        eligible_usdt_markets=0,
        unique_underlying_assets=0,
        selected_liquid_assets=0,
        usdt_usd_rate=None,
        warnings=[],
        ticker_batch_requests=0,
    )


def _snapshot(
    *,
    symbol: str = "SOLUSD",
    direction: str = "LONG",
    price: float = 100.0,
    primary_pair: str = "SOLUSD",
) -> MarketSnapshot:
    return MarketSnapshot(
        symbol=symbol,
        last_price=price,
        ema20=price,
        ema50=price,
        ema200=price,
        rsi=55.0,
        macd_line=0.1,
        macd_signal=0.05,
        macd_histogram=0.05,
        atr=1.0,
        atr_pct=1.0,
        volume_ratio=1.2,
        technical_score=80,
        trend="UP",
        trade_direction=direction,
        underlying_asset=symbol.removesuffix("USD"),
        primary_pair=primary_pair,
        primary_quote_currency="USD",
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
        ticker_last=price,
        ticker_bid=price,
        ticker_ask=price,
        primary_24h_liquidity_usd=1_000_000.0,
        combined_24h_liquidity_usd=1_000_000.0,
    )


def _universe(*, display_pair: str = "SOLUSD", pair_id: str = "SOLUSD") -> UniverseBuildResult:
    """A real universe result carrying the exact AssetPairs facts."""
    base = display_pair.removesuffix("USD")
    return UniverseBuildResult(
        assets=[
            UniverseAsset(
                base_asset=base,
                primary_pair=display_pair,
                primary_quote_currency="USD",
                primary_kraken_symbol=f"{base}/USD",
                combined_24h_notional_usd=1_000_000.0,
                primary_pair_id=pair_id,
                primary_pair_details={
                    "altname": pair_id,
                    "wsname": f"{base}/USD",
                    "pair_decimals": 2,
                    "ordermin": "0.02",
                    "status": "online",
                },
            )
        ],
        eligible_usd_markets=1,
        eligible_usdt_markets=0,
        unique_underlying_assets=1,
        selected_liquid_assets=1,
        usdt_usd_rate=None,
        warnings=[],
        ticker_batch_requests=1,
    )


def _plan(*, valid_now: bool = True) -> EntryExitPlan:
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=valid_now,
        entry_style="MARKET",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=90.0,
        target_1=110.0,
        target_2=120.0,
        reward_to_risk_1=1.0,
        reward_to_risk_2=2.0,
        risk_level="MEDIUM",
        reason="qualified",
        direction="LONG",
    )


class _Funnel:
    def __init__(self, states) -> None:
        self._states = states

    def get(self, symbol, direction):
        return self._states.get((str(symbol).upper(), str(direction).upper()))


class _ObserverStub:
    """Only the observer surface ``main()`` and the router actually use."""

    def __init__(self, states) -> None:
        self.funnel = _Funnel(states)
        self.qualified = 0
        self.finalized = 0
        self.eligibility_calls: list = []
        self.telemetry_enabled = False

    def record_scan(self, *args, **kwargs) -> None:
        return None

    def record_economic(self, *args, **kwargs) -> None:
        return None

    def record_candidate(self, *args, **kwargs) -> None:
        return None

    def record_snapshot_decision(self, *args, **kwargs) -> None:
        return None

    def __getattr__(self, name):
        """No-op any other observer surface ``main()`` happens to touch.

        Only the surfaces these tests assert on are real; everything else the
        orchestrator calls is telemetry, and a no-op keeps the test honest about
        what it stubs rather than silently diverging.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        def _noop(*args, **kwargs):
            return 0

        return _noop

    def record_qualified(self, ranked) -> None:
        self.qualified += 1

    def record_action_gate(self, ranked, *, allowed, reason) -> None:
        return None

    def record_paper_admission_eligibility(
        self, ranked, *, paper_enabled, engine_label=None, paper_v2=False
    ) -> int:
        self.eligibility_calls.append(
            {
                "paper_enabled": paper_enabled,
                "engine_label": engine_label,
                "paper_v2": paper_v2,
            }
        )
        return 0

    def finalize(self, *, scan_context=None, paper_admission_eligible=0) -> None:
        self.finalized += 1


class _Calls:
    def __init__(self) -> None:
        self.freqtrade = 0
        self.paper_v1 = 0
        self.router = 0
        self.lineage: list = []
        self.routed: list = []


#: Module-level persistence seams the real scan touches that are unrelated to the
#: paper-authority routing under test (registries, caches, notification state).
#: Each defaults to ``/app/data``, which is not writable in CI, so they are
#: redirected into the test's tmp directory. This isolates the *test*, not the
#: production paths: no production constant changes.
_SCAN_PERSISTENCE_SEAMS = (
    "app.services.asset_display_identity.REGISTRY_FILE",
    "app.services.shadow_learning.SHADOW_FILE",
    "app.services.telegram_delivery.STATE_FILE",
    "app.services.telegram_delivery.EVENT_FILE",
    "app.services.chief_alert_notifier.STATE_FILE",
    "app.services.notification_policy.STATE_FILE",
    "app.services.price_movement_learning.MOVEMENT_FILE",
    "app.services.alert_governor.STATE_FILE",
    "app.services.journal.JOURNAL_PATH",
)


def _isolate_scan_persistence(monkeypatch, tmp_path) -> None:
    """Redirect the scan's unrelated runtime persistence into ``tmp_path``.

    The scan orchestrator is real in these tests, so it reaches registries and
    caches that have nothing to do with paper-authority routing. Left alone they
    resolve to ``/app/data`` and raise ``PermissionError`` on Linux CI. Redirecting
    them keeps the tests deterministic and platform-independent while still
    exercising the real mode-selection and routing logic.
    """
    for target in _SCAN_PERSISTENCE_SEAMS:
        module_path, _, attribute = target.rpartition(".")
        monkeypatch.setattr(
            target, tmp_path / f"{module_path.rsplit('.', 1)[-1]}_{attribute}"
        )

    # The canonical directory resolves from this environment variable in
    # production, so pointing it at tmp is a supported configuration rather than
    # a path override.
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(tmp_path / "canonical"))

    # ``validate_scheduled_catalysts`` takes its cache as a default argument, so
    # the module constant is already bound. Inject a tmp cache explicitly.
    import functools

    real_catalysts = scan_opportunities.validate_scheduled_catalysts

    monkeypatch.setattr(
        scan_opportunities,
        "validate_scheduled_catalysts",
        functools.partial(
            real_catalysts, cache_path=tmp_path / "coinmarketcal_coin_map.json"
        ),
    )


@pytest.fixture(autouse=True)
def _isolated_scan_persistence(monkeypatch, tmp_path):
    """Keep every test in this module hermetic.

    Autouse because the scan orchestrator is real here: whichever test drives it
    would otherwise reach ``/app/data`` and fail on Linux CI for reasons unrelated
    to paper-authority routing.
    """
    _isolate_scan_persistence(monkeypatch, tmp_path)


def _rows(writer, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _drain_status(kind: str):
    """A ``LegacyDrainStatus`` for one legacy-state scenario.

    ``kind`` names the legacy condition; the tests assert the authority the resolver
    derives from it. ``None`` means "unreadable", which is a distinct input from
    "drained" and must not be conflated with it.
    """
    from app.services.paper_v2_cutover_readiness import LegacyDrainStatus

    if kind is None:
        return None
    if kind == "drained":
        return LegacyDrainStatus(status="READY", reason="legacy drained")
    if kind == "unreadable":
        # An unreadable legacy state is a distinct input from "drained": it must
        # resolve to UNAVAILABLE, not be treated as empty.
        return None
    if kind == "unavailable":
        return LegacyDrainStatus(
            status="UNAVAILABLE", reason="legacy state unreadable"
        )
    return LegacyDrainStatus(
        status="DRAINING",
        reason=f"legacy obligations remain: {kind}",
        freqtrade_open_trades=1,
    )


def _install_scan(
    monkeypatch,
    *,
    mode: str,
    direction: str = "LONG",
    valid_now: bool = True,
    snapshot=None,
    ranked_override=None,
    universe=None,
    router_recorder=None,
    drain=None,
):
    """Drive the real ``main()`` with only the outer seams stubbed.

    ``drain`` is the cutover-readiness verdict the scan should observe. ``None``
    means "legacy is drained", which is the state an active-mode test needs; a
    verdict string lets a test assert the blocked path.
    """
    calls = _Calls()
    snapshot = snapshot or _snapshot(direction=direction)
    settings = _Settings(paper_v2_mode=mode)

    if drain is not None:
        # Stub the legacy-STATE VIEW, not the decision logic: the authority resolver
        # under test is unchanged, only its input is controlled.
        monkeypatch.setattr(
            scan_opportunities,
            "_legacy_drain_status",
            lambda _settings: _drain_status(drain),
        )
    elif mode == "active":
        # Active-mode tests are about routing, not legacy drain, so legacy is
        # reported drained. The blocked paths have their own tests.
        monkeypatch.setattr(
            scan_opportunities,
            "_legacy_drain_status",
            lambda _settings: _drain_status("drained"),
        )

    monkeypatch.setattr(scan_opportunities, "get_settings", lambda: settings)
    monkeypatch.setattr(
        scan_opportunities,
        "scan_market",
        lambda limit: ScanResult(
            [snapshot], 1, 1, 0, 0, [], [], universe=universe or _universe()
        ),
    )
    monkeypatch.setattr(scan_opportunities, "select_candidates", lambda items: items)
    # These tests are about paper-authority routing, not margin eligibility, so
    # keep a SHORT candidate alive to the routing stage rather than letting the
    # margin gate drop it before anything under test runs.
    monkeypatch.setattr(
        scan_opportunities,
        "keep_margin_tradeable_candidates",
        lambda items: list(items),
    )
    # Likewise the short execution-quality gate: a SHORT that fails it never
    # reaches the routing seam these tests exercise.
    monkeypatch.setattr(
        scan_opportunities,
        "short_execution_is_tradeable",
        lambda candidate: (True, []),
    )
    monkeypatch.setattr(
        scan_opportunities, "_capture_native_scan_cohort", lambda scan, **kw: 0
    )
    # The REAL lineage preparation runs; only its outward dependencies are stubbed,
    # so the metadata under test is the metadata production would write.
    monkeypatch.setattr(
        "app.services.paper_trade_control.paper_trade_enabled", lambda: True, raising=True
    )

    def _capture_lineage(**kwargs):
        calls.lineage.append(dict(kwargs.get("payload") or {}))
        return "JRN:test"

    monkeypatch.setattr(
        "app.services.intelligence_journey.link_qualified_signal",
        _capture_lineage,
        raising=True,
    )

    if ranked_override is not None:
        ranked_list = ranked_override
    else:
        alert = {
            "recommended_capital": 500.0,
            "recommended_position_notional": 500.0,
        }
        ranked_list = [
            SimpleNamespace(
                rank=1,
                opportunity=SimpleNamespace(
                    alert=alert, snapshot=snapshot, plan=_plan(valid_now=valid_now)
                ),
                profit_ranking=SimpleNamespace(total_score=1.0),
            )
        ]

    monkeypatch.setattr(
        scan_opportunities,
        "_apply_ranked_action_gates",
        lambda ranked, **kw: list(ranked_list),
    )

    observer_holder: dict = {}

    def _build_observer(*, snapshots=None, decision_at=None, **kw):
        """Mirror production: the observer binds each episode at registration."""
        from app.services.canonical_episode_capture import canonical_episode_id

        states = {}
        for entry in snapshots or []:
            entry_direction = str(getattr(entry, "trade_direction", "LONG") or "LONG").upper()
            try:
                episode = canonical_episode_id(
                    snapshots, decision_at=decision_at, symbol=entry.symbol
                )
            except Exception:
                episode = None
            states[(str(entry.symbol).upper(), entry_direction)] = SimpleNamespace(
                candidate_id="OPIPC:" + "a" * 20,
                episode_id=episode,
                symbol=entry.symbol,
                direction=entry_direction,
            )
        stub = _ObserverStub(states)
        observer_holder["observer"] = stub
        return stub

    monkeypatch.setattr(scan_opportunities, "build_scan_observer", _build_observer)

    def _freqtrade(ranked, **kw):
        calls.freqtrade += 1
        return 0, 0

    def _paper_v1(ranked, **kw):
        calls.paper_v1 += 1
        return 0, 0

    monkeypatch.setattr(
        scan_opportunities, "_publish_freqtrade_paper_opportunities", _freqtrade
    )
    monkeypatch.setattr(
        scan_opportunities, "_maybe_enroll_paper_opportunities", _paper_v1
    )
    monkeypatch.setattr(scan_opportunities, "_paper_trade_enabled_safe", lambda: True)

    if router_recorder is not None:
        monkeypatch.setattr(
            scan_opportunities, "_route_paper_v2_opportunities", router_recorder
        )
    else:
        # Record that the routing seam was reached, then run the real router, so
        # a "nothing happened" assertion cannot pass vacuously.
        real_route = scan_opportunities._route_paper_v2_opportunities

        def _spy_route(ranked, **kwargs):
            calls.routed.append(list(ranked))
            return real_route(ranked, **kwargs)

        monkeypatch.setattr(
            scan_opportunities, "_route_paper_v2_opportunities", _spy_route
        )

    # Capture lineage metadata as it is produced by the real preparation path.
    return settings, calls, observer_holder, ranked_list


# ---------------------------------------------------------------------------
# OFF / default mode: existing behaviour preserved exactly
# ---------------------------------------------------------------------------


def test_off_mode_calls_both_legacy_paper_paths_and_never_paper_v2(monkeypatch, tmp_path):
    settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="off", router_recorder=_fail_if_called
    )
    scan_opportunities.main()

    assert calls.freqtrade == 1
    assert calls.paper_v1 == 1
    assert calls.router == 0


def test_default_configuration_keeps_paper_v2_off():
    from app.core.config import Settings

    assert Settings.model_fields["opip_paper_v2_mode"].default == "off"


def test_off_mode_lineage_metadata_is_unchanged(monkeypatch):
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="off", router_recorder=_fail_if_called
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(True, "FREQTRADE_DRY_RUN")]


def test_off_mode_short_lineage_is_unchanged(monkeypatch):
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="off", direction="SHORT", router_recorder=_fail_if_called
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(False, "NO_AUTHORITATIVE_SHORT_ENGINE_V1")]


def test_off_mode_wait_is_still_requested_for_the_legacy_engine(monkeypatch):
    """The legacy path has a pending-setup concept, so its metadata is unchanged."""
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="off", valid_now=False, router_recorder=_fail_if_called
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(True, "FREQTRADE_DRY_RUN")]


# ---------------------------------------------------------------------------
# ACTIVE mode: exclusive Paper-v2 authority
# ---------------------------------------------------------------------------


@pytest.fixture
def writer_env(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


@pytest.fixture(autouse=True)
def _reset_seams():
    yield
    set_writer_client_for_tests(None)
    set_kraken_client_for_tests(None)


def test_active_mode_excludes_both_legacy_authorities(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))

    _settings, calls, _observer, _ranked = _install_scan(monkeypatch, mode="active")
    scan_opportunities.main()

    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0
    # The real router/producer path ran and committed canonical evidence.
    rows = server.writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT COUNT(*) FROM events WHERE event_type = ?",
        ("decision_intelligence.context.recorded",),
    ).fetchone()[0]
    assert rows == 1


def test_active_mode_never_falls_back_after_a_paper_v2_failure(monkeypatch):
    def _explode(ranked, **kwargs):
        raise RuntimeError("paper v2 router exploded")

    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", router_recorder=_explode
    )
    with pytest.raises(RuntimeError):
        scan_opportunities.main()
    # The failure is not converted into a legacy execution.
    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0


def test_active_mode_short_is_not_routed_and_not_fallen_back(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    requests: list = []
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=requests)))

    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", direction="SHORT"
    )
    scan_opportunities.main()

    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0
    assert requests == []
    count = server.writer._conn.execute(  # noqa: SLF001
        "SELECT COUNT(*) FROM events"
    ).fetchone()[0]
    assert count == 0
    # Non-vacuous: the SHORT really did reach the routing seam and was refused
    # there, rather than being filtered out earlier.
    assert len(calls.routed) == 1
    assert len(calls.routed[0]) == 1


def test_active_mode_wait_is_not_routed_and_not_fallen_back(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    requests: list = []
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=requests)))

    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", valid_now=False
    )
    scan_opportunities.main()

    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0
    assert requests == []
    assert (
        server.writer._conn.execute(  # noqa: SLF001
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        == 0
    )
    # Non-vacuous: the WAIT reached the routing seam and was refused there.
    assert len(calls.routed) == 1
    assert len(calls.routed[0]) == 1


def test_active_mode_with_no_universe_metadata_fails_closed(monkeypatch):
    """No execution instrument can be proven, so nothing is attempted."""
    settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", universe=_universe_empty()
    )
    # Force the real router to have no usable universe.
    monkeypatch.setattr(
        scan_opportunities,
        "_route_paper_v2_opportunities",
        lambda ranked, **kw: None,
    )
    scan_opportunities.main()
    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0


# ---------------------------------------------------------------------------
# ACTIVE mode lineage metadata
# ---------------------------------------------------------------------------


def test_active_mode_lineage_reports_paper_v2_for_an_actionable_long(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _settings, calls, _observer, _ranked = _install_scan(monkeypatch, mode="active")
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(True, "OPIP_PAPER_V2")]


def test_active_mode_wait_lineage_is_not_a_paper_v2_request(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", valid_now=False
    )
    scan_opportunities.main()
    requested, engine = _lineage_pairs(calls)[0]
    assert requested is False
    assert engine == "OPIP_PAPER_V2_WAIT_NOT_EXECUTABLE"
    assert engine != "OPIP_PAPER_V2"


def test_active_mode_short_lineage_keeps_the_no_short_engine_meaning(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", direction="SHORT"
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(False, "NO_AUTHORITATIVE_SHORT_ENGINE_V1")]


def test_active_mode_admission_telemetry_names_the_paper_v2_engine(monkeypatch, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _settings, _calls, observer, _ranked = _install_scan(monkeypatch, mode="active")
    scan_opportunities.main()
    stub = observer["observer"]
    assert stub.eligibility_calls
    assert stub.eligibility_calls[0]["engine_label"] == "O'Pip Paper v2"


def test_off_mode_admission_telemetry_keeps_the_legacy_engine_label(monkeypatch):
    _settings, _calls, observer, _ranked = _install_scan(
        monkeypatch, mode="off", router_recorder=_fail_if_called
    )
    scan_opportunities.main()
    stub = observer["observer"]
    assert stub.eligibility_calls
    assert (
        stub.eligibility_calls[0]["engine_label"]
        == "v1 authoritative paper engine"
    )


def test_active_mode_prints_the_router_summary(monkeypatch, capsys, writer_env):
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _install_scan(monkeypatch, mode="active")
    scan_opportunities.main()
    out = capsys.readouterr().out
    assert "PAPER V2 ROUTING (active authority)" in out
    assert "Paper v2 executed: 1" in out
    assert "Legacy paper authorities invoked: 0" in out


def test_route_helper_reports_missing_universe_without_attempting(monkeypatch, capsys):
    """The scan-level helper fails closed rather than re-requesting AssetPairs."""
    settings = _Settings(paper_v2_mode="active")
    summary = scan_opportunities._route_paper_v2_opportunities(
        [],
        scan=SimpleNamespace(snapshots=[], universe=_universe_empty()),
        decision_at=NOW,
        stamp=None,
        settings=settings,
        opip=None,
    )
    assert summary is None
    assert "universe metadata unavailable" in capsys.readouterr().out


def test_router_summary_reports_zero_legacy_calls():
    assert PaperV2RouterSummary().legacy_calls == 0


def test_paper_v2_eligibility_uses_paper_v2_authority_not_legacy_control():
    """Paper v2 active must be reported from Paper v2 authority.

    The legacy paper control switch is a different fact, so passing it as "is
    Paper v2 enabled" would be false attribution.
    """
    from app.opip.decision.observer import build_scan_observer

    for direction, valid_now, expected in (
        ("LONG", True, 1),
        ("LONG", False, 0),   # WAIT: no pending-entry engine
        ("SHORT", True, 0),   # no short engine
    ):
        entry = _snapshot(direction=direction)
        observer = build_scan_observer(
            snapshots=[entry], decision_at=NOW, account_equity=10_000.0
        )
        ranked = [
            SimpleNamespace(
                rank=1,
                opportunity=SimpleNamespace(
                    alert={}, snapshot=entry, plan=_plan(valid_now=valid_now)
                ),
                profit_ranking=SimpleNamespace(total_score=1.0),
            )
        ]
        eligible = observer.record_paper_admission_eligibility(
            ranked,
            # Legacy paper control is OFF; Paper v2 is the authority.
            paper_enabled=False,
            engine_label="O'Pip Paper v2",
            paper_v2=True,
        )
        assert eligible == expected, (direction, valid_now)


def test_legacy_eligibility_reason_is_unchanged_when_paper_v2_is_off():
    """OFF mode keeps the legacy reason and the legacy control semantics."""
    from app.opip.decision.observer import build_scan_observer
    from app.opip.decision.models import ReasonCode

    entry = _snapshot(direction="LONG")
    observer = build_scan_observer(
        snapshots=[entry], decision_at=NOW, account_equity=10_000.0
    )
    ranked = [
        SimpleNamespace(
            rank=1,
            opportunity=SimpleNamespace(
                alert={}, snapshot=entry, plan=_plan(valid_now=True)
            ),
            profit_ranking=SimpleNamespace(total_score=1.0),
        )
    ]
    enabled = observer.record_paper_admission_eligibility(
        ranked, paper_enabled=True, engine_label="v1 authoritative paper engine"
    )
    assert enabled == 1

    disabled = observer.record_paper_admission_eligibility(
        ranked, paper_enabled=False, engine_label="v1 authoritative paper engine"
    )
    assert disabled == 0
    assert ReasonCode.PAPER_ENGINE_DISABLED is not None


def test_paper_v2_wait_has_its_own_reason_code():
    """A WAIT must not be reported as the engine being disabled."""
    from app.opip.decision.models import ReasonCode

    assert ReasonCode.PAPER_V2_WAIT_NOT_IMMEDIATELY_EXECUTABLE.value == (
        "PAPER_V2_WAIT_NOT_IMMEDIATELY_EXECUTABLE"
    )
    assert (
        ReasonCode.PAPER_V2_WAIT_NOT_IMMEDIATELY_EXECUTABLE
        is not ReasonCode.PAPER_ENGINE_DISABLED
    )


def test_scan_module_does_not_import_decision_intelligence():
    """The frozen runtime import boundary is unchanged by this slice."""
    import ast
    from pathlib import Path

    path = (
        Path(scan_opportunities.__file__).resolve()
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    di_root = "app.opip.decision_intelligence"
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not (name == di_root or name.startswith(di_root + ".")), name


def _fail_if_called(*args, **kwargs):
    raise AssertionError("Paper v2 must not be reached when the mode is off")


# ---------------------------------------------------------------------------
# Authority resolution: requested vs granted
# ---------------------------------------------------------------------------


def _resolve_with_drain(mode: str, drain):
    """Resolve authority with the legacy-state view controlled."""
    from types import SimpleNamespace

    from app.jobs import scan_opportunities

    settings = SimpleNamespace(
        opip_paper_v2_mode=mode, paper_trade_starting_equity=10_000.0
    )
    original = scan_opportunities._legacy_drain_status
    scan_opportunities._legacy_drain_status = lambda _s: _drain_status(drain)
    try:
        return scan_opportunities._resolve_paper_authority(settings)
    finally:
        scan_opportunities._legacy_drain_status = original


@pytest.mark.parametrize(
    ("mode", "drain", "expected"),
    [
        ("off", None, "LEGACY"),
        ("active", "drained", "PAPER_V2_READY"),
        ("active", "freqtrade_open", "PAPER_V2_DRAINING"),
        ("active", "paper_v1_open", "PAPER_V2_DRAINING"),
        ("active", "unreadable", "PAPER_V2_UNAVAILABLE"),
        ("active", None, "PAPER_V2_UNAVAILABLE"),
    ],
)
def test_resolved_authority_distinguishes_request_from_grant(mode, drain, expected):
    """Requested configuration and granted authority are separate facts."""
    authority = _resolve_with_drain(mode, drain)

    assert authority.granted == expected
    # Only READY authorizes new Paper-v2 entry, and only LEGACY authorizes new legacy
    # entry, so the two can never both be true.
    assert authority.paper_v2_routing is (expected == "PAPER_V2_READY")
    assert authority.legacy_new_entry_allowed is (expected == "LEGACY")
    assert not (authority.paper_v2_routing and authority.legacy_new_entry_allowed)


@pytest.mark.parametrize(
    ("mode", "drain", "expected"),
    [
        ("off", None, "LEGACY"),
        ("active", "drained", "PAPER_V2_READY"),
        ("active", "freqtrade_open", "PAPER_V2_DRAINING"),
        ("active", "unavailable", "PAPER_V2_UNAVAILABLE"),
        ("active", None, "PAPER_V2_UNAVAILABLE"),
    ],
)
def test_blocked_cutover_never_creates_a_new_legacy_entry(
    monkeypatch, mode, drain, expected
):
    """The core regression: a blocked cutover must not fall back to legacy admission.

    Previously a blocked cutover set an internal flag false, which routed straight
    into the legacy branch and created the very legacy obligations the cutover was
    waiting to drain - so the cutover could never complete.
    """
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode=mode, drain=drain
    )
    scan_opportunities.main()

    if expected == "LEGACY":
        assert calls.freqtrade == 1
        assert calls.paper_v1 == 1
    else:
        # DRAINING and UNAVAILABLE create no new obligation in either authority.
        assert calls.freqtrade == 0, expected
        assert calls.paper_v1 == 0, expected


def test_draining_authority_reports_the_blocking_reason(monkeypatch, capsys):
    _install_scan(monkeypatch, mode="active", drain="freqtrade_open")
    scan_opportunities.main()
    out = capsys.readouterr().out
    assert "PAPER V2 CUTOVER NOT GRANTED" in out
    assert "PAPER_V2_DRAINING" in out
    assert "New Freqtrade entries: 0" in out
    assert "New Paper-v1 enrollments: 0" in out
    assert "New Paper-v2 entries: 0" in out


def test_unavailable_authority_is_distinct_from_draining(monkeypatch, capsys):
    """An unreadable legacy state must not be reported as ordinary draining."""
    _install_scan(monkeypatch, mode="active", drain="unreadable")
    scan_opportunities.main()
    out = capsys.readouterr().out
    assert "PAPER_V2_UNAVAILABLE" in out
    assert "PAPER_V2_DRAINING" not in out


def test_lineage_under_draining_claims_no_engine(monkeypatch):
    """Lineage must not claim Paper v2 acted when authority is only requested."""
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", drain="freqtrade_open"
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(False, "OPIP_PAPER_V2_DRAINING")]


def test_lineage_under_unavailable_claims_no_engine(monkeypatch):
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", drain="unreadable"
    )
    scan_opportunities.main()
    assert _lineage_pairs(calls) == [(False, "OPIP_PAPER_V2_UNAVAILABLE")]


def test_draining_becomes_ready_without_a_configuration_change(monkeypatch, writer_env):
    """C8: the cutover completes on its own once legacy drains."""
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))

    # Scan N: legacy still holds an obligation, so no new entry anywhere.
    _install_scan(monkeypatch, mode="active", drain="freqtrade_open")
    scan_opportunities.main()
    assert _rows(server.writer, CTX_EVENT) == []

    # The legacy obligation closes through its own lifecycle and no configuration
    # changes. Scan N+1 grants the cutover.
    _install_scan(monkeypatch, mode="active", drain="drained")
    scan_opportunities.main()
    assert len(_rows(server.writer, CTX_EVENT)) == 1


@pytest.mark.parametrize(
    "mode", ["Active", "ACTIVE", " active ", "", "unexpected", 1, True, None]
)
def test_malformed_activation_takes_the_legacy_path(monkeypatch, mode):
    """C9: only the exact canonical value may request the cutover.

    A near-miss must be ordinary OFF, not an activation - and it must reach the
    historical legacy path rather than the blocked-cutover path, because no cutover
    was requested.
    """
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode=mode, drain="unreadable"
    )
    scan_opportunities.main()
    assert calls.freqtrade == 1
    assert calls.paper_v1 == 1
    assert calls.router == 0


def test_exact_active_requests_the_cutover(monkeypatch, writer_env):
    """C10: the exact canonical value is the only one that requests activation."""
    server, client = writer_env
    set_writer_client_for_tests(client)
    set_kraken_client_for_tests(KrakenClient(transport=_EchoTransport(requests=[])))
    _settings, calls, _observer, _ranked = _install_scan(
        monkeypatch, mode="active", drain="drained"
    )
    scan_opportunities.main()
    assert calls.freqtrade == 0
    assert calls.paper_v1 == 0
    assert len(_rows(server.writer, CTX_EVENT)) == 1


def test_no_dual_authority_across_every_state(monkeypatch):
    """No scan state may create a new entry in both authorities."""
    labels: list[str] = []
    for mode, drain in (
        ("off", None),
        ("active", "drained"),
        ("active", "freqtrade_open"),
        ("active", "unreadable"),
        ("active", None),
    ):
        _settings, calls, _observer, _ranked = _install_scan(
            monkeypatch, mode=mode, drain=drain
        )
        scan_opportunities.main()
        legacy_created = (calls.freqtrade + calls.paper_v1) > 0
        label = mode if mode == "off" else f"{mode}/{drain}"
        labels.append(label)
        if label == "off":
            assert legacy_created
        else:
            # Every requested-cutover state either routes Paper v2 (never legacy) or
            # creates nothing at all. New legacy entry is never one of the outcomes.
            assert not legacy_created, label

    assert "off" in labels
    assert "active/drained" in labels
    assert "active/freqtrade_open" in labels
    assert "active/None" in labels


def _lineage_pairs(calls) -> list[tuple[bool, str]]:
    """The (paper_requested, paper_engine) pairs the real path produced."""
    return [
        (item["paper_requested"], item["paper_engine"]) for item in calls.lineage
    ]
