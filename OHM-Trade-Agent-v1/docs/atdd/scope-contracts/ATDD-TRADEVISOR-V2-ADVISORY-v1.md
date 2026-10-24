INCREMENT:
ATDD-TRADEVISOR-V2-ADVISORY-v1

OWNER-APPROVED INTENT:
Authorize, and only authorize, the Tradevisor V2 advisory integration on `feature/tradevisor-v2-ingress`: default-disabled Tradevisor TradingView webhook ingress, deterministic evaluation, notifications and learning. The owner subsequently approved three *policy dispositions* on 2026-10-23: G-02, unavailable F6 permits a recorded shadow hypothesis but never actionable qualification without an independently approved trusted forecast or deterministic fallback; G-03, the explicitly classified noncanonical inbox is an approved shadow-observation/learning exception with no canonical admission or execution rights; and G-04, actionable SWING qualification requires independently trusted completed 4H context, never inferred from 1H. This increment implements fail-closed qualification and retains shadow plan evidence. No F6 model or fallback, no 4H data provider and no canonical handoff are authorized or created. ATDD-29 is now exercised by an explicit F6 abstention gate; ATDD-38 is an approved scope exception **not an outage PASS**; ATDD-43 remains structurally partial pending soak. This contract grants no trading authority, changes no risk default, no fixed sizing, no minimum reward-to-risk, no order path, no canonical writer, no selector, no release-profile key and no production configuration. It does not authorize deployment, activation, credentials, Telegram production delivery, paper or funded execution, or merging PR #444.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/atdd/README.md: the ATDD scope-control mechanism, its precedence, the required contract sections and the movable `ACTIVE_INCREMENT` pointer.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md: the canonical rule that `ACTIVE_INCREMENT` is mutable orchestration state naming the single increment currently authorized to change files, and that exact current-increment enforcement belongs to `tests/atdd_scope.py` and CI.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-LEARNING-RECOVERY-v1.md: the increment this contract succeeds. Its AC-005 acceptance test verifies that the pointer resolves to an existing contract and declares its own increment; it does not pin the pointer to itself, so the pointer is formally movable without breaking that increment.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: the canonical parser and checker, including exit 0 PASS, 1 TRACEABILITY_GAP and 2 SCOPE_CHANGE_REQUIRED.
- .github/workflows/pytest.yml `atdd-scope` job: reads `docs/atdd/ACTIVE_INCREMENT` and compares the pull-request diff against that increment only.
- OHM-Trade-Agent-v1/TRADEVISOR_V2.md, OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md and OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md: the operator documentation, the 52-scenario matrix and the certification record this increment makes authoritative for the integration.
- AGENTS.md and CLAUDE.md: smallest change that solves the proven problem; no trading authority; no new infrastructure; never weaken a gate.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the Tradevisor V2 integration as shipped, with no operator configuration
WHEN:
the installed posture is inspected
THEN:
the ingress is disabled by default, no committed Compose environment sets OPIP_TRADEVISOR_ENABLED, a request to the webhook route returns 404 and no event is durably recorded; and this increment is authorized only by this contract together with the ACTIVE_INCREMENT pointer that names it, which the scope checker proves

AC-002:
GIVEN:
an authenticated, well-formed Tradevisor BUY or SELL alert for a configured source instrument
WHEN:
it is delivered to the versioned webhook
THEN:
the route answers 202 with status accepted, trade_authority_changed false, and one durable row recording the source facts; acceptance means durably received and never means authorized, evaluated or approved

AC-003:
GIVEN:
a delivery whose edge verification stamp is wrong or absent, whose immediate peer is untrusted, or whose body token is missing or wrong
WHEN:
the route handles it
THEN:
it is refused with 404 before any schema detail is disclosed, no row is recorded, and the ingestion token is never persisted, logged or echoed

AC-004:
GIVEN:
a payload with an unsupported schema version, a non-positive or unparseable price, an unmapped source symbol, or a body larger than the configured cap
WHEN:
the route handles it
THEN:
it is refused with 422 or 413, no durable row is created, and no market, AI or Telegram call is made on the acknowledgement path

