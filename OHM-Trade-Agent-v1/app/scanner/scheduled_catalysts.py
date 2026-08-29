from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import time
from typing import Any, Callable

from app.scanner.models import MarketSnapshot
from app.scanner.news_context import candidate_symbol
from app.services.coinmarketcal import CoinMarketCalAPIError, CoinMarketCalClient
from app.services.registry_io import load_json, registry_lock, save_json_atomic


AVAILABLE = "AVAILABLE"
NO_UPCOMING_EVENTS = "NO_UPCOMING_EVENTS"
UNRESOLVED = "UNRESOLVED"
UNAVAILABLE = "UNAVAILABLE"
RESOLVED = "RESOLVED"
UNIQUE = "UNIQUE"
MAX_CHIEF_CATALYST_ITEMS = 3
DEFAULT_MAPPING_CACHE = Path("/app/data/coinmarketcal_coin_map.json")
# The Free API sustains one request/second. Initial uncached identity lookups
# are deliberately sequential and spaced; valid cache hits make no coin call.
MAPPING_LOOKUP_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True)
class ScheduledCatalystItem:
    event_id: int | str | None
    title: str
    date: str | None
    date_end: str | None
    date_type: str | None
    is_estimated: bool
    displayed_date: str | None
    categories: tuple[str, ...]
    coin_slugs: tuple[str, ...]
    impact: Any = None
    impact_summary: str | None = None
    time_to_event_seconds: float | None = None


