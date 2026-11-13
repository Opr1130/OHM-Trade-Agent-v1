# O'Pip Sequence 5 Wave A1 — Exact Learning Evidence Linkage

Status: evidence-only implementation.

## Purpose

Wave A1 closes the first missing Sequence 5 learning boundary by producing a
deterministic relationship between existing canonical evidence, ML
FeatureSnapshots, paper outcomes and Phase 3C forward outcomes.

No new trading path is introduced.

## Exact-link policy

O'Pip links evidence only through immutable identifiers already present in the
records:

- canonical `snapshot_id`
- canonical `episode_id`
- ML wrapper `canonical_snapshot_id` / `ml_snapshot_id`
- paper `paper_trade_id` / `episode_id`
- Phase 3C `snapshot_id` / `outcome_record_id` / `outcome_revision`

There is intentionally no fallback using ticker, timestamp proximity, rank,
price proximity or other fuzzy heuristics.

Conflicting duplicate immutable identities fail closed.

## Outcome authority

Closed paper lifecycle evidence is classified as `FINAL_PAPER` only when it
is explicitly paper-only, has no exchange write authority and has a valid
LONG/SHORT direction.

Phase 3C event-sampled observations retain the explicit source:

`PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS`

They remain `PROVISIONAL_MARKET` and can never silently become final
supervised truth.

## Cohorts

Wave A1 uses four explicit cohorts:

- `QUALIFIED_PAPER`
- `COUNTERFACTUAL_REJECTED`
- `OBSERVATION_ONLY`
- `INELIGIBLE_UNLINKED`

Rejected/counterfactual evidence is research-only. It cannot enter the primary
supervised cohort even if a later outcome looks favorable.

## Primary supervised eligibility

A linkage row is eligible only when:

1. an exact canonical -> ML FeatureSnapshot link exists;
2. the original cohort is `QUALIFIED_PAPER`;
3. exactly one final paper outcome links by the same episode identity; and
4. the outcome is not censored, data-gapped or execution-path ambiguous.

This is an evidence-quality flag only. It does not train or promote a model.

## Permanent authority boundary

The module performs normalization and linkage only. It has no exchange,
order, Telegram, position or execution dependency and cannot change ranking,
risk gates, PendingSetup, paper admission, alerts or funded trading authority.
