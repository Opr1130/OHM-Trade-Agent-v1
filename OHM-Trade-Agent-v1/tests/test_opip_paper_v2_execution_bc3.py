"""B/C-3 execution producer tests (increment 5).

Proves the full canonical path, restart reconstruction at every commit boundary,
fail-closed behaviour with no legacy fallback, and that the protection plan is
reused rather than rebuilt under its deterministic identity.
"""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.exchanges.kraken import KrakenClient, KrakenTransportError
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.contracts.identity import InstrumentVersion
from app.opip.contracts.paper_execution_events import (
    PAPER_EXECUTION_ATTEMPT_RECORDED,
    PAPER_FILL_RECORDED,
    PAPER_ORDER_INTENT_RECORDED,
    PAPER_PROTECTION_PLAN_RECORDED,
)
from app.opip.contracts.paper_execution_runtime import (
    PAPER_QUOTE_EVIDENCE_RECORDED,
)
from app.services.canonical_episode_capture import build_canonical_episode_snapshots
from app.opip.decision.versioning import GATE_POLICY_VERSION, gate_policy_fingerprint
from app.services.paper_v2_execution import (
    PaperV2ExecutionError,
    PaperV2Opportunity,
    build_disposition_id,
    run_paper_v2_opportunity,
)

NOW = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
INSTRUMENT_VERSION_ID = "INSTR:kraken:SOL:USD:1"
APP_ROOT = Path(__file__).resolve().parents[1] / "app"


class _Settings:
    opip_paper_v2_mode = "active"
    paper_v2_quote_max_age_seconds = 15
    paper_trade_fee_rate = 0.004
    paper_trade_slippage_bps = 10.0
    paper_v2_tp1_fraction = 0.5
    paper_v2_max_hold_seconds = 86_400


class _Transport:
    def __init__(self, *, stale: bool = False, future: bool = False, error=None):
        self._stale = stale
        self._future = future
        self._error = error

    def request(self, endpoint, params, timeout_seconds):
        if self._error is not None:
            raise self._error
        if self._future:
            ts = "2026-09-19T12:00:30Z"
        elif self._stale:
            ts = "2026-09-19T11:00:00Z"
        else:
            ts = "2026-09-19T11:59:59Z"
        return {
            "symbol": "SOL/USD",
            "bids": [{"price": 99.9, "qty": 10.0, "publication_ts": ts}],
            "asks": [{"price": 100.1, "qty": 12.0, "publication_ts": ts}],
        }

    def telemetry_snapshot(self):
        return {}


def _kraken(**kwargs) -> KrakenClient:
    return KrakenClient(transport=_Transport(**kwargs))


def _version(**overrides) -> InstrumentVersion:
    fields = {
        "venue": "kraken",
        "base_asset": "SOL",
        "quote_currency": "USD",
        "venue_instrument_id": "SOL/USD",
        "version": 1,
        "reference_data_version": "ref-1",
        "observed_at_utc": NOW - timedelta(minutes=5),
    }
    fields.update(overrides)
    return InstrumentVersion(**fields)


