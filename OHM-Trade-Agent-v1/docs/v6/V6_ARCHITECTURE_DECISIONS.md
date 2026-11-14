# V6 architecture decisions (for owner review)

D0 routine, D1 reversible (implemented in isolation), D2 material (recommendation only), D3 owner approval required.

| ID | Class | Decision | Rationale | Risk | Owner action |
|---|---|---|---|---|---|
| V6-D01 | D1 | Build the simulator as a pure package `app/opip/simulator` with its own parameterised rules, baseline taken from the validated reference policy | smallest isolated deliverable; no scanner coupling | rules could drift from production gates | none; see V6-D02 |
| V6-D02 | D2, owner-approved for design/isolated work 2026-11-13 | Expose production gates through one pure, versioned evaluation interface and have scanner and simulator both call it | removes duplicate rule logic and proves equivalence | touches the live qualification path | decide after S1 report; no change made |
| V6-D03 | D1 | Simulator evidence is labelled SYNTHETIC or SUPPLIED_UNVERIFIED and states it is not a profitability claim | prevents mixing backtest, paper, manual and synthetic results | none | none |
| V6-D04 | D1 | Comparison-ledger rotation uses a registry lock, unique archive names and counted drops (draft PR #560) | stops silent overwrite of evidence before PostgreSQL exists | lock timeout drops a record (counted) | review PR #560; preserve current files before any deploy |
| V6-D05 | D1 | Record `max_output_tokens` per Chief call (additive) | lets truncations be compared to the cap | none | none |
| V6-D06 | D2 | Do not implement lineage fields, dedupe of tracking-failure records, H2 reconciliation mode, labeler or shadow plans yet | depend on the S1 report and S4 persistence design | speculative fixes | wait for report |
| V6-D07 | D1 | No retrospective V1/V2 trade plans; historical comparisons stay SCORE_ONLY_NO_PLAN | decision-time evidence requirement | none | none |
| V6-D08 | D0 | No new infrastructure (no Redis/Kafka/Kubernetes/extra DB); reuse registry_lock, existing telemetry | CLAUDE.md and 4 GiB host | none | none |
| V6-D09 | D1 | The shared gate layer already exists in `app/opip/decision`; add simulator parity (read-only) instead of a third interface. See V6_SHARED_GATE_INTERFACE.md | avoids a duplicate engine | simulator stays approximate | none |
| V6-D10 | D2 | Gate threshold injection is gated until S1 evidence and byte-identical-decision proof | live path | regression | later approval |
