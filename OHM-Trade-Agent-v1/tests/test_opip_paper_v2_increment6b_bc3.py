"""B/C-3 Increment 6B: Paper-v2 scan router and default-off mode split.

Proves the exclusive paper authority split: with the mode off the existing
Freqtrade dry-run and legacy Paper-v1 behaviour is preserved exactly and Paper v2
is never reached; with the mode active only O'Pip Paper v2 runs and neither legacy
authority is invoked, including after a Paper-v2 failure.

Also proves the handoff invariants the router owns: canonical OPIPC candidate
identity, a decision snapshot from the same scan cohort, a qualification-time
policy stamp captured once, post-action-gate capital, snapshot-derived quantity,
exact Kraken metadata reuse, canonical instrument-version reconstruction, and the
instrument/native-symbol equality invariant.

Nothing here activates Paper v2: the default mode stays ``off``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.identity import InstrumentVersion
from app.opip.market.instruments import InstrumentVersionRegistry
from app.services.entry_exit_advisor import EntryExitPlan
from app.services.paper_v2_scan_router import (
    PAPER_V2_SCAN_SOURCE,
    PaperV2HandoffError,
    PaperV2QualificationStamp,
    PaperV2ScanFacts,
    capture_qualification_stamp,
    route_qualified_opportunities,
    set_kraken_client_for_tests,
    set_writer_client_for_tests,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
QUALIFICATION_TIME = NOW + timedelta(seconds=5)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
CTX_EVENT = "decision_intelligence.context.recorded"
SNAPSHOT_EVENT = "paper_execution.decision_snapshot.recorded"


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------


class _Settings:
    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400
    account_equity = 10_000.0


class _EchoTransport:
    """Counts every request and echoes the requested symbol, like the venue does.

    ``ask``/``bid``/quantities and the publication instant are parameters so a test
    can place the committed book inside or outside the qualified geometry, above
    the reservation, or too thin to fill.
    """

    def __init__(
        self,
        *,
        requests: list,
        stale: bool = False,
        ask: float = 100.0,
        bid: float = 99.9,
        ask_qty: float = 12.0,
        bid_qty: float = 10.0,
        published_at: str = "2026-09-19T11:59:59Z",
    ) -> None:
        self._requests = requests
        self._stale = stale
        self._ask = float(ask)
        self._bid = float(bid)
        self._ask_qty = float(ask_qty)
        self._bid_qty = float(bid_qty)
        self._published_at = published_at

    def request(self, endpoint, params, timeout_seconds):
        self._requests.append((endpoint, params.get("symbol")))
        ts = "2026-09-19T11:00:00Z" if self._stale else self._published_at
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": self._bid, "qty": self._bid_qty, "publication_ts": ts}],
            "asks": [{"price": self._ask, "qty": self._ask_qty, "publication_ts": ts}],
        }

    def telemetry_snapshot(self):
        return {}


def _observation(symbol: str = "SOLUSD", price: float = 100.0) -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        base_asset=symbol.removesuffix("USD"),
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
        last_price=price,
        ticker_last=price,
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=price * 1.05,
        low_24h=price * 0.95,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )


def _universe_asset(
    *,
    pair_id: str = "SOLUSD",
    altname: str = "SOLUSD",
    wsname: str = "SOL/USD",
    pair_decimals: int = 2,
    ordermin: str = "0.02",
    display_pair: str = "SOLUSD",
) -> SimpleNamespace:
    """The exact AssetPairs facts the production universe already observed."""
    return SimpleNamespace(
        base_asset=wsname.split("/")[0],
        primary_pair=display_pair,
        primary_pair_id=pair_id,
        primary_pair_details={
            "altname": altname,
            "wsname": wsname,
            "pair_decimals": pair_decimals,
            "ordermin": ordermin,
            "status": "online",
        },
    )


def _plan(*, valid_now: bool = True, stop: float = 90.0) -> EntryExitPlan:
    return EntryExitPlan(
        symbol="SOLUSD",
        valid_now=valid_now,
        entry_style="MARKET",
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=stop,
        target_1=110.0,
        target_2=120.0,
        reward_to_risk_1=1.0,
        reward_to_risk_2=2.0,
        risk_level="MEDIUM",
        reason="qualified",
        direction="LONG",
    )


class _Funnel:
    def __init__(self, states: dict) -> None:
        self._states = states

    def get(self, symbol: str, direction: str):
        return self._states.get((str(symbol).upper(), str(direction).upper()))


def _episode_id(observations, decision_at, symbol) -> str:
    from app.services.canonical_episode_capture import canonical_episode_id

    return canonical_episode_id(observations, decision_at=decision_at, symbol=symbol)


def _ranked(
    *,
    observations,
    decision_at,
    symbol="SOLUSD",
    direction="LONG",
    valid_now=True,
    capital=500.0,
    notional=500.0,
    price=100.0,
    primary_pair="SOLUSD",
    quote_currency="USD",
    episode_id=None,
    candidate_id=None,
):
    snapshot_payload_episode = episode_id or _episode_id(observations, decision_at, symbol)
    snapshot = SimpleNamespace(
        symbol=symbol,
        trade_direction=direction,
        primary_pair=primary_pair,
        primary_quote_currency=quote_currency,
        underlying_asset="SOL",
        last_price=price,
        ticker_last=price,
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=price * 1.05,
        low_24h=price * 0.95,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )
    alert = {
        "recommended_capital": capital,
        "recommended_position_notional": notional,
        "signal_id": "OHM:signal-should-not-be-used",
    }
    opportunity = SimpleNamespace(alert=alert, snapshot=snapshot, plan=_plan(valid_now=valid_now))
    state = SimpleNamespace(
        candidate_id=candidate_id
        or f"OPIPC:{symbol_pseudo_hash(symbol)}",
        episode_id=snapshot_payload_episode,
        symbol=symbol,
        direction=direction,
    )
    return SimpleNamespace(rank=1, opportunity=opportunity, profit_ranking=SimpleNamespace(total_score=1.0)), state


def symbol_pseudo_hash(symbol: str) -> str:
    import hashlib

    return hashlib.sha256(symbol.encode()).hexdigest()[:20]


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
def _reset_router_seams():
    yield
    set_writer_client_for_tests(None)
    set_kraken_client_for_tests(None)


def _rows(writer, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _stamp(**overrides) -> PaperV2QualificationStamp:
    fields = {
        "qualification_time": QUALIFICATION_TIME,
        "policy_version": "OPIP-GATE-POLICY-TEST",
        "policy_fingerprint": "GPF:" + "a" * 16,
    }
    fields.update(overrides)
    return PaperV2QualificationStamp(**fields)


def _route(
    ranked_list,
    *,
    observations,
    decision_at=NOW,
    universe=None,
    client=None,
    kraken=None,
    registry=None,
    opip=None,
    stamp=None,
):
    # ``None`` means "the default universe"; an explicit empty tuple means
    # "no universe metadata", which must fail closed rather than be defaulted.
    if universe is None:
        universe = (_universe_asset(),)
    return route_qualified_opportunities(
        ranked_list,
        scan_facts=PaperV2ScanFacts(
            snapshots=tuple(observations),
            decision_at=decision_at,
            universe_assets=tuple(universe),
        ),
        stamp=stamp or _stamp(),
        settings=_Settings(),
        opip=opip,
        client=client,
        kraken_client=kraken,
        registry=registry,
    )


def _registry() -> InstrumentVersionRegistry:
    return InstrumentVersionRegistry(reference_data_version="ref-test")


# ---------------------------------------------------------------------------
# Happy path: routes exactly once, with the right evidence
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _frozen_execution_clock(monkeypatch):
    """Freeze the router's execution clock.

    The producer validates market-data freshness against this clock, which is real
    wall-clock time in production. The fixtures' books carry fixed source
    timestamps, so it must be frozen for them to be deterministic.
    """
    monkeypatch.setattr(
        "app.services.paper_v2_scan_router.system_utc_clock", lambda: NOW
    )


def test_long_enter_now_routes_exactly_once_to_paper_v2(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    requests: list = []

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 1
    assert summary.handoff_failures == 0
    assert summary.operational_failures == 0
    assert summary.legacy_calls == 0
    assert len(_rows(server.writer, SNAPSHOT_EVENT)) == 1
    assert len(_rows(server.writer, CTX_EVENT)) == 1


def test_candidate_id_is_the_funnel_opipc_id_not_the_signal_id(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["candidate_id"] == state.candidate_id
    assert context["candidate_id"].startswith("OPIPC:")
    # The alert signal identity is a different fact and must not appear as the
    # candidate.
    assert context["candidate_id"] != "OHM:signal-should-not-be-used"


def test_snapshot_is_built_from_the_scan_cohort_with_decision_at_cutoff(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    recorded = _rows(server.writer, SNAPSHOT_EVENT)[0]
    assert recorded["snapshot_payload"]["scan_source"] == PAPER_V2_SCAN_SOURCE
    assert recorded["snapshot_payload"]["decision_at_utc"] == NOW.isoformat()
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["evidence_cutoff"] == "2026-09-19T12:00:00Z"
    # qualification_time is the evaluation instant, at or after the cutoff.
    assert context["evaluation_time"] == "2026-09-19T12:00:05Z"


def test_qualification_policy_stamp_is_passed_unchanged(writer_env, monkeypatch):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    stamp = _stamp(
        policy_version="OPIP-GATE-POLICY-CAPTURED",
        policy_fingerprint="GPF:" + "c" * 16,
    )
    # The live policy changes after the stamp was captured.
    import app.opip.decision.versioning as versioning

    monkeypatch.setattr(versioning, "GATE_POLICY_VERSION", "OPIP-GATE-POLICY-LIVE")
    monkeypatch.setattr(
        versioning, "gate_policy_fingerprint", lambda: "GPF:" + "d" * 16
    )

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
        stamp=stamp,
    )

    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["policy_version"] == "OPIP-GATE-POLICY-CAPTURED"
    assert context["policy_fingerprint"] == "GPF:" + "c" * 16
    assert context["policy_fingerprint"] != "GPF:" + "d" * 16


def test_capture_qualification_stamp_reads_the_live_policy_once():
    stamp = capture_qualification_stamp(qualification_time=NOW)
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    assert stamp.policy_version == GATE_POLICY_VERSION
    assert stamp.policy_fingerprint == gate_policy_fingerprint()
    assert stamp.qualification_time == NOW


# ---------------------------------------------------------------------------
# Capital / notional / quantity derivation
# ---------------------------------------------------------------------------


def test_post_action_gate_reduced_capital_is_used(writer_env):
    """A liquidity-capped allocation reaches Paper v2, not the earlier envelope."""
    server, client = writer_env
    observations = [_observation()]
    # The action gate reduced 2,000 (economic envelope) to 250 (capacity).
    ranked, state = _ranked(
        observations=observations,
        decision_at=NOW,
        capital=250.0,
        notional=250.0,
    )
    ranked.opportunity.alert["economic_validation_capital"] = 2_000.0
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 1
    context = _rows(server.writer, CTX_EVENT)[0]
    request = _rows(server.writer, "paper_execution.admission_request.recorded")[0]
    assert request["requested_capital"] == 250.0
    assert context["context_id"] == request["decision_context_id"]


def test_requested_quantity_derives_from_the_snapshot_reference_price(writer_env):
    server, client = writer_env
    observations = [_observation()]
    # 250 notional at the snapshot's own 100 reference price is 2.5 units. The
    # figure comes from the decision snapshot, not from a later quote.
    ranked, state = _ranked(
        observations=observations, decision_at=NOW, notional=250.0, capital=250.0
    )
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    order = _rows(server.writer, "paper_execution.order_intent.recorded")[0]
    assert order["requested_quantity"] == pytest.approx(2.5)


def test_leverage_inconsistency_fails_closed(writer_env):
    """A notional that disagrees with capital would imply leverage."""
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(
        observations=observations, decision_at=NOW, capital=500.0, notional=1_000.0
    )
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 0
    assert summary.handoff_failures == 1
    assert _rows(server.writer, CTX_EVENT) == []


@pytest.mark.parametrize("capital", [0.0, -1.0, float("nan"), "500", None])
def test_unusable_capital_fails_closed(writer_env, capital):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW, capital=capital)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.executed == 0
    assert summary.handoff_failures == 1


def test_plan_stop_and_targets_come_from_the_qualified_plan(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    plan = _rows(server.writer, "paper_protection.plan.recorded")[0]
    assert plan["stop_price"] == pytest.approx(90.0)
    assert [t["price"] for t in plan["targets"]] == [110.0, 120.0]


def test_upstream_additional_source_refs_may_be_empty_yet_provenance_is_not(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )

    context = _rows(server.writer, CTX_EVENT)[0]
    refs = context["provenance"]["source_record_refs"]
    assert refs, "the two mandatory ancestry proofs must always be present"
    snapshot_events = [
        str(row[0])
        for row in server.writer._conn.execute(  # noqa: SLF001
            "SELECT event_id FROM events WHERE event_type = ?", (SNAPSHOT_EVENT,)
        ).fetchall()
    ]
    registration_events = [
        str(row[0])
        for row in server.writer._conn.execute(  # noqa: SLF001
            "SELECT event_id FROM events WHERE event_type = ?",
            ("market.instrument_version.recorded",),
        ).fetchall()
    ]
    assert snapshot_events and registration_events
    assert snapshot_events[0] in refs
    for event_id in registration_events:
        assert event_id in refs


# ---------------------------------------------------------------------------
# Instrument identity
# ---------------------------------------------------------------------------


def test_exact_kraken_metadata_is_reused_without_a_second_assetpairs_request(
    writer_env, monkeypatch
):
    """Only the read-only pre-trade book is fetched; AssetPairs is not re-requested."""
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    requests: list = []

    def _forbidden(*args, **kwargs):
        raise AssertionError("AssetPairs must not be requested by Paper v2")

    monkeypatch.setattr(KrakenClient, "get_asset_pairs", _forbidden, raising=True)

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 1
    endpoints = {endpoint for endpoint, _ in requests}
    assert endpoints == {"PreTrade"}


def test_instrument_venue_symbol_equals_the_pretrade_native_symbol(writer_env):
    """The hard ancestry invariant, end to end."""
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    requests: list = []

    _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    # The canonical instrument record and the quote evidence agree on one string.
    instrument = _rows(server.writer, "market.instrument_version.recorded")[0]
    quote = _rows(server.writer, "paper_execution.quote_evidence.recorded")[0]
    assert instrument["venue_instrument_id"] == quote["native_symbol"]
    assert instrument["venue_instrument_id"] == "SOLUSD"
    # And that exact string is what was requested from the venue.
    assert {symbol for _, symbol in requests} == {"SOLUSD"}


def test_alias_sensitive_pair_keeps_the_metadata_derived_identity(writer_env):
    """BTC/XBT: the venue altname is the identity, not the display symbol."""
    server, client = writer_env
    observations = [_observation(symbol="BTCUSD")]
    ranked, state = _ranked(
        observations=observations,
        decision_at=NOW,
        symbol="BTCUSD",
        primary_pair="BTCUSD",
    )
    opip = SimpleNamespace(funnel=_Funnel({("BTCUSD", "LONG"): state}))
    requests: list = []
    # Kraken reports BTC as XBT in its own pair metadata.
    universe = (
        _universe_asset(
            pair_id="XXBTZUSD",
            altname="XBTUSD",
            wsname="XBT/USD",
            display_pair="BTCUSD",
        ),
    )

    summary = _route(
        [ranked],
        observations=observations,
        universe=universe,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 1
    instrument = _rows(server.writer, "market.instrument_version.recorded")[0]
    quote = _rows(server.writer, "paper_execution.quote_evidence.recorded")[0]
    # The alias-sensitive venue identity is preserved verbatim, not re-derived
    # from the BTC display symbol.
    assert instrument["venue_instrument_id"] == "XBTUSD"
    assert instrument["base_asset"] == "BTC"
    assert quote["native_symbol"] == "XBTUSD"
    assert {symbol for _, symbol in requests} == {"XBTUSD"}


def test_hydrated_registry_continues_prior_version_history(writer_env):
    """A committed prior version leads to an increment, not a restart at 1."""
    previous = InstrumentVersion(
        venue="kraken",
        base_asset="SOL",
        quote_currency="USD",
        venue_instrument_id="SOLUSD",
        version=7,
        reference_data_version="OPIP-IDENTITY-REFERENCE-V1",
        observed_at_utc=NOW - timedelta(days=1),
        price_decimals=1,  # deliberately different -> reference data changed
        tick_size=0.1,
        min_order_size=0.02,
        attributes={"pair_id": "SOLUSD", "altname": "SOLUSD"},
    )
    registry = InstrumentVersionRegistry(
        reference_data_version=previous.reference_data_version,
        known={previous.instrument_key: previous},
    )

    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=registry,
        opip=opip,
    )

    assert summary.executed == 1
    instrument = _rows(server.writer, "market.instrument_version.recorded")[0]
    # The scan's descriptor differs from the committed reference data, so the
    # version advances from the hydrated history rather than restarting at 1.
    assert instrument["version"] == 8


def test_registry_is_hydrated_from_canonical_evidence_by_default(writer_env, monkeypatch):
    """The router uses the canonical reconstruction, not a second counter."""
    server, client = writer_env
    calls: list = []

    import app.services.paper_v2_scan_router as router_module

    def _spy(db_path=None, **kwargs):
        calls.append(db_path)
        return _registry()

    monkeypatch.setattr(
        router_module, "hydrate_instrument_version_registry", _spy, raising=True
    )

    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    # registry=None -> the router must hydrate rather than invent a registry.
    summary = route_qualified_opportunities(
        [ranked],
        scan_facts=PaperV2ScanFacts(
            snapshots=tuple(observations),
            decision_at=NOW,
            universe_assets=(_universe_asset(),),
        ),
        stamp=_stamp(),
        settings=_Settings(),
        opip=opip,
        client=client,
        kraken_client=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=None,
    )
    assert summary.executed == 1
    assert len(calls) == 1


def test_absent_universe_metadata_fails_closed_for_every_opportunity(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        universe=(),
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.executed == 0
    assert summary.handoff_failures == 1
    assert _rows(server.writer, CTX_EVENT) == []


# ---------------------------------------------------------------------------
# Candidate ancestry fail-closed
# ---------------------------------------------------------------------------


def test_missing_funnel_candidate_fails_closed(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, _state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.handoff_failures == 1
    assert summary.executed == 0
    assert _rows(server.writer, CTX_EVENT) == []


def test_non_opipc_candidate_id_fails_closed(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    state.candidate_id = "OHM:signal-id"
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.handoff_failures == 1
    assert _rows(server.writer, CTX_EVENT) == []


def test_snapshot_episode_must_match_the_funnel_episode(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    state.episode_id = "EP:" + "0" * 24
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.handoff_failures == 1
    assert summary.executed == 0
    assert _rows(server.writer, CTX_EVENT) == []


# ---------------------------------------------------------------------------
# Direction and actionability
# ---------------------------------------------------------------------------


def test_short_is_never_routed_and_is_counted_explicitly(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(
        observations=observations, decision_at=NOW, direction="SHORT"
    )
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "SHORT"): state}))
    requests: list = []

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.short_unsupported == 1
    assert summary.executed == 0
    assert summary.legacy_calls == 0
    # No producer call at all: not even a read.
    assert requests == []
    assert _rows(server.writer, SNAPSHOT_EVENT) == []
    assert _rows(server.writer, CTX_EVENT) == []
    assert _rows(server.writer, "paper_execution.order_intent.recorded") == []


def test_long_wait_is_not_converted_into_an_immediate_buy(writer_env):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(
        observations=observations, decision_at=NOW, valid_now=False
    )
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    requests: list = []

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.wait_not_executable == 1
    assert summary.executed == 0
    assert requests == []
    assert _rows(server.writer, "paper_execution.order_intent.recorded") == []
    assert _rows(server.writer, CTX_EVENT) == []


# ---------------------------------------------------------------------------
# Failure isolation and no fallback
# ---------------------------------------------------------------------------


def test_one_failing_opportunity_does_not_stop_an_independent_one(writer_env):
    server, client = writer_env
    good = _observation("SOLUSD", 100.0)
    bad = _observation("ADAUSD", 2.0)
    cohort = [bad, good]
    # Both episodes come from the one scan cohort the router will build.
    bad_episode = _episode_id(cohort, NOW, "ADAUSD")
    good_episode = _episode_id(cohort, NOW, "SOLUSD")
    ranked_bad, state_bad = _ranked(
        observations=cohort,
        decision_at=NOW,
        symbol="ADAUSD",
        primary_pair="ADAUSD",
        episode_id=bad_episode,
    )
    ranked_good, state_good = _ranked(
        observations=cohort,
        decision_at=NOW,
        symbol="SOLUSD",
        primary_pair="SOLUSD",
        episode_id=good_episode,
    )
    opip = SimpleNamespace(
        funnel=_Funnel(
            {
                ("SOLUSD", "LONG"): state_good,
                ("ADAUSD", "LONG"): state_bad,
            }
        )
    )

    summary = route_qualified_opportunities(
        [ranked_bad, ranked_good],
        scan_facts=PaperV2ScanFacts(
            snapshots=tuple(cohort),
            decision_at=NOW,
            universe_assets=(_universe_asset(),),  # SOL only
        ),
        stamp=_stamp(),
        settings=_Settings(),
        opip=opip,
        client=client,
        kraken_client=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
    )

    assert summary.handoff_failures == 1
    assert summary.executed == 1
    assert len(_rows(server.writer, CTX_EVENT)) == 1


def test_producer_failure_does_not_fall_back_and_is_counted(writer_env):
    """An execution error is an operational failure, never a legacy fallback."""
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    # A stale book makes the producer fail at the quote stage.
    requests: list = []

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=requests, stale=True)),
        registry=_registry(),
        opip=opip,
    )

    assert summary.executed == 0
    assert summary.operational_failures == 1
    assert summary.legacy_calls == 0
    # It got as far as a real read, then stopped. No fill, and no legacy call.
    assert requests, "expected a pre-trade read before the failure"
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_cohort_snapshot_failure_fails_closed_for_all_without_legacy_fallback(
    writer_env, monkeypatch
):
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(observations=observations, decision_at=NOW)
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))

    import app.services.paper_v2_scan_router as router_module

    def _boom(*args, **kwargs):
        raise ValueError("cohort unavailable")

    monkeypatch.setattr(
        router_module, "build_canonical_episode_snapshots", _boom, raising=True
    )

    summary = _route(
        [ranked],
        observations=observations,
        client=client,
        kraken=KrakenClient(transport=_EchoTransport(requests=[])),
        registry=_registry(),
        opip=opip,
    )
    assert summary.handoff_failures == 1
    assert summary.executed == 0
    assert summary.legacy_calls == 0


def test_handoff_error_is_not_a_strategy_rejection():
    """Operational failure stays distinct from a strategy rejection."""
    assert issubclass(PaperV2HandoffError, RuntimeError)
    from app.services.paper_v2_execution import PaperV2ExecutionError

    assert not issubclass(PaperV2HandoffError, PaperV2ExecutionError)
    assert not issubclass(PaperV2ExecutionError, PaperV2HandoffError)


# ---------------------------------------------------------------------------
# Execution clock: freshness is measured at receipt, not at qualification
# ---------------------------------------------------------------------------


def _run_with_clock(
    writer_env,
    *,
    ask: float = 100.0,
    bid: float = 99.9,
    ask_qty: float = 12.0,
    published_at: str = "2026-09-19T11:59:59Z",
    capital: float = 500.0,
    notional: float = 500.0,
    clock=None,
):
    """Route one LONG through a transport whose committed book is parameterised."""
    server, client = writer_env
    observations = [_observation()]
    ranked, state = _ranked(
        observations=observations,
        decision_at=NOW,
        capital=capital,
        notional=notional,
        valid_now=True,
    )
    opip = SimpleNamespace(funnel=_Funnel({("SOLUSD", "LONG"): state}))
    requests: list = []
    summary = route_qualified_opportunities(
        [ranked],
        scan_facts=PaperV2ScanFacts(
            snapshots=tuple(observations),
            decision_at=NOW,
            universe_assets=(_universe_asset(),),
        ),
        stamp=_stamp(),
        settings=_Settings(),
        opip=opip,
        client=client,
        kraken_client=KrakenClient(
            transport=_EchoTransport(
                requests=requests,
                ask=ask,
                bid=bid,
                ask_qty=ask_qty,
                published_at=published_at,
            )
        ),
        registry=_registry(),
        execution_clock=clock,
    )
    return server, summary, requests


def test_quote_published_between_qualification_and_receipt_is_not_future_dated(
    writer_env,
):
    """T1 < T2 <= T3: a book published after qualification must still fill.

    Qualification is at T1, the venue publishes at T2 (after T1), and the producer
    receives and validates at T3. Measuring freshness against the qualification
    instant would refuse this valid book as future-dated, which is exactly the
    defect the separate execution clock fixes.
    """
    t1 = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)  # qualification
    t2 = t1 + timedelta(seconds=2)  # source publication
    t3 = t1 + timedelta(seconds=3)  # receipt
    assert t1 < t2 <= t3

    server, summary, requests = _run_with_clock(
        writer_env, published_at=t2.isoformat(), clock=lambda: t3
    )
    assert summary.executed == 1
    assert summary.operational_failures == 0
    assert requests, "the book was actually read"
    assert len(_rows(server.writer, "paper_execution.fill.recorded")) == 1


def test_quote_published_after_the_receipt_clock_is_still_refused(writer_env):
    """Control: a genuinely future-dated book is still refused."""
    server, summary, _requests = _run_with_clock(
        writer_env,
        published_at="2026-09-19T12:00:30Z",  # later than the frozen clock
        clock=lambda: NOW,
    )
    assert summary.executed == 0
    assert summary.operational_failures == 1
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_fill_occurrence_time_comes_from_the_execution_clock(writer_env):
    """Execution facts are stamped at execution time, decisions at decision time."""
    execution = datetime(2026, 9, 19, 12, 0, 30, tzinfo=timezone.utc)
    server, summary, _requests = _run_with_clock(
        writer_env,
        clock=lambda: execution,
        # Published one second before the execution clock, so it is fresh.
        published_at="2026-09-19T12:00:29Z",
    )
    assert summary.executed == 1
    fill = _rows(server.writer, "paper_execution.fill.recorded")[0]
    assert fill["fill_time"]["occurred_at"] == "2026-09-19T12:00:30Z"
    # The context still carries the qualification-derived decision instants.
    context = _rows(server.writer, CTX_EVENT)[0]
    assert context["evaluation_time"] == "2026-09-19T12:00:05Z"
    assert context["evidence_cutoff"] == "2026-09-19T12:00:00Z"


# ---------------------------------------------------------------------------
# Qualified entry geometry, reservation and displayed depth
# ---------------------------------------------------------------------------


def test_ask_above_the_qualified_chase_limit_is_not_filled(writer_env):
    server, summary, _requests = _run_with_clock(writer_env, ask=105.0, bid=104.0)
    assert summary.executed == 0
    assert summary.operational_failures == 1
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_ask_below_the_qualified_entry_band_is_not_filled(writer_env):
    server, summary, _requests = _run_with_clock(writer_env, ask=95.0, bid=94.0)
    assert summary.executed == 0
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_ask_at_or_below_the_qualified_stop_is_not_filled(writer_env):
    server, summary, _requests = _run_with_clock(writer_env, ask=89.0, bid=88.0)
    assert summary.executed == 0
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_ask_at_or_above_the_first_target_is_not_filled(writer_env):
    server, summary, _requests = _run_with_clock(writer_env, ask=110.0, bid=109.0)
    assert summary.executed == 0
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_actual_notional_above_the_reservation_is_not_filled(writer_env):
    """A rising ask must not silently exceed the approved reservation."""
    server, summary, _requests = _run_with_clock(
        writer_env, ask=101.0, bid=100.5, capital=500.0, notional=500.0
    )
    # 5 units at 101.0 is 505, above the 500 reserved.
    assert summary.executed == 0
    assert summary.operational_failures == 1
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_requested_quantity_above_displayed_ask_quantity_is_not_filled(writer_env):
    """No depth model exists, so a thin top-of-book cannot produce a full fill."""
    # The fixture requests 5 units (500 / 100), and the ask shows only 3.
    server, summary, _requests = _run_with_clock(writer_env, ask_qty=3.0)
    assert summary.executed == 0
    assert summary.operational_failures == 1
    assert _rows(server.writer, "paper_execution.fill.recorded") == []


def test_sufficient_displayed_depth_still_fills(writer_env):
    """Control: a book that supports the requested quantity still executes."""
    server, summary, _requests = _run_with_clock(writer_env, ask_qty=12.0)
    assert summary.executed == 1
    assert len(_rows(server.writer, "paper_execution.fill.recorded")) == 1


# ---------------------------------------------------------------------------
# Router authority surface
# ---------------------------------------------------------------------------


def test_router_holds_no_order_or_exchange_authority():
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_scan_router.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            referenced.add(node.name.lower())
    for token in (
        "place_order",
        "create_order",
        "submit_order",
        "kraken_private",
        "add_order",
        "cancel_order",
    ):
        assert not any(token in name for name in referenced), token


def test_router_does_not_import_the_legacy_paper_paths():
    """No legacy paper authority is reachable from the active route.

    Scans referenced identifiers rather than raw text, so the module's
    documentation of what it deliberately does *not* do is not mistaken for
    capability.
    """
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_scan_router.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            referenced.update(alias.name.lower() for alias in node.names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                referenced.add(alias.name.split(".")[-1].lower())
                referenced.add(alias.asname.lower() if alias.asname else "")
        elif isinstance(node, ast.Name):
            referenced.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr.lower())
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            referenced.add(node.name.lower())
    for module in modules:
        assert "freqtrade" not in module.lower(), module
        assert "paper_trade_engine" not in module, module
    for token in (
        "publish_qualified_long",
        "enroll_paper_opportunity",
        "freqtrade_signal_bridge",
    ):
        assert token not in referenced, token


def test_router_does_not_import_decision_intelligence_directly():
    import ast
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "paper_v2_scan_router.py"
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
