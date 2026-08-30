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

Price semantics are source-specific. The contemporaneous ticker is stored as
ticker_last/reference_price. completed_close is populated only for
LIVE_OPPORTUNITY_SCAN, where last_price is explicitly a completed-candle close;
ticker-only LIVE_FULL_MARKET observations leave completed_close missing.

The following remain outside the independent ML feature mapping:

- opportunity_score
- decision_status
- suppressed
- candidate rank and other deterministic/ranking outputs

Those values may appear only in the capture wrapper's audit_context.

## Storage

FeatureSnapshots are immutable deterministic contracts stored as atomic,
compressed JSONL batch chunks under:

/app/data/opip_ml_feature_snapshots_v1/

Each batch is serialized and gzip-compressed in memory, fsynced to a temporary
file, and atomically renamed into the snapshot directory. An interrupted write
therefore cannot publish a truncated gzip member. Chunk names are deterministic
from the consumed ledger range and compressed payload; if a crash occurs after
the chunk rename but before checkpoint persistence, the retry recognizes the
same chunk instead of publishing duplicate training evidence.

The durable checkpoint stores both next_line and byte_offset. Each capture pass
seeks directly to the last committed byte in the P1 evidence ledger and reads
only a bounded batch of new complete JSONL rows. Historical snapshot chunks and
the historical ledger prefix are not rescanned on every minute.

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
- atomic compressed FeatureSnapshot chunks with max_visible_at_utc <= decision_at_utc
- byte_offset advancing monotonically without historical rescans
- Phase 3C outcomes maturing independently
- no change to deterministic alerts, risk gates, paper admission, or exchange
  authority