def _snapshot_payload(
    *,
    decision: datetime = NOW,
    symbol: str = "SOLUSD",
    price: float = 100.0,
) -> dict:
    """A real production-shaped canonical episode snapshot.

    Built by the real builder, so the cohort/episode/snapshot identities are the
    builder's own. Because the cohort identity includes the decision instant, a
    test obtains a genuinely distinct episode by capturing at a different moment -
    which is also what distinguishes two episodes in production.
    """
    observation = SimpleNamespace(
        symbol=symbol,
        base_asset=symbol.removesuffix("USD"),
        kraken_public_symbol=f"{symbol.removesuffix('USD')}/USD",
        last_price=float(price),
        ticker_last=float(price),
        volume_24h=100_000.0,
        notional_24h_usd_approx=1_000_000.0,
        high_24h=float(price) * 1.05,
        low_24h=float(price) * 0.95,
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


def _opportunity(**overrides) -> PaperV2Opportunity:
    snapshot = overrides.pop("snapshot_payload", None)
    snapshot_at = overrides.pop("snapshot_at", NOW)
    if snapshot is None:
        snapshot = _snapshot_payload(decision=snapshot_at)
    fields = {
        "candidate_id": "candidate-1",
        "episode_id": snapshot["episode_id"],
        "cohort_id": snapshot["cohort_id"],
        "direction": "LONG",
        "instrument_version_id": INSTRUMENT_VERSION_ID,
        "snapshot_payload": snapshot,
        "evaluation_time": snapshot_at,
        "evidence_cutoff": snapshot_at,
        "source_record_refs": ("source:1",),
        # Captured where the opportunity was qualified; the producer must carry
        # this forward rather than re-read the live policy.
        "qualification_policy_version": GATE_POLICY_VERSION,
        "qualification_policy_fingerprint": gate_policy_fingerprint(),
        "instrument_version": _version(),
        "quote_currency": "USD",
        "requested_capital": 500.0,
        "requested_reservation_amount": 500.0,
        "decision_time": snapshot_at,
        "native_symbol": "SOL/USD",
        "requested_quantity": 5.0,
        "requested_notional": 500.0,
        "stop_price": 90.0,
        "target_prices": (110.0, 120.0),
    }
    fields.update(overrides)
    return PaperV2Opportunity(**fields)


@pytest.fixture
def env(tmp_path):
    server = CanonicalWriterServer(
        db_path=tmp_path / "canonical.sqlite3",
        socket_path=tmp_path / "canonical.sock",
    )
    try:
        yield server, InProcessWriterClient(server)
    finally:
        server.stop()


def _rows(writer, event_type: str) -> list[dict]:
    rows = writer._conn.execute(  # noqa: SLF001 - test-only inspection
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY local_sequence",
        (event_type,),
    ).fetchall()
    return [json.loads(str(row["payload_json"])) for row in rows]


def _run(env, opportunity=None, *, kraken=None, settings=None, now=NOW):
    server, client = env
    return run_paper_v2_opportunity(
        opportunity or _opportunity(),
        client=client,
        kraken_client=kraken or _kraken(),
        settings=settings or _Settings(),
        now=now,
    )


# ---------------------------------------------------------------------------
# Happy path and lineage
# ---------------------------------------------------------------------------


def test_full_path_executes_and_commits_every_stage(env):
    server, _client = env
    result = _run(env, kraken=_kraken())
    assert result.status == "EXECUTED"
    assert result.paper_trade_id and result.reservation_id
    assert result.entry_order_intent_id and result.execution_attempt_id
    assert result.fill_id and result.protection_plan_id
    assert result.filled_quantity == 5.0

    writer = server.writer
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_QUOTE_EVIDENCE_RECORDED)) == 1
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1
    assert len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1


def test_producer_commits_a_schema_v2_decision_context(env):
    """The producer's lineage context is schema v2 and reconstructs as v2.

    Increment 5's context must be the production schema-v2 contract: the reader
    must hydrate it into the v2 store, and the payload must carry the live gate
    policy identity rather than the v1 committee facts B/C-3A removed.
    """
    from app.opip.decision.versioning import (
        GATE_POLICY_VERSION,
        gate_policy_fingerprint,
    )
    from app.opip.decision_intelligence.evidence_reader import (
        read_di_evidence_snapshot,
    )

    server, _ = env
    _run(env)
    rows = _rows(server.writer, "decision_intelligence.context.recorded")
    assert len(rows) == 1
    payload = rows[0]
    assert payload["schema_version"] == 2
    assert payload["policy_version"] == GATE_POLICY_VERSION
    assert payload["policy_fingerprint"] == gate_policy_fingerprint()

    # The v1 committee facts must not reappear as fabricated lineage.
    for absent in (
        "evaluation_id",
        "consumed_input_watermark",
        "feature_version",
        "detector_version",
        "forecast_version",
        "candidate_set_ref",
    ):
        assert absent not in payload, absent

    snapshot = read_di_evidence_snapshot(db_path=server.writer.db_path)
    assert set(snapshot.contexts_v2) == {payload["context_id"]}
    assert snapshot.contexts == {}


