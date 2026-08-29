from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import gzip
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.opip.events.adapters import (
    normalize_coinmarketcal_events,
    normalize_cryptopanic_posts,
)
from app.opip.events.contract import (
    EventClass,
    EventIdentity,
    MappingStatus,
    OPipEvent,
    stable_event_id,
    stable_payload_hash,
)
from app.opip.events.identity import (
    known_unique_assets,
    resolve_registry_identity,
)
from app.opip.events.observer import capture_external_event_intelligence
from app.opip.events.storage import EventStore
from app.services.asset_display_identity import learn_verified_identity


NOW = datetime(2026, 8, 29, 5, 0, tzinfo=timezone.utc)


def _identity_path(tmp_path: Path) -> Path:
    path = tmp_path / "asset_identity_registry.json"
    assert learn_verified_identity(
        base_asset="SOL",
        pair="SOLUSD",
        display_name="Solana",
        source="COINGECKO",
        source_id="solana",
        path=path,
        learned_at=NOW - timedelta(hours=2),
    )
    return path


def _canonical_event(
    *,
    event_key: str = "1",
    headline: str = "Solana update",
    ingest: datetime = NOW,
) -> OPipEvent:
    identity = EventIdentity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        canonical_asset_id="solana",
        canonical_asset_name="Solana",
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=1.0,
        identity_learned_at_utc=ingest - timedelta(hours=1),
        mapping_provenance="test",
    )
    dedupe = f"TEST:NEWS:{event_key}:solana"
    payload_hash = stable_payload_hash({"headline": headline})
    return OPipEvent(
        event_id=stable_event_id(dedupe, payload_hash),
        dedupe_key=dedupe,
        provider="TEST",
        provider_event_id=event_key,
        event_class=EventClass.NEWS,
        payload_hash=payload_hash,
        source_event_time_utc=ingest - timedelta(minutes=10),
        ingest_time_utc=ingest,
        normalized_at_utc=ingest + timedelta(seconds=1),
        identity=identity,
        headline=headline,
        expires_at_utc=ingest + timedelta(hours=24),
    )


def _store(tmp_path: Path, *, max_bytes: int = 1024 * 1024, keep_lines: int = 100):
    root = tmp_path / "events"
    return EventStore(
        event_file=root / "events.jsonl",
        archive_dir=root / "archive",
        dead_letter_file=root / "dead.jsonl",
        lock_file=root / ".events.lock",
        max_bytes=max_bytes,
        keep_lines=keep_lines,
    )


def _post(*, post_id=1, published=None, title="Solana update"):
    published = published or (NOW - timedelta(hours=1))
    return {
        "id": post_id,
        "title": title,
        "description": "Provider description",
        "published_at": published.isoformat(),
        "kind": "news",
        "source": {"title": "Provider", "domain": "example.com"},
        "instruments": [{
            "code": "SOL",
            "title": "Solana",
            "slug": "solana",
        }],
        "votes": {"positive": 4, "negative": 1},
    }


def _cmc_mapping_cache(tmp_path: Path) -> Path:
    path = tmp_path / "cmc.json"
    path.write_text(
        json.dumps({
            "mappings": {
                "SOL": {
                    "underlying_symbol": "SOL",
                    "coingecko_id": "solana",
                    "coingecko_name": "Solana",
                    "coinmarketcal_slug": "solana",
                    "coinmarketcal_name": "Solana",
                    "coinmarketcal_symbol": "SOL",
                    "resolved_at": (NOW - timedelta(days=1)).isoformat(),
                }
            }
        }),
        encoding="utf-8",
    )
    return path


def _cmc_event(event_id=9):
    return {
        "id": event_id,
        "title": "Protocol event",
        "date": (NOW + timedelta(days=1)).isoformat(),
        "dateEnd": None,
        "dateType": "exact",
        "isEstimated": False,
        "displayedDate": None,
        "categories": [{"name": "Release"}],
        "coins": [{"slug": "solana", "name": "Solana", "symbol": "SOL"}],
        "impact": 3,
        "impactSummary": "Scheduled protocol release",
    }


def test_event_contract_rejects_naive_time():
    with pytest.raises(ValueError, match="timezone-aware UTC"):
        replace(_canonical_event(), ingest_time_utc=datetime(2026, 8, 29, 5, 0))


def test_event_contract_rejects_normalization_before_ingest():
    event = _canonical_event()
    with pytest.raises(ValueError, match="cannot precede"):
        replace(
            event,
            normalized_at_utc=event.ingest_time_utc - timedelta(seconds=1),
        )


