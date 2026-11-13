# OHM Price Movement Radar v1

## Purpose

The radar identifies conditions that may precede a large volatility expansion.
It classifies evidence as `PRICE_MOVEMENT / VOLATILITY_EXPANSION`; it does not
predict certainty, place an order, or bypass an OHM gate.

The radar separates two decisions:

1. **Magnitude readiness:** Is an unusually large move becoming plausible?
2. **Trade direction:** Has LONG or SHORT direction been confirmed strongly
   enough to continue through the existing OHM trade lifecycle?

`WATCH` and `READY` are non-actionable. Entry, stop, targets, and expiry are
attached only to `CONFIRMED` or `ACTIVE` movement evidence after the candidate
also survives Chief review, target attainability, economic quality, capital
allocation, portfolio risk, lifecycle, and notification deduplication.

## Data path

1. The Kraken universe scanner calculates symbol-relative Bollinger Band Width
   and ATR percentiles from completed 1H candles.
2. Only markets with possible hourly compression receive a second 15M OHLC
   request. Failure falls back to 1H and never rejects the market.
3. Existing Coinalyze enrichment uses the same four symbol-call units per
   finalist, but OI history is requested at 15M granularity. The provider
   derives 15M change, 1H change, 24H change, and a short-window acceleration
   z-score from that single OI response.
4. Existing Kraken execution evidence supplies observed depth asymmetry. Existing
   funding, liquidation totals, long/short ratio, relative volume, and cross-pair
   volume complete the score.
5. Signals are persisted locally and their absolute movement is observed at
   1H, 4H, and 12H using public Kraken ticker data. No paid AI call is used for
   movement learning.

## Score

| Component | Maximum | Evidence |
|---|---:|---|
| Compression | 30 | BBW percentile and ATR percentile |
| Leverage | 25 | OI 15M/1H acceleration while price remains quiet |
| Participation | 20 | 15M/1H relative volume and secondary-market volume |
| Liquidation fuel | 15 | Liquidation burst, funding crowding, long/short imbalance |
| Order-book fragility | 10 | Observed 0.5% bid/ask depth asymmetry after execution validation |

The score is deterministic context, not a probability, expected return, or
position-sizing input. It cannot rescue a candidate rejected by another gate.

## Lifecycle

| Stage | Semantics | Entry authority | Default window |
|---|---|---|---|
| `WATCH` | Compression plus a second evidence family | None | 4–12H |
| `READY` | Score threshold, leverage buildup, and at least three evidence families | None | 1–4H |
| `CONFIRMED` | 15M TradingView agreement or completed Kraken 15M range break with participation | Existing OHM gates only | 1–4H, then 4–12H |
| `ACTIVE` | A prior confirmed direction has expanded at least 1 ATR | Existing trade monitor | Now–4H, then 4–12H |
| `EXPIRED` | Watch window ended without confirmation | None | No trade |

When actionable, OHM reuses the existing `EntryExitPlan` exactly. The movement
layer does not calculate competing levels. Telegram shows the existing entry
zone, do-not-chase boundary, stop, T1, T2, reward/risk, expected ATR expansion,
timeframes, and four-hour entry-plan expiry.

## Configuration

```env
# off | shadow | alert
PRICE_MOVEMENT_MODE=shadow

PRICE_MOVEMENT_WATCH_SCORE=35
PRICE_MOVEMENT_READY_SCORE=70
PRICE_MOVEMENT_EXPIRY_HOURS=12
PRICE_MOVEMENT_ALERT_COOLDOWN_SECONDS=21600
```

- `off`: no movement evaluation or movement learning.
- `shadow`: default. Evaluate, persist, measure outcomes, and show dashboard
  telemetry, but send no movement-only Telegram message.
- `alert`: additionally send deduplicated, explicitly non-actionable
  `WATCH`/`READY`/`EXPIRED` Telegram updates. `CONFIRMED` movement context is
  shown only inside a fully approved OHM trade plan.

`COINALYZE_API_KEY` remains optional. Without it, the radar can record local
compression watches but cannot reach derivatives-backed readiness using missing
evidence as fake zeroes.

## Persistence and operations

- State and multi-horizon observations:
  `/app/data/price_movement_learning.json`
- Existing scan telemetry records WATCH/READY/CONFIRMED/ACTIVE counts.
- The operations dashboard shows movement stage counts and movement-learning
  observations.
- Routine movement learning makes zero OpenAI calls.

## Guardrails and known limitations

- Advisory only; Kraken credentials and execution behavior are unchanged.
- One existing Chief call remains the maximum per discovery cycle.
- TradingView ingress remains a durable candidate inbox and cannot call
  Telegram, providers, AI, or Kraken directly.
- Coinalyze supplies realized liquidation totals, not future liquidation prices.
  Therefore v1 measures liquidation bursts and does not claim CoinGlass-style
  heatmap proximity.
- Order-book fragility uses the validated current book snapshot. It does not yet
  claim time-persistent imbalance or spoofing resistance.
- No automatic threshold or live-weight promotion is permitted. Shadow evidence
  must be reviewed before changing thresholds or enabling `alert` mode.

## Verification

```bash
PYTHONPATH=. pytest -q tests/test_price_movement_radar.py
PYTHONPATH=. pytest -q
```
