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
    """Merge safe mapping caches without silently accepting identity conflicts."""
    by_symbol: dict[str, dict[str, str]] = {}
    conflicts: set[str] = set()
    for path in paths:
        for item in known_coinmarketcal_mappings(as_of=as_of, path=path):
            symbol = str(item.get("symbol") or "")
            if not symbol or symbol in conflicts:
                continue
            existing = by_symbol.get(symbol)
            if existing is None:
                by_symbol[symbol] = dict(item)
                continue

            same_identity = (
                str(existing.get("canonical_asset_id") or "")
                == str(item.get("canonical_asset_id") or "")
                and str(existing.get("coinmarketcal_slug") or "")
                == str(item.get("coinmarketcal_slug") or "")
            )
            if not same_identity:
                conflicts.add(symbol)
                by_symbol.pop(symbol, None)
                continue

            # Keep the earliest proven resolution time for an identical mapping.
            if str(item.get("resolved_at_utc") or "") < str(
                existing.get("resolved_at_utc") or ""
            ):
                by_symbol[symbol] = dict(item)

    return tuple(by_symbol[key] for key in sorted(by_symbol))


def normalize_chain_id(value: str | None) -> str:
    return re.sub(r"[^a-z0-9:_-]", "", str(value or "").casefold())


def normalize_contract_address(value: str | None) -> str:
    raw = str(value or "").strip()
    # EVM hex addresses are case-insensitive for identity purposes; checksum
    # casing is presentation/validation metadata. Other chains (for example
    # Solana base58) may be case-sensitive, so preserve exact casing.
    if re.fullmatch(r"0x[0-9a-fA-F]{40}", raw):
        return raw.lower()
    return raw


def _binding_learned_at(
    row: dict[str, Any],
    *,
    as_of: datetime,
) -> datetime | None:
    learned = _learned_at(row)
    if learned is None or learned > as_of:
        return None
    return learned


