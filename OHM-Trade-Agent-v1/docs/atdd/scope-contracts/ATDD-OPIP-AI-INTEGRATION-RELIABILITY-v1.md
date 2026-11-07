INCREMENT:
ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1

OWNER-APPROVED INTENT:
The owner directs a diagnostic-first reliability increment for the O'Pip Chief
(AI) integration after the November 6 production diagnostics reported
CHIEF_UNAVAILABLE, AI_SERVICE_UNAVAILABLE and ValueError without an attributable
cause. The increment must determine what can be proven from the available
evidence, correct only the defects that the evidence supports, and keep the
advisory and execution boundaries exactly as they are. The proven defect is that
every unusable provider outcome - an empty body, malformed JSON, a non-object
document, a wrong response shape, a provider-declared truncated response, a
provider rejection, a rate limit, a transport failure - reached the caller as the
same bare ValueError and was recorded with nothing but the exception class name,
so the categories could not be counted and the interaction could not be corrected
from evidence. The increment therefore makes each failure countable and keeps
fail-closed behaviour; it does not retry, does not change output budgets, does not
change providers or models, and does not widen any authority. No deployment,
merge, funded trading, paper admission, learning or IRR activation is authorized
by this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/opip_ai_router.py: the single provider invocation, routing and usage-record boundary.
- OHM-Trade-Agent-v1/app/services/chief_analyst.py: the advisory review, its response contract and its fail-closed verdict.
- OHM-Trade-Agent-v1/app/opip/decision/funnel.py: AI-stage invocation states and summary shape.
- OHM-Trade-Agent-v1/app/opip/decision/observer.py: attribution of the AI stage into the qualification funnel.
- OHM-Trade-Agent-v1/app/opip/decision/engine.py: the AI_INVOCATION gate and the operational reason class.
- OHM-Trade-Agent-v1/app/opip/decision/summary.py: the operator-facing scan summary.
- OHM-Trade-Agent-v1/app/opip/decision/explanations.py: the read-only zero-trade explanation payload.
- OHM-Trade-Agent-v1/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md: the operator-facing router and telemetry contract.
- OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md: advisory-only authority of the Chief stage.
- OHM-Trade-Agent-v1/OPIP_DECISION_ENGINE_V1.md: the operational reason class that AI_SERVICE_UNAVAILABLE belongs to.
- AGENTS.md: advisory authority, fail-closed gates, secret-handling and evidence-separation boundaries.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a configured provider returns a completed response that satisfies the Chief response contract
WHEN: the Chief review runs
THEN: the review completes with the provider and model recorded, no failure code set, and the advisory route marked advisory_only with funded_trade_authority_changed false.

AC-002:
GIVEN: a provider returns a completed response with no output text
WHEN: the Chief review runs
THEN: the review fails closed as CHIEF_UNAVAILABLE, the failure is classified EMPTY_RESPONSE with the provider status recorded, no candidate or recommendation is produced, and no OpenAI usage record is written for the unusable call.

AC-003:
GIVEN: a provider returns a body that is not valid JSON
WHEN: the Chief review runs
THEN: the review fails closed and is classified MALFORMED_JSON, and no alert, candidate or directional recommendation is produced from the partial body.

AC-004:
GIVEN: a provider returns valid JSON that violates the Chief response contract, being a non-object document, an unsupported recommended_action, a top_candidates value that is not a list, or a non-object candidate entry
WHEN: the Chief review runs
THEN: the review is rejected fail-closed and classified JSON_NOT_OBJECT or SCHEMA_INVALID, a null top_candidates can no longer raise a scan-aborting TypeError, and no candidate is inferred.

AC-005:
GIVEN: a provider declares the response incomplete because the output-token budget was exhausted, including when reasoning tokens consume that whole budget
WHEN: the Chief review runs
THEN: the failure is classified TRUNCATED_OUTPUT with incomplete_reason recorded, a partially emitted body is never accepted as a review, and the provider's input, output and reasoning token usage is preserved in the router usage record.

AC-006:
GIVEN: a provider failure whose classification is retryable by nature, being a rate limit, a timeout, a transport failure or a provider outage
WHEN: the Chief review runs
THEN: the attempt is classified accordingly with retryable true, exactly one provider call is made, and no retry is performed without a separately approved retry contract.

AC-007:
GIVEN: a non-retryable provider failure occurs and an alternate provider is configured
WHEN: the router executes the configured provider order
THEN: the failing attempt keeps its own classification, the configured fallback is attempted, and the successful response records the actual provider and model that answered.

AC-008:
GIVEN: every configured provider fails
WHEN: the Chief review runs
THEN: the failed provider chain is recorded with each provider, its order and its classification, the verdict stays fail-closed, and the operator line names the classification instead of only the exception class.

AC-009:
GIVEN: any provider failure is recorded in durable evidence
WHEN: the router usage record, the Chief stage evidence and the candidate trace are written
THEN: every recorded message is bounded and redacted, no credential, API key or prompt content is present, and billed token usage is recorded per attempt including reasoning tokens.

