# Signal Quality v1 — Phase 3A: Timing Decomposition & Forward Decision Telemetry

Phase 3A is **measurement and research plus telemetry only**. It changes no
production Signal Quality threshold, no score weight, no Telegram signal
semantics, no liquidity threshold, no Kraken access, no execution behaviour,
and nothing in the PendingSetup / human-confirmation lifecycle. It adds one
optional, dark-by-default telemetry write to the live scan path and a new
offline replay module. Nothing here is merged into production configuration
by this phase; nothing is auto-tuned.

> **Status: `PROVISIONAL_EVENT_SAMPLED_REPLAY` / `PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION`.**
> Stage-timing and opportunity-decay figures describe the current-production
> configuration replayed offline. Counterfactual sweep figures never describe
> production — they are always returned in a separate block, and no sweep
> selects, recommends, or applies a configuration.

---

## 1. Why Phase 3A exists: the DRV correction

The case study that opened Phase 3 was a real DRV alert: **Stage: BREAKOUT
CANDIDATE, Persistence: 3**. The original assumption — that ACTIONABLE_REVIEW
was blocked by persistence — does not follow from that observation alone.
`determine_stage()` gates ACTIONABLE_REVIEW on five independent conditions:

```
opportunity   >= actionable_opportunity   (80)
explosion     >= actionable_explosion     (75)
tradeability  >= actionable_tradeability  (70)
persistence   >= actionable_min_persistence_scans (3)
exhaustion    <  actionable_max_exhaustion (20)
```

Persistence reading 3 says only that *one* of five gates was satisfied. Phase
3A's first deliverable is a way to name, from replay evidence, exactly which
gate(s) were still failing — never assumed, always measured.

## 2. Architecture

Two independent, additive pieces:

1. **`app/services/signal_timing_v2.py`** — a new offline analysis module,
   built entirely on top of Phase 2's existing, already-validated replay
   pipeline (`reconstruct_scan_frames`, `replay_signal_quality`,
   `build_all_episodes`, `build_timelines`, `evaluate_episode_detection`).
   No alternate scorer is reimplemented; no Phase 2 function's existing
   signature or behaviour changed.
2. **`app/services/decision_telemetry.py`** — a new, dark-by-default write
   path added to the *live* scan (`app/jobs/scan_movers.py`), so future
   analysis has actual point-in-time live decision state instead of relying
   solely on event-sampled JSONL reconstruction.

Both compose with the existing `SIGNAL_QUALITY_V1_ENABLED` flag; neither can
run, write, or change behaviour while it or its own flag is off.

### 2.1 Gate-status diagnosis (`evaluate_stage_gates`)

For one `CandidateRow` and one target stage (`BREAKOUT_CANDIDATE` or
`ACTIONABLE_REVIEW`), builds a per-gate pass/fail table by comparing the row's
actual scored values against the *real* `SignalQualityConfig` threshold
*fields* — the same ones `determine_stage()` reads, so the threshold
*values* cannot diverge. The per-gate comparison operators (`>=` for the
score gates, strict `<` for exhaustion) are mirrored rather than shared code,
so drift there is possible in principle if `determine_stage()`'s logic ever
changed without a matching update here. Two layers of tests catch that:
`test_gate_status_agrees_with_determine_stage` (60 randomized seeds) provides
randomized regression coverage — evidence across sampled cases, not a proof
of exact equivalence — and `test_actionable_gate_boundary_is_correct` /
`test_breakout_gate_boundary_is_correct` / `test_exhaustion_strict_less_than_boundary_matches_determine_stage`
/ `test_liquidity_observation_boundary_matches_determine_stage` add
deterministic exactly-at / epsilon-below / epsilon-above coverage for every
gate, specifically targeting the boundaries randomized sampling is least
likely to hit.

### 2.2 Forward outcomes (`compute_forward_outcome`)

For one reference point in time (a stage-first-reached timestamp) and its
price, computes:

- **Fixed-horizon returns** at 5m/15m/30m/60m/4h/8h/24h, via the
  `SymbolTimeline.price_asof()` primitive (last observation at-or-before
  `t + H`), gated per horizon by `horizon_observed` (see below).
- **MFE / MAE** (maximum favourable / adverse excursion) and **time-to-MFE /
  time-to-MAE**, via the `SymbolTimeline.forward_extreme()` primitive — an
  independent, `mode="max"|"min"` sibling of Phase 2's existing
  `forward_maxima`, built with the identical strictly-exclusive `(t, t+H]`,
  O(n+m) monotonic-deque sweep. `forward_maxima` itself is untouched; nothing
  about Phase 2's already-validated no-lookahead behaviour changed.
