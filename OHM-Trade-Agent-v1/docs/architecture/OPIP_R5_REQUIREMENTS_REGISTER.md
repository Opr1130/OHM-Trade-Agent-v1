# O'Pip R5 requirements register (R5-0 conformance and contract freeze)

Status: `R5-0 DETERMINABLE SLICE COMPLETE`
Increment: `ATDD-R5-0-contract-conformance`
Authority granted by this document: `NONE`

This register is the request-by-request conformance audit of the adopted O'Pip architecture v1.5.0 (`Continuous Multi-Horizon Capital Intelligence`, 9 October 2026). It records, for every adopted requirement, its source clause, current implementation status with a repository evidence path, the authority it carries, its dependencies, the structural contract that governs it, the open owner input that remains, and the readiness gate it feeds. It is a tracking and traceability artifact: it changes no runtime, grants no trading authority, approves no delivery increment and ratifies no numeric policy.

## Adopted authority and preserved history

- Adopted authority: `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx`, SHA256 `69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`, extraction `docs/architecture/v1.5.0/ARCHITECTURE.md`, identity pin `docs/architecture/v1.5.0/SOURCE.md`. Where the extraction and the DOCX disagree, the DOCX controls.
- Inherited clause registers: `docs/architecture/v1.4.4/ARCHITECTURE.md` Appendix A (`PA-01`–`PA-20`) and Appendix B (`SQ-01`–`SQ-10`), which v1.5.0 section 13 declares remain applicable.
- Retained prior authorities: `docs/architecture/v1.4.3/ARCHITECTURE.md` and `docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx`, unchanged and not superseded in history.
- Owner planning input: `docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md` and `.csv`, owners `UNASSIGNED`, non-architecture dimensions `NOT_ASSESSED`, no authority conveyed.
- Repository-controlled production posture cited read-only: `app/services/release_profiles.py` with `docker-compose.yml` under the `EVIDENCE_SHADOW` profile; `TARGET_PAPER` remains `BLOCKED`.

## Row format (thirteen fields, fixed order)

Every conformance row uses exactly these thirteen fields, in this order:

- `ID` — stable requirement identifier. Feature rows `F1`–`F12`; inherited clauses `PA-01`–`PA-20` and `SQ-01`–`SQ-10`; contract and delivery rows `R5-0`, `R5-A`–`R5-F`, `R6`, `R7`; readiness gates `G1`–`G6`; open inputs `OQ-01`–`OQ-10`.
- `Requirement` — the adopted requirement in the clause's own terms.
- `Source` — adopted source family: `v1.5.0`, `v1.4.4`, or `v1.5.0+v1.4.4` for inherited clauses.
- `Clause` — the decisive clause: a v1.5.0 section number `§N`, an inherited clause identifier, or both.
- `Status` — one value from the closed status vocabulary below.
- `Evidence` — one repository path, several separated by `;`, or `NONE`. Every non-`NONE` path must exist in the repository.
- `Authority` — one value from the closed authority vocabulary below.
- `Depends on` — declared requirement identifiers separated by `,`, or `NONE`. Every identifier must be a declared row.
- `Contract` — structural contract identifiers `SC-01`–`SC-13` separated by `,`, or `NONE`. Every identifier must be defined in `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`.
- `Open input` — open-input identifiers `OQ-01`–`OQ-10` separated by `,`, or `NONE`. Every identifier must be a declared `OQ` row.
- `Owner` — `UNASSIGNED` until the OWNER names one; this register never invents an owner. Open-input rows `OQ-*` carry `OWNER` because those decisions are reserved to the human operator.
- `Gate` — readiness gate identifiers `G1`–`G6` separated by `,`, or `NONE`.
- `Note` — one short clarifying statement.

## Status vocabulary (closed)

