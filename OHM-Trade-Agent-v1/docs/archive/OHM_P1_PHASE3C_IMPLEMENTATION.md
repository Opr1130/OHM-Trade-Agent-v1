# OHM P1 / Phase 3C Foundation — Implementation

Status: research/shadow implementation. No production ranking or execution authority.

## What is implemented

- Canonical immutable `LiveScanSnapshot`, exchange-agnostic market-data contracts, and context-only catalyst contract.
- Dark-by-default local durable outbox controlled by `P1_SHADOW_OUTBOX_ENABLED` (default false).
- Candidate snapshots preserve the existing incoming ranked order and include suppressed candidates.
- Snapshot producer is local append-only I/O only; it performs no external API call and no P1 computation.
- Producer is invoked only from the existing post-alert Phase 3B shadow function, before its external OHLC reads.
- Separate outbox worker with durable line checkpoint, malformed-row dead-lettering, retry-on-processor-failure semantics, and idempotent immutable evidence ledger writes.
- Offline Phase 3C point-in-time join keyed by `(symbol, original decision timestamp)`.
- Episode-level first-detection deduplication, chronological 60/20/20 split, deterministic episode-level bootstrap confidence intervals, fixed-horizon/MFE/MAE summaries, and rank/liquidity/structure/retest/chase buckets.
- Explicit top-8 Phase 3B structure cohort bias reporting.
- Gate-0 evidence readiness only. The harness cannot promote a feature or modify production settings.
- Offline report job that reads immutable snapshot/Phase-3B/outcome files and writes `phase3c_verified_edge_report.json`.

## Files / paths

- Outbox: `/app/data/p1_shadow_outbox.jsonl`
- Evidence ledger: `/app/data/p1_evidence_ledger.jsonl`
- Checkpoint: `/app/data/p1_shadow_outbox_checkpoint.json`
- Dead letter: `/app/data/p1_shadow_outbox_dead_letter.jsonl`
- Offline outcome labels: `/app/data/phase3c_forward_outcomes.jsonl`
- Phase 3C report: `/app/data/phase3c_verified_edge_report.json`

The outcome-label file is offline evidence. It must be generated from point-in-time replay/forward-outcome tooling and must never be imported into live feature computation.

## Activation

`P1_SHADOW_OUTBOX_ENABLED` defaults to false. The code may be merged while remaining dark. Enabling the producer or scheduling the worker in production is a separate operational behavior/configuration decision and requires the normal explicit production approval process.

## No-lookahead rules

- `LiveScanSnapshot.decision_at_utc` is required and timezone-aware.
- The snapshot builder has no wall-clock fallback.
- `MarketDataSlice` rejects bars with `closed_at_utc > requested_end_at_utc`.
- Phase 3C joins structure/outcomes to a snapshot only by canonical symbol and the original decision timestamp.
- Forward outcomes exist only in the offline label side of the join.

## Promotion safety

Every Phase 3C report contains:

- `provisional: true`
- `score_is_probability: false`
- `auto_promotion_allowed: false`
- `trade_authority_changed: false`
- `production_execution_gate_changed: false`

Gate 1 and later promotion decisions remain governed by `OHM_P1_PROMOTION_GATES.md` and require separate evidence review and explicit approval.

## Explicit non-goals

This implementation does **not**:

- alter Phase 1 ranking or thresholds;
- change Telegram alert semantics;
- mutate PendingSetup;
- place, confirm, cancel, or modify an order;
- add shorts, leverage, or perpetuals;
- add a second market scanner;
- add Kafka, Redis, or a paid queue;
- use catalyst context in Phase 3D scoring;
- automatically tune or promote a signal.
