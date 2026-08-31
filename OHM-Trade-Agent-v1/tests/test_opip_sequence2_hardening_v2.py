from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

from app.opip.events.adapters import (
    normalize_coinmarketcal_events,
    normalize_cryptopanic_posts,
)
from app.opip.events.contract import (
    EventClass,
    EventIdentity,
    EventProvenance,
    EventSeverity,
    EventType,
    MappingStatus,
    OPipEvent,
    stable_event_id,
    stable_payload_hash,
)
from app.opip.events.identity import (
    learn_identity_binding,
    resolve_news_mention,
    resolve_structured_identity,
)
from app.opip.events.observer import capture_external_event_intelligence
from app.opip.events.provider_health import (
    ProviderHealthState,
    ProviderHealthStore,
)
from app.opip.events.storage import EventStore
from app.services.asset_display_identity import learn_verified_identity
from app.services.cryptopanic import CryptoPanicAPIError


NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def _identity_registry(tmp_path: Path) -> Path:
    path = tmp_path / "identity.json"
    assert learn_verified_identity(
        base_asset="SOL",
        pair="SOLUSD",
        display_name="Solana",
        source="COINGECKO",
        source_id="solana",
        path=path,
        learned_at=NOW - timedelta(days=2),
    )
    return path


def _settings(**overrides):
    values = {
        "opip_event_store_enabled": True,
        "opip_event_ingest_interval_seconds": 300,
        "opip_event_mapping_lookups_per_capture": 1,
        "opip_event_provider_timeout_seconds": 5.0,
        "cryptopanic_auth_token": "token",
        "cryptopanic_api_plan": "developer",
        "coinmarketcal_api_key": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _store(tmp_path: Path, *, keep_lines: int = 100) -> EventStore:
    root = tmp_path / "events"
    return EventStore(
        event_file=root / "events.jsonl",
        archive_dir=root / "archive",
        dead_letter_file=root / "dead.jsonl",
        lock_file=root / ".events.lock",
        max_bytes=10_000_000,
        keep_lines=keep_lines,
    )


def _event(key: str, *, ingest: datetime = NOW) -> OPipEvent:
    identity = EventIdentity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        canonical_asset_id="solana",
        canonical_asset_name="Solana",
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=1.0,
        identity_learned_at_utc=ingest - timedelta(days=1),
        mapping_provenance="test",
    )
    payload_hash = stable_payload_hash({"key": key})
    dedupe = f"TEST:NEWS:{key}:solana"
    return OPipEvent(
        event_id=stable_event_id(dedupe, payload_hash),
        dedupe_key=dedupe,
        provider="TEST",
        provider_event_id=key,
        event_class=EventClass.NEWS,
        payload_hash=payload_hash,
        source_event_time_utc=ingest - timedelta(minutes=10),
        ingest_time_utc=ingest,
        normalized_at_utc=ingest + timedelta(seconds=1),
        identity=identity,
        headline=f"event {key}",
        event_type=EventType.NEWS_GENERAL,
        severity=EventSeverity.INFO,
        provenance=EventProvenance(
            provider="TEST",
            provider_event_id=key,
            provider_asset_id="solana",
            source_reference="test",
            source_sequence=None,
            canonical_payload_hash=payload_hash,
            source_payload_hash=payload_hash,
        ),
        expires_at_utc=ingest + timedelta(hours=24),
    )


def _post(*, title: str = "Solana update", published: datetime | None = None):
    return {
        "id": 1,
        "title": title,
        "description": "description",
        "published_at": (published or NOW - timedelta(minutes=10)).isoformat(),
        "kind": "news",
        "source": {"title": "Example", "domain": "example.com"},
        "instruments": [
            {"code": "SOL", "title": "Solana", "slug": "solana"}
        ],
        "votes": {"positive": 3},
    }


def test_cryptopanic_emits_canonical_type_severity_and_provenance(tmp_path):
    result = normalize_cryptopanic_posts(
        [_post(title="Solana protocol exploit reported")],
        ingest_time=NOW,
        identity_registry_path=_identity_registry(tmp_path),
        normalized_at=NOW + timedelta(seconds=1),
    )
    assert len(result.events) == 1
    event = result.events[0]
    assert event.event_type == EventType.NEWS_SECURITY
    assert event.severity == EventSeverity.HIGH
    assert event.provenance is not None
    assert event.provenance.provider == "CRYPTOPANIC"
    assert event.provenance.provider_event_id == "1"
    assert event.provenance.canonical_payload_hash == event.payload_hash
    assert event.provenance.source_payload_hash
    assert event.schema_version == 2