- `IMPLEMENTED_BASELINE` — the capability exists before R5 and its evidence path exists. This is not an R5 completion claim and not a production proof.
- `PARTIAL` — part of the adopted requirement is present; the rest is unimplemented or unproven.
- `NOT_IMPLEMENTED` — no implementation present.
- `PROPOSED_NOT_FROZEN` — the adopted body defines it; no implementation or frozen value exists.
- `OWNER_DECISION_REQUIRED` — cannot be settled without an explicit OWNER decision.
- `RETIRED` — retired under an existing retirement or migration contract.
- `OUT_OF_SCOPE` — outside this increment and not assessed here.
- `IMPLEMENTED_VERIFIED` — implemented and covered by a named existing acceptance test. This register uses it only when such a test exists in the repository.

## Authority vocabulary (closed)

- `NONE` — no authority; documentation or research only.
- `SHADOW` — shadow or advisory evidence only; no admission or execution effect.
- `PAPER_RESEARCH` — isolated research paper simulation only; not `TARGET_PAPER` proof.
- `TARGET_PAPER_GATED` — reachable only through an owner-gated `TARGET_PAPER` cutover that remains `BLOCKED`.
- `LIVE` — funded or exchange authority. This register records no `LIVE` row: the adopted body grants none and this increment cannot create one.

## Validation status of this register

- Structural validation is performed by `tests/test_opip_r5_0_contract_conformance.py` under the increment `ATDD-R5-0-contract-conformance`.
- Runtime, trading, deployment and release-profile behaviour is unchanged by this register; the release-profile safeguards are re-asserted by the same test module.
- Where the adopted body leaves a value open, the row records `OWNER_DECISION_REQUIRED` and an `OQ` identifier instead of a numeric or named default.
- Inherited clause titles are carried verbatim, never invented or paraphrased: `PA-01`–`PA-20` from v1.4.4 Appendix A and `SQ-01`–`SQ-10` from v1.4.4 Appendix B. The conformance test proves that every inherited row's `Requirement` equals the appendix clause title for its identifier, so a substituted title fails this increment.

## Feature rows F1–F12 (adopted section 15 change map)

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| F1 | Observation: continuous coverage, material event triggers and retained availability evidence | v1.5.0 | §15;§3 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/market/observations.py` | SHADOW | NONE | SC-01,SC-02,SC-03 | OQ-04 | UNASSIGNED | G2 | EXTEND; disposition is architectural, not current production status |
| F2 | Feature Bus: incremental attention inputs, reproducible scheduling, preserved budgets and snapshots | v1.5.0 | §15;§3 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/features/engine.py` | SHADOW | NONE | SC-02,SC-03 | OQ-04 | UNASSIGNED | G2 | EXTEND within the existing owner |
| F3 | Detector: retain pure IGNITION evaluation; gate unsupported horizon research separately | v1.5.0 | §15;§4 | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/detectors/ignition.py` | SHADOW | NONE | SC-04 | NONE | UNASSIGNED | G1 | KEEP; one detector family, no second detector introduced |
| F4 | Lifecycle: horizon thesis, validity, time invalidation and one episode lineage | v1.5.0 | §15;§5 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/contracts/opportunity.py` | NONE | NONE | SC-04,SC-05 | OQ-03 | UNASSIGNED | G4 | EXTEND; logical states map onto existing F4 enums, not a parallel machine |
| F5 | Feasibility: horizon and notional aware evidence, liquidity and capacity; existing veto authority unchanged | v1.5.0 | §15;§5 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/contracts/feasibility.py` | NONE | NONE | SC-04 | NONE | UNASSIGNED | G3 | EXTEND; deterministic veto authority is preserved, not widened |
| F6 | Forecast: calibrated horizon and time outcomes with uncertainty; abstain without evidence | v1.5.0 | §15;§4 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/contracts/forecast.py` | SHADOW | NONE | SC-04,SC-12 | OQ-03 | UNASSIGNED | G3 | EXTEND; F6 alone owns calibrated statistical forecasts |
| F7 | Selector: capital aware portfolio plan, cash comparison, reservation and rotation | v1.5.0 | §15;§7;§8 | PARTIAL | `OHM-Trade-Agent-v1/app/services/capital_efficiency_ranking.py` | PAPER_RESEARCH | NONE | SC-06,SC-07,SC-08,SC-09 | OQ-05 | UNASSIGNED | G3 | EXTEND; fixed risk sizing and existing vetoes remain binding |
| F8 | Paper execution: deterministic portfolio management using the existing isolated engine | v1.5.0 | §15;§9 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py` | PAPER_RESEARCH | NONE | SC-08,SC-09,SC-11 | OQ-08 | UNASSIGNED | G5 | EXTEND; paper isolation and no funded write are preserved |
| F9 | Outcomes and learning: canonical expectations and labels, capital-time and horizon evaluation | v1.5.0 | §15;§11;§12 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/learning/evaluation.py` | SHADOW | NONE | SC-10,SC-12 | OQ-09 | UNASSIGNED | G3 | EXTEND; learning cannot edit active policy autonomously |
| F10 | Dashboard: same eight pages and semantic model; expose plans, capital and horizons | v1.5.0 | §15;§13 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/cockpit/portfolio.py` | NONE | NONE | SC-08,SC-12 | OQ-10 | UNASSIGNED | G4 | EXTEND; read-only facts only, no favorable state from missing evidence |
| F11 | Safety: independent protection, fail-closed admission and human resumption, re-proven under new load | v1.5.0 | §15;§5 | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/risk/policy.py` | NONE | NONE | NONE | NONE | UNASSIGNED | G5 | KEEP; protection precedes profit seeking and cannot starve |
| F12 | AI research: advisory-only Committee, governed registry, quantitative trust and cost evidence | v1.5.0 | §15;§11 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/committee/contracts.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G6 | KEEP; AI stays advisory and cannot override deterministic risk rejection |

