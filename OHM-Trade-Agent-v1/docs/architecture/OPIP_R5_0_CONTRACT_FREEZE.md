# O'Pip R5-0 structural contract freeze (SC-01 - SC-13)

Status: `R5-0 STRUCTURAL CONTRACTS FROZEN (DETERMINABLE PART)`
Increment: `ATDD-R5-0-contract-conformance`
Freeze of record: `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`, extraction `docs/architecture/v1.5.0/ARCHITECTURE.md`, identity pin `docs/architecture/v1.5.0/SOURCE.md`
Inherited clause registers preserved: `docs/architecture/v1.4.4/ARCHITECTURE.md` Appendix A (`PA-01`-`PA-20`) and Appendix B (`SQ-01`-`SQ-10`)
Companion register: `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md`
Authority granted by this freeze: `NONE`

## What this freeze is and is not

- It is the structural contract layer for the R5 requirements register: it names the boundaries, records, keys, ordering and ownership that the adopted body already determines, so later increments can be built against a stable surface.
- It is not a plan, a schedule, a design approval or a delivery increment. It creates no runner, no scheduler, no store and no execution path.
- It is not authoritative over the adopted sources. Where this freeze and a pinned source disagree, the pinned source controls and this freeze is corrected.

`PRODUCTION TRADE AUTHORITY GRANTED BY THIS FREEZE = NONE`
`FUNDED OR EXCHANGE ORDER AUTHORITY GRANTED BY THIS FREEZE = NONE`
`DEPLOYMENT OR RELEASE AUTHORITY GRANTED BY THIS FREEZE = NONE`
`NUMERIC POLICY RATIFIED BY THIS FREEZE = NONE`

This freeze ratifies no numeric policy. Every numeric input the adopted body leaves open stays `OWNER_DECISION_REQUIRED` in the register as open-input rows `OQ-01`-`OQ-10`; no threshold, duration, budget, cap or calibration value is chosen, implied, defaulted or inherited by this document. Contracts whose numeric surface is owner-gated are declared `OPEN`.

Frozen boundaries, restated and not widened: no new event store, no allocator or allocation database, no second outcome engine, no second position registry, no second scheduler, no second deployment control plane, and no funded or exchange authority. Work extends the existing module families named in the register's reuse verdicts.

Safeguards restated, not changed: the production posture remains the `EVIDENCE_SHADOW` release profile, `TARGET_PAPER` remains `BLOCKED`, and the Compose core services keep the literal `OPIP_PAPER_V2_MODE=off`. Paper and research surfaces (`PM-01`, `PM-02`) stay isolated from funded authority and cannot generate their own proof or activate the target engine.

No row of this freeze carries `LIVE` authority: it declares no funded trading authority and no exchange order authority, and it states zero deployment authority for the increment.

## How to read a contract section

Each `## SC-nn` section carries these labelled lines, in this order: `Register rows:`, `Adopted clauses:`, `Freeze status:`, `Open owner input:`; a section that owns a logical record also carries `Frozen record:`, `Record keys:`, `Owning module:` and `Reuse status:`.

- `Register rows:` - the declared rows of the companion register whose `Contract` field names this contract. The inverse direction is declared in the register and re-declared here.
- `Adopted clauses:` - the decisive clause identifiers. Each is a v1.5.0 section `§N` or a clause identifier that occurs verbatim in a pinned source.
- `Freeze status:` - `FROZEN` or `OPEN`. `FROZEN` covers the structural surface only: records, keys, identity, ordering, precedence and authority. `OPEN` means the adopted body leaves the structural surface itself owner-gated.
- `Open owner input:` - `OQ-*` rows, or `NONE`. An `OQ` named here never carries a default.
- `Frozen record:` - the logical record name as adopted in v1.5.0 section 10, or `NONE`.
- `Record keys:` - backticked key phrases copied from the adopted clause text; every phrase occurs verbatim in the pinned source. A logical record's keys come from the v1.5.0 section 10 lineage table, so every contract that owns a record adopts `§10` as well as the clause that governs the record's behaviour.
- `Owning module:` - exactly one existing repository module, repository-root relative.
- `Reuse status:` - one of `REUSE_EXISTING_MODULE`, `EXTEND_EXISTING_MODULE`, `NO_MODULE_CHANGE`.

