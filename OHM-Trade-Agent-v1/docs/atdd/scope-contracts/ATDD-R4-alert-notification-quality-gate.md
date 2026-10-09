INCREMENT:
ATDD-R4-alert-notification-quality-gate

OWNER-APPROVED INTENT:
Gate user-facing NEW TRADE Telegram on the already-authoritative ranked and action-gated selection, bound that delivery per decision window, and keep notification policy from creating detector, ranking, portfolio, or trading truth. Preserve protection and lifecycle alerts, the EVIDENCE_SHADOW posture, and the existing early-watch family behind its current flag. Shadow F7 stays non-authoritative.

ARCHITECTURE REFERENCES:
- O'Pip Profit Intelligence Platform Architecture v1.4.3 Section 19 Decision First Alert Contract, as the presentation contract for the primary NEW TRADE body.
- OHM-Trade-Agent-v1/docs/reviews/OPIP_SIGNAL_QUALITY_V2_CONSOLIDATED_DECISION.md sections G and H for rejected-versus-undelivered and next-best already performed by the action gate.
- OHM-Trade-Agent-v1/OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md is design-for-review and is not a production supersession of existing alert families.
- The live authoritative path is rank_profit_opportunities, optional capital-efficiency re-rank, then _apply_ranked_action_gates in app/jobs/scan_opportunities.py. Target-spine F7 remains shadow and is not alert authority.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a detector or local technical score that has not passed the authoritative rank and action gate
WHEN:
a NEW TRADE notification is considered
THEN:
Telegram is not sent from that score alone, and the scan path notifies only through the authoritative selection window

AC-002:
GIVEN:
several already-actionable candidates in one decision window
WHEN:
NEW TRADE delivery is chosen
THEN:
the existing authoritative order is preserved and user-facing delivery is bounded, while protection notifiers do not consult that bound

AC-003:
GIVEN:
an already-computed quality score and a notification-only threshold
WHEN:
the threshold changes
THEN:
notification eligibility changes and the caller rows keep the same rank and score

AC-004:
GIVEN:
a NEW TRADE identity, a material fingerprint, and a cooldown window
WHEN:
a duplicate, an unchanged geometry, a noise-only change, or a material geometry change is presented
THEN:
duplicates and unchanged geometry are suppressed, noise does not mint a fingerprint, and a material fingerprint may re-alert inside the window

AC-005:
GIVEN:
a still-waiting qualified opportunity
WHEN:
tracking or Telegram delivery fails
THEN:
the failure stays retryable and the chief notifier does not terminalize the trade

AC-006:
GIVEN:
authoritative plan evidence for one selected candidate
WHEN:
the primary NEW TRADE body is rendered
THEN:
the body is decision-first, uses exact prices, keeps at most four evidence-backed reasons, and does not label a heuristic score as confidence or a probability

AC-007:
GIVEN:
the authoritative actionable set for a scan
WHEN:
notification slots are chosen
THEN:
the notification function does not rebuild qualification, and paper routing still receives the full ranked set

AC-008:
GIVEN:
a candidate suppressed by the notification window
WHEN:
the suppression is recorded
THEN:
the candidate remains in the input set and the delivery audit records the versioned suppression reason

AC-009:
GIVEN:
the Telegram callback listener
WHEN:
its source is inspected
THEN:
it contains no exchange order placement and states that Kraken execution is not enabled

AC-010:
GIVEN:
a protection event and a closed noncritical attention budget
WHEN:
the protection event is evaluated
THEN:
it remains deliverable and the trade monitor does not use the NEW TRADE window selector

EXPLICITLY OUT OF SCOPE:
- Merging, deploying, or changing production
- Activating TARGET_PAPER, Paper-v2, Committee, or funded trading
- Changing strict F11, AC-026, Feature Bus cadence, canonical writer authority, or scheduler authority
- Making shadow F7 authoritative or changing frozen F5, F6, or F7 semantics
- Changing portfolio allocation or reservation semantics
- Retiring EARLY WATCH or other existing alert families
- Creating a second ranker, selector, alert pipeline, or learning store

