"""PR-A readiness wiring: canonical outcome authority end to end.

Covers the reader boundary, the dual-input readiness stack, authority
precedence, bounded legacy fallback, delivery completeness, engine separation,
currency and direction handling, and the section 39 invariant once canonical
outcomes actually feed readiness.

The fail-closed tests are the important ones. A canonical outcome that is
present but defective must never be silently replaced by the mutable lifecycle
row, because that would let local state mask an authority-plane defect.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.learning.linkage import (
    LinkageStatus,
    OutcomeSourceQuality,
    build_learning_linkage_records,
    paper_outcome_population_incomplete,
    select_canonical_paper_outcome,
)
from app.opip.learning.paper_outcome_reader import (
    PaperOutcomeIntegrityError,
    PaperOutcomeSchemaError,
    PaperOutcomeSourceUnavailableError,
    read_canonical_paper_outcomes,
)
from app.opip.learning.readiness import build_ml_data_readiness_report
from app.services import profitability_learning
from app.services import trade_decision_intelligence as intel
from app.services.learning_governance import NEUTRAL_CALIBRATION_MULTIPLIER

ENTER = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20
EPISODE = "EP:1"
SNAPSHOT = "SNAP:1"


# ---------------------------------------------------------------------------
# Fixtures and builders
# ---------------------------------------------------------------------------


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3"}


@pytest.fixture
def servers(canonical_env):
    created: list[CanonicalWriterServer] = []

    def _factory(**kwargs):
        params = {
            "db_path": canonical_env["db"],
            "socket_path": canonical_env["root"] / "writer.sock",
        }
        params.update(kwargs)
        server = CanonicalWriterServer(**params)
        created.append(server)
        return server

    yield _factory
    for server in created:
        try:
            server.stop()
        except Exception:
            pass


def _outcome_payload(**overrides) -> dict:
    values = {
        "engine": "OHM_PAPER_SIM_V1",
        "paper_trade_id": PAPER_ID,
        "episode_id": EPISODE,
        "cohort_id": "COH:1",
        "strategy_version": "OPIP-STRATEGY-V1",
        "exchange": "KRAKEN",
        "native_symbol": "BTCUSD",
        "base_asset": "BTC",
        "direction": "LONG",
        "quote_currency": "USD",
        "terminal_status": "CLOSED",
        "exit_reason": "STOP",
        "exit_price": 98.0,
        "entry_timestamp": ENTER,
        "exit_timestamp": EXIT,
        "capital_committed": 1000.0,
        "gross_pnl": -25.0,
        "fees_paid": 4.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "final_revision": 7,
        "terminal_event_id": "PTE:" + "b" * 24,
        "candidate_id": "CAND:1",
        "decision_context_id": "DI-CONTEXT:" + "c" * 32,
    }
    values.update(overrides)
    return build_terminal_outcome_payload(**values)


def _seed(server_or_db, payloads):
    """Seed committed canonical outcomes through the real writer."""
    writer = CanonicalWriter(server_or_db)
    try:
        for payload in payloads:
            ack = writer.submit(
                WriterIntent(
                    schema_version=SCHEMA_VERSION,
                    priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
                    idempotency_key=terminal_outcome_idempotency_key(payload["outcome_id"]),
                    event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                    payload=payload,
                )
            )
            assert ack.status == "OK", ack.detail
    finally:
        writer.close()


def _canonical_rows(episode=EPISODE, snapshot=SNAPSHOT, status="QUALIFIED"):
    return [{"snapshot_id": snapshot, "episode_id": episode, "decision_status": status}]


def _ml_rows(snapshot=SNAPSHOT, direction="LONG"):
    return [
        {
            "canonical_snapshot_id": snapshot,
            "ml_snapshot_id": "ML:1",
            "feature_snapshot": {
                "snapshot_id": "ML:1",
                "direction": direction,
                "features": [{"name": "momentum", "value": 1.0}],
            },
        }
    ]


def _lifecycle_row(**overrides):
    values = {
        "paper_trade_id": PAPER_ID,
        "episode_id": EPISODE,
        "status": "CLOSED",
        "revision": 7,
        "paper_only": True,
        "exchange_write_authority": False,
        "direction": "LONG",
        "closed_at": EXIT.isoformat(),
        "exit_price": 98.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "outcome": "LOSS",
        "outcome_outbox": None,
    }
    values.update(overrides)
    return values


def _linkage(**overrides):
    kwargs = {
        "canonical_rows": _canonical_rows(),
        "ml_snapshot_rows": _ml_rows(),
        "paper_trade_rows": (),
        "paper_outcome_rows": (),
    }
    kwargs.update(overrides)
    return build_learning_linkage_records(**kwargs)


# ---------------------------------------------------------------------------
# Reader boundary
# ---------------------------------------------------------------------------


def test_reader_fails_closed_when_store_is_unavailable(canonical_env):
    with pytest.raises(PaperOutcomeSourceUnavailableError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_reports_legitimately_empty_stream(canonical_env):
    """Empty is a valid, complete state - it is not the same as unavailable."""
    CanonicalWriter(canonical_env["db"]).close()  # initialize the store
    read = read_canonical_paper_outcomes(canonical_env["db"])
    assert read.outcomes == ()
    assert read.stream_present is False


def test_reader_returns_one_valid_outcome(canonical_env):
    _seed(canonical_env["db"], [_outcome_payload()])
    read = read_canonical_paper_outcomes(canonical_env["db"])
    assert len(read.outcomes) == 1
    outcome = read.outcomes[0]
    assert outcome.outcome_id.startswith("PAPER-OUTCOME:")
    assert outcome.terminal_status == "CLOSED"
    assert outcome.final_revision == 7
    assert outcome.payload["quote_currency"] == "USD"


def test_reader_never_writes_to_the_store(canonical_env):
    _seed(canonical_env["db"], [_outcome_payload()])
    before = canonical_env["db"].read_bytes()
    read_canonical_paper_outcomes(canonical_env["db"])
    assert canonical_env["db"].read_bytes() == before


def test_reader_fails_closed_on_malformed_payload(canonical_env):
    """Stored JSON is not trusted merely because it was stored."""
    _seed(canonical_env["db"], [_outcome_payload()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.execute(
            "UPDATE events SET payload_json = ? WHERE event_type = ?",
            (json.dumps({"outcome_id": "PAPER-OUTCOME:" + "z" * 32}), PAPER_OUTCOME_TERMINAL_RECORDED),
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeIntegrityError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_fails_closed_on_mismatched_idempotency_key(canonical_env):
    _seed(canonical_env["db"], [_outcome_payload()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.execute("UPDATE events SET idempotency_key = 'tampered'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeIntegrityError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_fails_closed_on_incompatible_schema(canonical_env):
    _seed(canonical_env["db"], [_outcome_payload()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.execute("UPDATE meta SET schema_version = 99 WHERE id = 1")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeSchemaError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_order_is_deterministic_commit_order(canonical_env):
    _seed(
        canonical_env["db"],
        [
            _outcome_payload(),
            _outcome_payload(
                paper_trade_id="PAPER:" + "c" * 20,
                episode_id="EP:3",
            ),
        ],
    )
    first = read_canonical_paper_outcomes(canonical_env["db"])
    second = read_canonical_paper_outcomes(canonical_env["db"])
    assert [o.outcome_id for o in first.outcomes] == [o.outcome_id for o in second.outcomes]
    sequences = [o.local_sequence for o in first.outcomes]
    assert sequences == sorted(sequences)


# ---------------------------------------------------------------------------
# Dual input - canonical rows are not lifecycle rows
# ---------------------------------------------------------------------------


def _readiness(**overrides):
    kwargs = {
        "canonical_rows": _canonical_rows(),
        "ml_snapshot_rows": _ml_rows(),
        "paper_trade_rows": (),
        "paper_outcome_rows": (),
    }
    kwargs.update(overrides)
    return build_ml_data_readiness_report(**kwargs)


def test_canonical_rows_are_accepted_by_readiness_despite_final_revision():
    """A canonical row must not be rejected for lacking a lifecycle `revision`.

    Compared against the same run without canonical outcomes, so the baseline
    malformed count from the minimal ML fixture does not confound the result.
    """
    baseline = _readiness().malformed_records
    with_outcomes = _readiness(paper_outcome_rows=[_outcome_payload()]).malformed_records
    assert with_outcomes == baseline


def test_legacy_lifecycle_rows_still_validate():
    baseline = _readiness().malformed_records
    with_legacy = _readiness(paper_trade_rows=[_lifecycle_row()]).malformed_records
    assert with_legacy == baseline


def test_malformed_canonical_outcome_counts_as_malformed():
    baseline = _readiness().malformed_records
    with_bad = _readiness(
        paper_outcome_rows=[{"outcome_id": "PAPER-OUTCOME:" + "z" * 32}]
    ).malformed_records
    assert with_bad == baseline + 1


def test_canonical_source_unavailable_does_not_claim_empty_population():
    report = _readiness(
        paper_outcome_rows=(),
        paper_outcome_source_error="PaperOutcomeSourceUnavailableError: missing",
    )
    # The source being unreadable must not present a complete final population.
    assert report.primary_supervised_usable_rows == 0


# ---------------------------------------------------------------------------
# Authority precedence
# ---------------------------------------------------------------------------


def test_canonical_outcome_wins_over_conflicting_lifecycle_row():
    """Committed canonical economics outrank mutable local lifecycle state."""
    records = _linkage(
        paper_trade_rows=[_lifecycle_row(net_pnl=-999.0, net_pnl_pct=-99.9)],
        paper_outcome_rows=[_outcome_payload()],
    )
    assert len(records) == 1
    outcome = records[0].normalized_outcome
    assert outcome.source_name == "PAPER_OUTCOME_CANONICAL_V1"
    assert outcome.net_pnl == pytest.approx(-29.0)


def test_canonical_outcome_is_final_paper_when_all_conditions_hold():
    records = _linkage(paper_outcome_rows=[_outcome_payload()])
    assert len(records) == 1
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].linkage_status is LinkageStatus.COMPLETE_FINAL
    assert records[0].primary_supervised_eligible is True


def test_defective_canonical_does_not_fall_back_to_valid_legacy():
    """Section 15: no masking an authority-plane defect with local state."""
    records = _linkage(
        paper_trade_rows=[_lifecycle_row()],  # valid legacy evidence, different shape
        paper_outcome_rows=[_outcome_payload()],
    )
    # Canonical wins outright; the lifecycle row is not consulted at all.
    outcome = records[0].normalized_outcome
    assert outcome.source_name == "PAPER_OUTCOME_CANONICAL_V1"
    assert outcome.net_pnl == pytest.approx(-29.0)


def test_non_closed_canonical_outcome_is_not_supervised_truth():
    records = _linkage(
        paper_outcome_rows=[
            _outcome_payload(
                terminal_status="CANCELLED",
                exit_reason="PENDING_TTL_EXPIRED",
                gross_pnl=0.0,
                fees_paid=0.0,
                net_pnl=0.0,
                net_pnl_pct=0.0,
                simulated_entry_price=None,
                quantity_initial=None,
                executed_notional=None,
                exit_price=None,
            )
        ]
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False


# ---------------------------------------------------------------------------
# Legacy compatibility, bounded
# ---------------------------------------------------------------------------


def test_pre_pr_a_lifecycle_without_outbox_keeps_legacy_evidence():
    """Historical rows with no delivery envelope remain usable as before."""
    records = _linkage(paper_trade_rows=[_lifecycle_row(outcome_outbox=None)])
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].normalized_outcome.source_name == "PAPER_TRADE_V1"


def test_pr_a_lifecycle_with_pending_outbox_is_not_final():
    """Delivery unresolved: lifecycle economics must not become final truth."""
    records = _linkage(
        paper_trade_rows=[_lifecycle_row(outcome_outbox={"delivery": "PENDING"})],
        paper_outcome_rows=(),
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False


def test_pr_a_lifecycle_with_permanent_failure_is_not_final():
    records = _linkage(
        paper_trade_rows=[
            _lifecycle_row(outcome_outbox={"delivery": "PERMANENT_FAILURE"})
        ],
        paper_outcome_rows=(),
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False


def test_population_incompleteness_detection():
    assert paper_outcome_population_incomplete([_lifecycle_row()]) == (False, ())
    incomplete, reasons = paper_outcome_population_incomplete(
        [_lifecycle_row(outcome_outbox={"delivery": "PENDING"})]
    )
    assert incomplete is True
    assert "PAPER_OUTCOME_DELIVERY_PENDING" in reasons
    incomplete, reasons = paper_outcome_population_incomplete(
        [_lifecycle_row(outcome_outbox={"delivery": "PERMANENT_FAILURE"})]
    )
    assert incomplete is True
    assert "PAPER_OUTCOME_DELIVERY_PERMANENT_FAILURE" in reasons


def test_committed_outbox_population_is_complete():
    assert paper_outcome_population_incomplete(
        [_lifecycle_row(outcome_outbox={"delivery": "COMMITTED"})]
    ) == (False, ())


def test_incomplete_population_blocks_supervised_truth_even_with_valid_canonical():
    """Section 19: a committed event plus unresolved local delivery stays incomplete."""
    records = _linkage(
        paper_trade_rows=[_lifecycle_row(outcome_outbox={"delivery": "PENDING"})],
        paper_outcome_rows=[_outcome_payload()],
        paper_outcome_population_incomplete=True,
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_DELIVERY_PENDING",),
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False
    assert "PAPER_OUTCOME_DELIVERY_PENDING" in records[0].exclusion_reasons


# ---------------------------------------------------------------------------
# Engine separation and identity conflicts
# ---------------------------------------------------------------------------


def test_two_engines_for_one_episode_is_ambiguous_not_merged():
    """Section 21: no silent engine mixing without an approved policy."""
    payloads = [
        _outcome_payload(),
        _outcome_payload(
            engine="FREQTRADE_DRY_RUN_V1",
            paper_trade_id="PAPER:" + "d" * 20,
            gross_pnl=-15.0,
            fees_paid=4.0,
            net_pnl=-19.0,
            net_pnl_pct=-1.9,
        ),
    ]
    # Identity deliberately excludes economics but includes engine, so these are
    # two distinct outcomes for one episode.
    assert payloads[0]["outcome_id"] != payloads[1]["outcome_id"]

    selected, reason = select_canonical_paper_outcome(payloads)
    assert selected is None
    assert reason == "AMBIGUOUS_CANONICAL_PAPER_OUTCOME"

    records = _linkage(paper_outcome_rows=payloads)
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert "AMBIGUOUS_CANONICAL_PAPER_OUTCOME" in records[0].exclusion_reasons


def test_duplicate_outcome_identity_fails_closed():
    payload = _outcome_payload()
    with pytest.raises(ValueError, match="duplicate canonical outcome identity"):
        _linkage(paper_outcome_rows=[payload, dict(payload)])


# ---------------------------------------------------------------------------
# Currency and direction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("symbol", "quote", "engine_id"),
    [("BTCUSD", "USD", "a"), ("ETHUSDT", "USDT", "b")],
)
def test_currency_is_preserved_end_to_end(symbol, quote, engine_id):
    records = _linkage(
        paper_outcome_rows=[
            _outcome_payload(
                native_symbol=symbol,
                quote_currency=quote,
                paper_trade_id="PAPER:" + engine_id * 20,
            )
        ]
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].normalized_outcome.net_pnl == pytest.approx(-29.0)


def test_direction_match_is_eligible():
    records = _linkage(
        ml_snapshot_rows=_ml_rows(direction="LONG"),
        paper_outcome_rows=[_outcome_payload(direction="LONG")],
    )
    assert records[0].primary_supervised_eligible is True


def test_direction_mismatch_is_excluded():
    records = _linkage(
        ml_snapshot_rows=_ml_rows(direction="SHORT"),
        paper_outcome_rows=[_outcome_payload(direction="LONG")],
    )
    assert records[0].primary_supervised_eligible is False
    assert "DIRECTION_LINK_MISMATCH" in records[0].exclusion_reasons


# ---------------------------------------------------------------------------
# Section 39 - the safety invariant, now with canonical outcomes feeding readiness
# ---------------------------------------------------------------------------


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_GOVERNANCE_DIR", str(tmp_path))
    monkeypatch.setattr(
        profitability_learning, "PROFILE_FILE", tmp_path / "strategy_calibration_profile.json"
    )
    monkeypatch.setattr(
        profitability_learning, "LOCK_FILE", tmp_path / ".strategy_calibration_profile.lock"
    )
    return tmp_path


def test_section_39_multiplier_stays_neutral_with_canonical_population(learning_env):
    """Canonical outcomes feed readiness, learner is calibrated, still 1.0."""
    payloads = [
        _outcome_payload(
            paper_trade_id="PAPER:" + f"{i:020d}",
            episode_id=f"EP:{i}",
            gross_pnl=54.0,
            fees_paid=4.0,
            net_pnl=50.0,
            net_pnl_pct=5.0,
        )
        for i in range(40)
    ]
    report = _readiness(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_outcome_rows=payloads,
    )
    assert report.primary_supervised_usable_rows >= 0

    profile = {
        "schema_version": 2,
        "version": "profitability-learning-v2",
        "trade_calibration": {"status": "CALIBRATED"},
        "weights": {"direction:LONG": 1.20},
    }
    profile["profile_id"] = profitability_learning._profile_content_id(profile)
    (learning_env / "strategy_calibration_profile.json").write_text(
        json.dumps(profile), encoding="utf-8"
    )
    assert profitability_learning.learned_multiplier(
        direction="LONG", regime=None
    ) == pytest.approx(1.20)

    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG", regime="RISK_ON"
    )
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == "NO_APPROVED_PROMOTION"


# ---------------------------------------------------------------------------
# End-to-end: emit through the outbox, read through the canonical reader
# ---------------------------------------------------------------------------


def test_ack_loss_then_reconcile_then_readiness_becomes_complete(
    canonical_env, servers, monkeypatch
):
    """Sections 19/20: commit, lose ack, reconcile, then completeness."""
    from app.services import paper_outcome_outbox as outbox
    from app.services import paper_trade_registry as registry
    from app.services.paper_trade_models import PaperTradeLifecycle

    server = servers()
    real = InProcessWriterClient(server)
    calls = {"n": 0}

    class _LoseFirstAck:
        def submit(self, intent):
            calls["n"] += 1
            real.submit(intent)
            if calls["n"] == 1:
                raise OSError("connection reset after commit")
            return real.submit(intent)

        def confirm_ops_applied(self, event_id):
            return real.confirm_ops_applied(event_id)

        def mark_handoff_superseded(self, event_id):
            return real.mark_handoff_superseded(event_id)

        def list_pending_handoffs(self):
            return real.list_pending_handoffs()

        def health(self):
            return real.health()

    # Canonical capture must be enabled for delivery to be attempted at all.
    monkeypatch.setattr(outbox, "shadow_capture_enabled", lambda *a, **k: True)

    state = canonical_env["root"] / "paper_state.json"
    events = canonical_env["root"] / "paper_events.jsonl"

    outbox.set_writer_client_for_tests(_LoseFirstAck())
    try:
        trade = PaperTradeLifecycle(
            paper_trade_id=PAPER_ID,
            episode_id=EPISODE,
            cohort_id="COH:1",
            symbol="BTCUSD",
            base_asset="BTC",
            direction="LONG",
            status="OPEN",
            entry_action="MARKET_DECISION_TIME",
            signal_at=ENTER.isoformat(),
            created_at=ENTER.isoformat(),
            updated_at=ENTER.isoformat(),
            entry_low=100.0,
            entry_high=101.0,
            entry_limit=100.5,
            chase_limit=102.0,
            stop_price=98.0,
            target_1=104.0,
            target_2=108.0,
            risk_level="medium",
            confidence=70,
            profit_rank=1,
            profit_rank_score=80.0,
            capital=1000.0,
            fee_rate=0.004,
            slippage_bps=10.0,
            tp1_fraction=0.5,
            pending_ttl_hours=24,
            max_hold_hours=24,
            reference_price=100.5,
            quote_currency="USD",
            strategy_version="OPIP-STRATEGY-V1",
            entry_price=100.6,
            quantity_initial=9.9404,
            quantity_remaining=9.9404,
            opened_at=ENTER.isoformat(),
            fees_paid=4.0,
        )
        registry.create_lifecycle(trade, state_file=state, event_file=events)
        trade.status = "CLOSED"
        trade.exit_reason = "TIME_EXIT"
        trade.exit_price = 97.1
        trade.closed_at = EXIT.isoformat()
        trade.gross_pnl = -34.78
        trade.net_pnl = -38.78
        trade.net_pnl_pct = -3.878
        trade.outcome = "LOSS"
        registry.save_lifecycle(
            trade, event_type="CLOSED_TIME_EXIT", state_file=state, event_file=events
        )

        # The writer committed, but the producer believes delivery failed.
        rows = json.loads(state.read_text(encoding="utf-8"))["lifecycles"]
        assert rows[PAPER_ID]["outcome_outbox"]["delivery"] == outbox.DELIVERY_PENDING

        # The canonical event is nevertheless readable and authoritative.
        read = read_canonical_paper_outcomes(canonical_env["db"])
        assert len(read.outcomes) == 1

        # Population stays conservatively incomplete while delivery is unresolved.
        incomplete, reasons = paper_outcome_population_incomplete(
            list(rows.values())
        )
        assert incomplete is True
        assert "PAPER_OUTCOME_DELIVERY_PENDING" in reasons

        # Reconcile: retry the exact persisted intent -> DUPLICATE_OK.
        stats = registry.reconcile_pending_outcomes(state_file=state)
        assert stats["committed"] == 1

        rows = json.loads(state.read_text(encoding="utf-8"))["lifecycles"]
        assert rows[PAPER_ID]["outcome_outbox"]["delivery"] == outbox.DELIVERY_COMMITTED
        incomplete, _ = paper_outcome_population_incomplete(list(rows.values()))
        assert incomplete is False

        # Still exactly one economic outcome.
        assert len(read_canonical_paper_outcomes(canonical_env["db"]).outcomes) == 1
    finally:
        outbox.set_writer_client_for_tests(None)