## Inherited clause rows PA-01–PA-20 (v1.4.4 Appendix A, preserved by section 13)

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| PA-01 | Suppress nonactionable trade alerts | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/decision/gates.py` | NONE | NONE | SC-08 | NONE | UNASSIGNED | G3 | INHERITED; identifier, title and meaning preserved |
| PA-02 | Require fresh qualification | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/early/point_in_time.py` | NONE | NONE | SC-04 | NONE | UNASSIGNED | G2 | INHERITED; identifier, title and meaning preserved |
| PA-03 | Preserve timing and validity | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/early/timing_ledger.py` | NONE | NONE | SC-03 | NONE | UNASSIGNED | G2 | INHERITED; identifier, title and meaning preserved |
| PA-04 | Include cash and avoid forced selection | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/services/capital_efficiency_ranking.py` | PAPER_RESEARCH | NONE | SC-08 | NONE | UNASSIGNED | G3 | INHERITED; identifier, title and meaning preserved |
| PA-05 | Select constrained economic value | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/decision/screening.py` | PAPER_RESEARCH | NONE | SC-08 | NONE | UNASSIGNED | G3 | INHERITED; identifier, title and meaning preserved |
| PA-06 | Require traceable qualification evidence | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/decision/evidence.py` | NONE | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; identifier, title and meaning preserved |
| PA-07 | Keep AI advisory | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/committee/contracts.py` | SHADOW | NONE | NONE | NONE | UNASSIGNED | G6 | INHERITED; AI cannot override deterministic risk rejection |
| PA-08 | Expose AI provenance and completeness | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/committee/attribution.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G6 | INHERITED; identifier, title and meaning preserved |
| PA-09 | Freeze expectations before outcomes | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/contracts/paper_outcome.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G3 | INHERITED; expectation schemas precede the first evaluated decision |
| PA-10 | Append actual outcomes safely | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/learning/paper_outcome_reader.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G1 | INHERITED; append-only, no reconstruction as prospective fact |
| PA-11 | Enforce causal confidence categories | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/committee/weakness.py` | SHADOW | NONE | SC-04 | NONE | UNASSIGNED | G6 | INHERITED; identifier, title and meaning preserved |
| PA-12 | Retain the full opportunity population | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/discovery/outcomes.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G1 | INHERITED; accepted and rejected rows both retained |
| PA-13 | Protect independently of discovery | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/risk/observer.py` | NONE | NONE | SC-05 | NONE | UNASSIGNED | G5 | INHERITED; protection is independent and cannot be starved |
| PA-14 | Prioritize existing positions | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/risk/relevance.py` | NONE | NONE | SC-05 | NONE | UNASSIGNED | G5 | INHERITED; identifier, title and meaning preserved |
| PA-15 | Preserve single ownership | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/canonical/writer.py` | NONE | NONE | SC-05 | NONE | UNASSIGNED | G1 | INHERITED; one writer per logical fact, no second source of truth |
| PA-16 | Record external evidence eligibility | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/events/adapters.py` | NONE | NONE | SC-04 | NONE | UNASSIGNED | G4 | INHERITED; identifier, title and meaning preserved |
| PA-17 | Reject hindsight contamination | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/ml/temporal.py` | NONE | NONE | SC-10 | NONE | UNASSIGNED | G1 | INHERITED; point-in-time separation preserved |
| PA-18 | Reconcile read only dashboard facts | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/cockpit/trust.py` | NONE | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; the dashboard reconciles and never originates facts |
| PA-19 | Expose a complete decision trace | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/cockpit/ledger.py` | NONE | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; identifier, title and meaning preserved |
| PA-20 | Block Paper v2 without usefulness | v1.5.0+v1.4.4 | §13;v1.4.4 A | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/services/release_profiles.py` | TARGET_PAPER_GATED | NONE | SC-13 | OQ-07 | UNASSIGNED | G6 | INHERITED; `TARGET_PAPER` remains blocked pending owner cutover |