def test_entry_intent_is_long_only_and_references_the_reservation(env):
    server, _ = env
    result = _run(env)
    order = _rows(server.writer, PAPER_ORDER_INTENT_RECORDED)[0]
    assert order["intent_role"] == "ENTRY"
    assert order["side"] == "BUY"
    assert order["reservation_id"] == result.reservation_id
    assert order["paper_trade_id"] == result.paper_trade_id


def test_quote_evidence_is_committed_before_the_attempt_that_cites_it(env):
    server, _ = env
    _run(env)
    writer = server.writer
    quote = _rows(writer, PAPER_QUOTE_EVIDENCE_RECORDED)[0]
    attempt = _rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)[0]
    fill = _rows(writer, PAPER_FILL_RECORDED)[0]
    assert attempt["market_evidence_ref"] == quote["quote_evidence_id"]
    assert fill["market_evidence_ref"] == quote["quote_evidence_id"]

    quote_seq = writer._conn.execute(  # noqa: SLF001
        "SELECT MIN(local_sequence) FROM events WHERE event_type = ?",
        (PAPER_QUOTE_EVIDENCE_RECORDED,),
    ).fetchone()[0]
    attempt_seq = writer._conn.execute(  # noqa: SLF001
        "SELECT MIN(local_sequence) FROM events WHERE event_type = ?",
        (PAPER_EXECUTION_ATTEMPT_RECORDED,),
    ).fetchone()[0]
    assert quote_seq < attempt_seq


def test_fill_uses_the_ask_side_not_the_midpoint(env):
    server, _ = env
    _run(env)
    fill = _rows(server.writer, PAPER_FILL_RECORDED)[0]
    # Best ask is 100.1; a midpoint or ticker-last price would differ.
    assert fill["price"] == pytest.approx(100.1)


def test_exposure_is_fill_derived(env):
    server, _ = env
    result = _run(env)
    totals = server.writer._canonical_fill_totals(result.paper_trade_id)  # noqa: SLF001
    assert totals["entry_quantity"] == 5.0
    assert totals["remaining_quantity"] == 5.0


def test_protection_plan_uses_the_opportunity_geometry_and_policy(env):
    server, _ = env
    _run(env)
    plan = _rows(server.writer, PAPER_PROTECTION_PLAN_RECORDED)[0]
    assert plan["stop_price"] == 90.0
    assert [t["price"] for t in plan["targets"]] == [110.0, 120.0]
    assert [t["fraction"] for t in plan["targets"]] == [0.5, 0.5]
    assert plan["max_hold_seconds"] == 86_400


def test_disposition_identity_is_deterministic():
    first = build_disposition_id(episode_id="e1", native_symbol="SOL/USD")
    second = build_disposition_id(episode_id="e1", native_symbol="SOL/USD")
    assert first == second
    assert first != build_disposition_id(episode_id="e2", native_symbol="SOL/USD")


# ---------------------------------------------------------------------------
# Restart / idempotency
# ---------------------------------------------------------------------------


def test_rerun_produces_no_duplicate_evidence(env):
    """A second run over the same opportunity must not duplicate any stage."""
    server, _ = env
    first = _run(env)
    second = _run(env)
    assert second.status == "EXECUTED"
    assert second.paper_trade_id == first.paper_trade_id
    assert second.protection_plan_id == first.protection_plan_id

    writer = server.writer
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_QUOTE_EVIDENCE_RECORDED)) == 1
    assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1
    assert len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1
    # No duplicate reservation: the portfolio still shows one active reservation.
    state = server.writer.paper_portfolio_state("USD")
    assert state.active_reservations == 1
    assert state.portfolio_version == 1


