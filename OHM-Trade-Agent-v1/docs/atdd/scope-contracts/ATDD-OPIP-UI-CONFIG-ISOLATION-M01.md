INCREMENT:
ATDD-OPIP-UI-CONFIG-ISOLATION-M01

OWNER-APPROVED INTENT:
Finish the existing Control Center V2 configuration functionality without changing trading authority. A legacy strategy-admin read failure must not hide the independent schema-driven configuration registry. Async configuration discovery must select a valid domain, and failed stage attempts must retain operator edits. Only UI presentation, configuration interactions and focused tests change; no merge, deployment or activation is authorized by this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/control-center-v2/src/main.jsx: separated registry staging and legacy advisory administration.
- OHM-Trade-Agent-v1/control-center-v2/src/api.mjs: explicit same-origin viewer and administrator authenticated API requests.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-V2-M02.md: prior configuration staging remains inert and server-validated.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: scoped acceptance and modified-file enforcement.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the independent configuration registry and a legacy strategy-admin API that is absent or refuses credentials
WHEN:
the operator opens Config Admin or retries legacy credentials
THEN:
the registry remains available where its own API provided a valid payload, the legacy failure is named and can be retried with a memory-only credential, and no unavailable activation or rollback action is presented

AC-002:
GIVEN:
configuration domains arrive after initial rendering and staging might be refused
WHEN:
the operator selects a domain or stages a draft with a failed response
THEN:
the first valid domain is selected only when available, valid operator selection is preserved, domain-specific edits clear on switching domains, and a failed stage response retains unsaved edits while success alone clears them

EXPLICITLY OUT OF SCOPE:
- Production deployment, merge, backend changes or environment edits.
- Credential storage, retrieval or alteration.
- Activation of advisory configurations, learning, trading, paper, F11 or funded authority.
- Scanner scheduling, qualification, Tradevisor and Nginx changes.

FROZEN BOUNDARIES:
- The independent server-side registry remains the authoritative schema; staging has no runtime effect.
- All API requests remain same-origin and authenticated, with server validation.
- No dashboard action can place orders or bypass deterministic gates; F11, canonical evidence and funded isolation are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_ui_config_isolation_m01.py::test_legacy_failure_keeps_registry_and_safe_retry
AC-002 -> tests/test_opip_ui_config_isolation_m01.py::test_async_domain_and_rejected_stage_preserve_work

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/test/config-capability-isolation.test.mjs
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_ui_config_isolation_m01.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-UI-CONFIG-ISOLATION-M01.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/test/config-capability-isolation.test.mjs
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_ui_config_isolation_m01.py

DEFERRED DISCOVERIES:
- The GitHub dashboard review and focused test job passed at commit c986685d; the prior ATDD scope gate refused only absent modified-file mappings.
- Production dashboard exposure and real server-side registry data require an independently authorized UI-only release and postcutover validation.

UNAPPROVED SCOPE CHANGES:
NONE
