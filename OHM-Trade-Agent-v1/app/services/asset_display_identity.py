from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.exchanges.kraken_identity import canonicalize_asset, canonicalize_pair
from app.services.registry_io import RegistryIOError, load_json, registry_lock, save_json_atomic


REGISTRY_FILE = Path("/app/data/asset_identity_registry.json")
SUPPORTED_QUOTES = ("USDT", "USD")
VERIFIED_MAPPING_STATUSES = {"UNIQUE", "PRICE_DISAMBIGUATED"}


@dataclass(frozen=True)
class AssetDisplayIdentity:
    base_asset: str
    pair: str
    display_name: str | None
    source: str | None = None
    source_id: str | None = None
    learned_at_utc: str | None = None

    @property
    def asset_text(self) -> str:
        if self.display_name:
            return f"{self.display_name} ({self.base_asset})"
        return self.base_asset

    @property
    def label(self) -> str:
        if self.display_name and self.pair and self.pair != self.base_asset:
            return f"{self.asset_text} — {self.pair}"
        if self.pair:
            return self.pair
        return self.asset_text


def _pair(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    return canonicalize_pair(raw) if raw else ""


def _base(value: str | None, pair: str = "") -> str:
    raw = canonicalize_asset(str(value or "").strip().upper())
    if raw:
        return raw
    market = _pair(pair)
    for quote in SUPPORTED_QUOTES:
        if market.endswith(quote) and len(market) > len(quote):
            return canonicalize_asset(market[: -len(quote)])
    return canonicalize_asset(market)


def _payload(path: Path) -> dict[str, Any]:
    try:
        value = load_json(path)
    except (OSError, TimeoutError, RegistryIOError):
        return {}
    return value if isinstance(value, dict) else {}


def learn_verified_identity(
    *,
    base_asset: str,
    pair: str,
    display_name: str,
    source: str,
    source_id: str,
    path: Path = REGISTRY_FILE,
    learned_at: datetime | None = None,
) -> bool:
    """Persist a presentation-only identity that was resolved externally.

    Pair identity is authoritative. The base-asset shortcut is used only while
    every verified pair for that ticker points to the same external identity.
    Conflicts make the base shortcut ambiguous rather than silently relabeling
    future alerts.
    """
    market = _pair(pair)
    base = _base(base_asset, market)
    name = " ".join(str(display_name or "").split()).strip()
    provider = str(source or "").strip().upper()
    provider_id = str(source_id or "").strip()
    if not market or not base or not name or not provider or not provider_id:
        return False

    learned = learned_at or datetime.now(timezone.utc)
    if learned.tzinfo is None or learned.utcoffset() is None:
        return False
    learned_iso = learned.astimezone(timezone.utc).isoformat()

    lock = path.parent / f".{path.name}.lock"
    try:
        with registry_lock(lock):
            payload = _payload(path)
            pairs = payload.get("pairs")
            assets = payload.get("assets")
            if not isinstance(pairs, dict):
                pairs = {}
            if not isinstance(assets, dict):
                assets = {}

            existing_pair = pairs.get(market)
            if isinstance(existing_pair, dict):
                old_id = str(existing_pair.get("source_id") or "")
                if old_id and old_id != provider_id:
                    return False

            pair_learned_at = learned_iso
            if isinstance(existing_pair, dict):
                pair_learned_at = str(
                    existing_pair.get("learned_at_utc") or learned_iso
                )
            pairs[market] = {
                "base_asset": base,
                "display_name": name,
                "source": provider,
                "source_id": provider_id,
                "learned_at_utc": pair_learned_at,
            }

            existing_asset = assets.get(base)
            if isinstance(existing_asset, dict):
                old_id = str(existing_asset.get("source_id") or "")
                ambiguous = bool(existing_asset.get("ambiguous"))
                pair_list = {_pair(item) for item in (existing_asset.get("pairs") or []) if _pair(item)}
                pair_list.add(market)
                if old_id and old_id != provider_id:
                    assets[base] = {
                        "ambiguous": True,
                        "pairs": sorted(pair_list),
                        "ambiguous_since_utc": learned_iso,
                    }
                elif not ambiguous:
                    asset_learned_at = str(
                        existing_asset.get("learned_at_utc") or learned_iso
                    )
                    assets[base] = {
                        "display_name": name,
                        "source": provider,
                        "source_id": provider_id,
                        "ambiguous": False,
                        "pairs": sorted(pair_list),
                        "learned_at_utc": asset_learned_at,
                    }
            else:
                assets[base] = {
                    "display_name": name,
                    "source": provider,
                    "source_id": provider_id,
                    "ambiguous": False,
                    "pairs": [market],
                    "learned_at_utc": learned_iso,
                }

            payload["pairs"] = pairs
            payload["assets"] = assets
            save_json_atomic(path, payload)
        return True
    except (OSError, TimeoutError, RegistryIOError):
        return False


def learn_candidate_identity(candidate: Any, *, path: Path = REGISTRY_FILE) -> bool:
    reference = getattr(candidate, "independent_market_reference", None)
    if reference is None:
        return False
    mapping = str(getattr(reference, "mapping_status", "") or "").upper()
    if mapping not in VERIFIED_MAPPING_STATUSES:
        return False
    name = str(getattr(reference, "coingecko_name", "") or "").strip()
    source_id = str(getattr(reference, "coingecko_id", "") or "").strip()
    pair = str(getattr(candidate, "primary_pair", "") or getattr(candidate, "symbol", "") or "")
    base = str(getattr(candidate, "underlying_asset", "") or "")
    return learn_verified_identity(
        base_asset=base,
        pair=pair,
        display_name=name,
        source="COINGECKO",
        source_id=source_id,
        path=path,
    )


def resolve_asset_identity(
    *,
    symbol: str | None = None,
    base_asset: str | None = None,
    pair: str | None = None,
    path: Path = REGISTRY_FILE,
) -> AssetDisplayIdentity:
    market = _pair(pair or symbol)
    base = _base(base_asset, market or str(symbol or ""))
    payload = _payload(path)
    pairs = payload.get("pairs")
    assets = payload.get("assets")
    pair_row = pairs.get(market) if isinstance(pairs, dict) and market else None
    if isinstance(pair_row, dict):
        row_base = _base(str(pair_row.get("base_asset") or base), market)
        return AssetDisplayIdentity(
            base_asset=row_base or base or market or "UNKNOWN",
            pair=market,
            display_name=str(pair_row.get("display_name") or "") or None,
            source=str(pair_row.get("source") or "") or None,
            source_id=str(pair_row.get("source_id") or "") or None,
            learned_at_utc=str(pair_row.get("learned_at_utc") or "") or None,
        )

    asset_row = assets.get(base) if isinstance(assets, dict) and base else None
    if isinstance(asset_row, dict) and not bool(asset_row.get("ambiguous")):
        return AssetDisplayIdentity(
            base_asset=base,
            pair=market,
            display_name=str(asset_row.get("display_name") or "") or None,
            source=str(asset_row.get("source") or "") or None,
            source_id=str(asset_row.get("source_id") or "") or None,
            learned_at_utc=str(asset_row.get("learned_at_utc") or "") or None,
        )

    return AssetDisplayIdentity(
        base_asset=base or market or "UNKNOWN",
        pair=market,
        display_name=None,
    )


def display_asset_text(
    symbol: str | None = None,
    *,
    base_asset: str | None = None,
    pair: str | None = None,
    path: Path = REGISTRY_FILE,
) -> str:
    return resolve_asset_identity(
        symbol=symbol,
        base_asset=base_asset,
        pair=pair,
        path=path,
    ).asset_text


def display_market_label(
    symbol: str | None = None,
    *,
    base_asset: str | None = None,
    pair: str | None = None,
    path: Path = REGISTRY_FILE,
) -> str:
    return resolve_asset_identity(
        symbol=symbol,
        base_asset=base_asset,
        pair=pair,
        path=path,
    ).label
