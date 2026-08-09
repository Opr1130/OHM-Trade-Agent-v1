from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.scanner.models import MarketSnapshot
from app.services.cryptopanic import CryptoPanicAPIError, CryptoPanicClient


AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
MAX_CHIEF_NEWS_ITEMS = 3
SYMBOL_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}


@dataclass(frozen=True)
class NewsItem:
    post_id: int | str | None
    title: str
    description: str | None
    published_at: str
    age_seconds: float
    kind: str | None
    source_title: str | None
    source_domain: str | None
    instrument_code: str
    instrument_title: str | None
    instrument_slug: str | None
    votes: dict[str, int] | None = None


@dataclass(frozen=True)
class NewsContext:
    status: str
    matched_post_count: int
    recent_post_count_6h: int
    recent_post_count_24h: int
    latest_post_age_seconds: float | None
    recent_items: tuple[NewsItem, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsValidationSummary:
    requested: int
    available: int
    unavailable: int
    request_count: int


def normalize_symbol(value: str) -> str:
    normalized = value.upper()
    return SYMBOL_ALIASES.get(normalized, normalized)


def candidate_symbol(candidate: MarketSnapshot) -> str:
    if candidate.underlying_asset:
        return normalize_symbol(candidate.underlying_asset)
    value = candidate.primary_pair or candidate.symbol
    for quote in ("USDT", "USD"):
        if value.upper().endswith(quote):
            value = value[:-len(quote)]
            break
    return normalize_symbol(value.replace("/", ""))


def unavailable_news(warning: str) -> NewsContext:
    return NewsContext(
        status=UNAVAILABLE,
        matched_post_count=0,
        recent_post_count_6h=0,
        recent_post_count_24h=0,
        latest_post_age_seconds=None,
        warnings=(warning,),
    )


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _votes(value: Any) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    result = {
        str(key): int(count)
        for key, count in value.items()
        if isinstance(count, int)
    }
    return result or None


def evaluate_news_context(
    candidate: MarketSnapshot,
    posts: list[dict[str, Any]],
    now: datetime | None = None,
) -> NewsContext:
    now = now or datetime.now(timezone.utc)
    wanted = candidate_symbol(candidate)
    items: list[NewsItem] = []
    invalid_timestamps = 0
    for post in posts:
        instruments = post.get("instruments")
        if not isinstance(instruments, list):
            continue
        matched_instrument = next(
            (
                instrument
                for instrument in instruments
                if isinstance(instrument, dict)
                and normalize_symbol(str(instrument.get("code", ""))) == wanted
            ),
            None,
        )
        if matched_instrument is None:
            continue
        published = _timestamp(post.get("published_at"))
        if published is None:
            invalid_timestamps += 1
            continue
        age_seconds = max(0.0, (now - published).total_seconds())
        source = post.get("source") if isinstance(post.get("source"), dict) else {}
        description = post.get("description")
        items.append(
            NewsItem(
                post_id=post.get("id"),
                title=str(post.get("title") or "Untitled news item")[:300],
                description=(
                    str(description)[:500]
                    if isinstance(description, str) and description
                    else None
                ),
                published_at=published.isoformat(),
                age_seconds=age_seconds,
                kind=str(post.get("kind")) if post.get("kind") else None,
                source_title=(
                    str(source.get("title")) if source.get("title") else None
                ),
                source_domain=(
                    str(source.get("domain")) if source.get("domain") else None
                ),
                instrument_code=wanted,
                instrument_title=(
                    str(matched_instrument.get("title"))
                    if matched_instrument.get("title")
                    else None
                ),
                instrument_slug=(
                    str(matched_instrument.get("slug"))
                    if matched_instrument.get("slug")
                    else None
                ),
                votes=_votes(post.get("votes")),
            )
        )
    items.sort(key=lambda item: item.age_seconds)
    warnings = (
        (f"Ignored {invalid_timestamps} matched post(s) with invalid timestamps",)
        if invalid_timestamps
        else ()
    )
    return NewsContext(
        status=AVAILABLE,
        matched_post_count=len(items),
        recent_post_count_6h=sum(item.age_seconds <= 6 * 3600 for item in items),
        recent_post_count_24h=sum(item.age_seconds <= 24 * 3600 for item in items),
        latest_post_age_seconds=items[0].age_seconds if items else None,
        recent_items=tuple(items[:MAX_CHIEF_NEWS_ITEMS]),
        warnings=warnings,
    )


def validate_finalist_news(
    candidates: list[MarketSnapshot],
    auth_token: str | None = None,
    api_plan: str = "developer",
    client: CryptoPanicClient | None = None,
    now: datetime | None = None,
) -> NewsValidationSummary:
    requested = candidates[:8]
    if client is None and not auth_token:
        for candidate in requested:
            candidate.news_context = unavailable_news(
                "CryptoPanic authentication token is not configured"
            )
        return NewsValidationSummary(len(requested), 0, len(requested), 0)

    client = client or CryptoPanicClient(auth_token or "", api_plan)
    symbols = [candidate_symbol(item) for item in requested]
    try:
        posts = client.get_posts(symbols)
    except CryptoPanicAPIError:
        for candidate in requested:
            candidate.news_context = unavailable_news(
                "CryptoPanic batch request unavailable"
            )
    else:
        for candidate in requested:
            candidate.news_context = evaluate_news_context(candidate, posts, now)
    contexts = [candidate.news_context for candidate in requested]
    return NewsValidationSummary(
        requested=len(requested),
        available=sum(item is not None and item.status == AVAILABLE for item in contexts),
        unavailable=sum(item is not None and item.status == UNAVAILABLE for item in contexts),
        request_count=1,
    )