## Frozen owner table: one existing owner per logical record

| Logical record | Owning module | Reuse status | Governing contract |
| --- | --- | --- | --- |
| AttentionTrigger | `OHM-Trade-Agent-v1/app/opip/events/contract.py` | REUSE_EXISTING_MODULE | SC-02 |
| OpportunityThesis | `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py` | EXTEND_EXISTING_MODULE | SC-04 |
| CapitalSnapshot | `OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py` | EXTEND_EXISTING_MODULE | SC-06 |
| PortfolioPlan | `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py` | EXTEND_EXISTING_MODULE | SC-08 |
| RotationGroup | `OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py` | EXTEND_EXISTING_MODULE | SC-09 |
| Expectation and Outcome | `OHM-Trade-Agent-v1/app/opip/learning/evaluation.py` | EXTEND_EXISTING_MODULE | SC-10 |
| CanonicalTemporalRecord | `OHM-Trade-Agent-v1/app/opip/canonical/writer.py` | REUSE_EXISTING_MODULE | SC-12 |

Each logical record has exactly one owning module, each module is claimed by at most one record, and every module already exists in the repository. Ownership is ownership over an existing module family, not permission to create a store, a registry, an engine or a scheduler. A record with no owner in this table has no owner and no new module may be introduced for it in this increment.

## SC-01 Continuous observation coverage and availability evidence

- Register rows: `F1`, `R5-A`
- Adopted clauses: `§3`, `ME-01`, `ME-07`
- Freeze status: `FROZEN`
- Open owner input: `OQ-04`
- Frozen invariants:
  - Observation stays incremental and event-driven: an unchanged world costs nothing and a material change produces a record (`ME-01`).
  - Availability evidence is retained: what was observed, when it arrived and what could not be observed are all persisted, and gaps reconcile against the same watermark rather than a wall clock (`ME-07`, `DC-01`).
  - Work stays bounded (`ME-04`) and fair across instruments (`ME-05`); the budget values themselves remain owner input and are not frozen here.

## SC-02 Continuous attention trigger and attention states

- Register rows: `F1`, `F2`, `R5-A`
- Adopted clauses: `§3`, `§10`, `ME-02`, `ME-03`
- Freeze status: `FROZEN`
- Open owner input: `NONE`
- Frozen record: `AttentionTrigger`
- Record keys: `instrument/version`, `trigger ID`, `policy`, `source event IDs`, `prior/new state`, `event/receipt/availability time`, `request time`, `cutoff`, `watermark`, `expiry`, `suppression disposition`
- Owning module: `OHM-Trade-Agent-v1/app/opip/events/contract.py`
- Reuse status: `REUSE_EXISTING_MODULE`
- Frozen invariants:
  - One record per transition request, grain as adopted: the trigger names the instrument and version, its policy, the source events that produced it, and the state it moves between (`ME-03`).
  - Attention states stay a closed, declared set rather than free text (`ME-02`), and the existing `app/opip/events/*` and `app/opip/streaming/*` families keep ownership; this contract introduces no second event store.

## SC-03 Scheduling, fairness, capacity and workload budgets

- Register rows: `F1`, `F2`, `PA-03`, `SQ-01`, `SQ-03`, `SQ-09`, `R5-A`, `G2`, `OQ-02`
- Adopted clauses: `§3`, `ME-04`, `ME-05`, `ME-06`
- Freeze status: `OPEN`
- Open owner input: `OQ-04`, `OQ-02`
- Frozen invariants:
  - Scheduling stays reproducible and bounded, replays without a hidden wall clock, and separates priorities instead of silently dropping work (`ME-04`, `ME-06`).
  - Fairness is required by clause but its numeric budgets are not: latency, freshness, queue and workload limits stay `OQ-04`, and instrument identity and corporate-action census disposition stay `OQ-02`.
  - This contract is `OPEN` because its structural surface is an owner-approved budget schedule, not a fixed record.

