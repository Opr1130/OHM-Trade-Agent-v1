"""Fail-soft external event ingestion for O'Pip.

This observer is independent of current trading finalists. It runs after real
risk-protection workflows in the unified cycle, reads only identities that were
already known at capture start, fetches discrete provider evidence, and writes
canonical shadow evidence.

The event store is not read by production qualification/ranking in Sequence 2.
External intelligence therefore remains evidence-only and non-authoritative.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
from statistics import mean
import time
from typing import Any, Iterable

from app.opip.events.adapters import (
    AdapterBatchResult,
    normalize_coinmarketcal_events,
    normalize_cryptopanic_posts,
)
from app.opip.events.contract import (
    IngestOutcome,
    MappingStatus,
    parse_utc,
    require_utc,
)
from app.opip.events.identity import (
    ASSET_IDENTITY_REGISTRY,
    COINMARKETCAL_MAPPING_CACHE,
    LEGACY_COINMARKETCAL_MAPPING_CACHE,
    known_unique_assets,
    merge_point_in_time_mappings,
    resolve_coinmarketcal_identity_mapping,
    save_event_coinmarketcal_mapping,
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
    coinmarketcal_mapping_requests: int = 0
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
            "coinmarketcal_mapping_requests": self.coinmarketcal_mapping_requests,
            "lag_min_seconds": min(lags) if lags else None,
            "lag_mean_seconds": mean(lags) if lags else None,
            "lag_max_seconds": max(lags) if lags else None,
        }


@dataclass(frozen=True)
class ExternalEventCaptureResult:
    enabled: bool
    ran: bool
    telemetry: dict[str, Any]


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _mappings_for_safe_assets(
    mappings: Iterable[dict[str, str]],
    assets: Iterable[dict[str, str]],
) -> list[dict[str, str]]:
    safe = {
        str(asset.get("symbol") or ""): dict(asset)
        for asset in assets
        if asset.get("symbol") and asset.get("canonical_asset_id")
    }
    result: list[dict[str, str]] = []
    for item in mappings:
        symbol = str(item.get("symbol") or "")
        asset = safe.get(symbol)
        if asset is None:
            continue
        if str(item.get("canonical_asset_id") or "") != str(
            asset.get("canonical_asset_id") or ""
        ):
            continue
        try:
            asset_learned = parse_utc(
                asset.get("learned_at_utc"),
                field_name="asset.learned_at_utc",
            )
            mapping_learned = parse_utc(
                item.get("resolved_at_utc"),
                field_name="mapping.resolved_at_utc",
            )
        except ValueError:
            continue
        if asset_learned is None or mapping_learned is None:
            continue
        row = dict(item)
        # The combined provider identity was not safe until BOTH the canonical
        # asset mapping and CoinMarketCal mapping were known.
        row["identity_visible_at_utc"] = max(
            asset_learned,
            mapping_learned,
        ).isoformat()
        result.append(row)
    return result


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
    observed_at: datetime,
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
                observed_at=observed_at,
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

        if (
            event.expires_at_utc is not None
            and event.expires_at_utc < observed_at
        ):
            telemetry.stale += 1

        if event.event_class.value == "NEWS":
            lag = max(
                0.0,
                (
                    event.ingest_time_utc - event.source_event_time_utc
                ).total_seconds(),
            )
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
    capture_started_at: datetime,
    store: EventStore | None = None,
    identity_registry_path: Path = ASSET_IDENTITY_REGISTRY,
    coinmarketcal_cache_path: Path = COINMARKETCAL_MAPPING_CACHE,
    legacy_coinmarketcal_cache_path: Path = LEGACY_COINMARKETCAL_MAPPING_CACHE,
    state_path: Path = INGEST_STATE_FILE,
    cryptopanic_client: Any | None = None,
    coinmarketcal_client: Any | None = None,
    sleep: Any = time.sleep,
    force: bool = False,
) -> ExternalEventCaptureResult:
    """Capture discrete provider evidence without current finalist selection."""
    enabled = bool(getattr(settings, "opip_event_store_enabled", False))
    if not enabled:
        return ExternalEventCaptureResult(
            enabled=False,
            ran=False,
            telemetry=IngestionTelemetry().as_dict(),
        )

    capture_started = require_utc(
        capture_started_at,
        field_name="capture_started_at",
    )
    interval = int(
        getattr(settings, "opip_event_ingest_interval_seconds", 300)
    )
    if not force and not _capture_due(
        now=capture_started,
        interval_seconds=interval,
        state_path=state_path,
    ):
        return ExternalEventCaptureResult(
            enabled=True,
            ran=False,
            telemetry=IngestionTelemetry().as_dict(),
        )

    telemetry = IngestionTelemetry()
    active_store = store or EventStore()

    try:
        _save_attempt_state(state_path, capture_started)
    except Exception:
        # Cadence-state failure must not block evidence collection or trading.
        telemetry.storage_errors += 1

    # Select assets only from identity knowledge that existed at capture start.
    # Rows without a trustworthy learned timestamp are deliberately excluded.
    assets = known_unique_assets(
        as_of=capture_started,
        path=identity_registry_path,
    )
    symbols = sorted(
        {item["symbol"] for item in assets if item.get("symbol")}
    )
    token = str(
        getattr(settings, "cryptopanic_auth_token", "") or ""
    ).strip()
    plan = str(
        getattr(settings, "cryptopanic_api_plan", "developer")
        or "developer"
    )
    provider_timeout = float(
        getattr(settings, "opip_event_provider_timeout_seconds", 5.0)
    )

    crypto_posts: list[dict[str, Any]] = []
    if token and symbols:
        client = cryptopanic_client or CryptoPanicClient(
            token,
            plan,
            timeout_seconds=provider_timeout,
        )
        try:
            for batch_symbols in _chunks(
                symbols,
                CRYPTOPANIC_BATCH_SIZE,
            ):
                telemetry.cryptopanic_requests += 1
                rows = client.get_posts(batch_symbols)
                crypto_posts.extend(
                    row for row in rows if isinstance(row, dict)
                )
        except CryptoPanicAPIError:
            telemetry.provider_errors += 1
            logger.warning(
                "O'Pip CryptoPanic event ingestion unavailable"
            )

    telemetry.events_received += len(crypto_posts)
    if crypto_posts:
        # Conservative receipt time: all rows in this batch are treated as
        # known no earlier than completion of provider I/O.
        crypto_ingest_at = datetime.now(timezone.utc)
        try:
            batch = normalize_cryptopanic_posts(
                crypto_posts,
                ingest_time=crypto_ingest_at,
                identity_registry_path=identity_registry_path,
                normalized_at=datetime.now(timezone.utc),
            )
            _record_adapter_result(
                batch,
                store=active_store,
                telemetry=telemetry,
                observed_at=crypto_ingest_at,
            )
        except Exception:
            telemetry.normalization_errors += 1
            logger.exception(
                "O'Pip CryptoPanic event normalization failed open"
            )

    mappings = _mappings_for_safe_assets(
        merge_point_in_time_mappings(
            as_of=capture_started,
            paths=(
                coinmarketcal_cache_path,
                legacy_coinmarketcal_cache_path,
            ),
        ),
        assets,
    )
    api_key = str(
        getattr(settings, "coinmarketcal_api_key", "") or ""
    ).strip()
    cmc_client = (
        coinmarketcal_client
        or CoinMarketCalClient(
            api_key,
            timeout_seconds=provider_timeout,
        )
        if api_key
        else None
    )

    # Expand catalyst coverage independently of trading finalists. New
    # mappings are written only to the Sequence 2 shadow cache; the existing
    # production finalist mapping cache is read-only here.
    mapped_symbols = {
        str(item.get("symbol") or "")
        for item in mappings
        if item.get("symbol")
    }
    lookup_budget = int(
        getattr(settings, "opip_event_mapping_lookups_per_capture", 1)
    )
    unresolved_assets = [
        item
        for item in assets
        if item.get("symbol") not in mapped_symbols
    ]
    cmc_requests_this_capture = 0
    if cmc_client is not None and lookup_budget > 0:
        for asset in unresolved_assets[:lookup_budget]:
            if cmc_requests_this_capture:
                sleep(1.05)
            telemetry.coinmarketcal_mapping_requests += 1
            cmc_requests_this_capture += 1
            try:
                rows = cmc_client.get_coins(str(asset["symbol"]))
            except CoinMarketCalAPIError:
                telemetry.provider_errors += 1
                logger.warning(
                    "O'Pip CoinMarketCal identity lookup unavailable for %s",
                    asset.get("symbol"),
                )
                continue

            learned_at = datetime.now(timezone.utc)
            mapping = resolve_coinmarketcal_identity_mapping(
                asset,
                rows,
                resolved_at=learned_at,
            )
            if mapping is None:
                continue
            if not save_event_coinmarketcal_mapping(
                mapping,
                path=coinmarketcal_cache_path,
            ):
                telemetry.storage_errors += 1
                continue
            mappings = _mappings_for_safe_assets(
                merge_point_in_time_mappings(
                    as_of=datetime.now(timezone.utc),
                    paths=(
                        coinmarketcal_cache_path,
                        legacy_coinmarketcal_cache_path,
                    ),
                ),
                assets,
            )
            mapped_symbols.add(str(asset["symbol"]))

    slugs = sorted(
        {
            item["coinmarketcal_slug"]
            for item in mappings
            if item.get("coinmarketcal_slug")
        }
    )
    catalyst_rows: list[dict[str, Any]] = []
    if cmc_client is not None and slugs:
        # Pace every CoinMarketCal request, including multiple mapping lookups
        # or multiple event batches, so increasing the bounded lookup budget
        # cannot accidentally create a provider burst.
        try:
            for batch_slugs in _chunks(
                slugs,
                COINMARKETCAL_BATCH_SIZE,
            ):
                if cmc_requests_this_capture:
                    sleep(1.05)
                telemetry.coinmarketcal_requests += 1
                cmc_requests_this_capture += 1
                rows = cmc_client.get_events(
                    batch_slugs,
                    capture_started,
                    capture_started + timedelta(days=7),
                )
                catalyst_rows.extend(
                    row for row in rows if isinstance(row, dict)
                )
        except CoinMarketCalAPIError:
            telemetry.provider_errors += 1
            logger.warning(
                "O'Pip CoinMarketCal event ingestion unavailable"
            )

    telemetry.events_received += len(catalyst_rows)
    if catalyst_rows:
        catalyst_ingest_at = datetime.now(timezone.utc)
        try:
            # Only mappings actually learned by event receipt can resolve this
            # batch; this includes safe legacy mappings and any shadow mapping
            # learned earlier in this same capture.
            visible_mappings = _mappings_for_safe_assets(
                merge_point_in_time_mappings(
                    as_of=catalyst_ingest_at,
                    paths=(
                        coinmarketcal_cache_path,
                        legacy_coinmarketcal_cache_path,
                    ),
                ),
                assets,
            )
            batch = normalize_coinmarketcal_events(
                catalyst_rows,
                ingest_time=catalyst_ingest_at,
                mappings=visible_mappings,
                normalized_at=datetime.now(timezone.utc),
            )
            _record_adapter_result(
                batch,
                store=active_store,
                telemetry=telemetry,
                observed_at=catalyst_ingest_at,
            )
        except Exception:
            telemetry.normalization_errors += 1
            logger.exception(
                "O'Pip CoinMarketCal event normalization failed open"
            )

    return ExternalEventCaptureResult(
        enabled=True,
        ran=True,
        telemetry=telemetry.as_dict(),
    )
