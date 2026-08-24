# Signal Quality v1 — Phase 2 Historical Replay and Calibration Harness

Phase 2 is a **read-only, offline** validation harness for the Phase 1 Signal
Quality / Explosion Detection pipeline. It changes no threshold, sends no
Telegram message, places no order, mutates no position, touches no private
Kraken endpoint, and has no deployment side effect.

It answers one question: **does the Phase 1 detector surface genuine explosive
movers early, and how often does it fire on moves that never arrive?**

> **Status: `PROVISIONAL_EVENT_SAMPLED_REPLAY` — always, in this harness.**
> Every number it produces is provisional. It must never be presented as
> validated production truth, and never used to tune production automatically.
> OHLC coverage does **not** upgrade this status: see §8.

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

### Why carrying forward is defensible, and what the drift number is not

Last-observation-carried-forward is justified here by *why* a row is missing.
`_should_persist` writes a row **because** something moved:

| Trigger | Threshold |
| --- | --- |
| Price change | ≥ 1.0% |
| Lift change | ≥ 0.75% |
| High-distance change | ≥ 0.75% |
| Notional ratio | ≥ 1.50× |
| Heartbeat | every 3600s regardless |

Crucially, `_should_persist` compares against the last **persisted** row —
which is exactly the value the replay carries — so silence implies the carried
value was within those thresholds of the truth. `CARRY_*_BOUND` constants
mirror the thresholds and `test_carry_error_bounds_match_phase_1` fails if the
runtime changes and the replay does not.

#### The bound is conditional, not absolute

`_should_persist` returns `NO_MEANINGFUL_CHANGE` only when **all** of these held
at that scan:

1. the production scan actually ran;
2. the symbol was actually observed in it — `collect_full_market_observations`
   fail-softs on a ticker batch error, and an unobserved symbol has no persist
   decision at all;
3. the last persisted row was under 3600s old, since the heartbeat writes
   regardless of movement;
4. the prior row parsed as finite (`INVALID_PRIOR_STATE` forces a write).

Where any precondition fails, **silence is not evidence of a quiet market** and
the bound simply does not apply. The replay cannot tell "quiet" from "not
observed" from the JSONL alone — the same ambiguity Phase 1's own retention
design has to live with — so rather than assume the bound holds everywhere, the
report counts the cells where it provably cannot:
`bound_inapplicable_cells_beyond_heartbeat` are carries at or past the heartbeat
interval, for which a heartbeat row *should* have existed. Past 5% of carries
the report raises `CARRY_BOUND_INAPPLICABLE`. The block also publishes
`bound_is_conditional: true` and the precondition list itself, so the caveat
travels with the number.

The theoretical bound is the defensible statement. The report also publishes a
companion diagnostic, `reconstruction_drift_proxy`, but it is important to be
exact about what that is:

> **It is not the reconstruction error, and it cannot be.** The true error of a
> carried value is its distance from the contemporaneous market price at that
> scan — a price that was never recorded, which is precisely why there was
> something to carry. Point-in-time reconstruction error is **unknowable**
> without a live scan log.

`measure_persistence_gap_drift` measures the distance from each carried value to
the **next persisted observation**. That row exists *because* the market moved
past a persist threshold, so the gap bundles two different things: how stale the
carried value was, and how much the market moved after the scan. It therefore
tends to **overstate** carry error, and ordinary later movement can show up here
as though it were reconstruction distortion.

It is still worth reporting as an upper-bound-flavoured uncertainty proxy: a
small distribution is genuine reassurance, a large one is a reason to distrust
reconstructed features. The block carries
`is_point_in_time_reconstruction_error: false` and an `interpretation` string so
the distinction travels with the numbers. A carry with no later observation is
counted `imputed_cells_unresolved` rather than assumed zero-drift. Past 20%
exceedance the report raises `PERSISTENCE_GAP_DRIFT_HIGH`, itself carrying the
caveat that exceeding the bound does not prove the carried value was wrong.

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
+100, +200 and +300 percent from its baseline.

### Exclusive classes vs cumulative cohorts

Two different questions, so two separately named blocks — previously one set of
`MOVE_*` keys served both and could be misread as mutually exclusive:

| Block | Keys | Counting |
| --- | --- | --- |
| `detection_metrics_by_exclusive_class` | `MOVE_20_50`, `MOVE_50_100`, `MOVE_100_200`, `MOVE_200_300`, `MOVE_300_PLUS` | **Mutually exclusive** half-open bands `[low, high)`. Each episode appears once; the bands sum to the episode total. |
| `detection_metrics_by_threshold_cohort` | `GE_20`, `GE_50`, `GE_100`, `GE_200`, `GE_300` | **Cumulative and overlapping.** A +320% episode is in all five. Do not sum. |