def test_new_producer_instance_after_full_commit_resumes_without_duplication(tmp_path):
    """Restart after every stage already committed."""
    db_path = tmp_path / "canonical.sqlite3"
    sock = tmp_path / "canonical.sock"

    first_server = CanonicalWriterServer(db_path=db_path, socket_path=sock)
    try:
        _run((first_server, InProcessWriterClient(first_server)))
    finally:
        first_server.stop()

    reopened = CanonicalWriterServer(db_path=db_path, socket_path=sock)
    try:
        result = _run((reopened, InProcessWriterClient(reopened)))
        assert result.status == "EXECUTED"
        writer = reopened.writer
        assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
        assert len(_rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED)) == 1
        assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1
        assert len(_rows(writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1
        assert writer.paper_portfolio_state("USD").active_reservations == 1
    finally:
        reopened.stop()


def test_restart_after_admission_only_continues_from_canonical_evidence(env, monkeypatch):
    """A failure after admission must resume, not re-admit with a new version."""
    server, client = env

    # First run fails at the quote stage, after admission has committed.
    with pytest.raises(PaperV2ExecutionError, match="quote unavailable"):
        _run(env, kraken=_kraken(error=KrakenTransportError("down")))

    writer = server.writer
    assert writer.paper_portfolio_state("USD").portfolio_version == 1
    # The ENTRY intent legitimately precedes the quote, so it is already committed.
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert _rows(writer, PAPER_EXECUTION_ATTEMPT_RECORDED) == []
    assert _rows(writer, PAPER_FILL_RECORDED) == []

    # Second run resumes: same trade, no second admission, completes the rest.
    result = _run(env)
    assert result.status == "EXECUTED"
    assert writer.paper_portfolio_state("USD").portfolio_version == 1
    assert len(_rows(writer, PAPER_ORDER_INTENT_RECORDED)) == 1
    assert len(_rows(writer, PAPER_FILL_RECORDED)) == 1


def test_progress_seam_reports_committed_stages(env):
    server, client = env
    result = _run(env)
    progress = client.get_paper_v2_execution_state(result.disposition_id)
    assert progress.status == "OK"
    assert progress.admitted is True
    assert progress.entry_order_intent_id == result.entry_order_intent_id
    assert progress.execution_attempt_id == result.execution_attempt_id
    assert progress.fill_id == result.fill_id
    assert progress.protection_plan is not None
    assert progress.protection_plan["protection_plan_id"] == result.protection_plan_id
    assert progress.remaining_quantity == 5.0


def test_progress_seam_writes_nothing(env):
    server, client = env

    def _snap():
        writer = server.writer
        return (
            writer._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],  # noqa: SLF001
            writer._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM idempotency_keys"
            ).fetchone()[0],
            writer._conn.execute(  # noqa: SLF001
                "SELECT history_epoch, next_local_sequence FROM meta WHERE id = 1"
            ).fetchone()[:2],
        )

    before = _snap()
    client.get_paper_v2_execution_state("PDISP:does-not-exist")
    assert _snap() == before


# ---------------------------------------------------------------------------
# Protection plan reuse (deterministic identity)
# ---------------------------------------------------------------------------


class _ChangedSettings(_Settings):
    # A restart must not silently restate committed economics under one identity.
    paper_v2_tp1_fraction = 0.25
    paper_v2_max_hold_seconds = 3_600


def test_restart_with_changed_policy_reuses_the_committed_plan(env):
    server, client = env
    original = _run(env, settings=_Settings())
    assert original.status == "EXECUTED"

    resumed = _run(env, settings=_ChangedSettings())
    assert resumed.protection_plan_id == original.protection_plan_id

    plans = _rows(server.writer, PAPER_PROTECTION_PLAN_RECORDED)
    assert len(plans) == 1
    # The committed economics are untouched by the changed settings.
    assert [t["fraction"] for t in plans[0]["targets"]] == [0.5, 0.5]
    assert plans[0]["max_hold_seconds"] == 86_400


def test_divergent_same_identity_plan_submission_is_rejected(env):
    """Control: the canonical writer refuses divergent economics on one identity."""
    server, client = env
    from app.opip.contracts.paper_execution_events import (
        paper_evidence_idempotency_key,
    )
    from app.opip.canonical.models import WriterIntent

    result = _run(env)
    plan = _rows(server.writer, PAPER_PROTECTION_PLAN_RECORDED)[0]
    divergent = dict(plan)
    divergent["targets"] = [
        {"target_id": "TP1", "price": 110.0, "fraction": 0.25},
        {"target_id": "TP2", "price": 120.0, "fraction": 0.75},
    ]
    ack = client.submit(
        WriterIntent(
            schema_version=1,
            priority="LOW",
            idempotency_key=paper_evidence_idempotency_key(
                PAPER_PROTECTION_PLAN_RECORDED, divergent
            ),
            event_type=PAPER_PROTECTION_PLAN_RECORDED,
            payload=divergent,
        )
    )
    assert ack.status == "REJECTED"
    assert len(_rows(server.writer, PAPER_PROTECTION_PLAN_RECORDED)) == 1
    assert result.protection_plan_id