def test_event_store_rejects_persistence_before_normalization(tmp_path):
    store = _store(tmp_path)
    event = _canonical_event()
    with pytest.raises(ValueError, match="cannot precede"):
        store.append(
            event,
            persisted_at=event.normalized_at_utc - timedelta(microseconds=1),
        )


def test_event_id_is_deterministic_and_content_revision_changes_id():
    first = _canonical_event(headline="v1")
    same = _canonical_event(headline="v1")
    revised = _canonical_event(headline="v2")
    assert first.event_id == same.event_id
    assert first.dedupe_key == revised.dedupe_key
    assert first.event_id != revised.event_id


def test_identity_learned_before_event_is_eligible(tmp_path):
    path = _identity_path(tmp_path)
    identity = resolve_registry_identity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        as_of=NOW,
        path=path,
    )
    assert identity.mapping_status == MappingStatus.UNIQUE
    assert identity.canonical_asset_id == "solana"
    assert identity.identity_learned_at_utc == NOW - timedelta(hours=2)


def test_identity_learned_after_event_is_not_retroactive(tmp_path):
    path = tmp_path / "asset_identity_registry.json"
    assert learn_verified_identity(
        base_asset="SOL",
        pair="SOLUSD",
        display_name="Solana",
        source="COINGECKO",
        source_id="solana",
        path=path,
        learned_at=NOW + timedelta(hours=1),
    )
    identity = resolve_registry_identity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        as_of=NOW,
        path=path,
    )
    assert identity.mapping_status == MappingStatus.UNKNOWN
    assert identity.mapping_provenance.endswith("learned_after_event")


def test_legacy_identity_without_knowledge_time_is_not_used_historically(tmp_path):
    path = tmp_path / "asset_identity_registry.json"
    path.write_text(
        json.dumps({
            "assets": {
                "SOL": {
                    "display_name": "Solana",
                    "source": "COINGECKO",
                    "source_id": "solana",
                    "ambiguous": False,
                    "pairs": ["SOLUSD"],
                }
            }
        }),
        encoding="utf-8",
    )
    identity = resolve_registry_identity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        as_of=NOW,
        path=path,
    )
    assert identity.mapping_status == MappingStatus.UNKNOWN
    assert identity.mapping_provenance.endswith("timestamp_unavailable")
    assert known_unique_assets(as_of=NOW, path=path) == ()


def test_ticker_collision_remains_ambiguous(tmp_path):
    path = tmp_path / "asset_identity_registry.json"
    assert learn_verified_identity(
        base_asset="ABC",
        pair="ABCUSD",
        display_name="Alpha Coin",
        source="COINGECKO",
        source_id="alpha",
        path=path,
        learned_at=NOW - timedelta(hours=2),
    )
    assert learn_verified_identity(
        base_asset="ABC",
        pair="ABCUSDT",
        display_name="Another Coin",
        source="COINGECKO",
        source_id="another",
        path=path,
        learned_at=NOW - timedelta(hours=1),
    )
    identity = resolve_registry_identity(
        source_symbol="ABC",
        source_name="Alpha Coin",
        provider_asset_id="alpha",
        as_of=NOW,
        path=path,
    )
    assert identity.mapping_status == MappingStatus.AMBIGUOUS


def test_cryptopanic_adapter_preserves_provenance_and_identity(tmp_path):
    path = _identity_path(tmp_path)
    result = normalize_cryptopanic_posts(
        [_post()],
        ingest_time=NOW,
        identity_registry_path=path,
        normalized_at=NOW + timedelta(seconds=1),
    )
    assert not result.failures
    assert len(result.events) == 1
    event = result.events[0]
    assert event.provider == "CRYPTOPANIC"
    assert event.identity.mapping_status == MappingStatus.UNIQUE
    assert event.identity.canonical_asset_id == "solana"
    assert event.source_metadata["source_domain"] == "example.com"
    assert event.source_metadata["instrument"]["slug"] == "solana"
    assert event.persisted_at_utc is None
    assert event.decision_visible_at_utc is None


def test_cryptopanic_invalid_timestamp_fails_safely(tmp_path):
    path = _identity_path(tmp_path)
    row = _post()
    row["published_at"] = "not-a-time"
    result = normalize_cryptopanic_posts(
        [row],
        ingest_time=NOW,
        identity_registry_path=path,
    )
    assert result.events == ()
    assert result.failures
    assert result.failures[0].outcome.value == "INVALID_TIMESTAMP"


