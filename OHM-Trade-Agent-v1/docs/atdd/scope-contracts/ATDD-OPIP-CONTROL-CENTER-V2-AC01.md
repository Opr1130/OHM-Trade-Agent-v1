INCREMENT:
ATDD-OPIP-CONTROL-CENTER-V2-AC01

OWNER-APPROVED INTENT:
2026-11-04 owner acceptance of production release 08af854636b0a4714e25fd564a3cdd3024420b6d reported two defects: the approved O'Pip logo does not load, and the operational dashboards show little or no data. The first is a release-packaging defect: deploy/control-center/Dockerfile.edge builds the Vite bundle from index.html, vite.config.js and src only, so control-center-v2/public is never copied into the build stage and /dashboard/v2/assets/* 404s. The second is a data-path defect: the analytical projection is populated by a maintenance worker that is not scheduled, so every projection-backed surface reports UNAVAILABLE while the canonical evidence the console needs already exists on disk. This increment ships the public asset into the edge image and adds a bounded, read-only read-through to the same registered canonical sources, clearly labelled CANONICAL_DIRECT / UNPROJECTED, so an operator sees real evidence and a recorded explanation of zero BUY signals instead of an empty console. No trading, F11, learning, scanner-admission or configuration authority changes, and no metric is synthesised.

ARCHITECTURE REFERENCES:
- deploy/control-center/Dockerfile.edge and deploy/control-center/docker-compose.v2.yml: the opt-in edge image and its owner-gated overlay.
- app/opip/analytics/sources.py: the registered-source list the read-through reuses instead of inventing a second one.
- app/opip/analytics/queries.py: classify_signal and the response shapes the read-through must match exactly.
- app/opip/storage/bounded_jsonl.py: the reviewed seek-based tail reader used for every direct read.
- app/services/technical_scoring_comparison.py and app/services/scan_attempt.py: the canonical writers of the comparison ledger and attempt ledger.
- app/opip/decision/explanations.py: the recorded zero-trade explanation the BUY panel renders.
- AGENTS.md and CLAUDE.md: canonical evidence stays immutable; no authority widening; no fabricated operational claims.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the opt-in V2 edge image, the approved transparent brand asset at control-center-v2/public/assets/opip-control-center-logo-transparent.png, and a Vite build that emits public/ verbatim into the served base path
WHEN:
deploy/control-center/Dockerfile.edge builds the image
THEN:
the public directory is copied into the Vite build stage before the build runs, the approved 1120x1635 RGBA artwork is the file that ships, and the logo URL under the dashboard base path resolves to that PNG instead of a 404 that falls back to the missing-asset mark.

AC-002:
GIVEN:
an absent, empty or unreadable analytical projection and canonical evidence that does exist in the registered sources
WHEN:
an authenticated client reads health, signals, forensics, scanner or data
THEN:
each read falls back to a bounded tail read of the same registered canonical sources, the response is labelled source CANONICAL_DIRECT with verification UNPROJECTED and states the projection status and reason that caused the fallback, uncomputed values stay null rather than zero, and no width of evidence is claimed beyond what was read.

AC-003:
GIVEN:
a scan that produced no BUY signal and the recorded zero-trade explanation
WHEN:
the operator opens the overview
THEN:
the console renders the recorded status, dominant terminal gate, deepest stage reached, per-stage counts, qualification and paper-admission counts and the recorded rejection classes, marks stages the explanation did not return as not recorded, and never invents a recommendation, alert or score to explain the absence.

AC-004:
GIVEN:
the read-through layer and any authenticated read
WHEN:
it answers instead of the projection
THEN:
it performs no write of any kind: canonical evidence is byte- and mtime-identical afterwards, no projection database is created or mutated, no trading, F11, learning, scanner-admission or configuration authority is consulted or changed, and funded-order authority remains absent.

EXPLICITLY OUT OF SCOPE:
- Merging, deploying, or invoking any release controller.
- Changing F11 protection, entry admission, scanner execution mode or trading authority.
- Scheduling the analytical ingestion worker (that changes the trading-host runtime shape and needs its own approved change).
- Registering new evidence sources (AI inference, qualification, promotion, outcome maturation): they remain explicitly unavailable.
- Rewriting, compacting or deleting canonical JSONL.
- Retroactively editing the previously merged M02 scope contract.

FROZEN BOUNDARIES:
Canonical JSONL stays authoritative, append-only and never written by a read path. The projection remains derived and is not populated by this increment. No funded trading, no F11 override, no learning or Committee activation. Secrets remain out of the read path. Retention deletion stays disabled.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_control_center_v2_acceptance.py::test_edge_image_ships_the_approved_public_asset
AC-002 -> tests/test_opip_control_center_v2_acceptance.py::test_read_through_answers_with_real_evidence_when_the_projection_is_absent
AC-002 -> tests/test_opip_control_center_v2_acceptance.py::test_api_serves_canonical_evidence_when_the_projection_is_missing
AC-003 -> tests/test_opip_control_center_v2_acceptance.py::test_zero_buy_surface_explains_from_recorded_qualification_evidence
AC-004 -> tests/test_opip_control_center_v2_acceptance.py::test_read_through_is_read_only_and_preserves_authority

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/control-center/Dockerfile.edge
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_acceptance.py
AC-001 -> OHM-Trade-Agent-v1/docs/CONTROL_CENTER_V2_ARCHITECTURE.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/direct.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/queries.py
AC-002 -> OHM-Trade-Agent-v1/app/api/control_center.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_acceptance.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-002 -> OHM-Trade-Agent-v1/docs/CONTROL_CENTER_V2_ARCHITECTURE.md
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-003 -> OHM-Trade-Agent-v1/control-center-v2/test/format.test.mjs
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_acceptance.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/analytics/direct.py
AC-004 -> OHM-Trade-Agent-v1/app/api/control_center.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_acceptance.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-V2-AC01.md

DEFERRED DISCOVERIES:
- The projection is still unpopulated in production because no scheduler entry exists for app/jobs/opip_analytics_maintenance.py. Until an approved Compose change adds one, every projection-backed surface answers through the labelled read-through, and retention and reconciliation panels stay explicitly unavailable.
- AI inference, qualification-decision, promotion and outcome-maturation sources remain unregistered, so those KPIs stay NO_EVIDENCE, INSUFFICIENT DATA or OUTCOME NOT MATURED. Registering them is separate reviewed work.
- The direct read is bounded to the newest acquisition batches and the tail of each ledger; a very large staging tree is not fully re-read, and the response reports truncation.
- Reconciled KPI parity, retention housekeeping and quarantine telemetry cannot be observed until the projection is populated.

UNAPPROVED SCOPE CHANGES:
NONE