# ---------------------------------------------------------------------------
# Admission outcomes
# ---------------------------------------------------------------------------


def _fill_portfolio(env, *, count: int):
    """Drive the portfolio to its capacity limit with real admitted trades."""
    server, _ = env
    for index in range(count):
        _run(
            env,
            _opportunity(
                snapshot_at=NOW + timedelta(seconds=index),
                candidate_id=f"filler-c-{index}",
            ),
        )


def test_capacity_rejection_stops_the_opportunity_without_fallback(env):
    """At the position limit admission stops this opportunity cleanly.

    A capacity stop is a clean terminal disposition, not an error: the spec
    requires the producer to stop the opportunity, and it must not be retried
    through another paper engine.
    """
    server, _ = env
    _fill_portfolio(env, count=3)
    result = _run(
        env,
        _opportunity(
            snapshot_at=NOW + timedelta(seconds=100), candidate_id="c-over"
        ),
    )
    assert result.status == "CAPACITY_REJECTED"
    assert result.fill_id is None
    assert len(_rows(server.writer, PAPER_FILL_RECORDED)) == 3  # only the fillers


def test_capital_rejection_stops_the_opportunity(env):
    """A reservation larger than the policy equity limit is refused cleanly."""
    server, _ = env
    result = _run(
        env,
        _opportunity(
            snapshot_at=NOW + timedelta(seconds=200),
            candidate_id="c-big",
            requested_capital=10_001.0,
            requested_reservation_amount=10_001.0,
            requested_notional=10_001.0,
        ),
    )
    assert result.status == "CAPITAL_REJECTED"
    assert result.fill_id is None
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []
    assert _rows(server.writer, PAPER_PROTECTION_PLAN_RECORDED) == []


def test_stale_portfolio_version_fails_closed_without_retry(env):
    """A lost optimistic-concurrency race stops the disposition; no mutation."""
    server, client = env
    real_read = client.get_paper_portfolio_state

    def _stale_read(quote_currency):
        state = real_read(quote_currency)
        # Simulate the version advancing between read and admit.
        return replace(state, portfolio_version=(state.portfolio_version or 0) + 7)

    client.get_paper_portfolio_state = _stale_read  # type: ignore[assignment]
    with pytest.raises(PaperV2ExecutionError, match="stale portfolio version"):
        _run(env)

    # The stale request remains as evidence, and no trade was created.
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []
    assert server.writer.paper_portfolio_state("USD").active_reservations == 0


def test_portfolio_read_failure_fails_closed(env):
    server, client = env

    def _unavailable(quote_currency):
        from app.opip.canonical.models import PaperPortfolioState

        return PaperPortfolioState(status="RETRYABLE", error_code="WORKER_UNHEALTHY")

    client.get_paper_portfolio_state = _unavailable  # type: ignore[assignment]
    with pytest.raises(PaperV2ExecutionError, match="portfolio state unavailable"):
        _run(env)
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []


# ---------------------------------------------------------------------------
# Failure boundaries, no fallback
# ---------------------------------------------------------------------------


def test_kraken_book_failure_fails_closed(env):
    server, _ = env
    with pytest.raises(PaperV2ExecutionError, match="quote unavailable"):
        _run(env, kraken=_kraken(error=KrakenTransportError("network")))
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []
    assert _rows(server.writer, PAPER_EXECUTION_ATTEMPT_RECORDED) == []


def test_stale_book_fails_closed(env):
    server, _ = env
    with pytest.raises(PaperV2ExecutionError, match="quote unavailable"):
        _run(env, kraken=_kraken(stale=True))
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []


def test_future_dated_book_fails_closed(env):
    server, _ = env
    with pytest.raises(PaperV2ExecutionError, match="quote unavailable"):
        _run(env, kraken=_kraken(future=True))
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []


