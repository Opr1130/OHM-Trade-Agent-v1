"""Fail-soft external event ingestion for O'Pip.

This observer is independent of current trading finalists. It reads the
point-in-time-safe identity catalogs accumulated by the existing production
system, fetches discrete provider evidence for those known assets, normalizes
and persists it, and returns prefetched payloads that existing finalist
enrichment may reuse.

The feature is dark by default and has no trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from app.opip.events.adapters import (
    AdapterBatchResult,
    COINMARKETCAL,
    CRYPTOPANIC,
    normalize_coinmarketcal_events,
    normalize_cryptopanic_posts,
)
from app.opip.events.contract import IngestOutcome, MappingStatus, require_utc
from app.opip.events.identity import (
    ASSET_IDENTITY_REGISTRY,
    COINMARKETCAL_MAPPING_CACHE,
    known_coinmarketcal_mappings,
    known_unique_assets,
)
from app.opip.events.storage import EventStore
from app.services.coinmarketcal import CoinMarketCalAPIError, CoinMarketCalClient
from app.services.cryptopanic import CryptoPanicAPIError, CryptoPanicClient
from app.services.registry_io import (
    RegistryIOError,
    load_json,
    registry_lock,
    save_json_atomic,
)


logger = logging.getLogger(__name__)

INGEST_STATE_FILE = Path("/app/data/opip/events/ingest_state.json")
CRYPTOPANIC_BATCH_SIZE = 50
COINMARKETCAL_BATCH_SIZE = 100


@dataclass
class IngestionTelemetry:
    events_received: int = 0
    events_normalized: int = 0
    events_persisted: int = 0
    duplicates: int = 0
    revisions: int = 0
    malformed: int = 0
    mapping_unique: int = 0
    mapping_ambiguous: int = 0
    mapping_unknown: int = 0
    stale: int = 0
    provider_errors: int = 0
    normalization_errors: int = 0
    storage_errors: int = 0
    cryptopanic_requests: int = 0
    coinmarketcal_requests: int = 0
    lag_samples_seconds: list[float] | None = None

    def __post_init__(self) -> None:
        if self.lag_samples_seconds is None:
            self.lag_samples_seconds = []

    def as_dict(self) -> dict[str, Any]:
        lags = list(self.lag_samples_seconds or [])
        return {
            "events_received": self.events_received,
            "events_normalized": self.events_normalized,
            "events_persisted": self.events_persisted,
            "duplicates": self.duplicates,
            "revisions": self.revisions,
            "malformed": self.malformed,
            "mapping_unique": self.mapping_unique,
            "mapping_ambiguous": self.mapping_ambiguous,
            "mapping_unknown": self.mapping_unknown,
            "stale": self.stale,
            "provider_errors": self.provider_errors,
            "normalization_errors": self.normalization_errors,
            "storage_errors": self.storage_errors,
            "cryptopanic_requests": self.cryptopanic_requests,
            "coinmarketcal_requests": self.coinmarketcal_requests,
            "lag_min_seconds": min(lags) if lags else None,
            "lag_mean_seconds": mean(lags) if lags else None,
            "lag_max_seconds": max(lags) if lags else None,
        }


@dataclass(frozen=True)
class ExternalEventCaptureResult:
    enabled: bool
    ran: bool
    cryptopanic_posts: tuple[dict[str, Any], ...] | None
    cryptopanic_request_count: int
    coinmarketcal_events: tuple[dict[str, Any], ...] | None
    coinmarketcal_covered_slugs: tuple[str, ...]
    coinmarketcal_request_count: int
    telemetry: dict[str, Any]


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _read_state(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except (OSError, TimeoutError, RegistryIOError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _capture_due(
    *,
    now: datetime,
    interval_seconds: int,
    state_path: Path,
) -> bool:
    state = _read_state(state_path)
    raw = state.get("last_attempt_at_utc")
    if not isinstance(raw, str) or not raw:
        return True
    try:
        previous = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if previous.tzinfo is None or previous.utcoffset() is None:
        return True
    previous = previous.astimezone(timezone.utc)
    return (now - previous).total_seconds() >= interval_seconds


def _save_attempt_state(path: Path, now: datetime) -> None:
    lock = path.parent / f".{path.name}.lock"
    with registry_lock(lock):
        state = _read_state(path)
        state["last_attempt_at_utc"] = now.isoformat()
        save_json_atomic(path, state)


def _record_adapter_result(
    batch: AdapterBatchResult,
    *,
    store: EventStore,
    telemetry: IngestionTelemetry,
    now: datetime,
) -> None:
    telemetry.events_normalized += len(batch.events)
    for failure in batch.failures:
        if failure.outcome in {
            IngestOutcome.MALFORMED_PAYLOAD,
            IngestOutcome.MISSING_TIMESTAMP,
            IngestOutcome.INVALID_TIMESTAMP,
        }:
            telemetry.malformed += 1
        else:
            telemetry.normalization_errors += 1
        try:
            store.record_dead_letter(
                provider=failure.provider,
                reason=f"{failure.outcome.value}:{failure.reason}",
                observed_at=now,
                provider_event_id=failure.provider_event_id,
                payload_hash=failure.payload_hash,
            )
        except Exception:
            telemetry.storage_errors += 1

    for event in batch.events:
        status = event.identity.mapping_status
        if status == MappingStatus.UNIQUE:
            telemetry.mapping_unique += 1
        elif status == MappingStatus.AMBIGUOUS:
            telemetry.mapping_ambiguous += 1
        else:
            telemetry.mapping_unknown += 1
        if event.expires_at_utc is not None and event.expires_at_utc < now:
            telemetry.stale += 1
        if event.event_class.value == "NEWS":
            lag = max(0.0, (event.ingest_time_utc - event.source_event_time_utc).total_seconds())
            if telemetry.lag_samples_seconds is not None:
                telemetry.lag_samples_seconds.append(lag)
        try:
            result = store.append(event)
        except Exception:
            telemetry.storage_errors += 1
            logger.exception("O'Pip canonical event persistence failed")
            continue
        if result.outcome == IngestOutcome.DUPLICATE:
            telemetry.duplicates += 1
        elif result.outcome == IngestOutcome.REVISION:
            telemetry.revisions += 1
            telemetry.events_persisted += 1
        else:
            telemetry.events_persisted += 1


def capture_external_event_intelligence(
    *,
    settings: Any,
    decision_at: datetime,
    store: EventStore | None = None,
    identity_registry_path: Path = ASSET_IDENTITY_REGISTRY,
    coinmarketcal_cache_path: Path = COINMARKETCAL_MAPPING_CACHE,
    state_path: Path = INGEST_STATE_FILE,
    cryptopanic_client: Any | None = None,
    coinmarketcal_client: Any | None = None,
    force: bool = False,
) -> ExternalEventCaptureResult:
    """Capture provider events independently of current finalist selection."""
    enabled = bool(getattr(settings, "opip_event_store_enabled", False))
    if not enabled:
        return ExternalEventCaptureResult(
            enabled=False,
            ran=False,
            cryptopanic_posts=None,
            cryptopanic_request_count=0,
            coinmarketcal_events=None,
            coinmarketcal_covered_slugs=(),
            coinmarketcal_request_count=0,
            telemetry=IngestionTelemetry().as_dict(),
        )

    now = require_utc(decision_at, field_name="decision_at")
    interval = int(getattr(settings, "opip_event_ingest_interval_seconds", 300))
    if not force and not _capture_due(
        now=now,
        interval_seconds=interval,
        state_path=state_path,
    ):
        return ExternalEventCaptureResult(
            enabled=True,
            ran=False,
            cryptopanic_posts=None,
            cryptopanic_request_count=0,
            coinmarketcal_events=None,
            coinmarketcal_covered_slugs=(),
            coinmarketcal_request_count=0,
            telemetry=IngestionTelemetry().as_dict(),
        )

    telemetry = IngestionTelemetry()
    active_store = store or EventStore()
    crypto_posts: list[dict[str, Any]] = []
    crypto_fetched = False
    catalyst_rows: list[dict[str, Any]] = []
    catalyst_fetched = False
    covered_slugs: list[str] = []

    try:
        _save_attempt_state(state_path, now)
    except Exception:
        # Rate-state failure must not block capture or trading.
        telemetry.storage_errors += 1

    assets = known_unique_assets(as_of=now, path=identity_registry_path)
    symbols = sorted({item["symbol"] for item in assets if item.get("symbol")})
    token = str(getattr(settings, "cryptopanic_auth_token", "") or "").strip()
    plan = str(getattr(settings, "cryptopanic_api_plan", "developer") or "developer")
    if token and symbols:
        client = cryptopanic_client or CryptoPanicClient(token, plan)
        try:
            for batch_symbols in _chunks(symbols, CRYPTOPANIC_BATCH_SIZE):
                rows = client.get_posts(batch_symbols)
                telemetry.cryptopanic_requests += 1
                crypto_fetched = True
                crypto_posts.extend(row for row in rows if isinstance(row, dict))
        except CryptoPanicAPIError:
            telemetry.provider_errors += 1
            logger.warning("O'Pip CryptoPanic event ingestion unavailable")

    telemetry.events_received += len(crypto_posts)
    if crypto_fetched:
        try:
            batch = normalize_cryptopanic_posts(
                crypto_posts,
                ingest_time=now,
                identity_registry_path=identity_registry_path,
                normalized_at=datetime.now(timezone.utc),
            )
            _record_adapter_result(
                batch,
                store=active_store,
                telemetry=telemetry,
                now=now,
            )
        except Exception:
            telemetry.normalization_errors += 1
            logger.exception("O'Pip CryptoPanic event normalization failed open")

    mappings = known_coinmarketcal_mappings(
        as_of=now,
        path=coinmarketcal_cache_path,
    )
    slugs = sorted(
        {
            item["coinmarketcal_slug"]
            for item in mappings
            if item.get("coinmarketcal_slug")
        }
    )
    api_key = str(getattr(settings, "coinmarketcal_api_key", "") or "").strip()
    if api_key and slugs:
        client = coinmarketcal_client or CoinMarketCalClient(api_key)
        try:
            for batch_slugs in _chunks(slugs, COINMARKETCAL_BATCH_SIZE):
                rows = client.get_events(
                    batch_slugs,
                    now,
                    now + timedelta(days=7),
                )
                telemetry.coinmarketcal_requests += 1
                catalyst_fetched = True
                covered_slugs.extend(batch_slugs)
                catalyst_rows.extend(row for row in rows if isinstance(row, dict))
        except CoinMarketCalAPIError:
            telemetry.provider_errors += 1
            logger.warning("O'Pip CoinMarketCal event ingestion unavailable")

    telemetry.events_received += len(catalyst_rows)
    if catalyst_fetched:
        try:
            batch = normalize_coinmarketcal_events(
                catalyst_rows,
                ingest_time=now,
                mappings=mappings,
                normalized_at=datetime.now(timezone.utc),
            )
            _record_adapter_result(
                batch,
                store=active_store,
                telemetry=telemetry,
                now=now,
            )
        except Exception:
            telemetry.normalization_errors += 1
            logger.exception("O'Pip CoinMarketCal event normalization failed open")

    return ExternalEventCaptureResult(
        enabled=True,
        ran=True,
        cryptopanic_posts=tuple(crypto_posts) if crypto_fetched else None,
        cryptopanic_request_count=telemetry.cryptopanic_requests,
        coinmarketcal_events=tuple(catalyst_rows) if catalyst_fetched else None,
        coinmarketcal_covered_slugs=tuple(sorted(set(covered_slugs))),
        coinmarketcal_request_count=telemetry.coinmarketcal_requests,
        telemetry=telemetry.as_dict(),
    )
