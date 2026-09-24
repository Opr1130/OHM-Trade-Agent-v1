"""Canonical-replica -> Committee case production is provenance-first and fail-closed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.opip.committee import case_producer
from app.opip.committee.case_producer import (
    CanonicalDecisionSnapshotRecord,
    CaseSourceError,
    build_case_envelope,
    produce_case_population,
)
from app.opip.committee.outbound import screen_model_bound_view
from app.opip.contracts.episode_snapshot import (
    CANONICAL_EPISODE_AUTHORITY_FLAGS,
    CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE,
    CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION,
    CANONICAL_EPISODE_SOURCE_EXCHANGE,
    canonical_episode_id,
    canonical_snapshot_id,
)
from app.opip.contracts.paper_execution import (
    ENGINE_OPIP_PAPER_V2,
    PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
)
from app.opip.contracts.serialization import episode_snapshot_hash
from app.opip.decision_intelligence.identity import DecisionContextV2, Provenance

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
SOURCE_SHA = "a" * 40
SNAPSHOT_EVENT = "EVT:decision-snapshot-1"


def _inner_snapshot() -> dict:
    cohort = "COHORT:test"
    symbol = "BTCUSD"
    episode = canonical_episode_id(
        schema_version=CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION,
        cohort_id=cohort,
        symbol=symbol,
    )
    snapshot = canonical_snapshot_id(
        schema_version=CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION,
        episode_id=episode,
    )
    row = {
        "record_type": CANONICAL_EPISODE_SNAPSHOT_RECORD_TYPE,
        "schema_version": CANONICAL_EPISODE_SNAPSHOT_SCHEMA_VERSION,
        "snapshot_id": snapshot,
        "episode_id": episode,
        "cohort_id": cohort,
        "cohort_position": 1,
        "cohort_size": 3,
        "decision_at_utc": NOW.isoformat(),
        "symbol": symbol,
        "base_asset": "BTC",
        "kraken_public_symbol": "XBTUSD",
        "reference_price": 65000.25,
        "last_price": 65001.5,
        "volume_24h": 1234.5,
        "liquidity_24h_usd_approx": 1000000.0,
        "high_24h": 66000.0,
        "low_24h": 63000.0,
        "lift_from_24h_low_pct": 3.1,
        "distance_from_24h_high_pct": 1.5,
        "ml_feature_seed": {},
        "signal_quality_enabled": True,
        "decision_status": "SCORED_ELIGIBLE",
        "candidate_rank": 1,
        "signal_quality_universe_size": 3,
        "stage": "QUALIFIED",
        "pattern": "BREAKOUT",
        "opportunity_score": 81.2,
        "explosion_potential_score": 72.0,
        "tradeability_score": 78.0,
        "pattern_strength_score": 75.0,
        "volume_acceleration_score": 70.0,
        "relative_strength_score": 84.0,
        "persistence_scans": 3,
        "exhaustion_penalty": 0.0,
        "exhaustion_band": "NORMAL",
        "relative_strength_percentile": 91.0,
        "suppressed": False,
        "reasons": [],
        "components": {},
        "source_exchange": CANONICAL_EPISODE_SOURCE_EXCHANGE,
        "scan_source": "LIVE_OPPORTUNITY_SCAN",
        **dict(CANONICAL_EPISODE_AUTHORITY_FLAGS),
    }
    return row


def _source() -> CanonicalDecisionSnapshotRecord:
    inner = _inner_snapshot()
    return CanonicalDecisionSnapshotRecord(
        event_id=SNAPSHOT_EVENT,
        recorded_at=NOW + timedelta(seconds=2),
        payload={
            "schema_version": PAPER_EXECUTION_CONTRACT_SCHEMA_VERSION,
            "engine": ENGINE_OPIP_PAPER_V2,
            "snapshot_id": inner["snapshot_id"],
            "episode_id": inner["episode_id"],
            "cohort_id": inner["cohort_id"],
            "snapshot_hash": episode_snapshot_hash(inner),
            "snapshot_payload": inner,
        },
    )


def _context(*, evaluation_time: datetime = NOW) -> DecisionContextV2:
    source = _source()
    return DecisionContextV2(
        context_id="DI-CONTEXT-V2:test-1",
        candidate_id="OPIPC:test-1",
        episode_id=source.payload["episode_id"],
        instrument_version="INSTR:kraken:BTC:USD:1",
        snapshot_id=source.payload["snapshot_id"],
        snapshot_hash=source.payload["snapshot_hash"],
        evaluation_time=evaluation_time,
        evidence_cutoff=evaluation_time,
        policy_version="gate-v1",
        policy_fingerprint="GPF:test",
        environment="paper",
        eligibility=True,
        provenance=Provenance(
            producing_component="test",
            artifact_or_build_id=SOURCE_SHA,
            process_instance_id="producer-test",
            emitted_at=evaluation_time,
            source_record_refs=("EVT:instrument-1", SNAPSHOT_EVENT),
        ),
    )


def test_canonical_case_is_deterministic_and_model_bound_view_is_screenable():
    first = build_case_envelope(
        context=_context(), source=_source(), source_release_sha=SOURCE_SHA
    )
    second = build_case_envelope(
        context=_context(), source=_source(), source_release_sha=SOURCE_SHA
    )
    assert first.case.case_hash == second.case.case_hash
    assert first.case.snapshot.snapshot_hash == second.case.snapshot.snapshot_hash
    assert first.scheduler_item.evidence_snapshot_hash == first.case.snapshot.snapshot_hash
    view = screen_model_bound_view(first.case.snapshot.model_bound_view())
    assert view["case_id"] == first.case.case_id
    assert view["evidence"]
    assert all(
        not isinstance(item.get("metric_value"), float)
        for item in view["evidence"]
    )


def test_only_allowlisted_pre_outcome_fields_enter_model_evidence():
    envelope = build_case_envelope(
        context=_context(), source=_source(), source_release_sha=SOURCE_SHA
    )
    names = {item.payload["metric_name"] for item in envelope.case.snapshot.items}
    assert "snapshot.last_price" in names
    assert "snapshot.decision_status" in names
    assert "context.policy_fingerprint" in names
    assert all("components" not in name for name in names)
    assert all("reasons" not in name for name in names)
    assert all("outcome" not in name.lower() for name in names)


def test_source_snapshot_must_be_cited_by_context_provenance():
    context = _context()
    bad = DecisionContextV2(
        **{
            **context.as_dict(),
            "provenance": Provenance(
                producing_component="test",
                artifact_or_build_id=SOURCE_SHA,
                process_instance_id="bad",
                emitted_at=NOW,
                source_record_refs=("EVT:instrument-1",),
            ),
        }
    )
    with pytest.raises(CaseSourceError, match="not cited"):
        build_case_envelope(
            context=bad, source=_source(), source_release_sha=SOURCE_SHA
        )


def test_snapshot_identity_mismatch_fails_closed():
    context = _context()
    other = _source()
    object.__setattr__(
        other,
        "payload",
        {**other.payload, "snapshot_hash": "PSNAP:wrong"},
    )
    with pytest.raises(CaseSourceError, match="does not match"):
        build_case_envelope(
            context=context, source=other, source_release_sha=SOURCE_SHA
        )


def test_source_release_sha_is_exact_and_lowercase():
    with pytest.raises(CaseSourceError, match="40 lowercase hex"):
        build_case_envelope(
            context=_context(), source=_source(), source_release_sha="main"
        )


@dataclass
class _FakeBundle:
    canonical_db_path: Path


class _FakeDI:
    def __init__(self, contexts):
        self.contexts_v2 = {item.context_id: item for item in contexts}
        self.is_complete = True
        self.anomaly_codes = ()

    def superseded_by(self, record_id: str):
        return None


def test_population_uses_explicit_activation_boundary_and_no_backfill(
    tmp_path, monkeypatch
):
    old = _context(evaluation_time=NOW - timedelta(hours=1))
    fresh = _context(evaluation_time=NOW)
    # Give the fresh context a distinct identity while keeping the same source
    # snapshot lineage for this selection-only test.
    object.__setattr__(fresh, "context_id", "DI-CONTEXT-V2:fresh")

    monkeypatch.setattr(
        case_producer, "resolve_current_generation", lambda root: tmp_path / "gen"
    )
    monkeypatch.setattr(
        case_producer,
        "resolve_verified_replica_bundle",
        lambda **kwargs: _FakeBundle(tmp_path / "canonical.sqlite3"),
    )
    monkeypatch.setattr(
        case_producer,
        "read_di_evidence_snapshot",
        lambda path: _FakeDI((old, fresh)),
    )
    monkeypatch.setattr(
        case_producer,
        "_read_decision_snapshots",
        lambda path: {SNAPSHOT_EVENT: _source()},
    )

    population = produce_case_population(
        replica_repository_root=tmp_path,
        expected_source_release_sha=SOURCE_SHA,
        not_before=NOW - timedelta(minutes=1),
        now=NOW,
    )
    assert len(population.envelopes) == 1
    assert population.envelopes[0].evidence_id == "DI-CONTEXT-V2:fresh"


def test_incomplete_di_replica_refuses_the_population(tmp_path, monkeypatch):
    di = _FakeDI((_context(),))
    di.is_complete = False
    di.anomaly_codes = ("DI_UNKNOWN_EVENT",)
    monkeypatch.setattr(
        case_producer, "resolve_current_generation", lambda root: tmp_path / "gen"
    )
    monkeypatch.setattr(
        case_producer,
        "resolve_verified_replica_bundle",
        lambda **kwargs: _FakeBundle(tmp_path / "canonical.sqlite3"),
    )
    monkeypatch.setattr(
        case_producer, "read_di_evidence_snapshot", lambda path: di
    )
    with pytest.raises(CaseSourceError, match="contains anomalies"):
        produce_case_population(
            replica_repository_root=tmp_path,
            expected_source_release_sha=SOURCE_SHA,
            not_before=NOW,
            now=NOW,
        )


def test_future_activation_boundary_is_refused(tmp_path):
    with pytest.raises((CaseSourceError, ValueError), match="future"):
        produce_case_population(
            replica_repository_root=tmp_path,
            expected_source_release_sha=SOURCE_SHA,
            not_before=NOW + timedelta(seconds=1),
            now=NOW,
        )
