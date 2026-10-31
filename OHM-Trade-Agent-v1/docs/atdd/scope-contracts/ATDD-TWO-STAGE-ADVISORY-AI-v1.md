INCREMENT:
ATDD-TWO-STAGE-ADVISORY-AI-v1

OWNER-APPROVED INTENT:

Record and prove the two-stage advisory design: Stage 1 keeps the existing
Chief Analyst router on the provider-documented OpenAI model `gpt-6.1-sol`, and
Stage 2 adds a default-off, shadow-only Independent Research & Risk Review on
the provider-documented DeepSeek model `deepseek-flash` behind a new isolated
adapter. Stage 2 challenges an already-qualified Chief recommendation using an
immutable evidence copy, is strictly budgeted per provider, is idempotent, and
returns advisory metadata only. It never changes ranking, alert qualification,
risk, capital, canonical decisions, orders, the Chief conclusion, the Committee,
learning, Paper-v2 or the release profile, and it never widens funded authority.

ARCHITECTURE REFERENCES:

- v1.5.0 section F12: asynchronous advisory research; no AI vote, narrative or
  wrapper changes admission, exit, size, stops, risk or probability
- v1.5.0 UI-02: AI review state and stance remain separate optional metadata;
  missing AI is never favourable analysis and creates no approval or veto
- v1.5.0 F11: protection stays independent of AI and of the advisory review
- OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md: the increment record
- Provider documentation: OpenAI model catalogue (`gpt-6.1-sol`, Responses API,
  reasoning levels, list price) and DeepSeek Models & Pricing plus JSON Output /
  Thinking Mode / Chat Completions references (`deepseek-flash`)
- OHM-Trade-Agent-v1/app/services/opip_ai_router.py and
  OHM-Trade-Agent-v1/app/services/recommendation_gate.py: the reused routing and
  qualification boundaries

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
Stage 2 is enabled in shadow mode and the Chief review contains qualified
recommendations
WHEN:
the independent review runs
THEN:
only the top eligible qualified recommendations are sent, the request matches
the documented provider contract, the transmitted evidence copy is immutable and
digest-verifiable, authority-shaped provider fields are dropped, and a disabled
or unknown mode or an unqualified review spends nothing

AC-004:
GIVEN:
a configured Stage 2 with a provider credential
WHEN:
the credential is missing, the model is undocumented, the answer is malformed,
the provider fails, the spend cannot be proven, or a daily or per-call budget is
exhausted
THEN:
the attempt fails closed without changing the Chief review, no credential
material is persisted or logged, and a repeated identical evidence copy replays
without a second paid call

AC-005:
GIVEN:
a successful, failed or disabled Stage 2 attempt
WHEN:
the Chief review is returned to the caller
THEN:
the conclusion, ranking, alert set and every pre-existing field are unchanged,
the Stage-2 result is additive advisory metadata marked non-authoritative, and
the Stage-2 module cannot import any ranking, risk, execution, exchange,
Telegram, paper, canonical, committee or learning module


EXPLICITLY OUT OF SCOPE:

- any change to risk sizing, minimum reward-to-risk, position verification,
  deterministic gates, alert qualification, ranking or Telegram delivery
- any Committee, learning, Paper-v2, canonical-writer, target-spine, Feature Bus
  or release-profile change, activation or consumption
- any exchange, Kraken, order, position or funded-credential access
- any deploy, merge, activation, operator-mode change or production credential
- any new dependency, service, scheduler, database, droplet or retry loop

FROZEN BOUNDARIES:

- EVIDENCE_SHADOW release profile, `OPIP_PAPER_V2_MODE=off` and
  `OPIP_COMMITTEE_MODE=off` unchanged
- F11 protection independence unchanged; no protection path waits on Stage 2
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
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_shadow_review_is_off_by_default_and_spends_nothing
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_only_off_and_shadow_modes_are_accepted
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_unqualified_chief_items_are_not_sent
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_only_the_top_eligible_recommendations_are_reviewed
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_qualified_recommendation_uses_documented_deepseek_contract
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_evidence_copy_is_immutable_and_matches_the_transmitted_bytes
AC-003 -> tests/test_opip_irr_shadow_v1.py::test_non_advisory_provider_fields_are_dropped
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_missing_credential_makes_no_call_and_writes_no_evidence
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_unverified_model_id_is_refused
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_malformed_provider_answer_fails_closed
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_non_json_provider_body_fails_closed
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_provider_transport_failure_is_contained
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_provider_http_error_is_recorded_without_a_body
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_daily_cost_budget_blocks_further_spend
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_daily_call_budget_blocks_further_spend
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_unreadable_usage_journal_fails_closed
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_per_call_cost_limit_blocks_an_expensive_request
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_identical_evidence_is_reviewed_once
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_changed_evidence_is_a_new_review
AC-004 -> tests/test_opip_irr_shadow_v1.py::test_evidence_journal_is_auditable_and_carries_no_credential
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_stage2_runs_after_the_chief_conclusion_and_never_changes_it
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_stage2_failure_leaves_the_chief_conclusion_intact
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_stage2_unexpected_error_is_contained
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_stage2_is_disabled_when_the_mode_is_off
AC-005 -> tests/test_opip_irr_shadow_v1.py::test_stage2_module_imports_no_authority_module

IMPLEMENTATION MAP:

AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_two_stage_stage1_openai_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TWO-STAGE-ADVISORY-AI-v1.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_two_stage_stage1_openai_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-003 -> OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-003 -> OHM-Trade-Agent-v1/OPIP_ADAPTIVE_INTELLIGENCE_ROUTER_V1.md
AC-004 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-005 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TWO-STAGE-ADVISORY-AI-v1.md

DEFERRED DISCOVERIES:

- the first live shadow call is the acceptance observation for provider
  compatibility; no paid API call was made in this increment
- `OPIP_AI_PROVIDER_ORDER` is a priority list rather than an allowlist; making
  it strict is a separate routing change that needs its own approved contract
- an operator-facing view of the Stage-2 verdict (dashboard or Telegram) is not
  included and would need its own presentation contract

UNAPPROVED SCOPE CHANGES:
NONE
