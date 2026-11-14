"""Non-authoritative USD estimate of AI spend from recorded token usage.

MEASUREMENT ONLY - NOT AN INVOICE, NO PRODUCTION DECISION AUTHORITY.

Provider invoices are the source of truth for spend. This module turns the token
counts the platform already records into an *estimate* so spend can be watched
between invoices, and it states how far each figure can be trusted.

Rules, each enforced by a test:

- No priced row, no estimate. A call whose provider/model has no price entry
  effective at the call's timestamp is reported as ``UNPRICED`` and contributes
  nothing to the estimate. It is never costed at zero.
- Pricing is data with provenance. Every :class:`PriceEntry` carries the
  provider, model, per-million-token USD rates, an effective-from date, an
  optional effective-to date and a ``source`` plus ``verified_on`` stamp. No
  prices are shipped in this module: the caller supplies the catalog, so a
  price nobody verified cannot be introduced by accident.
- Arithmetic is exact. Rates and results use :class:`~decimal.Decimal`; no
  binary float touches money.
- Cached input tokens are charged at the entry's cached rate when it has one.
  Otherwise they are charged at the uncached input rate and the row is flagged
  ``CACHED_AT_UNCACHED_RATE``, which can only overstate cost.
- Reasoning tokens are part of output tokens and are not charged twice.
- Every result and summary says ``estimate=True``, ``invoice=False``.

The module is pure: it reads no file, clock or network and grants no authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Sequence

PRICED = "PRICED"
UNPRICED = "UNPRICED"
UNPRICED_NO_ENTRY = "NO_PRICE_ENTRY_EFFECTIVE"
UNPRICED_NO_TOKENS = "TOKEN_COUNTS_MISSING_OR_INVALID"
UNPRICED_NO_TIME = "TIMESTAMP_MISSING_OR_INVALID"

FLAG_CACHED_AT_UNCACHED_RATE = "CACHED_AT_UNCACHED_RATE"

_MILLION = Decimal(1_000_000)


class PriceCatalogError(ValueError):
    """A price entry was malformed or two entries overlap."""


def _decimal(value: Any, field: str, *, allow_none: bool = False) -> Decimal | None:
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str, Decimal)):
        # float is refused on purpose: a binary float is not an exact rate.
        raise PriceCatalogError(f"{field} must be an int, str or Decimal")
    try:
        number = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise PriceCatalogError(f"{field} is not a number: {value!r}") from exc
    if not number.is_finite() or number < 0:
        raise PriceCatalogError(f"{field} must be a non-negative finite number")
    return number


def _date(value: Any, field: str, *, allow_none: bool = False) -> date | None:
    if value is None and allow_none:
        return None
    if isinstance(value, datetime):
        raise PriceCatalogError(f"{field} must be a date, not a datetime")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise PriceCatalogError(f"{field} is not an ISO date: {value!r}") from exc
    raise PriceCatalogError(f"{field} must be an ISO date")


@dataclass(frozen=True)
class PriceEntry:
    """One verified price for a provider/model pair over an effective period."""

    provider: str
    model: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    effective_from: date
    effective_to: date | None
    source: str
    verified_on: date
    cached_input_usd_per_million: Decimal | None = None

    @classmethod
    def build(
        cls,
        *,
        provider: str,
        model: str,
        input_usd_per_million: Any,
        output_usd_per_million: Any,
        effective_from: Any,
        source: str,
        verified_on: Any,
        effective_to: Any = None,
        cached_input_usd_per_million: Any = None,
    ) -> "PriceEntry":
        for name, value in (("provider", provider), ("model", model), ("source", source)):
            if not isinstance(value, str) or not value.strip():
                raise PriceCatalogError(f"{name} is required")
        start = _date(effective_from, "effective_from")
        end = _date(effective_to, "effective_to", allow_none=True)
        if end is not None and end < start:
            raise PriceCatalogError("effective_to precedes effective_from")
        return cls(
            provider=provider.strip().lower(),
            model=model.strip(),
            input_usd_per_million=_decimal(input_usd_per_million, "input_usd_per_million"),
            output_usd_per_million=_decimal(output_usd_per_million, "output_usd_per_million"),
            effective_from=start,
            effective_to=end,
            source=source.strip(),
            verified_on=_date(verified_on, "verified_on"),
            cached_input_usd_per_million=_decimal(
                cached_input_usd_per_million,
                "cached_input_usd_per_million",
                allow_none=True,
            ),
        )

    def covers(self, day: date) -> bool:
        return self.effective_from <= day and (
            self.effective_to is None or day <= self.effective_to
        )


class PriceCatalog:
    """Price entries keyed by provider/model with non-overlapping periods."""

    def __init__(self, entries: Sequence[PriceEntry] = ()) -> None:
        self._by_key: dict[tuple[str, str], list[PriceEntry]] = {}
        for entry in entries:
            bucket = self._by_key.setdefault((entry.provider, entry.model), [])
            for other in bucket:
                if _overlap(entry, other):
                    raise PriceCatalogError(
                        f"overlapping price periods for {entry.provider}:{entry.model}"
                    )
            bucket.append(entry)

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._by_key.values())

    def entry_for(self, provider: str, model: str, day: date) -> PriceEntry | None:
        for entry in self._by_key.get((str(provider).strip().lower(), str(model).strip()), ()):
            if entry.covers(day):
                return entry
        return None


def _overlap(a: PriceEntry, b: PriceEntry) -> bool:
    a_end = a.effective_to or date.max
    b_end = b.effective_to or date.max
    return a.effective_from <= b_end and b.effective_from <= a_end


def _token_count(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _call_day(value: Any) -> date | None:
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            stamp = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if stamp.tzinfo is None:
        return None
    return stamp.astimezone(timezone.utc).date()


@dataclass(frozen=True)
class CostEstimate:
    """Estimate for one recorded AI call. Never an invoice."""

    status: str
    reason: str | None
    provider: str | None
    model: str | None
    usd: Decimal | None
    flags: tuple[str, ...]
    price_source: str | None
    price_verified_on: date | None
    price_effective_from: date | None
    estimate: bool = True
    invoice: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "provider": self.provider,
            "model": self.model,
            "usd": None if self.usd is None else str(self.usd),
            "flags": list(self.flags),
            "price_source": self.price_source,
            "price_verified_on": None if self.price_verified_on is None else self.price_verified_on.isoformat(),
            "price_effective_from": None if self.price_effective_from is None else self.price_effective_from.isoformat(),
            "estimate": self.estimate,
            "invoice": self.invoice,
        }


def _unpriced(reason: str, provider: str | None, model: str | None) -> CostEstimate:
    return CostEstimate(
        status=UNPRICED,
        reason=reason,
        provider=provider,
        model=model,
        usd=None,
        flags=(),
        price_source=None,
        price_verified_on=None,
        price_effective_from=None,
    )


def estimate_call_cost(
    row: Mapping[str, Any],
    catalog: PriceCatalog,
    *,
    default_provider: str | None = None,
) -> CostEstimate:
    """Estimate one usage row (``timestamp_utc``/``ts``, ``model``, token counts)."""
    provider = row.get("provider") or default_provider
    provider = provider.strip().lower() if isinstance(provider, str) and provider.strip() else None
    model = row.get("model")
    model = model.strip() if isinstance(model, str) and model.strip() else None

    day = _call_day(row.get("timestamp_utc", row.get("ts")))
    if day is None:
        return _unpriced(UNPRICED_NO_TIME, provider, model)
    input_tokens = _token_count(row.get("input_tokens"))
    output_tokens = _token_count(row.get("output_tokens"))
    cached_raw = row.get("cached_input_tokens", 0)
    cached = _token_count(cached_raw if cached_raw is not None else 0)
    if (
        input_tokens is None
        or output_tokens is None
        or cached is None
        or cached > input_tokens
    ):
        return _unpriced(UNPRICED_NO_TOKENS, provider, model)
    if provider is None or model is None:
        return _unpriced(UNPRICED_NO_ENTRY, provider, model)
    entry = catalog.entry_for(provider, model, day)
    if entry is None:
        return _unpriced(UNPRICED_NO_ENTRY, provider, model)

    flags: list[str] = []
    uncached_input = input_tokens - cached
    cost = uncached_input * entry.input_usd_per_million
    if cached:
        if entry.cached_input_usd_per_million is None:
            cost += cached * entry.input_usd_per_million
            flags.append(FLAG_CACHED_AT_UNCACHED_RATE)
        else:
            cost += cached * entry.cached_input_usd_per_million
    cost += output_tokens * entry.output_usd_per_million
    return CostEstimate(
        status=PRICED,
        reason=None,
        provider=provider,
        model=model,
        usd=cost / _MILLION,
        flags=tuple(flags),
        price_source=entry.source,
        price_verified_on=entry.verified_on,
        price_effective_from=entry.effective_from,
    )


def summarize_costs(
    rows: Iterable[Mapping[str, Any]],
    catalog: PriceCatalog,
    *,
    default_provider: str | None = None,
) -> dict[str, Any]:
    """Estimated USD by model, with unpriced calls reported separately."""
    if isinstance(rows, (str, bytes, bytearray, Mapping)):
        raise TypeError("rows must be an iterable of mappings")
    total = Decimal(0)
    priced = 0
    unpriced = 0
    by_model: dict[str, Decimal] = {}
    unpriced_reasons: dict[str, int] = {}
    unpriced_models: dict[str, int] = {}
    flagged = 0
    for row in rows:
        result = estimate_call_cost(row, catalog, default_provider=default_provider)
        if result.status == PRICED and result.usd is not None:
            priced += 1
            total += result.usd
            key = f"{result.provider}:{result.model}"
            by_model[key] = by_model.get(key, Decimal(0)) + result.usd
            if result.flags:
                flagged += 1
        else:
            unpriced += 1
            unpriced_reasons[result.reason or "UNKNOWN"] = (
                unpriced_reasons.get(result.reason or "UNKNOWN", 0) + 1
            )
            label = f"{result.provider}:{result.model}"
            unpriced_models[label] = unpriced_models.get(label, 0) + 1
    return {
        "estimate": True,
        "invoice": False,
        "currency": "USD",
        "estimated_usd": str(total),
        "priced_calls": priced,
        "unpriced_calls": unpriced,
        "complete": unpriced == 0,
        "flagged_calls": flagged,
        "by_model_usd": {key: str(value) for key, value in sorted(by_model.items())},
        "unpriced_reasons": dict(sorted(unpriced_reasons.items())),
        "unpriced_models": dict(sorted(unpriced_models.items())),
        "note": "Estimate from recorded tokens; the provider invoice is authoritative.",
    }
