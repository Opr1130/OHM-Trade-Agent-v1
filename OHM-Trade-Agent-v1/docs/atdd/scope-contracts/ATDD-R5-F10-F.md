INCREMENT:
ATDD-R5-F10-F

OWNER-APPROVED INTENT:
This is the census-declared Telegram increment of the owner-approved F10 mission, inside frozen architecture v1.5.0. The source census plans exactly seven criteria for it -- `AC-120` through `AC-126` -- and this contract is the first F10 increment that approves criteria by the identifiers the census planned for it, because the census names this increment as their owner rather than deferring them to the page increment. Those seven keep their census meanings verbatim; this contract adds only an eighth of its own for its delivery record.

What changes where. One new module is added to the notification plane: a pure decision-first binding guard that turns a card into either an actionable presentation, an advisory-only presentation, a withholding or a refusal -- and says which and why. It performs no I/O, sends nothing, and decides no eligibility. The existing notification policy remains the only eligibility authority and its verdict is an *input*: the binder cannot produce an actionable card without it. The existing delivery ledger remains the only place a delivery, a suppression, a failure or an edit is recorded, and the existing taxonomy remains the only place a notification plane is classified. No transport code, no retry policy, no cooldown, no budget, no threshold and no Telegram endpoint is touched, and no production credential is used.

Why a binder rather than a new notifier. The census records two Telegram capabilities as needing extension: proving that every actionable label is bound to a current committed eligible decision, and fail-closed actionability where no price is guessed and no ordinal score is dressed as a calibrated confidence. Both are *binding* concerns, not delivery concerns. Putting them in a new module that cannot send, cannot schedule and cannot read policy state keeps the extension honest: the rules that decide what may be shown are separable, testable and provably unable to become a second authority, while transport and eligibility stay where they already are.

The census's verdicts are carried, not rewritten. All eight dashboard pages stay unclaimable on the read plane, the parity predicate stays NOT_VERIFIED with zero verified pages, per-page projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified. The 54 dashboard criteria are untouched by this increment; the criterion matrix's coverage note is updated only to record that the Telegram criteria now have a delivered owner, and its own 54 rows and totals do not change.

KEY LAW - THE BINDER CONSUMES A VERDICT. It never computes one. Its authorization parameter is required, a missing verdict is a refusal, and the module imports the eligibility authority nowhere. A card that has not been through the policy cannot be rendered as something to act on.

KEY LAW - A MISSING VALUE IS NEVER A SUBSTITUTED VALUE. No price is invented where no committed price evidence exists, an unstated or unparseable validity is withheld rather than assumed current, an expired card is refused, and an ordinal score stays ordinal: without a calibrated marker the confidence is withheld with its reason and no percentage is printed.

KEY LAW - ADVISORY TEXT IS NOT DETERMINISTIC EVIDENCE. Committee material is returned in its own advisory block, never merged into the facts, and the rendered card states that it carries no authority. A deterministic risk or execution gate can never be overridden by anything on this path, because nothing on this path can act.

KEY LAW - THE DELIVERY LEDGER IS THE ONLY RECORD OF DELIVERY. A failed send is recorded as a failure with its reason and is not delivered; a suppression is a distinct status; and a message identity is only reported for a send the ledger accepted. No second ledger, no second dedupe key and no second confirmation predicate is introduced.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md sections 13 and 15: the inherited cockpit-alert clause, the F10 feature row, the rule that notification delivery is not order authority and does not gate protection, and the evidence clause "Unsupported source claims, incomplete cost and missing evidence cannot be converted to favorable dashboard states."
- docs/architecture/OPIP_F10_SOURCE_CENSUS.md sections 4 and 8: the seven Telegram rows `F10-TG-01`..`F10-TG-07` this contract approves, their declared producers, their declared evidence, and the follower declaration that names this increment. The census is not modified by this increment.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md sections 5 and 6: the parity predicate this increment must not appear to satisfy, and the fail-closed negative cases.
- docs/atdd/scope-contracts/ATDD-R4-alert-notification-quality-gate.md: the increment that made the notification policy the quality and volume gate this increment reuses unchanged.
- docs/atdd/evidence/F10_CRITERION_MATRIX.md: the dashboard criterion matrix whose coverage note this increment updates without changing a single row or total.
- app/services/notification_policy.py: the only eligibility and dedupe authority, whose verdict this binder consumes.
- app/services/telegram_delivery.py: the only delivery ledger, whose states and audit rows this increment reads and does not change.
- app/services/alert_taxonomy.py and app/services/alert_v2_format.py: the frozen notification planes and the decision-first rendering vocabulary the binder reuses.
- app/services/alert_decision_binding.py: the binding guard this increment adds.
- tests/atdd_scope.py: the scope control that authorizes this increment's exact file set.