- **`window_complete`**, reusing `SymbolTimeline.has_complete_window` so a
  horizon return computed from data that doesn't actually reach that far
  forward is flagged, not silently presented as a true measurement.

**Horizon observability (`horizon_observed`).** Independent review flagged
that on the reconstructed replay grid, production scans every
`DEFAULT_SCAN_INTERVAL_SECONDS` (600s / 10 minutes) — so a naive "5m" query
commonly has *no observation at all* strictly between the reference and the
horizon. `price_asof` alone would silently return the reference price itself
in that case, which reads as "the market moved 0% in 5 minutes" when the true
answer is "OHM has no data this soon." This is fixed via a new primitive,
`SymbolTimeline.has_forward_observation(moment, horizon)` — an O(log n) pair
of binary searches over the same `(t, t+horizon]` boundary
`forward_maxima`/`forward_extreme` already use, answering only "does any
observation exist in this window", not "what is it". `compute_forward_outcome`
checks this **before** calling `price_asof` for every horizon: whenever it is
`False`, `horizon_returns_pct[label]` is forced to `None` rather than the
carried-forward value, and `ForwardOutcome.horizon_observed[label]` records
which case applied. Concretely: 5m is frequently `None`/unobserved on the
replay grid (expected, not a bug); 15m and coarser are ordinarily observed,
since at least one real scan falls inside a 15-minute window even at exactly
production cadence. `price_asof` itself is unchanged and is used nowhere in
Phase 2 (`git grep price_asof` confirms the only caller is this module), so
this fix cannot alter Phase 2 behaviour.

**MAE sign convention (`mae_pct` vs. `max_adverse_excursion_pct`).**
Independent review also flagged that `mae_pct` — `(min_future_price /
reference_price - 1) * 100` — can be *positive* if every future observation
stayed above the reference price, which a reader could misread against the
name "Maximum Adverse Excursion" (conventionally ≤ 0). Rather than silently
redefining the field, both readings are now reported: `mae_pct` keeps its
existing, signed, "minimum forward return actually realised" meaning
(documented explicitly in `ForwardOutcome`'s docstring), and a new field,
`max_adverse_excursion_pct = min(0.0, mae_pct)` (`None` only when `mae_pct`
is `None`), gives the conventional always-≤-0 reading. Neither one overwrites
or redefines the other.

### 2.3 Stage timing decomposition (`build_stage_timing_records`)

One `StageTimingRecord` per Phase 2 episode (`MoveEpisode`) — **not** one per
candidate or per scan. This is episode-conditioned scope, stated explicitly
because it changes what any aggregate over these records means: a candidate
that never became part of a qualifying episode (no real move followed it, or
one too small to open an episode) has no `StageTimingRecord` and is invisible
to every statistic built from this function's output. Treat figures derived
from it as "conditional on a real move having happened" — never as "all OHM
signals," and never as a false-positive or false-negative rate. That analysis
is still possible, but from a different, unfiltered source: Phase 2's audit
rows and Phase 3A's own `decision_telemetry` (§2.9) both retain every scored
candidate — suppressed included — regardless of what happened afterward.

Each record contains:

- `first_candidate_at` / `_stage` / `_price` — the episode's first detection
  in `[baseline_at, peak_at)`, reusing `evaluate_episode_detection`'s own
  definition and its existing `move_completed_fraction_pct` figure, rather
  than introducing a second, possibly-diverging definition of "first
  candidate."
- `first_early_building_at`, `first_breakout_candidate_at`,
  `first_actionable_review_at` — first time each tier was reached within the
  same episode window (`None` if never reached).
- `first_candidate_distance_from_24h_high_pct` — reuses the
  `distance_from_24h_high_pct` value already computed by Phase 1's feature
  derivation and now retained on `CandidateRow` (§2.5); no new derivation.
- `first_candidate_gate_status_breakout` / `_actionable` — the §2.1 diagnosis
  at the first-candidate row, naming exactly which gate(s) blocked immediate
  BREAKOUT/ACTIONABLE promotion.
- `outcome_from_first_candidate` / `_first_breakout_candidate` /
  `_first_actionable_review` — §2.2 forward outcomes computed from each
  stage's own first-reached price, so "what would have happened from an entry
  at EARLY_BUILDING vs. at ACTIONABLE_REVIEW" is directly comparable.

### 2.4 Opportunity decay (`opportunity_decay_by_persistence`)

For each episode that reached a later confirmation tier, reports how much
price appreciation happened *between* first-candidate and that tier
(`{tier}_gained_before_confirmation_pct`) and how long that took
(`{tier}_seconds_since_first_candidate`). Purely descriptive — it reports what
waiting for confirmation cost in already-elapsed move; it does not recommend
loosening or tightening any gate.

### 2.5 `CandidateRow` additions (additive, backward-compatible)

Two new optional, defaulted fields on the existing `CandidateRow` dataclass in
`signal_quality_phase2.py`:

```python
distance_from_24h_high_pct: float | None = None
lift_from_24h_low_pct: float | None = None
```

Populated in `replay_signal_quality()` from the same `SymbolFeatures` already
computed for that scan — values that already existed and were previously
discarded after scoring, not a new feature derivation. Every existing
keyword-constructed `CandidateRow` (all of Phase 2's own tests included)
stays valid unchanged, since both fields are optional and default to `None`.

### 2.6 Counterfactual sweeps

Both sweeps vary only `SignalQualityConfig` / `Phase2Config` fields via
`dataclasses.replace` — production `Settings` objects are never read or
mutated by either — and both re-run the exact same `replay_signal_quality` /
`build_stage_timing_records` pipeline used for the current-production replay,
so no alternate code path can silently diverge.

- **`run_persistence_counterfactual`** — sweeps
  `breakout_min_persistence_scans` × `actionable_min_persistence_scans` over
  `{1, 2, 3}` (configurable), skipping any combination where the actionable
  value would be *less* strict than the breakout value — that ordering is a
  structural property of `determine_stage()`'s cascade (actionable is checked
  first and is everywhere-at-least-as-strict), not a choice made by the
  sweep.
- **`run_threshold_ablation`** — re-runs the replay under an arbitrary list of
  `SignalQualityConfig` field overrides (e.g.
  `{"breakout_opportunity": 65.0}`), one full replay per override. An unknown
  field name raises `TypeError` from `dataclasses.replace` rather than
  silently doing nothing.

Both return `{"status": "PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION", ...}` in
a block that is never merged into the current-production report returned by
`run_phase3a_timing_replay` — the two are always separate top-level keys.

### 2.7 Top-level orchestrator (`run_phase3a_timing_replay`)

Mirrors Phase 2's `run_phase2_replay`: reads one observation file, writes
nothing, returns a single JSON-able report with `stage_timing_records`,
`opportunity_decay`, and (unless `run_persistence_sweep=False`)
`persistence_counterfactual` as a clearly separate key. Exposed as a CLI via
`app/jobs/report_signal_quality_phase3a_timing.py`, matching
`report_signal_quality_phase2.py`'s existing pattern (read-only, prints JSON,
`--observations` / `--horizon-hours` / `--no-persistence-sweep` flags).