def learn_identity_binding(
    *,
    canonical_symbol: str,
    binding_type: str,
    learned_at: datetime,
    path: Path = ASSET_IDENTITY_REGISTRY,
    alias: str | None = None,
    venue: str | None = None,
    venue_symbol: str | None = None,
    instrument_type: str | None = None,
    chain_id: str | None = None,
    contract_address: str | None = None,
) -> bool:
    """Attach a verified alias/instrument/contract to an existing asset."""
    symbol = normalize_symbol(canonical_symbol)
    learned = require_utc(learned_at, field_name="learned_at")
    kind = str(binding_type or "").strip().upper()
    if kind not in {"ALIAS", "VENUE_INSTRUMENT", "ONCHAIN"}:
        return False

    if kind == "ALIAS":
        normalized_alias = normalize_symbol(alias)
        if not normalized_alias:
            return False
        binding = {
            "alias": normalized_alias,
            "learned_at_utc": learned.isoformat(),
        }
        collection = "identity_aliases"
        unique_key = ("alias",)
    elif kind == "VENUE_INSTRUMENT":
        normalized_venue = str(venue or "").strip().upper()
        normalized_venue_symbol = str(venue_symbol or "").strip().upper()
        normalized_type = str(instrument_type or "").strip().upper()
        if not normalized_venue or not normalized_venue_symbol or not normalized_type:
            return False
        binding = {
            "venue": normalized_venue,
            "venue_symbol": normalized_venue_symbol,
            "instrument_type": normalized_type,
            "learned_at_utc": learned.isoformat(),
        }
        collection = "venue_instruments"
        unique_key = ("venue", "venue_symbol", "instrument_type")
    else:
        normalized_chain = normalize_chain_id(chain_id)
        normalized_contract = normalize_contract_address(contract_address)
        if not normalized_chain or not normalized_contract:
            return False
        binding = {
            "chain_id": normalized_chain,
            "contract_address": normalized_contract,
            "learned_at_utc": learned.isoformat(),
        }
        collection = "onchain_contracts"
        unique_key = ("chain_id", "contract_address")

    lock = path.parent / f".{path.name}.lock"
    try:
        with registry_lock(lock):
            payload = _safe_payload(path)
            assets = payload.get("assets")
            if not isinstance(assets, dict):
                return False
            asset = assets.get(symbol)
            if not isinstance(asset, dict) or bool(asset.get("ambiguous")):
                return False
            if (
                not str(asset.get("source_id") or "").strip()
                or not str(asset.get("display_name") or "").strip()
                or _learned_at(asset) is None
            ):
                return False

            rows = asset.get(collection)
            if not isinstance(rows, list):
                rows = []
            for existing in rows:
                if not isinstance(existing, dict):
                    continue
                if all(
                    str(existing.get(key) or "") == str(binding.get(key) or "")
                    for key in unique_key
                ):
                    existing_learned = _learned_at(existing)
                    if existing_learned is None or learned < existing_learned:
                        existing["learned_at_utc"] = learned.isoformat()
                        save_json_atomic(path, payload)
                    return True

            rows.append(binding)
            asset[collection] = rows
            save_json_atomic(path, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


def resolve_structured_identity(
    *,
    as_of: datetime,
    source_symbol: str | None = None,
    source_name: str | None = None,
    provider_asset_id: str | None = None,
    venue: str | None = None,
    venue_symbol: str | None = None,
    instrument_type: str | None = None,
    chain_id: str | None = None,
    contract_address: str | None = None,
    path: Path = ASSET_IDENTITY_REGISTRY,
) -> EventIdentity:
    """Resolve timestamped aliases, venue instruments, or on-chain contracts."""
    cutoff = require_utc(as_of, field_name="as_of")
    base = resolve_registry_identity(
        source_symbol=source_symbol,
        source_name=source_name,
        provider_asset_id=provider_asset_id,
        as_of=cutoff,
        path=path,
    )
    has_venue_identity = bool(
        str(venue or "").strip() or str(venue_symbol or "").strip()
    )
    has_onchain_identity = bool(
        str(chain_id or "").strip() or str(contract_address or "").strip()
    )
    if has_venue_identity and not (
        str(venue or "").strip() and str(venue_symbol or "").strip()
    ):
        return EventIdentity(
            source_symbol=source_symbol,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:incomplete_venue_identity",
        )
    if has_onchain_identity and not (
        str(chain_id or "").strip() and str(contract_address or "").strip()
    ):
        return EventIdentity(
            source_symbol=source_symbol,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:incomplete_onchain_identity",
        )
    if (
        base.mapping_status == MappingStatus.UNIQUE
        and not has_venue_identity
        and not has_onchain_identity
    ):
        return base

    payload = _safe_payload(path)
    assets = payload.get("assets")
    if not isinstance(assets, dict):
        return base

    wanted_alias = normalize_symbol(source_symbol)
    wanted_venue = str(venue or "").strip().upper()
    wanted_venue_symbol = str(venue_symbol or "").strip().upper()
    wanted_instrument_type = str(instrument_type or "").strip().upper()
    wanted_chain = normalize_chain_id(chain_id)
    wanted_contract = normalize_contract_address(contract_address)

    matches: list[tuple[str, dict[str, Any], datetime, str]] = []
    for raw_symbol, asset in assets.items():
        if not isinstance(asset, dict) or bool(asset.get("ambiguous")):
            continue
        asset_learned = _binding_learned_at(asset, as_of=cutoff)
        if asset_learned is None:
            continue
        canonical_id = str(asset.get("source_id") or "").strip()
        canonical_name = str(asset.get("display_name") or "").strip()
        if not canonical_id or not canonical_name:
            continue

        binding_candidates: list[tuple[dict[str, Any], str]] = []
        if wanted_alias:
            for row in asset.get("identity_aliases") or []:
                if (
                    isinstance(row, dict)
                    and normalize_symbol(row.get("alias")) == wanted_alias
                ):
                    binding_candidates.append((row, "verified_alias"))
        if wanted_venue and wanted_venue_symbol:
            for row in asset.get("venue_instruments") or []:
                if not isinstance(row, dict):
                    continue
                if (
                    str(row.get("venue") or "").upper() == wanted_venue
                    and str(row.get("venue_symbol") or "").upper()
                    == wanted_venue_symbol
                    and (
                        not wanted_instrument_type
                        or str(row.get("instrument_type") or "").upper()
                        == wanted_instrument_type
                    )
                ):
                    binding_candidates.append((row, "verified_venue_instrument"))
        if wanted_chain and wanted_contract:
            for row in asset.get("onchain_contracts") or []:
                if not isinstance(row, dict):
                    continue
                if (
                    normalize_chain_id(row.get("chain_id")) == wanted_chain
                    and normalize_contract_address(row.get("contract_address"))
                    == wanted_contract
                ):
                    binding_candidates.append((row, "verified_onchain_contract"))

        for binding, provenance in binding_candidates:
            binding_learned = _binding_learned_at(binding, as_of=cutoff)
            if binding_learned is None:
                continue
            matches.append(
                (
                    normalize_symbol(str(raw_symbol)),
                    asset,
                    max(asset_learned, binding_learned),
                    provenance,
                )
            )

    canonical_matches = {
        str(asset.get("source_id") or "")
        for _, asset, _, _ in matches
        if str(asset.get("source_id") or "")
    }
    if len(canonical_matches) > 1:
        return EventIdentity(
            source_symbol=wanted_alias or source_symbol,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.AMBIGUOUS,
            venue=wanted_venue or None,
            venue_symbol=wanted_venue_symbol or None,
            instrument_type=wanted_instrument_type or None,
            chain_id=wanted_chain or None,
            contract_address=wanted_contract or None,
            mapping_provenance="asset_identity_registry:structured_collision",
        )
    if len(matches) != 1:
        return EventIdentity(
            source_symbol=wanted_alias or source_symbol,
            source_name=source_name,
            provider_asset_id=provider_asset_id,
            mapping_status=MappingStatus.UNKNOWN,
            venue=wanted_venue or None,
            venue_symbol=wanted_venue_symbol or None,
            instrument_type=wanted_instrument_type or None,
            chain_id=wanted_chain or None,
            contract_address=wanted_contract or None,
            mapping_provenance="asset_identity_registry:structured_unresolved",
        )

    canonical_symbol, asset, learned, provenance = matches[0]
    aliases = tuple(
        sorted(
            {
                normalize_symbol(item.get("alias"))
                for item in (asset.get("identity_aliases") or [])
                if isinstance(item, dict)
                and _binding_learned_at(item, as_of=cutoff) is not None
                and normalize_symbol(item.get("alias"))
            }
        )
    )
    return EventIdentity(
        source_symbol=wanted_alias or canonical_symbol,
        source_name=source_name,
        provider_asset_id=provider_asset_id,
        canonical_asset_id=str(asset.get("source_id") or ""),
        canonical_asset_name=str(asset.get("display_name") or ""),
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=1.0,
        identity_learned_at_utc=learned,
        mapping_provenance=f"asset_identity_registry:{provenance}",
        venue=wanted_venue or None,
        venue_symbol=wanted_venue_symbol or None,
        instrument_type=wanted_instrument_type or None,
        chain_id=wanted_chain or None,
        contract_address=wanted_contract or None,
        aliases=aliases,
    )


def resolve_news_mention(
    text: str,
    *,
    as_of: datetime,
    path: Path = ASSET_IDENTITY_REGISTRY,
) -> EventIdentity:
    """Resolve free text only when exactly one known asset matches."""
    cutoff = require_utc(as_of, field_name="as_of")
    raw_text = str(text or "")
    lowered = raw_text.casefold()
    payload = _safe_payload(path)
    assets = payload.get("assets")
    if not isinstance(assets, dict) or not raw_text.strip():
        return EventIdentity(
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:text_unresolved",
        )

    matches: list[tuple[str, dict[str, Any], datetime]] = []
    for raw_symbol, asset in assets.items():
        if not isinstance(asset, dict) or bool(asset.get("ambiguous")):
            continue
        learned = _binding_learned_at(asset, as_of=cutoff)
        if learned is None:
            continue
        canonical_id = str(asset.get("source_id") or "").strip()
        canonical_name = str(asset.get("display_name") or "").strip()
        if not canonical_id or not canonical_name:
            continue

        phrases: set[str] = set()
        if len(canonical_name) >= 4:
            phrases.add(canonical_name.casefold())
        symbol = normalize_symbol(str(raw_symbol))
        symbol_patterns: list[str] = []
        if symbol:
            symbol_patterns.extend(
                [
                    rf"\${re.escape(symbol)}\b",
                    rf"\({re.escape(symbol)}\)",
                ]
            )
            if len(symbol) >= 4:
                symbol_patterns.append(rf"\b{re.escape(symbol)}\b")

        binding_times = [learned]
        for row in asset.get("identity_aliases") or []:
            if not isinstance(row, dict):
                continue
            alias_learned = _binding_learned_at(row, as_of=cutoff)
            alias = normalize_symbol(row.get("alias"))
            if alias_learned is None or not alias:
                continue
            binding_times.append(alias_learned)
            symbol_patterns.extend(
                [
                    rf"\${re.escape(alias)}\b",
                    rf"\({re.escape(alias)}\)",
                ]
            )
            if len(alias) >= 4:
                symbol_patterns.append(rf"\b{re.escape(alias)}\b")

        phrase_match = any(
            re.search(rf"\b{re.escape(phrase)}\b", lowered)
            for phrase in phrases
        )
        symbol_match = any(
            re.search(pattern, raw_text, flags=re.IGNORECASE)
            for pattern in symbol_patterns
        )
        if phrase_match or symbol_match:
            matches.append((symbol, asset, max(binding_times)))

    canonical_ids = {
        str(asset.get("source_id") or "")
        for _, asset, _ in matches
        if str(asset.get("source_id") or "")
    }
    if len(canonical_ids) > 1:
        return EventIdentity(
            mapping_status=MappingStatus.AMBIGUOUS,
            mapping_provenance="asset_identity_registry:text_collision",
        )
    if len(matches) != 1:
        return EventIdentity(
            mapping_status=MappingStatus.UNKNOWN,
            mapping_provenance="asset_identity_registry:text_unresolved",
        )

    symbol, asset, learned = matches[0]
    return EventIdentity(
        source_symbol=symbol,
        source_name=str(asset.get("display_name") or ""),
        provider_asset_id=str(asset.get("source_id") or ""),
        canonical_asset_id=str(asset.get("source_id") or ""),
        canonical_asset_name=str(asset.get("display_name") or ""),
        mapping_status=MappingStatus.UNIQUE,
        mapping_confidence=0.95,
        identity_learned_at_utc=learned,
        mapping_provenance="asset_identity_registry:verified_text_mention",
    )