APPROVED ACCEPTANCE CRITERIA:

AC-120:
GIVEN:
the decision-first binder and the existing notification eligibility authority
WHEN:
a card is bound without a committed decision identity, without the policy's verdict, and after the policy has refused it
THEN:
the card is refused rather than labelled actionable, the policy's own refusal reason is carried through, and the binder decides no eligibility of its own

AC-121:
GIVEN:
the binder and cards that each miss one thing
WHEN:
a card has no committed price evidence, an expired validity, an unstated validity, an ordinal score, a paper, shadow or advisory mode, and advisory Committee text
THEN:
no price or confidence is invented, expired and unstated evidence is never actionable, an ordinal score is withheld as uncalibrated with no percentage printed, a paper, shadow or advisory card is advisory-only, and Committee text stays in its own advisory block outside the deterministic facts

AC-122:
GIVEN:
the notification policy's revalidation, slot, quality and volume entry points
WHEN:
a card is revalidated at emission time on quality, volume and slot grounds
THEN:
the policy's verdict governs whether a card may be actionable, a suppressed or over-budget card stays non-actionable, and the binder consumes that verdict instead of computing one

AC-123:
GIVEN:
the delivery ledger's derived alert identity, the policy's confirmation predicate and the application package
WHEN:
the identity is derived twice from the same decision and once from a changed fingerprint, and the package is searched for a second authority
THEN:
the identity is stable for one decision, changes with the fingerprint, and no module outside the notification policy and the delivery ledger defines an eligibility predicate or an alert identity

AC-124:
GIVEN:
the delivery ledger and a transport that fails
WHEN:
a send raises, a suppression is recorded, and the ledger is read back
THEN:
the failed attempt is recorded as a failed status with its failure reason and is not delivered, the suppression is a distinct status, and no message identity is claimed for a send that never landed

AC-125:
GIVEN:
the binding module, the delivery module and the release architecture gate
WHEN:
their imports, their failure behaviour and the gate's own statements are inspected
THEN:
no notification module reaches risk, protection or execution, a transport failure is returned as a delivery result rather than raised into a lifecycle caller, and the gate still asserts independent protection

AC-126:
GIVEN:
the owner-set notification limits in the policy and the binding module
WHEN:
the defaults, the binder's own content and the census's classification are inspected
THEN:
every limit remains a policy value the binder neither reads nor writes, the binder holds no cooldown, budget or volume value of its own, and the criterion stays owner-gated rather than ratified

AC-127:
GIVEN:
the autonomous build report, this increment's scope contract, the movable active-increment pointer and the acceptance-test index
WHEN:
the report, the contract, the pointer and the index are read together
THEN:
the report records this increment with the exact base head it stacks on, its own scope contract and its own test count, the pointer resolves to a declared contract, every approved criterion is traced to exactly the acceptance test that cites it and mapped to a path of this increment, the criteria this increment owns are exactly the census's Telegram criteria plus this one, and the parity row stays OUTSTANDING

EXPLICITLY OUT OF SCOPE:
- Any change to the notification policy, its cooldowns, its budgets, its volume caps, its quality threshold, its dedupe keys, its confirmation predicate or its reservation lifecycle. It remains the only eligibility authority and this increment only reads its verdict.
- Any change to the delivery ledger's states, event rows, retry behaviour or message-edit lineage. It remains the only record of delivery and this increment only reads it.
- Any change to the notification taxonomy, the alert classes, the system-incident renderer or the family map.
- Any new transport, scheduler, retry loop, outbox, queue, store, table, migration or durable artifact, and any second notification eligibility, dedupe or delivery authority.
- Any contact with a live Telegram endpoint, any use of a production bot token or chat id, and any production credential. The only send path exercised fails on purpose through an injected transport.
- Any trading, admission, protection, exit, promotion, deployment, release-profile or production authority, and any funded-execution path. A notification is never an order instruction.
- Any change to the 54 dashboard criteria, to the criterion matrix's rows or totals, to the frozen pages, or to the parity posture.
- Any ratification of a notification threshold, cooldown, budget or volume cap, all of which stay owner-gated.

