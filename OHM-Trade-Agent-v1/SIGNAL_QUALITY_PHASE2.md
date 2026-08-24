# Signal Quality v1 — Phase 2 Historical Replay and Calibration Harness

Phase 2 is a **read-only, offline** validation harness for the Phase 1 Signal
Quality / Explosion Detection pipeline. It changes no threshold, sends no
Telegram message, places no order, mutates no position, touches no private
Kraken endpoint, and has no deployment side effect.

It answers one question: **does the Phase 1 detector surface genuine explosive
movers early, and how often does it fire on moves that never arrive?**

> **Status: `PROVISIONAL_EVENT_SAMPLED_REPLAY`.** Until OHLC cross-validation is
> supplied and sample counts are adequate, every number this harness produces is
> provisional. It must never be presented as validated production truth, and it
> must never be used to tune production automatically.

---

## 1. Why a naive replay is wrong

`full_market_observations.jsonl` is **event-sampled**, not a scan log.
`_should_persist()` drops quiet scans and keeps active ones, so quiet periods
may persist only an hourly heartbeat while an explosive run persists many rows.

Replaying rows directly would therefore:

- hand busy periods extra "scans" they never had,
- manufacture persistence out of event density alone,
- and oversample exactly the volatile periods whose outcomes we are measuring.

Production `scan_movers` runs every **10 minutes**, regardless of what was
persisted. The replay must reconstruct that decision grid.

## 2. Time-correct scan reconstruction

A fixed 10-minute grid is built. At each boundary, a symbol carries the latest
observation known **at or before** that boundary, for a bounded freshness
window (`max_carry_seconds`, default 3600s, matching production stale-history
retention).

| Property | Guarantee |
| --- | --- |
| Frame count and timestamps | Depend only on elapsed time, never on event density |
| Admission rule | `observed_at <= scan_at`; no later row can enter an earlier frame |
| Carried values | Re-timestamped to the scan, and flagged `imputed=True` |
| Stale values | Expire after the carry window; the symbol leaves the frame |
| Flat carries | Produce zero price change, so they *break* a persistence chain rather than extending one |

**Imputed vs observed is reported, not hidden.** Every `ScanCell` records its
`source_at` and an `imputed` flag, and the report publishes
`observed_cells`, `imputed_cells` and `imputed_cell_pct`. A carried cell is a
real production scan — Phase 1 genuinely scanned then — but its *values* are
stale, and stale values are never presented as fresh observations.

### Why carrying forward is defensible, and how far it can be wrong

Last-observation-carried-forward is justified here by *why* a row is missing.
`_should_persist` writes a row **because** something moved:

| Trigger | Threshold |
| --- | --- |
| Price change | ≥ 1.0% |
| Lift change | ≥ 0.75% |
| High-distance change | ≥ 0.75% |
| Notional ratio | ≥ 1.50× |
| Heartbeat | every 3600s regardless |

So the absence of a row between two scans is itself evidence the market was
quiet — price moved *less than 1%* — rather than evidence of missing data. The
imputation error is therefore bounded by Phase 1's own thresholds, with the
hourly heartbeat as the one exception. `CARRY_*_BOUND` constants mirror those
thresholds and `test_carry_error_bounds_match_phase_1` fails if the runtime
changes and the replay does not.

The bound is theory; the report also **measures** it.
`measure_carry_fidelity` compares every carried price against the next real
observation for that symbol and publishes the realised drift distribution
(count/p25/median/p75), how many carries exceeded the 1% bound, and the carry
age distribution, under `reconstruction_fidelity`. A carry with no later
observation is counted as `imputed_cells_unresolved` rather than assumed to be
zero drift. If more than 20% of carries exceed the bound, the report raises
`CARRY_DRIFT_HIGH` — reconstructed features are correspondingly less reliable
and capture rates should be discounted.

## 3. Replaying the exact Phase 1 pipeline

No alternate scorer exists. The replay calls the production functions directly:

- `signal_features.derive_features_for_universe`
- `signal_features.derive_universe_percentiles`
- `signal_scoring.evaluate_universe`

It also mirrors production's retention and scoring scope: a symbol unobserved
past the retention window loses its history, and only symbols present in the
current scan are scored — retained history exists for when a symbol returns,
never to rank stale snapshots as current.

Relative strength is computed across the whole universe **observable at that
scan**, so the percentile is not selection-biased and cannot see a symbol that
had not yet appeared.

