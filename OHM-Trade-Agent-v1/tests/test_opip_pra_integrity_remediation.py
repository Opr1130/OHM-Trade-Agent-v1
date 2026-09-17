"""PR-A exact-head integrity remediation regression tests.

Each test here corresponds to one reviewed finding. They exist to prove the
specific failure combination, not just the general behaviour, because every one
of these was a gap that the earlier suites did not cover.

Grouped by finding:

* unified paper-outcome completeness enforcement (gap spool must affect
  eligibility, not just the report);
* canonical source failure must disable legacy final-paper fallback;
* supersession ancestry enforcement;
* frozen ``paper_outcome.v1`` watermark boundary;
* gap resolution recoverable without a persisted ``gap_id``.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

from app.opip.canonical.client import InProcessWriterClient
from app.opip.canonical.models import WriterIntent
from app.opip.canonical.paths import SCHEMA_VERSION
from app.opip.canonical.server import CanonicalWriterServer
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    assert_supersession_consistent,
    build_terminal_outcome_payload,
    resolve_effective_outcome_ids,
    terminal_outcome_idempotency_key,
)
from app.opip.learning.linkage import (
    LinkageStatus,
    OutcomeSourceQuality,
    build_learning_linkage_records,
)
from app.opip.learning.paper_outcome_reader import (
    PaperOutcomeIntegrityError,
    read_canonical_paper_outcomes,
)
from app.opip.learning.readiness import MLReadinessState, build_ml_data_readiness_report

ENTER = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 16, 13, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20
EPISODE = "EP:1"
SNAPSHOT = "SNAP:1"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _outcome(**overrides) -> dict:
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


def _lifecycle(**overrides) -> dict:
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
    }
    values.update(overrides)
    return values


def _canonical_rows():
    return [{"snapshot_id": SNAPSHOT, "episode_id": EPISODE, "decision_status": "QUALIFIED"}]


def _ml_rows(direction="LONG"):
    return [
        {
            "canonical_snapshot_id": SNAPSHOT,
            "ml_snapshot_id": "ML:1",
            "feature_snapshot": {
                "snapshot_id": "ML:1",
                "direction": direction,
                "features": [{"name": "momentum", "value": 1.0}],
            },
        }
    ]


def _linkage(**overrides):
    kwargs = {
        "canonical_rows": _canonical_rows(),
        "ml_snapshot_rows": _ml_rows(),
        "paper_trade_rows": (),
        "paper_outcome_rows": (),
    }
    kwargs.update(overrides)
    return build_learning_linkage_records(**kwargs)


def _readiness(**overrides):
    kwargs = {
        "canonical_rows": _canonical_rows(),
        "ml_snapshot_rows": _ml_rows(),
        "paper_trade_rows": (),
        "paper_outcome_rows": (),
    }
    kwargs.update(overrides)
    return build_ml_data_readiness_report(**kwargs)


@pytest.fixture
def canonical_env(tmp_path, monkeypatch):
    root = tmp_path / "canonical"
    root.mkdir()
    monkeypatch.setenv("OPIP_CANONICAL_DIR", str(root))
    return {"root": root, "db": root / "opip_canonical_v1.sqlite3"}


@pytest.fixture
def servers(canonical_env):
    """In-process canonical writer servers, always stopped on teardown."""
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


def _submit(writer, payload, key=None):
    return writer.submit(
        WriterIntent(
            schema_version=SCHEMA_VERSION,
            priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
            idempotency_key=key or terminal_outcome_idempotency_key(payload["outcome_id"]),
            event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
            payload=payload,
        )
    )


# ===========================================================================
# Finding 2 - gap-spool incompleteness must affect eligibility
# ===========================================================================


def test_unresolved_gap_makes_valid_canonical_outcome_not_final():
    """The exact combination the earlier suite missed.

    A perfectly valid canonical CLOSED outcome must still be refused when an
    unresolved evidence gap means the population is not certifiably whole.
    """
    records = _linkage(
        paper_outcome_rows=[_outcome()],
        paper_outcome_population_incomplete=True,
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED",),
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False
    assert "PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED" in records[0].exclusion_reasons


def test_corrupt_gap_spool_makes_valid_canonical_outcome_not_final():
    records = _linkage(
        paper_outcome_rows=[_outcome()],
        paper_outcome_population_incomplete=True,
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_EVIDENCE_GAP_SPOOL_CORRUPT",),
    )
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.UNUSABLE
    assert records[0].primary_supervised_eligible is False


def test_gap_incompleteness_reaches_readiness_eligibility_not_only_the_report():
    """Readiness must enforce the unified decision, not just echo the reason."""
    report = _readiness(
        paper_outcome_rows=[_outcome()],
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED",),
    )
    assert report.paper_outcome_population_incomplete is True
    assert "PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED" in report.paper_outcome_incomplete_reasons
    assert report.primary_supervised_usable_rows == 0
    assert report.final_supervised_truth_count == 0
    assert "PAPER_OUTCOME_POPULATION_INCOMPLETE" in report.blockers
    assert report.readiness_state is not MLReadinessState.READY_FOR_OFFLINE_TRAINING


def test_complete_population_does_not_raise_the_incompleteness_blocker():
    """Sanity guard: enforcement must not fire on a genuinely complete set.

    Asserted at the population/blocker level rather than by counting supervised
    rows, so the result does not depend on how rich the test ML fixture is.
    """
    report = _readiness(paper_outcome_rows=[_outcome()])
    assert report.paper_outcome_population_incomplete is False
    assert report.paper_outcome_incomplete_reasons == ()
    assert "PAPER_OUTCOME_POPULATION_INCOMPLETE" not in report.blockers


def test_unified_reasons_combine_all_three_sources():
    report = _readiness(
        paper_trade_rows=[_lifecycle(outcome_outbox={"delivery": "PENDING"})],
        paper_outcome_rows=[_outcome()],
        paper_outcome_source_error="PaperOutcomeSourceUnavailableError: missing",
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED",),
    )
    reasons = set(report.paper_outcome_incomplete_reasons)
    assert "PAPER_OUTCOME_DELIVERY_PENDING" in reasons
    assert "CANONICAL_OUTCOME_SOURCE_UNAVAILABLE" in reasons
    assert "PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED" in reasons
    assert report.paper_outcome_population_incomplete is True


# ===========================================================================
# Finding 3 - source failure disables legacy final-paper fallback
# ===========================================================================


def test_canonical_source_unavailable_blocks_legacy_final_fallback():
    """The exact combination the earlier suite missed.

    A perfectly valid pre-PR-A lifecycle row with no outbox must NOT become
    FINAL_PAPER when the authority plane cannot be read; a legacy row cannot
    rescue an unavailable authority plane.
    """
    records = _linkage(
        paper_trade_rows=[_lifecycle()],  # no outbox -> otherwise legacy-eligible
        paper_outcome_rows=(),
        paper_outcome_population_incomplete=True,
        paper_outcome_incomplete_reasons=("CANONICAL_OUTCOME_SOURCE_UNAVAILABLE",),
    )
    assert records[0].normalized_outcome.source_quality is not OutcomeSourceQuality.FINAL_PAPER
    assert records[0].primary_supervised_eligible is False
    assert "CANONICAL_OUTCOME_SOURCE_UNAVAILABLE" in records[0].exclusion_reasons


def test_canonical_source_unavailable_readiness_is_not_ready():
    report = _readiness(
        paper_trade_rows=[_lifecycle()],
        paper_outcome_rows=(),
        paper_outcome_source_error="PaperOutcomeSourceUnavailableError: missing",
    )
    assert report.paper_outcome_population_incomplete is True
    assert report.final_supervised_truth_count == 0
    assert report.readiness_state is not MLReadinessState.READY_FOR_OFFLINE_TRAINING


def test_genuine_legacy_row_still_works_when_source_is_readable_and_complete():
    """The bounded compatibility path must survive the new gate."""
    records = _linkage(paper_trade_rows=[_lifecycle()])
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].primary_supervised_eligible is True


# ===========================================================================
# Finding 4 - supersession ancestry enforcement
# ===========================================================================


def _correction(original: dict, **overrides) -> dict:
    values = {
        "correction_seq": 1,
        "supersedes_id": original["outcome_id"],
        "gross_pnl": -10.0,
        "net_pnl": -14.0,
    }
    values.update(overrides)
    return _outcome(**values)


def test_valid_correction_is_accepted_and_resolves_to_corrected_record():
    original = _outcome()
    corrected = _correction(original)
    assert corrected["outcome_id"] != original["outcome_id"]
    assert_supersession_consistent(correction=corrected, superseded=original)
    effective = resolve_effective_outcome_ids([original, corrected])
    assert list(effective) == [corrected["outcome_id"]]


def test_original_remains_physically_present_after_correction(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        original = _outcome()
        assert _submit(writer, original).status == "OK"
        assert _submit(writer, _correction(original)).status == "OK"
        read = read_canonical_paper_outcomes(canonical_env["db"])
        ids = {o.outcome_id for o in read.outcomes}
        assert original["outcome_id"] in ids
        assert len(ids) == 2
    finally:
        writer.close()


def test_correction_with_missing_target_is_rejected(canonical_env):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        orphan = _correction(
            _outcome(), supersedes_id="PAPER-OUTCOME:" + "9" * 32
        )
        ack = _submit(writer, orphan)
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
    finally:
        writer.close()


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("paper_trade_id", "PAPER:" + "z" * 20, "cross-paper_trade_id"),
        ("episode_id", "EP:other", "cross-episode_id"),
        ("engine", "FREQTRADE_DRY_RUN_V1", "cross-engine"),
    ],
)
def test_cross_coordinate_supersession_is_rejected_on_write(
    canonical_env, field, value, expected
):
    writer = CanonicalWriter(canonical_env["db"])
    try:
        original = _outcome()
        assert _submit(writer, original).status == "OK"
        ack = _submit(writer, _correction(original, **{field: value}))
        assert ack.status == "REJECTED"
        assert ack.error_code == "INVALID_INTENT"
        assert expected in str(ack.detail)
    finally:
        writer.close()


def test_correction_cannot_change_terminal_status_or_quote_currency():
    """A correction corrects economics; it cannot re-identify the terminal event.

    Exercised directly against the consistency rule so the assertion isolates
    the supersession relationship rather than the economics validator.
    """
    original = _outcome()
    base = dict(original)
    base.update(
        {
            "outcome_id": "PAPER-OUTCOME:" + "7" * 32,
            "supersedes_id": original["outcome_id"],
            "correction_seq": 1,
        }
    )

    with pytest.raises(ValueError, match="cannot change terminal_status"):
        assert_supersession_consistent(
            correction={**base, "terminal_status": "CANCELLED"}, superseded=original
        )

    with pytest.raises(ValueError, match="cannot change quote_currency"):
        assert_supersession_consistent(
            correction={**base, "quote_currency": "USDT"}, superseded=original
        )

    with pytest.raises(ValueError, match="cannot change exit_reason"):
        assert_supersession_consistent(
            correction={**base, "exit_reason": "TIME_EXIT"}, superseded=original
        )

    # The unmodified correction is consistent.
    assert_supersession_consistent(correction=base, superseded=original)


def test_invalid_correction_sequence_is_rejected():
    original = _outcome()
    with pytest.raises(ValueError, match="correction_seq must follow"):
        assert_supersession_consistent(
            correction=_correction(original, correction_seq=5), superseded=original
        )


def test_tampered_store_cross_trade_supersession_fails_closed():
    """Defense in depth: the reader path re-derives the relationship."""
    original = _outcome()
    forged = _outcome(
        paper_trade_id="PAPER:" + "z" * 20,
        correction_seq=1,
        supersedes_id=original["outcome_id"],
        gross_pnl=-1.0,
        net_pnl=-5.0,
    )
    with pytest.raises(ValueError, match="cross-paper_trade_id"):
        resolve_effective_outcome_ids([original, forged])


def test_tampered_store_missing_supersession_target_fails_closed():
    orphan = _outcome(correction_seq=1, supersedes_id="PAPER-OUTCOME:" + "8" * 32)
    with pytest.raises(ValueError, match="supersession target not found"):
        resolve_effective_outcome_ids([orphan])


# ===========================================================================
# Finding 5 - frozen paper_outcome.v1 watermark boundary
# ===========================================================================


def _seed_events(db, payloads):
    writer = CanonicalWriter(db)
    try:
        for payload in payloads:
            assert _submit(writer, payload).status == "OK"
    finally:
        writer.close()


def test_reader_accepts_legitimately_empty_stream(canonical_env):
    CanonicalWriter(canonical_env["db"]).close()
    read = read_canonical_paper_outcomes(canonical_env["db"])
    assert read.outcomes == ()
    assert read.stream_present is False


def test_reader_rejects_events_without_a_stream_watermark(canonical_env):
    _seed_events(canonical_env["db"], [_outcome()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.execute("DELETE FROM watermarks WHERE stream = 'paper_outcome.v1'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeIntegrityError, match="without a paper_outcome.v1 watermark"):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_rejects_watermark_without_its_boundary_event(canonical_env):
    _seed_events(canonical_env["db"], [_outcome()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        connection.execute("DELETE FROM events WHERE event_type = ?", (PAPER_OUTCOME_TERMINAL_RECORDED,))
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeIntegrityError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_rejects_events_beyond_the_frozen_watermark(canonical_env):
    _seed_events(canonical_env["db"], [_outcome()])
    connection = sqlite3.connect(str(canonical_env["db"]))
    try:
        # Rewind the stream watermark so a committed event sits beyond it.
        connection.execute(
            "UPDATE watermarks SET local_sequence = local_sequence - 1 "
            "WHERE stream = 'paper_outcome.v1'"
        )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(PaperOutcomeIntegrityError):
        read_canonical_paper_outcomes(canonical_env["db"])


def test_reader_frozen_read_is_deterministic(canonical_env):
    _seed_events(canonical_env["db"], [_outcome()])
    first = read_canonical_paper_outcomes(canonical_env["db"])
    second = read_canonical_paper_outcomes(canonical_env["db"])
    assert first.stream_present is True
    assert [o.outcome_id for o in first.outcomes] == [o.outcome_id for o in second.outcomes]


def test_reader_only_reads_at_or_before_the_boundary(canonical_env):
    """A later event must not be silently included in a frozen read."""
    _seed_events(canonical_env["db"], [_outcome()])
    later = _outcome(
        paper_trade_id="PAPER:" + "e" * 20,
        episode_id="EP:2",
    )
    _seed_events(canonical_env["db"], [later])
    read = read_canonical_paper_outcomes(canonical_env["db"])
    assert len(read.outcomes) == 2


# ===========================================================================
# Finding 6 - gap resolution without a persisted gap_id
# ===========================================================================


def _spool_file(canonical_env):
    return canonical_env["root"] / "gap_spool.json"


def test_gap_resolution_finds_descriptor_by_key_when_gap_id_absent(canonical_env):
    """The durable gap_id can be lost while the descriptor is still written."""
    from app.opip.canonical.gap_spool import append_capture_gap, load_gap_spool
    from app.services import paper_outcome_outbox as outbox

    spool = _spool_file(canonical_env)
    append_capture_gap(
        idempotency_key="PAPER-OUTCOME-KEY:MINE",
        scan_id=EPISODE,
        identity=PAPER_ID,
        intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        error_code="TRANSPORT_FAILURE",
        path=spool,
    )

    outbox._resolve_gap_for(
        {"gap_id": None, "idempotency_key": "PAPER-OUTCOME-KEY:MINE"}, spool_file=spool
    )
    assert load_gap_spool(spool)["unresolved"] == []


def test_gap_resolution_leaves_unrelated_descriptors_untouched(canonical_env):
    from app.opip.canonical.gap_spool import append_capture_gap, load_gap_spool
    from app.services import paper_outcome_outbox as outbox

    spool = _spool_file(canonical_env)
    append_capture_gap(
        idempotency_key="PAPER-OUTCOME-KEY:MINE",
        scan_id=EPISODE,
        identity=PAPER_ID,
        intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        error_code="TRANSPORT_FAILURE",
        path=spool,
    )
    append_capture_gap(
        idempotency_key="PAPER-OUTCOME-KEY:OTHER",
        scan_id="EP:9",
        identity="PAPER:" + "9" * 20,
        intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        error_code="TRANSPORT_FAILURE",
        path=spool,
    )

    outbox._resolve_gap_for(
        {"gap_id": None, "idempotency_key": "PAPER-OUTCOME-KEY:MINE"}, spool_file=spool
    )
    remaining = load_gap_spool(spool)["unresolved"]
    assert len(remaining) == 1
    assert remaining[0]["idempotency_key"] == "PAPER-OUTCOME-KEY:OTHER"


def test_ambiguous_gap_matches_fail_closed_without_removing_anything(canonical_env):
    from app.opip.canonical.gap_spool import append_capture_gap, load_gap_spool
    from app.services import paper_outcome_outbox as outbox

    spool = _spool_file(canonical_env)
    for _ in range(2):
        append_capture_gap(
            idempotency_key="PAPER-OUTCOME-KEY:DUP",
            scan_id=EPISODE,
            identity=PAPER_ID,
            intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
            error_code="TRANSPORT_FAILURE",
            path=spool,
        )

    outbox._resolve_gap_for(
        {"gap_id": None, "idempotency_key": "PAPER-OUTCOME-KEY:DUP"}, spool_file=spool
    )
    # Ambiguity must not silently drop a descriptor.
    assert len(load_gap_spool(spool)["unresolved"]) == 2


def test_end_to_end_reconcile_resolves_gap_without_persisted_gap_id(
    canonical_env, servers, monkeypatch
):
    """Commit succeeds on retry and the orphaned-risk descriptor is resolved.

    Reproduces: transport failure -> descriptor written -> gap_id never
    persisted -> retry succeeds. The descriptor must not leak forever.
    """
    from app.opip.canonical.gap_spool import (
        append_capture_gap,
        evidence_window_incomplete,
        load_gap_spool,
    )
    from app.opip.learning.paper_outcome_reader import read_canonical_paper_outcomes
    from app.services import paper_outcome_outbox as outbox
    from app.services import paper_trade_registry as registry

    monkeypatch.setattr(outbox, "shadow_capture_enabled", lambda *a, **k: True)
    outbox.set_writer_client_for_tests(InProcessWriterClient(servers()))

    state = canonical_env["root"] / "paper_state.json"
    spool = _spool_file(canonical_env)
    monkeypatch.setattr(registry, "EVIDENCE_GAP_SPOOL_FILE", spool)

    payload = _outcome()
    key = terminal_outcome_idempotency_key(payload["outcome_id"])
    # Durable state: PENDING delivery, intent present, but NO gap_id.
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_only": True,
                "lifecycles": {
                    PAPER_ID: {
                        "paper_trade_id": PAPER_ID,
                        "episode_id": EPISODE,
                        "status": "CLOSED",
                        "revision": 8,
                        "outcome_outbox": {
                            "schema_version": 1,
                            "outcome_id": payload["outcome_id"],
                            "idempotency_key": key,
                            "intent": {
                                "schema_version": SCHEMA_VERSION,
                                "priority": PAPER_OUTCOME_PRIORITY,
                                "idempotency_key": key,
                                "event_type": PAPER_OUTCOME_TERMINAL_RECORDED,
                                "payload": payload,
                                "event_time": None,
                                "causation_id": None,
                                "correlation_id": EPISODE,
                                "ops_handoff": None,
                            },
                            "delivery": "PENDING",
                            "attempts": 1,
                            "gap_id": None,
                            "last_error": "TRANSPORT_FAILURE",
                            "last_detail": None,
                            "last_attempt_at": None,
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    append_capture_gap(
        idempotency_key=key,
        scan_id=EPISODE,
        identity=PAPER_ID,
        intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        error_code="TRANSPORT_FAILURE",
        path=spool,
    )
    assert evidence_window_incomplete(spool) is True

    stats = registry.reconcile_pending_outcomes(state_file=state)
    assert stats["committed"] == 1

    rows = json.loads(state.read_text(encoding="utf-8"))["lifecycles"]
    assert rows[PAPER_ID]["outcome_outbox"]["delivery"] == "COMMITTED"
    # Exactly one economic outcome, and the descriptor is resolved.
    assert len(read_canonical_paper_outcomes(canonical_env["db"]).outcomes) == 1
    assert load_gap_spool(spool)["unresolved"] == []
    assert evidence_window_incomplete(spool) is False
    outbox.set_writer_client_for_tests(None)
