# Signal Quality v1 — Phase 2 Historical Validation

Phase 2 is a read-only validation and calibration harness for the Phase 1 Signal Quality / Explosion Detection pipeline. It does not change production thresholds, Telegram behavior, execution gates, Kraken permissions, trade authority, or order behavior.

## Objective

Measure whether the current Phase 1 priors surface genuine explosive movers early enough to be useful while suppressing thin-market and failed-breakout noise.

Outcome classes are evaluated separately:

- `MOVE_20`
- `MOVE_50`
- `MOVE_100`
- `MOVE_200`
- `MOVE_300_PLUS`

The initial report includes early-capture rate, first-detection timing, fraction of the eventual move already completed at first detection, false-positive summary, and Explosion Potential precision buckets.

## Event-sampling correction

`full_market_observations.jsonl` is not a regular scan log. Quiet periods are mostly heartbeat-persisted while active periods produce dense rows. A row-by-row replay would therefore overweight volatility and produce invalid persistence.

Phase 2 reconstructs a fixed 10-minute scan grid. At each grid boundary it uses only the latest observation already known at that time and carries it forward for a bounded freshness window. The carried value is timestamped as that reconstructed scan, matching the fact that production ran a scan even when the JSONL writer did not persist a new event. A flat carried value naturally breaks momentum persistence rather than creating artificial momentum.

No future row can enter an earlier reconstructed frame.

## Outcome-label limitation

The first implementation labels forward moves from later persisted `last_price` observations. That is deliberately conservative: event sampling may miss an intraperiod high. Therefore the report status is `PROVISIONAL_EVENT_SAMPLED_REPLAY` and **must not be used to automatically tune production**.

Before any calibration recommendation is adopted, major-mover labels should be cross-validated against time-aligned OHLC data so +20/+50/+100/+200/+300% peaks are not undercounted.

## Running

Inside the application container:

```bash
python -m app.jobs.report_signal_quality_phase2
```

The command prints JSON only. It does not modify the Phase 1 configuration or write production decisions.

## Safety invariants

- no automatic tuning
- no production threshold changes
- no Telegram sends
- no order placement or cancellation
- no position or fill mutation
- no Kraken private execution calls
- no use of future data in feature generation
- outcome data is used only after a decision-time frame has been scored

## Interpretation

A high Explosion Potential score is still a heuristic score, not a probability. Phase 2 exists to determine whether the ordering and stage thresholds have empirical value. Calibration recommendations remain proposals until independently reviewed, tested out-of-sample, and explicitly approved.
