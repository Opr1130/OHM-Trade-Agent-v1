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
actual scored values against the *real* `SignalQualityConfig` thresholds —
the same fields `determine_stage()` reads. This is a comparison table, not a
reimplementation of the stage cascade, so it cannot drift from what
production actually decided without a test catching it:
`test_gate_status_agrees_with_determine_stage` (property-style, 60 random
seeds) asserts the table's `eligible` bit always agrees with a direct call to
the real `determine_stage()`.

### 2.2 Forward outcomes (`compute_forward_outcome`)

For one reference point in time (a stage-first-reached timestamp) and its
price, computes:

- **Fixed-horizon returns** at 5m/15m/30m/60m/4h/8h/24h, via the new
  `SymbolTimeline.price_asof()` primitive (last observation at-or-before
  `t + H`).
- **MFE / MAE** (maximum favourable / adverse excursion) and **time-to-MFE /
  time-to-MAE**, via the new `SymbolTimeline.forward_extreme()` primitive — an
  independent, `mode="max"|"min"` sibling of Phase 2's existing
  `forward_maxima`, built with the identical strictly-exclusive `(t, t+H]`,
  O(n+m) monotonic-deque sweep. `forward_maxima` itself is untouched; nothing
  about Phase 2's already-validated no-lookahead behaviour changed.
- **`window_complete`**, reusing `SymbolTimeline.has_complete_window` so a
  horizon return computed from data that doesn't actually reach that far
  forward is flagged, not silently presented as a true measurement.

### 2.3 Stage timing decomposition (`build_stage_timing_records`)

One `StageTimingRecord` per Phase 2 episode (`MoveEpisode`), containing:

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
otherwise.

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
| `price` | float \| null | **known limitation — see §5.1** |
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

### 5.1 `price` is `None` in every telemetry record

`SignalQualityCandidate` (Phase 1, `signal_scoring.py`) carries no reference
price field, and neither does any other part of the existing Signal Quality
Telegram rendering path — this is a pre-existing gap, not one Phase 3A
introduced. `build_telemetry_record()` reads a `reference_price` attribute
opportunistically via `getattr`, so if that field is ever added to
`SignalQualityCandidate` in a future, separately-approved change, telemetry
picks it up automatically with no further call-site change. Fixing it now
would mean touching `signal_scoring.py`, which is deliberately untouched this
phase.

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

### 5.3 Stage timing is per-episode, not per-scan

`build_stage_timing_records` associates a symbol's detections with one Phase
2 `MoveEpisode` window (`[baseline_at, peak_at)`), consistent with Phase 2's
existing episode-dedup invariant (one distinct explosive run = one episode,
regardless of event density). A symbol with no matching episode in the replay
window (e.g. a move too small to open an episode, or truncated at the end of
the observation file) has no `StageTimingRecord`, even if it produced
detections. This mirrors Phase 2's own missed-winner-forensics scoping, not a
new limitation.

### 5.4 Counterfactual sweeps re-run the full replay per configuration point

Each point in a sweep re-executes `replay_signal_quality` end to end. This is
correct (no shortcuts that could silently diverge from the real pipeline) but
means a fine-grained sweep over a large observation file is O(sweep size ×
single replay cost). Acceptable for the sweep ranges Phase 3A ships with
({1,2,3}×{1,2,3} for persistence); a caller requesting a much larger sweep
should expect proportionally longer runtime. No caching or parallelism was
added — out of scope for a measurement-only phase.

---

## 6. Testing

- `tests/test_signal_timing_v2.py` (76 tests) — gate-status diagnosis
  including the DRV case itself, the `determine_stage()` cross-validation
  property test (60 seeds), forward-outcome horizons/MFE/MAE/no-lookahead,
  stage-timing decomposition, opportunity decay, and both counterfactual
  sweeps (including a "loosening never reduces detections" monotonicity
  check and a "sweeps never mutate the base config" check).
- `tests/test_decision_telemetry.py` (8 tests) — flag-off no-op, flag-on
  write-through, empty-candidate-list no-op, fail-soft on a forced write
  failure, the four safety-invariant fields, the `price` limitation and its
  opportunistic-read fallback.
- `tests/test_scan_movers_decision_telemetry_v1.py` (4 tests) — `full_market
  is None` no-op, **both flags at their real defaults write nothing**
  (the explicit "all-flags-off behaviour unchanged" proof), a forced
  exception inside `record_decision_telemetry` never escapes the call site,
  and an enabled write-through that also asserts `full_market` itself is
  never mutated by the call site.
- Full Phase 2 regression: `tests/test_signal_quality_phase2.py` — unchanged,
  passing (`CandidateRow`'s two new fields are additive/defaulted; no
  existing test needed modification).
- Full Phase 1 regression:
  `tests/test_signal_quality_explosion_detection_v1.py`,
  `tests/test_signal_quality_history_state_v1.py`,
  `tests/test_signal_quality_config_v1.py`, `tests/test_full_market_observation.py`,
  `tests/test_signal_quality_audit_v1.py` — unchanged, passing.
- Complete suite: `python -m pytest -q` — **1019 passed**, no failures, no
  new warnings.
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
   field, method signature, or control flow changed. The full Phase 2 test
   suite (which constructs `CandidateRow` via keyword arguments throughout)
   passes unmodified.
6. Nothing in this phase reads or writes `app/core/config.py`'s existing
   fields, nor any file under `app/services/signal_scoring.py`,
   `app/services/entry_exit_advisor.py`, `app/services/full_market_observation.py`,
   or the PendingSetup / confirmation lifecycle.

---

## 8. Recommendation for independent review

Suggested review order:

1. `app/core/config.py` diff (one field, one comment block).
2. `app/services/decision_telemetry.py` in full (new file, ~190 lines) —
   confirm the three safety properties (§2.8) hold by inspection, then check
   `tests/test_decision_telemetry.py` proves each one.
3. `app/jobs/scan_movers.py` diff (one import, one function, one call site) —
   confirm no existing line changed, then check
   `tests/test_scan_movers_decision_telemetry_v1.py`, especially
   `test_both_flags_off_by_default_writes_nothing`.
4. `app/services/signal_quality_phase2.py` diff — confirm both new
   `CandidateRow` fields are optional/defaulted and both new
   `SymbolTimeline` methods (`forward_extreme`, `price_asof`) are additive
   siblings of the existing `forward_maxima`/`index_before`, not
   replacements. Confirm `test_gate_status_agrees_with_determine_stage`
   (in `signal_timing_v2`'s test file, but exercising this module's
   `determine_stage`) actually imports and calls the real production
   function, not a copy.
5. `app/services/signal_timing_v2.py` in full (new file) — the gate-status
   diagnosis (§2.1) is the module's most safety-relevant piece, since it is
   what will be used to explain future DRV-style cases; review it alongside
   `determine_stage()` in `signal_scoring.py` line by line for threshold and
   comparison-operator parity.
6. §5 (known limitations) — confirm each is an honest scope boundary, not a
   masked defect.

Independent review should specifically check for: any path by which a
telemetry write could throw into the scoring/alerting path (traced through
`record_decision_telemetry`'s try/except and `_maybe_record_decision_telemetry`'s
own); any path by which a counterfactual sweep result could be mistaken for,
or accidentally merged into, the current-production report; and any drift
between `evaluate_stage_gates`'s thresholds and `determine_stage`'s.
