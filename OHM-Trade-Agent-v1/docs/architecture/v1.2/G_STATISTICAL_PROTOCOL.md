# G. Statistical protocol

## Primary endpoint

Net portfolio dollars over a common evaluation window versus:

1. cash / no-trade
2. the frozen legacy comparator

## Minimum meaningful economic effect

The minimum meaningful effect is **experiment-derived and owner-approved per registered family**. It is not an architecture constant.

Do **not** promote reviewer-supplied N_eff, profit-factor, ECE, or bps thresholds to architecture constants.

Existing P1 promotion-gate numbers (for example +0.50 percentage points 4h forward return, episode-count floors) remain **legacy research-gate documentation**. They are not v1 Profit Intelligence architecture invariants.

## Variance and dependence

Dependence-aware evaluation resamples **contiguous time blocks** that contain the contemporaneous candidate / portfolio panel.

No universal block length or N_eff threshold is an architecture constant.

## Sample size / power / precision

Each registered experiment declares, before unblinding:

- family membership
- primary endpoint
- minimum meaningful effect
- power or precision target
- dependence treatment
- stopping rule

## Multiple testing

Multiple-testing control is mandatory. The exact procedure and alpha are experiment-derived.

Default v1 screening should use valid family-wise control for the registered small family. Sealed prospective evaluation remains required.

## Sealed prospective evaluation and stopping

- Hypotheses are frozen before the prospective window opens.
- Stopping rules are predeclared.
- Do not force statistical promotion to meet a calendar date.

## Scoring, shrinkage, missingness

- Use proper scoring rules and reliability analysis for execution probabilities and conditional path probabilities **separately**.
- Thin cohorts use shrinkage and/or abstention (`INSUFFICIENT_EVIDENCE`).
- Missingness and fidelity-grade sensitivity are required.
- Sampled-success-only evidence is forbidden.
