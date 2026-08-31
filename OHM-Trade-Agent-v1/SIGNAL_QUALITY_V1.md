# Signal Quality / Explosion Detection v1 — Phase 1

**Status: shipped dark.** `SIGNAL_QUALITY_V1_ENABLED` defaults to `false`. With
the flag off, the Broad Watch path behaves exactly as it did before this
change — including on disk. Dark mode is a hard branch, not a filter applied at
the end: while disabled, Phase 1 performs no schema migration, writes no
`history_by_symbol`, writes no `schema_version`, adds nothing to the state file,
derives no features and scores nothing. See §3.

**Everything numeric in this document is an interpretable prior, not a
calibrated value.** Nothing here has been fitted to outcome data. Phase 1 had no
access to a scored production observation history, so no detection rates,
capture rates, or before/after comparisons appear in this document or anywhere
in the code. Phase 2 answers empirically whether these priors actually
distinguish explosive movers from failed pumps.

Everything in Phase 1 is advisory. It places no orders, confirms no entries,
changes no execution gate, and touches no position-verification surface.

---

## 1. What problem this solves

The Broad Watch feed was alerting on markets that were untradeable in practice —
a ~$10.6k/24h market and a ~$4.0k/24h market among them. Two defects combined:

1. **Liquidity was a scoring term, not a gate.** In the legacy
   `_transition()` scorer, thin liquidity subtracted a few points from a single
   blended score. A strong enough chart pattern simply outvoted it.
2. **The notification boundary sliced an unfiltered list.** `scan_movers.py`
   sent `broad_candidates[:4]`, so a handful of thin `WATCH_ONLY` rows could
   occupy every slot in the feed and starve the tier it exists to surface.

Phase 1 separates the questions that the single blended score had conflated, and
puts a hard gate in front of all of them.

## 2. Pipeline

```
collect observations
  → derive per-symbol temporal features from runtime history
  → derive cross-universe relative-strength percentiles
  → apply hard tradeability gate
  → calculate independent scores
  → apply exhaustion/chase penalty
  → determine stage
  → rank
  → notification gate
```

| Module | Responsibility |
| --- | --- |
| `app/services/signal_features.py` | Temporal + cross-universe feature derivation. Pure functions: no network, no filesystem, no clock reads. |
| `app/services/signal_scoring.py` | Scores, exhaustion, stage machine, leaderboard, feed filter. Pure. |
| `app/services/full_market_observation.py` | Runtime scan-history state, and the pipeline entry point. |
| `app/jobs/scan_movers.py` | Notification boundary and card rendering. |

Two boundaries are deliberate and load-bearing:

* **Coin identity never reaches a scorer.** Symbols are opaque dictionary keys
  used for ranking and rendering only. No scoring function takes a symbol.
* **Learning persistence is not feature persistence.** See §3.

## 3. State migration (schema 1 → 2)

`full_market_observation_state.json` gains `history_by_symbol` and
`schema_version: 2`. The migration is **non-destructive**: `latest_by_symbol`
keeps its exact schema-1 semantics, so the legacy transition detector and its
consumers are unaffected, and an existing schema-1 file seeds a one-element
history rather than being discarded.

**All of it is gated on `SIGNAL_QUALITY_V1_ENABLED`.** A disabled deployment
never migrates, never writes the schema-2 keys, and leaves the state file
shaped exactly as schema 1 — so enabling the feature is the only event that
changes persisted state. Migration on first enablement is idempotent: re-running
the same timestamped scan replaces its snapshot rather than duplicating it.

Disabling *after* an enabled run is **not** a destructive rollback. Existing
`history_by_symbol` and `schema_version` are retained verbatim and simply stop
being updated or read; re-enabling resumes from the retained history.

The critical distinction:

> `full_market_observations.jsonl` is **event-sampled**. `_should_persist()`
> deliberately drops quiet scans. Counting persistence in persisted rows would
> therefore count *events*, not *time*.

