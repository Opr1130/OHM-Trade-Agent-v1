# Configurable technical score V2 (LONG) — draft / no production authority

This is a narrowly scoped **shadow scoring function**. It is **not yet wired
into the running scanner**, qualification funnel, telemetry, Telegram, or
dashboard. Its presence cannot affect V1 scores, Top-8 selection, risk,
capital, IRR, learning, or funded orders.

## Configuration

Edit only `app/scanner/technical_score_v2.json` through a reviewed PR. This
contains six weights (must total 100), 14 numeric slope/boundary parameters,
threshold (75), and mode (`shadow`). Every profile is versioned. Invalid
configuration fails closed; setting mode to `production` is rejected.

The pure API is `load_v2_config()` and `score_long_v2(snapshot, features, config)`.
The caller must supply point-in-time higher-timeframe EMA50 trend, ADX,
candle-close location, rising MACD histogram, and a rolling ATR percentile.
No unavailable measurement is silently treated as zero or favorable.

The scorer has no API calls, disk writes, AI calls, Telegram, or trading
authority. It has no runtime feature-acquisition logic. The next separately
reviewed integration should compute the missing 4-hour/ADX features from
adequate completed candle history (or verified existing data), record V1/V2
scores in read-only telemetry, verify memory/API budgets, and perform outcome
maturation without activating learning. Avoid proxy higher-timeframe signals
based on insufficient 1-hour history.

## Baseline guardrails

Do not change `MIN_TECHNICAL_SCORE = 80`, `MAX_CANDIDATES = 8`,
per-direction cap, the current V1 scorer, target/economic/portfolio
vetoes, or the drift fail-closed contract. V2 threshold 75 is **not**
an authorized BUY threshold. A future promotion requires point-in-time
comparison, owner approval, and separate production change control.