## 4. Episode de-duplication — one run, one episode

Labelling every persisted row produces hundreds of overlapping 24-hour windows
over a single move. Each would count as an independent "winner", inflating
every capture rate by the density of the run itself.

Instead, each symbol's history is collapsed into distinct **episodes** via a
baseline → trigger → peak → reset cycle:

1. A running **baseline** tracks the lowest price within a trailing window
   (`baseline_window_hours`, default 24h).
2. An episode **opens** the first time price rises `trigger_pct` (default 20%)
   above that baseline. The baseline low and its timestamp anchor the episode.
3. The episode **runs** while price makes new highs, tracking the peak.
4. It **closes** on a `close_retrace_pct` (default 30%) retracement from the
   peak, on a stale peak (`peak_cooldown_hours`, default 12h), or at end of data.
5. The baseline **resets** to the lowest price after the peak, so the next
   episode requires a genuine new advance from a new low.

One DENT-style run is therefore **one** episode with one baseline, one peak and
one set of threshold-crossing timestamps — not hundreds of overlapping windows.
`test_one_explosive_run_is_one_episode` pins this against a densely sampled run.

### Episode parameters are priors, so their effect is measured

The trigger, retrace, baseline-window and cooldown values are **not calibrated**
and they directly determine how many "winners" exist to be captured. A capture
rate quoted without them is not interpretable: if halving the trigger doubles
the episode count, the headline rate is an artefact of the parameter rather
than a property of the detector.

Every report therefore includes `episode_parameter_sensitivity` — a sweep of
episode counts and class distributions across trigger ∈ {15, 20, 30} ×
retrace ∈ {20, 30, 50}, with the active default marked. If totals vary by 3× or
more across that sweep, the report raises
`EPISODE_COUNT_PARAMETER_SENSITIVE` and the capture rates must not be quoted
without the sweep beside them.

Each episode records the first time it closed at or above +3, +5, +10, +20, +50,
+100, +200 and +300 percent from its baseline. Its class comes from its peak
return: `MOVE_20` / `MOVE_50` / `MOVE_100` / `MOVE_200` / `MOVE_300_PLUS`.

## 5. Detections are evaluated only on movement *after* the detection

This is the correction at the heart of Phase 2, and the flaw the first draft
carried. That draft credited a detection for lying inside a 24-hour window
whose major move may have **completed before the detection existed** — which
inverts cause and effect and would make a late detection look prescient.

Two independent mechanisms now enforce post-detection-only credit:

**Forward returns.** `SymbolTimeline.forward_maxima` takes the maximum price
over the half-open interval `(t, t + horizon]` — strictly exclusive of the
detection's own timestamp. A detection's forward return is measured from *its
own price at its own time*. A spike that finished before it contributes nothing.
`test_forward_return_is_measured_from_the_detection_not_the_window` builds
exactly that case: price doubles and fully retraces before the detection, and
the detection is classified `FAIL_LT_5` with a negative forward return.

**Early-capture credit.** For an episode, a detection counts only if it lands in
`[baseline_at, peak_at)`, and "detected before +X" requires
`detection_at < crossing_time[X]` — strictly earlier than the actual crossing.
Tests cover a detection after the peak, a detection after a threshold, and a
detection exactly *at* a crossing; none receives credit for the move it missed.

## 6. Metrics

Per class (`MOVE_20` … `MOVE_300_PLUS`), counted in **episodes**, not windows:

- distinct episodes, detected-before-peak count, early-capture rate
- detected before +5 / +10 / +20, with rates
- median lead time from first detection to peak
- median fraction of the total move already completed at first detection
- missed episodes
- first-detection profile: stage counts, and quartiles for explosion potential,
  opportunity, liquidity, persistence and exhaustion

**False positives** are judged prospectively from each detection's own
timestamp, bucketed `FAIL_LT_5` / `MOVE_5_10` / `MOVE_10_20` / `MOVE_20_50` /
`MOVE_50_PLUS`, with +20% precision broken out by stage, explosion-potential
bucket, opportunity bucket and liquidity band. Detections whose forward horizon
is not fully covered by data are excluded from precision and counted separately.

**Missed-winner forensics** reconstruct what OHM knew at the last scan before
each of +3/+5/+10/+20/+50 for every undetected major episode — all scores,
reason codes, persistence, exhaustion, liquidity and stage. This is the report
that explains *why* a winner was missed. Audit rows are retained only for
symbols that actually produced an episode, which bounds memory.

