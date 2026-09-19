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
        # A live book reports its publication time as now; the producer's quote
        # freshness bound is measured against the qualification instant.
        ts = (
            datetime.now(timezone.utc) - timedelta(seconds=1)
        ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": 99.9, "qty": 10.0, "publication_ts": ts}],
            "asks": [{"price": 100.1, "qty": 12.0, "publication_ts": ts}],
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

    def record_paper_admission_eligibility(self, ranked, *, paper_enabled, engine_label=None) -> int:
        self.eligibility_calls.append(
            {"paper_enabled": paper_enabled, "engine_label": engine_label}
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
):
    """Drive the real ``main()`` with only the outer seams stubbed."""
    calls = _Calls()
    snapshot = snapshot or _snapshot(direction=direction)
    settings = _Settings(paper_v2_mode=mode)

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


def _lineage_pairs(calls) -> list[tuple[bool, str]]:
    """The (paper_requested, paper_engine) pairs the real path produced."""
    return [
        (item["paper_requested"], item["paper_engine"]) for item in calls.lineage
    ]
