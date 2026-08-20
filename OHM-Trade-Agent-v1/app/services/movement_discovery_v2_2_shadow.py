from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from app.exchanges.kraken import KrakenClient
from app.scanner.market_scanner import analyze_symbol
from app.scanner.universe import TICKER_BATCH_SIZE, _is_excluded_market, _market_symbols
from app.services.movement_discovery_v2 import CoarseMover, evaluate_early_mover
from app.services.registry_io import registry_lock


VERSION = "movement-discovery-v2.2-shadow"
CURRENT_MOMENTUM = "CURRENT_MOMENTUM"
EARLY_ACCELERATION = "EARLY_ACCELERATION"
REACCELERATION = "REACCELERATION"
DEFAULT_TOTAL_CANDIDATES = 40
COHORT_QUOTAS = {
    CURRENT_MOMENTUM: 16,
    EARLY_ACCELERATION: 12,
    REACCELERATION: 12,
}
MIN_RANKED_NOTIONAL_USD = 50_000.0
CAP_24H_LIFT_PCT = 15.0
SHADOW_FILE = Path("/app/data/movement_discovery_v2_2_shadow.jsonl")


@dataclass(frozen=True)
class ShadowCoarseCandidate:
    cohort: str
    mover: CoarseMover
    high_today: float
    low_today: float
    lift_today_pct: float
    distance_from_today_high_pct: float
    challenger_score: float


@dataclass(frozen=True)
class ShadowChallengerResult:
    version: str
    cohort: str
    symbol: str
    base_asset: str
    challenger_score: float
    reference_price: float
    lift_today_pct: float
    lift_24h_pct: float
    distance_from_24h_high_pct: float
    liquidity_24h_usd_approx: float
    v21_signal_present: bool
    v21_stage: str | None
    v21_continuation_confidence: int | None
    v21_entry_quality: int | None
    v21_entry_recommendation: str | None
    v21_alert_eligible: bool
    shadow_only: bool = True
    production_decision_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _pct(current: float, reference: float) -> float:
    return 0.0 if reference <= 0 else (current / reference - 1.0) * 100.0


def _distance_from_high(last: float, high: float) -> float:
    return 999.0 if last <= 0 or high <= 0 else max(0.0, (high - last) / last * 100.0)


def _liquidity_component(notional: float) -> float:
    return max(0.0, min(18.0, math.log10(max(notional, 1.0)) * 2.5))


def _candidate_for_cohort(
    *,
    cohort: str,
    base: str,
    quote: str,
    display_pair: str,
    ticker: dict[str, float],
) -> ShadowCoarseCandidate | None:
    last = float(ticker.get("last") or 0.0)
    high24 = float(ticker.get("high_24h") or 0.0)
    low24 = float(ticker.get("low_24h") or 0.0)
    high_today = float(ticker.get("high_today") or high24)
    low_today = float(ticker.get("low_today") or low24)
    volume24 = float(ticker.get("volume_24h") or 0.0)
    if min(last, high24, low24, high_today, low_today, volume24) <= 0:
        return None
    notional = last * volume24
    if notional < MIN_RANKED_NOTIONAL_USD:
        return None
    lift24 = _pct(last, low24)
    lift_today = _pct(last, low_today)
    dist24 = _distance_from_high(last, high24)
    dist_today = _distance_from_high(last, high_today)
    liquidity = _liquidity_component(notional)

    admitted = False
    score = 0.0
    if cohort == CURRENT_MOMENTUM:
        admitted = lift24 >= 2.0 and dist24 <= 6.0
        score = min(lift24, CAP_24H_LIFT_PCT) * 3.0 + max(0.0, 6.0 - dist24) * 2.0 + min(lift_today, 8.0) * 2.0 + liquidity
    elif cohort == EARLY_ACCELERATION:
        admitted = 1.0 <= lift24 <= 15.0 and lift_today >= 1.5 and dist_today <= 5.0
        score = min(lift_today, 8.0) * 6.0 + min(lift24, 10.0) * 2.0 + max(0.0, 5.0 - dist_today) * 2.0 + liquidity
    elif cohort == REACCELERATION:
        midpoint = (high24 + low24) / 2.0
        admitted = 6.0 < dist24 <= 15.0 and last > midpoint and lift_today >= 1.0 and dist_today <= 6.0
        score = min(lift_today, 8.0) * 6.0 + max(0.0, 15.0 - dist24) * 2.0 + max(0.0, 6.0 - dist_today) * 2.0 + liquidity
    if not admitted:
        return None

    mover = CoarseMover(
        base_asset=base,
        primary_pair=display_pair,
        kraken_public_symbol=f"{base}/{quote}",
        last_price=last,
        volume_24h=volume24,
        notional_24h_usd_approx=notional,
        high_24h=high24,
        low_24h=low24,
        lift_from_24h_low_pct=lift24,
        distance_from_24h_high_pct=dist24,
        coarse_score=round(score, 4),
    )
    return ShadowCoarseCandidate(
        cohort=cohort,
        mover=mover,
        high_today=high_today,
        low_today=low_today,
        lift_today_pct=round(lift_today, 4),
        distance_from_today_high_pct=round(dist_today, 4),
        challenger_score=round(score, 4),
    )


