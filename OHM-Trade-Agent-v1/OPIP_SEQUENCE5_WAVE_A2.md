# O'Pip Sequence 5 Wave A2

Status: evidence-only.

## Governed learning

The lifecycle is:

OBSERVATION -> HYPOTHESIS -> SHADOW_TEST -> ACCEPTED | REJECTED

ACCEPTED requires an explicit human principal and approval timestamp. Lifecycle
records retain evidence identifiers, metrics, known regressions, and optional
effective/rollback references. Automatic activation and automatic promotion are
prohibited.

## Paired evaluation

Champion and challenger are evaluated on the same immutable T0 sample. Reports
include support, coverage, net expectancy, precision/recall, false-positive and
false-negative counts, MFE/MAE, tail-loss proxy, and missed-opportunity cost.
Confidence intervals are emitted only above declared support.

## Structured diagnostics

The zero-trade diagnostic consumes candidate/gate evidence, provider health,
and linkage readiness. It reports candidate counts, binding gate, nearest miss,
event/risk restriction, degraded providers, and readiness issues without
parsing free-form logs.

All outputs remain measurement-only.