@dataclass(frozen=True)
class ScheduledCatalystContext:
    status: str
    mapping_status: str
    coinmarketcal_slug: str | None
    event_count_next_7d: int
    nearest_event: ScheduledCatalystItem | None
    events: tuple[ScheduledCatalystItem, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScheduledCatalystSummary:
    requested: int
    available: int
    unresolved: int
    unavailable: int
    mapping_lookup_requests: int
    events_request_count: int


@dataclass(frozen=True)
class ResolvedCoinMapping:
    underlying_symbol: str
    coingecko_id: str
    coingecko_name: str
    coinmarketcal_slug: str
    coinmarketcal_name: str
    coinmarketcal_symbol: str
    resolved_at: str


def _normalized_identity(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _unresolved(warning: str) -> ScheduledCatalystContext:
    return ScheduledCatalystContext(
        status=UNRESOLVED,
        mapping_status=UNRESOLVED,
        coinmarketcal_slug=None,
        event_count_next_7d=0,
        nearest_event=None,
        warnings=(warning,),
    )


def _unavailable(
    warning: str,
    mapping_status: str = UNAVAILABLE,
    slug: str | None = None,
) -> ScheduledCatalystContext:
    return ScheduledCatalystContext(
        status=UNAVAILABLE,
        mapping_status=mapping_status,
        coinmarketcal_slug=slug,
        event_count_next_7d=0,
        nearest_event=None,
        warnings=(warning,),
    )


def _reference_identity(
    candidate: MarketSnapshot,
) -> tuple[str, str] | None:
    reference = candidate.independent_market_reference
    if (
        reference is None
        or reference.mapping_status != UNIQUE
        or not reference.coingecko_id
        or not reference.coingecko_name
    ):
        return None
    return reference.coingecko_id, reference.coingecko_name


def resolve_coinmarketcal_mapping(
    candidate: MarketSnapshot,
    rows: list[dict[str, Any]],
    now: datetime,
) -> ResolvedCoinMapping | None:
    identity = _reference_identity(candidate)
    if identity is None:
        return None
    coingecko_id, coingecko_name = identity
    symbol = candidate_symbol(candidate)
    plausible: list[dict[str, Any]] = []
    for row in rows:
        row_symbol = str(row.get("symbol") or "").upper()
        row_name = str(row.get("name") or "")
        row_slug = str(row.get("slug") or "")
        identity_match = (
            _normalized_identity(row_name) == _normalized_identity(coingecko_name)
            or _normalized_identity(row_slug) == _normalized_identity(coingecko_id)
        )
        if row_symbol == symbol and identity_match and row_slug:
            plausible.append(row)
    if len(plausible) != 1:
        return None
    selected = plausible[0]
    return ResolvedCoinMapping(
        underlying_symbol=symbol,
        coingecko_id=coingecko_id,
        coingecko_name=coingecko_name,
        coinmarketcal_slug=str(selected["slug"]),
        coinmarketcal_name=str(selected.get("name") or ""),
        coinmarketcal_symbol=str(selected.get("symbol") or "").upper(),
        resolved_at=now.isoformat(),
    )


def _load_cached_mapping(
    candidate: MarketSnapshot,
    cache: dict[str, Any],
) -> ResolvedCoinMapping | None:
    identity = _reference_identity(candidate)
    if identity is None:
        return None
    item = cache.get("mappings", {}).get(candidate_symbol(candidate))
    if not isinstance(item, dict):
        return None
    coingecko_id, coingecko_name = identity
    required = (
        "underlying_symbol", "coingecko_id", "coingecko_name",
        "coinmarketcal_slug", "coinmarketcal_name",
        "coinmarketcal_symbol", "resolved_at",
    )
    if not all(isinstance(item.get(key), str) and item[key] for key in required):
        return None
    if (
        item["underlying_symbol"] != candidate_symbol(candidate)
        or item["coingecko_id"] != coingecko_id
        or _normalized_identity(item["coingecko_name"])
        != _normalized_identity(coingecko_name)
    ):
        return None
    return ResolvedCoinMapping(**{key: item[key] for key in required})


def _read_cache(path: Path) -> dict[str, Any]:
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with registry_lock(lock_path):
        cache = load_json(path)
    return cache if isinstance(cache, dict) else {}


def _merge_cache(path: Path, mappings: list[ResolvedCoinMapping]) -> None:
    if not mappings:
        return
    lock_path = path.with_suffix(f"{path.suffix}.lock")
    with registry_lock(lock_path):
        cache = load_json(path)
        if not isinstance(cache, dict):
            cache = {}
        stored = cache.setdefault("mappings", {})
        for mapping in mappings:
            stored[mapping.underlying_symbol] = asdict(mapping)
        save_json_atomic(path, cache)


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _string_values(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    result: list[str] = []
    for item in value:
        if isinstance(item, dict) and item.get(key):
            result.append(str(item[key]))
        elif isinstance(item, str):
            result.append(item)
    return tuple(result)


def parse_scheduled_event(
    row: dict[str, Any],
    now: datetime,
) -> ScheduledCatalystItem:
    is_estimated = row.get("isEstimated") is True
    date_value = row.get("date")
    event_time = _parse_timestamp(date_value)
    return ScheduledCatalystItem(
        event_id=row.get("id"),
        title=str(row.get("title") or "Untitled scheduled event")[:300],
        date=str(date_value) if date_value is not None else None,
        date_end=(str(row["dateEnd"]) if row.get("dateEnd") is not None else None),
        date_type=(str(row["dateType"]) if row.get("dateType") else None),
        is_estimated=is_estimated,
        displayed_date=(
            str(row["displayedDate"]) if row.get("displayedDate") else None
        ),
        categories=_string_values(row.get("categories"), "name"),
        coin_slugs=_string_values(row.get("coins"), "slug"),
        impact=row.get("impact"),
        impact_summary=(
            str(row["impactSummary"]) if row.get("impactSummary") else None
        ),
        time_to_event_seconds=(
            max(0.0, (event_time - now).total_seconds())
            if event_time is not None and not is_estimated
            else None
        ),
    )


def validate_scheduled_catalysts(
    candidates: list[MarketSnapshot],
    api_key: str | None = None,
    client: CoinMarketCalClient | None = None,
    cache_path: Path = DEFAULT_MAPPING_CACHE,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
    prefetched_events: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
    prefetched_slugs: tuple[str, ...] = (),
    prefetched_request_count: int = 0,
) -> ScheduledCatalystSummary:
    now = now or datetime.now(timezone.utc)
    requested = candidates[:8]
    cache = _read_cache(cache_path)
    resolved: dict[str, ResolvedCoinMapping] = {}
    new_mappings: list[ResolvedCoinMapping] = []
    lookup_requests = 0
    unresolved_candidates: list[MarketSnapshot] = []

    for candidate in requested:
        symbol = candidate_symbol(candidate)
        if _reference_identity(candidate) is None:
            candidate.scheduled_catalyst_context = _unresolved(
                "CoinMarketCal identity was not guessed because the CoinGecko "
                "identity is ambiguous or unavailable"
            )
            continue
        cached = _load_cached_mapping(candidate, cache)
        if cached is not None:
            resolved[symbol] = cached
            continue
        if client is None and not api_key:
            candidate.scheduled_catalyst_context = _unavailable(
                "CoinMarketCal API key is not configured"
            )
            continue
        unresolved_candidates.append(candidate)

    active_client = client or (CoinMarketCalClient(api_key) if api_key else None)
    for index, candidate in enumerate(unresolved_candidates):
        if index:
            sleep(MAPPING_LOOKUP_INTERVAL_SECONDS)
        if active_client is None:
            candidate.scheduled_catalyst_context = _unavailable(
                "CoinMarketCal API key is not configured"
            )
            continue
        try:
            rows = active_client.get_coins(candidate_symbol(candidate))
        except CoinMarketCalAPIError:
            lookup_requests += 1
            candidate.scheduled_catalyst_context = _unavailable(
                "CoinMarketCal coin mapping request unavailable"
            )
            continue
        lookup_requests += 1
        mapping = resolve_coinmarketcal_mapping(candidate, rows, now)
        if mapping is None:
            candidate.scheduled_catalyst_context = _unresolved(
                "CoinMarketCal identity could not be uniquely resolved"
            )
            continue
        resolved[candidate_symbol(candidate)] = mapping
        new_mappings.append(mapping)

    _merge_cache(cache_path, new_mappings)
    if not resolved:
        contexts = [item.scheduled_catalyst_context for item in requested]
        return ScheduledCatalystSummary(
            requested=len(requested),
            available=0,
            unresolved=sum(item is not None and item.status == UNRESOLVED for item in contexts),
            unavailable=sum(item is not None and item.status == UNAVAILABLE for item in contexts),
            mapping_lookup_requests=lookup_requests,
            events_request_count=0,
        )

    event_rows = [
        row for row in (prefetched_events or ())
        if isinstance(row, dict)
    ]
    covered_slugs = {str(item) for item in prefetched_slugs if str(item)}
    required_slugs = {
        item.coinmarketcal_slug for item in resolved.values()
        if item.coinmarketcal_slug
    }
    missing_slugs = sorted(required_slugs - covered_slugs)
    failed_slugs: set[str] = set()
    events_request_count = max(0, int(prefetched_request_count))

    if missing_slugs:
        if active_client is None:
            failed_slugs.update(missing_slugs)
        else:
            try:
                fetched_rows = active_client.get_events(
                    missing_slugs,
                    now,
                    now + timedelta(days=7),
                )
            except CoinMarketCalAPIError:
                failed_slugs.update(missing_slugs)
            else:
                event_rows.extend(
                    row for row in fetched_rows if isinstance(row, dict)
                )
            events_request_count += 1

    parsed_events = [parse_scheduled_event(row, now) for row in event_rows]
    for candidate in requested:
        mapping = resolved.get(candidate_symbol(candidate))
        if mapping is None:
            continue
        if mapping.coinmarketcal_slug in failed_slugs:
            candidate.scheduled_catalyst_context = _unavailable(
                "CoinMarketCal events request unavailable",
                mapping_status=RESOLVED,
                slug=mapping.coinmarketcal_slug,
            )
            continue
        matching = [
            item
            for item in parsed_events
            if mapping.coinmarketcal_slug in item.coin_slugs
        ]
        matching.sort(
            key=lambda item: (
                _parse_timestamp(item.date)
                or datetime.max.replace(tzinfo=timezone.utc)
            )
        )
        status = AVAILABLE if matching else NO_UPCOMING_EVENTS
        candidate.scheduled_catalyst_context = ScheduledCatalystContext(
            status=status,
            mapping_status=RESOLVED,
            coinmarketcal_slug=mapping.coinmarketcal_slug,
            event_count_next_7d=len(matching),
            nearest_event=matching[0] if matching else None,
            events=tuple(matching[:MAX_CHIEF_CATALYST_ITEMS]),
        )

    contexts = [item.scheduled_catalyst_context for item in requested]
    return ScheduledCatalystSummary(
        requested=len(requested),
        available=sum(
            item is not None and item.status in {AVAILABLE, NO_UPCOMING_EVENTS}
            for item in contexts
        ),
        unresolved=sum(item is not None and item.status == UNRESOLVED for item in contexts),
        unavailable=sum(item is not None and item.status == UNAVAILABLE for item in contexts),
        mapping_lookup_requests=lookup_requests,
        events_request_count=events_request_count,
    )
