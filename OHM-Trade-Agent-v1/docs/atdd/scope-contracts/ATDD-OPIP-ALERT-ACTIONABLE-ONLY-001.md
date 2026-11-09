INCREMENT:
ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001

OWNER-APPROVED INTENT:
The owner approved requirement OPIP-ALERT-ACTIONABLE-ONLY-001 after the trading channel
received approximately 750 alerts in 10 days, including roughly 50 in one day, many of them
informational Early Watch / Market Watch cards carrying heuristic scores but no executable trade
plan. The owner directs that Telegram deliver only complete, validated BUY/SELL manual-trade
opportunities, material updates or cancellations of an already-notified plan, and critical
position-protection or safety events, while market discovery and learning evidence continue
silently. This increment therefore (a) makes the automatic Early Watch channel evidence-only in
the proposed release and gives it one unambiguous policy instead of an independent bypass, (b)
introduces one shared, pure actionable-notification admission policy that consumes the existing
deterministic qualification, geometry, economic and risk verdicts rather than duplicating them,
(c) routes every actionable producer through it -- the native qualified path, its durable retry
outbox and the Tradevisor plane -- enforcing expiry and delivery eligibility at both enqueue and
send time, and (d) closes the unused direct Early Watch send that bypassed the policy entirely.
It requires fresh market evidence, a confirmed entry trigger with a feasible entry zone,
executable liquidity/spread/depth/size-slippage evidence, stop, target(s), invalidation, expiry,
positive cost-adjusted economics and canonical episode deduplication, and it deliberately uses a
configurable net reward-to-risk requirement instead of a fixed profit amount. AI stays advisory
and cannot satisfy or override any condition; heuristic scores are never inputs and are never
presented as probabilities. Protection alerts, critical system notifications and actionable
updates are preserved, as are explicit user-requested one-time command responses. Notification
outcomes (ADMITTED/SUPPRESSED with a reason) stay separate from trade outcomes. No funded or
automated trading, no live exchange execution, no paper admission, no learning activation and no
weakening of F11 is authorized by this increment; the active ATDD pointer is deliberately not
moved or pinned here, because its sequencing belongs to the increment that currently holds it.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/actionable_admission.py: the new single admission policy; pure, notification-only, no I/O and no authority.
- OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py: the native qualified-plan producer, its material geometry fingerprint and its reserve/send/confirm settlement.
- OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py: the durable retry outbox where enqueue-time and send-time eligibility are enforced.
- OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py: the Tradevisor producer and its existing action/economic/allocation contract.
- OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py: where the execution, liquidity and coverage evidence is recorded for the Tradevisor plane.
- OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py: the single production call site that supplies execution evidence and the configured policy.
- OHM-Trade-Agent-v1/app/jobs/scan_movers.py: the governed automatic Early Watch channel and its evidence-only resolution.
- OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py: the direct Early Watch sender whose bypass is closed.
- OHM-Trade-Agent-v1/app/services/notification_policy.py, alert_governor.py, attention_budget.py and alert_taxonomy.py: the existing dedupe, cooldown, reservation, budget and plane separation that this increment reuses unchanged.
- OHM-Trade-Agent-v1/app/services/telegram_delivery.py: the delivery ledger, its suppression reasons and the bounded aggregate summary.
- OHM-Trade-Agent-v1/app/core/config.py: where the versioned actionable-notification settings are declared.
- OHM-Trade-Agent-v1/docker-compose.yml: the production activation surface whose Early Watch value is pinned.
- OHM-Trade-Agent-v1/docs/atdd/README.md and OHM-Trade-Agent-v1/tests/atdd_scope.py: the scope-control mechanism this contract is written against.
- AGENTS.md: alert-only authority, fixed risk and minimum reward-to-risk preservation, evidence separation, durable evidence and fail-closed gates.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: an Early Watch observation scored 68/100 with heuristic risk 28/100 and no approved entry
WHEN: the automatic Early Watch policy and the actionable admission gate are evaluated
THEN: no proactive Telegram delivery is possible, the observation keeps its suppression reason in the delivery ledger, and a heuristic score is not an input the gate can accept

