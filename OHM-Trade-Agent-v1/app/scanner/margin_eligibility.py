from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from app.exchanges.kraken import KrakenAPIError, KrakenClient
from app.scanner.candidates import MIN_TECHNICAL_SCORE
from app.scanner.models import MarketSnapshot


BITNOMIAL_EXECUTION_VENUE = "bitnomial_exchange"
MIN_SHORT_LEVERAGE = 2.0
DEFAULT_UNKNOWN_PAIR_LEVERAGE = 2.0


@dataclass(frozen=True)
class MarginEligibilitySummary:
    requested: int
    eligible: int
    ineligible: int
    unavailable: int


def _normalize_pair(value: str) -> str:
    normalized = value.upper().replace("XBT", "BTC")
    normalized = normalized.replace(":BTNL", "")
    normalized = normalized.replace("/", "")
    return normalized


def _reported_max_leverage(details: dict[str, Any]) -> float | None:
    values: list[float] = []
    for key in ("leverage_sell", "leverage_buy", "leverage"):
        raw = details.get(key)
        if isinstance(raw, (list, tuple)):
            for item in raw:
                try:
                    values.append(float(item))
                except (TypeError, ValueError):
                    pass
        elif raw is not None:
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                pass
    return max(values) if values else None


def validate_short_margin_eligibility(
    candidates: list[MarketSnapshot],
    *,
    account_leverage_ceiling: float = 3.0,
    client: KrakenClient | None = None,
) -> MarginEligibilitySummary:
    """Attach US-retail margin evidence to SHORT candidates.

    Pair presence in Kraken's Bitnomial execution-venue discovery is the
    tradability authority. If the venue omits leverage tiers, v1 conservatively
    assumes only 2x because every listed US retail margin pair supports at least
    2x, while never exceeding the configured account ceiling.
    """
    short_candidates = [c for c in candidates if c.trade_direction == "SHORT"]
    if not short_candidates:
        return MarginEligibilitySummary(0, 0, 0, 0)

    client = client or KrakenClient()
    try:
        pairs = client.get_asset_pairs(execution_venue=BITNOMIAL_EXECUTION_VENUE)
    except KrakenAPIError as exc:
        for candidate in short_candidates:
            candidate.margin_eligible = False
            candidate.margin_validation_status = "UNAVAILABLE"
            candidate.margin_warnings = [f"Kraken margin pair discovery unavailable: {exc}"]
        return MarginEligibilitySummary(len(short_candidates), 0, 0, len(short_candidates))

    lookup: dict[str, tuple[str, dict[str, Any]]] = {}
    for pair_id, details in pairs.items():
        identifiers = {
            pair_id,
            str(details.get("altname") or ""),
            str(details.get("wsname") or ""),
        }
        for identifier in identifiers:
            if identifier:
                lookup[_normalize_pair(identifier)] = (pair_id, details)

    eligible = 0
    ineligible = 0
    for candidate in short_candidates:
        primary = candidate.primary_pair or candidate.symbol
        match = lookup.get(_normalize_pair(primary))
        if match is None:
            candidate.margin_eligible = False
            candidate.margin_validation_status = "INELIGIBLE"
            candidate.margin_warnings = [
                "Pair is not listed by Kraken's US retail margin execution venue"
            ]
            ineligible += 1
            continue

        pair_id, details = match
        reported = _reported_max_leverage(details)
        venue_max = reported if reported is not None else DEFAULT_UNKNOWN_PAIR_LEVERAGE
        effective_max = min(float(account_leverage_ceiling), venue_max)
        candidate.margin_venue_symbol = (
            str(details.get("wsname") or details.get("altname") or pair_id)
        )
        if ":BTNL" not in candidate.margin_venue_symbol.upper():
            candidate.margin_venue_symbol = f"{primary}:BTNL"
        candidate.margin_max_leverage = effective_max
        candidate.margin_eligible = effective_max >= MIN_SHORT_LEVERAGE
        candidate.margin_validation_status = (
            "ELIGIBLE" if candidate.margin_eligible else "INELIGIBLE"
        )
        candidate.margin_warnings = []
        if reported is None:
            candidate.margin_warnings.append(
                "Venue did not expose a leverage tier; conservative 2x ceiling assumed"
            )
        if candidate.margin_eligible:
            eligible += 1
        else:
            ineligible += 1

    return MarginEligibilitySummary(
        requested=len(short_candidates),
        eligible=eligible,
        ineligible=ineligible,
        unavailable=0,
    )


def keep_margin_tradeable_candidates(
    candidates: list[MarketSnapshot],
) -> list[MarketSnapshot]:
    """LONG candidates pass; SHORT candidates require live margin eligibility."""
    return [
        candidate
        for candidate in candidates
        if candidate.trade_direction != "SHORT" or candidate.margin_eligible
    ]



def recover_margin_rejected_long(
    candidate: MarketSnapshot,
    *,
    min_score: int = MIN_TECHNICAL_SCORE,
) -> MarketSnapshot | None:
    """Recover an independently qualifying LONG after a SHORT margin stop.

    The preferred SHORT remains fail-closed: this never authorizes or modifies
    it. Recovery is allowed only when the selector previously recorded a LONG
    technical score that already cleared the unchanged production threshold.
    """
    if str(candidate.trade_direction or "").upper() != "SHORT":
        return None
    if bool(candidate.margin_eligible):
        return None
    status = str(candidate.margin_validation_status or "").upper()
    if status not in {"INELIGIBLE", "UNAVAILABLE"}:
        return None

    raw_long_score = candidate.directional_long_score
    if raw_long_score is None:
        return None
    try:
        long_score = int(raw_long_score)
    except (TypeError, ValueError, OverflowError):
        return None
    if long_score < int(min_score):
        return None

    return replace(
        candidate,
        trade_direction="LONG",
        technical_score=long_score,
        margin_eligible=False,
        margin_venue_symbol=None,
        margin_max_leverage=None,
        margin_validation_status="NOT_REQUIRED",
        margin_warnings=None,
    )


def recover_margin_rejected_longs(
    candidates: list[MarketSnapshot],
    *,
    min_score: int = MIN_TECHNICAL_SCORE,
) -> list[MarketSnapshot]:
    """Return LONG fallbacks for margin-stopped SHORT candidates only."""
    recovered: list[MarketSnapshot] = []
    for candidate in candidates:
        fallback = recover_margin_rejected_long(candidate, min_score=min_score)
        if fallback is not None:
            recovered.append(fallback)
    return recovered



def bound_recovered_longs(
    current_candidates: list[MarketSnapshot],
    recovered_longs: list[MarketSnapshot],
    *,
    max_per_direction: int,
) -> list[MarketSnapshot]:
    """Limit LONG fallbacks without evicting candidates that already survived."""
    existing_longs = sum(
        str(candidate.trade_direction or "").upper() == "LONG"
        for candidate in current_candidates
    )
    available = max(0, int(max_per_direction) - existing_longs)
    return list(recovered_longs[:available])
