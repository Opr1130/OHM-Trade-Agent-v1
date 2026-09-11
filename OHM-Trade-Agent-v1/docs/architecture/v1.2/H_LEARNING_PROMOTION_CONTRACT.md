# H. Learning / promotion contract

## Lifecycle

```
Hypothesis
  → preregistered experiment
  → shadow / research simulation
  → sealed prospective evaluation
  → human release
```

## Outputs

- Learner outputs are versioned and immutable.
- A promotion artifact includes a version hash, scope, effective time, and rollback rules.
- Consumption may be live, advisory, shadow, rejected, quarantined, superseded, or otherwise explicitly governed.
- Consumption does **not** authorize autonomous policy changes, live ranking influence, threshold mutation, or trading authority.

## Safety suspension and resumption

- Automatic safety suspension is allowed.
- Resumption requires explicit human approval (Technical Release Owner or later designated human).
- No autonomous policy modification.

## Drift

Learning-worker release compatibility is **exact SHA** with production (`CURRENT` / `RELEASE_DRIFT` / `UNVERIFIED`).

Capture and outcomes fail closed on release drift. Evidence sync may still run for diagnostics.

Busy/memory skips and blocked admissions write durable dispositions. Silent exit-0 without a record is a defect.

## Plane separation

- Learning never opens the live operational SQLite file over the network (once that file exists).
- Learning reads verified immutable exports only.
- No Kraken or Telegram credentials on the learning worker.
- Core `/deploy` does not update the learning worker; a matching owner-gated `/deploy-learning <40-char-sha>` is required.

## Machine learning

Machine learning is deferred until empirical/statistical baselines are demonstrably inadequate and sufficient prospective data exist. SQ-01 is **not** started.

`SIGNAL QUALITY SQ-01 STARTED = NO`
