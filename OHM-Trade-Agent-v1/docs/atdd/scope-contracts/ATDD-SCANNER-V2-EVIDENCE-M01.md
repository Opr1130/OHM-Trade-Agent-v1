INCREMENT:
ATDD-SCANNER-V2-EVIDENCE-M01

OWNER-APPROVED INTENT:
2026-11-03 owner-observed production defect: eleven recorded V1/V2 comparisons returned V2-ready = 0, V2 shortlist = 0 and 96 sampled NOT_COMPUTED statuses, while the V1 observation leg still shortlisted 8. Round 2 authorized the repair end to end (image provenance, governed release, fail-closed V2 evidence, downstream validation, Control Center accuracy) under fixed boundaries: no funded trading or learning activation, no F11, scoring-threshold or risk-policy change, no canonical-history rewrite and no unnecessary restarts.
The confirmed root cause is release-shaped, not scoring-shaped: `opip-scan-worker` is a separate Compose service with its own image and the release controller never built it (`git log -S scanner -- deploy/remote/ohm-deploy` is empty), so the image froze at a 2026-11-01 build that predates the V2 evidence fields (`fc929b53`) and never carried the V2 scoring authority added for it in `d39cf4a1` (2026-11-02). The worker serialised snapshots without V2 fields, promotion defaulted them to NOT_COMPUTED and the V2-authority selector skipped every row. Nothing failed closed, so the state was indistinguishable from "no opportunities".
This increment therefore (a) makes the governed release own the scanner image with recorded provenance and rollback, (b) makes V2 authority refuse acquisition evidence that carries no V2 scoring with an explicit durable reason, (c) records the worker's scoring provenance so an old image and a missing authority are different facts, and (d) makes the Control Center report only recorded evidence for the same states. No trading authority, threshold, credential, F11, learning, paper or scheduler-cadence change is authorized or made.

