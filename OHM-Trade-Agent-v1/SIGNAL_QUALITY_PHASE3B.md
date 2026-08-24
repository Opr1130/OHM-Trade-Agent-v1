# Signal Quality Phase 3B — Technical Structure + Chase-Risk Intelligence

## Status

DESIGN / IMPLEMENTATION WORKSTREAM. No production authority changes.

Phase 3B is additive and advisory-only. It must not change production Signal Quality thresholds, score weights, liquidity gates, Telegram semantics, Kraken access, PendingSetup lifecycle, or execution behavior without a later separately approved integration phase.

## Objective

Use the Phase 3A timing/telemetry evidence base to answer two practical questions for every candidate:

1. Is the price structure objectively supportive of a new spot entry, pullback/retest entry, or only continued observation?
2. Has too much of the move already occurred, making the candidate a late/chase-risk setup even though momentum remains strong?

The DRVUSD case is a motivating example only. Phase 3B must be population-oriented and deterministic, not tuned to one coin.

## New modules

### `app/services/technical_structure.py`

Deterministic, no-lookahead structure primitives over completed observations only:

- confirmed swing highs / lows
- bullish structure break (close above confirmed swing high)
- bearish structure break (close below confirmed swing low)
- conservative CHoCH-style transition only when a complete HH/HL relation flips to a complete LH/LL relation, or vice versa; mixed compression such as LH + HL is MIXED but not CHoCH
- measurable imbalance / FVG-style low-overlap zones
- liquidity sweep of prior swing/equal high/low followed by reclaim
- breakout retest / level hold, with later close-through invalidating an earlier held retest for that breakout event
- distance from most recent structural breakout
- current structural bias: BULLISH / BEARISH / MIXED / INSUFFICIENT_DATA

The code may render familiar labels such as BOS, CHoCH, FVG, SWEEP, RETEST, but every label must map to a testable deterministic rule. No subjective chart-reading heuristics.

### `app/services/chase_risk.py`

Independent assessment that does not alter Phase 1 scoring:

Inputs should include only already-known or explicitly supplied point-in-time data, such as:

- current/reference price
- recent confirmed high/low
- distance from 24h high
- lift from 24h low
- move completed fraction where available
- recent acceleration / persistence / exhaustion
- structure-break level and retest state
- Phase 3A timing context when available

Output:

- chase_risk_score: 0..100
- band: LOW / MODERATE / HIGH / EXTREME
- extension_pct_from_breakout
- distance_from_recent_high_pct
- retest_available: bool
- late_entry: bool
- reasons: tuple[str, ...]
- advisory_only: True

The assessment must be deterministic and explainable. It must not authorize a trade.

### Provisional chase-risk priors

All chase-risk weights, bands, and the current `late_entry` cutoff are **uncalibrated Phase 3B priors**, not production policy and not estimated probabilities.

Independent review identified intentional overlaps that must be measured before any later integration:

- extension from breakout and proximity to recent/24h high can both represent run-up magnitude;
- extension from breakout and Phase 3A move-completed fraction can be correlated by construction;
- lift from 24h low and move-completed fraction may also overlap;
- persistence, exhaustion, and the `NOT_SEEN` retest adjustment can overlap with extension;
- the `HELD` retest deduction and score=60 `late_entry` boundary are provisional heuristics.

Phase 3B does **not** resolve these by auto-tuning. The purpose of the initial implementation is to create deterministic, auditable features that can be evaluated on the Phase 3A population. Volatility-normalized extension (ATR/recent-range normalization) is a candidate future experiment, not an adopted requirement.

## Safety boundary

Phase 3B is spot-only and advisory-only.

- BUY may describe a possible long/spot entry setup.
- HOLD / REDUCE / EXIT may describe existing spot-position management in later phases.
- No OPEN SHORT recommendation.
- No leverage, perpetuals, futures, or autonomous order behavior.
- No calls into order placement, confirmation, PendingSetup, Telegram callbacks, or Kraken execution.

## Data lineage

All technical structure and chase-risk features must be computed from observations at or before the decision timestamp. Forward outcomes are allowed only in offline validation/replay and must never feed live decisions.

Do not make a second market-data fetch solely for Phase 3B if the required same-scan observation already exists in memory.

## No duplicate production scanner

Phase 3B should initially be pure service code plus offline/shadow tests. Do not introduce a permanent second live scanner or a competing scan clock. Production composition, if later approved, should occur through the existing scan cycle.

## Proposed API

```python
@dataclass(frozen=True)
class TechnicalStructureContext:
    symbol: str
    observed_at: datetime
    bias: str
    last_swing_high: float | None
    last_swing_low: float | None
    bullish_break_level: float | None
    bearish_break_level: float | None
    change_of_character: bool
    imbalance_zone_low: float | None
    imbalance_zone_high: float | None
    liquidity_sweep: str | None
    retest_state: str | None
    reasons: tuple[str, ...]
    advisory_only: bool = True

@dataclass(frozen=True)
class ChaseRiskAssessment:
    score: int
    band: str
    extension_pct_from_breakout: float | None
    distance_from_recent_high_pct: float | None
    retest_available: bool
    late_entry: bool
    reasons: tuple[str, ...]
    advisory_only: bool = True
```

Exact field names may evolve during implementation, but semantics must remain deterministic and spot/advisory-only.

## Validation requirements

Tests must cover at minimum:

- strict no-lookahead: future observations cannot alter structure at decision time
- swing confirmation only after required completed bars
- flat/plateau highs and lows do not become confirmed pivots accidentally
- exact BOS boundary behavior
- HH/HL and LH/LL sequence transitions
- mixed compression is not mislabeled as CHoCH
- FVG/imbalance detection and non-detection cases
- sweep-and-reclaim detection
- breakout retest success / failure, including later invalidation of an earlier held retest
- chase score monotonicity for increasing extension with otherwise identical inputs
- near-high risk increases only when supported by actual point-in-time data
- missing-data degradation to INSUFFICIENT_DATA / neutral risk rather than invented certainty
- deterministic repeatability
- spot-only safety invariants
- no imports from execution / PendingSetup / Telegram callback modules
- Phase 1 / Phase 2 / Phase 3A regression remains clean

## Population validation plan

Before Phase 3C/3D integration, evaluate Phase 3B against Phase 3A telemetry/replay using:

1. chronological train/validation/test segmentation rather than random shuffling;
2. episode deduplication so repeated scans from one move are not treated as independent evidence;
3. 15m / 30m / 60m / 4h forward returns plus 24h MFE, MAE and time-to-extreme where observable;
4. chase-score buckets and component ablations;
5. structure-bias and retest-state buckets;
6. false-positive analysis for high chase scores followed by continued favorable returns;
7. regime and liquidity stratification;
8. bootstrap/confidence intervals or comparable uncertainty estimates when sample sizes support them.

The key test is empirical monotonicity: higher chase-risk buckets should demonstrate measurably worse entry timing or risk-adjusted forward outcomes before the score is allowed to influence ranking or alerts. If that relationship does not hold out-of-sample, weights/components must be revised rather than rationalized after the fact.

## Evidence rule

Do not select production thresholds from one example. Phase 3B may propose candidate chase-risk bands and structure thresholds, but they remain provisional until evaluated against Phase 3A telemetry/replay with chronological out-of-sample validation.

## Integration checkpoint

Before any live Telegram or ranking integration:

1. complete the pure technical_structure and chase_risk modules;
2. run dedicated tests and Phase 1/2/3A regressions;
3. run offline/shadow population analysis;
4. review results with architecture and quantitative reviewers;
5. only then design Phase 3C/3D composition.

No merge or production deployment without separate approval.
