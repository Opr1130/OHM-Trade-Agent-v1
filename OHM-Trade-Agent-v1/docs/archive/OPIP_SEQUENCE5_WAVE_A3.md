# O'Pip Sequence 5 Wave A3 — Paper and ML Data Readiness

Status: evidence-only; no model training or production scoring.

## Paper learning readiness

The current production paper architecture is explicitly audited rather than
assumed.

- LONG architecture exists, but the readiness report fails closed until
  production LONG health is explicitly verified.
- SHORT remains NOT_READY because Paper Trade v1 is LONG-only.
- funding, margin, liquidation and SHORT lifecycle accounting are not claimed
  until they are implemented and validated.
- no readiness state grants funded execution or leverage authority.

This allows the production paper incident to remain visible instead of being
masked by static architecture support.

## ML Data Readiness v1

The readiness report measures:

- canonical evidence count
- ML FeatureSnapshot and feature-bearing count
- exact outcome linkage and rate
- final supervised truth vs provisional-only evidence
- point-in-time violations
- duplicate and malformed evidence
- per-feature and overall missingness
- censored, ambiguous and data-gap outcomes
- direction, lane, regime and asset coverage
- horizon, MFE and MAE availability
- qualified, counterfactual and observation-only cohorts
- primary supervised usable rows
- explicit exclusion reasons

The deterministic states are:

- NOT_READY — a structural integrity/truth requirement fails
- COLLECT_MORE_DATA — integrity is clean but support/coverage is insufficient
- READY_FOR_OFFLINE_TRAINING — every declared policy gate is satisfied

The default minimum support is a declared reviewable statistical support
constant, not a time-based rule. There is no "30 days" or "10,000 trades"
requirement.

## Final truth

Phase 3C event-sampled outcomes remain provisional and cannot unlock training.
Only exact, final paper outcome evidence can currently satisfy final supervised
truth.

## Read-only source boundary

The readiness job reads canonical, ML, Phase 3C, paper and capture-health source
files without invoking registry quarantine/repair behavior. Corruption is
counted as malformed evidence and fails readiness closed. Only the dedicated
readiness report file is written.

## Authority boundary

The readiness job is not installed in the live deterministic scheduler. It
cannot train, score, promote, change ranking, change risk policy, send alerts,
or place/modify/cancel/confirm any funded order.