AC-002:
GIVEN: a candidate whose only distinction is a 95/100 heuristic score and which is missing its entry trigger, stop, target or net economics
WHEN: the actionable admission gate is evaluated
THEN: the notification is refused with that specific missing fact as its reason and nothing is delivered

AC-003:
GIVEN: a fresh BUY that satisfies every mandatory condition with a complete plan
WHEN: the gate admits it and the notification policy reserves delivery
THEN: the verdict carries the plan facts the card must state, and exactly one reservation is granted for that identity and material fingerprint

AC-004:
GIVEN: an already-delivered actionable episode observed again without a material change
WHEN: the gate and the notification policy are evaluated
THEN: the observation is refused as a duplicate, heuristic scores and timestamps alone cannot mint a new fingerprint, and no second delivery is permitted

AC-005:
GIVEN: a delivered actionable plan whose stop and target have since moved
WHEN: the material update is evaluated
THEN: the material fingerprint changes while the delivery identity does not, and exactly one new emission is permitted for the update

AC-006:
GIVEN: a qualified setup whose Telegram send fails
WHEN: the delivery ledger and the notification policy are inspected
THEN: the attempt is recorded as SEND_FAILED and not delivered, the reserved capacity is released so the still-valid setup stays retryable, and the trade is not rejected

AC-007:
GIVEN: an exhausted informational attention budget
WHEN: stop, emergency, position-warning or actionable-trade notifications are evaluated
THEN: they are still allowed while an informational Early Watch card is refused

AC-008:
GIVEN: a plan that satisfies every condition with exactly one mandatory fact missing, stale, invalid or already spent
WHEN: the gate is evaluated
THEN: the notification fails closed with that specific reason, never becomes admitted, and reports only notification vocabulary rather than a trade lifecycle decision

AC-009:
GIVEN: the production Compose contract and the Early Watch policy resolution
WHEN: the automatic channel is resolved and a broad sweep is suppressed
THEN: the release pins the channel false, the channel cannot run while the actionable-only policy governs alerts, discovery and learning capture continue, and suppression recording stays bounded

AC-010:
GIVEN: the actionable-notification settings
WHEN: they are read from configuration and applied to a verdict
THEN: every threshold is a validated setting with a safe default, an unusable value cannot fail open, the version is recorded in the audit payload, and no fixed profit amount exists in the policy

AC-011:
GIVEN: the native qualified path, its durable retry outbox, the Tradevisor plane and the direct Early Watch sender
WHEN: each producer is inspected and the direct sender is called without an opt-in
THEN: all actionable producers call the one shared gate, retry delivery is revalidated for expiry at send time, and the direct Early Watch send is refused with a recorded reason

AC-012:
GIVEN: this scope contract and the movable active-increment pointer
WHEN: both are inspected
THEN: this contract declares exactly this increment with every required section, its implementation map authorizes the changed paths including the sequenced pointer, every acceptance criterion is traced to a test, and the pointer resolves to an existing contract without being owned or pinned by this increment

AC-013:
GIVEN: an architectural review of the released gate found six adapter and notification-contract defects
WHEN: the corrected policy, producers, retry outbox and card renderer are exercised
THEN: 24h market turnover and the position-capacity ceiling are separate facts with separate tests (turnover against the market floor, ceiling against the contemplated order size and configured utilization), no synthetic freshness or venue identity can be produced inside the gate (a missing timestamp or upstream identity verdict fails closed), an executable limit entry is distinguished from an unconfirmed future setup by the authoritative trigger flag, plan style, analysed price and chase boundary, a retry row without an admitted verdict fails closed with its reason instead of being delivered blind, every emitted actionable card states the entry zone, stop-loss, targets, invalidation, expiry, setup identity and the net reward-to-risk the gate admitted (an incomplete card is refused, and both actionable planes share the block), and execution depth plus the measured impact are read from the direction-specific leg while the cost model adds assumed fees and the measured impact exactly once

