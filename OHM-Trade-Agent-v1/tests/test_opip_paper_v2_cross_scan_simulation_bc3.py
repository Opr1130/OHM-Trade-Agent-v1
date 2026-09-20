"""B/C-3 cross-scan Paper-v2 simulation over one canonical SQLite database.

This module is the production-readiness proof for PR #255. Unlike the other
B/C-3 suites, which each build an isolated store, this one simulates a *sequence
of scans and process lifecycles* against the **same** canonical database, so it
proves the invariants that only appear across scans:

* S1  a first scan creates a canonical Paper-v2 exposure (plan before exposure);
* S2  a later scan sees that exposure through the real action gate, and the
      outcome is demonstrably different from the no-exposure case (non-vacuous);
* S3  an interrupted, accepted-but-unfilled attempt survives the loss of its
      opportunity and is completed by canonical recovery in a fresh process;
* S4  a restart reproduces every projection byte-for-byte and never re-executes;
* S5  the *canonical closure contract* removes exposure and releases capacity
      exactly once (see "Known gap" — no production exit producer exists yet);
* S6  the freed capacity is genuinely reusable by a later candidate;
* S7  the legacy drain gate moves DRAINING -> READY across scans with no
      configuration change, and only then may Paper v2 admit;
* S8  an unreadable legacy state fails closed to UNAVAILABLE and recovers.

Test order is significant and intentional: the module-scoped ``env`` fixture
owns one database, and the scenarios are phases of one simulation. Scenarios run
in definition order (pytest default; this repository does not use a random-order
plugin). The final test asserts the cross-scan global invariants over the
database the whole simulation produced.

Because the scenarios are phases of a trajectory, a scenario selected *in
isolation* cannot run: the autouse ``_sequential_simulation_precondition`` fixture
rejects such a selection with an explicit message naming the missing earlier
phases, rather than letting it fail as an incidental ``KeyError``. It never skips
or weakens an assertion. Run the module whole.

Known gap this module does NOT close
------------------------------------

Filled Paper-v2 exposure has **no production path to closure**. As of this commit:

* nothing in ``app/`` calls ``trigger_paper_protection_action`` (the writer RPC and
  client method exist, but no production caller does);
* nothing in ``app/`` constructs an EXIT order intent;
* no production code produces a SELL fill;
* the only reconciliation producer in ``app/`` is the zero-fill terminalizer, which
  by design refuses once exposure exists.

Consequently a trade that reaches a fill keeps its reservation and slot for the
lifetime of the canonical store: capacity consumed by a filled Paper-v2 trade is
never returned. S5 exercises the writer's closure *contract* with hand-built
canonical evidence, which is why it is not presented as an end-to-end lifecycle
proof. Wiring a production exit/close producer is B/C-2 scope and is not part of
this cutover-wiring change; until it exists, operating Paper v2 with the mode
``active`` can exhaust capacity.

Stubs and the invariants that stay real
---------------------------------------
Only the seams below are faked. Each is a boundary the task explicitly allows;
every stub documents the invariant that remains real.

1. ``_BookTransport`` - the public Kraken Level-1 transport. It is a plain
   ``request()`` returning bids/asks plus a ``publication_ts``. **Real:** this
   path has no order authority at all; the producer's quote-evidence contract,
   freshness validation, instrument/lineage binding, executable-geometry and
   displayed-depth refusals, frozen economics and canonical commit proof all run
   unchanged.
2. ``scan_opportunities._legacy_drain_status`` - the legacy-drain evidence seam.
   **Real:** ``_resolve_paper_authority`` itself, and the exclusive authority
   flags (only READY routes Paper v2; only LEGACY admits legacy).
3. ``active_trade_registry.TRADE_FILE`` (a filesystem location) in S2, so the
   legacy registry can be read hermetically. **Real:** the action gate's
   canonical position source; the test proves BTC blocks while legacy is empty.
4. Outer scan inputs (``scan_market``/``select_candidates``/``main()``) are not
   driven at all: S1-S6 call the real producer and the real action gate directly
   because that is sufficient, and S7 calls the real ``_resolve_paper_authority``
   and the real ``_paper_lineage_attribution``. No notification delivery is
   stubbed because no scan orchestrator is run.

All filesystem writes are confined to pytest's ``tmp_path``.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from app.exchanges.kraken import KrakenClient
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_ECONOMIC_MODEL_VERSION,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
    PAPER_EXECUTION_MODEL_VERSION,
)
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    paper_evidence_idempotency_key,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
    quote_evidence_idempotency_key,
)
from app.services.paper_v2_execution import (
    PaperV2ExecutionError,
    PaperV2Opportunity,
    build_disposition_id,
    recover_outstanding_paper_v2_trades,
    run_paper_v2_opportunity,
)

# ---------------------------------------------------------------------------
# Fixed simulation instants
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
QUALIFICATION_TIME = NOW + timedelta(seconds=5)
#: The fixture book's own source instant. Earlier than every execution reading,
#: which is the real production ordering the writer enforces.
QUOTE_PUBLISHED_AT = "2026-09-19T11:59:59Z"
#: A later instant used for the canonical closure of an already-open exposure.
CLOSURE_TS = "2026-09-19T14:00:00Z"

#: Canonical event types the simulation counts and inspects.
CTX_EVENT = "decision_intelligence.context.recorded"
DISPOSITION_EVENT = "paper_execution.opportunity_disposition.recorded"

#: The event families that must never be duplicated by a restart or a recovery.
_EXECUTION_FAMILIES = (
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
    PAPER_QUOTE_EVIDENCE_RECORDED,
    PAPER_RECONCILIATION_RECORDED,
    DISPOSITION_EVENT,
)


class _Settings:
    """A test settings double. Paper v2 mode is the exact canonical value."""

    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400
    account_equity = 10_000.0
    paper_trade_starting_equity = 10_000.0


class _BookTransport:
    """STUB: the public Kraken Level-1 transport only.

    ``request()`` returns a bid/ask book with a publication instant, exactly the
    shape ``fetch_fresh_level1_observation`` consumes. It grants no order
    authority: the producer still validates freshness, binds the quote to the
    canonical instrument identity, refuses a book outside the qualified entry
    geometry or too thin to fill, prices the fill from the frozen economics and
    requires a durable canonical commit. Only the venue bytes are faked.
    """

    def __init__(
        self,
        *,
        bid: float = 99.9,
        ask: float = 100.0,
        bid_qty: float = 10.0,
        ask_qty: float = 12.0,
    ) -> None:
        self.requests: list[tuple[str, Any]] = []
        self._bid = float(bid)
        self._ask = float(ask)
        self._bid_qty = float(bid_qty)
        self._ask_qty = float(ask_qty)

    def request(self, endpoint, params, timeout_seconds):
        self.requests.append((endpoint, params.get("symbol")))
        return {
            "symbol": params.get("symbol"),
            "bids": [{"price": self._bid, "qty": self._bid_qty, "publication_ts": QUOTE_PUBLISHED_AT}],
            "asks": [{"price": self._ask, "qty": self._ask_qty, "publication_ts": QUOTE_PUBLISHED_AT}],
        }

    def telemetry_snapshot(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# Canonical environment: ONE database across the whole simulation
# ---------------------------------------------------------------------------


class _CanonicalEnv:
    """One canonical database, with restart support.

    A restart stops the current server and opens a fresh ``CanonicalWriterServer``
    over the same db path. That is the process abstraction the cross-scan
    simulation needs: the new server shares no in-memory state with the old one,
    only the durable evidence.
    """

    def __init__(self, *, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.db_path = tmp_path / "canonical.sqlite3"
        self._socket_sequence = 0
        self.server = self._new_server()
        self.client = InProcessWriterClient(self.server)

    def _new_server(self) -> CanonicalWriterServer:
        self._socket_sequence += 1
        return CanonicalWriterServer(
            db_path=self.db_path,
            socket_path=self.tmp_path / f"canonical-{self._socket_sequence}.sock",
        )

    def restart(self) -> None:
        """Simulate a fresh process over the same durable canonical store."""
        self.server.stop()
        self.server = self._new_server()
        self.client = InProcessWriterClient(self.server)


@pytest.fixture(scope="module")
def env(tmp_path_factory) -> _CanonicalEnv:
    holder = _CanonicalEnv(
        tmp_path=tmp_path_factory.mktemp("paper-v2-cross-scan")
    )
    try:
        yield holder
    finally:
        holder.server.stop()


# ---------------------------------------------------------------------------
# Shared evidence carried between simulation phases
# ---------------------------------------------------------------------------

_EVIDENCE: dict[str, Any] = {
    "trades": {},
    "long_trade_ids": set(),
    "pre_closure_positions": [],
    "s2_non_vacuity": {},
}


#: Prerequisite evidence each scenario consumes. These scenarios are phases of ONE
#: trajectory over a single canonical database, so a scenario selected in isolation
#: cannot replay its earlier phases.
_REQUIRES: dict[str, tuple[str, ...]] = {
    "test_s2_second_scan_sees_the_first_scans_exposure": ("BTCUSD",),
    "test_s3_interrupted_accepted_attempt_survives_loss_of_opportunity": ("BTCUSD",),
    "test_s4_restart_reproduces_projections_and_never_re_executes": (
        "BTCUSD",
        "SOLUSD",
    ),
    "test_s5_canonical_closure_removes_exposure_and_releases_once": (
        "BTCUSD",
        "SOLUSD",
    ),
    "test_s6_freed_capacity_is_reusable_by_a_later_candidate": ("BTCUSD", "SOLUSD"),
    "test_global_cross_scan_canonical_invariants": ("BTCUSD", "SOLUSD"),
}


@pytest.fixture(autouse=True)
def _sequential_simulation_precondition(request):
    """Reject a partial selection of this simulation with an explicit reason.

    The scenarios are ordered phases of one cross-scan trajectory, so running one
    in isolation (``-k``, ``--lf``, a randomising plugin, CI sharding) cannot
    reproduce the canonical state its assertions depend on. Failing loudly with the
    missing phases is deliberate: it turns an incidental ``KeyError`` into an
    actionable message, and it never silently skips or weakens an assertion.
    """
    if "env" not in request.fixturenames:
        return
    missing = [
        symbol
        for symbol in _REQUIRES.get(request.node.name, ())
        if symbol not in _EVIDENCE["trades"]
    ]
    if missing:
        pytest.fail(
            "the cross-scan simulation is a sequential trajectory: "
            f"{request.node.name} requires the earlier phases that produce {missing}. "
            "Run the whole module instead: "
            "pytest tests/test_opip_paper_v2_cross_scan_simulation_bc3.py",
            pytrace=False,
        )


# ---------------------------------------------------------------------------
# Opportunity construction (real production-shaped inputs)
# ---------------------------------------------------------------------------


def _symbol_base(production_symbol: str) -> str:
    return production_symbol.removesuffix("USD")


def _native_symbol(production_symbol: str) -> str:
    return f"{_symbol_base(production_symbol)}/USD"


def _candidate_id(production_symbol: str) -> str:
    base = _symbol_base(production_symbol).lower()
    return "OPIPC:" + (base + "0" * 20)[:20]


def _instrument_version(production_symbol: str) -> InstrumentVersion:
    base = _symbol_base(production_symbol)
    return InstrumentVersion(
        venue="kraken",
        base_asset=base,
        quote_currency="USD",
        venue_instrument_id=_native_symbol(production_symbol),
        version=1,
        reference_data_version=f"ref-{base.lower()}",
        observed_at_utc=NOW - timedelta(minutes=5),
    )


def _snapshot_payload(*, symbol: str, decision: datetime = NOW) -> dict:
    """A real production-shaped canonical episode snapshot.

    Built by the real builder, so episode/cohort/snapshot identities are the
    builder's own rather than hand-rolled test identities.
    """
    from app.services.canonical_episode_capture import (
        build_canonical_episode_snapshots,
    )

    observation = SimpleNamespace(
        symbol=symbol,
        base_asset=_symbol_base(symbol),
        kraken_public_symbol=_native_symbol(symbol),
        last_price=100.0,
        ticker_last=100.0,
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=105.0,
        low_24h=95.0,
        lift_from_24h_low_pct=5.0,
        distance_from_24h_high_pct=4.0,
    )
    candidate = SimpleNamespace(
        symbol=symbol,
        universe_size=3,
        stage="BREAKOUT_CANDIDATE",
        pattern="REACCELERATION",
        opportunity_score=78.0,
        explosion_potential_score=74.0,
        tradeability_score=72.0,
        pattern_strength_score=80.0,
        volume_acceleration_score=70.0,
        relative_strength_score=88.0,
        persistence_scans=3,
        exhaustion_penalty=10.0,
        exhaustion_band="LOW",
        relative_strength_percentile=95.0,
        suppressed=False,
        reasons=(),
        components={"near_high": 75.0},
    )
    return build_canonical_episode_snapshots(
        [observation],
        candidates=[candidate],
        decision_at=decision,
        signal_quality_enabled=True,
        scan_source="LIVE_FULL_MARKET",
    )[0]


def _opportunity(production_symbol: str) -> PaperV2Opportunity:
    """A qualified LONG opportunity for one production symbol.

    Geometry and capital are the approved, already-qualified facts the producer
    is contractually forbidden to recalculate.
    """
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )

    snapshot = _snapshot_payload(symbol=production_symbol)
    version = _instrument_version(production_symbol)
    return PaperV2Opportunity(
        candidate_id=_candidate_id(production_symbol),
        episode_id=snapshot["episode_id"],
        cohort_id=snapshot["cohort_id"],
        direction="LONG",
        instrument_version_id=version.instrument_version_id,
        snapshot_payload=snapshot,
        evaluation_time=QUALIFICATION_TIME,
        evidence_cutoff=NOW,
        source_record_refs=(),
        qualification_policy_version=GATE_POLICY_VERSION,
        qualification_policy_fingerprint=gate_policy_fingerprint(),
        instrument_version=version,
        quote_currency="USD",
        requested_capital=500.0,
        requested_reservation_amount=500.0,
        decision_time=QUALIFICATION_TIME,
        native_symbol=_native_symbol(production_symbol),
        requested_quantity=5.0,
        requested_notional=500.0,
        entry_low=99.0,
        entry_high=101.0,
        chase_limit=102.0,
        stop_price=90.0,
        target_prices=(110.0, 120.0),
    )


def _disposition_id(production_symbol: str) -> str:
    snapshot = _snapshot_payload(symbol=production_symbol)
    return build_disposition_id(
        episode_id=snapshot["episode_id"],
        native_symbol=_native_symbol(production_symbol),
    )


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------


def _fixed_clock(value: datetime) -> Callable[[], datetime]:
    return lambda: value


def _sequence_clock(values: list[datetime], *, fallback: datetime) -> Callable[[], datetime]:
    pending = list(values)

    def _clock() -> datetime:
        if pending:
            return pending.pop(0)
        return fallback

    return _clock


def _run(
    env: _CanonicalEnv,
    opportunity: PaperV2Opportunity,
    *,
    kraken: KrakenClient | None = None,
    settings: Any | None = None,
    clock: Callable[[], datetime] | None = None,
    now: datetime = QUALIFICATION_TIME,
):
    return run_paper_v2_opportunity(
        opportunity,
        client=env.client,
        kraken_client=kraken or KrakenClient(transport=_BookTransport()),
        settings=settings or _Settings(),
        now=now,
        clock=clock or _fixed_clock(QUALIFICATION_TIME),
    )


def _record_trade(env: _CanonicalEnv, production_symbol: str, result) -> dict:
    """Capture the canonical identities of one executed LONG trade."""
    state = env.client.get_paper_v2_execution_state(result.disposition_id)
    assert state.status == "OK", state.error_code or state.status
    record = {
        "symbol": production_symbol,
        "disposition_id": result.disposition_id,
        "paper_trade_id": result.paper_trade_id,
        "reservation_id": result.reservation_id,
        "quote_evidence_id": state.quote_evidence["quote_evidence_id"],
        "decision_context_id": state.decision_context_id,
        "fill_id": result.fill_id,
    }
    _EVIDENCE["trades"][production_symbol] = record
    _EVIDENCE["long_trade_ids"].add(result.paper_trade_id)
    return record


def _rows(writer, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
        "SELECT payload_json FROM events WHERE event_type = ? "
        "ORDER BY history_epoch, local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _count(writer, event_type: str) -> int:
    return int(
        writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
            "SELECT COUNT(*) FROM events WHERE event_type = ?", (event_type,)
        ).fetchone()[0]
    )


def _count_for_trade(writer, event_type: str, paper_trade_id: str) -> int:
    return int(
        writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
            "SELECT COUNT(*) FROM events WHERE event_type = ? "
            "AND json_extract(payload_json, '$.paper_trade_id') = ?",
            (event_type, paper_trade_id),
        ).fetchone()[0]
    )


def _first_sequence(writer, event_type: str) -> int:
    return int(
        writer._conn.execute(  # noqa: SLF001 - test-only canonical inspection
            "SELECT MIN(local_sequence) FROM events WHERE event_type = ?",
            (event_type,),
        ).fetchone()[0]
    )


def _family_counts(writer) -> dict[str, int]:
    return {event_type: _count(writer, event_type) for event_type in _EXECUTION_FAMILIES}


def _submit(writer, event_type: str, payload: dict):
    """Submit one raw canonical evidence record through the real writer."""
    if event_type == PAPER_QUOTE_EVIDENCE_RECORDED:
        key = quote_evidence_idempotency_key(payload)
    else:
        key = paper_evidence_idempotency_key(event_type, payload)
    return writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority="LOW",
            idempotency_key=key,
            event_type=event_type,
            payload=dict(payload),
        )
    )


def _exact(ts: str) -> dict:
    return {"precision": "EXACT", "basis": "SOURCE_REPORTED", "occurred_at": ts}


# ---------------------------------------------------------------------------
# Authority helpers (S7 / S8)
# ---------------------------------------------------------------------------


def _stub_drain(monkeypatch, status: str | None) -> None:
    """STUB: the legacy-drain evidence seam only.

    INVARIANT THAT REMAINS REAL: ``_resolve_paper_authority`` and the exclusive
    authority flags. Only the *input* legacy verdict is controlled here, never the
    decision logic that consumes it.
    """
    from app.jobs import scan_opportunities
    from app.services.paper_v2_cutover_readiness import LegacyDrainStatus

    if status is None:
        monkeypatch.setattr(scan_opportunities, "_legacy_drain_status", lambda _s: None)
        return
    verdict = LegacyDrainStatus(status=status, reason=f"legacy state {status}")
    monkeypatch.setattr(
        scan_opportunities, "_legacy_drain_status", lambda _s: verdict
    )


def _resolve_authority(monkeypatch, *, mode: str, drain: str | None):
    from app.jobs import scan_opportunities

    settings = _Settings()
    settings.opip_paper_v2_mode = mode
    _stub_drain(monkeypatch, drain)
    return scan_opportunities._resolve_paper_authority(settings)


def _gate_candidate(production_symbol: str, capital: float = 500.0):
    """The minimal candidate shape ``apply_action_gate`` consumes."""
    from app.services.entry_exit_advisor import EntryExitPlan

    plan = EntryExitPlan(
        symbol=production_symbol,
        valid_now=True,
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
    candidate = {
        "economic_qualified": True,
        "recommended_capital": capital,
        "symbol": production_symbol,
    }
    return candidate, plan


def _ranked(candidate: dict, plan) -> SimpleNamespace:
    return SimpleNamespace(
        rank=1,
        opportunity=SimpleNamespace(
            alert=candidate,
            snapshot=SimpleNamespace(trade_direction="LONG"),
            plan=plan,
        ),
        profit_ranking=SimpleNamespace(total_score=1.0),
    )


# ===========================================================================
# S1 - first scan creates canonical Paper-v2 exposure
# ===========================================================================


def test_s1_first_scan_creates_canonical_paper_v2_exposure(env, monkeypatch):
    """Proves the first scan admits and fills a qualified BTC LONG canonically.

    REAL: authority resolution, admission, protection plan, ENTRY intent, quote
    evidence, attempt, fill and the exposure/portfolio projections.
    STUBBED: the public Kraken L1 transport and the legacy-drain verdict input.

    Invariants checked: the protection plan is committed *before* the earliest
    fill-capable stage, so exposure can never exist without frozen geometry; and
    the admitted reservation is counted exactly once.
    """
    from app.jobs import scan_opportunities

    authority = _resolve_authority(monkeypatch, mode="active", drain="READY")
    assert authority.granted == scan_opportunities.AUTHORITY_PAPER_V2_READY
    assert authority.requested is True
    assert authority.paper_v2_routing is True
    assert authority.legacy_new_entry_allowed is False

    writer = env.server.writer
    assert env.client.get_paper_v2_active_exposures().exposures == []
    assert env.client.get_paper_portfolio_state("USD").active_reservations == 0

    result = _run(env, _opportunity("BTCUSD"))
    assert result.status == "EXECUTED"
    assert result.filled_quantity == 5.0

    # --- the plan precedes every fill-capable stage --------------------------
    plan_seq = _first_sequence(writer, PAPER_PROTECTION_PLAN_RECORDED)
    intent_seq = _first_sequence(writer, PAPER_ORDER_INTENT_RECORDED)
    attempt_seq = _first_sequence(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)
    fill_seq = _first_sequence(writer, PAPER_FILL_RECORDED)
    assert plan_seq < intent_seq < attempt_seq < fill_seq

    # --- every ENTRY stage exists -------------------------------------------
    intents = _rows(writer, PAPER_ORDER_INTENT_RECORDED)
    assert len(intents) == 1
    assert intents[0]["intent_role"] == "ENTRY"
    assert intents[0]["side"] == "BUY"
    assert len(_rows(writer, PAPER_QUOTE_EVIDENCE_RECORDED)) == 1
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1

    # --- exactly one canonical active exposure, from real fills -------------
    exposures = env.client.get_paper_v2_active_exposures().exposures
    assert len(exposures) == 1
    exposure = exposures[0]
    assert exposure.symbol == "BTCUSD"
    assert exposure.direction == "LONG"
    assert exposure.remaining_quantity > 0
    assert exposure.remaining_notional_basis > 0

    # --- one reservation, no double counting --------------------------------
    portfolio = env.client.get_paper_portfolio_state("USD")
    assert portfolio.active_reservations == 1
    assert portfolio.reserved_capital == pytest.approx(500.0)
    assert portfolio.portfolio_version == 1

    record = _record_trade(env, "BTCUSD", result)
    assert record["paper_trade_id"] == exposure.paper_trade_id

    # The canonical disposition itself records the ADMITTED outcome exactly once.
    dispositions = _rows(writer, DISPOSITION_EVENT)
    assert len(dispositions) == 1
    assert dispositions[0]["disposition"] == "ADMITTED"
    assert dispositions[0]["paper_trade_id"] == result.paper_trade_id
    assert float(dispositions[0]["requested_reservation_amount"]) == pytest.approx(500.0)


# ===========================================================================
# S2 - the second scan sees the first scan's exposure (non-vacuous)
# ===========================================================================


def test_s2_second_scan_sees_the_first_scans_exposure(env, monkeypatch, tmp_path):
    """Proves the next scan is constrained by the *canonical* exposure.

    REAL: ``canonical_portfolio_positions``, ``evaluate_portfolio_risk`` and
    ``_apply_ranked_action_gates`` under a granted Paper-v2 authority.
    STUBBED: only ``active_trade_registry.TRADE_FILE`` is redirected to tmp so the
    legacy registry can be read hermetically. The registry is empty; BTC still
    blocks, which proves canonical evidence - not the legacy file - is the source.

    Non-vacuity: the same ETH evaluation is run twice, with the canonical BTC
    position and with an empty position list, and the outcomes must differ.
    """
    from app.jobs import scan_opportunities
    from app.services.paper_v2_portfolio_source import canonical_portfolio_positions
    from app.services.portfolio_risk import evaluate_portfolio_risk

    writer = env.server.writer
    btc = _EVIDENCE["trades"]["BTCUSD"]

    positions = canonical_portfolio_positions(env.client)
    assert [p.symbol for p in positions] == ["BTCUSD"]
    assert positions[0].capital == pytest.approx(500.0)

    # --- BTC is refused for the exact duplicate-symbol reason ----------------
    decision = evaluate_portfolio_risk(
        active_trades=positions,
        proposed_symbol="BTCUSD",
        proposed_direction="LONG",
        proposed_capital=100.0,
        account_capital=10_000.0,
    )
    assert decision.allowed is False
    assert decision.reason == "symbol already active"

    # ...and the same refusal comes out of the REAL action gate, which reads its
    # existing positions from canonical exposure under Paper-v2 authority.
    authority = scan_opportunities.PaperAuthority(
        requested=True, granted=scan_opportunities.AUTHORITY_PAPER_V2_READY, reason="s2"
    )
    btc_candidate, btc_plan = _gate_candidate("BTCUSD")
    gated = scan_opportunities._apply_ranked_action_gates(
        [_ranked(btc_candidate, btc_plan)],
        settings=SimpleNamespace(account_equity=10_000.0),
        authority=authority,
        client=env.client,
    )
    assert gated == []
    assert btc_candidate["portfolio_risk_reason"] == "symbol already active"

    # --- non-vacuity: the ETH outcome genuinely differs with BTC present -----
    with_btc = evaluate_portfolio_risk(
        active_trades=positions,
        proposed_symbol="ETHUSD",
        proposed_direction="LONG",
        proposed_capital=100.0,
        account_capital=10_000.0,
        max_positions=1,
    )
    without_btc = evaluate_portfolio_risk(
        active_trades=[],
        proposed_symbol="ETHUSD",
        proposed_direction="LONG",
        proposed_capital=100.0,
        account_capital=10_000.0,
        max_positions=1,
    )
    assert with_btc.allowed is False
    assert with_btc.reason == "maximum simultaneous positions reached"
    assert without_btc.allowed is True
    assert (with_btc.allowed, with_btc.reason) != (
        without_btc.allowed,
        without_btc.reason,
    )
    _EVIDENCE["s2_non_vacuity"] = {
        "with_btc": (with_btc.allowed, with_btc.reason),
        "without_btc": (without_btc.allowed, without_btc.reason),
    }

    # --- the legacy registry is NOT what supplies BTC ------------------------
    # STUB: a filesystem location only (the legacy registry path), redirected so
    # the read is hermetic. INVARIANT THAT REMAINS REAL: the gate's existing
    # positions come from canonical exposure under Paper-v2 authority, so BTC
    # still blocks even though the legacy registry reads empty.
    monkeypatch.setattr(
        "app.services.active_trade_registry.TRADE_FILE",
        tmp_path / "active_trades.json",
    )
    from app.services.active_trade_registry import get_active_trades

    assert get_active_trades() == []
    assert canonical_portfolio_positions(env.client) != []
    assert _count_for_trade(writer, PAPER_FILL_RECORDED, btc["paper_trade_id"]) == 1


# ===========================================================================
# S3 - interrupted accepted attempt survives loss of the opportunity
# ===========================================================================


def test_s3_interrupted_accepted_attempt_survives_loss_of_opportunity(env):
    """Proves an accepted-but-unfilled attempt is retained and later completed.

    REAL: the producer's causal clock, ``_ExecutionRetryRequired`` semantics, the
    recoverable-execution projection and ``recover_outstanding_paper_v2_trades``.
    STUBBED: only the public Kraken L1 transport (the recovery path reads no
    market data at all, so it has no stub needs).

    The execution clock regresses *only* at the fill reading, so the attempt
    commits and the fill fails closed. Recovery then happens in a fresh process
    with no opportunity available and must add exactly one fill and nothing else.
    """
    writer = env.server.writer
    disposition_id = _disposition_id("SOLUSD")

    readings = [
        QUALIFICATION_TIME,  # plan
        QUALIFICATION_TIME,  # ENTRY intent
        QUALIFICATION_TIME,  # quote receipt
        QUALIFICATION_TIME + timedelta(seconds=10),  # accepted attempt
        QUALIFICATION_TIME - timedelta(seconds=30),  # fill: regressed
    ]
    clock = _sequence_clock(
        readings, fallback=QUALIFICATION_TIME + timedelta(seconds=60)
    )
    with pytest.raises(PaperV2ExecutionError):
        _run(env, _opportunity("SOLUSD"), clock=clock)

    state = env.client.get_paper_v2_execution_state(disposition_id)
    assert state.status == "OK"
    assert state.execution_attempt is not None
    assert state.entry_attempt_fill_capable is True
    assert state.fill is None
    assert state.terminal_reconciliation is None
    assert state.paper_trade_id
    sol_trade_id = state.paper_trade_id
    _EVIDENCE["trades"]["SOLUSD"] = {
        "symbol": "SOLUSD",
        "disposition_id": disposition_id,
        "paper_trade_id": sol_trade_id,
        "reservation_id": state.reservation_id,
        "quote_evidence_id": state.quote_evidence["quote_evidence_id"],
        "decision_context_id": state.decision_context_id,
        "fill_id": None,
    }
    _EVIDENCE["long_trade_ids"].add(sol_trade_id)

    # The attempt is fill-capable, so the reservation is retained, not released.
    portfolio = env.client.get_paper_portfolio_state("USD")
    assert portfolio.active_reservations == 2
    assert portfolio.reserved_capital == pytest.approx(1_000.0)
    assert _count(writer, PAPER_RECONCILIATION_RECORDED) == 0

    counts_before = _family_counts(writer)
    quote_rows_before = _count(writer, PAPER_QUOTE_EVIDENCE_RECORDED)

    # --- SOL does not appear in any later qualified cohort -------------------
    # A fresh process over the same database, driven only by canonical evidence.
    env.restart()
    summary = recover_outstanding_paper_v2_trades(
        env.client,
        execution_clock=_fixed_clock(QUALIFICATION_TIME + timedelta(seconds=300)),
    )
    assert summary["status"] == "OK"
    assert summary["considered"] == 1
    assert summary["completed"] == 1

    writer = env.server.writer
    recovered_state = env.client.get_paper_v2_execution_state(disposition_id)
    assert recovered_state.status == "OK"
    assert recovered_state.fill is not None
    assert _count_for_trade(writer, PAPER_FILL_RECORDED, sol_trade_id) == 1
    # Exactly one of everything - no new attempt, ENTRY, admission or quote.
    assert _count_for_trade(writer, PAPER_EXECUTION_ATTEMPT_RECORDED, sol_trade_id) == 1
    assert _count_for_trade(writer, PAPER_ORDER_INTENT_RECORDED, sol_trade_id) == 1
    assert _count(writer, DISPOSITION_EVENT) == counts_before[DISPOSITION_EVENT]
    assert _count(writer, PAPER_QUOTE_EVIDENCE_RECORDED) == quote_rows_before
    # No zero-fill terminalization of a trade that now holds a fill.
    assert _count(writer, PAPER_RECONCILIATION_RECORDED) == 0

    exposures = env.client.get_paper_v2_active_exposures().exposures
    assert sorted(exposure.symbol for exposure in exposures) == ["BTCUSD", "SOLUSD"]
    for exposure in exposures:
        assert exposure.remaining_quantity > 0

    # Pre-closure canonical evidence captured for the later capacity-reuse phase.
    from app.services.paper_v2_portfolio_source import canonical_portfolio_positions

    _EVIDENCE["pre_closure_positions"] = canonical_portfolio_positions(env.client)
    _EVIDENCE["trades"]["SOLUSD"]["fill_id"] = recovered_state.fill_id


# ===========================================================================
# S4 - restart idempotency
# ===========================================================================


def test_s4_restart_reproduces_projections_and_never_re_executes(env):
    """Proves every projection is a function of durable evidence, not process state.

    REAL: ``paper_v2_execution_state``, ``paper_v2_active_exposures``,
    ``paper_portfolio_state`` and the recovery sweep.
    STUBBED: nothing; the recovery sweep consults canonical evidence only.

    The before/after projections must be *identical*, and a recovery re-run must
    not duplicate any ENTRY, attempt or fill.
    """
    btc = _EVIDENCE["trades"]["BTCUSD"]
    sol = _EVIDENCE["trades"]["SOLUSD"]

    before = {
        "exposures": env.client.get_paper_v2_active_exposures(),
        "btc_state": env.client.get_paper_v2_execution_state(btc["disposition_id"]),
        "sol_state": env.client.get_paper_v2_execution_state(sol["disposition_id"]),
        "portfolio": env.client.get_paper_portfolio_state("USD"),
    }
    assert before["portfolio"].active_reservations == 2
    counts_before = _family_counts(env.server.writer)

    env.restart()

    after = {
        "exposures": env.client.get_paper_v2_active_exposures(),
        "btc_state": env.client.get_paper_v2_execution_state(btc["disposition_id"]),
        "sol_state": env.client.get_paper_v2_execution_state(sol["disposition_id"]),
        "portfolio": env.client.get_paper_portfolio_state("USD"),
    }
    for key in before:
        assert after[key] == before[key], key

    # Nothing is outstanding, so a recovery sweep must be a strict no-op.
    summary = recover_outstanding_paper_v2_trades(
        env.client,
        execution_clock=_fixed_clock(QUALIFICATION_TIME + timedelta(seconds=600)),
    )
    assert summary["status"] == "OK"
    assert summary["considered"] == 0
    assert summary["completed"] == 0
    assert _family_counts(env.server.writer) == counts_before


# ===========================================================================
# S5 - canonical closure removes exposure
# ===========================================================================


def test_s5_canonical_closure_removes_exposure_and_releases_once(env):
    """Proves the *canonical closure contract* releases exposure and capacity once.

    Scope: this exercises the writer's frozen rules — exit-capacity, aggregate
    conservation and terminal reconciliation — and proves they accept a truthful
    EXIT + FINAL_VERIFIED closure and release the reservation exactly once, while
    refusing to delete rows or mutate the frozen ENTRY fill.

    It is deliberately NOT presented as an end-to-end production lifecycle proof.
    No production entry point currently produces an exit fill or a non-zero-fill
    reconciliation: ``trigger_paper_protection_action`` has no production caller,
    nothing constructs an EXIT order intent, and the only reconciliation producer in
    ``app/`` is the zero-fill terminalizer. The closure below is therefore hand-built
    canonical evidence, and it demonstrates the *contract* a future production exit
    producer must satisfy rather than proving one exists. See the module docstring's
    "Known gap" section.
    """
    writer = env.server.writer
    btc = _EVIDENCE["trades"]["BTCUSD"]
    paper_trade_id = btc["paper_trade_id"]

    state = env.client.get_paper_v2_execution_state(btc["disposition_id"])
    assert state.status == "OK"
    quantity = float(state.fill["quantity"])
    quote_id = btc["quote_evidence_id"]
    context_id = btc["decision_context_id"]
    reservation_id = btc["reservation_id"]
    reserved = float(state.requested_reservation_amount)

    pre_portfolio = env.client.get_paper_portfolio_state("USD")
    assert pre_portfolio.active_reservations == 2
    assert pre_portfolio.reserved_capital == pytest.approx(1_000.0)

    # The frozen ENTRY fill is history: the closure appends, it never mutates.
    entry_fills_before = [
        row
        for row in _rows(writer, PAPER_FILL_RECORDED)
        if row["paper_trade_id"] == paper_trade_id
    ]
    assert len(entry_fills_before) == 1

    exit_order_id = f"exit-close-{paper_trade_id}"
    assert (
        _submit(
            writer,
            PAPER_ORDER_INTENT_RECORDED,
            {
                "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
                "engine": ENGINE_OPIP_PAPER_V2,
                "order_intent_id": exit_order_id,
                "paper_trade_id": paper_trade_id,
                "decision_context_id": context_id,
                "intent_seq": 0,
                "intent_role": "EXIT",
                "side": "SELL",
                "order_type": "MARKET",
                "requested_quantity": quantity,
                "requested_notional": quantity * 110.0,
                "reason_code": "CANONICAL_CLOSURE",
                "intent_time": _exact(CLOSURE_TS),
                "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
                "reservation_id": reservation_id,
            },
        ).status
        == "OK"
    )

    exit_attempt_id = f"attempt-exit-close-{paper_trade_id}"
    assert (
        _submit(
            writer,
            PAPER_EXECUTION_ATTEMPT_RECORDED,
            {
                "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
                "engine": ENGINE_OPIP_PAPER_V2,
                "execution_attempt_id": exit_attempt_id,
                "order_intent_id": exit_order_id,
                "paper_trade_id": paper_trade_id,
                "attempt_seq": 0,
                "execution_state": "ACCEPTED",
                "attempt_time": _exact(CLOSURE_TS),
                "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
                "accepted_quantity": quantity,
                "market_evidence_ref": quote_id,
            },
        ).status
        == "OK"
    )

    assert (
        _submit(
            writer,
            PAPER_FILL_RECORDED,
            {
                "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
                "engine": ENGINE_OPIP_PAPER_V2,
                "fill_id": f"fill-exit-close-{paper_trade_id}",
                "execution_attempt_id": exit_attempt_id,
                "order_intent_id": exit_order_id,
                "paper_trade_id": paper_trade_id,
                "fill_seq": 0,
                "side": "SELL",
                "quantity": quantity,
                "price": 110.0,
                "fee_cost": 0.25,
                "spread_cost": 0.25,
                "slippage_cost": 0.25,
                "other_supported_cost": 0.25,
                "fill_time": _exact(CLOSURE_TS),
                "execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
                "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
                "market_evidence_ref": quote_id,
            },
        ).status
        == "OK"
    )

    totals = writer._canonical_fill_totals(paper_trade_id)  # noqa: SLF001
    assert totals["remaining_quantity"] == pytest.approx(0.0)

    reconciliation = {
        "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
        "engine": ENGINE_OPIP_PAPER_V2,
        "reconciliation_id": f"recon-close-{paper_trade_id}",
        "paper_trade_id": paper_trade_id,
        "reconciliation_seq": 0,
        "position_state": "FLAT",
        "terminal_reconciliation_state": "FINAL_VERIFIED",
        "filled_entry_quantity": totals["entry_quantity"],
        "filled_exit_quantity": totals["exit_quantity"],
        "remaining_quantity": totals["remaining_quantity"],
        "reserved_capital": reserved,
        "realized_gross_pnl": totals["gross_pnl"],
        "recorded_execution_costs": totals["execution_costs"],
        "realized_net_pnl": totals["gross_pnl"] - totals["execution_costs"],
        "reconciled_time": _exact(CLOSURE_TS),
        "economic_model_version": PAPER_ECONOMIC_MODEL_VERSION,
    }
    assert _submit(writer, PAPER_RECONCILIATION_RECORDED, reconciliation).status == "OK"

    after_state = env.client.get_paper_v2_execution_state(btc["disposition_id"])
    assert after_state.remaining_quantity == pytest.approx(0.0)

    # The committed ENTRY fill is byte-for-byte unchanged and still present; only
    # a new EXIT fill was appended.
    entry_fills_after = [
        row
        for row in _rows(writer, PAPER_FILL_RECORDED)
        if row["paper_trade_id"] == paper_trade_id
    ]
    assert entry_fills_after[0] == entry_fills_before[0]
    assert len(entry_fills_after) == 2

    exposures = env.client.get_paper_v2_active_exposures().exposures
    assert [exposure.symbol for exposure in exposures] == ["SOLUSD"]

    released = env.client.get_paper_portfolio_state("USD")
    assert released.active_reservations == pre_portfolio.active_reservations - 1
    assert released.reserved_capital == pytest.approx(
        pre_portfolio.reserved_capital - reserved
    )

    # An exact replay resolves idempotently and never releases a second time.
    duplicate = _submit(writer, PAPER_RECONCILIATION_RECORDED, reconciliation)
    assert duplicate.status == "DUPLICATE_OK"
    after_duplicate = env.client.get_paper_portfolio_state("USD")
    assert (after_duplicate.active_reservations, after_duplicate.reserved_capital) == (
        released.active_reservations,
        released.reserved_capital,
    )
    assert _count(writer, PAPER_RECONCILIATION_RECORDED) == 1


# ===========================================================================
# S6 - later capacity reuse
# ===========================================================================


def test_s6_freed_capacity_is_reusable_by_a_later_candidate(env):
    """Proves capacity released by the closure is genuinely reusable.

    The candidate's *earlier* rejection is a real evaluation against the
    pre-closure canonical positions captured while BTC was open: the position
    limit was reached. After the closure the same evaluation succeeds.

    REAL: ``canonical_portfolio_positions`` and ``evaluate_portfolio_risk``.
    STUBBED: nothing.
    """
    from app.services.paper_v2_portfolio_source import canonical_portfolio_positions
    from app.services.portfolio_risk import evaluate_portfolio_risk

    pre_closure = _EVIDENCE["pre_closure_positions"]
    assert sorted(position.symbol for position in pre_closure) == ["BTCUSD", "SOLUSD"]

    earlier = evaluate_portfolio_risk(
        active_trades=pre_closure,
        proposed_symbol="ADAUSD",
        proposed_direction="LONG",
        proposed_capital=100.0,
        account_capital=10_000.0,
        max_positions=2,
    )
    assert earlier.allowed is False
    assert earlier.reason == "maximum simultaneous positions reached"

    post_closure = canonical_portfolio_positions(env.client)
    assert [position.symbol for position in post_closure] == ["SOLUSD"]

    later = evaluate_portfolio_risk(
        active_trades=post_closure,
        proposed_symbol="ADAUSD",
        proposed_direction="LONG",
        proposed_capital=100.0,
        account_capital=10_000.0,
        max_positions=2,
    )
    assert later.allowed is True
    assert (earlier.allowed, earlier.reason) != (later.allowed, later.reason)


# ===========================================================================
# S7 - DRAINING -> READY across scans
# ===========================================================================


def test_s7_draining_becomes_ready_across_scans_without_config_change(env, monkeypatch):
    """Proves the cutover completes on its own once legacy drains.

    REAL: ``_resolve_paper_authority`` and ``_paper_lineage_attribution``.
    STUBBED: only the legacy-drain verdict input.

    Scan A reports DRAINING: no new entry is authorized in *either* authority
    (``paper_v2_routing`` and ``legacy_new_entry_allowed`` are both False), and a
    WAIT and a SHORT are not Paper-v2 entry requests either. Scan B, with the
    configuration unchanged and only the legacy obligation finished, grants READY
    and Paper v2 may admit.
    """
    from app.jobs import scan_opportunities

    settings = _Settings()

    # --- Scan A: legacy still holds an obligation ----------------------------
    _stub_drain(monkeypatch, "DRAINING")
    authority_a = scan_opportunities._resolve_paper_authority(settings)
    assert authority_a.requested is True
    assert authority_a.granted == scan_opportunities.AUTHORITY_PAPER_V2_DRAINING
    assert authority_a.paper_v2_routing is False
    assert authority_a.legacy_new_entry_allowed is False

    # --- Scan B: same configuration, legacy obligation finished --------------
    _stub_drain(monkeypatch, "READY")
    authority_b = scan_opportunities._resolve_paper_authority(settings)
    assert authority_b.requested is True
    assert authority_b.granted == scan_opportunities.AUTHORITY_PAPER_V2_READY
    assert authority_b.paper_v2_routing is True
    assert authority_b.legacy_new_entry_allowed is False

    # A WAIT and a SHORT are never Paper-v2 entry requests, even when granted.
    wait = scan_opportunities._paper_lineage_attribution(
        paper_enabled=False, direction="LONG", valid_now=False, authority=authority_b
    )
    assert wait == (False, scan_opportunities.PAPER_ENGINE_PAPER_V2_WAIT)
    short = scan_opportunities._paper_lineage_attribution(
        paper_enabled=False, direction="SHORT", valid_now=True, authority=authority_b
    )
    assert short == (False, scan_opportunities.PAPER_ENGINE_NO_AUTHORITATIVE_SHORT)

    # --- Paper v2 may admit now ---------------------------------------------
    result = _run(env, _opportunity("ADAUSD"))
    assert result.status == "EXECUTED"
    _record_trade(env, "ADAUSD", result)


# ===========================================================================
# S8 - legacy state unavailable
# ===========================================================================


def test_s8_unreadable_legacy_state_fails_closed_then_recovers(monkeypatch):
    """Proves an unreadable legacy state blocks *both* authorities, then recovers.

    REAL: ``_resolve_paper_authority``.
    STUBBED: only the legacy-drain verdict input, which reports ``None`` to model
    an unreadable store (a distinct input from "drained").

    UNAVAILABLE must not be conflated with DRAINING: it authorizes no Paper-v2
    routing and no new legacy entry either. Once drain evidence is healthy, a
    later resolution yields READY - progress is possible.
    """
    from app.jobs import scan_opportunities

    settings = _Settings()

    _stub_drain(monkeypatch, None)
    unavailable = scan_opportunities._resolve_paper_authority(settings)
    assert unavailable.requested is True
    assert unavailable.granted == scan_opportunities.AUTHORITY_PAPER_V2_UNAVAILABLE
    assert unavailable.paper_v2_routing is False
    assert unavailable.legacy_new_entry_allowed is False

    _stub_drain(monkeypatch, "READY")
    recovered = scan_opportunities._resolve_paper_authority(settings)
    assert recovered.granted == scan_opportunities.AUTHORITY_PAPER_V2_READY
    assert recovered.paper_v2_routing is True
    assert recovered.legacy_new_entry_allowed is False


# ===========================================================================
# Cross-scan global invariants
# ===========================================================================


def test_global_cross_scan_canonical_invariants(env):
    """Assert the invariants that must hold over the whole simulation's store.

    Run last by design: it consumes the database every scenario above produced.

    * no duplicate paper_trade_id / order_intent_id / execution_attempt_id /
      fill_id;
    * every active exposure has positive remaining quantity, and the closed BTC
      trade is not active;
    * no zero-fill terminal exists for a trade that has fills;
    * no SHORT or WAIT produced an entry: every attempt/fill belongs to a LONG
      trade, and a SHORT opportunity is refused before any canonical write.
    """
    writer = env.server.writer
    client = env.client
    long_trade_ids = set(_EVIDENCE["long_trade_ids"])
    assert len(long_trade_ids) == 3  # BTC, SOL and the S7 ADA admission

    # --- identity uniqueness -------------------------------------------------
    for identity_field, event_type in (
        ("paper_trade_id", DISPOSITION_EVENT),
        ("reservation_id", DISPOSITION_EVENT),
        ("order_intent_id", PAPER_ORDER_INTENT_RECORDED),
        ("execution_attempt_id", PAPER_EXECUTION_ATTEMPT_RECORDED),
        ("fill_id", PAPER_FILL_RECORDED),
    ):
        values = [str(row[identity_field]) for row in _rows(writer, event_type)]
        assert values, (event_type, identity_field)
        assert len(values) == len(set(values)), (event_type, identity_field)
    disposition_trade_ids = [str(row["paper_trade_id"]) for row in _rows(writer, DISPOSITION_EVENT)]
    assert len(disposition_trade_ids) == len(set(disposition_trade_ids))
    assert set(disposition_trade_ids) == long_trade_ids

    # --- exposure invariants -------------------------------------------------
    exposures = client.get_paper_v2_active_exposures().exposures
    assert sorted(exposure.symbol for exposure in exposures) == ["ADAUSD", "SOLUSD"]
    active_ids = set()
    for exposure in exposures:
        assert exposure.remaining_quantity > 0
        active_ids.add(exposure.paper_trade_id)
    btc = _EVIDENCE["trades"]["BTCUSD"]
    assert btc["paper_trade_id"] not in active_ids

    # --- no zero-fill terminal for a trade that has fills --------------------
    zero_fill_terminals = [
        row
        for row in _rows(writer, PAPER_RECONCILIATION_RECORDED)
        if row["terminal_reconciliation_state"] == "FINAL_VERIFIED"
        and float(row["filled_entry_quantity"]) == 0.0
        and float(row["filled_exit_quantity"]) == 0.0
    ]
    for row in zero_fill_terminals:
        assert _count_for_trade(
            writer, PAPER_FILL_RECORDED, str(row["paper_trade_id"])
        ) == 0
    # In this simulation no zero-fill terminal exists at all, so no filled trade
    # can have been mistakenly released.
    assert zero_fill_terminals == []

    # --- no SHORT or WAIT produced an entry ---------------------------------
    attempts = _rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)
    fills = _rows(writer, PAPER_FILL_RECORDED)
    assert attempts and fills
    assert {str(row["paper_trade_id"]) for row in attempts} <= long_trade_ids
    assert {str(row["paper_trade_id"]) for row in fills} <= long_trade_ids

    # A candidate that was evaluated (ETH in S2) but never entered has no
    # admission and therefore no attempt or fill.
    eth_state = client.get_paper_v2_execution_state(_disposition_id("ETHUSD"))
    assert eth_state.status == "OK"
    assert eth_state.admitted is False
    assert eth_state.execution_attempt is None
    assert eth_state.fill is None

    before = _family_counts(writer)
    short_opportunity = replace(_opportunity("ETHUSD"), direction="SHORT")
    with pytest.raises(PaperV2ExecutionError):
        _run(env, short_opportunity)
    assert _family_counts(writer) == before
    assert _count(writer, CTX_EVENT) == 3

    # The S2 non-vacuity evidence is part of the final proof: the same candidate
    # really did resolve differently with and without the canonical exposure.
    non_vacuity = _EVIDENCE["s2_non_vacuity"]
    assert non_vacuity["with_btc"] != non_vacuity["without_btc"]