So `history_by_symbol` advances on **every runtime scan**, before and
independently of the `_should_persist()` decision. A quiet scan that never
reaches the JSONL stream still occupies its place in the temporal series.

History is a bounded ring buffer (`SIGNAL_QUALITY_HISTORY_SCANS`, default 8),
and an out-of-order or replayed scan replaces the newest row rather than
fabricating an interval.

### Retention vs. continuity

`collect_full_market_observations` fail-softs on a ticker batch exception, so a
transient Kraken error, a malformed response or a network gap are all
indistinguishable from a symbol disappearing. Pruning history on *presence*
would let any of them silently erase a symbol's feature state, reset its
persistence, and make its return look like a first observation.

History is therefore pruned on **age**, not presence
(`SIGNAL_QUALITY_STALE_HISTORY_RETENTION_SECONDS`, default 3600 — six scans at
the default cadence). The two windows answer different questions:

| | Bounds | Default | Effect when exceeded |
| --- | --- | --- | --- |
| Continuity window | how long a **persistence chain** survives a gap | 1500s (interval × 2.5) | chain resets to 0; no pattern is classified |
| Retention window | how long **evidence** is kept without observation | 3600s | the symbol's history is deleted |

Retention is validated at boot to be longer than continuity — pruning evidence
before continuity can even break would delete exactly what a returning symbol
needs. So a symbol that vanishes for a scan or two keeps its snapshots and
loses its credit: it returns with its history intact, `continuity_intact`
False, and a persistence chain of 0.

Symbols retained but absent from the current scan are **not scored**. They keep
their history for when they return, but stale snapshots must never be ranked as
though they were current observations. Genuinely delisted markets age out once
the retention window passes, so state stays bounded in both depth (ring buffer)
and breadth (retention age).

`full_market_transition_learning.py` keeps its own separate state file and is
untouched. Learning state and live feature state stay conceptually separate.

## 4. Features

Per-scan rates are **normalised to the nominal scan cadence**, so a long gap
between scans cannot masquerade as a burst of momentum. Second-order terms
(acceleration) are treated as zero when there is no third scan — an unknown is
not evidence of acceleration.

### Rolling volume growth proxy — a stated limitation

Kraken's `volume_24h` is a **rolling 24-hour aggregate**. The change between two
snapshots is *not* interval volume. The feature is therefore named
`rolling_notional_growth_rate_pct` and carries
`ROLLING_VOLUME_GROWTH_PROXY_NOTE` in code. It must not be described as
5-minute or 15-minute volume acceleration. Phase 2 must replace it with true
interval volume before any volume-confirmation claim is treated as calibrated.

To honour "do not award a high score from a single anomalous snapshot", the
volume score is capped by corroboration: reaching the upper bands requires
several consecutive rising intervals *and* growth against the multi-scan median.

## 5. Scores

All bounded 0–100.

**`tradeability_score`** — 24h USD notional only, interpolated in log space
(liquidity is multiplicative). Phase 1 has no trustworthy spread, depth or
slippage feed on this path and does not pretend otherwise; the signature is the
Phase 2 extension point.

| Notional | 100k | 250k | 500k | 1M | 2.5M | 5M | 10M+ |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Score | 20 | 40 | 55 | 70 | 82 | 90 | 100 |

Below `SIGNAL_QUALITY_MIN_LIQUIDITY_USD` the score is 0. **Gating is
independent of this score** — no pattern score can overturn it.

**`pattern_strength_score`** — 35% price acceleration + 30% structural
expansion + 20% position near the 24h high + 15% pattern-specific bonus.
**Contains no liquidity term.** It answers "is the chart moving?", never "is
this worth our attention?". The three structural patterns
(`COMPRESSION_RELEASE`, `REACCELERATION`, `PROGRESSIVE_EXPANSION`) are inherited
unchanged from the legacy detector; what changed is the consequence.