## Inherited clause rows SQ-01–SQ-10 (v1.4.4 Appendix B, preserved by section 13)

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SQ-01 | Discovery before move | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/discovery/earliness.py` | SHADOW | NONE | SC-03 | NONE | UNASSIGNED | G2 | INHERITED; discovery is compared with the first eligible observed onset, never a hindsight low |
| SQ-02 | Validity and lateness | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/early/timing_ledger.py` | SHADOW | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; validity and lateness are reported separately, never collapsed into one latency |
| SQ-03 | Confirmation delay | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/streaming/sequencing.py` | SHADOW | NONE | SC-03 | NONE | UNASSIGNED | G1 | INHERITED; late output cannot renew actionability or rewrite the committed trigger |
| SQ-04 | Post alert excursions | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/discovery/outcomes.py` | SHADOW | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; MFE and MAE are hindsight diagnostics, not executable returns |
| SQ-05 | Target and exit outcomes | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/contracts/paper_outcome.py` | SHADOW | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; a no-fill is not a losing fill and an ambiguous bar is not a win |
| SQ-06 | Net economics and occupancy | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/learning/evaluation.py` | SHADOW | NONE | SC-12 | OQ-07 | UNASSIGNED | G3 | INHERITED; costs are deducted once and the occupancy basis stays an owner decision |
| SQ-07 | Rank and alternatives | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/decision/screening.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G3 | INHERITED; the ex ante rank is never rewritten by later declared-policy outcomes |
| SQ-08 | Missed opportunity | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/discovery/outcomes.py` | NONE | NONE | SC-12 | NONE | UNASSIGNED | G4 | INHERITED; opportunity cost needs a preregistered feasible reference policy |
| SQ-09 | False positives and no trade | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/decision/gates.py` | SHADOW | NONE | SC-03 | NONE | UNASSIGNED | G2 | INHERITED; a zero-alert day is not automatic success and lateness stays separate |
| SQ-10 | Prospective usefulness gate | v1.5.0+v1.4.4 | §13;v1.4.4 B | IMPLEMENTED_BASELINE | `OHM-Trade-Agent-v1/app/opip/learning/readiness.py` | SHADOW | NONE | SC-04 | OQ-07 | UNASSIGNED | G3 | INHERITED; missing thresholds, insufficient sample or failed guardrails cannot pass |

