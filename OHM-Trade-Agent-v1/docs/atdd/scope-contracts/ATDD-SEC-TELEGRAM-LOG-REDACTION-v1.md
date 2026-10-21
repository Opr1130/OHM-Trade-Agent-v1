INCREMENT:
ATDD-SEC-TELEGRAM-LOG-REDACTION-v1

OWNER-APPROVED INTENT:
Contain and correct a credential exposure introduced by the certified Telegram poller. During V2 activation `telegram_callback_listener` handled `httpx` failures with `logger.exception(...)`. Telegram's Bot API embeds the bot token in the request path, an `httpx.HTTPStatusError` message contains that URL, and `logger.exception` renders the exception text and traceback, so a `409 Conflict` from `getUpdates` wrote the production bot token into the Docker log. The owner authorised one narrowly scoped security correction: never log token-bearing URLs, raw HTTPX exception messages or request objects on any Telegram path; log a bounded category and status instead; add defence-in-depth redaction for any token that reaches a log record by another route; and prove with negative tests that a synthetic token cannot appear in log output. The owner also authorised a controlled core-only release replacement rather than a first-time bootstrap, so the existing writer, canonical database, genesis receipt, `WRITER_ACTIVATED.json`, incident ledger, recovery evidence and scheduler-OFF state are preserved. No trading functionality, release profile, protection semantics or authorization behaviour changes.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/services/telegram_callback_listener.py: the `getUpdates` poller, command worker and market-watch loop; the six `logger.exception` sites that produced the exposure.
- OHM-Trade-Agent-v1/app/services/telegram_notifier.py: `_telegram_post` already logs endpoint, status and exception class only, and is the pattern this increment applies everywhere.
- OHM-Trade-Agent-v1/app/services/telegram_delivery.py: the certified tracked-delivery path that must keep working unchanged.
- AGENTS.md principle 7: secrets must never be exposed in logs, receipts or source.
- OHM-Trade-Agent-v1/deploy/m07/scripts/v2/bootstrap-v2.sh: the M07 first-start gates that a core-only replacement must not re-run or bypass.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md: the movable ACTIVE_INCREMENT rule.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
an HTTPX or unexpected exception whose rendered text contains a Telegram bot URL
WHEN:
the error is reduced for logging
THEN:
the result contains only the exception class name and, when available, the HTTP status code; it never contains the token, the request URL, the request object or the raw exception message, and the rendering is bounded
AC-002:
GIVEN:
the production polling paths in the Telegram listener
WHEN:
a 403, 409, timeout, transport failure or unexpected exception occurs
THEN:
the emitted log record contains no bot token and no api.telegram.org URL, the control flow still waits and retries exactly as before, and the listener never terminates
AC-003:
GIVEN:
a log record that already carries a bot token in its message or arguments
WHEN:
it passes through the Telegram logger
THEN:
the token is replaced with a redaction marker before the record is emitted, as defence in depth behind the primary control
AC-004:
GIVEN:
the corrected listener module and the existing authorization helpers
WHEN:
the security tests and the existing Telegram suite run
THEN:
the listener contains no logger.exception call, the authorization and rate-limit semantics are unchanged, and every previously passing Telegram test still passes

EXPLICITLY OUT OF SCOPE:
- Any change to trading logic, risk sizing, minimum reward-to-risk, protection verdicts, release profiles or execution authority
- Any change to the canonical writer, schema, DDL, wire protocol or recovery semantics
- Any new Telegram feature, command, alert type or delivery behaviour
- Re-running the M07 first-time bootstrap, reissuing WRITER_ACTIVATED.json or reactivating the writer
- Rotating credentials inside this increment; the owner performs token replacement outside the code change

FROZEN BOUNDARIES:
- The certified application SHA c196dd674678b3b1ee91c351ee97c1c9e2b3938d and the existing image remain immutable; any corrected application is published as a NEW immutable image, never patched in place.
- Exactly one canonical writer throughout; the scheduler stays OFF until the separate acceptance gates pass.
- No secret is read, printed, logged or placed in a receipt by this increment.
- Retry intervals, stop-event control flow and queue semantics are unchanged.
- Every historical increment contract and assertion remains intact; only the movable pointer changes.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_telegram_log_redaction_v1.py::test_safe_error_never_contains_the_token
AC-002 -> tests/test_telegram_log_redaction_v1.py::test_polling_failure_logs_without_the_token
AC-003 -> tests/test_telegram_log_redaction_v1.py::test_redaction_filter_scrubs_message_and_arguments
AC-003 -> tests/test_telegram_log_redaction_v1.py::test_directly_logged_exception_is_redacted
AC-004 -> tests/test_telegram_log_redaction_v1.py::test_listener_uses_no_traceback_logging

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/telegram_log_safety.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_telegram_log_redaction_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/telegram_callback_listener.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_telegram_log_redaction_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/telegram_log_safety.py
AC-003 -> OHM-Trade-Agent-v1/app/services/telegram_notifier.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_telegram_log_redaction_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/services/telegram_callback_listener.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_telegram_log_redaction_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SEC-TELEGRAM-LOG-REDACTION-v1.md

DEFERRED DISCOVERIES:
- The certified image already ships this logging pattern; the correction only takes effect once a newly built image is deployed through the core-only replacement procedure.
- HTTPX call sites outside the Telegram path were not audited under this increment and would need their own scope if a similar pattern is suspected.

UNAPPROVED SCOPE CHANGES:
NONE