### 2.8 Forward decision telemetry (`decision_telemetry.py`)

Phase 2's replay is an *approximation*: it carries values forward across
event-sampling gaps and cannot see anything the live process didn't persist.
`record_decision_telemetry()` removes that approximation going forward by
appending one JSONL row per scored candidate at the moment the live scan
actually produced it — ground truth for future analysis instead of only
reconstruction.

Three properties make this safe to ship dark and turn on later:

- **Dark by default.** `DECISION_TELEMETRY_V1_ENABLED` defaults to `False`.
  Composed with `SIGNAL_QUALITY_V1_ENABLED` also defaulting to `False` (which
  already makes `full_market.signal_quality_candidates` empty), both flags
  default off and either one being off writes nothing.
- **Fail-soft.** Every failure mode is caught inside
  `record_decision_telemetry()` itself, which has no return value a caller
  could act on and no side effect besides the append. `scan_movers.py`'s call
  site (`_maybe_record_decision_telemetry`) wraps the call in its own
  try/except anyway, as defence in depth against a defect in the call site
  itself, not just inside the telemetry module.
- **One-directional.** Nothing in the module reads the telemetry file back;
  the only public write entry point takes already-computed candidates and
  returns a count. Telemetry can never feed back into a decision.

**Call site** (`app/jobs/scan_movers.py`): one new import, one new ~15-line
wrapper function, and one new call in `main()` immediately after
`full_market` is obtained:

```python
_maybe_record_decision_telemetry(full_market, settings)
```

No other line in `scan_movers.py` changed. The wrapper is a no-op whenever
`full_market is None`, and delegates entirely to
`record_decision_telemetry()`'s own dark-by-default and fail-soft guarantees
otherwise. It also passes `full_market.signal_quality_reference_prices`
through to `record_decision_telemetry` (§2.9) — the one other argument this
call site carries.

### 2.9 Same-scan reference price (`FullMarketResult.signal_quality_reference_prices`)