def test_news_event_type_does_not_treat_hackathon_as_security(tmp_path):
    result = normalize_cryptopanic_posts(
        [_post(title="Solana hackathon opens applications")],
        ingest_time=NOW,
        identity_registry_path=_identity_registry(tmp_path),
        normalized_at=NOW + timedelta(seconds=1),
    )
    assert result.events[0].event_type == EventType.NEWS_GENERAL
    assert result.events[0].severity == EventSeverity.INFO


def test_coinmarketcal_emits_fine_event_type_and_provenance():
    mapping = {
        "symbol": "SOL",
        "canonical_asset_id": "solana",
        "canonical_asset_name": "Solana",
        "coinmarketcal_slug": "solana",
        "coinmarketcal_name": "Solana",
        "resolved_at_utc": (NOW - timedelta(days=1)).isoformat(),
        "identity_visible_at_utc": (NOW - timedelta(days=1)).isoformat(),
    }
    row = {
        "id": 44,
        "title": "Token Unlock",
        "date": (NOW + timedelta(days=2)).isoformat(),
        "categories": [{"name": "Tokenomics"}],
        "coins": [{"slug": "solana", "name": "Solana", "symbol": "SOL"}],
    }
    result = normalize_coinmarketcal_events(
        [row],
        ingest_time=NOW,
        mappings=[mapping],
        normalized_at=NOW + timedelta(seconds=1),
    )
    event = result.events[0]
    assert event.event_type == EventType.TOKEN_UNLOCK
    assert event.severity == EventSeverity.MEDIUM
    assert event.provenance is not None
    assert event.provenance.provider == "COINMARKETCAL"


def test_provider_health_state_machine_and_freshness(tmp_path):
    store = ProviderHealthStore(tmp_path / "health.json")

    missing = store.record_missing_credentials(
        provider="CRYPTOPANIC",
        checked_at=NOW,
        expected_interval_seconds=300,
    )
    assert missing.state == ProviderHealthState.MISSING_CREDENTIALS

    no_event = store.record_success(
        provider="CRYPTOPANIC",
        checked_at=NOW + timedelta(minutes=1),
        expected_interval_seconds=300,
        request_count=1,
    )
    assert no_event.state == ProviderHealthState.NO_EVENT

    stale = store.record_success(
        provider="CRYPTOPANIC",
        checked_at=NOW + timedelta(minutes=2),
        expected_interval_seconds=300,
        request_count=1,
        event_source_times=[NOW - timedelta(days=2)],
        event_ingest_times=[NOW + timedelta(minutes=2)],
        stale_events=1,
    )
    assert stale.state == ProviderHealthState.STALE

    limited = store.record_unavailable(
        provider="CRYPTOPANIC",
        checked_at=NOW + timedelta(minutes=3),
        expected_interval_seconds=300,
        request_count=1,
        rate_limited=True,
        retry_after_seconds=60,
        error_kind="CryptoPanicAPIError",
    )
    assert limited.state == ProviderHealthState.RATE_LIMITED
    assert limited.retry_after_seconds == 60
    assert limited.consecutive_failures == 1

    recovered = store.record_context_success(
        provider="CRYPTOPANIC",
        checked_at=NOW + timedelta(minutes=4),
        expected_interval_seconds=300,
    )
    assert recovered.state == ProviderHealthState.HEALTHY
    assert recovered.consecutive_failures == 0

    aged = store.read(
        "CRYPTOPANIC",
        as_of=NOW + timedelta(minutes=25),
        stale_multiplier=3,
    )
    assert aged is not None
    assert aged.state == ProviderHealthState.STALE


def test_context_provider_success_becomes_stale_when_source_is_old(tmp_path):
    store = ProviderHealthStore(tmp_path / "health-context.json")
    snapshot = store.record_context_success(
        provider="COINGECKO",
        checked_at=NOW,
        expected_interval_seconds=300,
        source_observed_at=NOW - timedelta(minutes=20),
    )
    assert snapshot.state == ProviderHealthState.STALE
    assert snapshot.last_observation_at_utc == NOW - timedelta(minutes=20)
    assert snapshot.last_event_source_time_utc is None


