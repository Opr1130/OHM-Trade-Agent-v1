from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.explosion_learning import read_recent_explosion_states


@dataclass(frozen=True)
class DailyGainerTrace:
    base_asset: str
    symbol: str
    current_price: float
    gain_today_pct: float
    liquidity_24h_usd_approx: float
    first_ohm_seen_at: str | None
    first_ohm_phase: str | None
    first_ohm_phase_score: int | None
    first_ohm_price: float | None
    gain_when_first_seen_pct: float | None
    remaining_upside_after_first_seen_pct: float | None
    first_source_cohort: str | None
    first_momentum_1h_pct: float | None
    first_momentum_acceleration: float | None
    first_relative_volume: float | None
    first_trade_count_acceleration: float | None
    first_aggressor_imbalance: float | None
    trace_status: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(current: float, reference: float) -> float:
    return 0.0 if reference <= 0 else (current / reference - 1.0) * 100.0


def _today_open(client: KrakenClient, symbol: str, now: datetime) -> float | None:
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    try:
        candles = client.get_ohlc(symbol, interval=60, since=int(start.timestamp()))
    except Exception:
        return None
    completed = [candle for candle in candles if candle.timestamp >= start.timestamp()]
    if not completed:
        return None
    return float(completed[0].open)


def _full_universe_rows(client: KrakenClient) -> list[tuple[str, str, str, dict[str, float]]]:
    pair_details = client.get_asset_pairs()
    markets: list[tuple[str, str, str, str, str]] = []
    for pair_id, details in pair_details.items():
        if _is_excluded_market(details):
            continue
        symbols = _market_symbols(details)
        if symbols is None:
            continue
        base, quote = symbols
        altname = str(details.get("altname", pair_id)).upper()
        markets.append((pair_id, altname, f"{base}{quote}", base, quote))

    tickers: dict[str, dict[str, float]] = {}
    pair_ids = sorted({row[0] for row in markets})
    for start in range(0, len(pair_ids), TICKER_BATCH_SIZE):
        try:
            tickers.update(client.get_tickers(pair_ids[start:start + TICKER_BATCH_SIZE]))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, dict[str, float]]] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)
        if ticker is None:
            continue
        current = by_asset.get(base)
        if current is None or (current[1] != "USD" and quote == "USD"):
            by_asset[base] = (display_pair, quote, f"{base}/{quote}", ticker)
    return [
        (base, row[0], row[2], row[3])
        for base, row in by_asset.items()
    ]


def reconcile_daily_gainers(
    *,
    client: KrakenClient | None = None,
    now: datetime | None = None,
    top_n: int = 20,
    snapshot_path=None,
) -> list[DailyGainerTrace]:
    """Compare today's Kraken leaders with OHM's actual recorded observations.

    This is a retrospective audit only. It does not reconstruct unavailable
    historical feature values and never changes production decisions.
    """
    client = client or KrakenClient()
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now = now.astimezone(timezone.utc)

    states = read_recent_explosion_states(path=snapshot_path, limit=50000)
    first_by_base: dict[str, dict[str, Any]] = {}
    for row in states:
        symbol = str(row.get("symbol") or "").upper()
        base = symbol.split("/")[0].replace("USD", "") if "/" in symbol else symbol.removesuffix("USD").removesuffix("USDT")
        if not base:
            continue
        observed = str(row.get("observed_at") or "")
        current = first_by_base.get(base)
        if current is None or observed < str(current.get("observed_at") or ""):
            first_by_base[base] = row

    candidates: list[tuple[float, str, str, str, dict[str, float], float]] = []
    for base, display_pair, public_symbol, ticker in _full_universe_rows(client):
        last = float(ticker.get("last") or 0.0)
        volume = float(ticker.get("volume_24h") or 0.0)
        if last <= 0 or volume <= 0:
            continue
        opening = _today_open(client, display_pair, now)
        if opening is None or opening <= 0:
            continue
        gain = _pct(last, opening)
        candidates.append((gain, base, display_pair, public_symbol, ticker, opening))

    candidates.sort(key=lambda row: (-row[0], row[1]))
    traces: list[DailyGainerTrace] = []
    for gain, base, display_pair, public_symbol, ticker, opening in candidates[:max(1, top_n)]:
        last = float(ticker["last"])
        notional = last * float(ticker.get("volume_24h") or 0.0)
        first = first_by_base.get(base)
        if first is None:
            traces.append(DailyGainerTrace(
                base_asset=base,
                symbol=display_pair,
                current_price=last,
                gain_today_pct=round(gain, 4),
                liquidity_24h_usd_approx=round(notional, 2),
                first_ohm_seen_at=None,
                first_ohm_phase=None,
                first_ohm_phase_score=None,
                first_ohm_price=None,
                gain_when_first_seen_pct=None,
                remaining_upside_after_first_seen_pct=None,
                first_source_cohort=None,
                first_momentum_1h_pct=None,
                first_momentum_acceleration=None,
                first_relative_volume=None,
                first_trade_count_acceleration=None,
                first_aggressor_imbalance=None,
                trace_status="NOT_SEEN_BY_WAVE5",
            ))
            continue

        try:
            first_price = float(first.get("reference_price") or 0.0)
        except (TypeError, ValueError):
            first_price = 0.0
        gain_at_first = _pct(first_price, opening) if first_price > 0 else None
        remaining = _pct(last, first_price) if first_price > 0 else None
        traces.append(DailyGainerTrace(
            base_asset=base,
            symbol=display_pair,
            current_price=last,
            gain_today_pct=round(gain, 4),
            liquidity_24h_usd_approx=round(notional, 2),
            first_ohm_seen_at=str(first.get("observed_at") or "") or None,
            first_ohm_phase=str(first.get("phase") or "") or None,
            first_ohm_phase_score=int(first.get("phase_score")) if first.get("phase_score") is not None else None,
            first_ohm_price=round(first_price, 12) if first_price > 0 else None,
            gain_when_first_seen_pct=round(gain_at_first, 4) if gain_at_first is not None else None,
            remaining_upside_after_first_seen_pct=round(remaining, 4) if remaining is not None else None,
            first_source_cohort=str(first.get("source_cohort") or "") or None,
            first_momentum_1h_pct=float(first.get("momentum_1h_pct")) if first.get("momentum_1h_pct") is not None else None,
            first_momentum_acceleration=float(first.get("momentum_acceleration")) if first.get("momentum_acceleration") is not None else None,
            first_relative_volume=float(first.get("relative_volume")) if first.get("relative_volume") is not None else None,
            first_trade_count_acceleration=float(first.get("trade_count_acceleration")) if first.get("trade_count_acceleration") is not None else None,
            first_aggressor_imbalance=float(first.get("aggressor_imbalance")) if first.get("aggressor_imbalance") is not None else None,
            trace_status="TRACED_FROM_RECORDED_STATE",
        ))
    return traces