FROZEN BOUNDARIES:
- EVIDENCE_SHADOW modes stay as already deployed. This increment does not edit release profiles.
- ACTIONABLE_TRADE remains in CRITICAL_EVENTS so protection fail-open and the generic noncritical budget bypass stay intact.
- The action gate remains the next-best selector. Notification code does not promote a vetoed rank.
- Telegram callbacks remain local lifecycle acknowledgements without exchange authority.
- min_alert_score remains the legacy webhook decision threshold and is not the notification-only floor.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r4_alert_notification_quality.py::test_score_only_path_cannot_send_new_trade
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_new_trade_window_is_ranked_and_bounded
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_duplicate_ranks_do_not_consume_new_trade_slots
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_new_trade_delivery_never_exceeds_configured_cap
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_tracking_failure_queue_counts_toward_the_cap
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_same_queued_fingerprint_does_not_consume_another_slot
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_queued_unchanged_rank_leaves_the_slot_for_the_next_rank
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_recovery_delivers_no_more_than_the_configured_bound
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_recovery_bound_leaves_later_rows_pending_for_the_next_run
AC-002 -> tests/test_opip_r4_alert_notification_quality.py::test_stuck_tracking_row_does_not_block_a_later_recovery
AC-003 -> tests/test_opip_r4_alert_notification_quality.py::test_notification_threshold_does_not_change_rank_or_score
AC-004 -> tests/test_opip_r4_alert_notification_quality.py::test_fingerprint_dedupe_cooldown_and_material_change
AC-005 -> tests/test_opip_r4_alert_notification_quality.py::test_tracking_failure_does_not_terminalize_trade
AC-006 -> tests/test_opip_r4_alert_notification_quality.py::test_primary_alert_is_decision_first_without_fake_confidence
AC-006 -> tests/test_opip_r4_alert_notification_quality.py::test_delivered_primary_message_contains_generated_trade_id
AC-006 -> tests/test_opip_r4_alert_notification_quality.py::test_queued_outbox_message_contains_same_trade_id
AC-007 -> tests/test_opip_r4_alert_notification_quality.py::test_notification_policy_does_not_rebuild_qualification
AC-007 -> tests/test_opip_r4_alert_notification_quality.py::test_threshold_suppression_keeps_lifecycle
AC-007 -> tests/test_opip_r4_alert_notification_quality.py::test_volume_cap_suppression_keeps_lifecycle
AC-007 -> tests/test_opip_r4_alert_notification_quality.py::test_notification_settings_do_not_change_paper_routing_input
AC-008 -> tests/test_opip_r4_alert_notification_quality.py::test_suppressed_candidate_remains_auditable
AC-009 -> tests/test_opip_r4_alert_notification_quality.py::test_telegram_callback_has_no_exchange_order_authority
AC-010 -> tests/test_opip_r4_alert_notification_quality.py::test_protection_alerts_are_outside_the_new_trade_budget

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-alert-notification-quality-gate.md
AC-001 -> OHM-Trade-Agent-v1/app/api/routes.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_trade_lifecycle.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-002 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-002 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-002 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-002 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-002 -> OHM-Trade-Agent-v1/app/core/config.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-003 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-003 -> OHM-Trade-Agent-v1/app/core/config.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-004 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-004 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-005 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-005 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_wave9_action_notification_reliability.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_wave9_review_fixes.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-006 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-006 -> OHM-Trade-Agent-v1/app/services/telegram_notifier.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-008 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-008 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py
AC-010 -> OHM-Trade-Agent-v1/app/services/notification_policy.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_r4_alert_notification_quality.py

DEFERRED DISCOVERIES:
- OHM-Trade-Agent-v1/OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md and the consolidated review describe a future cutover that would stop user-facing EARLY WATCH. No production supersession is in force. The family remains behind opip_early_watch_alerts_enabled and was not retired here.
- The legacy TradingView v1 webhook still computes an alert/watch/reject decision with min_alert_score. That decision is journaled and no longer sends Telegram. Retiring the decision itself is a separate increment.
- Shadow F7 is not the live alert authority. Promoting it would change frozen selection semantics and was not done.
- Next-best promotion already exists in _apply_ranked_action_gates. Notification code does not add a second promotion rule.

UNAPPROVED SCOPE CHANGES:
NONE
