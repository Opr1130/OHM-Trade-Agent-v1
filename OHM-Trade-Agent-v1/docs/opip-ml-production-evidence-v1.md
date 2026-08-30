# O'Pip ML Production Evidence Capture v1

## Purpose

Production Evidence Capture v1 converts O'Pip's existing canonical every-pair
scan evidence into ML Foundation v1 FeatureSnapshots without adding model
authority or another market-data request path.

## Authority boundary

- deterministic O'Pip decisions remain authoritative;
- the canonical scan producer only adds immutable feature seed data to the
  already-existing evidence outbox;
- the ML consumer does not run inside the unified production cycle at all;
- ML capture and outcome maturation use independent cron jobs and independent
  flock locks;
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
tracks the accepted P1 evidence-ledger cursor. Before appending, the worker
reconstructs deterministic ML snapshot identities from the compressed store, so
a crash after a durable write but before checkpoint persistence is deduplicated
on retry.

Health:

/app/data/opip_ml_capture_health.json

Dead letter:

/app/data/opip_ml_capture_dead_letter.jsonl

## Activation

Capture follows the existing P1 evidence boundary and runs only when:

P1_SHADOW_OUTBOX_ENABLED=true

No additional production-trading flag is introduced.

## Independent scheduling

The production scheduler installs a separate O'Pip ML evidence cron file:

- ML FeatureSnapshot capture: every minute, lock
  /var/run/opip-ml-capture.lock
- Phase 3C forward-outcome maturation: every 10 minutes, lock
  /var/run/opip-ml-outcomes.lock

Neither job uses /var/run/ohm-unified-cycle.lock. This prevents evidence volume,
compression, ledger reads, or outcome maturation from delaying deterministic
risk protection.

The existing Phase 3C job produces fixed-horizon/MFE/MAE research outcomes
without reading them into live decisions.

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
