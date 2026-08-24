# OHM P1 Intelligence Program — Evidence and Promotion Gates

Status: governance baseline for research/shadow promotion decisions.
Production baseline: `ee1fcc8fe4fd21bc3e447319958af3d33639f645`.

These gates are intentionally stricter than exploratory analysis. They exist to prevent hindsight tuning and accidental promotion of attractive but unproven features.

## 1. General rule

No P1 score is a probability. No feature, ranker, setup rule, or catalyst feature may influence production ranking/Telegram merely because it performs well in an exploratory sample.

Before the final holdout is evaluated, the experiment must predeclare:
- primary metric(s)
- comparison baseline
- episode definition
- split dates/folds
- minimum sample size
- economically meaningful improvement threshold
- allowed feature set/version

The thresholds below are initial governance defaults. They may be revised only **before** a holdout is opened and must be versioned; they may not be loosened after results are seen.

## 2. Data-quality gate (Gate 0)

A study is not eligible for promotion review unless:
- all live features are reproducible from point-in-time data;
- no forward outcome field is present in feature inputs;
- episode deduplication is deterministic;
- chronological splits contain no episode spanning multiple partitions;
- at least 95% of eligible records have the required baseline fields;
- missing structure/catalyst fields are explicit statuses rather than imputed positive values;
- top-8 Phase 3B cohort coverage is reported separately from the full candidate population;
- each reported subgroup has at least 30 independent episodes, otherwise it is marked `INSUFFICIENT_SAMPLE`;
- the overall test/holdout set has at least 100 independent episodes before a production-influence recommendation is allowed.

Bootstrap intervals use at least 1,000 resamples at the episode level.

## 3. Phase 3C feature verification gate (Gate 1)

A candidate feature/family may become `VALIDATED_FOR_SHADOW` only if all of the following hold on the untouched test/holdout data:

1. **Direction stability** — effect direction is consistent with the predeclared hypothesis in at least two chronological validation windows/folds.
2. **Incremental value** — adding the feature/family improves at least one predeclared primary metric versus the Phase 1 baseline and does not materially degrade the paired risk metric.
3. **Uncertainty** — the 95% bootstrap confidence interval for the incremental primary effect excludes zero, unless the predeclared analysis is explicitly a non-inferiority test.
4. **Economic significance** — default minimum improvement is either:
   - at least +0.50 percentage points in mean 4h forward return versus baseline, **or**
   - at least +5% relative improvement in a predeclared MAE-adjusted expectancy metric,
   while median/mean MAE does not worsen by more than 5% relative to baseline.
5. **No single-bucket dependency** — the result is not solely explained by one tiny liquidity/rank/regime bucket.
6. **Ablation evidence** — the feature adds value beyond its nearest correlated family. Pairwise absolute correlation above 0.70 requires drop/combine/family-cap analysis before promotion.

These defaults are not trading guarantees. They are governance thresholds for allowing a feature into a shadow experiment.

## 4. Component retirement rule

Every feature/version has a lifecycle:

`CANDIDATE -> VALIDATED_FOR_SHADOW -> ACTIVE_SHADOW -> RETIRED`

A component is eligible for retirement when any of these is true:
- two consecutive chronological evaluation windows show no positive incremental value;
- its effect direction reverses on the holdout;
- correlation with a stronger feature/family exceeds 0.85 and ablation shows no independent contribution;
- data quality/coverage falls below Gate 0 requirements;
- operational cost/latency materially exceeds its measured incremental value.

Retirement never changes the Phase 1 production baseline automatically. Promotion and retirement actions require versioned evidence artifacts and review.

## 5. Phase 3D shadow composition gate (Gate 2)

Phase 3D may use only feature versions marked `VALIDATED_FOR_SHADOW`.

The first composition must be simple/interpretable and run side-by-side with Phase 1. Before any live advisory overlay is considered, require:
- at least 8 weeks of shadow observations;
- at least 200 independent eligible episodes overall;
- production-vs-shadow comparison on the same episodes;
- predeclared top-k metrics (at minimum top-5 4h return, precision@5, and NDCG or equivalent rank-quality metric);
- positive incremental top-k performance with a 95% episode-level bootstrap CI whose lower bound is above zero for the primary metric;
- at least 5% relative improvement in the primary ranking metric versus Phase 1;
- no more than 5% relative degradation in MAE/risk metric;
- rank stability diagnostics and explanation for material churn;
- no evidence that improvement comes only from illiquid or top-8-enriched subsets.

If the gate fails, the shadow ranker remains provisional and does not influence Telegram or production rank.

## 6. Limited advisory overlay gate (Gate 3)

Passing Gate 2 does **not** authorize ranking replacement.

A later, separately reviewed exact SHA may display the validated shadow ranking/setup as a clearly labeled advisory overlay only if:
- Gate 2 evidence is approved;
- rollback/removal is immediate and configuration-gated;
- Phase 1 production rank remains visible and authoritative;
- the overlay has no PendingSetup or execution authority;
- the overlay runs for at least four additional weeks before any further promotion decision.

## 7. Trade Setup Intelligence gate

Trade setup geometry remains research/advisory until it has at least 100 independent point-in-time setups and demonstrates:
- reproducible entry/invalidation/target levels under historical replay;
- positive average realized/simulated R after predeclared fee/slippage assumptions;
- target-before-invalidation and average-R metrics reported with bootstrap intervals;
- no use of forward data to choose levels or timeframe;
- separate results for retest-preferred vs non-retest setups;
- no hidden short/perp geometry.

Setup validation may support a future advisory overlay, never autonomous execution.

## 8. Catalyst feature gate

Catalyst context is excluded from Phase 3D v1 composition.

A catalyst-derived numerical feature can be proposed only in a later version after:
- deterministic entity/symbol mapping;
- source/publication/event/ingestion timestamps are stored;
- point-in-time joins prove public availability no later than decision time;
- duplicate story clustering is validated;
- missing/late news does not retroactively alter historical decisions;
- independent Phase 3C-style ablation shows incremental value beyond market/structure features.

Until then, catalyst data is context only.

## 9. Cross-coin ranking gate

Cross-coin research must compare against the existing Phase 1 ranking on identical decision cohorts.

Report at minimum:
- precision@1/3/5/10
- NDCG or equivalent gain-aware ranking metric
- Spearman rank correlation
- top-k overlap
- forward return and MAE by k
- liquidity/rank/regime stratification
- rank churn/stability

It cannot replace production ranking until it independently passes the Phase 3D Gate 2 standard and receives a separate review/approval.

## 10. Probability calibration prohibition

The words `probability`, `chance of profit`, or an equivalent calibrated-success interpretation must not be attached to P1 scores until a separate calibration study on untouched holdout data demonstrates proper calibration using an approved scoring/calibration method.

Until then:
- scores are ordinal/advisory;
- confidence metadata means data/source/classification confidence only, not probability of price movement;
- Telegram production semantics remain unchanged.

## 11. Required evidence artifact

Every promotion review must produce a versioned evidence bundle containing:
- exact code SHA
- exact feature/schema versions
- data window and split dates
- episode count and exclusion reasons
- missingness/coverage report
- baseline and candidate metrics
- uncertainty intervals
- correlation/ablation report
- subgroup performance
- failure/false-positive examples
- conclusion: `PASS`, `FAIL`, or `INSUFFICIENT_EVIDENCE`

Only `PASS` is eligible for the next shadow/advisory gate. Even a `PASS` requires human review and explicit approval before any production behavior change.