## Contract and delivery rows R5-0, R5-A–R5-F, R6, R7 (adopted sections 9–16)

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R5-0 | Architecture and ATDD contract: owners, numeric policy inputs, source adoption and bounded editable scope | v1.5.0 | §16 | PARTIAL | `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md`;`OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-0-architecture-source-adoption.md` | NONE | NONE | SC-13 | OQ-01,OQ-02 | UNASSIGNED | G1 | NEW; this increment delivers the determinable slice only |
| R5-A | Continuous Market Eye: new attention responsibility inside F1, F2 and F4; prove latency, coverage, capacity and zero authority | v1.5.0 | §16;§3 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/events/contract.py` | SHADOW | R5-0 | SC-01,SC-02,SC-03 | OQ-04 | UNASSIGNED | G2 | NEW; attention may change evaluation priority, never authority |
| R5-B | Horizon classifier and thesis contract attached to F4; prove immutable timing, class uncertainty and no accidental strategy proliferation | v1.5.0 | §16;§4 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py` | NONE | R5-A | SC-04 | OQ-03 | UNASSIGNED | G3 | NEW; horizon classes bound strategy variants |
| R5-C | Reconciled capital and capital-time view inside existing F7 and F8 ownership; prove disjoint balances and atomic consumption | v1.5.0 | §16;§7 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/services/capital_rotation_intelligence.py` | NONE | R5-B | SC-06,SC-07,SC-12 | OQ-06 | UNASSIGNED | G3 | NEW; reuse existing ownership, add no allocator database |
| R5-D | Portfolio plan and rotation contract inside F7 and F8; prove switching advantage, actual proceeds and requalification | v1.5.0 | §16;§8 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py` | PAPER_RESEARCH | R5-C | SC-08,SC-09 | OQ-05 | UNASSIGNED | G3 | NEW; rotation is a portfolio decision, not an alert |
| R5-E | Deterministic portfolio paper management with single canonical writer, bounded retries and fail-closed isolation | v1.5.0 | §9;§10 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/contracts/paper_execution.py` | PAPER_RESEARCH | R5-D | SC-09,SC-11 | OQ-08 | UNASSIGNED | G5 | NEW; paper only, never funded authority |
| R5-F | Learning: canonical expectations, capital-time and horizon evaluation, repair lineage and named dispositions | v1.5.0 | §11;§12 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/learning/readiness.py` | SHADOW | R5-A,R5-E | SC-10,SC-12 | OQ-09 | UNASSIGNED | G6 | NEW; learning stays non-authoritative |
| R6 | Committee shadow review: complete persona evidence, quantitative trust, bounded calls and retained cost | v1.5.0 | §11 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/committee/contracts.py` | SHADOW | NONE | SC-10 | NONE | UNASSIGNED | G6 | retained; AI remains advisory and cannot override risk rejection |
| R7 | Governed retirement of legacy engines and duplicate discovery after cutover under the retirement ledger | v1.5.0 | §16;§13 | PARTIAL | `OHM-Trade-Agent-v1/docs/architecture/OPIP_RETIREMENT_LEDGER.md` | NONE | R5-E,R5-F | SC-13 | OQ-08,OQ-10 | UNASSIGNED | G6 | retained; no retirement without a reviewed migration |

## Readiness gate rows G1–G6 (adopted section 18 gates and section 19 owner control)

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| G1 | Discovery and truth gate: complete retained population with frozen expectations before outcomes | v1.5.0 | §18;§11 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/discovery/outcomes.py` | NONE | R5-A,R5-F | SC-10 | OQ-07 | UNASSIGNED | NONE | gate thresholds remain an owner decision |
| G2 | Coverage and continuity gate: continuous market coverage with freshness and timeliness evidence | v1.5.0 | §18;§3 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/streaming/healthcheck.py` | NONE | R5-A | SC-03 | OQ-04 | UNASSIGNED | NONE | latency, queue and freshness thresholds are open |
| G3 | Horizon and capital gate: horizon quality, capital efficiency and rotation evidence | v1.5.0 | §18;§12 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/learning/readiness.py` | NONE | R5-C,R5-D | SC-12 | OQ-06 | UNASSIGNED | NONE | sleeve and rotation numbers are open |
| G4 | Operator usefulness and trace gate: reconciled read-only facts and complete decision trace | v1.5.0 | §18;§13 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/opip/cockpit/ledger.py` | NONE | R5-B,R5-D | SC-12 | OQ-10 | UNASSIGNED | NONE | usefulness evidence is an operator judgement |
| G5 | Safety and isolation gate: independent protection, fail-closed admission and paper isolation under new load | v1.5.0 | §18;§5 | PARTIAL | `OHM-Trade-Agent-v1/app/opip/risk/policy.py` | NONE | R5-E | NONE | NONE | UNASSIGNED | NONE | existing controls preserved and re-proven under new load |
| G6 | Governance and cutover gate: owner authorization, retirement ledger and release-profile review before TARGET_PAPER | v1.5.0 | §18;§19 | PROPOSED_NOT_FROZEN | `OHM-Trade-Agent-v1/app/services/release_profiles.py` | TARGET_PAPER_GATED | R5-E,R5-F | SC-13 | OQ-01 | UNASSIGNED | NONE | `TARGET_PAPER` remains BLOCKED until owner cutover |

## Open input rows OQ-01–OQ-10 (owner decisions this increment cannot take)

Rows in this table carry `Owner` = `OWNER`: each decision is reserved to the human operator, and every other row's `Owner` field stays `UNASSIGNED` until the operator names one.

| ID | Requirement | Source | Clause | Status | Evidence | Authority | Depends on | Contract | Open input | Owner | Gate | Note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OQ-01 | Owner assignment for every R5 requirement and gate; tracker owners remain UNASSIGNED | v1.5.0 | §19;§15 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/docs/architecture/v1.5.0/OPIP_Feature_Priorities_v1_5_0.md` | NONE | NONE | SC-13 | NONE | OWNER | G6 | naming an owner is an owner act, not an agent act |
| OQ-02 | Instrument identity, corporate-action handling and duplicate-source census disposition | v1.5.0 | §3;§13 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/events/identity.py` | NONE | NONE | SC-03 | NONE | OWNER | G2 | registry facts requiring owner review |
| OQ-03 | Horizon boundaries and expected holding ranges for each horizon class | v1.5.0 | §4 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/contracts/temporal.py` | NONE | NONE | SC-13 | NONE | OWNER | G3 | durations are numbers: owner decision only |
| OQ-04 | Market Eye workload, freshness, queue and latency budgets | v1.5.0 | ME-05;ME-06 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/streaming/healthcheck.py` | NONE | NONE | SC-13 | NONE | OWNER | G2 | capacity and latency are owner policy, not agent defaults |
| OQ-05 | Rotation advantage, cooldown, requalification and turnover budget | v1.5.0 | RO-02 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py` | NONE | NONE | SC-13 | NONE | OWNER | G3 | no advantage or cooldown value may be invented |
| OQ-06 | Sleeve caps and hard versus soft constraint behaviour | v1.5.0 | PP-03 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/contracts/portfolio.py` | NONE | NONE | SC-13 | NONE | OWNER | G3 | cap levels are owner policy |
| OQ-07 | Readiness gate thresholds for sections 18 and the section 12 metric definitions | v1.5.0 | §18;§12 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md` | NONE | NONE | SC-12,SC-13 | NONE | OWNER | G6 | thresholds are ratified by the owner only |
| OQ-08 | R4 closure status and the legacy paper engine coexistence window | v1.5.0 | §16 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/docs/architecture/OPIP_RETIREMENT_LEDGER.md` | NONE | NONE | SC-13 | NONE | OWNER | G6 | no legacy retirement without a reviewed migration |
| OQ-09 | Review-critical persisted registry facts and learning consumption dispositions | v1.5.0 | §11 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/learning/job_disposition.py` | NONE | NONE | SC-10 | NONE | OWNER | G3 | every persisted artifact needs a governed disposition |
| OQ-10 | Operator usefulness questionnaire and legacy outcomes cockpit retention | v1.5.0 | §16;§13 | OWNER_DECISION_REQUIRED | `OHM-Trade-Agent-v1/app/opip/cockpit/ledger.py` | NONE | NONE | SC-12 | NONE | OWNER | G4 | usefulness is an operator judgement |

