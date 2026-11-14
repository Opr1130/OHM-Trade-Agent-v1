INCREMENT:
ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1

OWNER-APPROVED INTENT:
The Telegram delivery event ledger appended one row on every scan for a suppression whose verdict had not changed (observed: up to 1,410 rows for one signal; a 106 MB ledger), which also crowds real delivery events out of the 20,000-event summary window. Bound the repeats of an unchanged non-attempt verdict (same alert, same status, same reason) for the QUALIFIED_OPPORTUNITY family only (other families, such as SYSTEM_HEALTH incidents, keep their contractual one-row-per-occurrence audit) to one ledger row per window and count the skipped repeats on the next row, so audit completeness and the first/changed/heartbeat rows are kept. Send attempts, failures and retries are never collapsed.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/app/services/telegram_delivery.py: delivery event ledger and state.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the same alert suppressed for the same reason on every five-minute scan for two days, and separately a changed reason, plan fingerprint or status
WHEN:
each suppression is recorded
THEN:
the ledger holds one row per hourly window (48 rows, not 576) with the skipped repeats counted on the following row, and every changed reason, fingerprint or status is written immediately

AC-002:
GIVEN:
repeated SEND_FAILED attempts, and a delivery between two identical suppressions
WHEN:
they are recorded
THEN:
every attempt row is kept with its attempt and retry counts, and the later suppression is written because the previous status differs

AC-003:
GIVEN:
a window that has elapsed, a clock that went backwards, or a zero window
WHEN:
a suppression is recorded
THEN:
the row is written

AC-004:
GIVEN:
repeated identical suppressions for an alert family other than QUALIFIED_OPPORTUNITY
WHEN:
they are recorded
THEN:
every occurrence is written as before

AC-005:
GIVEN:
suppressions collapsed into later rows, or still pending in the state file
WHEN:
the delivery summary is built
THEN:
suppressed, by_status, by_family and by_suppression_reason include every collapsed repeat under the verdict it repeated (not the verdict of the row that carries it), nothing is counted twice when pending repeats are later flushed to a row, and collapsed_suppression_repeats reports how many were folded

AC-006:
GIVEN:
a malformed, non-object or non-finite Telegram delivery state file
WHEN:
the delivery summary is built for any scope
THEN:
the summary never moves, quarantines, locks or rewrites the file or creates any file, and reports that the state was not readable

AC-007:
GIVEN:
collapsed suppression runs that began before, on or after the requested UTC day
WHEN:
a day-scoped summary is built
THEN:
only runs that began that day are credited to it, runs that began on an earlier day are reported separately as unattributed to a day and never credited, and the all-time summary counts every run exactly once

EXPLICITLY OUT OF SCOPE:
- Any change to admission verdicts, delivery, retry scheduling, the outbox, or message content.
- Truncation or rewriting of the existing ledger; deployment, restarts, runtime configuration, PostgreSQL, F11, Learning, Committee and order authority.

FROZEN BOUNDARIES:
- Rows written are byte-compatible except for the additive suppressed_since_last_row, suppressed_since_last_row_status and suppressed_since_last_row_reason fields; state gains additive keys only.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-004 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_004_other_alert_families_keep_one_row_per_occurrence
AC-001 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_001_a_unchanged_suppression_is_bounded_to_one_row_per_window
AC-001 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_001_b_every_change_is_written
AC-002 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_002_send_attempts_are_never_collapsed
AC-002 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_002_a_a_send_between_suppressions_resets_the_run
AC-003 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_003_window_expiry_and_clock_skew_write_the_row
AC-005 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_005_summary_counts_collapsed_repeats_under_the_verdict_they_repeated
AC-006 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_006_summary_never_moves_or_rewrites_a_malformed_state_file
AC-007 -> tests/test_telegram_suppression_repeat_bound_v1.py::test_ac_007_a_day_scoped_summary_never_credits_a_run_that_began_yesterday

IMPLEMENTATION MAP:
AC-005 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-006 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-007 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-001 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-002 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-003 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-004 -> OHM-Trade-Agent-v1/app/services/telegram_delivery.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_telegram_suppression_repeat_bound_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-TELEGRAM-SUPPRESSION-BOUND-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
