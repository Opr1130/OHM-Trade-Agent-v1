# B. Market / data contract

## Distinct time and order facts

These remain separate and must not be collapsed:

| Fact | Meaning |
| --- | --- |
| Source event time | Time assigned by the venue or feed for the observation |
| Receipt time | Local time the process first received the payload |
| Ingestion order | Order observations entered the validated-observation path |
| Source sequence | Venue sequence if present; may be gapped or reused after reconnect |
| Commit order | Writer-assigned `(history_epoch, local_sequence)` |

Recovery from an older off-host snapshot opens a **new `history_epoch`** so identities are not reused.

## Schema and corrections

- Every event carries `schema_version`.
- Old events are read through deterministic read-time conversion.
- Missing historical fields remain unknown; they are never fabricated.
- Historical corrections **append superseding events**. Historical events are never rewritten.

## Persistence policy

- Raw Kraken trade events are **ephemeral bounded inputs**. Do not persist every raw trade as canonical history unless a later declared replay requirement justifies it.
- Persist **fixed-interval aggregates** sufficient for declared v1 IGNITION features.
- Persist versioned `FeatureStateCheckpoint` records tied to consumed-input watermarks.
- Persist every actual detector-evaluation `FeatureSnapshot`, including evaluations that produce no claim.
- Persist claim, selection, execution evidence, and all gap / reset / restart scheduling events.

## Evaluation grid (N10 — ratified)

| Clock | Cadence |
| --- | --- |
| Aggregate / watermark | **1 minute** |
| IGNITION evaluation | **1 minute** |
| 15-minute candles | Allowed as **features or paper-policy inputs only** |

The 15-minute paper candle interval **must not** dictate early detection cadence.

Warm-up follows the longest required feature window plus estimator-stability requirements. No fixed bar count is an architecture constant. Today's `MIN_CANDLES_REQUIRED=200` is current scanner practice, not a v1 constant.

## Restart and coverage states

These are distinct facts:

- `NEW_LISTING_COLD_START`
- `INSUFFICIENT_HISTORY`
- `RESTART_WARMUP`

Cross-sectional cold-start substitution remains a **shadow-only** research hypothesis.

## Feed-gap and late/out-of-order policy

- Material feed gaps reset persistence evidence. Persistence timers do not accrue across the gap.
- Late or out-of-order input does not fabricate fills or favorable features.
- A coverage gap before outcome resolution is `INCOMPLETE_COVERAGE`, not a timeout and not a silent drop.
- Missing evidence is never favorable evidence.

## Feature recomputation vs detector replay

- Checkpoints + complete aggregate deltas reconstruct supported rolling features.
- Exact `FeatureSnapshot` records reproduce detector decisions.
- These are separate capabilities.

## Retention

Until the canonical SQLite exists, retain the existing bounded JSONL HOT / WARM / COLD machinery. Exact day counts stay configurable and are not frozen as architecture constants in PR 1.

See [fixtures/observation.example.json](fixtures/observation.example.json) and [fixtures/feature_snapshot.example.json](fixtures/feature_snapshot.example.json).
