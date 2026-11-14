INCREMENT:
ATDD-OPIP-RELAY-ACK-CANDIDATES-V1

OWNER-APPROVED INTENT:
Resolve the Codex P2 finding left on merged PR #578: when several comments carry the same ACK message id, a later non-genuine one overwrites a genuine ACK, so a restart posts a second ACK. The communication-only relay is not activated, gains no authority and is not a release prerequisite.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md: transport ADR, trust model and remediation.
- OHM-Trade-Agent-v1/AGENTS.md: external AI is advisory; deterministic gates fail closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a genuine ACK from this relay for a request and, in the same comment list, other comments carrying the identical ACK message id, whether they appear before or after the genuine one
WHEN:
the relay decides whether the request was already answered after local state was lost
THEN:
the genuine ACK is still recognised, the request is marked answered and no second ACK is posted

AC-002:
GIVEN:
several comments carrying the expected ACK message id, none of which is a genuine ACK from this relay addressed to the requester and naming the request
WHEN:
the relay decides whether the request was already answered
THEN:
none of them suppresses the legitimate ACK, which is posted

EXPLICITLY OUT OF SCOPE:
- Activating or scheduling the relay, issue 291, secrets or host installs.
- Any change to the canonical writer, deploy scripts, F11, trading, learning or Committee.

FROZEN BOUNDARIES:
- Comments are untrusted data; GitHub identity is a single account so sender carries no authority.
- authority must remain COMMUNICATION_ONLY; the module stays stdlib only with no execution primitive.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_relay_ack_candidates_v1.py::test_ac_001_a_genuine_ack_survives_same_id_decoys_in_either_order
AC-002 -> tests/test_opip_relay_ack_candidates_v1.py::test_ac_002_only_decoys_with_the_ack_id_never_suppress_the_real_ack

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_relay_ack_candidates_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-ACK-CANDIDATES-V1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_relay_ack_candidates_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-RELAY-ACK-CANDIDATES-V1.md

DEFERRED DISCOVERIES:
- NOTED: the relay still runs only when something invokes it; same-account comments remain communication, never approval.

UNAPPROVED SCOPE CHANGES:
NONE
