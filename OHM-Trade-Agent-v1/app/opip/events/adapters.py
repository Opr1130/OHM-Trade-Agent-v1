"""Provider-normalization adapters for discrete external intelligence.

The adapters consume provider payloads and emit canonical evidence. They never
call exchanges, notification services, qualification gates, or AI models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from app.opip.events.contract import (
    EventClass,
    EventIdentity,
    IngestOutcome,
    MappingStatus,
    OPipEvent,
    parse_utc,
    require_utc,
    stable_event_id,
    stable_payload_hash,
)
from app.opip.events.identity import (
    normalize_symbol,
    resolve_registry_identity,
)


CRYPTOPANIC = "CRYPTOPANIC"
COINMARKETCAL = "COINMARKETCAL"


@dataclass(frozen=True)
class AdapterFailure:
    provider: str
    outcome: IngestOutcome
    reason: str
    provider_event_id: str | None = None
    payload_hash: str | None = None


@dataclass(frozen=True)
class AdapterBatchResult:
    events: tuple[OPipEvent, ...]
    failures: tuple[AdapterFailure, ...] = ()


def _string(value: Any, *, limit: int | None = None) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split()).strip()
    if not text:
        return None
    return text[:limit] if limit is not None else text


def _provider_event_id(value: Any) -> str | None:
    text = _string(value, limit=200)
    return text


def _dedupe_key(
    *,
    provider: str,
    provider_event_id: str | None,
    event_class: EventClass,
    asset_key: str,
    source_time: datetime,
    headline: str,
) -> str:
    if provider_event_id:
        return f"{provider}:{event_class.value}:{provider_event_id}:{asset_key}"
    fallback = stable_payload_hash(
        {
            "provider": provider,
            "class": event_class.value,
            "asset": asset_key,
            "source_time": source_time.isoformat(),
            "headline": headline,
        }
    )
    return f"{provider}:{event_class.value}:DERIVED:{fallback}"


def _news_payload(row: dict[str, Any], instrument: dict[str, Any]) -> dict[str, Any]:
    source = row.get("source") if isinstance(row.get("source"), dict) else {}
    votes = row.get("votes") if isinstance(row.get("votes"), dict) else {}
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "description": row.get("description"),
        "published_at": row.get("published_at"),
        "kind": row.get("kind"),
        "source": {
            "title": source.get("title"),
            "domain": source.get("domain"),
        },
        "instrument": {
            "code": instrument.get("code"),
            "title": instrument.get("title"),
            "slug": instrument.get("slug"),
        },
        "votes": votes,
    }


def normalize_cryptopanic_posts(
    posts: Iterable[dict[str, Any]],
    *,
    ingest_time: datetime,
    identity_registry_path=None,
    normalized_at: datetime | None = None,
) -> AdapterBatchResult:
    ingest = require_utc(ingest_time, field_name="ingest_time")
    normalized = require_utc(
        normalized_at or ingest,
        field_name="normalized_at",
    )
    events: list[OPipEvent] = []
    failures: list[AdapterFailure] = []

    for row in posts:
        if not isinstance(row, dict):
            failures.append(
                AdapterFailure(
                    provider=CRYPTOPANIC,
                    outcome=IngestOutcome.MALFORMED_PAYLOAD,
                    reason="post was not an object",
                )
            )
            continue
        provider_id = _provider_event_id(row.get("id"))
        payload_hash_for_error = stable_payload_hash(row)
        try:
            published = parse_utc(
                row.get("published_at"),
                field_name="published_at",
            )
        except ValueError:
            failures.append(
                AdapterFailure(
                    provider=CRYPTOPANIC,
                    outcome=IngestOutcome.INVALID_TIMESTAMP,
                    reason="invalid published_at",
                    provider_event_id=provider_id,
                    payload_hash=payload_hash_for_error,
                )
            )
            continue
        if published is None:
            failures.append(
                AdapterFailure(
                    provider=CRYPTOPANIC,
                    outcome=IngestOutcome.MISSING_TIMESTAMP,
                    reason="missing published_at",
                    provider_event_id=provider_id,
                    payload_hash=payload_hash_for_error,
                )
            )
            continue

        headline = _string(row.get("title"), limit=500) or "Untitled CryptoPanic news"
        description = _string(row.get("description"), limit=2000)
        source = row.get("source") if isinstance(row.get("source"), dict) else {}
        source_domain = _string(source.get("domain"), limit=300)
        instruments = row.get("instruments")
        if not isinstance(instruments, list) or not instruments:
            instruments = [{}]

        for instrument in instruments:
            instrument = instrument if isinstance(instrument, dict) else {}
            source_symbol = normalize_symbol(_string(instrument.get("code"), limit=40))
            source_name = _string(instrument.get("title"), limit=300)
            provider_asset_id = _string(instrument.get("slug"), limit=300)
            identity_kwargs = {
                "source_symbol": source_symbol or None,
                "source_name": source_name,
                "provider_asset_id": provider_asset_id,
                "as_of": ingest,
            }
            if identity_registry_path is not None:
                identity_kwargs["path"] = identity_registry_path
            identity = resolve_registry_identity(**identity_kwargs)

            asset_key = (
                identity.canonical_asset_id
                or provider_asset_id
                or source_symbol
                or "GLOBAL"
            )
            dedupe = _dedupe_key(
                provider=CRYPTOPANIC,
                provider_event_id=provider_id,
                event_class=EventClass.NEWS,
                asset_key=asset_key,
                source_time=published,
                headline=headline,
            )
            canonical_payload = _news_payload(row, instrument)
            payload_hash = stable_payload_hash(canonical_payload)
            events.append(
                OPipEvent(
                    event_id=stable_event_id(dedupe, payload_hash),
                    dedupe_key=dedupe,
                    provider=CRYPTOPANIC,
                    provider_event_id=provider_id,
                    event_class=EventClass.NEWS,
                    payload_hash=payload_hash,
                    source_event_time_utc=published,
                    ingest_time_utc=ingest,
                    normalized_at_utc=normalized,
                    identity=identity,
                    headline=headline,
                    summary=description,
                    source_reference=source_domain,
                    source_metadata={
                        "kind": _string(row.get("kind"), limit=100),
                        "source_title": _string(source.get("title"), limit=300),
                        "source_domain": source_domain,
                        "instrument": {
                            "code": source_symbol or None,
                            "title": source_name,
                            "slug": provider_asset_id,
                        },
                    },
                    numeric={
                        "votes": (
                            dict(row.get("votes"))
                            if isinstance(row.get("votes"), dict)
                            else {}
                        )
                    },
                    expires_at_utc=published + timedelta(hours=24),
                )
            )

    return AdapterBatchResult(tuple(events), tuple(failures))


def _coin_rows(row: dict[str, Any]) -> list[dict[str, Any]]:
    value = row.get("coins")
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _catalyst_identity(
    coin: dict[str, Any],
    mapping_by_slug: dict[str, dict[str, str]],
    *,
    ingest_time: datetime,
) -> EventIdentity:
    slug = _string(coin.get("slug"), limit=300)
    symbol = normalize_symbol(_string(coin.get("symbol"), limit=40))
    name = _string(coin.get("name"), limit=300)
    mapping = mapping_by_slug.get(str(slug or ""))
    if not isinstance(mapping, dict):
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=name,
            provider_asset_id=slug,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="coinmarketcal_mapping_cache:missing",
        )

    try:
        learned = parse_utc(
            mapping.get("resolved_at_utc"),
            field_name="resolved_at_utc",
        )
    except ValueError:
        learned = None
    if learned is None:
        return EventIdentity(
            source_symbol=symbol or mapping.get("symbol"),
            source_name=name or mapping.get("canonical_asset_name"),
            provider_asset_id=slug,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="coinmarketcal_mapping_cache:timestamp_unavailable",
        )
    if learned > ingest_time:
        return EventIdentity(
            source_symbol=symbol or mapping.get("symbol"),
            source_name=name or mapping.get("canonical_asset_name"),
            provider_asset_id=slug,
            mapping_status=MappingStatus.UNKNOWN,
            identity_learned_at_utc=learned,
            mapping_provenance="coinmarketcal_mapping_cache:learned_after_event",
        )

    return EventIdentity(
        source_symbol=symbol or mapping.get("symbol"),
        source_name=name or mapping.get("canonical_asset_name"),
        provider_asset_id=slug,
        canonical_asset_id=mapping.get("canonical_asset_id"),
        canonical_asset_name=mapping.get("canonical_asset_name"),
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=1.0,
        identity_learned_at_utc=learned,
        mapping_provenance="coinmarketcal_mapping_cache:verified_external_identity",
    )


def _catalyst_payload(row: dict[str, Any], coin: dict[str, Any]) -> dict[str, Any]:
    categories = row.get("categories") if isinstance(row.get("categories"), list) else []
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "date": row.get("date"),
        "dateEnd": row.get("dateEnd"),
        "dateType": row.get("dateType"),
        "isEstimated": row.get("isEstimated"),
        "displayedDate": row.get("displayedDate"),
        "categories": categories,
        "coin": {
            "slug": coin.get("slug"),
            "name": coin.get("name"),
            "symbol": coin.get("symbol"),
        },
        "impact": row.get("impact"),
        "impactSummary": row.get("impactSummary"),
    }


def normalize_coinmarketcal_events(
    rows: Iterable[dict[str, Any]],
    *,
    ingest_time: datetime,
    mappings: Iterable[dict[str, str]] = (),
    normalized_at: datetime | None = None,
) -> AdapterBatchResult:
    ingest = require_utc(ingest_time, field_name="ingest_time")
    normalized = require_utc(
        normalized_at or ingest,
        field_name="normalized_at",
    )
    mapping_by_slug = {
        str(item.get("coinmarketcal_slug") or ""): dict(item)
        for item in mappings
        if isinstance(item, dict) and item.get("coinmarketcal_slug")
    }
    events: list[OPipEvent] = []
    failures: list[AdapterFailure] = []

    for row in rows:
        if not isinstance(row, dict):
            failures.append(
                AdapterFailure(
                    provider=COINMARKETCAL,
                    outcome=IngestOutcome.MALFORMED_PAYLOAD,
                    reason="event was not an object",
                )
            )
            continue
        provider_id = _provider_event_id(row.get("id"))
        payload_hash_for_error = stable_payload_hash(row)
        try:
            event_time = parse_utc(row.get("date"), field_name="date")
        except ValueError:
            failures.append(
                AdapterFailure(
                    provider=COINMARKETCAL,
                    outcome=IngestOutcome.INVALID_TIMESTAMP,
                    reason="invalid event date",
                    provider_event_id=provider_id,
                    payload_hash=payload_hash_for_error,
                )
            )
            continue
        if event_time is None:
            failures.append(
                AdapterFailure(
                    provider=COINMARKETCAL,
                    outcome=IngestOutcome.MISSING_TIMESTAMP,
                    reason="missing event date",
                    provider_event_id=provider_id,
                    payload_hash=payload_hash_for_error,
                )
            )
            continue

        headline = _string(row.get("title"), limit=500) or "Untitled CoinMarketCal event"
        summary = _string(row.get("impactSummary"), limit=2000)
        coins = _coin_rows(row) or [{}]
        for coin in coins:
            identity = _catalyst_identity(
                coin,
                mapping_by_slug,
                ingest_time=ingest,
            )
            asset_key = (
                identity.canonical_asset_id
                or identity.provider_asset_id
                or identity.source_symbol
                or "GLOBAL"
            )
            dedupe = _dedupe_key(
                provider=COINMARKETCAL,
                provider_event_id=provider_id,
                event_class=EventClass.CATALYST,
                asset_key=asset_key,
                source_time=event_time,
                headline=headline,
            )
            canonical_payload = _catalyst_payload(row, coin)
            payload_hash = stable_payload_hash(canonical_payload)
            numeric = {
                "impact": row.get("impact"),
                "is_estimated": row.get("isEstimated") is True,
                "date_type": row.get("dateType"),
            }
            events.append(
                OPipEvent(
                    event_id=stable_event_id(dedupe, payload_hash),
                    dedupe_key=dedupe,
                    provider=COINMARKETCAL,
                    provider_event_id=provider_id,
                    event_class=EventClass.CATALYST,
                    payload_hash=payload_hash,
                    source_event_time_utc=event_time,
                    ingest_time_utc=ingest,
                    normalized_at_utc=normalized,
                    identity=identity,
                    headline=headline,
                    summary=summary,
                    source_reference=(
                        f"coinmarketcal:event:{provider_id}"
                        if provider_id
                        else "coinmarketcal:event"
                    ),
                    source_metadata={
                        "date_end": _string(row.get("dateEnd"), limit=100),
                        "date_type": _string(row.get("dateType"), limit=100),
                        "is_estimated": row.get("isEstimated") is True,
                        "displayed_date": _string(
                            row.get("displayedDate"),
                            limit=300,
                        ),
                        "categories": [
                            _string(item.get("name"), limit=200)
                            for item in (
                                row.get("categories")
                                if isinstance(row.get("categories"), list)
                                else []
                            )
                            if isinstance(item, dict) and _string(item.get("name"), limit=200)
                        ],
                        "coin": {
                            "slug": identity.provider_asset_id,
                            "name": identity.source_name,
                            "symbol": identity.source_symbol,
                        },
                    },
                    numeric=numeric,
                    expires_at_utc=event_time + timedelta(hours=24),
                )
            )

    return AdapterBatchResult(tuple(events), tuple(failures))
