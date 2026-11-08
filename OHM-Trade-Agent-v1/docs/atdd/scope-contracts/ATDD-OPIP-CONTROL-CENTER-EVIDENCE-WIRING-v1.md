INCREMENT:
ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1

OWNER-APPROVED INTENT:
The owner directs an evidence-first recovery of the O'Pip Control Center V2 after the
November 8 production review. The deployed release (88fba758) answers HTTP 200, but the
console reported "signal is aborted without reason" on the Overview, Intelligence and
Data & Retention workspaces, "AI advisory NO_EVIDENCE" and "AI inference evidence
NO_EVIDENCE", "F11 verification NOT ATTESTED", "zero-BUY explanation unavailable" and
"derived projection not readable", while the strategy-administration reference document
declared scoring.production=v1 against a runtime that selects v2.

The increment does not assume a backend root cause from a UI symptom. It makes the three
conflations in that report impossible to repeat and bounds the two readers that sit in the
authenticated request path:

1. a read that ran out of its budget was surfaced as the browser's own abort text, and
   could not be told apart from a refusal, an absent capability or an empty evidence set;
2. "no AI evidence" meant "no AI source is registered", so an operator could not tell a
   missing projection from a provider that was never called, even though the Chief router
   writes one durable usage row per invocation;
3. a reference document was indistinguishable from an activation, so declaring
   production=v1 looked like a live contradiction of the deployed v2 selector.

It also removes two hard-coded display constants (the Overview F11 verdict and the
"Qualified signals" KPI) that could never show recorded evidence, and stops the Data
workspace from asserting a zero quarantine backlog it cannot observe.

Scope boundaries, all preserved: no funded order, no alert path, no paper admission, no
learning, no committee authority, no F11 change, no advisory activation, no threshold,
weight, fee, slippage or economic-assumption change, no canonical write, no projection
created or migrated, no scheduler entry, no new deployment path. Reads stay read-only and
the projection remains owned by `app.jobs.opip_analytics_maintenance`.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/opip/analytics/direct.py: bounded read-through to canonical evidence.
- OHM-Trade-Agent-v1/app/services/opip_ai_router.py: the single provider invocation and its usage ledger (ROUTER_USAGE_FILE).
- OHM-Trade-Agent-v1/app/api/control_center.py: the authenticated read API, its fallbacks and its absence states.
- OHM-Trade-Agent-v1/app/opip/decision/store.py: the qualification streams and their read models.
- OHM-Trade-Agent-v1/app/opip/decision/explanations.py: the "why zero trades?" read model that consumes those readers.
- OHM-Trade-Agent-v1/app/opip/config/staging.py: effective-value readback and the inert staging surface.
- OHM-Trade-Agent-v1/app/jobs/opip_analytics_maintenance.py: the deliberately unscheduled projection maintenance entrypoint.
- OHM-Trade-Agent-v1/control-center-v2/src/api.mjs, src/main.jsx: the console's read contract and rendering.
- OHM-Trade-Agent-v1/docs/CONTROL_CENTER_V2_ARCHITECTURE.md: the projection-to-surface map.
- AGENTS.md: never fabricate evidence, never infer success from absence, keep the advisory and execution boundaries closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the Chief router usage ledger contains recorded invocations, including failures with error codes
WHEN: the Control Center asks for AI inference evidence and the projection has no inference rows
THEN: the surface answers MEASURED from the ledger, reports inferences, succeeded, failed, the failure-code counts, the p50 latency and the models, and reports matured_outcomes, win_rate, false_signal_rate and missed_opportunity_rate as unknown with reason NO_MATURED_OUTCOME_EVIDENCE; an absent ledger and an empty ledger are distinct NO_EVIDENCE reasons, and neither is rendered as a successful interaction.

AC-002:
GIVEN: an authenticated read does not answer inside the console's read budget
WHEN: the console classifies the failure
THEN: the read is reported as TIMEOUT with an explicit "READ TIMEOUT after <budget> ms" message that names the endpoint, a caller-cancelled (superseded) read remains an AbortError and is not reported as a failure, and no browser abort string is ever displayed as a state of the system; a timeout renders as a negative state, never as neutral or healthy.

AC-003:
GIVEN: the derived analytical projection is not readable
WHEN: the Data & Retention workspace is rendered
THEN: quarantine backlog, aging and schema version are reported as unknown rather than zero or empty, the projection's absence is explained by naming its unscheduled maintenance entrypoint, and no figure is presented as a reconciled projection value.

AC-004:
GIVEN: the qualification streams are bounded at 64 MiB / 50,000 rows for funnel events and 8 MiB / 10,000 rows for scan summaries
WHEN: the zero-trade explanation reads the latest scan summary and that scan's funnel rows
THEN: each read is bounded to a documented tail window, the newest summary and the requested scan's rows are still returned in stream order, a truncated final row is skipped rather than fatal, and the read cost no longer scales with the stream size.

AC-005:
GIVEN: the reference profile declares scoring.production=v1 while the deployment selects OPIP_PRODUCTION_SCORING_VERSION=v2
WHEN: the configuration read model is assembled
THEN: the declared value, the shadow, the target and the runtime selector are reported separately with their own sources, the reconciliation state is REFERENCE_DRIFT (MATCH when equal, UNVERIFIED when the selector is unset and never read as agreement), mutates_runtime is false, and console_can_activate remains false.

AC-006:
GIVEN: the runtime reports a protection artifact and the projection reports qualification counts
WHEN: the Overview renders its KPI cards
THEN: the F11 verdict is derived from the recorded protection state (ARMED, TRIGGERED_HALT, OVERRIDE_SHADOW or an explicit UNVERIFIED) instead of a hard-coded label, and the qualified/rejected KPI rows are rendered from the payload's qualification source instead of a hard-coded UNAVAILABLE.

AC-007:
GIVEN: every change in this increment
WHEN: the focused suites are run
THEN: tests/test_opip_control_center_v2_evidence_wiring_v1.py, the Control Center acceptance, M02, scope, round-2 and edge suites, the qualification funnel integration and decision-engine suites, the UI config isolation suites and the console's node test suite all pass, `python -m compileall -q app` is clean, and `npm run build` produces the V2 bundle.

TRACEABILITY:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/direct.py
AC-001 -> OHM-Trade-Agent-v1/app/api/control_center.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_acceptance.py
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-002 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/analytics/direct.py
AC-003 -> OHM-Trade-Agent-v1/app/api/control_center.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/decision/store.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/config/staging.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1.md
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
No production artefact was retrievable from this workstation: there is no SSH path to the
trading droplet, no mounted /app/data, and no exported control-center payload, so the
November 8 counts cannot be re-derived here and are not claimed to be. Three questions are
therefore deferred to evidence this increment now makes retrievable rather than answered by
inference: (1) which endpoint exceeded the read budget, answered by the classified TIMEOUT
message that names the endpoint and the budget; (2) whether the Chief was invoked on the
scan that produced the zero-BUY report, answered by the router usage ledger through
/api/opip/control-center/ai; and (3) which deterministic gate refused the shortlist,
answered by the zero-trade explanation once its readers are bounded. The projection remains
unscheduled by design: populating it is an owner-authorized deployment decision, not a code
change, and the workspace now names that blocker explicitly.

UNAPPROVED SCOPE CHANGES:
NONE