**Winner vs failed breakout** compares descriptive distributions (count, p25,
median, p75) between detections preceding a ≥+20% forward move and detections
failing to reach +5%. Descriptive only — no model is fitted, no weight tuned.

## 7. Out-of-sample discipline

The harness splits **chronologically** at `calibration_fraction` (default 0.6)
of the detection timeline and reports +20% precision separately for the
calibration and validation periods. A random split across adjacent scans would
place near-identical decisions on both sides and make the held-out set
meaningless. Any threshold observation must hold on the validation period
before it is proposed, and nothing is ever applied automatically.

## 8. OHLC cross-validation

The persisted stream can miss intraperiod highs, so episode peaks drawn from
`last_price` are a floor, not a truth.

`OhlcProvider` abstracts a read-only historical OHLC source:

- `NullOhlcProvider` (default) validates nothing and the report says so.
- `KrakenPublicOhlcProvider` uses only `KrakenClient.get_ohlc`, a **public**
  endpoint, and fails soft — outcome validation may never break a report.
- `CachedOhlcProvider` reads candles from a local JSONL cache: **offline and
  deterministic**, so a validated report is reproducible instead of depending
  on whatever the exchange returns that minute. Build a cache once with
  `--write-ohlc-cache`, then validate repeatedly with `--ohlc-cache`.
- Tests use a deterministic in-memory fixture provider. **No unit test touches
  the network**, so CI stays deterministic.

`write_ohlc_cache` is the only function in the module that writes a file, and
it writes only to an operator-chosen cache path — never a production registry.

Validation is **outcome-side only**. It never feeds feature generation, so it
cannot leak future information into a decision. Both the event-sampled and the
OHLC peak are retained on the episode so undercounting stays visible rather
than being silently corrected. When any episode is OHLC-validated the report
status becomes `OHLC_CROSS_VALIDATED_REPLAY`.

## 9. Performance

Production history is large, so the naive approach — rescanning all future rows
for every row — is quadratic and unusable. Instead:

- observations are grouped into per-symbol timelines once,
- forward maxima use a **monotonic deque** sliding window, O(n + m) per symbol,
- episode baselines use a monotonic deque running minimum,
- timeline lookups use `bisect`,
- scan history is a bounded `deque`, and audit retention is scoped to episode
  symbols only.

## 10. Report contents

`source_lines`, `rejected_lines`, `observation_rows`, `symbols`, date coverage,
`reconstructed_scans`, observed/imputed cell counts and share, `detections`,
`episodes`, `major_move_episodes`, per-class episode counts, detections with an
incomplete forward window, OHLC coverage, `reconstruction_fidelity` (measured
carry drift), `episode_parameter_sensitivity` (the prior sweep), and an explicit
`warnings` list: missing OHLC validation, high carry drift, parameter-sensitive
episode counts, small validation sample, rejected rows, no episodes, nothing to
replay.

## 11. Running

```bash
# Offline, provisional (default)
python -m app.jobs.report_signal_quality_phase2

# Build a reusable OHLC cache once (the only file-writing mode)
python -m app.jobs.report_signal_quality_phase2 --write-ohlc-cache /tmp/ohlc.jsonl

# Reproducible OHLC-validated run, offline
python -m app.jobs.report_signal_quality_phase2 --ohlc-cache /tmp/ohlc.jsonl

# Or validate straight against the public endpoint
python -m app.jobs.report_signal_quality_phase2 --ohlc
```

Prints JSON to stdout. Apart from `--write-ohlc-cache`, reads only; writes
nothing.

## 12. Safety invariants

- read-only and offline; no file is written
- no automatic tuning, no weight fitting, no model promotion
- no production threshold or `.env` change
- no Telegram sends
- no order placement, cancellation, or confirmation
- no trade registration or position mutation
- no private Kraken calls — public OHLC read only
- no future data in feature generation
- outcome data consulted only after a decision has been scored

Tests assert these boundaries, including a source-level check that the module
references no execution, position-verification, registration or Telegram
surface.

## 13. Interpretation

A high Explosion Potential score remains a heuristic score, not a probability.
Phase 2 exists to determine whether the Phase 1 ordering and stage thresholds
have empirical value. Any calibration recommendation stays a proposal until it
is independently reviewed, holds out-of-sample, and is explicitly approved.