**Classification requires intact continuity.** The inherited boundaries are raw
interval deltas, so across an outage a move that merely *accumulated* while OHM
was not looking would satisfy them exactly as a live acceleration does. A
pattern is a claim that OHM observed a transition happen, so
`classify_pattern` returns `None` when `continuity_intact` is False. The market
keeps its leaderboard row for audit and forfeits the pattern-quality credit.

**`volume_acceleration_score`** — from the rolling growth proxy above. Bands:
flat/falling 0–20, modest 20–45, clearly accelerating 45–70, extreme *and
consistent* 70–100.

**`persistence_score`** — consecutive qualifying runtime scans:
1→20, 2→40, 3→60, 4→75, 5→88, 6+→100. A scan qualifies only when it shows
minimum directional structure (advancing, lifted off the low, near the high).
The chain breaks at the first non-qualifying scan *and* at any continuity gap,
so a one-scan spike surrounded by flat scans earns exactly 20, not 100.

**`relative_strength_score`** — 60% price-change percentile + 40%
structural-acceleration percentile, computed against **every market with
derivable features**, not only transition candidates. Ranking inside an
already-filtered set makes the percentile selection-biased by construction.

**`explosion_potential_score`** — 30% price acceleration + 25% volume proxy +
20% relative strength + 15% persistence + 10% structural breakout. Measured
**before** the exhaustion penalty. An early-pattern resemblance heuristic,
**not a probability**.

**`opportunity_score`** — 30% explosion potential + 25% tradeability + 20%
pattern strength + 15% relative strength + 10% persistence, **minus the
exhaustion penalty**. The hard gate runs before this, so a $4k market can never
reach a serious ranking regardless of acceleration.

### Exhaustion is applied exactly once

The penalty is subtracted only at the opportunity layer. Explosion potential
stays pre-exhaustion deliberately, so the two scores answer different
questions and a configured penalty costs exactly its nominal points:

| | Early strong mover | Late parabolic mover |
| --- | --- | --- |
| `explosion_potential_score` | high | **still high** |
| `exhaustion_penalty` | ~0 | large |
| `opportunity_score` | high | reduced |
| Stage | may progress | downgraded / suppressed |

A market can therefore read as genuinely explosive while being a poor thing to
look at *right now* because it is already extended. Both numbers are retained
on the candidate and in the leaderboard, which is what Phase 2 needs in order
to calibrate the penalty against real outcomes.

## 6. Exhaustion / chase penalty

Bands: 0–10 normal, 10–20 moderately extended, 20–35 clearly extended, 35–50
blow-off. Capped at 50.

Evidence: short-window run-up from runtime history (primary); weakening
acceleration after a large run; price rising sharply while the volume proxy is
no longer strengthening. `lift_from_24h_low_pct` is retained as **weak legacy
evidence only** and is capped at 6 points so it cannot dominate.

**A coin is never penalised merely for being strong.** The extension term stays
at zero until the recent run-up passes `run_up_soft_pct` (12%), so a fast, fresh
mover collects no chase penalty at all. The objective is to separate *early
strong acceleration* from *late parabolic extension*.

Reason codes: `EXTENDED_MOVE`, `MOMENTUM_DECELERATING`, `BLOW_OFF_RISK`.

The penalty is applied once, at the opportunity layer only (see §5).

## 7. Stage machine

All four stages are advisory. **`ACTIONABLE_REVIEW` authorises human review, not
entry.** Cards render `Action: HUMAN REVIEW ONLY — no entry is authorized`.

| Stage | Opportunity | Explosion | Tradeability | Persistence | Exhaustion |
| --- | --- | --- | --- | --- | --- |
| `EARLY_BUILDING` | ≥55 | ≥50 | ≥20 | — | <25 |
| `BREAKOUT_CANDIDATE` | ≥70 | ≥65 | ≥40 | ≥2 scans | <25 |
| `ACTIONABLE_REVIEW` | ≥80 | ≥75 | ≥70 | ≥3 scans | <20 |