EXPLICITLY OUT OF SCOPE:
- Any change to deterministic qualification, ranking, scoring, capital allocation, risk sizing, execution geometry or the economic execution gate's protected thresholds
- Any new trading, order-placement, order-modification, order-cancellation or confirmation authority; funded and automated execution remain disabled
- Paper-v2 activation, Feature Bus activation, canonical writer mode changes, learning worker activation or release-profile changes
- Any new scheduler, service, state store, database, queue or deployment path; the existing notification policy, alert governor, delivery ledger and outbox remain the only mechanisms
- Weakening or bypassing F11, the action gate, the economic gate, the target attainability gate, the liquidity capacity gate, the execution-quality gate or position verification
- Deleting, truncating or rewriting the delivery ledger, outbox, governor state or any historical evidence
- Moving, pinning or rewriting `docs/atdd/ACTIVE_INCREMENT`, or any other increment's contract
- Changing the rendered content rules of protection, system-health, external-order or command-response alerts
- Modifying the economics of an alert card beyond what the gate computes to justify admission

FROZEN BOUNDARIES:
- `app/services/actionable_admission.py` stays pure: no I/O, no delivery, no registry writes, no clock of its own, and no trade, size or order authority.
- Notification outcomes and trade outcomes stay separate: a suppression is never a REJECTED/EXPIRED trade decision and must never terminalize a pending setup.
- Every existing deterministic verdict remains an input; no threshold in this policy may rescue a candidate the pipeline rejected, and AI can never satisfy or override a condition.
- Protection, system-health, external-order and command-response planes keep their existing delivery paths and their immunity to informational budgets.
- Delivery stays reserve/send/confirm with an idempotent identity; a failed send stays retryable while the plan is valid, and an expired plan is never delivered late.
- An entry must carry a stop, at least one target, an invalidation and an expiry; a missing expiry may be resolved only from the configured validity window, never invented as a level.
- The minimum economics requirement stays a configurable net reward-to-risk ratio; no fixed profit amount may be introduced into the notification policy.
- All durable evidence stays additive: no truncation, migration or rewrite of historical delivery rows.
- The scope checker's external contract (exit 0 PASS, 2 SCOPE_CHANGE_REQUIRED, 1 TRACEABILITY_GAP) is unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_001_early_watch_with_heuristic_scores_produces_no_proactive_telegram
AC-002 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_002_high_score_without_a_complete_plan_is_never_actionable
AC-003 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_003_a_complete_plan_is_admitted_once_with_a_durable_identity
AC-004 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_004_repeated_observation_without_material_change_never_duplicates
AC-005 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_005_material_change_updates_the_original_identity
AC-006 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_006_failed_delivery_is_retryable_and_never_rejects_the_trade
AC-007 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_007_critical_protection_bypasses_informational_throttling
AC-008 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_008_missing_stale_or_invalid_evidence_fails_closed
AC-008 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_008_a_suppression_is_never_a_trade_rejection
AC-009 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_009_automatic_early_watch_is_evidence_only_and_capture_continues
AC-009 -> tests/test_wave9_actionable_only_alerts.py::test_production_compose_keeps_early_watch_dark
AC-010 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_010_the_policy_is_versioned_configurable_and_never_a_profit_cutoff
AC-011 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_011_every_actionable_producer_routes_through_one_gate
AC-012 -> tests/test_opip_alert_actionable_only_v1.py::test_ac_012_governance_contract_traceability_and_movable_pointer
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_deep_market_with_a_small_capacity_ceiling_is_refused_by_capacity_only
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_large_capacity_ceiling_cannot_excuse_a_thin_market
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_capacity_is_compared_with_the_order_size_and_the_configured_utilization
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_the_gate_no_longer_exposes_an_aliased_liquidity_field
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_absent_evidence_timestamp_fails_closed_instead_of_using_the_clock
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_venue_identity_is_never_synthesised_by_the_adapter
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_producers_supply_venue_and_identity_from_the_exchange_that_own_them
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_placeable_limit_entry_is_admitted_and_classified_as_executable
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_limit_beyond_the_chase_boundary_is_an_unconfirmed_future_setup
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_an_authoritative_unconfirmed_trigger_refuses_even_with_a_valid_zone
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_market_entry_outside_its_zone_is_not_confirmed
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_short_limit_entries_use_the_mirrored_executable_window
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_legacy_outbox_row_without_an_admitted_verdict_fails_closed
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_outbox_row_with_a_suppressed_verdict_is_not_delivered
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_outbox_row_admitted_then_expired_is_retired_with_the_expiry_reason
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_the_emitted_actionable_card_states_every_mandatory_plan_element
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_an_incomplete_card_is_refused_rather_than_delivered
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_the_tradevisor_delivery_appends_the_same_mandatory_block
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_long_and_short_read_their_own_depth_leg_and_measured_impact
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_a_short_plan_is_refused_when_only_the_ask_side_is_covered
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_the_cost_model_counts_fees_and_measured_impact_exactly_once
AC-013 -> tests/test_opip_alert_actionable_only_regressions_v1.py::test_an_absent_measured_impact_makes_the_net_economics_uncomputable

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_movers.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-003 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-004 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-005 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-006 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-007 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-008 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-009 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-009 -> OHM-Trade-Agent-v1/app/jobs/scan_movers.py
AC-009 -> OHM-Trade-Agent-v1/app/core/config.py
AC-009 -> OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_wave9_actionable_only_alerts.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_wave9_action_notification_reliability.py
AC-010 -> OHM-Trade-Agent-v1/app/core/config.py
AC-010 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-011 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-011 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py
AC-011 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-011 -> OHM-Trade-Agent-v1/app/services/movement_discovery_v2.py
AC-011 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-011 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_trade_lifecycle.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_tradevisor_notifier_v1.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ALERT-ACTIONABLE-ONLY-001.md
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-012 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-013 -> OHM-Trade-Agent-v1/app/services/actionable_admission.py
AC-013 -> OHM-Trade-Agent-v1/app/services/actionable_card.py
AC-013 -> OHM-Trade-Agent-v1/app/services/capital_efficiency_ranking.py
AC-013 -> OHM-Trade-Agent-v1/app/exchanges/kraken_identity.py
AC-013 -> OHM-Trade-Agent-v1/app/services/tradevisor_market.py
AC-013 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-013 -> OHM-Trade-Agent-v1/app/services/tradevisor_notifier.py
AC-013 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-013 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-013 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-013 -> OHM-Trade-Agent-v1/app/core/config.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_regressions_v1.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_alert_actionable_only_v1.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_tradevisor_notifier_v1.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_trade_lifecycle.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_wave9_action_notification_reliability.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py

DEFERRED DISCOVERIES:
- Whether an already-qualified limit entry waiting for a better price should be admitted at all is
  an owner volume decision, not an engineering one. The policy therefore ships
  `opip_actionable_admit_limit_entries` (default true, i.e. current behaviour for a complete limit
  plan) rather than unilaterally narrowing to immediate entries only; flipping it to false admits
  only plans whose trigger is confirmed now. Both branches are deterministic and already covered
  by the same gate.
- The notification cost model uses one configurable round-trip percentage. Trading both a fee and
  a measured slippage term per venue would be more precise, but the scanner already validates
  spread, depth coverage and measured round-trip drag, so a second per-venue cost model would
  duplicate an existing calculation for a notification-only decision.
- The Tradevisor plane's evidence now records the visible liquidity and full-coverage facts the
  gate needs. A follow-up could align its liquidity source with the scanner's capacity ceiling so
  both planes cite one number; the current values come from the same snapshot the decision used.
- Whether the suppression ledger should also expose per-family admission counters is a Control
  Center presentation question; the bounded `by_suppression_reason` aggregate added here is the
  minimum needed to keep suppression attributable.

UNAPPROVED SCOPE CHANGES:
NONE