## SC-04 Horizon thesis record: identity, keys and validity

- Register rows: `F3`, `F4`, `F5`, `F6`, `PA-02`, `PA-11`, `PA-16`, `SQ-10`, `R5-B`
- Adopted clauses: `§4`, `§5`, `§10`, `HZ-01`, `HZ-02`
- Freeze status: `FROZEN`
- Open owner input: `OQ-03`
- Frozen record: `OpportunityThesis`
- Record keys: `episode/claim/decision IDs`, `horizon class`, `classifier version`, `entry window`, `first-fill anchor`, `holding range`, `price/time invalidators`, `superseded revision`
- Owning module: `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py`
- Reuse status: `EXTEND_EXISTING_MODULE`
- Frozen invariants:
  - One record per thesis revision, with the thesis identity frozen and never rewritten in place: an extension or reclassification is a new revision (`HZ-01`, `HZ-02`).
  - The original thesis and the first-fill clock are immutable (`HZ-02`), and logical states map onto the existing `F4` enums rather than a parallel machine.
  - Horizon boundary durations and expected holding ranges stay `OQ-03`; the record surface is frozen while the numbers are not.

## SC-05 Protective precedence and lifecycle state contract

- Register rows: `F4`, `PA-13`, `PA-14`, `PA-15`
- Adopted clauses: `§5`, `LC-01`, `LC-02`, `LC-03`
- Freeze status: `FROZEN`
- Open owner input: `NONE`
- Frozen invariants:
  - Protection precedes profit seeking: protection and admission suspension outrank any profit objective, and a protective close invalidates the thesis rather than being scored against it (`LC-01`).
  - Profit targets, trailing rules, partial exits and time invalidation stay predeclared deterministic policies; they are not free parameters (`LC-02`).
  - The canonical writer acknowledges only committed transitions and recovery resumes from committed state (`LC-03`). This contract widens no veto and adds no second position registry.

## SC-06 Capital snapshot and accounting partitions

- Register rows: `F7`, `R5-C`
- Adopted clauses: `§6`, `§10`, `CI-01`, `CI-02`, `CI-03`
- Freeze status: `FROZEN`
- Open owner input: `NONE`
- Frozen record: `CapitalSnapshot`
- Record keys: `account/environment/engine`, `currency`, `cash partitions`, `positions`, `liabilities if supported`, `marks`, `sleeve/risk usage`, `fee state`, `source watermark`, `reconciliation`, `freshness`
- Owning module: `OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py`
- Reuse status: `EXTEND_EXISTING_MODULE`
- Frozen invariants:
  - One record per portfolio version: cash reconciles into its declared partitions at a committed version, and deployable capital is bounded by free cash and the remaining approved risk, concentration, liquidity and horizon limits (`CI-01`, `CI-02`).
  - The snapshot recomputes on committed fills, cancellations, expiries, deposits or withdrawals where supported, fees, marks and approvals (`CI-03`), and it reuses the existing capital-evidence and risk families instead of creating an allocator database.

## SC-07 Reservation, atomic consumption and concurrent change

- Register rows: `F7`, `R5-C`
- Adopted clauses: `§6`, `CI-04`
- Freeze status: `FROZEN`
- Open owner input: `NONE`
- Frozen invariants:
  - Reservation compares the expected portfolio version and commits capital and risk usage atomically; concurrent change fails closed rather than over-committing (`CI-04`).
  - Reservation state lives with the capital services, not in a new store, and fixed risk sizing and minimum reward-to-risk defaults remain binding and unmodified.

## SC-08 Portfolio plan record and candidate comparison

