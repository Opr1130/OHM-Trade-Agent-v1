INCREMENT:
ATDD-OPIP-RELAY-REMEDIATION-V1

OWNER-APPROVED INTENT:
Correct five confirmed defects in the merged communication-only engineering relay (#570) in one focused change. The relay keeps no merge, deploy, production or trading authority and gains none; every fix makes it refuse or deduplicate more.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md: transport ADR, trust model and this remediation.
- OHM-Trade-Agent-v1/AGENTS.md: external AI is advisory; deterministic gates fail closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a request whose ACK was published but whose local record was lost before it was written
WHEN:
the relay runs again
THEN:
no second ACK is posted, because the published ACK in the thread counts as the answer

AC-002:
GIVEN:
an issue thread longer than 100 comments
WHEN:
the relay reads it
THEN:
every page is read so a late request is answered, and a thread longer than the page bound fails closed with no state changed

AC-003:
GIVEN:
a request addressed to a recipient other than this relay
WHEN:
the relay runs
THEN:
it posts nothing, records nothing, and reports the message as not for this relay

AC-004:
GIVEN:
a request that names a head_sha
WHEN:
no current head is supplied, or it differs
THEN:
the request is stale and nothing is posted; it is answered only when the named head equals the current head or none is named

AC-005:
GIVEN:
a message id at or beyond the length at which the derived acknowledgement id would exceed its limit
WHEN:
the relay parses or answers it
THEN:
ids that fit are acknowledged, longer ones are rejected at parse, and a reply that cannot be built is recorded as rejected instead of raising

EXPLICITLY OUT OF SCOPE:
- Any change to issue 291, the local agent bridge, scheduling, new services, secrets or host installs.
- Executing or applying anything found in a comment; any merge, deploy, F11, learning or trading authority.

FROZEN BOUNDARIES:
- Comments are untrusted data; GitHub identity is a single account so sender carries no authority.
- authority must remain COMMUNICATION_ONLY; the module stays stdlib only with no execution primitive.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_relay_remediation_v1.py::test_ac_001_a_crash_between_post_and_record_does_not_send_a_second_ack
AC-002 -> tests/test_opip_relay_remediation_v1.py::test_ac_002_a_request_beyond_the_first_100_comments_is_found
AC-002 -> tests/test_opip_relay_remediation_v1.py::test_ac_002_a_thread_longer_than_the_page_bound_fails_closed
AC-003 -> tests/test_opip_relay_remediation_v1.py::test_ac_003_a_message_for_another_recipient_is_not_answered_or_consumed
AC-004 -> tests/test_opip_relay_remediation_v1.py::test_ac_004_a_named_head_must_be_verified_or_the_request_is_stale
AC-005 -> tests/test_opip_relay_remediation_v1.py::test_ac_005_the_longest_accepted_id_still_gets_an_ack_and_nothing_crashes
AC-005 -> tests/test_opip_relay_remediation_v1.py::test_ac_005_an_id_too_long_to_acknowledge_is_rejected_at_parse
AC-005 -> tests/test_opip_relay_remediation_v1.py::test_ac_005_a_reply_that_cannot_be_built_is_recorded_not_raised

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_relay_remediation_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-REMEDIATION-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_relay_remediation_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-REMEDIATION-V1.md
AC-003 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_relay_remediation_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_engineering_relay_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-REMEDIATION-V1.md
AC-004 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_relay_remediation_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-REMEDIATION-V1.md
AC-005 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_relay_remediation_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-REMEDIATION-V1.md

DEFERRED DISCOVERIES:
- NOTED: the relay still runs only when something invokes it (no poller); GitHub comments from the single owner account remain communication, never approval.

UNAPPROVED SCOPE CHANGES:
NONE
