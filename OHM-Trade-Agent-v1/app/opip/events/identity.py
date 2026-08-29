"""Point-in-time-safe asset identity for O'Pip Event Intelligence.

Ticker-only attribution is never treated as canonical identity. Learned
registry mappings are eligible only when their learned timestamp proves that
O'Pip knew the mapping by the event ingestion time being evaluated.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any

from app.opip.events.contract import EventIdentity, MappingStatus, parse_utc, require_utc
from app.services.registry_io import (
    RegistryIOError,
    load_json,
    registry_lock,
    save_json_atomic,
)


ASSET_IDENTITY_REGISTRY = Path("/app/data/asset_identity_registry.json")
LEGACY_COINMARKETCAL_MAPPING_CACHE = Path("/app/data/coinmarketcal_coin_map.json")
EVENT_COINMARKETCAL_MAPPING_CACHE = Path(
    "/app/data/opip/events/coinmarketcal_identity_map.json"
)
# Backward-compatible name inside the Sequence 2 package. New shadow mappings
# are kept separate from the current finalist-oriented production cache so
# evidence collection cannot silently alter today's catalyst decision path.
COINMARKETCAL_MAPPING_CACHE = EVENT_COINMARKETCAL_MAPPING_CACHE
SYMBOL_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


def normalize_symbol(value: str | None) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return SYMBOL_ALIASES.get(raw, raw)


def normalize_identity_text(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _safe_payload(path: Path) -> dict[str, Any]:
    try:
        payload = load_json(path)
    except (OSError, TimeoutError, RegistryIOError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _learned_at(row: dict[str, Any], key: str = "learned_at_utc") -> datetime | None:
    raw = row.get(key)
    if raw is None:
        return None
    try:
        return parse_utc(str(raw), field_name=key)
    except ValueError:
        return None


def resolve_registry_identity(
    *,
    source_symbol: str | None,
    source_name: str | None,
    provider_asset_id: str | None,
    as_of: datetime,
    path: Path = ASSET_IDENTITY_REGISTRY,
) -> EventIdentity:
    """Resolve a provider instrument without retroactive identity leakage."""
    decision_time = require_utc(as_of, field_name="as_of")
    symbol = normalize_symbol(source_symbol)
    payload = _safe_payload(path)
    assets = payload.get("assets")
    row = assets.get(symbol) if isinstance(assets, dict) and symbol else None

    if not isinstance(row, dict):
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:missing",
        )

    if bool(row.get("ambiguous")):
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.AMBIGUOUS,
            mapping_provenance="asset_identity_registry:ambiguous",
        )

    learned = _learned_at(row)
    if learned is None:
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:timestamp_unavailable",
        )
    if learned > decision_time:
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            identity_learned_at_utc=learned,
            mapping_provenance="asset_identity_registry:learned_after_event",
        )

    canonical_id = str(row.get("source_id") or "").strip()
    canonical_name = str(row.get("display_name") or "").strip()
    if not canonical_id or not canonical_name:
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            identity_learned_at_utc=learned,
            mapping_provenance="asset_identity_registry:incomplete",
        )

    provider_id_match = bool(
        provider_asset_id
        and normalize_identity_text(provider_asset_id)
        == normalize_identity_text(canonical_id)
    )
    provider_name_match = bool(
        source_name
        and normalize_identity_text(source_name)
        == normalize_identity_text(canonical_name)
    )
    if not (provider_id_match or provider_name_match):
        return EventIdentity(
            source_symbol=symbol or None,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            identity_learned_at_utc=learned,
            mapping_provenance="asset_identity_registry:provider_mismatch",
        )

    return EventIdentity(
        source_symbol=symbol or None,
        source_name=source_name,
        provider_asset_id=provider_asset_id,
        canonical_asset_id=canonical_id,
        canonical_asset_name=canonical_name,
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=1.0,
        identity_learned_at_utc=learned,
        mapping_provenance="asset_identity_registry:verified_external_identity",
    )


def known_unique_assets(
    *,
    as_of: datetime,
    path: Path = ASSET_IDENTITY_REGISTRY,
) -> tuple[dict[str, str], ...]:
    """Return identities that were safely known by as_of.

    Rows without learned_at_utc are intentionally excluded: historical state
    from before Sequence 2 cannot be assigned a trustworthy knowledge time.
    """
    decision_time = require_utc(as_of, field_name="as_of")
    payload = _safe_payload(path)
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        return ()

    result: list[dict[str, str]] = []
    for raw_symbol, raw_row in assets.items():
        if not isinstance(raw_row, dict) or bool(raw_row.get("ambiguous")):
            continue
        learned = _learned_at(raw_row)
        if learned is None or learned > decision_time:
            continue
        symbol = normalize_symbol(str(raw_symbol))
        source_id = str(raw_row.get("source_id") or "").strip()
        name = str(raw_row.get("display_name") or "").strip()
        if not symbol or not source_id or not name:
            continue
        result.append(
            {
                "symbol": symbol,
                "canonical_asset_id": source_id,
                "canonical_asset_name": name,
                "learned_at_utc": learned.isoformat(),
            }
        )
    result.sort(key=lambda item: (item["symbol"], item["canonical_asset_id"]))
    return tuple(result)


def known_coinmarketcal_mappings(
    *,
    as_of: datetime,
    path: Path = COINMARKETCAL_MAPPING_CACHE,
) -> tuple[dict[str, str], ...]:
    """Return CoinMarketCal mappings whose resolved_at was already known."""
    decision_time = require_utc(as_of, field_name="as_of")
    payload = _safe_payload(path)
    mappings = payload.get("mappings")
    if not isinstance(mappings, dict):
        return ()

    result: list[dict[str, str]] = []
    for raw in mappings.values():
        if not isinstance(raw, dict):
            continue
        try:
            resolved = parse_utc(
                str(raw.get("resolved_at") or ""),
                field_name="resolved_at",
            )
        except ValueError:
            continue
        if resolved is None or resolved > decision_time:
            continue
        symbol = normalize_symbol(str(raw.get("underlying_symbol") or ""))
        coingecko_id = str(raw.get("coingecko_id") or "").strip()
        coingecko_name = str(raw.get("coingecko_name") or "").strip()
        slug = str(raw.get("coinmarketcal_slug") or "").strip()
        cmc_name = str(raw.get("coinmarketcal_name") or "").strip()
        if not symbol or not coingecko_id or not coingecko_name or not slug:
            continue
        result.append(
            {
                "symbol": symbol,
                "canonical_asset_id": coingecko_id,
                "canonical_asset_name": coingecko_name,
                "coinmarketcal_slug": slug,
                "coinmarketcal_name": cmc_name,
                "resolved_at_utc": resolved.isoformat(),
            }
        )
    result.sort(key=lambda item: (item["symbol"], item["coinmarketcal_slug"]))
    return tuple(result)



def resolve_coinmarketcal_identity_mapping(
    asset: dict[str, str],
    rows: list[dict[str, Any]],
    *,
    resolved_at: datetime,
) -> dict[str, str] | None:
    """Resolve exactly one CoinMarketCal coin against verified CoinGecko identity."""
    learned = require_utc(resolved_at, field_name="resolved_at")
    symbol = normalize_symbol(asset.get("symbol"))
    canonical_id = str(asset.get("canonical_asset_id") or "").strip()
    canonical_name = str(asset.get("canonical_asset_name") or "").strip()
    if not symbol or not canonical_id or not canonical_name:
        return None

    plausible: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_symbol = normalize_symbol(str(row.get("symbol") or ""))
        row_name = str(row.get("name") or "").strip()
        row_slug = str(row.get("slug") or "").strip()
        identity_match = (
            normalize_identity_text(row_name)
            == normalize_identity_text(canonical_name)
            or normalize_identity_text(row_slug)
            == normalize_identity_text(canonical_id)
        )
        if row_symbol == symbol and row_slug and identity_match:
            plausible.append(row)

    if len(plausible) != 1:
        return None

    selected = plausible[0]
    return {
        "underlying_symbol": symbol,
        "coingecko_id": canonical_id,
        "coingecko_name": canonical_name,
        "coinmarketcal_slug": str(selected.get("slug") or "").strip(),
        "coinmarketcal_name": str(selected.get("name") or "").strip(),
        "coinmarketcal_symbol": normalize_symbol(
            str(selected.get("symbol") or "")
        ),
        "resolved_at": learned.isoformat(),
    }


def save_event_coinmarketcal_mapping(
    mapping: dict[str, str],
    *,
    path: Path = EVENT_COINMARKETCAL_MAPPING_CACHE,
) -> bool:
    """Persist an identity-safe shadow mapping without touching production cache."""
    symbol = normalize_symbol(mapping.get("underlying_symbol"))
    required = (
        "coingecko_id",
        "coingecko_name",
        "coinmarketcal_slug",
        "coinmarketcal_name",
        "coinmarketcal_symbol",
        "resolved_at",
    )
    if not symbol or not all(str(mapping.get(key) or "").strip() for key in required):
        return False
    try:
        parse_utc(str(mapping["resolved_at"]), field_name="resolved_at")
    except ValueError:
        return False

    lock = path.parent / f".{path.name}.lock"
    try:
        with registry_lock(lock):
            payload = _safe_payload(path)
            stored = payload.get("mappings")
            if not isinstance(stored, dict):
                stored = {}
            existing = stored.get(symbol)
            if isinstance(existing, dict):
                existing_id = str(existing.get("coingecko_id") or "").strip()
                existing_slug = str(existing.get("coinmarketcal_slug") or "").strip()
                if (
                    existing_id
                    and existing_id != str(mapping["coingecko_id"])
                ) or (
                    existing_slug
                    and existing_slug != str(mapping["coinmarketcal_slug"])
                ):
                    # A conflict is not silently reassigned. The next operator
                    # review can inspect provider identity evidence.
                    return False
                first_resolved = str(
                    existing.get("resolved_at") or mapping["resolved_at"]
                )
            else:
                first_resolved = str(mapping["resolved_at"])

            row = dict(mapping)
            row["underlying_symbol"] = symbol
            row["resolved_at"] = first_resolved
            stored[symbol] = row
            payload["mappings"] = stored
            save_json_atomic(path, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


def merge_point_in_time_mappings(
    *,
    as_of: datetime,
    paths: tuple[Path, ...],
) -> tuple[dict[str, str], ...]:
    """Merge safe mapping caches without allowing later rows to leak backward."""
    combined: dict[tuple[str, str], dict[str, str]] = {}
    for path in paths:
        for item in known_coinmarketcal_mappings(as_of=as_of, path=path):
            key = (
                str(item.get("symbol") or ""),
                str(item.get("coinmarketcal_slug") or ""),
            )
            existing = combined.get(key)
            if existing is None:
                combined[key] = dict(item)
                continue
            # Keep the earliest proven resolution time for identical mappings.
            if str(item.get("resolved_at_utc") or "") < str(
                existing.get("resolved_at_utc") or ""
            ):
                combined[key] = dict(item)
    return tuple(
        combined[key]
        for key in sorted(combined)
    )
