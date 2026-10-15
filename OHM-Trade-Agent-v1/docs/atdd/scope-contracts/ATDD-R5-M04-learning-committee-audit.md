INCREMENT:
ATDD-R5-M04-learning-committee-audit

OWNER-APPROVED INTENT:
This is the audit increment of the owner-directed Mission 04 learning-and-Committee work, inside frozen O'Pip v1.5.0. It answers one question with evidence and implements nothing: of the items Mission 04 asks to prove across expected-versus-actual learning, learning governance, the AI Committee, AI economics and read-only operator evidence, which does the repository already prove, which named test proves it, and which are genuinely open?

Why an audit comes first. The repository already carries a very large body of relevant evidence: 190 tests across six committee suites alone, 30 of them safety assertions; 26 latency assertions; a 41-test pre-trade suite; the R5-F prospective-outcome contract with nine approved criteria; the R6 shadow-review-readiness contract with eight; and a learning suite that proves the entire governed chain as one assembled lineage. An increment that re-proved any of that would be manufacturing work, and one that assumed coverage without citing it would be asserting without evidence. The audit resolves both.

What changes where. No application module changes. This increment adds one evidence document, one acceptance-test module and the records that accompany them. It adds no producer, registry, store, scheduler, publication boundary, route, schema, configuration key or authority, and it activates nothing.

The finding that shapes the mission. Fifty-eight rows were assessed: forty-four coverage items across five workstreams and the fourteen mandatory negative tests. Fifty are proven by a named existing test. Four are partial. Three are owner-gated. One is answered by an explicit declared posture rather than by measurement. That means one follower increment closes the four partial rows, and the gated items are recorded as owner decisions rather than as engineering gaps.

The AI-economics determination, which is the sharpest part of this audit. Invocation-attempt telemetry exists and is durable - provider family and reported provider, requested and reported model, per-attempt number, input and output tokens, an estimated cost, a runtime charge, a cost-completeness token, latency, request and response instants and a failure class, all on a content-derived identity so a retry that re-delivers one observation is recorded as a duplicate and never as a second opinion. Matched-policy incremental economic value is proven, including the discipline that an unknown operating cost makes the operating net unknown rather than zero. What is **absent** is a billed cost: the string `billed` appears nowhere in the application package, so "billed versus estimated" cannot be answered from present evidence. What is **declared rather than measured** is prompt-cache usage, which `app/opip/committee/registry.py` refuses to credit (`CACHE_SAVINGS_CREDITED = False`) and accounts at uncached rates. And what is **not canonical** is the whole cost basis, which is written to the committee plane's own bounded JSONL archive rather than through the single canonical writer.

KEY LAW - WHERE THE COST BASIS IS INCOMPLETE, THE ANSWER IS INSUFFICIENT_EVIDENCE. The mission forbids estimating a favourable AI net value, and the read plane already behaves that way: `REASON_NO_CANONICAL_AI_COST` is what the F10 semantic service publishes for the attributable-AI-cost panel. This audit reports the same verdict rather than a number.

KEY LAW - DEVELOPMENT SPEND IS NOT OPERATING SPEND. Cline and DeepSeek engineering-development expense is never treated as a Committee trading-operating cost anywhere in this mission.

KEY LAW - AN AUDIT MAY NOT PROMOTE ITSELF. An item is `PROVEN` only when a named acceptance test exists; a module citation without a test is allowed only for a row that is not claimed as proven.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md: the frozen v1.5.0 architecture, including the learning and Committee responsibilities this audit maps items onto.
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md sections SC-10 and SC-12: the expectation-and-outcome learning record's immutable-payload, population-separation and prospective-only invariants, and the canonical-ordering contract. The freeze is referenced, never restated.
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md: the declared `AI usage` availability row and the declared absence of an AI-cost read model.
- docs/atdd/scope-contracts/ATDD-R5-F-prospective-outcome-integration.md and ATDD-R6-committee-shadow-review-readiness.md: the two delivered contracts whose acceptance tests carry most of Workstreams A, B and C.
- app/opip/learning/evaluation.py, app/opip/committee/learning.py, app/opip/committee/contracts.py, app/opip/committee/registry.py, app/opip/committee/store.py, app/opip/cockpit/semantic.py: the modules the audit cites and whose absence of a billed-cost field it records.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-400:
GIVEN:
the recorded coverage matrix with its declared class, workstream, contract and negative vocabularies and its declared totals
WHEN:
every coverage and negative row is parsed, every field is compared with the vocabularies, and the totals are recomputed
THEN:
every row carries the declared keys and identifier shape, every class, workstream and contract value is one the document declares, every class is used by at least one row, and the item, negative, row, class and workstream counts equal the recomputed values

