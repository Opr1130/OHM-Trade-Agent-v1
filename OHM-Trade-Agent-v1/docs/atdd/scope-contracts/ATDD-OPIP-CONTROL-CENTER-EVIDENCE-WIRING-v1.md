INCREMENT:
ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1

OWNER-APPROVED INTENT:
The owner directs an evidence-first recovery of the O'Pip Control Center V2 after the
November 8 production review of the deployed release 88fba758. The console answered HTTP
200 and reported "signal is aborted without reason" on the Overview, Intelligence and
Data & Retention workspaces, "AI advisory NO_EVIDENCE" beside an unavailable zero-BUY
explanation, "F11 verification NOT ATTESTED", "derived projection not readable", and a
strategy-administration reference document declaring scoring.production=v1 against a
runtime that selects v2. The increment does not convert a UI symptom into a backend root
cause. It makes three conflations impossible to repeat and bounds the two readers that sit
in the authenticated request path: (1) a read that ran out of its budget was surfaced as
the browser's own abort text and could not be told apart from a refusal, an absent
capability or an empty evidence set; (2) "no AI evidence" meant "no AI source is
registered", so a missing projection was indistinguishable from a provider that was never
called, even though the Chief router writes one durable usage row per invocation; (3) a
reference document was indistinguishable from an activation. It also removes two
hard-coded display constants (the Overview F11 verdict and the qualified/rejected KPI
rows) and stops the Data workspace from asserting a zero quarantine backlog it cannot
observe. No trading authority, alert path, threshold, weight, fee, slippage or economic
assumption is changed. No deployment, merge, funded trading, paper admission, learning,
committee authority, F11 change or advisory activation is authorized by this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/app/opip/analytics/direct.py: bounded read-through to canonical evidence and its absence states.
- OHM-Trade-Agent-v1/app/services/opip_ai_router.py: the single provider invocation and its usage ledger (ROUTER_USAGE_FILE).
- OHM-Trade-Agent-v1/app/api/control_center.py: the authenticated read API, its fallbacks and its reason codes.
- OHM-Trade-Agent-v1/app/opip/decision/store.py: the qualification streams and their read models.
- OHM-Trade-Agent-v1/app/opip/decision/explanations.py: the "why zero trades?" read model that consumes those readers.
- OHM-Trade-Agent-v1/app/opip/config/staging.py: effective-value readback and the inert staging surface.
- OHM-Trade-Agent-v1/app/jobs/opip_analytics_maintenance.py: the deliberately unscheduled projection maintenance entrypoint.
- OHM-Trade-Agent-v1/control-center-v2/src/api.mjs and src/main.jsx: the console's read contract and rendering.
- OHM-Trade-Agent-v1/docs/CONTROL_CENTER_V2_ARCHITECTURE.md: the projection-to-surface map.
- AGENTS.md: never fabricate evidence, never infer success from absence, keep the advisory and execution boundaries closed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the Chief router usage ledger holds recorded invocations, including failures with error codes, and the projection holds no inference rows
WHEN: the Control Center asks for AI inference evidence
THEN: the surface answers MEASURED from the ledger with inferences, succeeded, failed, the failure-code counts, the p50 latency and the models, keeps matured_outcomes, win_rate, false_signal_rate and missed_opportunity_rate unknown with reason NO_MATURED_OUTCOME_EVIDENCE, distinguishes an absent ledger (NO_AI_USAGE_LEDGER) from an empty one (NO_AI_INVOCATION_RECORDED), and tolerates a truncated final row without losing the readable rows.

AC-002:
GIVEN: an authenticated read does not answer inside the console's read budget
WHEN: the console classifies the failure
THEN: the read is reported as TIMEOUT with an explicit "READ TIMEOUT after <budget> ms" message naming the endpoint, a caller-cancelled read stays an AbortError and is not reported as a failure, an HTTP refusal is reported as CREDENTIAL_REFUSED or HTTP_<status> and never as neutral or healthy, and no browser abort string is displayed as a state of the system.

AC-003:
GIVEN: the derived analytical projection is not readable
WHEN: the Data & Retention surface is answered from canonical evidence
THEN: quarantine backlog, aging and schema version are reported as unknown rather than zero or empty, the projection's absence carries a reason code and names its unscheduled maintenance entrypoint, and no figure is presented as a reconciled projection value.

AC-004:
GIVEN: the qualification streams are bounded at 64 MiB / 50,000 rows for funnel events and 8 MiB / 10,000 rows for scan summaries
WHEN: the zero-trade explanation reads the latest scan summary and that scan's funnel rows
THEN: each read uses a documented tail window, the newest summary and the requested scan's rows are still returned in stream order, a truncated final row is skipped rather than fatal, and the request cost no longer scales with the stream size.