`SignalQualityCandidate` (Phase 1) carries no price field, and adding one
there was explicitly ruled out: it would be a scoring-semantics change for a
measurement-only phase. Two other shortcuts were ruled out too — a second
Kraken/ticker request after scoring (an extra market-data fetch this phase
must not add), and reading a later ticker print (which would silently turn
"the price OHM actually saw" into "the price some time after"). Instead, the
exact same-scan price already sitting in memory is exposed read-only:

- `process_full_market_observations()` builds `history: dict[str, list[ObservationSnapshot]]`
  once per scan. For every observed symbol, `_append_history(...)` appends
  that scan's `ObservationSnapshot` (`last_price=observation.last_price`) as
  `history[symbol][-1]` **before** `evaluate_signal_quality(history, ...)` is
  called with that same dict.
- `evaluate_signal_quality` feeds that same `history` into
  `derive_features_for_universe`, which is what actually derives the features
  each returned `SignalQualityCandidate` is scored from. So
  `history[candidate.symbol][-1].last_price` is not merely *a* price for that
  symbol — it is the exact snapshot the candidate's own score came from.
- Immediately after `candidates = evaluate_signal_quality(...)` succeeds, one
  more read-only pass (its own independent fail-soft `try`/`except`, so a
  defect here can never take the already-scored `candidates` down with it)
  builds:
  ```python
  reference_prices = {
      candidate.symbol: history[candidate.symbol][-1].last_price
      for candidate in candidates
      if history.get(candidate.symbol)
  }
  ```
- `FullMarketResult` gains one new additive, defaulted field:
  `signal_quality_reference_prices: Mapping[str, float] = field(default_factory=dict)`
  — empty whenever `signal_quality_v1_enabled` is `False`, and containing an
  entry only for symbols that actually produced a candidate this scan (not
  "every observed symbol's price").
- `decision_telemetry.build_telemetry_record()` reads this mapping by
  `candidate.symbol` as `price`'s primary source, falling back to the
  existing opportunistic `getattr(candidate, "reference_price", None)` read
  only if the mapping has no entry — so a future `SignalQualityCandidate`
  that does gain its own price field is still picked up with no call-site
  change.

No line of `evaluate_signal_quality`, `derive_features_for_universe`,
`evaluate_universe`, or anything in `signal_scoring.py` changed. Stage
determination, alert behaviour, and execution are all untouched — this is a
read of data the scan already held, exposed one field further out.

---

## 3. Telemetry schema (`DecisionTelemetryRecord`, `schema_version = 1`)