- Register rows: `F7`, `F8`, `F10`, `PA-01`, `PA-04`, `PA-05`, `R5-D`
- Adopted clauses: `§7`, `§10`, `PP-01`, `PP-02`, `PP-04`
- Freeze status: `FROZEN`
- Open owner input: `OQ-06`
- Frozen record: `PortfolioPlan`
- Record keys: `candidate-panel manifest`, `portfolio version`, `plan expiry`, `policy/forecast IDs`, `alternatives including cash`, `action sequence`, `expected economics`, `constraints`
- Owning module: `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py`
- Reuse status: `EXTEND_EXISTING_MODULE`
- Frozen invariants:
  - One record per plan revision, storing the plan and parent revision, account and environment, as-of and expiry, portfolio version, and the alternatives considered including cash (`PP-01`).
  - Eligible policy dispositions are the declared set including `HOLD_CASH`, `WAIT`, `HOLD_POSITION`, `NEW_ENTRY`, `REDUCE`, `EXIT` and `ROTATE` (`PP-02`); tie-breaking, eligibility, exclusions and the cash comparison are deterministic and recorded (`PP-04`).
  - Sleeve caps and the hard versus soft constraint behaviour stay `OQ-06`; the record surface is frozen while the cap values are not.

## SC-09 Rotation group record and switching comparison

- Register rows: `F7`, `F8`, `R5-D`, `R5-E`
- Adopted clauses: `§8`, `§10`, `RO-01`, `RO-03`, `RO-04`
- Freeze status: `FROZEN`
- Open owner input: `OQ-05`
- Frozen record: `RotationGroup`
- Record keys: `old position`, `replacement thesis`, `both-leg intent/fill IDs`, `committed proceeds`, `requalification decision`, `residual exposure`, `terminal state`, `reasons`
- Owning module: `OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py`
- Reuse status: `EXTEND_EXISTING_MODULE`
- Frozen invariants:
  - One record per linked intent group with stable identifiers linking both legs and all revisions, where a partial exit can only fund policy-allowed entries (`RO-03`).
  - Switching is judged on a common-window comparison that includes exit and entry fees and spread, and the realized net effect, turnover, time out of market and missed replacement entries are measured against a preregistered comparison (`RO-01`, `RO-04`).
  - The minimum economic advantage, uncertainty treatment, cooldown, requalification rule and turnover budget stay `OQ-05`; no advantage value is invented by this freeze.

## SC-10 Expectation and outcome learning record

- Register rows: `F9`, `F12`, `PA-08`, `PA-09`, `PA-10`, `PA-12`, `PA-17`, `SQ-07`, `R5-F`, `R6`, `G1`, `OQ-09`
- Adopted clauses: `§10`, `§11`, `§12`, `LE-01`, `LE-02`, `LE-03`
- Freeze status: `FROZEN`
- Open owner input: `OQ-09`
- Frozen record: `Expectation and Outcome`
- Record keys: `immutable expected payload/hash`, `cutoff`, `actual fill/return/duration/cost facts`, `maturity`, `coverage`, `attribution`, `experiment`, `correction lineage`
- Owning module: `OHM-Trade-Agent-v1/app/opip/learning/evaluation.py`
- Reuse status: `EXTEND_EXISTING_MODULE`
- Frozen invariants:
  - One record per entity, policy, horizon and revision, with the expected payload and its hash immutable and the cutoff preserved (`LE-02`).
  - Populations stay separate: taken, rejected, missed, expired, no-fill and no-trade episodes are retained at deduplicated episode or intent grain, and realized paper outcomes, declared-policy feasible counterfactuals and hindsight measures are measured separately (`LE-01`, `LE-02`).
  - Learning means prospective improvement against a frozen baseline after operating costs with coverage (`LE-03`); learning cannot edit active policy autonomously and cannot write to the trading host's production stores.
  - Review-critical persisted facts and learning consumption dispositions stay `OQ-09`; this contract introduces no second outcome engine.

## SC-11 Paper isolation and research environment

- Register rows: `F8`, `R5-E`
- Adopted clauses: `§9`, `PM-01`, `PM-02`
- Freeze status: `FROZEN`
- Open owner input: `NONE`
- Frozen invariants:
  - Research paper portfolio management runs in a separately identified offline or research environment on the existing engine, deterministic and portfolio-aware, with no funded write and no exchange order authority (`PM-01`).
  - `TARGET_PAPER` remains a later owner-gated cutover that requires the intended admission path, mature forecast evidence, independent protection proof, registered portfolio and signal usefulness, metadata readiness, legacy drain, exact release checks and rollback (`PM-02`).
  - Paper and research cannot generate their own proof, and no circular dependency may be resolved by silently activating the target engine. The `EVIDENCE_SHADOW` profile remains the production posture with `TARGET_PAPER` `BLOCKED`.

