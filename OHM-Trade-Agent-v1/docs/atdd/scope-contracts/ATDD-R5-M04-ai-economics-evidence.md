INCREMENT:
ATDD-R5-M04-ai-economics-evidence

OWNER-APPROVED INTENT:
This is the AI-economics increment of the owner-directed Mission 04 work, inside frozen O'Pip v1.5.0. The audit left exactly four partial rows: retry cost, failed-attempt cost, duplicate AI attempt and missing failed-attempt billing. Per-attempt failure telemetry and per-provider failure accounting already existed; what no test asserted was that a retry and a failed attempt each carry their **own** attributable cost, that a re-delivered observation collapses to one identity, and that an unreported cost is never a silent zero.

What changes where. No application module changes. This increment adds one acceptance-test module, one determination document and its contract, closes the audit's four partial rows, and updates the pointer and the build report. It enables no provider beyond the deterministic scripted double, contacts no endpoint, holds no credential, adds no route, producer, registry, store, scheduler, publication boundary, price or threshold.

What it proves, and one correction to what the audit expected. The audit assumed a failed attempt would record the cost its answer declared. It does not: the runtime attributes a cost only when one was **reported**, so a timed-out attempt records **null**, never the pre-flight estimate and never zero. That is the more conservative behaviour, and the tests were written to the true contract rather than to the audit's expectation. The increment then proves: a failover's attributed spend is the answering attempt's cost alone with the failed attempt contributing nothing; the aggregate completeness is UNKNOWN when one attempt's cost was never reported, so an incomplete basis cannot present itself as complete; a failed attempt is recorded with its failure class and a measured latency but no cost; a fully-attributed success carries COMPLETE completeness with its own spend; two identical executions produce an identical attempt record, so a re-delivery collapses rather than becoming a second, independently-priced observation, while a differing execution does not; and an unreported cost is null rather than zero.

The determination this increment records. A complete **canonical** attributable cost basis is **`INSUFFICIENT_EVIDENCE`**. Invocation-attempt telemetry exists and is durable - provider and model identity, per-attempt tokens, estimated cost, a runtime charge, completeness, latency, failure class on a content-derived identity - and matched-policy incremental economic value is proven. But no billed cost exists anywhere in the application package, prompt-cache usage is deliberately not measured and not credited (`CACHE_SAVINGS_CREDITED = False`, cost at uncached rates), and the whole cost basis lives in the committee plane's own bounded JSONL archive rather than through the single canonical writer. Three owner decisions are requested; none is implemented.

KEY LAW - AN UNMEASURED COST IS UNKNOWN, NOT FREE. A null cost, an INCOMPLETE or UNKNOWN completeness token and an aggregate that refuses to read as COMPLETE are the correct outcomes, and they are what the tests demand.

KEY LAW - THE ANSWER IS INSUFFICIENT_EVIDENCE RATHER THAN AN ESTIMATE. The mission forbids estimating a favourable AI net value. The read plane already withholds attributable AI cost with `NO_CANONICAL_AI_COST_EVIDENCE`, and this increment reaches the same verdict rather than a number. Cline and DeepSeek engineering-development spend is never treated as Committee trading-operating expense.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md: the frozen v1.5.0 architecture.
- docs/architecture/OPIP_R5_0_CONTRACT_FREEZE.md sections SC-10 and SC-12: the immutable-payload and prospective-only learning invariants and the canonical-ordering contract. The freeze is referenced, never restated.
- docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md: the audit whose four partial rows this increment closes.
- app/opip/committee/contracts.py, app/opip/committee/registry.py, app/opip/committee/store.py, app/opip/committee/role_router.py, app/opip/committee/role_execution.py, app/opip/committee/fakes.py: the contracts and the deterministic scripted provider this increment drives.
- app/opip/cockpit/semantic.py and semantic_pages.py: the read plane that already withholds attributable AI cost with the declared reason.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-410:
GIVEN:
a failover route whose primary times out and whose fallback answers with its own reported cost
WHEN:
the role is executed
THEN:
the two attempts are numbered in order, the timed-out attempt records no cost because it reported none, the answering attempt records its own reported cost, the attributed spend equals the answering attempt's cost alone so nothing is shared or double-counted, the aggregate completeness is UNKNOWN because one attempt's cost was never reported, and the result belongs to the attempt that answered

