# Profit Intelligence 4A — Evidence Contract

**Status:** read-only evidence increment. No trading, risk, execution, alert,
ranking, promotion, Committee, HTTP-edge or deployment authority is added or
widened.

This document records, for the two evidence families Profit Intelligence 4A
projects, exactly which canonical fact is read, who produces it, and which
versioned read model consumes it. It exists so a consumer never has to infer
provenance from a missing key.

## Scope

Profit Intelligence could previously answer execution-lineage and economic
questions from the canonical replica but could **not** answer:

1. how an opportunity progressed through pre-trade qualification;
2. where and why an opportunity was rejected or filtered;
3. what future outcome followed a sealed decision point.

Those facts are produced on the trading host and the learning plane, which the
read-only analytics plane does not read. 4A adds two *typed, versioned, derived*
projections over the already-persisted canonical evidence. It does not add a
producer, a write path, or a second source of truth.

## Evidence producers

| Fact family | Canonical source | Producer | Projection | Version |
| --- | --- | --- | --- | --- |
| Qualification progression | `funnel_events.jsonl` / `screening_evaluations.jsonl` under `/app/data/opip/qualification/` | `app.opip.decision.observer` driving `app.opip.decision.funnel`, persisted by `app.opip.decision.store` | `QualificationFunnelProjectionV1` (`app/opip/profit_intelligence/qualification_funnel.py`) | `profit-intelligence-qualification-funnel-v1` |
| Rejection / gate attribution | terminal `GateResult` records inside the same `funnel_events.jsonl` rows | same producer | same projection (canonical `reason_code` / `reason_class` / `first_terminal_gate`) | same |
| Forward outcomes (Phase 3C) | `/app/data/phase3c_forward_outcomes.jsonl` | `app.services.phase3c_outcomes` matured by `app.jobs.build_phase3c_forward_outcomes` | `ForwardOutcomeProjectionV1` (`app/opip/profit_intelligence/forward_outcomes.py`) | `profit-intelligence-forward-outcomes-v1` |
| Forward outcomes (Discovery V2-01) | `app.opip.discovery.store.FORWARD_OUTCOMES_FILE` | `app.opip.discovery.outcomes` matured by `app.jobs.build_discovery_forward_outcomes` | same projection, distinct source | same |

The two forward-outcome families are never merged: they are different
producers with different identities, horizons and schema versions. Phase 3C
labels (`5m…24h`) and Discovery labels (`1h/4h/12h`) are published unchanged;
`60m` and `1h` are the same duration but remain distinct canonical labels.

## Availability semantics

Availability reuses `app.opip.cockpit.trust` (`Freshness`, `Completeness`,
`TrustEnvelope`) and `app.opip.profit_intelligence.semantics.FactAvailability`
(`KNOWN`, `DERIVED`, `UNAVAILABLE`, `NOT_APPLICABLE`).

- A bucket this evidence family **cannot produce** is declared in
  `DISPOSITIONS_WITHOUT_CANONICAL_PRODUCER`, never fabricated as a measured
  zero. The pre-trade funnel never produces `RISK_BLOCKED` or
  `EXECUTION_FAILED`: risk gates and execution attempts are downstream of this
  family.
- `UNAVAILABLE` / `UNKNOWN` / `INCOMPLETE` are never converted into `0`.
- An unreadable store yields a structurally identical envelope with
  `UNAVAILABLE` freshness and `UNKNOWN` completeness — never an empty-but-healthy
  result.

## Conservation and idempotency

- A record is *attributable* only when its canonical identity and decision are
  readable. Terminal dispositions reconcile to the attributable population;
  an unattributable row forces `holds = false` and is reported as
  `unattributed`, never silently dropped or double-counted.
- Replayed/duplicate appends are collapsed by canonical identity
  (`(scan_id, candidate_id)` for the funnel; `snapshot_id` / `observation_id`
  for forward outcomes) and the ignored count is reported.

## Anti-hindsight contract

- Decision-time facts (`reference_at`, `reference_price`, direction) are kept in
  separate fields from every horizon's future facts.
- A forward window must be anchored strictly after the sealed cutoff;
  `forward_window_is_point_in_time` proves the declared window arithmetic given
  the sealed `reference_at` (a violation marks the horizon `UNAVAILABLE` rather
  than being trusted). It does not independently validate the upstream
  observations that produced the values; those remain the canonical producers'
  responsibility, and consumption here is `EVIDENCE_ONLY`.
- Incomplete windows stay incomplete and unavailable market data stays
  unavailable.
- Consumption is `EVIDENCE_ONLY`. Nothing here can change a threshold, a
  strategy or a trading decision.

## The join is evidence-only

`join_funnel_record_to_forward_outcomes` joins on the canonical episode identity
(`funnel.episode_id` ↔ `forward.canonical_episode_id`). It reports
`MATCHED` / `NO_MATCH` / `AMBIGUOUS` / `IDENTITY_UNAVAILABLE` /
`SOURCE_UNAVAILABLE` and never chooses a row from an ambiguous match. `NO_MATCH`
means a verified absence across every supplied **readable** source; if any
supplied source was unreadable (or none was read), the join reports
`SOURCE_UNAVAILABLE` rather than asserting absence. Even on a match it returns
`classification = MISSED_PROFIT_CLASSIFICATION_UNAVAILABLE`: a positive market
outcome is not executable profit.

## Missed profitable opportunity

`MISSED_OPPORTUNITY_DISPOSITION = BLOCKED_AT_FROZEN_BOUNDARY` is unchanged. The
exact remaining ingredients and the required producer are recorded in
`MISSED_OPPORTUNITY_UNBLOCK` (`app/opip/profit_intelligence/semantics.py`).

## Explicit non-goals

No HTTP/Grafana/Cockpit exposure, no widening of the frozen four-path reverse
proxy contract, no production trading change, no strategy promotion, no
Committee activation, no provider activation, no secrets, no deployment.