AC-005:
GIVEN:
an alert whose {{time}} describes a bar that has not closed, whose trigger is older than the freshness window, or whose trigger is in the future beyond the skew allowance
WHEN:
ingress validation runs
THEN:
it is refused with 422 because {{time}} is the bar open and bar close is derived as open plus interval, so an unfinished, stale or future-dated bar can never be treated as closed evidence

AC-006:
GIVEN:
the same alert delivered twice, including a replay whose receipt time differs
WHEN:
both deliveries are processed
THEN:
the first answers accepted and the second answers duplicate with the same dedup_id, and exactly one durable row exists because the dedup identity is built from source facts only

AC-007:
GIVEN:
a BUY alert for which native market evidence or executable spread and depth cannot be obtained
WHEN:
the advisory pipeline evaluates it
THEN:
the decision is non-actionable with no plan, recorded as MARKET_DATA_UNAVAILABLE or EXECUTION_EVIDENCE_UNAVAILABLE, and no recommendation is rendered

AC-008:
GIVEN:
a Tradevisor SELL alert
WHEN:
the advisory pipeline evaluates it
THEN:
the outcome is WATCH with actionable false and no plan, the evidence records short_order NOT_PERMITTED and auto_close_funded_position NOT_PERFORMED, and no short is opened and no funded position is closed

AC-009:
GIVEN:
a source payload that asserts risk, sizing, order type or trading authority, or an evaluated decision
WHEN:
the boundary is exercised
THEN:
the authority-asserting payload is refused with 422 because the schema forbids unknown fields, and any evaluated decision records trade_authority_changed false and native_admission NOT_PERFORMED

AC-010:
GIVEN:
an event whose evaluation keeps raising
WHEN:
it exceeds the configured attempt limit
THEN:
it reaches the terminal POISONED disposition, the durable row states it, and the recorded failure reason carries the exception type only and never its message text

AC-011:
GIVEN:
a frozen actionable plan that was never delivered
WHEN:
the delivery or retry pass runs after the plan has expired
THEN:
the plan is no longer a retry candidate, nothing is sent, and stale actionable advice cannot be resurrected because a retry re-evaluates fresh evidence instead of re-sending a stored plan

AC-012:
GIVEN:
persisted advisory decision evidence and a learning worker whose deployed SHA does not equal the expected production SHA
WHEN:
the consumption job builds its report
THEN:
it fails closed with RELEASE_DRIFT or RELEASE_UNVERIFIED, publishes no cohort metrics, records policy_promotion NONE and trade_authority_changed false, and the release classifier is the learning plane's own function object rather than a copy

AC-013:
GIVEN:
the operator cycle and the Tradevisor advisory phase
WHEN:
the cycle runs with the ingress disabled, enabled, or with a failing advisory pass
THEN:
the phase is inert while disabled, runs strictly after active-position protection, and any failure is contained so it never delays or aborts the cycle; this is structural independence only and is not overload or soak certification

AC-014:
GIVEN:
the durable advisory store and the integration's documentation
WHEN:
the evidence disposition is read
THEN:
it declares evidence_class ADVISORY_NON_CANONICAL, canonical_handoff NOT_PERFORMED and consumption app.jobs.report_tradevisor_learning with no admission or execution authority; the owner-approved G-03 shadow exception is explicit, ATDD-38's canonical-outage scenario remains UNEXERCISED (not PASS), and ATDD-43 overload proof is pending

AC-015:
GIVEN:
an otherwise qualifying BUY with native 1H, R4, F5, F7 and known exposure, but no trusted F6 forecast/approved fallback or completed trusted SWING 4H context
WHEN:
the evaluator finishes the proposed trade hypothesis and the notifier receives the decision
THEN:
the decision is WATCH and nonactionable, records ADVISORY_EVIDENCE_INCOMPLETE with explicit F6 and (for SWING only) 4H blockers, preserves a frozen hypothetical plan for prospective shadow measurement without an actionable expiry, and emits no actionable Telegram notification; forward-only candle observations are measured as SHADOW_HYPOTHESIS with no fill attribution, but unrelated WATCH rejections are not treated as trade hypotheses; no 1H approximation or missing-status substitution creates an authorization

