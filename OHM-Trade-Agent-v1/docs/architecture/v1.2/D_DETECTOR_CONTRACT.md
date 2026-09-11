# D. Detector contract

## Interface

```
evaluate(snapshot, prior_state, evaluation_time) -> (claims, next_state)
```

Types (logical; not implemented in PR 1):

- `snapshot`: `FeatureSnapshot`
- `prior_state`: `DetectorState`
- `evaluation_time`: explicit datetime; no hidden clock
- `claims`: list of `DetectorClaim`
- `next_state`: `DetectorState`

See [fixtures/detector_evaluate.example.json](fixtures/detector_evaluate.example.json).

## Purity

`evaluate()` has no network, disk, database, internal clock, or global mutable state.

## Ownership

| Concern | Owner |
| --- | --- |
| Transition semantics (IGNITION on/off, hysteresis) | Detector |
| Persistence of `DetectorState` | Runtime via canonical writer |
| Episode identity, deferral deadline, terminal reason | Opportunity lifecycle |
| Claim idempotency | Keyed to the actual transition, not the wall clock |

## Hysteresis, debounce, elapsed-time

- Hysteresis and debounce parameters are versioned policy.
- Persistence timers do **not** accrue across a material feed gap.
- Affected persistence evidence resets and requires revalidation.

## Deferral and expiry

- Deferred opportunities carry a deadline bounded by the validity horizon.
- They terminate with an explicit reason.
- Expired claims cannot resume without a new evaluation.

## v1 detector family

**IGNITION only.**

Cold-start cross-sectional substitution is a separate **shadow hypothesis only**. Insufficient calibration blocks approved-selector use but may not block preregistered research simulation in the research account.

## Evaluation cadence

IGNITION evaluates on the **1-minute** grid (N10). 15-minute data may appear inside the snapshot as a feature; it does not set evaluation time.