def test_coinmarketcal_adapter_uses_point_in_time_mapping(tmp_path):
    cache = _cmc_mapping_cache(tmp_path)
    payload = json.loads(cache.read_text(encoding="utf-8"))
    mapping = payload["mappings"]["SOL"]
    result = normalize_coinmarketcal_events(
        [_cmc_event()],
        ingest_time=NOW,
        mappings=[{
            "symbol": "SOL",
            "canonical_asset_id": "solana",
            "canonical_asset_name": "Solana",
            "coinmarketcal_slug": "solana",
            "coinmarketcal_name": "Solana",
            "resolved_at_utc": mapping["resolved_at"],
        }],
        normalized_at=NOW + timedelta(seconds=1),
    )
    assert len(result.events) == 1
    assert result.events[0].identity.mapping_status == MappingStatus.UNIQUE
    assert result.events[0].event_class == EventClass.CATALYST
    assert result.events[0].source_metadata["categories"] == ["Release"]


def test_persistence_stamps_visibility_only_on_durable_row(tmp_path):
    store = _store(tmp_path)
    event = _canonical_event()
    result = store.append(event, persisted_at=NOW + timedelta(seconds=2))
    assert event.persisted_at_utc is None
    assert result.event is not None
    assert result.event.persisted_at_utc == NOW + timedelta(seconds=2)
    assert result.event.decision_visible_at_utc == NOW + timedelta(seconds=2)


def test_failed_persistence_cannot_be_point_in_time_visible(tmp_path):
    root = tmp_path / "broken"
    root.mkdir()
    event_file = root / "events.jsonl"
    event_file.mkdir()
    store = EventStore(
        event_file=event_file,
        archive_dir=root / "archive",
        dead_letter_file=root / "dead.jsonl",
        lock_file=root / ".lock",
        max_bytes=1024,
        keep_lines=10,
    )
    event = _canonical_event()
    with pytest.raises((IsADirectoryError, OSError)):
        store.append(event)
    assert event.persisted_at_utc is None
    assert event.decision_visible_at_utc is None


def test_duplicate_and_revision_semantics(tmp_path):
    store = _store(tmp_path)
    first = _canonical_event(headline="v1")
    duplicate = _canonical_event(headline="v1")
    revision = _canonical_event(headline="v2")
    a = store.append(first, persisted_at=NOW + timedelta(seconds=2))
    b = store.append(duplicate, persisted_at=NOW + timedelta(seconds=3))
    c = store.append(revision, persisted_at=NOW + timedelta(seconds=4))
    assert a.outcome.value == "NORMALIZED"
    assert b.outcome.value == "DUPLICATE"
    assert c.outcome.value == "REVISION"
    assert c.event is not None
    assert c.event.revision_of == first.event_id
    assert len(tuple(store.iter_events())) == 2


def test_distinct_events_are_not_falsely_deduplicated(tmp_path):
    store = _store(tmp_path)
    store.append(_canonical_event(event_key="1"), persisted_at=NOW + timedelta(seconds=2))
    store.append(_canonical_event(event_key="2"), persisted_at=NOW + timedelta(seconds=3))
    assert len(tuple(store.iter_events())) == 2


def test_point_in_time_inclusion_exclusion_and_expiry(tmp_path):
    store = _store(tmp_path)
    event = _canonical_event()
    persisted = NOW + timedelta(seconds=2)
    store.append(event, persisted_at=persisted)
    assert store.get_visible_events(asset_id="SOL", decision_at=persisted - timedelta(microseconds=1)) == ()
    assert len(store.get_visible_events(asset_id="SOL", decision_at=persisted)) == 1
    assert store.get_visible_events(
        asset_id="SOL",
        decision_at=NOW + timedelta(days=2),
    ) == ()
    assert len(store.get_visible_events(
        asset_id="SOL",
        decision_at=NOW + timedelta(days=2),
        include_expired=True,
    )) == 1


