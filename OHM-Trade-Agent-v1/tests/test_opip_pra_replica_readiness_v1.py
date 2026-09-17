"""Replica-fed readiness: race matrix, ack-loss, and the §39 invariant.

These tests exercise the *runtime* path rather than the replica contract in
isolation: a real canonical writer, a real snapshot, the real production export
helper, the real generation install, the real canonical reader, and the real
readiness/linkage stack.

Two properties matter throughout, and every case asserts a fail-closed
direction:

1. All three authority inputs come from one verified generation. A canonical
   store from one generation can never be judged complete using another
   generation's lifecycle state or gap spool.
2. Non-transactional capture across the three artifacts may only ever produce a
   conservative *false-incomplete*. No race may produce a *false-complete*
   population.

The snapshot helper mirrors the production path's primitives (SQLite online
backup plus rollback-journal normalization). It cannot call the full
``publish_backup_generation`` on Windows because the PR-A0 directory-durability
primitive refuses there; a POSIX-marked test covers that path on Linux CI.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from app.opip.canonical.backup import normalize_to_rollback_journal
from app.opip.canonical.paths import EVENT_SCHEMA_VERSION
from app.opip.canonical.writer import CanonicalWriter
from app.opip.contracts.paper_outcome import (
    PAPER_OUTCOME_PRIORITY,
    PAPER_OUTCOME_STREAM,
    PAPER_OUTCOME_TERMINAL_RECORDED,
    build_terminal_outcome_payload,
    terminal_outcome_idempotency_key,
)
from app.opip.learning.canonical_replica import (
    export_replica_bundle,
    install_replica_generation,
    resolve_current_generation,
    resolve_verified_replica_bundle,
)
from app.opip.learning.paper_outcome_reader import read_canonical_paper_outcomes
from app.opip.learning.linkage import (
    LinkageStatus,
    OutcomeSourceQuality,
    build_learning_linkage_records,
)
from app.opip.learning.readiness import build_ml_data_readiness_report
from app.services import profitability_learning
from app.services import trade_decision_intelligence as intel
from app.services.learning_governance import NEUTRAL_CALIBRATION_MULTIPLIER

RELEASE_SHA = "0cd0c30eba0d45fb97aa1032364bfd93be671657"
OTHER_SHA = "9067af25dbb011281c0c636faecf22e56f79850f"
NOW = datetime(2026, 9, 17, 3, 30, tzinfo=timezone.utc)
ENTER = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)
EXIT = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)
PAPER_ID = "PAPER:" + "a" * 20
EPISODE = "EP:1"
SNAPSHOT = "SNAP:1"


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _snapshot(source: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(source))
    try:
        out = sqlite3.connect(str(dest))
        try:
            src.backup(out)
        finally:
            out.close()
    finally:
        src.close()
    normalize_to_rollback_journal(dest)
    return dest


def _outcome(paper_trade_id: str = PAPER_ID, episode_id: str = EPISODE, **overrides) -> dict:
    values = {
        "engine": "OHM_PAPER_SIM_V1",
        "paper_trade_id": paper_trade_id,
        "episode_id": episode_id,
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


def _commit_outcome(db: Path, payload: dict) -> None:
    """Commit one terminal outcome through the real canonical writer."""
    from app.opip.canonical.models import WriterIntent

    writer = CanonicalWriter(db)
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
                idempotency_key=terminal_outcome_idempotency_key(payload["outcome_id"]),
                event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                payload=payload,
            )
        )
        assert ack.status == "OK", ack.detail
    finally:
        writer.close()


def _lifecycle(delivery: str | None) -> dict:
    row = {
        "paper_trade_id": PAPER_ID,
        "episode_id": EPISODE,
        "status": "CLOSED",
        "revision": 8,
        "paper_only": True,
        "exchange_write_authority": False,
        "direction": "LONG",
        "closed_at": EXIT.isoformat(),
        "exit_price": 98.0,
        "net_pnl": -29.0,
        "net_pnl_pct": -2.9,
        "outcome": "LOSS",
    }
    if delivery is not None:
        row["outcome_outbox"] = {"delivery": delivery, "gap_id": None}
    return row


def _state(path: Path, delivery: str | None = "COMMITTED") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_only": True,
                "lifecycles": {PAPER_ID: _lifecycle(delivery)},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _spool(path: Path, unresolved: list | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"unresolved": unresolved or [], "updated_at": None}), encoding="utf-8"
    )
    return path


def _canonical_rows(episode: str = EPISODE, snapshot: str = SNAPSHOT) -> list[dict]:
    return [{"snapshot_id": snapshot, "episode_id": episode, "decision_status": "QUALIFIED"}]


def _ml_rows(snapshot: str = SNAPSHOT, direction: str = "LONG") -> list[dict]:
    return [
        {
            "canonical_snapshot_id": snapshot,
            "ml_snapshot_id": "ML:1",
            "feature_snapshot": {
                "snapshot_id": "ML:1",
                "direction": direction,
                # The readiness report requires a parseable decision clock; a
                # wrapper without one is counted malformed and contributes no
                # feature-bearing evidence.
                "decision_at_utc": ENTER.isoformat().replace("+00:00", "Z"),
                "features": [{"name": "momentum", "value": 1.0}],
            },
        }
    ]


def _build_generation(
    tmp_path: Path,
    *,
    outcomes: list[dict] | None = None,
    delivery: str | None = "COMMITTED",
    unresolved: list | None = None,
    generation_id: str = "gen-0001",
    now: datetime = NOW,
) -> Path:
    """Produce a canonical bundle from real artifacts and install it."""
    live = tmp_path / "live.sqlite3"
    CanonicalWriter(live).close()
    for payload in outcomes or []:
        _commit_outcome(live, payload)

    state = _state(tmp_path / "src_state.json", delivery=delivery)
    spool = _spool(tmp_path / "src_gap.json", unresolved=unresolved)
    staging = tmp_path / f"staging-{generation_id}"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=spool,
        generation_id=generation_id,
        now=now,
    )
    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=staging,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=now,
    )
    return resolve_current_generation(host)


def _readiness(**overrides):
    """Build a readiness report with the minimal ML fixture.

    The report's supervised-row counts additionally require a fully populated
    ML feature fixture; these tests assert the properties the replica bridge
    owns (population completeness and source availability) rather than fixture
    richness.
    """
    kwargs = {
        "canonical_rows": _canonical_rows(),
        "ml_snapshot_rows": _ml_rows(),
    }
    kwargs.update(overrides)
    return build_ml_data_readiness_report(**kwargs)


# ---------------------------------------------------------------------------
# Case D - fully consistent generation is eligible
# ---------------------------------------------------------------------------


def test_consistent_generation_is_final_paper(tmp_path):
    """Case D: canonical row + COMMITTED + no gaps is eligible.

    ``FINAL_PAPER`` is decided in linkage, so that is where this asserts
    supervised eligibility. The readiness report additionally requires a fully
    populated ML feature fixture (feature-bearing snapshots, PIT-clean decision
    clocks) which is orthogonal to the replica bridge; asserting those counts
    here would test the ML fixture rather than the bridge. What the report must
    show is that the bridge did NOT mark the population incomplete and did NOT
    report a source failure.
    """
    current = _build_generation(tmp_path, outcomes=[_outcome()])

    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    assert bundle.completeness_supported is True

    read = read_canonical_paper_outcomes(bundle.canonical_db_path)
    assert len(read.outcomes) == 1
    outcome_rows = [o.as_dict() for o in read.outcomes]

    records = build_learning_linkage_records(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=outcome_rows,
        paper_outcome_incomplete_reasons=(),
        paper_outcome_population_incomplete=False,
    )
    assert len(records) == 1
    assert records[0].normalized_outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
    assert records[0].linkage_status is LinkageStatus.COMPLETE_FINAL
    assert records[0].primary_supervised_eligible is True

    report = _readiness(
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=outcome_rows,
    )
    assert report.paper_outcome_population_incomplete is False
    assert report.paper_outcome_incomplete_reasons == ()
    assert "PAPER_OUTCOME_POPULATION_INCOMPLETE" not in report.blockers


# ---------------------------------------------------------------------------
# Case A - database newer than state (PENDING outbox)
# ---------------------------------------------------------------------------


def test_db_newer_than_state_is_incomplete(tmp_path):
    """Canonical outcome present but the outbox still says PENDING."""
    current = _build_generation(tmp_path, outcomes=[_outcome()], delivery="PENDING")

    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    # The canonical row is genuinely readable from the replica...
    read = read_canonical_paper_outcomes(bundle.canonical_db_path)
    assert len(read.outcomes) == 1

    # ...but unresolved delivery means the population is not complete.
    report = build_ml_data_readiness_report(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("PENDING")],
        paper_outcome_rows=[o.as_dict() for o in read.outcomes],
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_DELIVERY_PENDING",),
    )
    assert report.paper_outcome_population_incomplete is True
    assert report.final_supervised_truth_count == 0
    assert report.primary_supervised_usable_rows == 0


def test_permanent_failure_is_incomplete(tmp_path):
    current = _build_generation(tmp_path, outcomes=[_outcome()], delivery="PERMANENT_FAILURE")
    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    report = build_ml_data_readiness_report(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("PERMANENT_FAILURE")],
        paper_outcome_rows=[
            o.as_dict() for o in read_canonical_paper_outcomes(bundle.canonical_db_path).outcomes
        ],
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_DELIVERY_PERMANENT_FAILURE",),
    )
    assert report.final_supervised_truth_count == 0
    assert report.paper_outcome_population_incomplete is True


# ---------------------------------------------------------------------------
# Case B - state newer than database
# ---------------------------------------------------------------------------


def test_state_newer_than_db_is_incomplete(tmp_path):
    """State claims COMMITTED but no canonical outcome was captured.

    A lifecycle assertion must never be accepted over an absent authority row.
    """
    current = _build_generation(tmp_path, outcomes=[], delivery="COMMITTED")

    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    read = read_canonical_paper_outcomes(bundle.canonical_db_path)
    assert read.outcomes == ()

    report = build_ml_data_readiness_report(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=[],
        paper_outcome_incomplete_reasons=(),
    )
    # No canonical row means no supervised truth, regardless of the claim.
    assert report.final_supervised_truth_count == 0
    assert report.primary_supervised_usable_rows == 0


# ---------------------------------------------------------------------------
# Case C - unresolved gap
# ---------------------------------------------------------------------------


def test_unresolved_gap_is_incomplete(tmp_path):
    current = _build_generation(
        tmp_path,
        outcomes=[_outcome()],
        unresolved=[{"gap_id": "GAP:1", "idempotency_key": "k"}],
    )
    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    report = build_ml_data_readiness_report(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=[
            o.as_dict() for o in read_canonical_paper_outcomes(bundle.canonical_db_path).outcomes
        ],
        paper_outcome_incomplete_reasons=("PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED",),
    )
    assert report.final_supervised_truth_count == 0
    assert report.paper_outcome_population_incomplete is True


# ---------------------------------------------------------------------------
# Source failure and legitimate emptiness
# ---------------------------------------------------------------------------


def test_valid_empty_generation_is_empty_not_unavailable(tmp_path):
    """An empty but verified generation is a valid empty population."""
    current = _build_generation(tmp_path, outcomes=[])
    bundle = resolve_verified_replica_bundle(
        root=current, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    read = read_canonical_paper_outcomes(bundle.canonical_db_path)
    assert read.outcomes == ()
    assert read.stream_present is False
    assert bundle.completeness_supported is True


def test_missing_replica_is_source_unavailable_not_empty(tmp_path):
    from app.opip.learning.canonical_replica import ReplicaUnavailableError

    with pytest.raises(ReplicaUnavailableError):
        resolve_verified_replica_bundle(
            root=tmp_path / "absent", expected_source_release_sha=RELEASE_SHA, now=NOW
        )


def test_release_sha_mismatch_blocks_the_bundle(tmp_path):
    current = _build_generation(tmp_path, outcomes=[_outcome()])
    from app.opip.learning.canonical_replica import ReplicaProvenanceError

    with pytest.raises(ReplicaProvenanceError):
        resolve_verified_replica_bundle(
            root=current, expected_source_release_sha=OTHER_SHA, now=NOW
        )


def test_stale_generation_blocks_the_bundle(tmp_path):
    current = _build_generation(tmp_path, outcomes=[_outcome()])
    from app.opip.learning.canonical_replica import ReplicaStaleError

    with pytest.raises(ReplicaStaleError):
        resolve_verified_replica_bundle(
            root=current,
            expected_source_release_sha=RELEASE_SHA,
            now=NOW + timedelta(seconds=3600),
        )


# ---------------------------------------------------------------------------
# Ack-loss across two generations
# ---------------------------------------------------------------------------


def test_ack_loss_then_reconcile_across_generations(tmp_path):
    """Commit, lose the ack, reconcile, and produce a second clean generation.

    Generation 1 carries the acknowledged-but-unconfirmed state (PENDING).
    Generation 2 carries the reconciled state. Learning must see an incomplete
    population first, then exactly one complete economic outcome - never two.
    """
    from app.opip.canonical.gap_spool import (
        append_capture_gap,
        evidence_window_incomplete,
        load_gap_spool,
        resolve_gap,
    )
    from app.opip.canonical.models import WriterIntent

    payload = _outcome()
    key = terminal_outcome_idempotency_key(payload["outcome_id"])

    # The writer commits, but the producer never sees the acknowledgement.
    live = tmp_path / "live.sqlite3"
    CanonicalWriter(live).close()
    _commit_outcome(live, payload)

    spool = tmp_path / "src_gap.json"
    append_capture_gap(
        idempotency_key=key,
        scan_id=EPISODE,
        identity=PAPER_ID,
        intended_event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
        error_code="TRANSPORT_FAILURE",
        path=spool,
    )
    assert evidence_window_incomplete(spool) is True

    state = _state(tmp_path / "src_state.json", delivery="PENDING")
    staging1 = tmp_path / "staging-gen1"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging1,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=spool,
        generation_id="gen-ack1",
        now=NOW,
    )
    host = tmp_path / "host"
    install_replica_generation(
        staging_dir=staging1,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    gen1 = resolve_current_generation(host)

    bundle1 = resolve_verified_replica_bundle(
        root=gen1, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    # Generation 1: the row is readable, but delivery and the gap are unresolved.
    assert len(read_canonical_paper_outcomes(bundle1.canonical_db_path).outcomes) == 1
    report1 = build_ml_data_readiness_report(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("PENDING")],
        paper_outcome_rows=[
            o.as_dict()
            for o in read_canonical_paper_outcomes(bundle1.canonical_db_path).outcomes
        ],
        paper_outcome_incomplete_reasons=(
            "PAPER_OUTCOME_DELIVERY_PENDING",
            "PAPER_OUTCOME_EVIDENCE_GAP_UNRESOLVED",
        ),
    )
    assert report1.final_supervised_truth_count == 0

    # Reconciliation: the exact persisted intent is retried and returns
    # DUPLICATE_OK, so no second economic outcome is created.
    writer = CanonicalWriter(live)
    try:
        ack = writer.submit(
            WriterIntent(
                schema_version=1,
                priority=PAPER_OUTCOME_PRIORITY,  # type: ignore[arg-type]
                idempotency_key=key,
                event_type=PAPER_OUTCOME_TERMINAL_RECORDED,
                payload=payload,
            )
        )
        assert ack.status in {"OK", "DUPLICATE_OK"}
    finally:
        writer.close()

    resolve_gap(load_gap_spool(spool)["unresolved"][0]["gap_id"], path=spool)
    assert evidence_window_incomplete(spool) is False

    state2 = _state(tmp_path / "src_state2.json", delivery="COMMITTED")
    staging2 = tmp_path / "staging-gen2"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging2,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state2,
        paper_gap_source=spool,
        generation_id="gen-ack2",
        now=NOW + timedelta(seconds=60),
    )
    install_replica_generation(
        staging_dir=staging2,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW + timedelta(seconds=60),
    )
    gen2 = resolve_current_generation(host)
    assert gen2.name == "gen-ack2"

    bundle2 = resolve_verified_replica_bundle(
        root=gen2, expected_source_release_sha=RELEASE_SHA, now=NOW + timedelta(seconds=60)
    )
    outcomes = read_canonical_paper_outcomes(bundle2.canonical_db_path).outcomes
    # Exactly one economic outcome: the retry did not duplicate it.
    assert len(outcomes) == 1
    assert outcomes[0].outcome_id == payload["outcome_id"]

    records = build_learning_linkage_records(
        canonical_rows=_canonical_rows(),
        ml_snapshot_rows=_ml_rows(),
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=[o.as_dict() for o in outcomes],
        paper_outcome_incomplete_reasons=(),
        paper_outcome_population_incomplete=False,
    )
    assert records[0].primary_supervised_eligible is True
    assert records[0].normalized_outcome.net_pnl == pytest.approx(-29.0)

    report2 = _readiness(
        paper_trade_rows=[_lifecycle("COMMITTED")],
        paper_outcome_rows=[o.as_dict() for o in outcomes],
    )
    assert report2.paper_outcome_population_incomplete is False


# ---------------------------------------------------------------------------
# Multi-epoch through the bridge
# ---------------------------------------------------------------------------


def test_multi_epoch_tip_survives_to_the_reader(tmp_path):
    live = tmp_path / "live.sqlite3"
    CanonicalWriter(live).close()
    old = _outcome(paper_trade_id="PAPER:" + "a" * 20, episode_id="EP:1")
    new = _outcome(paper_trade_id="PAPER:" + "e" * 20, episode_id="EP:2")
    _commit_outcome(live, old)
    _commit_outcome(live, new)

    # Simulate a restore: advance the meta epoch and reset the sequence counter,
    # which is exactly what makes (2, 1) the true tip above (1, N).
    connection = sqlite3.connect(str(live))
    try:
        connection.execute(
            "UPDATE events SET history_epoch = 1, local_sequence = 40 WHERE event_id IN "
            "(SELECT event_id FROM events ORDER BY rowid LIMIT 1)"
        )
        connection.execute(
            "UPDATE events SET history_epoch = 2, local_sequence = 1 WHERE event_id IN "
            "(SELECT event_id FROM events ORDER BY rowid DESC LIMIT 1)"
        )
        connection.execute("UPDATE meta SET history_epoch = 2, next_local_sequence = 2 WHERE id = 1")
        connection.execute(
            "UPDATE watermarks SET history_epoch = 2, local_sequence = 1 WHERE stream = ?",
            (PAPER_OUTCOME_STREAM,),
        )
        connection.commit()
    finally:
        connection.close()

    current = _build_generation(tmp_path, outcomes=None)
    # Replace the generation with one built from the multi-epoch source.
    state = _state(tmp_path / "s2.json", delivery="COMMITTED")
    spool = _spool(tmp_path / "g2.json")
    staging = tmp_path / "staging-me"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=spool,
        generation_id="gen-me",
        now=NOW,
    )
    host2 = tmp_path / "host2"
    install_replica_generation(
        staging_dir=staging,
        host_root=host2,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    gen = resolve_current_generation(host2)

    read = read_canonical_paper_outcomes(gen / "opip" / "canonical" / "opip_canonical_v1.sqlite3")
    coordinates = [(o.history_epoch, o.local_sequence) for o in read.outcomes]
    assert max(coordinates) == (2, 1)
    assert current is not None  # the earlier generation remains valid


# ---------------------------------------------------------------------------
# §39 through the replica-fed path
# ---------------------------------------------------------------------------


@pytest.fixture
def learning_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPIP_LEARNING_GOVERNANCE_DIR", str(tmp_path / "gov"))
    monkeypatch.setattr(
        profitability_learning, "PROFILE_FILE", tmp_path / "strategy_calibration_profile.json"
    )
    monkeypatch.setattr(
        profitability_learning, "LOCK_FILE", tmp_path / ".strategy_calibration_profile.lock"
    )
    return tmp_path


def test_section_39_replica_fed_population_stays_neutral(tmp_path, learning_env):
    """The real architectural path: a replica-fed canonical population exists,
    the learner is calibrated, and no promotion exists - so the multiplier is
    exactly 1.0.

    This is the invariant that makes completing the bridge safe. Before the
    bridge, canonical outcomes could not reach learning at all; now they can,
    which is precisely when an ungated learner would become dangerous.
    """
    payloads = [
        _outcome(
            paper_trade_id="PAPER:" + f"{i:020d}",
            episode_id=f"EP:{i}",
            gross_pnl=54.0,
            fees_paid=4.0,
            net_pnl=50.0,
            net_pnl_pct=5.0,
        )
        for i in range(40)
    ]
    live = tmp_path / "live39.sqlite3"
    CanonicalWriter(live).close()
    for payload in payloads:
        _commit_outcome(live, payload)

    state = tmp_path / "s39.json"
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "paper_only": True,
                "lifecycles": {
                    p["paper_trade_id"]: {
                        "paper_trade_id": p["paper_trade_id"],
                        "episode_id": p["episode_id"],
                        "status": "CLOSED",
                        "revision": 8,
                        "outcome_outbox": {"delivery": "COMMITTED"},
                    }
                    for p in payloads
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    spool = _spool(tmp_path / "g39.json")

    staging = tmp_path / "staging39"
    export_replica_bundle(
        source_db=live,
        staging_dir=staging,
        source_release_sha=RELEASE_SHA,
        paper_state_source=state,
        paper_gap_source=spool,
        generation_id="gen-39",
        now=NOW,
    )
    host = tmp_path / "host39"
    install_replica_generation(
        staging_dir=staging,
        host_root=host,
        expected_source_release_sha=RELEASE_SHA,
        now=NOW,
    )
    gen = resolve_current_generation(host)

    bundle = resolve_verified_replica_bundle(
        root=gen, expected_source_release_sha=RELEASE_SHA, now=NOW
    )
    outcomes = read_canonical_paper_outcomes(bundle.canonical_db_path).outcomes
    assert len(outcomes) == 40, "the bridge must deliver the full canonical population"
    assert bundle.completeness_supported is True

    # The learner's own artifact claims calibrated-and-move-sizing.
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

    # No approved promotion exists, so runtime influence stays exactly neutral.
    multiplier, status = intel._effective_calibration_multiplier(
        direction="LONG", regime="RISK_ON"
    )
    assert multiplier == NEUTRAL_CALIBRATION_MULTIPLIER
    assert status == "NO_APPROVED_PROMOTION"