An episode's own `outcome_class` uses the exclusive vocabulary, so
`episodes_by_class` and the exclusive metrics agree key for key. Each block
states its counting rule inline, and both are covered by tests.

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
bucket, opportunity bucket and liquidity band.

### The judged population requires a complete forward window

```
judged  <=>  forward_max_return_pct is not None  AND  window_complete is True
```

Both conditions, not just the first. A detection near the right edge of the
dataset has a future print but not a full horizon; admitting it would let the
data simply running out register as a failure — or let a truncated partial spike
register as a win. Such rows enter **no** rate, precision table, cohort or
split. They are reported separately under
`false_positives.excluded_incomplete_window` with their own bucket counts, and
`coverage.detections_with_incomplete_forward_window`, so they are excluded but
never silently discarded. Five tests pin this, each failing if the
`window_complete` condition is dropped.

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
  deterministic**, so a report is reproducible instead of depending on whatever
  the exchange returns that minute. Build a cache once with
  `--write-ohlc-cache`, then validate repeatedly with `--ohlc-cache`. It
  normalises symbol case, deduplicates by `(symbol, start_at)`, skips the format
  header, and counts `rejected_rows` / `duplicate_rows` rather than dropping
  them silently.
- Tests use a deterministic in-memory fixture provider. **No unit test touches
  the network**, so CI stays deterministic.

`write_ohlc_cache` is the only function in the module that writes a file, and
it deduplicates candles by `(symbol, start_at)`, since overlapping episodes on
one symbol would otherwise write the same candle repeatedly.

### Cache file identity guard

The writer **refuses to truncate an existing file that is not already an OHLC
cache**, raising `OhlcCacheTargetError` — a mistyped path aimed at a production
registry fails loudly instead of destroying it. The check fails closed, and a
file passes only by:

1. carrying the `ohm-phase2-ohlc-cache-v1` header the writer emits on line one;
2. being empty; or
3. having **every** one of its first 200 non-blank lines candle-shaped, which
   lets a hand-built cache be replaced without demanding a header.

Requiring *every* sampled line, not just the first, is the load-bearing part: a
production JSONL registry could plausibly open with a row that happens to carry
`symbol` / `start_at` / `high`, and truncating it would be unrecoverable.
Non-regular files (directories, devices, symlinks to them) and files that are
not valid UTF-8 JSON are refused outright.

Validation is **outcome-side only**. It never feeds feature generation, so it
cannot leak future information into a decision.

### OHLC does not upgrade the report status

`validate_episodes_with_ohlc` attaches `ohlc_peak_return_pct` and deliberately
leaves `peak_return_pct`, `peak_at`, `outcome_class` and `crossings` exactly as
the close-based episode construction produced them. Nothing downstream is
recomputed. So every capture rate, lead time, threshold crossing and class
assignment in the report remains **event-sampled even at 100% OHLC coverage**.

Letting one covered episode flip the whole report to "cross-validated" would
claim far more than the data supports, so there is deliberately no
cross-validated status constant to reach for —
`test_no_cross_validated_status_constant_exists` fails if one reappears.
Instead an `ohlc_validation` block reports:

- `status`: `NO_OHLC_VALIDATION` / `PARTIAL_OHLC_PEAK_COMPARISON` / `COMPLETE_OHLC_PEAK_COMPARISON`
- `provider`, `episodes_requested`, `episodes_with_candles`, `coverage_pct`
- `event_sampled_peak_vs_ohlc_peak_delta_pct`, and how often event sampling understated the peak
- `fully_validated_metrics` (empty), `partially_validated_metrics` (peak magnitude, compared not replaced), and `not_validated_metrics` (class, peak timing, crossings, capture rates, forward returns)

Both peaks are retained so undercounting stays visible rather than being
silently corrected, and so no metric can quietly start mixing close-based
episode construction with high-based labels.

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
incomplete forward window, OHLC coverage, `ohlc_validation` (coverage and what
it does and does not establish), `reconstruction_drift_proxy` (persistence-gap
drift), `episode_parameter_sensitivity` (the prior sweep), and an explicit
`warnings` list: absent or partial OHLC coverage, peak-comparison-only coverage,
high persistence-gap drift, parameter-sensitive episode counts, incomplete
forward windows, small validation sample, rejected rows, no episodes, nothing to
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