def test_ambiguous_evidence_never_attaches_by_ticker_only(tmp_path):
    store = _store(tmp_path)
    base = _canonical_event()
    ambiguous = replace(
        base,
        event_id=stable_event_id(
            "TEST:NEWS:ambiguous:SOL",
            base.payload_hash,
        ),
        dedupe_key="TEST:NEWS:ambiguous:SOL",
        identity=EventIdentity(
            source_symbol="SOL",
            source_name="Different Sol",
            provider_asset_id="different-sol",
            mapping_status=MappingStatus.AMBIGUOUS,
            mapping_provenance="test:ambiguous",
        ),
    )
    persisted_at = NOW + timedelta(seconds=2)
    store.append(ambiguous, persisted_at=persisted_at)
    assert store.get_visible_events(
        asset_id="SOL",
        decision_at=persisted_at,
    ) == ()


def test_archive_before_compact_is_replayable(tmp_path):
    store = _store(tmp_path, max_bytes=10_000_000, keep_lines=2)
    for index in range(4):
        store.append(
            _canonical_event(event_key=str(index)),
            persisted_at=NOW + timedelta(seconds=10 + index),
        )
    archives = list((tmp_path / "events" / "archive").glob("events-*.jsonl.gz"))
    assert archives
    assert all(item.with_suffix(item.suffix + ".sha256").exists() for item in archives)
    with gzip.open(archives[0], "rt", encoding="utf-8") as handle:
        assert handle.readline().strip()
    replay = store.replay_events()
    assert len(replay) == 4
    assert [item.provider_event_id for item in replay] == ["0", "1", "2", "3"]


def test_archive_failure_never_deletes_hot_evidence(tmp_path, monkeypatch):
    import app.opip.events.storage as storage_module

    store = _store(tmp_path, max_bytes=10_000_000, keep_lines=1)
    store.append(_canonical_event(event_key="1"), persisted_at=NOW + timedelta(seconds=1))

    original = storage_module._sha256_file
    monkeypatch.setattr(
        storage_module,
        "_sha256_file",
        lambda path: (_ for _ in ()).throw(RuntimeError("verify failure")),
    )
    store.append(_canonical_event(event_key="2"), persisted_at=NOW + timedelta(seconds=2))
    monkeypatch.setattr(storage_module, "_sha256_file", original)

    hot_lines = (tmp_path / "events" / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(hot_lines) == 2


def test_observer_captures_non_finalist_known_asset_without_candidates(tmp_path):
    identity_path = _identity_path(tmp_path)
    store = _store(tmp_path)

    class CryptoClient:
        def __init__(self):
            self.calls = []
        def get_posts(self, symbols):
            self.calls.append(tuple(symbols))
            return [_post(published=NOW - timedelta(minutes=30))]

    client = CryptoClient()
    settings = SimpleNamespace(
        opip_event_store_enabled=True,
        opip_event_ingest_interval_seconds=300,
        cryptopanic_auth_token="token",
        cryptopanic_api_plan="developer",
        coinmarketcal_api_key=None,
    )
    result = capture_external_event_intelligence(
        settings=settings,
        capture_started_at=NOW,
        store=store,
        identity_registry_path=identity_path,
        coinmarketcal_cache_path=tmp_path / "no-cmc.json",
        state_path=tmp_path / "ingest-state.json",
        cryptopanic_client=client,
        force=True,
    )
    assert result.ran
    assert client.calls == [("SOL",)]
    assert result.telemetry["events_persisted"] == 1
    stored = tuple(store.iter_events())
    assert len(stored) == 1
    # Receipt time is conservative: provider evidence is never considered
    # known before capture started, and visibility is later still (persistence).
    assert stored[0].ingest_time_utc >= NOW
    assert stored[0].decision_visible_at_utc >= stored[0].ingest_time_utc


def test_observer_storage_failure_is_fail_soft(tmp_path):
    identity_path = _identity_path(tmp_path)

    class BrokenStore:
        def append(self, event):
            raise OSError("disk unavailable")
        def record_dead_letter(self, **kwargs):
            pass

    class CryptoClient:
        def get_posts(self, symbols):
            return [_post(published=NOW - timedelta(minutes=30))]

    settings = SimpleNamespace(
        opip_event_store_enabled=True,
        opip_event_ingest_interval_seconds=300,
        cryptopanic_auth_token="token",
        cryptopanic_api_plan="developer",
        coinmarketcal_api_key=None,
    )
    result = capture_external_event_intelligence(
        settings=settings,
        capture_started_at=NOW,
        store=BrokenStore(),
        identity_registry_path=identity_path,
        coinmarketcal_cache_path=tmp_path / "no-cmc.json",
        state_path=tmp_path / "state.json",
        cryptopanic_client=CryptoClient(),
        force=True,
    )
    assert result.telemetry["storage_errors"] == 1