## SC-12 Canonical ordering, temporal contract and shared writer

- Register rows: `F6`, `F9`, `F10`, `PA-06`, `PA-18`, `PA-19`, `SQ-02`, `SQ-04`, `SQ-05`, `SQ-06`, `SQ-08`, `R5-C`, `R5-F`, `G3`, `G4`, `OQ-07`, `OQ-10`
- Adopted clauses: `§10`, `§12`, `DC-01`, `DC-02`, `DC-03`
- Freeze status: `FROZEN`
- Open owner input: `OQ-07`, `OQ-10`
- Frozen record: `CanonicalTemporalRecord`
- Record keys: `(history_epoch, local_sequence)`, `source event`, `receipt`, `availability`, `evaluation`, `commit times`, `hidden wall clock`
- Owning module: `OHM-Trade-Agent-v1/app/opip/canonical/writer.py`
- Reuse status: `REUSE_EXISTING_MODULE`
- Frozen invariants:
  - The canonical temporal record preserves `(history_epoch, local_sequence)` order and separates source event, receipt, availability, evaluation and commit times; a timestamp alone is not an idempotency key and replay must not depend on a hidden wall clock (`DC-01`).
  - Every linked decision uses eligible evidence at its own cutoff and consumed watermark, and later revisions produce later linked assessments (`DC-02`).
  - Learning cannot read the live SQLite file over the network; units, precision, rounding, enum mappings, retention, compatibility and additive migration rules are frozen before coding each increment, unknown historical fields stay unknown, and derived views rebuild and reconcile at the same watermark before readers switch (`DC-03`).
  - Exactly one canonical writer owns durable domain records; no new event store, allocator database, outcome engine or position registry is introduced. Gate thresholds and cockpit retention stay `OQ-07` and `OQ-10`.

## SC-13 Delivery sequence, readiness gating and legacy disposition

