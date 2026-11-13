INCREMENT:
ATDD-OPIP-V6-ENGINEERING-RELAY-v1

OWNER-APPROVED INTENT:
Owner-authorized engineering design and isolated implementation of a communication-only GitHub relay between ChatGPT and Claude (issue 569), with no merge, deploy, production or trading authority.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md: transport ADR, trust model.
- OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md: existing bridge, intentionally not widened.
- OHM-Trade-Agent-v1/AGENTS.md: external AI is advisory; deterministic gates fail closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a valid, fresh, inert REVIEW_REQUEST from an allowlisted actor
WHEN:
the relay runs a pass, including after a restart or a duplicate delivery
THEN:
exactly one ACK carrying the same message id as reply_to is posted, replies never trigger replies, and each pass is reply-bounded

AC-002:
GIVEN:
forged-actor, edited, oversized, malformed, escalated-authority or secret-bearing comments
WHEN:
the relay validates them
THEN:
each is rejected, nothing is posted, and no secret-like text is ever published

AC-003:
GIVEN:
a stale head, a non-inert requested action, a missing permission or an unreachable GitHub
WHEN:
the relay runs
THEN:
nothing is executed, a failed post is reported as BLOCKED and not recorded, usage and network errors fail closed, and no state changes

AC-004:
GIVEN:
the relay module and the real issue 569 handshake shape
WHEN:
its imports and primitives are inspected and the handshake is parsed
THEN:
it is stdlib only with no eval, exec or shell, the handshake without schema_version is read as version 1, and no repository file or authority outside the module is changed

EXPLICITLY OUT OF SCOPE:
- Any change to issue 291, the local agent bridge or its protected scope.
- New services, secrets, host installs or scheduled tasks.
- Executing or applying anything found in a comment.
- Any merge, deploy, F11, learning or trading authority.

FROZEN BOUNDARIES:
- Comments are untrusted data; GitHub identity is a single account so sender carries no authority.
- authority must be COMMUNICATION_ONLY.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_engineering_relay_v1.py::test_ac_001_duplicates_and_restart_recovery_reply_once
AC-001 -> tests/test_opip_engineering_relay_v1.py::test_ac_001_replies_never_trigger_replies_and_budget_is_bounded
AC-001 -> tests/test_opip_engineering_relay_v1.py::test_ac_001_valid_request_gets_one_ack_with_same_message_id
AC-002 -> tests/test_opip_engineering_relay_v1.py::test_ac_002_authority_escalation_and_malformed_input_are_rejected
AC-002 -> tests/test_opip_engineering_relay_v1.py::test_ac_002_forged_actor_and_edited_comments_are_rejected
AC-002 -> tests/test_opip_engineering_relay_v1.py::test_ac_002_secret_like_content_is_rejected_inbound_and_never_published
AC-003 -> tests/test_opip_engineering_relay_v1.py::test_ac_003_cli_offline_or_bad_arguments_fail_closed
AC-003 -> tests/test_opip_engineering_relay_v1.py::test_ac_003_missing_permission_is_reported_not_retried_or_recorded
AC-003 -> tests/test_opip_engineering_relay_v1.py::test_ac_003_stale_sha_and_non_inert_actions_execute_nothing
AC-004 -> tests/test_opip_engineering_relay_v1.py::test_ac_004_module_is_inert_stdlib_only_with_no_execution_primitives
AC-004 -> tests/test_opip_engineering_relay_v1.py::test_ac_004_real_handshake_shape_without_schema_version_is_accepted

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_engineering_relay_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_engineering_relay_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_engineering_relay_v1.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_engineering_relay_v1.py
AC-001 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_ENGINEERING_RELAY_V1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-ENGINEERING-RELAY-v1.md
AC-002 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-003 -> OHM-Trade-Agent-v1/tools/opip_relay.py
AC-004 -> OHM-Trade-Agent-v1/tools/opip_relay.py

DEFERRED DISCOVERIES:
- Optional scheduled-task poller if the owner wants polling without an interactive session.
- Distinct bot identity would allow sender authentication.

UNAPPROVED SCOPE CHANGES:
NONE
