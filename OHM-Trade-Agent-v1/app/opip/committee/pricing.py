"""Provider cost accounting as data, not as embedded business assumptions.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Pricing is configuration. When no price is configured for a provider/model pair
the cost is reported as ``UNKNOWN`` rather than guessed, because an invented
price would silently corrupt any cost-versus-information comparison.

Prices are quoted the way providers publish them: microunits per **million**
tokens. That keeps the arithmetic integer-exact (a $2.50 per million token rate
is ``2500000``) and avoids the fractional-microunit problem of a per-token unit.

A malformed price specification raises rather than being ignored: a typo would
otherwise make every cost permanently unknown without anyone noticing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Sequence

#: Env var holding the price book, e.g.
#: ``openai:model-a=2000000/8000000;anthropic:model-b=3000000/15000000``
#: where each rate is microunits per million tokens.
COMMITTEE_PRICES_ENV = "OPIP_COMMITTEE_PRICES"

#: Tokens that one declared rate covers.
TOKENS_PER_PRICING_UNIT = 1_000_000

#: Fixed-point scale for money: microunits (1e-6 of a currency unit).
MICROUNITS_PER_UNIT = 1_000_000

COST_UNKNOWN = "UNKNOWN"


class PriceBookConfigError(ValueError):
    """A price specification was malformed and cannot be interpreted safely."""


@dataclass(frozen=True)
class TokenPrice:
    """Declared price for one provider/model pair, per million tokens."""

    provider: str
    model: str
    microunits_per_million_input_tokens: int
    microunits_per_million_output_tokens: int

    def __post_init__(self) -> None:
        for field_name in ("provider", "model"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise PriceBookConfigError(f"{field_name} is required")
        for field_name in (
            "microunits_per_million_input_tokens",
            "microunits_per_million_output_tokens",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise PriceBookConfigError(
                    f"{field_name} must be a non-negative integer"
                )

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider.strip().lower(), self.model.strip())


class PriceBook:
    """A lookup table of declared prices. Absence means cost is unknown."""

    def __init__(self, prices: Sequence[TokenPrice] = ()) -> None:
        self._prices: dict[tuple[str, str], TokenPrice] = {}
        for price in prices:
            if price.key in self._prices:
                raise PriceBookConfigError(
                    f"duplicate price for {price.provider}:{price.model}"
                )
            self._prices[price.key] = price

    def __len__(self) -> int:
        return len(self._prices)

    @property
    def is_empty(self) -> bool:
        return not self._prices

    def price_for(self, provider: str, model: str) -> TokenPrice | None:
        return self._prices.get((str(provider).strip().lower(), str(model).strip()))

    def cost_microunits(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int | None,
        output_tokens: int | None,
    ) -> int | None:
        """Estimated cost in microunits, or ``None`` when it cannot be known.

        An unknown price, or a missing token count, yields ``None``. It is never
        treated as zero, because zero would read as "this model is free".
        """
        price = self.price_for(provider, model)
        if price is None:
            return None
        if input_tokens is None or output_tokens is None:
            return None
        if type(input_tokens) is not int or type(output_tokens) is not int:
            return None
        if input_tokens < 0 or output_tokens < 0:
            return None
        return scale_tokens(
            input_tokens, price.microunits_per_million_input_tokens
        ) + scale_tokens(output_tokens, price.microunits_per_million_output_tokens)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> PriceBook:
        """Parse the price book from configuration.

        Format: ``provider:model=input/output`` entries separated by ``;``.
        Each rate is microunits per million tokens.
        """
        raw = str(env.get(COMMITTEE_PRICES_ENV, "") or "").strip()
        if not raw:
            return cls(())
        prices: list[TokenPrice] = []
        for entry in raw.split(";"):
            entry = entry.strip()
            if not entry:
                continue
            if "=" not in entry or ":" not in entry:
                raise PriceBookConfigError(
                    f"malformed price entry {entry!r}; "
                    "expected provider:model=input/output"
                )
            target, _, rate = entry.partition("=")
            provider, _, model = target.partition(":")
            input_rate, separator, output_rate = rate.partition("/")
            if not separator:
                raise PriceBookConfigError(
                    f"malformed price entry {entry!r}; expected input/output rates"
                )
            prices.append(
                TokenPrice(
                    provider=provider.strip(),
                    model=model.strip(),
                    microunits_per_million_input_tokens=_parse_rate(input_rate, entry),
                    microunits_per_million_output_tokens=_parse_rate(output_rate, entry),
                )
            )
        return cls(prices)


def scale_tokens(tokens: int, microunits_per_million_tokens: int) -> int:
    """Cost in microunits for a token count, rounded half up."""
    total = tokens * microunits_per_million_tokens
    return (total + TOKENS_PER_PRICING_UNIT // 2) // TOKENS_PER_PRICING_UNIT


def _parse_rate(value: str, entry: str) -> int:
    text = value.strip()
    if not text:
        raise PriceBookConfigError(f"missing rate in price entry {entry!r}")
    try:
        amount = Decimal(text)
    except Exception as exc:
        raise PriceBookConfigError(
            f"non-numeric rate {text!r} in price entry {entry!r}"
        ) from exc
    # Non-finite rates must fail closed here, with the documented error type.
    # ``Decimal("Infinity")`` would otherwise pass the negativity and integrality
    # checks and then raise a bare ``OverflowError`` from ``int()``, and
    # ``Decimal("NaN")`` raises ``InvalidOperation`` from the comparison below.
    if not amount.is_finite():
        raise PriceBookConfigError(
            f"non-finite rate {text!r} in price entry {entry!r}"
        )
    if amount < 0:
        raise PriceBookConfigError(f"negative rate in price entry {entry!r}")
    if amount != amount.to_integral_value():
        raise PriceBookConfigError(
            f"rate {text!r} in price entry {entry!r} must be a whole number of "
            "microunits per million tokens"
        )
    return int(amount)


__all__ = [
    "COMMITTEE_PRICES_ENV",
    "COST_UNKNOWN",
    "MICROUNITS_PER_UNIT",
    "PriceBook",
    "PriceBookConfigError",
    "TOKENS_PER_PRICING_UNIT",
    "TokenPrice",
    "scale_tokens",
]