AC-401:
GIVEN:
the evidence path and named node every row cites
WHEN:
each path is resolved against the repository root and each node looked for in the file it is attributed to
THEN:
every path exists, every named node is present, no row classified PROVEN rests on a module citation alone, and the owner-gated rows are exactly the three the document names

AC-402:
GIVEN:
the declared follower increments and every row that is not fully proven
WHEN:
the closed sets are compared with the partial set
THEN:
every partial row is named by exactly one follower, no follower claims a proven, declared or owner-gated row, and the follower identifiers are exactly the declared ones in order

AC-403:
GIVEN:
the matrix's own prose and the read plane it cites
WHEN:
its refusal section and scope statement are read and the application is inspected
THEN:
the document claims no AI net value in either direction, states that development spend is not conflated with Committee operating expense, states that the Committee has no authority, declares the frozen F6/F9/F12 ownership, states that no producer, registry, store, scheduler or publication boundary was created, and the read plane still withholds attributable AI cost for the declared reason with no AI-cost read model present

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any change to F6 statistical forecast ownership, F9 learning ownership or F12 advisory-only status.
- Any new producer, registry, store, scheduler or publication boundary, and any exposure of committee-local evidence through the F10 semantic service.
- Any Committee activation, timer enablement, provider call, credential use or live endpoint contact.
- Any token price, fee, budget, ceiling, threshold, latency bound or trust-stage tolerance.
- Any estimate of AI net value, and any treatment of engineering-development spend as operating expense.
- Re-proving any row an existing named test already proves.

FROZEN BOUNDARIES:
- `TARGET_PAPER` remains `BLOCKED`; the production posture remains `EVIDENCE_SHADOW`; the Committee ships dark and only shadow is permitted.
- The Committee remains advisory-only with no order, risk, execution, protection or promotion authority, and nothing outside its package imports it.
- Learning cannot edit an active policy autonomously and cannot write to the trading host's production stores.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.
- The single canonical writer remains the only authority that commits operational domain events and projections, and this increment writes none.

ACCEPTANCE TEST TRACEABILITY:
AC-400 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_400_every_row_uses_the_declared_vocabulary
AC-400 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_400_the_declared_totals_recompute_from_the_rows
AC-401 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_401_every_cited_path_and_node_resolves
AC-401 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_401_no_proven_item_rests_on_a_module_citation_alone
AC-402 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_402_every_follower_closes_named_rows_and_no_owner_gated_row
AC-403 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_403_the_matrix_claims_no_ai_value_and_no_authority
AC-403 -> tests/test_opip_r5_m04_coverage_matrix.py::test_ac_403_the_frozen_ownership_and_read_plane_are_declared_unchanged

IMPLEMENTATION MAP:
AC-400 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M04-learning-committee-audit.md
AC-400 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md
AC-400 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_coverage_matrix.py
AC-401 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md
AC-401 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_coverage_matrix.py
AC-402 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md
AC-402 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_coverage_matrix.py
AC-403 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md
AC-403 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_coverage_matrix.py
AC-400 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-400 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The Committee telemetry is richer than the F10 census implies. The census records `AI usage` as `BLOCKED_BY_UPSTREAM_EVIDENCE`, but a durable per-attempt record with provider, model, tokens, estimated cost, charge, completeness, latency and failure class already exists - in the committee plane's own archive rather than on the canonical plane. The gap is therefore an exposure-architecture gap, not a missing-evidence gap, and it is recorded as an owner decision rather than closed here.
- No billed cost exists anywhere in the application package, so "billed versus estimated cost" is `INSUFFICIENT_EVIDENCE`. Supplying it needs a provider-billing producer, which is an owner decision.
- Prompt-cache usage is deliberately not measured and deliberately not credited; cost is accounted at uncached rates. That is a declared conservative posture rather than a measurement, and it is classified `DECLARED_NOT_MEASURED` rather than `PROVEN`.
- Per-attempt cost is carried by the contract for both retries and failed attempts, but no existing test asserts that a retry and a failure each carry their own attributable cost. Both rows are `PARTIAL` and both are closed by the follower increment.
- The audit's own first draft asserted three prose phrases whose line wrapping differed from the document's; the checks now normalise whitespace, so wrapping cannot make an audit claim pass or fail by accident.
- No headless browser harness exists in this repository, and no rendered-surface claim is made.

UNAPPROVED SCOPE CHANGES:
NONE