AC-010:
GIVEN: a provider returns a valid review containing an alert and a watch recommendation
WHEN: the downstream recommendation gate and confidence schema evaluate that review
THEN: only the alert proceeds to further qualification, WATCH and malformed confidence remain independently refused, and no trading authority is granted.

AC-011:
GIVEN: the production failure mode of an unusable provider response
WHEN: the review is attributed through the qualification funnel to the decision engine
THEN: the candidate terminates on AI_INVOCATION with AI_SERVICE_UNAVAILABLE, the classified failure code is preserved in the gate metadata and the scan summary, the shadow engine stays non-authoritative, and counterfactual eligibility is not created.

AC-012:
GIVEN: this increment is the active ATDD increment
WHEN: the scope checker evaluates the contract and the changed-file set
THEN: the contract declares every required section in order with GIVEN, WHEN and THEN criteria, every acceptance test is traced to its criterion, and only files mapped to an approved criterion may change.

EXPLICITLY OUT OF SCOPE:
- Any change to provider selection, provider order, routing policy, model identifiers, reasoning effort or output-token budgets.
- Any retry, backoff or repeated-call policy, and any increase in paid provider usage.
- Any change to scoring, thresholds, ranking, risk sizing, F11 protection, portfolio protection, paper admission, learning or IRR activation, or execution authority.
- Any provider replacement, new dependency, new service or new architecture layer.
- Any production deployment, merge, funded trading, or production configuration change.
- Attribution of the specific November 6 production counts to a failure category. No runtime production evidence was retrievable from the development environment, so the increment makes the categories measurable and claims nothing beyond the deterministic mechanism it corrects.

FROZEN BOUNDARIES:
- AI output remains advisory only; it never authorizes, sizes, places or modifies a trade.
- Deterministic risk, target, economic, liquidity, capital and protection gates are unchanged and remain authoritative.
- An unusable provider response always fails closed as CHIEF_UNAVAILABLE and then AI_SERVICE_UNAVAILABLE.
- The recorded error_type vocabulary and the reason-code vocabulary are unchanged; the new classification fields are additive.
- Provider telemetry stays fail-open; provider adjudication stays fail-closed.
- No secret, credential or prompt content may appear in recorded diagnostics.
- The Chief response contract stays strict: an invalid or missing recommendation is rejected, never fabricated.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_001_valid_response_completes_chief_evaluation
AC-002 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_002_empty_response_is_classified_and_fails_closed
AC-003 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_003_malformed_json_never_produces_a_recommendation
AC-004 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_004_wrong_json_structure_is_rejected_with_its_own_code
AC-005 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_005_reasoning_exhaustion_is_not_mistaken_for_an_empty_answer
AC-005 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_005_partial_body_from_a_truncated_response_is_never_accepted
AC-006 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_006_retryable_failure_is_classified_and_not_retried
AC-007 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_007_non_retryable_failure_uses_the_configured_fallback
AC-008 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_008_failed_chain_is_recorded_when_every_provider_fails
AC-009 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_009_failure_diagnostics_never_carry_credentials
AC-009 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_009_usage_record_is_written_only_for_billed_attempts
AC-010 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_010_valid_response_proceeds_to_downstream_qualification
AC-010 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_010_malformed_confidence_never_reaches_qualification
AC-011 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_011_production_incident_chain_is_reproduced_and_attributed
AC-012 -> tests/test_opip_ai_integration_reliability_v1.py::test_ac_012_scope_contract_declares_the_active_increment


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-001 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-002 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-003 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-004 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-005 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-006 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-007 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-008 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/services/opip_ai_router.py
AC-009 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-009 -> OHM-Trade-Agent-v1/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-011 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/decision/funnel.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/decision/observer.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/decision/engine.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/decision/summary.py
AC-011 -> OHM-Trade-Agent-v1/app/opip/decision/explanations.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1.md
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py

DEFERRED DISCOVERIES:
No runtime production evidence was retrievable from the development environment:
there is no SSH path to the trading droplet from this workstation, no analytics
plane database access, and no exported AI funnel artefact, so the November 6
counts (Chief invocation failures, successes and ValueErrors) cannot be
re-attributed here and are not claimed to be. The observed diagnostics were also
insufficient by construction: only the exception class name survived a failure.
This increment therefore makes the categories measurable rather than asserting a
category. Two decisions are deliberately deferred until the released code
produces that distribution: (1) whether a bounded retry is justified for
PROVIDER_RATE_LIMIT, PROVIDER_TIMEOUT, PROVIDER_TRANSPORT and
PROVIDER_UNAVAILABLE, and (2) whether the premium route's reasoning effort is
incompatible with the configured OPENAI_MAX_OUTPUT_TOKENS. Both need evidence
from opip_ai_router_usage.jsonl (failure counts by error_code and reasoning_tokens
versus output_tokens on failed calls) before they can be corrected without
increasing paid usage. The DigitalOcean fallback remains conditional on a
configured DIGITALOCEAN_INFERENCE_KEY that cannot be verified from this
environment.

UNAPPROVED SCOPE CHANGES:
NONE
