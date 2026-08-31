# O'Pip ML Foundation v1

Status: architecture foundation; evidence only; no production ML activation.

## Permanent authority boundary

The deterministic O'Pip Decision Engine remains authoritative. ML Foundation
code cannot place, modify, cancel, confirm, or authorize exchange orders and
cannot mutate deterministic risk/safety configuration.

## Point-in-time invariant

ML features are eligible only when `visible_at <= decision_at`. Each feature
carries mandatory `ingested_at` and `visible_at` plus a source version.
`source_at` is retained when the provider exposes a trustworthy event timestamp;
it remains explicitly absent when the provider does not. O'Pip never fabricates
source time from receipt time. Late or revised information is not allowed to
rewrite historical decision-time state.

## FeatureSnapshot

`FeatureSnapshot` is immutable and hash-addressed. Deterministic score and
classification can be retained as audit context but are deliberately excluded
from the primary independent ML feature mapping.

## Labels

Labels are direction-aware. Each price bar declares its covered interval and
actual `visible_at`; fixed-horizon closes carry their own availability time.
The full barrier path must be contiguous through the declared horizon or the
record is censored. Same-bar target/stop collisions are not assigned an invented
order: when finer-grained evidence is unavailable the affected label is marked
ambiguous/censored and excluded from the primary supervised training cohort.
Label IDs hash the exact immutable label payload, including availability and
cost-model versions.

## Dataset/validation

Dataset manifests are immutable and versioned. Censored, ambiguous, data-gap,
late-label, schema-mismatched, cost-model-mismatched, and semantically mislinked
rows are excluded with explicit reasons. Included rows bind the exact snapshot
and label identities used for training.
Financial validation uses chronological splits with purge and embargo semantics;
random train/test splitting is not the principal validation path.

## Model governance

Lifecycle is `REGISTERED -> VALIDATED -> SHADOW -> CHALLENGER`. Promotion into
`CHALLENGER` requires explicit human approval metadata. Statistical degradation
requires adequate sample support; structural failures may suspend evidence.
Automatic promotion is not implemented.

## Model runtime

Foundation v1 defines a provider-neutral `ModelAdapter` only. XGBoost and
LightGBM dependencies are intentionally not added yet. The same adapter contract
will support both, while only one shadow challenger should be active at a time
after offline evidence selects it.

## Reuse of existing O'Pip infrastructure

This foundation is designed to reuse existing canonical episode identity,
event point-in-time semantics, bounded asynchronous queue patterns, telemetry,
paper outcomes, and existing audit/learning infrastructure rather than create a
parallel trading path.
