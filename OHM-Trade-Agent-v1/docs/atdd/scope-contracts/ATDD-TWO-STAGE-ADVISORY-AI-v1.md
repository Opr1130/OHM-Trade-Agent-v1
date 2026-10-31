INCREMENT:
ATDD-TWO-STAGE-ADVISORY-AI-v1

OWNER-APPROVED INTENT:

Record and prove the two-stage advisory design: Stage 1 keeps the existing
Chief Analyst router on the provider-documented OpenAI model `gpt-6.1-sol`, and
Stage 2 adds a default-off, shadow-only Independent Research & Risk Review on
the provider-documented DeepSeek model `deepseek-flash`. Remediation round: the
Stage-2 provider call is removed from the scan/Chief path and moved to a
dedicated bounded worker on the existing scheduler; only finally-qualified
survivors (deterministic target + economic + portfolio/capital gates) produce
immutable evidence; provider spend is reserved atomically before every attempt
and every chargeable outcome is settled; and the credential may only ever be
sent to the explicitly approved provider origin. It never changes ranking,
alert qualification, risk, capital, canonical decisions, orders, the Chief
conclusion, the Committee, learning, Paper-v2 or the release profile, and it
never widens funded authority.

ARCHITECTURE REFERENCES:

- v1.5.0 section F12: asynchronous advisory research; no AI vote, narrative or
  wrapper changes admission, exit, size, stops, risk or probability
- v1.5.0 UI-02: AI review state and stance remain separate optional metadata;
  missing AI is never favourable analysis and creates no approval or veto
- v1.5.0 F11: protection stays independent of AI and of the advisory review
- OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md: the increment record
- Provider documentation: OpenAI model catalogue (`gpt-6.1-sol`) and DeepSeek
  Models & Pricing, JSON Output, Thinking Mode and Chat Completions references
  (`deepseek-flash`, `https://api.deepseek.com`)
- OHM-Trade-Agent-v1/app/services/p1_shadow_outbox.py and
  OHM-Trade-Agent-v1/deploy/cron.d/opip-feature-bus-capture: the existing
  producer/consumer, bounded-worker and single-scheduler patterns reused here
- OHM-Trade-Agent-v1/app/services/recommendation_gate.py: the reused
  deterministic qualification boundary

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the documented OpenAI model `gpt-6.1-sol` and the existing Chief router
WHEN:
a Chief review is planned and its provider request is invoked
THEN:
the model id, the two input roles, the bounded output cap and a documented
reasoning effort are preserved, and an empty, malformed or non-object answer
still fails closed with no fabricated recommendation

AC-002:
GIVEN:
an OpenAI-only increment with no DigitalOcean credential or model id configured
WHEN:
the route is planned, and when the OpenAI budget blocks it
THEN:
exactly one OpenAI target exists, no DigitalOcean fallback can run, the block
fails closed, and the fact that the provider order is a priority list rather
than an allowlist stays recorded rather than assumed away

AC-003:
GIVEN:
Stage 2 is enabled in shadow mode and the provider is slow, hung or unavailable
WHEN:
the scan, the Chief review and the queue producer run
THEN:
none of them performs or waits on provider I/O, the Chief conclusion is
returned unchanged with no Stage-2 field, and the only process that talks to
the provider is the dedicated bounded worker

AC-004:
GIVEN:
a candidate that has passed the final deterministic target, economic and
portfolio/capital gates, and a provider answer that tries to add authority
WHEN:
the scan queues the immutable evidence copy and the worker reviews it
THEN:
only finally-qualified survivors are queued, the worker re-checks that
qualification before spending, the transmitted evidence is detached and
digest-verifiable, and every authority-shaped provider field is dropped

AC-005:
GIVEN:
a configured Stage 2 with a provider credential
WHEN:
an attempt is made, or the spend ledger is corrupt or unwritable, or a daily or
per-call budget is exhausted, or the provider fails, returns malformed content
or omits token usage, or an identical evidence copy repeats
THEN:
the worst-case spend is reserved atomically before the call, every chargeable
outcome is settled and counted, unprovable or unwritable spend refuses the
call, concurrent attempts cannot exceed the budget, and a repeated identical
evidence copy replays without a second paid call

AC-006:
GIVEN:
the Stage-2 endpoint configuration
WHEN:
an arbitrary HTTPS host, a non-default port, a path, a query, a fragment or
embedded userinfo is configured
THEN:
the endpoint is refused before any credential is attached, and only the
explicitly approved provider origin is reachable