ARCHITECTURE REFERENCES:
- deploy/remote/ohm-deploy: the single release controller, its deploy lock, exact-SHA build, receipts and rollback trap.
- .github/workflows/deploy-production.yml: owner-only approval issue (#64), exact main SHA and green-CI gates.
- docker-compose.yml (`opip-scan-worker`): the isolated acquisition service, its profile gating, read-only mounts, memory cgroup and ephemeral lifecycle.
- app/services/scan_result_promotion.py and app/services/scan_promotion_runner.py: the only seam from acquisition evidence to qualification.
- app/scanner/directional_candidates.py and app/jobs/scan_opportunities.py: V2 production authority and its fail-closed selection.
- app/scanner/market_scanner.py and app/scanner/v2_features.py: where V2 evidence is computed, in the only process holding completed candles.
- control-center-v2 (src/api.mjs, src/main.jsx) and app/api/scoring_comparison.html: the read-only operational surfaces.
- tests/atdd_scope.py: the unmodified file-mapping and acceptance-check authority.


APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the governed release controller and a release that declares the isolated `opip-scan-worker` service
WHEN:
`ohm-deploy` builds a candidate release
THEN:
it builds the scanner image from the same approved `$TARGET_SHA`, proves that image carries the V2 evidence schema and that `org.opencontainers.image.revision` equals the approved SHA before anything is recreated, records the immutable image ID, revision, schema verdict and previous image in the receipt and a root-only state file, and restores the scanner from `$PREVIOUS_SHA` on both rollback paths.

AC-002:
GIVEN:
a core running with V2 production authority
WHEN:
the promoted acquisition evidence carries no V2 scoring, either because every row is `NOT_COMPUTED` or because every row raised
THEN:
promotion refuses it with the durable reasons `V2_EVIDENCE_ABSENT` or `V2_EVIDENCE_ERROR`, never falls back to V1 ranking and never produces a silently empty shortlist; a V1-authority release is unaffected.

AC-003:
GIVEN:
acquisition evidence that was scored under V2
WHEN:
it is serialised, verified and promoted back into the qualification path
THEN:
the V2 long/short scores, feature status, profile digest and component evidence survive the round trip unchanged and the V2 selector can select a genuine above-threshold score, while a population that was attempted and is merely short of inputs remains consumable and simply selects nothing.

AC-004:
GIVEN:
an isolated acquisition attempt
WHEN:
the worker writes its durable status
THEN:
it records the production scoring version it ran with, whether V2 evaluation was enabled, whether the running image's snapshot schema declares the V2 fields at all, and a per-status census, so "the image is too old" and "the attempt ran without the scoring authority" can never again be the same silent observation.

AC-005:
GIVEN:
the read-only Control Center and scoring page
WHEN:
they display scans, qualification, AI activity or configuration administration
THEN:
every figure is read from a recorded payload and an unreturned field is shown as unverified rather than zero, the qualification funnel and rejection breakdown name where candidates stopped, the six AI states stay distinct, a 404 strategy-administration capability is represented as unavailable instead of as an empty form or a credential failure, timestamps are UTC with the local rendering beside them, the credential collapses after an accepted read and is never written to browser storage, and raw JSON remains only as an optional advanced view.


EXPLICITLY OUT OF SCOPE:
- Any funded trading, order placement, exchange credential or paper/live authority change.
- Learning enablement, committee activation, analytics-plane moves or PostgreSQL on the trading host.
- F11 protection state, risk sizing, minimum reward-to-risk, scoring thresholds or strategy policy.
- Retrying, rewriting or "cleaning up" the eleven historical comparison records and their NOT_COMPUTED rows; they are the audit trail of this defect.
- Changing the scan cadence, the scheduler, the admission-token or fencing mechanism, the scanner resource limits or its ephemeral lifecycle.
- Enabling the advisory strategy profile (a separate latent cross-worker digest risk is recorded below, not activated).

FROZEN BOUNDARIES:
- `deploy/remote/ohm-deploy` remains the only release controller; no second deployment mechanism, no CI/CD redesign.
- Owner-only approval, exact main SHA, exact-SHA pytest CI and the forced-command SSH gateway are unchanged.
- The isolated worker keeps no `env_file`, no funded credential, no Telegram token, no AI provider key, its read-only mounts, its own memory cgroup and its own ephemeral container.
- AI stays advisory and subordinate to deterministic gates; no paid provider call is made by this increment.
- Canonical writer mode, release profile, paper mode and every other activation literal are unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_scan_v2_evidence_v1.py::test_release_controller_builds_proves_and_restores_the_scanner_image
AC-001 -> tests/test_opip_scan_v2_evidence_v1.py::test_release_controller_refuses_an_incompatible_scanner_image
AC-002 -> tests/test_opip_scan_v2_evidence_v1.py::test_v2_authority_rejects_a_population_with_no_v2_evidence
AC-002 -> tests/test_opip_scan_v2_evidence_v1.py::test_v2_authority_rejects_a_population_that_errored_everywhere
AC-002 -> tests/test_opip_scan_v2_evidence_v1.py::test_v1_authority_is_unchanged_by_the_v2_gate
AC-003 -> tests/test_opip_scan_v2_evidence_v1.py::test_v2_authority_promotes_a_genuine_v2_score
AC-003 -> tests/test_opip_scan_v2_evidence_v1.py::test_v2_authority_accepts_attempted_but_insufficient_evidence
AC-004 -> tests/test_opip_scan_v2_evidence_v1.py::test_worker_status_records_which_cause_it_hit
AC-004 -> tests/test_opip_scan_v2_evidence_v1.py::test_unconfigured_worker_reports_not_computed_for_every_row
AC-004 -> tests/test_opip_scan_v2_evidence_v1.py::test_scanner_service_carries_the_core_scoring_authority
AC-005 -> tests/test_opip_control_center_v2_round2_v1.py::test_control_center_render_helpers_are_source_backed
AC-005 -> tests/test_opip_control_center_v2_round2_v1.py::test_control_center_never_persists_credentials_and_labels_utc

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/jobs/scan_worker.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-005 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-005 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-005 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-005 -> OHM-Trade-Agent-v1/app/api/scoring_comparison.html
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_round2_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCANNER-V2-EVIDENCE-M01.md
AC-001 -> OHM-Trade-Agent-v1/.gitignore

DEFERRED DISCOVERIES:
- The frozen production scanner image could not be inspected from the engineering workstation (no Docker, no host access); the Nov-1 build date and the empty `-S scanner` history are the evidence, and the post-deploy receipt records the immutable ID, revision and schema verdict so the next inspection is authoritative.
- A latent asymmetry remains unactivated: if `OPIP_ADVISORY_PROFILE_ENABLED` were enabled on the core only, the worker would score under the bundled policy while the core validates the active one, and `V2_SCORING_POLICY_DIGEST_MISMATCH` would be raised out of the detached promotion job rather than recorded as a promotion rejection. Advisory remains off; converting that raise into a recorded rejection belongs to the increment that enables the profile.
- Production READY / INSUFFICIENT_FEATURES / ERROR / NOT_COMPUTED counts for a full 200-row population, the downstream qualification outcome and the Control Center read against live evidence are unverified until one naturally scheduled post-deploy scan is observed; no synthetic trade is generated to shorten that wait.
- The previous scanner image is restored by rebuilding `$PREVIOUS_SHA` rather than by pinning a stored image ID, mirroring the established core/writer rollback pattern; the prior image is never deleted and its ID is recorded when a container happened to be resolvable.

UNAPPROVED SCOPE CHANGES:
NONE