EXPLICITLY OUT OF SCOPE:
- Any order placement, modification, cancellation or confirmation path, and any execution authority
- Any change to fixed risk sizing, minimum reward-to-risk, position verification or execution safeguards
- Registering a trusted F6 forecast, approving a deterministic F6 actionable fallback, or activating a forecast-dependent qualification pathway (G-02's fail-closed policy *is* approved)
- Introducing a canonical handoff or promoting this shadow observation to canonical admission/execution (G-03's noncanonical shadow exception *is* approved)
- Fetching or deriving trustworthy completed 4H data, or changing the native 1H regime basis (G-04's SWING fail-closed policy *is* approved)
- Enabling the ingress in any environment, activating TradingView alerts, creating or rotating credentials, enabling Telegram production delivery, or enabling paper or funded execution
- Deployment, production configuration, branch protection, rulesets or workflow changes
- Any change to the Wave 8.2 TradingView bridge, the release-profile key set, the canonical writer, the selector or the learning-worker SHA contract
- Changing the scope checker's parser, arrow syntax, status codes or fail-closed behaviour
- Editing, retiring or weakening another increment's contract, acceptance traceability or assertions
- Adding a second active increment, a concurrent-increment mechanism, or a second scope-control mechanism

FROZEN BOUNDARIES:
- `docs/atdd/ACTIVE_INCREMENT` names exactly one increment and that increment's contract must exist; `tests/atdd_scope.py` remains the sole owner of exact current-increment enforcement.
- `ATDD-OPIP-LEARNING-RECOVERY-v1` remains a valid, complete, unmodified contract whose acceptance tests continue to pass; its AC-005 expression of the pointer rule is the movable form and is not weakened by this handoff.
- `OPIP_TRADEVISOR_ENABLED` stays false unless an operator explicitly sets it; no committed environment may set it.
- Advisory evidence never acquires admission, execution, Telegram-production or canonical-writer authority.
- ATDD-29 is an exercised fail-closed F6 abstention check; ATDD-38 remains an **owner-approved shadow-only scope exception**, not a successful canonical outage test; ATDD-43 remains partial, without load/soak certification.
- No production, trading, Feature Bus, paper or deployment authority is created, widened or transferred by this increment.
- Learning-evidence retention is fail-closed (N-01): a terminal *transport* status never authorises deletion. Retention requires a non-learning terminal state (`POISONED`) or a valid durable acknowledgement naming the exact `dedup_id`, an allowed disposition, a definitive measurement status and the current release SHA. Unacknowledged evidence is retained; capacity enforces existing 503 backpressure rather than evicting it; legacy rows without consumption metadata are treated as unconsumed.
- A definitive outcome requires provably complete observation (N-02): expected bars are the UTC epoch-grid multiples of the real candle interval starting strictly after the decision instant and closing at or before the window end. Missing, duplicated-conflicting, misaligned, unfinished or non-finite bars yield `PARTIAL_COVERAGE`, which never contributes to measured counts, false-positive rates or profitability statistics. A touch is never a fill.


ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_001_default_installation_exposes_nothing
AC-002 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_002_authenticated_buy_and_sell_are_durably_accepted
AC-003 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_003_unauthenticated_alerts_learn_nothing
AC-004 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_004_malformed_and_oversized_payloads_are_refused
AC-005 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_005_unfinished_stale_and_future_events_are_refused
AC-006 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_006_duplicate_delivery_creates_exactly_one_record
AC-007 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_007_missing_evidence_abstains_without_a_plan
AC-008 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_008_sell_is_evidence_only
AC-009 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_009_source_cannot_assert_authority
AC-010 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_010_poison_payload_reaches_a_terminal_disposition
AC-011 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_011_expired_plan_is_never_delivered
AC-012 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_012_learning_fails_closed_on_release_drift
AC-013 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_013_advisory_phase_is_last_inert_and_contained
AC-014 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_014_evidence_is_noncanonical_and_has_a_consumer
AC-015 -> tests/test_tradevisor_v2_advisory_acceptance_v1.py::test_ac_015_untrusted_f6_and_missing_swing_4h_never_send_actionable_alert


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md
AC-001 -> OHM-Trade-Agent-v1/app/core/config.py
AC-001 -> OHM-Trade-Agent-v1/app/main.py
AC-001 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/models/tradevisor.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-002 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-002 -> OHM-Trade-Agent-v1/tools/tradevisor_alert_preview.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/api/tradevisor.py
AC-003 -> OHM-Trade-Agent-v1/deploy/nginx/tradevisor-webhook.conf.example
AC-003 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/tradevisor_ingress.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_ingress_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_webhook_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_market.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-007 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-009 -> OHM-Trade-Agent-v1/app/api/tradevisor.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_inbox_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_pipeline.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_notifier_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_recovery_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-012 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-012 -> OHM-Trade-Agent-v1/app/jobs/report_tradevisor_learning.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_v1.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/drain_tradevisor_inbox.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_tradevisor_boundaries_v1.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-014 -> OHM-Trade-Agent-v1/app/services/tradevisor_inbox.py
AC-014 -> OHM-Trade-Agent-v1/app/jobs/report_tradevisor_learning.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md
AC-014 -> OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_inbox_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_recovery_v1.py
AC-014 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py
AC-015 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-015 -> OHM-Trade-Agent-v1/app/services/tradevisor_learning.py
AC-015 -> OHM-Trade-Agent-v1/TRADEVISOR_V2.md
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/tradevisor_v2_atdd.md
AC-015 -> OHM-Trade-Agent-v1/docs/reviews/tradevisor_v2_certification.md
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_learning_v1.py
AC-015 -> OHM-Trade-Agent-v1/tests/test_tradevisor_v2_advisory_acceptance_v1.py

DEFERRED DISCOVERIES:
- G-01: no genuine TradingView to O'Pip delivery has been observed; the source-to-production certification remains open and blocks activation, not this increment.
- G-02 POLICY RESOLVED 2026-10-23: F6 is missing; no unapproved actionable fallback. This increment blocks actionable qualification and preserves shadow hypotheses. A trusted model/fallback and validation remain future work.
- G-03 POLICY RESOLVED 2026-10-23: explicitly noncanonical shadow observation and learning are permitted. The original ATDD-38 canonical-outage scenario is not exercised and cannot be claimed PASS; any promotion needs separate approval.
- G-04 POLICY RESOLVED 2026-10-23: a SWING needs completed trusted 4H regime context to become actionable. No 4H provider exists yet; all current otherwise qualified SWING proposals remain WATCH, independently of the F6 block.
- G-05: F11 independence is proven structurally; no overload, burst or soak measurement exists, so ATDD-43 stays partial.
- TradingView documents an SSL client certificate that the recipient may verify. This integration does not verify it; adopting recipient-side verification is an edge change plus a staging verification, tracked as a security follow-up.
- Whether the Tradevisor scenario suite should carry acceptance markers per scenario, rather than the grouped criteria used here, is a documentation question for a future increment.
- N-01 limitation (2026-10-24): no deployment unit invokes `report_tradevisor_learning` and the learning worker holds no reverse-write authority into production storage, so no production process records a consumption acknowledgement. Eligible evidence is therefore retained until the inbox reaches capacity and ingress returns 503. Ending that state requires an owner-approved acknowledgement writer or durable archival handoff; until then the consumption lifecycle must not be described as fully operational.
- N-02 limitation (2026-10-24): coverage uses the interval the caller declares (60 minutes from the report job). A source that changes interval, or returns bars on a different grid, will be classified `PARTIAL_COVERAGE` rather than measured - a deliberate fail-closed default.

UNAPPROVED SCOPE CHANGES:
NONE