FROZEN BOUNDARIES:
- `app/services/notification_policy.py` stays the only eligibility, budget, cooldown and dedupe authority. `app/services/telegram_delivery.py` stays the only delivery ledger and the only place an alert identity is derived.
- The binding module is pure: no I/O, no network, no clock beyond the instant it is given, no policy state, and no import of the policy, the transport or any risk, protection or execution module.
- Advisory evidence never becomes deterministic evidence, and nothing on the notification path can override a deterministic risk or execution gate.
- Protection stays independent of Telegram: a delivery failure is a delivery fact and cannot reach F11.
- The dashboard plane is untouched: all eight pages stay unclaimable, parity stays NOT_VERIFIED with zero verified pages, projection authority stays UNDECIDED, the counts-and-P&L reconciliation stays NOT_RECORDED and the threshold policy stays unratified.
- USD and USDT remain distinct portfolios; no notification presents a joined or summed figure.
- The project's standing controls on risk sizing, minimum reward-to-risk, position verification, execution safeguards, AI non-authority, paper isolation and deployment authorization are neither relaxed nor referenced by any logic here.

ACCEPTANCE TEST TRACEABILITY:
AC-120 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_120_an_actionable_label_is_bound_to_a_committed_eligible_decision
AC-121 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_121_missing_or_uncalibrated_evidence_fails_closed
AC-122 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_122_revalidation_stays_with_the_notification_policy
AC-123 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_123_emission_identity_is_derived_and_unique
AC-124 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_124_a_failed_send_is_recorded_distinctly_from_a_delivery
AC-125 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_125_telegram_failure_cannot_reach_protection_or_execution
AC-126 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_126_notification_limits_stay_owner_set
AC-127 -> tests/test_opip_r5_f10_f_decision_first_notifications.py::test_ac_127_the_report_the_pointer_and_the_criteria_agree

IMPLEMENTATION MAP:
AC-120 -> OHM-Trade-Agent-v1/app/services/alert_decision_binding.py
AC-120 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-121 -> OHM-Trade-Agent-v1/app/services/alert_decision_binding.py
AC-121 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-122 -> OHM-Trade-Agent-v1/app/services/alert_decision_binding.py
AC-122 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-123 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-124 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-125 -> OHM-Trade-Agent-v1/app/services/alert_decision_binding.py
AC-125 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-126 -> OHM-Trade-Agent-v1/app/services/alert_decision_binding.py
AC-126 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py
AC-127 -> OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md
AC-127 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-127 -> OHM-Trade-Agent-v1/docs/atdd/evidence/F10_CRITERION_MATRIX.md
AC-127 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R5-F10-F.md
AC-127 -> OHM-Trade-Agent-v1/tests/test_opip_r5_f10_f_decision_first_notifications.py

DEFERRED DISCOVERIES:
- This is the first F10 increment that approves criteria under the identifiers the census planned for it, because the census names this increment as their owner instead of deferring them to the page increment. That is the census's own mapping, not a renumbering: the seven meanings are unchanged, and the criterion matrix's coverage note is updated to record the new owner without altering a single row or total.
- The binding rules were kept out of the delivery module on purpose. The census records fail-closed actionability and decision binding as presentation concerns needing extension, while transport and eligibility already work; separating them makes each independently testable and makes it provable that the new code cannot send, cannot schedule and cannot read policy state.
- The binder consumes the eligibility verdict rather than importing the policy, and a test asserts the module never reaches the policy. That is a stronger guarantee than calling the policy and ignoring a bad result, because the module structurally cannot produce a verdict of its own.
- An unstated validity is withheld rather than refused. A refusal reads as a decision about the card; a withholding reads as an absence of evidence, which is what an unstated expiry actually is. The distinction is deliberate and asserted.
- Advisory text is separated by field allow-list rather than by convention: fields that are not declared facts are not rendered at all, so a producer cannot smuggle an unlisted claim into the card body by adding a key.
- The send failure is exercised through an injected transport that raises, which proves the delivery path records the failure and returns a result instead of propagating it, without contacting Telegram and without a production credential. No live endpoint was contacted at any point.
- The criterion this increment owns for its own delivery record is `AC-127`, a new identifier. Reusing one of the census's Telegram identifiers for bookkeeping would have changed what that identifier means.
- Notification thresholds stay exactly where the owner left them. This increment asserts that they remain owner-gated and that the binder carries no cooldown, budget or volume value of its own, which is the strongest statement available without the owner ratifying a number.

UNAPPROVED SCOPE CHANGES:
NONE
