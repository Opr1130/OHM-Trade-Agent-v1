# O'Pip ML Production Evidence Capture v1

## Purpose

Production Evidence Capture v1 converts O'Pip's existing canonical every-pair
scan evidence into ML Foundation v1 FeatureSnapshots without adding model
authority or another market-data request path.

## Authority boundary

- deterministic O'Pip decisions remain authoritative;
- capture runs only after active/pending protection, Early Watch, paper
  simulation, and event-intelligence work in the unified cycle;
- the worker performs local evidence I/O only;
- it has no exchange, order, position, Telegram, or execution dependency;
- capture, outcome maturation, and storage failures are fail-open;
- no XGBoost or LightGBM package is activated by this change.

## Point-in-time rule

The current scanner does not expose a trustworthy provider source timestamp for
each calculated technical feature. Production Evidence Capture therefore does
not fabricate one.

For values already computed in memory when the scan returns:

- source_at_utc = null
- ingested_at_utc = decision_at_utc
- visible_at_utc = decision_at_utc

This is conservative: it loses finer availability precision but cannot make a
feature appear available before O'Pip possessed it.

Old canonical rows that predate the ML feature seed are skipped. They are not
backfilled.

## Feature policy

The ML seed contains market/technical/momentum/volatility/liquidity evidence
such as price, EMA, RSI, MACD, ATR, volume ratio, range statistics, momentum,
ticker bid/ask, and cross-pair market measurements.

The following remain outside the independent ML feature mapping:

- opportunity_score
- decision_status
- suppressed
- candidate rank and other deterministic/ranking outputs

Those values may appear only in the capture wrapper's audit_context.

## Storage

FeatureSnapshots are immutable deterministic contracts and are appended to:

/app/data/opip_ml_feature_snapshots_v1.jsonl.gz

The gzip file is append-only compressed evidence. A separate durable checkpoint
tracks the accepted P1 evidence-ledger cursor.

Health:

/app/data/opip_ml_capture_health.json

Dead letter:

/app/data/opip_ml_capture_dead_letter.jsonl

## Activation

Capture follows the existing P1 evidence boundary and runs only when:

P1_SHADOW_OUTBOX_ENABLED=true

No additional production-trading flag is introduced.

## Outcome maturation

The worker reuses the existing Phase 3C forward-outcome maturation job on a
bounded 10-minute cadence. This produces fixed-horizon/MFE/MAE research
outcomes without reading them into live decisions.

Barrier TP/SL labels remain governed by the ML Foundation LabelEngine and are
not manufactured for broad-market episodes that have no declared trade plan.

## Exit evidence

A healthy production capture should show:

- temporal_violations = 0
- malformed = 0 or understood/dead-lettered
- processed > 0 after a new broad scan
- compressed FeatureSnapshot rows with max_visible_at_utc <= decision_at_utc
- Phase 3C outcomes maturing independently
- no change to deterministic alerts, risk gates, paper admission, or exchange
  authority
