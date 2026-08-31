from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.events.contract import EventSeverity, EventType, MappingStatus
from app.opip.events.provider_health import ProviderHealthSnapshot, ProviderHealthState
from app.opip.events.storage import EventStore
from app.opip.risk import exposure_matcher
from app.opip.risk.config import EventRiskShieldConfig
from app.opip.risk.contract import Direction, ExposureFamily, ExposureState, ExposureView, RiskState
from app.opip.risk.observer import (
    replay_policy_from_t0,
    run_event_risk_shield,
    select_visible_events_with_coverage,
)
from app.opip.risk.storage import RiskAssessmentStore, T0AttributionStore

# Reuse canonical BUILD 3.2 fixture constructors; this keeps new hardening tests
# focused on the regression rather than duplicating OPipEvent construction.
from tests.test_opip_event_risk_shield_observer_v1 import NOW, _event, _exposure, _healthy, _identity


def _event_store(tmp_path: Path, *, keep_lines: int = 1000) -> EventStore:
    root = tmp_path / "events"
    return EventStore(
        event_file=root / "events.jsonl",
        archive_dir=root / "archive",
        dead_letter_file=root / "dead.jsonl",
        lock_file=root / ".events.lock",
        max_bytes=1024 * 1024,
        keep_lines=keep_lines,
    )


def _run(tmp_path: Path, store: EventStore, exposure: ExposureView | None = None, **kwargs):
    exposure = exposure or _exposure()
    params = dict(
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
        event_store=store,
        exposure_provider=lambda _at: (exposure,),
        health_resolver=lambda provider, at: _healthy(provider),
        storage_root=tmp_path / "risk",
    )
    params.update(kwargs)
    return run_event_risk_shield(**params)


def test_recent_event_compacted_to_warm_remains_visible(tmp_path):
    store = _event_store(tmp_path, keep_lines=1)
    first = _event(key="recent-archived", ingest=NOW - timedelta(minutes=30))
    second = _event(key="recent-hot", ingest=NOW - timedelta(minutes=5))
    store.append(first, persisted_at=NOW - timedelta(minutes=29))
    store.append(second, persisted_at=NOW - timedelta(minutes=4))
    selection = select_visible_events_with_coverage(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert {e.provider_event_id for e in selection.events} == {"recent-archived", "recent-hot"}
    assert selection.archive_segments_scanned == 1
    assert selection.coverage_complete is True


def test_archive_segment_ceiling_marks_coverage_incomplete(tmp_path):
    store = _event_store(tmp_path, keep_lines=1)
    for i in range(4):
        event = _event(key=f"e{i}", ingest=NOW - timedelta(minutes=30-i))
        store.append(event, persisted_at=NOW - timedelta(minutes=29-i))
    selection = select_visible_events_with_coverage(
        event_store=store,
        decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True, max_archive_segments=1),
    )
    assert selection.coverage_complete is False
    assert selection.archive_segments_truncated is True
    assert "ARCHIVE_SEGMENT_CEILING_REACHED" in selection.warnings