AC-007:
GIVEN:
a disabled or credential-less Stage 2, and a successful attempt
WHEN:
the producer, the worker and the evidence journal are inspected
THEN:
the default posture is inert and spends nothing, a missing credential leaves
the queue unprocessed, the audit rows carry no credential material, and the
Stage-2 modules cannot import any ranking, risk, execution, exchange, Telegram,
paper, canonical, committee or learning module

EXPLICITLY OUT OF SCOPE:

- any change to risk sizing, minimum reward-to-risk, position verification,
  deterministic gates, alert qualification, ranking or Telegram delivery
- any Committee, learning, Paper-v2, canonical-writer, target-spine, Feature Bus
  or release-profile change, activation or consumption
- any exchange, Kraken, order, position or funded-credential access
- any deploy, merge, activation, operator-mode change or production credential
- any new dependency, service, scheduler, database, droplet or agent framework
- any scanner reliability, acquisition or promotion behaviour

FROZEN BOUNDARIES:

- EVIDENCE_SHADOW release profile, `OPIP_PAPER_V2_MODE=off` and
  `OPIP_COMMITTEE_MODE=off` unchanged
- F11 protection independence unchanged; the worker never takes the unified
  cycle lock and no protection path waits on Stage 2
- canonical writer ownership and the legacy new-entry authority unchanged
- funded credential isolation and the alert-only/MONITOR posture unchanged
- no production configuration literal added to `docker-compose.yml`

ACCEPTANCE TEST TRACEABILITY:

AC-001 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_gpt_6_1_sol_is_routed_with_documented_reasoning_effort
AC-001 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_responses_request_uses_documented_parameters
AC-001 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_malformed_or_empty_chief_answer_fails_closed
AC-002 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_openai_only_posture_holds_when_no_digitalocean_credential_exists
AC-002 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_provider_order_is_a_priority_list_not_an_allowlist
AC-002 -> tests/test_opip_two_stage_stage1_openai_v1.py::test_openai_budget_block_fails_closed_without_a_digitalocean_credential
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_chief_review_performs_no_provider_io
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_hung_provider_never_delays_the_primary_pipeline
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_producer_enqueues_only_finally_qualified_survivors
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_worker_refuses_evidence_without_final_qualification
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_evidence_copy_is_immutable_and_digest_verifiable
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_non_advisory_provider_fields_are_dropped
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_reservation_is_written_before_the_provider_call
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_concurrent_attempts_cannot_overspend
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_failed_call_is_chargeable
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_malformed_response_with_usage_is_charged
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_failure_without_usage_charges_the_reserved_worst_case
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_malformed_spend_ledger_fails_closed
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_spend_persistence_failure_fails_closed
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_daily_cost_budget_blocks_before_spending
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_repeat_evidence_is_not_charged_twice
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_only_the_approved_provider_endpoint_is_accepted
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_approved_origin_is_normalised
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_unapproved_endpoint_makes_no_call
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_invoke_refuses_a_rogue_config
AC-007 -> tests/test_opip_irr_shadow_v1.py::test_disabled_is_inert_and_spends_nothing
AC-007 -> tests/test_opip_irr_shadow_v1.py::test_missing_credential_leaves_the_queue_unprocessed
AC-007 -> tests/test_opip_irr_shadow_v1.py::test_evidence_journal_is_auditable_and_carries_no_credential
AC-007 -> tests/test_opip_irr_shadow_v1.py::test_stage2_modules_import_no_authority_module

IMPLEMENTATION MAP:

AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_two_stage_stage1_openai_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TWO-STAGE-ADVISORY-AI-v1.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_two_stage_stage1_openai_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-003 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-003 -> OHM-Trade-Agent-v1/app/jobs/drain_irr_shadow.py
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-irr-shadow
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-004 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/drain_irr_shadow.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-007 -> OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-007 -> OHM-Trade-Agent-v1/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TWO-STAGE-ADVISORY-AI-v1.md

DEFERRED DISCOVERIES:

- the first live shadow call is the acceptance observation for provider
  compatibility; no paid API call was made in this increment
- `OPIP_AI_PROVIDER_ORDER` is a priority list rather than an allowlist; making
  it strict is a separate routing change that needs its own approved contract
- an operator-facing view of the Stage-2 verdict (dashboard or Telegram) is not
  included and would need its own presentation contract
- the IRR queue is append-only and bounded by `OPIP_IRR_MAX_CANDIDATES` per
  scan; compaction or archival is a separate retention question

UNAPPROVED SCOPE CHANGES:
NONE