def test_observer_distinguishes_missing_credentials_no_event_and_rate_limit(tmp_path):
    identity = _identity_registry(tmp_path)
    event_store = _store(tmp_path)
    health = ProviderHealthStore(tmp_path / "provider-health.json")

    missing = capture_external_event_intelligence(
        settings=_settings(cryptopanic_auth_token=None),
        capture_started_at=NOW,
        store=event_store,
        health_store=health,
        identity_registry_path=identity,
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        legacy_coinmarketcal_cache_path=tmp_path / "legacy.json",
        state_path=tmp_path / "state-missing.json",
        force=True,
    )
    assert missing.telemetry["provider_health"]["CRYPTOPANIC"] == "MISSING_CREDENTIALS"
    assert missing.telemetry["provider_health"]["COINMARKETCAL"] == "MISSING_CREDENTIALS"

    class EmptyCrypto:
        def get_posts(self, symbols):
            return []

    no_event = capture_external_event_intelligence(
        settings=_settings(),
        capture_started_at=NOW + timedelta(minutes=5),
        store=event_store,
        health_store=health,
        identity_registry_path=identity,
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        legacy_coinmarketcal_cache_path=tmp_path / "legacy.json",
        state_path=tmp_path / "state-empty.json",
        cryptopanic_client=EmptyCrypto(),
        force=True,
    )
    assert no_event.telemetry["provider_health"]["CRYPTOPANIC"] == "NO_EVENT"

    class LimitedCrypto:
        def get_posts(self, symbols):
            raise CryptoPanicAPIError(
                "limited",
                status_code=429,
                retry_after_seconds=30,
            )

    limited = capture_external_event_intelligence(
        settings=_settings(),
        capture_started_at=NOW + timedelta(minutes=10),
        store=event_store,
        health_store=health,
        identity_registry_path=identity,
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        legacy_coinmarketcal_cache_path=tmp_path / "legacy.json",
        state_path=tmp_path / "state-limited.json",
        cryptopanic_client=LimitedCrypto(),
        force=True,
    )
    assert limited.telemetry["provider_health"]["CRYPTOPANIC"] == "RATE_LIMITED"


def test_observer_distinguishes_stale_and_degraded_provider_evidence(tmp_path):
    identity = _identity_registry(tmp_path)
    event_store = _store(tmp_path)
    health = ProviderHealthStore(tmp_path / "health.json")

    class StaleCrypto:
        def get_posts(self, symbols):
            return [_post(published=NOW - timedelta(hours=30))]

    stale = capture_external_event_intelligence(
        settings=_settings(),
        capture_started_at=NOW,
        store=event_store,
        health_store=health,
        identity_registry_path=identity,
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        legacy_coinmarketcal_cache_path=tmp_path / "legacy.json",
        state_path=tmp_path / "state-stale.json",
        cryptopanic_client=StaleCrypto(),
        force=True,
    )
    assert stale.telemetry["provider_health"]["CRYPTOPANIC"] == "STALE"

    class MalformedCrypto:
        def get_posts(self, symbols):
            row = _post()
            row["published_at"] = "not-a-time"
            return [row]

    degraded = capture_external_event_intelligence(
        settings=_settings(),
        capture_started_at=NOW + timedelta(minutes=5),
        store=event_store,
        health_store=health,
        identity_registry_path=identity,
        coinmarketcal_cache_path=tmp_path / "cmc.json",
        legacy_coinmarketcal_cache_path=tmp_path / "legacy.json",
        state_path=tmp_path / "state-degraded.json",
        cryptopanic_client=MalformedCrypto(),
        force=True,
    )
    assert degraded.telemetry["provider_health"]["CRYPTOPANIC"] == "DEGRADED"


def test_structured_identity_bindings_are_point_in_time_safe(tmp_path):
    path = _identity_registry(tmp_path)
    learned = NOW - timedelta(hours=1)
    assert learn_identity_binding(
        canonical_symbol="SOL",
        binding_type="ALIAS",
        alias="SOLX",
        learned_at=learned,
        path=path,
    )
    assert learn_identity_binding(
        canonical_symbol="SOL",
        binding_type="VENUE_INSTRUMENT",
        venue="KRAKEN",
        venue_symbol="SOL/USD:USD",
        instrument_type="PERPETUAL",
        learned_at=learned,
        path=path,
    )
    assert learn_identity_binding(
        canonical_symbol="SOL",
        binding_type="ONCHAIN",
        chain_id="solana:mainnet",
        contract_address="So11111111111111111111111111111111111111112",
        learned_at=learned,
        path=path,
    )

    before = resolve_structured_identity(
        source_symbol="SOLX",
        as_of=learned - timedelta(seconds=1),
        path=path,
    )
    assert before.mapping_status == MappingStatus.UNKNOWN

    alias = resolve_structured_identity(
        source_symbol="SOLX",
        as_of=NOW,
        path=path,
    )
    assert alias.mapping_status == MappingStatus.UNIQUE
    assert alias.canonical_asset_id == "solana"

    venue = resolve_structured_identity(
        as_of=NOW,
        venue="KRAKEN",
        venue_symbol="SOL/USD:USD",
        instrument_type="PERPETUAL",
        path=path,
    )
    assert venue.mapping_status == MappingStatus.UNIQUE
    assert venue.venue == "KRAKEN"
    assert venue.instrument_type == "PERPETUAL"

    onchain = resolve_structured_identity(
        as_of=NOW,
        chain_id="solana:mainnet",
        contract_address="So11111111111111111111111111111111111111112",
        path=path,
    )
    assert onchain.mapping_status == MappingStatus.UNIQUE
    assert onchain.chain_id == "solana:mainnet"
    assert (
        onchain.contract_address
        == "So11111111111111111111111111111111111111112"
    )


