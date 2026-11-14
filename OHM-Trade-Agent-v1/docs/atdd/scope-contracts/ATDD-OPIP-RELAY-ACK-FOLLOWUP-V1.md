INCREMENT:
ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1

OWNER-APPROVED INTENT:
Resolve the two Codex P2 findings left on merged PR #577 in one narrow follow-up. The communication-only relay is not activated, gains no authority and is not a release prerequisite for the canonical-writer deployment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md: transport ADR, trust model and remediation.
- OHM-Trade-Agent-v1/AGENTS.md: external AI is advisory; deterministic gates fail closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a reply-triggering request whose id is 89 to 92 characters long, so its generated ACK id is 93 to 96 characters
WHEN:
the published ACK is read back after a restart that lost local state
THEN:
the full-length ACK id parses, the request is recognised as answered, and no second ACK is posted, while a request id longer than 92 characters is still refused

AC-002:
GIVEN:
a comment that carries the id of an expected ACK but is not a genuine acknowledgement, for example another kind, another sender, another recipient or another reply_to
WHEN:
the relay decides whether the request was already answered
THEN:
the comment does not suppress the legitimate ACK, while a genuine ACK from this relay, addressed to the requester and naming the request, still does

EXPLICITLY OUT OF SCOPE:
- Activating or scheduling the relay, issue 291, the local agent bridge, secrets or host installs.
- Any change to the canonical writer, deploy scripts, F11, trading, learning or Committee.

FROZEN BOUNDARIES:
- Comments are untrusted data; GitHub identity is a single account so sender carries no authority.
- authority must remain COMMUNICATION_ONLY; the module stays stdlib only with no execution primitive.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_relay_ack_followup_v1.py::test_ac_001_a_full_length_ack_can_be_read_back_so_a_restart_does_not_duplicate_it
AC-001 -> tests/test_opip_relay_ack_followup_v1.py::test_ac_001_the_request_limit_is_unchanged_and_acks_may_use_the_full_limit
AC-002 -> tests/test_opip_relay_ack_followup_v1.py::test_ac_002_an_unverified_comment_with_the_ack_id_does_not_suppress_the_real_ack
AC-002 -> tests/test_opip_relay_ack_followup_v1.py::test_ac_002_a_genuine_published_ack_still_suppresses_a_duplicate

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_relay_ack_followup_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_relay_ack_followup_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-ACK-FOLLOWUP-V1.md

DEFERRED DISCOVERIES:
- NOTED: the relay still runs only when something invokes it; same-account comments remain communication, never approval.

UNAPPROVED SCOPE CHANGES:
NONE