AC-411:
GIVEN:
a route whose provider times out, and a route whose provider is unavailable so nothing is invoked
WHEN:
each role is executed
THEN:
the failure is recorded as FAILED with a named failure class, an attempt number and a measured latency but no cost, the uninvoked attempt records neither cost nor latency, and neither cost reads as zero

AC-412:
GIVEN:
the same logical observation executed twice under identical conditions and then once more under a differing condition
WHEN:
the recorded attempts are compared
THEN:
the two identical executions produce an equal attempt record, so a re-delivery collapses rather than doubling as evidence, and the differing execution is not equal, so a changed cost or latency cannot hide behind the same record

AC-413:
GIVEN:
a failed attempt whose call reports no cost at all, and a successful execution
WHEN:
each recorded attempt and execution is read
THEN:
the failed attempt's cost is null rather than zero and is not replaced by the declaration it carried, and the successful execution carries its own attributed spend with COMPLETE completeness, so the completeness vocabulary is real rather than decorative

AC-414:
GIVEN:
the recorded AI-economics determination
WHEN:
its verdict and its requested decisions are read
THEN:
a complete canonical attributable cost basis is reported as INSUFFICIENT_EVIDENCE, the declared cache posture and the absent billed cost are both named, the read plane's withholding reason is cited, every requested decision names a producer, a consumer, a schema, a failure mode, a security implication and a migration implication, and the document states that it implements nothing

EXPLICITLY OUT OF SCOPE:
- Any change to an application module, contract, route, producer, schema, store, writer, job, cron unit, configuration key or release profile.
- Any new producer, registry, store, scheduler or publication boundary, and any exposure of committee-local evidence through the F10 semantic service.
- Any real provider call, credential, endpoint, timer, activation or funded activity.
- Any token price, fee, budget, ceiling, threshold or latency bound.
- Any estimate of AI net value, and any treatment of development spend as operating expense.
- Any change to F6, F9 or F12 ownership, or to the Committee's advisory-only status.

FROZEN BOUNDARIES:
- The Committee ships dark, only shadow is permitted, and it holds no order, risk, execution, protection or promotion authority.
- `TARGET_PAPER` remains `BLOCKED`; the production posture remains `EVIDENCE_SHADOW`.
- The single canonical writer remains the only authority that commits operational domain events and projections, and this increment writes none.
- Learning cannot edit an active policy autonomously and cannot write to the trading host's production stores.
- Fixed risk sizing and the minimum reward-to-risk defaults remain binding and unmodified.

ACCEPTANCE TEST TRACEABILITY:
AC-410 -> tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_410_a_retry_carries_its_own_attributable_cost
AC-411 -> tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_411_a_failed_attempt_retains_its_cost_and_its_completeness
AC-412 -> tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_412_a_duplicate_ai_attempt_collapses_to_one_identity
AC-413 -> tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_413_a_missing_failed_attempt_billing_is_incomplete_never_zero
AC-414 -> tests/test_opip_r5_m04_ai_economics_evidence.py::test_ac_414_the_determination_records_the_verdict_and_its_implications

IMPLEMENTATION MAP:
AC-410 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-M04-ai-economics-evidence.md
AC-410 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py
AC-411 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py
AC-412 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py
AC-413 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py
AC-414 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_ai_economics_evidence.py
AC-414 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_AI_ECONOMICS_DETERMINATION.md
AC-410 -> OHM-Trade-Agent-v1/docs/atdd/evidence/R5_M04_LEARNING_COMMITTEE_MATRIX.md
AC-410 -> OHM-Trade-Agent-v1/tests/test_opip_r5_m04_coverage_matrix.py
AC-410 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-410 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md

DEFERRED DISCOVERIES:
- The audit expected a failed attempt to record the cost its answer declared. It does not: the runtime attributes a cost only when one was reported, so a timed-out attempt records null. That is more conservative than the audit assumed, and it is why the four rows are proven against the true contract rather than the assumed one.
- The router does not attach the recorded call outcome to a role result; the call outcome is assembled later. Per-attempt cost is therefore read from the attempt record and the attributed spend from the execution, and the tests assert both rather than assuming a field that is not populated at this seam.
- The three owner decisions in the determination - canonicalise invocation evidence, capture a billed cost, measure prompt-cache usage - are recorded and unimplemented. Until they are decided, `INSUFFICIENT_EVIDENCE` is the correct verdict and no estimate is offered.
- No headless browser harness exists in this repository, and no rendered-surface claim is made.

UNAPPROVED SCOPE CHANGES:
NONE