AC-005:
GIVEN: the reference profile declares scoring.production=v1 while the deployment selects OPIP_PRODUCTION_SCORING_VERSION=v2
WHEN: the configuration read model is assembled
THEN: the declared value, the shadow, the target and the runtime selector are reported separately with their own sources, the reconciliation state is REFERENCE_DRIFT (MATCH when equal, UNVERIFIED when the selector is unset and never read as agreement), mutates_runtime is false, and console_can_activate remains false.

AC-006:
GIVEN: the runtime reports a protection artifact and the health payload reports a qualification source
WHEN: the Overview renders its KPI cards
THEN: the F11 verdict is derived from the recorded protection state (ARMED, TRIGGERED_HALT, OVERRIDE_SHADOW or an explicit UNVERIFIED) and the qualified/rejected rows are derived from the payload's qualification source, with no hard-coded verdict label or UNAVAILABLE constant left in either place.

EXPLICITLY OUT OF SCOPE:
- Deployment, merge, rollback, release-profile change or strategy-profile activation.
- Funded trading, live exchange execution, learning activation, paper admission, committee authority or IRR activation.
- Any change to F11 protection, fixed risk sizing, execution-path minimum reward/risk defaults, position limits or risk ceilings.
- Any threshold, rubric weight, fee, slippage, liquidity, candidate-budget or economic-assumption change.
- Scheduling the analytical projection, running ingestion, creating or migrating the derived database, or adding a scheduler/deployment path.
- Registering a new ingestion source or record kind for AI, alert or qualification evidence.
- Re-deriving or claiming the November 8 production counts.

FROZEN BOUNDARIES:
- V2 production / V1 observation selection is unchanged; OPIP_PRODUCTION_SCORING_VERSION remains the only selector.
- The bundling reference profile app/core/strategy_policy.json is not modified, and its validator keeps refusing a production cutover.
- The control center read API stays read-only: reader connections stay mode=ro, and no GET writes canonical evidence or the projection.
- Operator authentication, the dual-credential strategy-admin authority, and secret handling are unchanged; console credentials stay in memory only.
- Alert, notifier, action-gate and execution paths are untouched, including every suppression reason.
- Advisory activation remains unavailable from this surface; console_can_activate stays false.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_chief_evidence_is_read_from_the_router_ledger
AC-001 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_ai_absence_states_are_distinguished_and_never_claim_success
AC-001 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_a_truncated_ledger_tail_does_not_hide_the_readable_rows
AC-002 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_the_console_classifies_a_timed_out_read_instead_of_leaking_an_abort
AC-003 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_data_coverage_does_not_assert_a_zero_quarantine
AC-004 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_scan_summary_read_is_bounded_and_still_returns_the_newest_row
AC-004 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_funnel_rows_are_read_from_a_bounded_tail_for_the_requested_scan
AC-004 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_a_truncated_funnel_tail_row_is_skipped_rather_than_fatal
AC-005 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_scoring_authority_is_reconciled_without_activating_anything
AC-005 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_config_overview_reports_the_runtime_selector_and_keeps_activation_closed
AC-006 -> tests/test_opip_control_center_v2_evidence_wiring_v1.py::test_the_console_derives_f11_and_qualification_instead_of_hardcoding_them

IMPLEMENTATION MAP:
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
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/decision/store.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/config/staging.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_evidence_wiring_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-EVIDENCE-WIRING-v1.md

DEFERRED DISCOVERIES:
No production artefact was retrievable from this workstation: there is no SSH path to the
trading droplet, no mounted /app/data and no exported control-center payload, so the
November 8 counts cannot be re-derived here and are not claimed to be. Three questions are
deferred to evidence this increment makes retrievable instead of being answered by
inference: (1) which endpoint exceeded the read budget, answered by the classified TIMEOUT
message that names the endpoint and the budget; (2) whether the Chief was invoked on the
scan behind the zero-BUY report, answered by the router usage ledger through
/api/opip/control-center/ai; (3) which deterministic gate refused the shortlist, answered by
the zero-trade explanation once its readers are bounded. Two gaps are declared and left
open: the projection remains unscheduled by design, so populating it is an owner-authorized
deployment decision rather than a code change, and alert/Telegram delivery and suppression
evidence is still reachable only through the legacy intelligence read model rather than a
Control Center V2 surface of its own.

UNAPPROVED SCOPE CHANGES:
NONE