`SUPPRESSED` on any hard-gate failure: liquidity below the floor, or invalid /
non-finite market data. Markets in the $100k–$250k observation band can never
advance beyond `EARLY_BUILDING`.

## 8. Notification boundary

Filtering happens **before** the cap, which is the fix for the sliced-list
defect. The main feed accepts `BREAKOUT_CANDIDATE` and `ACTIONABLE_REVIEW` only;
`EARLY_BUILDING` requires `SIGNAL_QUALITY_EARLY_ALERTS_ENABLED`. `SUPPRESSED`
never reaches Telegram — it stays in the leaderboard and the scan logs as
`Status: SUPPRESSED / Reason: <code>`, so the gate remains auditable.

## 9. Reason codes

`INSUFFICIENT_LIQUIDITY`, `OBSERVATION_ONLY_LIQUIDITY`, `WEAK_PATTERN`,
`WEAK_RELATIVE_STRENGTH`, `INSUFFICIENT_PERSISTENCE`, `VOLUME_NOT_CONFIRMING`,
`EXTENDED_MOVE`, `MOMENTUM_DECELERATING`, `BLOW_OFF_RISK`,
`INVALID_MARKET_DATA`. All deterministic and directly tested.

## 10. Configuration

Flat typed `Settings` fields, consistent with the rest of the repository.
Operator-facing knobs are environment-driven; composition weights and structural
pattern boundaries stay in code, where they are reviewable as priors rather than
becoming production drift surface.

`Settings` refuses to boot on a misordered ladder (liquidity bands, stage
thresholds, persistence minimums, exhaustion tolerances) — an inverted band
would silently turn a gate into a no-op.

### Scan cadence

The repository ships **no cron entry for `app.jobs.scan_movers`**. The only
shipped scan-class schedule is `deploy/cron.d/ohm-wave5-explosion-learning`
(`*/10`), so `SIGNAL_QUALITY_SCAN_INTERVAL_SECONDS` defaults to 600 to match it.
The continuity window is `interval × SIGNAL_QUALITY_CONTINUITY_MULTIPLIER`
(2.5 → 1500s): one late scan does not reset a persistence chain, a real outage
does. **If the deployed cadence differs, set this to the real value** — every
rate normalisation, continuity decision and retention check keys off it, and
the boot validator will reject a retention window that no longer outlasts
continuity.

## 11. Phase 2

Phase 2 replays production `full_market_observations.jsonl` plus the applicable
learning registries. The replay harness **must reconstruct time-based scans**
rather than treating each persisted row as an equivalent regular interval; the
stream is event-sampled and treating rows as uniform would bake that bias into
the calibration. Target outcome classes: `MOVE_20`, `MOVE_50`, `MOVE_100`,
`MOVE_200`, `MOVE_300_PLUS`.

Phase 2 also replaces the rolling-volume proxy with true interval volume, and
extends `tradeability_score` with spread, depth, slippage, trade count and
turnover.

`weights_are_calibrated` stays `False` until that work is done.

## 12. Relationship to the existing Signal Quality Audit

`app/services/signal_quality_audit.py` is **retrospective trade-outcome
analysis** and is deliberately left separate. It must not become the live Broad
Watch scoring engine. No code is shared between them.

## 13. Safety invariants

Asserted in tests on every candidate: `advisory_only=True`,
`weights_are_calibrated=False`, `trade_authority_changed=False`,
`production_execution_gate_changed=False`.

No Kraken write endpoints. No autonomous orders. No automatic entry/exit
confirmation. No ML. No runtime self-tuning or automatic model promotion. No
production config changes. Not deployed.

`execution_validation.py`, `kraken_position_verification.py` and every other
execution-evidence safeguard are unmodified. Tests assert that the feature and
scoring modules cannot reach an execution or verification surface, and that they
perform no I/O.