One JSONL row per scored candidate per live scan, written to
`/app/data/decision_telemetry.jsonl` by default (path is caller-overridable,
used only by tests):

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1` |
| `recorded_at` | ISO-8601 UTC | telemetry write time |
| `scan_source` | str | `"LIVE"` (reserved for future non-live sources) |
| `symbol` | str | upper-cased |
| `price` | float \| null | same-scan price from `signal_quality_reference_prices` (§2.9); `null` only if that mapping has no entry for this symbol |
| `liquidity_24h_usd_approx` | float | |
| `stage` | str | |
| `pattern` | str \| null | |
| `opportunity_score`, `explosion_potential_score`, `tradeability_score`, `pattern_strength_score`, `volume_acceleration_score`, `relative_strength_score` | int | |
| `persistence_scans` | int | |
| `exhaustion_penalty` | int | |
| `exhaustion_band` | str | |
| `relative_strength_percentile` | float | |
| `universe_size` | int | |
| `reasons` | list[str] | |
| `suppressed` | bool | |
| `signal_quality_enabled`, `early_alerts_enabled` | bool | flag state at write time |
| `advisory_only` | bool | always `True` |
| `weights_are_calibrated` | bool | always `False` |
| `trade_authority_changed` | bool | always `False` |
| `production_execution_gate_changed` | bool | always `False` |

The four safety-invariant fields are **asserted**, not merely documented —
they are fixed dataclass defaults, not derived from any input, so no future
change to `build_telemetry_record()` can make them read anything but their
locked value without a code change to the dataclass itself.

---

## 4. Experiment matrix

| Experiment | Function | Status tag | Notes |
|---|---|---|---|
| Gate-status diagnosis | `evaluate_stage_gates` | n/a (diagnostic primitive) | Cross-validated against real `determine_stage()` |
| Per-episode stage timing | `build_stage_timing_records` | `PROVISIONAL_EVENT_SAMPLED_REPLAY` | Current-production configuration |
| Forward outcomes (5m…24h, MFE, MAE) | `compute_forward_outcome` | n/a (primitive) | No-lookahead, strictly `(t, t+H]` |
| Opportunity decay across confirmation | `opportunity_decay_by_persistence` | `PROVISIONAL_EVENT_SAMPLED_REPLAY` | Descriptive only |
| Persistence counterfactual (1/2/3 × 1/2/3) | `run_persistence_counterfactual` | `PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION` | Reported in a separate block; no winner selected |
| Threshold/weight ablation | `run_threshold_ablation` | `PROVISIONAL_COUNTERFACTUAL_NOT_PRODUCTION` | Arbitrary `SignalQualityConfig` field overrides |
| Forward decision telemetry | `record_decision_telemetry` | n/a (live, not replay) | Dark by default; §2.8 |

---

## 5. Known limitations

### 5.1 (Resolved) `price` is populated from the same-scan observation, not from `SignalQualityCandidate`

An earlier draft of this phase shipped `price = None` in every telemetry
record, reasoning that `SignalQualityCandidate` carries no price field and
that adding one there was out of scope. That was corrected before this
branch's implementation was finalized: `price` is not read off the
candidate at all. It is read from `FullMarketResult.signal_quality_reference_prices`
(§2.9) — the exact same-scan `ObservationSnapshot.last_price` that
`derive_features_for_universe` used to derive that candidate's own score,
already sitting in `process_full_market_observations()`'s in-memory
`history` dict. No field was added to `SignalQualityCandidate`, no second
Kraken/ticker request was made, and no later print is ever substituted.
`price` is only `None` if `signal_quality_reference_prices` has no entry for
that symbol — which does not happen for any candidate produced by a live
scan, since the mapping is built directly from the same candidates list.

### 5.2 Two originally-proposed ablations are not reachable

The original Phase 3A proposal listed "acceleration-trigger sensitivity" and
"volume-corroboration-cap relaxation" as ablation candidates. Both are
hardcoded module-level constants inside `signal_scoring.py`
(`MOVEMENT_RATE_ANCHORS_PRIOR` and the volume corroboration cap logic), not
`SignalQualityConfig` fields — `run_threshold_ablation` can only vary actual
config fields via `dataclasses.replace`. Reaching these two would require
either touching `signal_scoring.py` (out of scope) or forking a duplicate
scorer (rejected as a drift risk). Documented here rather than silently
dropped or worked around.

### 5.3 Stage timing is episode-conditioned, not signal-conditioned

`build_stage_timing_records` associates a symbol's detections with one Phase
2 `MoveEpisode` window (`[baseline_at, peak_at)`), consistent with Phase 2's
existing episode-dedup invariant (one distinct explosive run = one episode,
regardless of event density). A symbol with no matching episode in the replay
window (e.g. a move too small to open an episode, or truncated at the end of
the observation file) has no `StageTimingRecord`, even if it produced
detections. This mirrors Phase 2's own missed-winner-forensics scoping, not a
new limitation.

Independent review asked this be stated even more plainly, since it changes
what a `StageTimingRecord` population statistic means: **do not describe any
figure derived from `stage_timing_records` as representing "all OHM
signals."** It represents signals conditioned on a real move having
happened. False-positive analysis (candidates that never led anywhere,
suppressed rows, near-misses) remains fully possible — from Phase 2's audit
rows and from Phase 3A's own `decision_telemetry` (§2.9), both of which
retain every scored candidate regardless of episode outcome — just not from
this function's output. See §2.3 for the same note where the function is
introduced.

### 5.4 Counterfactual sweeps re-run the full replay per configuration point

Each point in a sweep re-executes `replay_signal_quality` end to end. This is
correct (no shortcuts that could silently diverge from the real pipeline) but
means a fine-grained sweep over a large observation file is O(sweep size ×
single replay cost). Acceptable for the sweep ranges Phase 3A ships with
({1,2,3}×{1,2,3} for persistence); a caller requesting a much larger sweep
should expect proportionally longer runtime. No caching or parallelism was
added — out of scope for a measurement-only phase.

### 5.5 Telemetry retention: not implemented this phase, growth documented here

`decision_telemetry.jsonl` (§2.8) is a plain append-only file with no
rotation, no size cap, and no expiry logic. That is a deliberate Phase 3A
choice — per the approved scope, this phase adds the write path and nothing
more — but it means the file grows without bound for as long as
`DECISION_TELEMETRY_V1_ENABLED` stays on, and that must be sized before any
long-term production enablement.

**Growth math.** `evaluate_universe` (§2.9's data source) scores and returns
**every observed market** — suppressed rows included, by design (§7's
suppressed-candidate preservation requirement) — so one telemetry line is
written per universe symbol per scan, not per alert. At the production scan
cadence (`DEFAULT_SCAN_INTERVAL_SECONDS` = 600s ⇒ 144 scans/day) and a
measured ~850 bytes/record (a realistic row with several reason strings and
a `LONGUSD`-length symbol; shorter symbols or fewer reasons run smaller):

| Universe size | Rows/day | Approx. size/day | Approx. size/30 days |
|---:|---:|---:|---:|
| 100 | 14,400 | ~12 MB | ~360 MB |
| 300 | 43,200 | ~37 MB | ~1.1 GB |
| 500 | 72,000 | ~61 MB | ~1.8 GB |

The exact Kraken USD-market count is not pinned down here — these rows are
illustrative order-of-magnitude figures from the measured per-record size and
production cadence, not a claim about the live universe size. The
takeaway that does not depend on the exact count: this is multi-hundred-MB
to multi-GB per month at any realistic universe size, entirely because full,
unfiltered universe capture is the point of the design (§7) — narrowing it
to alert-worthy rows would defeat the reason this file exists.

**Proposed (not implemented) bounded design**, for a separate, explicitly-
approved follow-up before any long-term production enablement:

1. **Daily rotation** — write to `decision_telemetry-YYYY-MM-DD.jsonl`
   instead of one ever-growing file, keyed off `recorded_at`'s date. Old
   files become independently prunable/archivable without touching the
   active file or its lock; no change to the per-record schema.
2. **A retention window** (e.g. 30/60/90 days, operator-configured) enforced
   by a separate, explicitly fail-soft prune step — a cron-style job or a
   check at scan start — that deletes rotated files older than the window.
   Never a truncate-in-place on the active file: JSONL has no efficient
   in-place head-truncation, and truncating a file mid-append is a data-loss
   risk this design should avoid entirely.
3. **Optional compression** of rotated-out files (e.g. gzip) if the
   retention window is long enough that raw size becomes a concern.
4. Rotation and pruning stay outside the write path's fail-soft boundary in
   the same way persistence and scoring already are: a pruning failure must
   never block or slow down a live scan, and must never be allowed to touch
   the file currently being appended to.

None of this is implemented in Phase 3A. It is documented here so growth is
visible before telemetry is ever left on in production for an extended
period, and so a follow-up implementing it has a starting design already
reviewed.

### 5.6 Reviewer-proposed production validation numbers are not adopted policy

Independent review proposed candidate production-readiness criteria for a
possible future promotion decision — e.g. requiring N ≥ 100 independent
episodes, an MAE worse than -8%, and a degradation ceiling around 1.1x. These
are recorded here **only for traceability to that review**, and explicitly
are **not** adopted OHM policy, not a Phase 3A output, and not implemented
anywhere in this branch. Phase 3A remains measurement-only per the approved
scope (§8 of the original architecture approval): it reports numbers, it does
not gate, threshold, or promote anything against them. Any future decision to
adopt specific validation criteria is a separate, explicit decision for a
later phase, not something this phase or this document sets.

---

## 6. Testing

- `tests/test_signal_timing_v2.py` (94 tests) — gate-status diagnosis
  including the DRV case itself; the `determine_stage()` randomized
  regression test (60 seeds); deterministic exactly-at/epsilon-below/
  epsilon-above boundary tests for every ACTIONABLE_REVIEW and
  BREAKOUT_CANDIDATE gate (`test_actionable_gate_boundary_is_correct`,
  `test_breakout_gate_boundary_is_correct`), plus two tests specifically
  cross-checking the exhaustion strict-`<` boundary and the liquidity
  observation-only boundary against the real `determine_stage`; forward-
  outcome horizons/MFE/MAE/no-lookahead; the new horizon-observability tests
  (`test_five_minute_horizon_is_unobserved_on_a_ten_minute_scan_grid`,
  `test_coarser_horizon_is_observed_on_the_same_ten_minute_grid`); the new
  `max_adverse_excursion_pct` capping tests; stage-timing decomposition;
  opportunity decay; and both counterfactual sweeps (including a "loosening
  never reduces detections" monotonicity check and a "sweeps never mutate
  the base config" check).
- `tests/test_decision_telemetry.py` (12 tests) — flag-off no-op, flag-on
  write-through with the price read from `reference_prices`, suppressed
  candidates still recorded (§7), empty-candidate-list no-op, fail-soft on a
  forced write failure, the four safety-invariant fields, price sourced from
  the `reference_prices` mapping (keyed by upper-cased symbol), the
  opportunistic-attribute fallback when the mapping has no entry, and the
  mapping taking priority over that fallback when both are present.
- `tests/test_scan_movers_decision_telemetry_v1.py` (5 tests) — `full_market
  is None` no-op, **both flags at their real defaults write nothing**
  (the explicit "all-flags-off behaviour unchanged" proof), a forced
  exception inside `record_decision_telemetry` never escapes the call site,
  an enabled write-through that also asserts `full_market` itself is never
  mutated by the call site, and the same-scan reference price flowing end to
  end from `FullMarketResult` into the written JSONL record.
- `tests/test_full_market_observation.py` (10 tests, 5 new for Phase 3A) —
  `signal_quality_reference_prices` matches the exact same-scan price
  `evaluate_signal_quality` was called with; empty when Signal Quality is
  disabled; empty (and candidates still `()`) when scoring itself fails,
  fail-soft; a failure while *building the price mapping* is independently
  fail-soft and never discards the already-computed candidates; and the
  mapping contains only symbols that actually produced a candidate, not
  every observed symbol.
- Full Phase 2 regression: `tests/test_signal_quality_phase2.py` (98 tests)
  — passing. `signal_quality_phase2.py` gained one more additive method this
  round (`SymbolTimeline.has_forward_observation`, §2.2); `price_asof` and
  `forward_extreme` are otherwise unchanged, and neither is called anywhere
  in Phase 2 itself (only from `signal_timing_v2.py`), so this fix cannot
  alter Phase 2 behaviour — confirmed by the full Phase 2 suite passing
  unmodified.
- Full Phase 1 regression:
  `tests/test_signal_quality_explosion_detection_v1.py`,
  `tests/test_signal_quality_history_state_v1.py`,
  `tests/test_signal_quality_config_v1.py`, `tests/test_signal_quality_audit_v1.py`
  — unchanged, passing.
- Complete suite: `python -m pytest -q` — **1047 passed**, no failures, no
  new warnings (6 pre-existing FastAPI/Starlette deprecation warnings,
  unrelated to this phase).
- `python -m compileall -q app tests` — clean.

---

## 7. Proof that all-flags-off production behaviour is unchanged

1. `Settings()` defaults: `signal_quality_v1_enabled = False`,
   `decision_telemetry_v1_enabled = False` (new field, added immediately
   after `signal_quality_early_alerts_enabled` in `app/core/config.py`,
   additive only).
2. `test_both_flags_off_by_default_writes_nothing`
   (`tests/test_scan_movers_decision_telemetry_v1.py`) constructs
   `Settings()` with no overrides, asserts both flags read `False`, calls the
   real `_maybe_record_decision_telemetry` against a `FullMarketResult` that
   *does* carry a candidate, and asserts the telemetry file is never created.
3. `test_flag_off_writes_nothing`
   (`tests/test_decision_telemetry.py`) asserts the same at the
   `record_decision_telemetry()` layer directly.
4. `scan_movers.py`'s only other change is the one `_maybe_record_decision_telemetry`
   call, placed after `full_market` is already computed and before any
   existing line — it reads `full_market` and `settings`, returns nothing,
   and cannot alter either object (`FullMarketResult` is a frozen dataclass).
   `test_enabled_flags_write_through_to_disk` additionally asserts
   `full_market == replace(full_market)` after the call, i.e. genuinely
   unchanged.
5. `signal_quality_phase2.py`'s two new `CandidateRow` fields are optional
   and default to `None`; `replay_signal_quality()`'s only change is passing
   two additional keyword arguments when constructing each row — no existing
   field, method signature, or control flow changed. The one further change
   to this file, `SymbolTimeline.has_forward_observation` (§2.2), is a new
   method alongside the existing ones — it reads `_epochs` the same way
   `price_asof`/`index_before` already do and calls nothing else. The full
   Phase 2 test suite (which constructs `CandidateRow` via keyword arguments
   throughout, and never calls `price_asof`, `forward_extreme`, or
   `has_forward_observation` — confirmed by `git grep`) passes unmodified.
6. `full_market_observation.py` *is* touched this phase (§2.9), but only
   additively: one new, defaulted `FullMarketResult` field
   (`signal_quality_reference_prices: Mapping[str, float] = field(default_factory=dict)`)
   and one new read-only block that runs only inside the existing
   `if signal_quality_enabled:` branch, in its own fail-soft `try`/`except`
   that cannot affect `candidates`. No existing field, function signature, or
   control-flow branch changed; `evaluate_signal_quality`,
   `derive_features_for_universe`, and `evaluate_universe` are called exactly
   as before with exactly the same arguments.
   `test_reference_prices_empty_when_signal_quality_disabled` asserts the new
   field is `{}` whenever `signal_quality_v1_enabled` is `False` - i.e. the
   same condition that already made `signal_quality_candidates` empty.
7. Nothing in this phase reads or writes `app/core/config.py`'s existing
   fields, nor any file under `app/services/signal_scoring.py` or
   `app/services/entry_exit_advisor.py`, nor the PendingSetup / confirmation
   lifecycle.

---

## 8. Recommendation for independent review

Suggested review order:

1. `app/core/config.py` diff (one field, one comment block).
2. `app/services/decision_telemetry.py` in full (new file, ~205 lines) —
   confirm the three safety properties (§2.8) hold by inspection, then check
   `tests/test_decision_telemetry.py` proves each one.
3. `app/jobs/scan_movers.py` diff (one import, one function, one call site) —
   confirm no existing line changed, then check
   `tests/test_scan_movers_decision_telemetry_v1.py`, especially
   `test_both_flags_off_by_default_writes_nothing` and
   `test_reference_prices_flow_from_full_market_to_the_written_record`.
4. `app/services/full_market_observation.py` diff (§2.9) — confirm the new
   `signal_quality_reference_prices` field is additive/defaulted, that its
   population sits inside the existing `if signal_quality_enabled:` branch in
   its own fail-soft `try`/`except`, and that `history[candidate.symbol][-1]`
   really is the same snapshot `evaluate_signal_quality` was called with (not
   a re-fetch or a value read after scoring). Check
   `tests/test_full_market_observation.py`'s five new cases, especially
   `test_reference_prices_match_the_same_scan_price_used_for_scoring` and
   `test_reference_price_lookup_failure_is_fail_soft_and_never_touches_candidates`.
5. `app/services/signal_quality_phase2.py` diff — confirm both new
   `CandidateRow` fields are optional/defaulted and all three new
   `SymbolTimeline` methods (`forward_extreme`, `price_asof`,
   `has_forward_observation`) are additive siblings of the existing
   `forward_maxima`/`index_before`, not replacements; confirm (e.g. via
   `git grep`) that Phase 2 itself never calls any of the three. Confirm
   `test_gate_status_agrees_with_determine_stage` and the deterministic
   `test_*_gate_boundary_*` / `test_*_boundary_matches_determine_stage`
   tests (in `signal_timing_v2`'s test file, but exercising this module's
   `determine_stage`) actually import and call the real production function,
   not a copy.
6. `app/services/signal_timing_v2.py` in full (new file) — the gate-status
   diagnosis (§2.1) is the module's most safety-relevant piece, since it is
   what will be used to explain future DRV-style cases; review it alongside
   `determine_stage()` in `signal_scoring.py` line by line for threshold and
   comparison-operator parity, paying particular attention to the exhaustion
   strict-`<` and liquidity boundaries. Separately confirm `compute_forward_outcome`
   gates every horizon through `has_forward_observation` before trusting
   `price_asof` (§2.2), and that `max_adverse_excursion_pct` is genuinely
   `min(0.0, mae_pct)` rather than a redefinition of `mae_pct` itself.
7. §5 (known limitations) — confirm each remaining one is an honest scope
   boundary, not a masked defect; that §5.1's resolution actually holds (no
   `signal_scoring.py` change, no second market-data fetch, no later price
   substituted); that §5.3's episode-conditioned-scope framing matches what
   `build_stage_timing_records` actually does; that §5.5's growth figures are
   presented as illustrative rather than as a claim about the live Kraken
   universe size, and that no rotation/retention logic was actually added;
   and that §5.6's reviewer-proposed numbers (N ≥ 100 episodes, MAE < -8%,
   1.1x degradation ceiling) appear nowhere else in this branch as adopted
   policy or as an implemented gate.

Independent review should specifically check for: any path by which a
telemetry write could throw into the scoring/alerting path (traced through
`record_decision_telemetry`'s try/except and `_maybe_record_decision_telemetry`'s
own); any path by which building `signal_quality_reference_prices` could
throw into or delay the `candidates` result it's computed alongside; any path
by which a counterfactual sweep result could be mistaken for, or accidentally
merged into, the current-production report; any drift between
`evaluate_stage_gates`'s thresholds and `determine_stage`'s; any horizon
label whose `horizon_returns_pct` value is populated despite
`horizon_observed` reading `False` for it (the two must never disagree); and
any place in this document or the code that states or implies
`StageTimingRecord`-derived figures represent the full universe of OHM
signals rather than the episode-conditioned subset (§2.3/§5.3).