- Register rows: `PA-20`, `R5-0`, `R7`, `G6`, `OQ-01`, `OQ-03`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-07`, `OQ-08`
- Adopted clauses: `§13`, `§14`, `§16`, `§18`, `§19`
- Freeze status: `OPEN`
- Open owner input: `OQ-01`, `OQ-03`, `OQ-04`, `OQ-05`, `OQ-06`, `OQ-07`, `OQ-08`
- Frozen invariants:
  - Delivery order is declared and acyclic: `R5-A` precedes `R5-B`, `R5-B` precedes `R5-C`, `R5-C` precedes `R5-D`, `R5-D` precedes `R5-E`, and `R5-F` follows `R5-A` and `R5-E`; gates consume increments and never the reverse (`§14`, `§18`).
  - Retirement stays behind a reviewed migration and the legacy paper engine coexistence window is not closed by this increment (`§16`, `PA-20`).
  - This contract is `OPEN`: its structural surface is owner-gated. Threshold values, owners and closure decisions stay `OQ-01` and `OQ-03`-`OQ-08`, and this freeze ratifies none of them.

## Contract to register row correspondence (bidirectional)

Every contract below is declared in this freeze and every `SC-*` identifier used by the register is one of these thirteen. The register's `Contract` field is the forward direction and this table is the inverse direction; both are checked against each other by the conformance tests.

| Contract | Register rows |
| --- | --- |
| SC-01 | F1, R5-A |
| SC-02 | F1, F2, R5-A |
| SC-03 | F1, F2, PA-03, SQ-01, SQ-03, SQ-09, R5-A, G2, OQ-02 |
| SC-04 | F3, F4, F5, F6, PA-02, PA-11, PA-16, SQ-10, R5-B |
| SC-05 | F4, PA-13, PA-14, PA-15 |
| SC-06 | F7, R5-C |
| SC-07 | F7, R5-C |
| SC-08 | F7, F8, F10, PA-01, PA-04, PA-05, R5-D |
| SC-09 | F7, F8, R5-D, R5-E |
| SC-10 | F9, F12, PA-08, PA-09, PA-10, PA-12, PA-17, SQ-07, R5-F, R6, G1, OQ-09 |
| SC-11 | F8, R5-E |
| SC-12 | F6, F9, F10, PA-06, PA-18, PA-19, SQ-02, SQ-04, SQ-05, SQ-06, SQ-08, R5-C, R5-F, G3, G4, OQ-07, OQ-10 |
| SC-13 | PA-20, R5-0, R7, G6, OQ-01, OQ-03, OQ-04, OQ-05, OQ-06, OQ-07, OQ-08 |

Rows are listed in the register's declared row order. No contract is unused and no register row names an undeclared contract.

## Open owner inputs: `OWNER_DECISION_REQUIRED`, no numeric policy ratified

Every open input below is an `OWNER_DECISION_REQUIRED` row of the register; its subject is owner input, and this freeze supplies no default for it.

| Open input | Gate | Structural contracts | Subject |
| --- | --- | --- | --- |
| OQ-01 | G6 | SC-13 | Owner assignment for every R5 requirement and gate; tracker owners remain `UNASSIGNED` |
| OQ-02 | G2 | SC-03 | Instrument identity, corporate-action handling and duplicate-source census disposition |
| OQ-03 | G3 | SC-13 | Horizon boundaries and expected holding ranges for each horizon class |
| OQ-04 | G2 | SC-13 | Market Eye workload, freshness, queue and latency budgets |
| OQ-05 | G3 | SC-13 | Rotation advantage, cooldown, requalification and turnover budget |
| OQ-06 | G3 | SC-13 | Sleeve caps and hard versus soft constraint behaviour |
| OQ-07 | G6 | SC-12, SC-13 | Readiness gate thresholds for section 18 and the section 12 metric definitions |
| OQ-08 | G6 | SC-13 | R4 closure status and the legacy paper engine coexistence window |
| OQ-09 | G3 | SC-10 | Review-critical persisted registry facts and learning consumption dispositions |
| OQ-10 | G4 | SC-12 | Operator usefulness questionnaire and legacy outcomes cockpit retention |

## Authority and safeguard statements

- This freeze grants and conveys zero trading authority, zero funded or exchange order authority, zero deployment authority and zero release authority. No `SC-*` contract widens a risk gate, a veto, a sizing rule, a minimum reward-to-risk requirement or an execution safeguard.
- No row of this freeze carries `LIVE` authority and the freeze declares no funded or exchange authority; consistent with the register, which records no `LIVE` row.
- Deterministic risk rejection, execution-safety gates and fail-closed admission are unaffected by this document; external AI remains advisory evidence and cannot override them.
- The production posture is unchanged: the `EVIDENCE_SHADOW` release profile remains the activated profile, `TARGET_PAPER` remains `BLOCKED`, and the Compose core services keep the literal `OPIP_PAPER_V2_MODE=off`.
- Paper and research surfaces stay isolated from funded authority, production registries, order paths and production execution credentials.
- This freeze introduces no event store, no allocator or allocation database, no second outcome engine, no second position registry, no second scheduler and no second deployment control plane.

## Validation of this freeze

- Structural conformance is checked by `tests/test_opip_r5_0_contract_conformance.py`, whose tests map to `AC-001`-`AC-012` in `docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md`.
- The checks include: every declared register row and field count; clause citations resolving to pinned sources; verbatim inherited clause titles; closed status and authority vocabularies with existing evidence paths; dependency resolution and acyclicity; one owning module per logical record; record keys occurring verbatim in the adopted clauses; open numeric policy recorded as owner input; no mapped authority path; scope-contract self-consistency; and the release-profile safeguards above.
- This freeze states no production root cause and claims no production completion. It is a documentation artifact for the `R5-0` conformance slice and changes no runtime behaviour.



