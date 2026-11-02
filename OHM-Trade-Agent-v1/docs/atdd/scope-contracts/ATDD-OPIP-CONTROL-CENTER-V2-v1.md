INCREMENT:
ATDD-OPIP-CONTROL-CENTER-V2-v1

OWNER-APPROVED INTENT:
Owner's November 2, 2026 go-ahead after Control Center review CI authorized finishing the operator dashboard and configuration administration, while retaining the legacy cockpit and preserving trading safety boundaries. This increment brings the existing dashboard candidate into repository-native, isolated, build-only PR review. The scope here is explicitly review/test/certification, not final promotion, funded-trading activation, architectural authority, canonical-writer replacement, runtime deployment, or deletion. It supersedes the historical scoring increment as the active *change scope* without revoking its prior approvals or changing its acceptance criteria.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md: F10 EXTEND, canonical writer and safety constraints.
- docs/architecture/OPIP_F10_DASHBOARD_PARITY_SPEC.md: eight-page parity NOT_VERIFIED until canonical reconciliation.
- app/api/dashboard.py: existing authenticated operator and protected advisory-admin endpoints.
- app/api/cockpit_semantic.py and app/api/cockpit_service.py: analytics-plane read-only semantic service; not rewritten or mounted here.
- app/opip/data_platform/README or deploy/analytics/README.md: separate protected analytics platform; not deployed by this change.
- tests/atdd_scope.py: the unmodified file-mapping and acceptance-check authority.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the existing O'Pip authenticated read-only operator endpoints, and no verified V1/V2 paired comparisons
WHEN:
the React Control Center reads the endpoints and renders them
THEN:
it displays actual returned read evidence with clear NO_EVIDENCE/UNAVAILABLE states, never fabricates profit or score authority, and its API client rejects external destinations and missing operator credentials.

AC-002:
GIVEN:
a protected production advisory-only strategy-admin API and separate admin credentials
WHEN:
an operator loads, edits, stages, activates or rolls back an advisory policy through the Control Center
THEN:
the UI uses existing dual credential headers and expected SHA256 optimistic concurrency, requires explicit advisory acknowledgments, does not persist secrets in browser storage, and changes no funded, F11, execution or production authority.

AC-003:
GIVEN:
the review candidate and its exact source dependencies
WHEN:
GitHub Actions exercises source tests, builds the static Vite output and executes the browser smoke test
THEN:
the workflow has read-only permissions and no deployment/SSH secrets, uses npm ci from the checked-in lockfile, publishes a build-only artifact, and the browser confirms visible evidence and safe admin behavior without any production connection.

AC-004:
GIVEN:
the original scoring increment, the dedicated dashboard scope and the repository acceptance gate
WHEN:
CI checks this PR's changed-file set
THEN:
each file is mapped to an approved criterion, the exact active increment points to this contract, the prior scoring contract remains intact and traceable, no acceptance check or architecture gate is removed, and the legacy dashboard is not retired.

EXPLICITLY OUT OF SCOPE:
- Live deployment, Nginx routing, database provisioning or cleanup activation; any production-side change requires separate certified release.
- Changing F11, trading authorization, scanner, deterministic ranking, paper routing, Telegram, learning configuration or AI execution.
- Declaring F10 parity or all eight pages implemented. The existing F10 parity and consumer census gates remain in force.
- Self-service editing outside the allowlisted advisory-policy fields.
- Any unverified live health or profit claims.

FROZEN BOUNDARIES:
Canonical evidence remains with its current writer; F11 remains independent; funded trading authority is absent. No new SQL/JSONL production authority and no data deletion. All UI interactions must use documented existing authenticated read/advisory API contracts. Older dashboard routes stay available. Draft PR is not deployed by CI.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_control_center_v2_scope.py::test_read_only_evidence_and_no_credential_bypass
AC-002 -> tests/test_opip_control_center_v2_scope.py::test_protected_advisory_only_workflow
AC-003 -> tests/test_opip_control_center_v2_scope.py::test_networked_build_only_ci
AC-004 -> tests/test_opip_control_center_v2_scope.py::test_active_scope_and_legacy_are_preserved

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/index.html
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-001 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/e2e/smoke.spec.mjs
AC-003 -> .github/workflows/opip-control-center-v2-ci.yml
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/package.json
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/package-lock.json
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/vite.config.js
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/playwright.config.mjs
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/src/style.css
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/e2e/smoke.spec.mjs
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/README.md
AC-004 -> OHM-Trade-Agent-v1/control-center-v2/README.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_scope.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_strategy_scoring_scope_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-V2-v1.md

DEFERRED DISCOVERIES:
- The existing PostgreSQL analytical plane's live activation and capacity must be verified independently before choosing whether to reuse it; this PR starts no database.
- Live semantic eight-page parity, historical reconciliation and resource measurements are not yet established.
- Missing scoring comparison evidence requires an independent production trace before the UI may advertise comparisons as complete.

UNAPPROVED SCOPE CHANGES:
NONE