def test_known_asset_cannot_bypass_unverified_structured_identity(tmp_path):
    path = _identity_registry(tmp_path)
    wrong_contract = resolve_structured_identity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        as_of=NOW,
        chain_id="solana:mainnet",
        contract_address="Different111111111111111111111111111111111",
        path=path,
    )
    assert wrong_contract.mapping_status == MappingStatus.UNKNOWN

    incomplete = resolve_structured_identity(
        source_symbol="SOL",
        source_name="Solana",
        provider_asset_id="solana",
        as_of=NOW,
        chain_id="solana:mainnet",
        path=path,
    )
    assert incomplete.mapping_status == MappingStatus.UNKNOWN
    assert incomplete.mapping_provenance.endswith("incomplete_onchain_identity")


def test_structured_identity_collision_and_text_mentions_fail_closed(tmp_path):
    path = _identity_registry(tmp_path)
    assert learn_verified_identity(
        base_asset="ABC",
        pair="ABCUSD",
        display_name="Alpha Beta Coin",
        source="COINGECKO",
        source_id="alpha-beta-coin",
        path=path,
        learned_at=NOW - timedelta(days=1),
    )
    assert learn_identity_binding(
        canonical_symbol="SOL",
        binding_type="ALIAS",
        alias="SHARED",
        learned_at=NOW - timedelta(hours=1),
        path=path,
    )
    assert learn_identity_binding(
        canonical_symbol="ABC",
        binding_type="ALIAS",
        alias="SHARED",
        learned_at=NOW - timedelta(hours=1),
        path=path,
    )

    collision = resolve_structured_identity(
        source_symbol="SHARED",
        as_of=NOW,
        path=path,
    )
    assert collision.mapping_status == MappingStatus.AMBIGUOUS

    mention = resolve_news_mention(
        "Developers announced a Solana upgrade.",
        as_of=NOW,
        path=path,
    )
    assert mention.mapping_status == MappingStatus.UNIQUE
    assert mention.canonical_asset_id == "solana"

    ambiguous = resolve_news_mention(
        "Solana and Alpha Beta Coin announced upgrades.",
        as_of=NOW,
        path=path,
    )
    assert ambiguous.mapping_status == MappingStatus.AMBIGUOUS


def test_warm_cold_lifecycle_is_verified_and_replayable(tmp_path):
    store = _store(tmp_path, keep_lines=1)
    store.append(_event("1"), persisted_at=NOW + timedelta(seconds=2))
    store.append(_event("2"), persisted_at=NOW + timedelta(seconds=3))

    warm = list(store.archive_dir.glob("events-*.jsonl.gz"))
    assert warm
    old = (NOW - timedelta(days=40)).timestamp()
    for archive in warm:
        os.utime(archive, (old, old))
        checksum = archive.with_suffix(archive.suffix + ".sha256")
        os.utime(checksum, (old, old))

    moved = store.maintain_lifecycle(now=NOW, cold_after_days=30)
    assert moved >= 1
    assert not list(store.archive_dir.glob("events-*.jsonl.gz"))

    cold = list(store.cold_archive_dir.rglob("events-*.jsonl.gz"))
    assert cold
    assert all(
        item.with_suffix(item.suffix + ".sha256").exists()
        for item in cold
    )
    assert len(store.replay_events()) == 2

    manifest = json.loads(
        store.archive_manifest_file.read_text(encoding="utf-8")
    )
    assert manifest["segments"]
    assert any(
        row["tier"] == "COLD"
        for row in manifest["segments"].values()
    )

    stats = store.storage_stats()
    assert stats.cold_archive_segments >= 1
    assert stats.cold_archive_bytes > 0
    assert stats.manifest_segments >= 1