def discover_v22_shadow_candidates(
    client: KrakenClient | None = None,
    *,
    total_candidates: int = DEFAULT_TOTAL_CANDIDATES,
) -> list[ShadowCoarseCandidate]:
    """Build diversified shadow cohorts using ticker data only."""
    client = client or KrakenClient()
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
        batch = pair_ids[start:start + TICKER_BATCH_SIZE]
        try:
            tickers.update(client.get_tickers(batch))
        except Exception:
            continue

    by_asset: dict[str, tuple[str, str, str, str, str, dict[str, float]]] = {}
    for pair_id, altname, display_pair, base, quote in markets:
        ticker = tickers.get(pair_id) or tickers.get(altname) or tickers.get(display_pair)
        if ticker is None:
            continue
        current = by_asset.get(base)
        if current is None or (current[4] != "USD" and quote == "USD"):
            by_asset[base] = (pair_id, altname, display_pair, base, quote, ticker)

    cohort_rows: dict[str, list[ShadowCoarseCandidate]] = {key: [] for key in COHORT_QUOTAS}
    for _, _, display_pair, base, quote, ticker in by_asset.values():
        for cohort in COHORT_QUOTAS:
            row = _candidate_for_cohort(
                cohort=cohort,
                base=base,
                quote=quote,
                display_pair=display_pair,
                ticker=ticker,
            )
            if row is not None:
                cohort_rows[cohort].append(row)

    selected: list[ShadowCoarseCandidate] = []
    used_assets: set[str] = set()
    for cohort in (EARLY_ACCELERATION, REACCELERATION, CURRENT_MOMENTUM):
        rows = sorted(
            cohort_rows[cohort],
            key=lambda row: (-row.challenger_score, -row.mover.notional_24h_usd_approx, row.mover.base_asset),
        )
        quota = min(COHORT_QUOTAS[cohort], total_candidates - len(selected))
        for row in rows:
            if len([item for item in selected if item.cohort == cohort]) >= quota:
                break
            if row.mover.base_asset in used_assets:
                continue
            selected.append(row)
            used_assets.add(row.mover.base_asset)
        if len(selected) >= total_candidates:
            break

    if len(selected) < total_candidates:
        overflow = sorted(
            [row for rows in cohort_rows.values() for row in rows if row.mover.base_asset not in used_assets],
            key=lambda row: (-row.challenger_score, -row.mover.notional_24h_usd_approx, row.mover.base_asset),
        )
        for row in overflow:
            if len(selected) >= total_candidates:
                break
            if row.mover.base_asset in used_assets:
                continue
            selected.append(row)
            used_assets.add(row.mover.base_asset)
    return selected


def scan_v22_shadow(
    client: KrakenClient | None = None,
    *,
    total_candidates: int = DEFAULT_TOTAL_CANDIDATES,
) -> tuple[list[ShadowCoarseCandidate], list[ShadowChallengerResult], dict[str, int]]:
    candidates = discover_v22_shadow_candidates(client, total_candidates=total_candidates)
    results: list[ShadowChallengerResult] = []
    stats = {"selected": len(candidates), "analyzed": 0, "skipped": 0}
    for row in candidates:
        status, snapshot, _ = analyze_symbol(row.mover.primary_pair)
        if status != "ok" or snapshot is None:
            stats["skipped"] += 1
            continue
        stats["analyzed"] += 1
        signal = evaluate_early_mover(snapshot, row.mover)
        results.append(
            ShadowChallengerResult(
                version=VERSION,
                cohort=row.cohort,
                symbol=row.mover.primary_pair,
                base_asset=row.mover.base_asset,
                challenger_score=row.challenger_score,
                reference_price=row.mover.last_price,
                lift_today_pct=row.lift_today_pct,
                lift_24h_pct=round(row.mover.lift_from_24h_low_pct, 4),
                distance_from_24h_high_pct=round(row.mover.distance_from_24h_high_pct, 4),
                liquidity_24h_usd_approx=round(row.mover.notional_24h_usd_approx, 2),
                v21_signal_present=signal is not None,
                v21_stage=signal.stage if signal else None,
                v21_continuation_confidence=signal.continuation_confidence if signal else None,
                v21_entry_quality=signal.entry_quality if signal else None,
                v21_entry_recommendation=signal.entry_recommendation if signal else None,
                v21_alert_eligible=bool(signal and signal.alert_eligible),
            )
        )
    results.sort(key=lambda row: (-row.challenger_score, row.symbol))
    return candidates, results, stats


def append_v22_shadow_results(
    results: list[ShadowChallengerResult],
    *,
    path: Path | None = None,
    now: datetime | None = None,
) -> int:
    if not results:
        return 0
    now = now or datetime.now(timezone.utc)
    target = path or SHADOW_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    with registry_lock(lock):
        with target.open("a", encoding="utf-8") as handle:
            for result in results:
                payload = result.as_dict()
                payload["record_type"] = "V22_SHADOW_CANDIDATE"
                payload["observed_at"] = now.isoformat()
                handle.write(json.dumps(payload, sort_keys=True, default=str) + "\n")
            handle.flush()
    return len(results)