## Dependency graph (declared edges only)

`DECLARED_ORDER: R5-0, R5-A, R5-B, R5-C, R5-D, R5-E, R5-F`

- Edges are exactly those in the `Depends on` fields: `R5-A`←`R5-0`; `R5-B`←`R5-A`; `R5-C`←`R5-B`; `R5-D`←`R5-C`; `R5-E`←`R5-D`; `R5-F`←`R5-A`,`R5-E`; `R7`←`R5-E`,`R5-F`.
- Gates consume increments, not the reverse: `G1`←`R5-A`,`R5-F`; `G2`←`R5-A`; `G3`←`R5-C`,`R5-D`; `G4`←`R5-B`,`R5-D`; `G5`←`R5-E`; `G6`←`R5-E`,`R5-F`.
- No increment depends on a later increment in `DECLARED_ORDER`, and the graph is acyclic. This satisfies the adopted requirement that collection (`R5-A`) precedes learning (`R5-F`) even though learning is the final delivery increment.
- Feature rows `F1`–`F12`, inherited clause rows `PA-*` and `SQ-*`, and open-input rows `OQ-*` declare no inter-row dependencies; they are predicates on the contract, not stages of it.

## Reuse and gap verdicts (existing platform only)

- `CONFORMANT_BASELINE` — capability present before R5 with an existing module: `F3`, `F11`, all `PA-01`–`PA-20`, all `SQ-01`–`SQ-10`. Baseline presence is not R5 completion and not production proof.
- `EXTEND_REQUIRED` — the adopted body adds requirements inside an existing owner: `F1`, `F2`, `F4`, `F5`, `F6`, `F7`, `F8`, `F9`, `F10`, `F12`.
- `NEW_INCREMENT_REQUIRED` — no implementation exists; each is a future increment: `R5-A`, `R5-B`, `R5-C`, `R5-D`, `R5-E`, `R5-F`.
- `OWNER_DECISION_REQUIRED` — `OQ-01`–`OQ-10`, plus every numeric field left open inside `SC-13`.
- `RETIREMENT_REQUIRED` — none in this increment; `R7` keeps retirement gated behind a reviewed migration.
- Mandatory reuse, restated from the frozen boundaries: attention reuses `app/opip/events/*` and `app/opip/streaming/*`; scheduling reuses `app/opip/features/*` and `app/opip/streaming/worker.py`; horizon work extends `app/opip/contracts/{opportunity,temporal,forecast,feasibility}.py`; capital evidence reuses `app/services/capital_rotation_intelligence.py`, `app/services/capital_efficiency_ranking.py`, `app/services/full_market_observation.py` and `app/opip/risk/*`; portfolio and rotation extend `app/opip/contracts/{portfolio,paper_execution}.py` under the single writer `app/opip/canonical/writer.py`; learning reuses `app/opip/learning/*`, `app/opip/discovery/*` and `app/opip/ml/*`; presentation reuses `app/opip/cockpit/*`; AI research reuses `app/opip/committee/*`.
- Forbidden by the frozen boundaries: a new event store, an allocator or allocation database, a second outcome engine, a second position registry, a second scheduler, a second deployment control plane, and any funded or exchange authority.

## Validation of this register

- Structural conformance is checked by `tests/test_opip_r5_0_contract_conformance.py`, whose tests map to `AC-001`–`AC-012` in `docs/atdd/scope-contracts/ATDD-R5-0-contract-conformance.md`.
- Contract structure is defined in `docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md`; every `SC-*` identifier used here is declared there and vice versa.
- Register integrity rules: exactly thirteen fields per row; status and authority values from the closed vocabularies; every evidence path exists in the repository; no `LIVE` authority row; every dependency, contract, open-input and gate identifier resolves to a declared row.
- This register states no production root cause, claims no production completion, and conveys no trading, deployment or release authority.