def test_instrument_registration_failure_fails_closed(env):
    server, client = env

    class _Rejecting:
        def __getattr__(self, name):
            return getattr(client, name)

        def submit(self, intent):
            from app.opip.canonical.models import WriterAck

            return WriterAck(status="REJECTED", error_code="INVALID_INTENT")

    with pytest.raises(PaperV2ExecutionError, match="instrument registration failed"):
        run_paper_v2_opportunity(
            _opportunity(),
            client=_Rejecting(),
            kraken_client=_kraken(),
            settings=_Settings(),
            now=NOW,
        )


def test_context_failure_fails_closed(env):
    server, client = env
    with pytest.raises(PaperV2ExecutionError, match="decision context failed"):
        run_paper_v2_opportunity(
            # The snapshot commits, but a context with no policy fingerprint
            # cannot: the context stage must fail closed on its own facts.
            _opportunity(qualification_policy_fingerprint=""),
            client=client,
            kraken_client=_kraken(),
            settings=_Settings(),
            now=NOW,
        )
    assert _rows(server.writer, PAPER_ORDER_INTENT_RECORDED) == []


def test_unhealthy_writer_fails_closed(env):
    server, client = env
    server.mark_worker_unhealthy_for_tests("TEST")
    with pytest.raises(PaperV2ExecutionError):
        _run(env)
    assert _rows(server.writer, PAPER_FILL_RECORDED) == []


def test_producer_never_falls_back_to_a_legacy_engine(env):
    """Static proof: the producer cannot reach a legacy paper producer."""
    source = (APP_ROOT / "services" / "paper_v2_execution.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    for forbidden in (
        "app.services.paper_outcome_outbox",
        "app.services.paper_trade_engine",
        "app.services.freqtrade_signal_bridge",
        "app.exchanges.kraken_private",
    ):
        assert not any(
            name == forbidden or name.startswith(forbidden + ".") for name in modules
        ), f"producer must not import {forbidden}"


def test_no_bc3_runtime_module_reaches_private_exchange_apis():
    """Live/funded isolation: no B/C-3 module touches private trading surfaces."""
    roots = sorted((APP_ROOT / "services").glob("paper_v2_*.py")) + sorted(
        (APP_ROOT / "opip" / "canonical").glob("decision_context_bridge.py")
    )
    assert roots, "expected B/C-3 modules to exist"
    for path in roots:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
        assert not any("kraken_private" in name for name in modules), path.name
        for token in ("place_order", "create_order", "submit_order", "withdraw"):
            assert token not in source, f"{path.name} must not reference {token}"


def test_portfolio_state_is_reread_for_each_opportunity(env):
    """Sequential opportunities must each read the newly advanced version."""
    server, client = env
    reads: list[int] = []
    real_read = client.get_paper_portfolio_state

    def _counting_read(quote_currency):
        state = real_read(quote_currency)
        reads.append(state.portfolio_version)
        return state

    client.get_paper_portfolio_state = _counting_read  # type: ignore[assignment]
    _run(env, _opportunity(candidate_id="c-a"))
    _run(
        env,
        _opportunity(
            snapshot_at=NOW + timedelta(seconds=1), candidate_id="c-b"
        ),
    )

    # Version observed for the second opportunity reflects the first admission.
    assert reads == [0, 1]
    assert server.writer.paper_portfolio_state("USD").portfolio_version == 2


def test_usd_and_usdt_remain_independent(env):
    server, _ = env
    _run(env, _opportunity(quote_currency="USD"))
    _run(
        env,
        _opportunity(
            snapshot_at=NOW + timedelta(seconds=1),
            candidate_id="c-usdt",
            quote_currency="USDT",
            # The instrument identity must stay coherent with the registered
            # version, or the canonical instrument binding fails closed.
            instrument_version_id="INSTR:kraken:SOL:USDT:1",
            instrument_version=_version(quote_currency="USDT"),
        ),
    )
    assert server.writer.paper_portfolio_state("USD").portfolio_version == 1
    assert server.writer.paper_portfolio_state("USDT").portfolio_version == 1
    assert server.writer.paper_portfolio_state("USD").active_reservations == 1
    assert server.writer.paper_portfolio_state("USDT").active_reservations == 1