def test_missing_manifest_with_archives_is_incomplete_not_no_risk(tmp_path):
    store = _event_store(tmp_path, keep_lines=1)
    store.append(_event(key="a"), persisted_at=NOW - timedelta(minutes=8))
    store.append(_event(key="b", ingest=NOW - timedelta(minutes=5)), persisted_at=NOW - timedelta(minutes=4))
    store.archive_manifest_file.unlink()
    selection = select_visible_events_with_coverage(
        event_store=store, decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert selection.coverage_complete is False
    assert "ARCHIVE_MANIFEST_UNAVAILABLE" in selection.warnings


def test_source_event_age_not_recent_ingestion_controls_staleness(tmp_path):
    store = _event_store(tmp_path)
    recent_ingest = _event(key="late-news", ingest=NOW - timedelta(minutes=2))
    old_source = replace(recent_ingest, source_event_time_utc=NOW - timedelta(hours=30))
    store.append(old_source, persisted_at=NOW - timedelta(minutes=1))
    result = _run(tmp_path, store)
    assessment = result.assessments[0]
    assert assessment.event_age_seconds == 30 * 3600
    assert assessment.evidence_age_seconds is not None and assessment.evidence_age_seconds < 300
    assert assessment.risk_state is RiskState.WATCH
    assert "STALE_EVENT_ESCALATION_CAPPED" in assessment.warnings


def test_t0_self_heals_when_assessment_was_stored_first(tmp_path, monkeypatch):
    store = _event_store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    original = T0AttributionStore.append
    calls = {"n": 0}

    def fail_once(self, record):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated T0 disk failure")
        return original(self, record)

    monkeypatch.setattr(T0AttributionStore, "append", fail_once)
    first = _run(tmp_path, store)
    assert first.assessments_stored == 1
    assert first.t0_records_written == 0
    second = _run(tmp_path, store, decision_at=NOW + timedelta(minutes=1))
    assert second.assessments_stored == 0
    assert second.duplicates_suppressed == 1
    records = list(T0AttributionStore(family=ExposureFamily.REAL_ADVISORY, root=tmp_path / "risk").iter_records())
    assert len(records) == 1


def test_price_lookup_failure_does_not_lose_t0(tmp_path):
    store = _event_store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    result = _run(tmp_path, store, price_lookup=lambda symbol: (_ for _ in ()).throw(RuntimeError("down")))
    assert result.t0_records_written == 1
    assert any(w.startswith("T0_PRICE_LOOKUP_UNAVAILABLE") for w in result.warnings)


def test_replay_uses_only_t0_policy_and_exposure_snapshot(tmp_path, monkeypatch):
    store = _event_store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    result = _run(tmp_path, store)
    record = list(T0AttributionStore(family=ExposureFamily.REAL_ADVISORY, root=tmp_path / "risk").iter_records())[0]

    from app.services import active_trade_registry, pending_setup_registry
    from app.opip.events import provider_health
    monkeypatch.setattr(active_trade_registry, "get_active_trades", lambda: (_ for _ in ()).throw(AssertionError("live registry read")))
    monkeypatch.setattr(pending_setup_registry, "get_pending_setups", lambda: (_ for _ in ()).throw(AssertionError("live registry read")))
    monkeypatch.setattr(provider_health.ProviderHealthStore, "read", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live health read")))

    replayed = replay_policy_from_t0(record)
    assert replayed.risk_state is result.assessments[0].risk_state


def test_t0_store_rejects_cross_family_write(tmp_path):
    store = _event_store(tmp_path)
    store.append(_event(), persisted_at=NOW - timedelta(minutes=5))
    _run(tmp_path, store)
    record = list(T0AttributionStore(family=ExposureFamily.REAL_ADVISORY, root=tmp_path / "risk").iter_records())[0]
    with pytest.raises(ValueError):
        T0AttributionStore(family=ExposureFamily.PAPER, root=tmp_path / "risk").append(record)


def test_generic_macro_headline_does_not_become_macro_without_explicit_scope(tmp_path):
    store = _event_store(tmp_path)
    event = replace(
        _event(key="macro", identity=_identity(status=MappingStatus.UNKNOWN)),
        headline="Global crypto macro panic recession central bank",
    )
    store.append(event, persisted_at=NOW - timedelta(minutes=5))
    result = _run(tmp_path, store)
    assert result.assessments == ()


def test_config_is_dark_by_default(monkeypatch):
    monkeypatch.delenv("OPIP_EVENT_RISK_SHIELD_ENABLED", raising=False)
    assert EventRiskShieldConfig().enabled is False
    assert EventRiskShieldConfig.from_env().enabled is False


def test_all_four_family_state_cells_remain_distinct():
    cells = {
        (ExposureFamily.REAL_ADVISORY, ExposureState.ACTIVE),
        (ExposureFamily.REAL_ADVISORY, ExposureState.PENDING),
        (ExposureFamily.PAPER, ExposureState.ACTIVE),
        (ExposureFamily.PAPER, ExposureState.PENDING),
    }
    views = {
        (family, state): _exposure(
            exposure_id=f"{family.value}:{state.value}", family=family, state=state
        )
        for family, state in cells
    }
    assert set(views) == cells
    assert len({v.exposure_id for v in views.values()}) == 4


def test_active_real_requires_verified_exchange_truth(monkeypatch, tmp_path):
    from app.services import active_trade_registry
    trade = SimpleNamespace(
        trade_id="T1", symbol="SOLUSD", direction="LONG", status="active",
        entry_price=100.0, stop_price=90.0, opened_at=NOW.isoformat(),
    )
    monkeypatch.setattr(active_trade_registry, "get_active_trades", lambda: [trade])
    monkeypatch.setattr(exposure_matcher, "_resolve_canonical_identity", lambda *a, **k: ("solana", "Solana", "UNIQUE"))

    class FakeVerifier:
        def __init__(self, status): self.status = status
        def refresh(self): pass
        def verify(self, trade):
            return SimpleNamespace(status=self.status, reason=self.status, verified=self.status == "VERIFIED")

    verified = exposure_matcher._load_active_real(
        decision_at=NOW, identity_registry=tmp_path / "identity.json",
        verify_positions=True, verifier_factory=lambda: FakeVerifier("VERIFIED"),
    )
    assert len(verified[0]) == 1 and verified[1] is True
    absent = exposure_matcher._load_active_real(
        decision_at=NOW, identity_registry=tmp_path / "identity.json",
        verify_positions=True, verifier_factory=lambda: FakeVerifier("ABSENT"),
    )
    assert absent[0] == [] and absent[1] is True
    unavailable = exposure_matcher._load_active_real(
        decision_at=NOW, identity_registry=tmp_path / "identity.json",
        verify_positions=True, verifier_factory=lambda: FakeVerifier("UNAVAILABLE"),
    )
    assert unavailable[0] == [] and unavailable[1] is False


def test_malformed_direction_never_defaults_to_long(monkeypatch, tmp_path):
    from app.services import active_trade_registry
    trade = SimpleNamespace(
        trade_id="T1", symbol="SOLUSD", direction="SIDEWAYS", status="active",
        entry_price=100.0, stop_price=90.0, opened_at=NOW.isoformat(),
    )
    monkeypatch.setattr(active_trade_registry, "get_active_trades", lambda: [trade])
    monkeypatch.setattr(exposure_matcher, "_resolve_canonical_identity", lambda *a, **k: ("solana", "Solana", "UNIQUE"))
    rows, complete, warnings = exposure_matcher._load_active_real(
        decision_at=NOW, identity_registry=tmp_path / "identity.json",
        verify_positions=False, verifier_factory=None,
    )
    assert rows == []
    assert complete is False
    assert any(w.startswith("ACTIVE_REAL_INVALID") for w in warnings)

def test_corrupted_overlapping_archive_marks_coverage_incomplete(tmp_path):
    store = _event_store(tmp_path, keep_lines=1)
    store.append(_event(key="a"), persisted_at=NOW - timedelta(minutes=8))
    store.append(_event(key="b", ingest=NOW - timedelta(minutes=5)), persisted_at=NOW - timedelta(minutes=4))
    archive = next(store.archive_dir.glob("events-*.jsonl.gz"))
    archive.write_bytes(b"corrupt")
    selection = select_visible_events_with_coverage(
        event_store=store, decision_at=NOW,
        config=EventRiskShieldConfig(enabled=True),
    )
    assert selection.coverage_complete is False
    assert "ARCHIVE_SEGMENT_UNREADABLE" in selection.warnings
