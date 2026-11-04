INCREMENT:
ATDD-OPIP-CONTROL-CENTER-V2-M02

OWNER-APPROVED INTENT:
Complete the O'Pip Operational Control Center V2 as an operational-truth surface over evidence that already exists. The increment adds a derived, reconstructible SQLite analytical projection of canonical scan and comparison evidence, idempotent incremental ingestion with checkpointing and quarantine, retention-aware reconciliation, bounded retention housekeeping that is dry-run by default, a schema-driven eight-domain Admin Console that can stage but never activate, and a rebuilt operator UI whose every figure is either measured or explicitly unavailable. ACTIVE_INCREMENT moves to this contract because the analytical, configuration and UI work is the current change scope; this supersedes the prior increment as the active change scope only, without revoking any earlier approval or changing any earlier acceptance criterion.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: canonical JSONL stays authoritative and immutable; no trading-authority widening; no autonomous retention deletion; secrets never exposed.
- OHM-Trade-Agent-v1/CLAUDE.md: layout, engineering model and release boundaries.
- OHM-Trade-Agent-v1/app/api/dashboard.py: the existing authenticated operator and protected dual-credential strategy-admin endpoints reused here; no second authentication rule is introduced.
- OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py and app/services/scan_attempt.py: the evidence this projection reads, never rewrites.
- OHM-Trade-Agent-v1/app/services/protection_health.py: F11 remains independent; this increment reads no protection verdict into authority.
- OHM-Trade-Agent-v1/app/services/strategy_admin_drafts.py: the existing advisory-profile activation and rollback authority is reused unchanged.
- OHM-Trade-Agent-v1/deploy/control-center/nginx/v2.conf: the exact-match edge allowlist extended with these routes only.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: the unmodified file-mapping and acceptance-check authority.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
canonical append-only evidence written by other services and a registered source list
WHEN:
the ingestion worker runs, is interrupted, is replayed, or meets a partial trailing line, a replaced file, a missing manifest or a malformed record
THEN:
it resumes from a verified byte offset, never ingests an unfinished line, de-duplicates by source identity plus offset, advances the checkpoint in the same transaction as the rows it accepted, quarantines malformed records with a reason and a raw excerpt, and reports the source as quarantined rather than clean.

AC-002:
GIVEN:
a derived projection and its canonical sources
WHEN:
reconciliation runs against a matching, drifted, quarantined, purged or missing source
THEN:
it re-hashes sampled records, distinguishes RECONCILED, DRIFT_DETECTED, UNVERIFIED, SOURCE_UNAVAILABLE and SCHEMA_MISMATCH, counts authorized retained purges as coverage instead of drift, records an auditable run, and reports no run at all as UNVERIFIED.

AC-003:
GIVEN:
derived analytical rows and a retention policy of exactly 7, 15, 30, 60 or 90 days
WHEN:
housekeeping runs, with or without deletion authority
THEN:
it defaults to a dry run, refuses a delete request that is not separately authorized, only ever purges verified unlocked derived rows, protects unverified and audit-locked rows, quarantine and reconciliation and configuration audit history, retains the newest projected record per source so reconciliation stays provable, records durable coverage and housekeeping telemetry, and reports reclaimed bytes only when they were actually measured.

AC-004:
GIVEN:
a code-declared configuration registry spanning eight operational domains and a staging store separate from the effective runtime policy
WHEN:
an editor stages a value, imports a document, or reads the effective configuration
THEN:
syntactic and cross-field validation reject invalid configuration instead of coercing it (including a V2 weight total that is not 100, unordered ranges, read-only settings, factory-immutable caps and any attempt to stage funded-order or AI execution authority), staging records an actor, an audit note, a checksum and a validation result with optimistic concurrency and no runtime effect, the effective value is read from its live source rather than the staging table, a historical preview is labelled hypothetical or explicitly unavailable, and activation and rollback remain with the existing protected strategy-admin endpoints.

AC-005:
GIVEN:
the authenticated Control Center read API and an absent, partial or complete projection
WHEN:
a client reads health, signals, forensics, scanner, AI, data, configuration or a bounded maintenance action
THEN:
reads are served over a read-only connection with keyset pagination and bounded page sizes, a missing projection or a query timeout is reported as UNAVAILABLE with a reason instead of a 500 or an empty dataset, GET endpoints mutate nothing, maintenance and staging require the existing dual credential and report that they change no trading state, and no response carries a secret.

AC-006:
GIVEN:
the rebuilt operator console, its design tokens and the approved brand asset path
WHEN:
an operator loads any workspace
THEN:
the console calls only the explicitly allowlisted same-origin routes, renders every figure from a recorded payload or an explicit UNAVAILABLE, NO_EVIDENCE, STALE, INSUFFICIENT DATA or OUTCOME NOT MATURED label, distinguishes null from zero, keeps a raw-JSON payload behind an explicit advanced disclosure, references the approved transparent logo by a single declared path and states when the asset is missing rather than substituting another logo, stores no credential or preset in browser storage, and exposes every displayed control as a real operation or a disabled control with a reason.

EXPLICITLY OUT OF SCOPE:
- Enabling funded trading, exchange execution, learning activation, the Intelligence Committee or PAPER_V2.
- Changing production scoring thresholds, the V2 rubric weights, the production scoring selector or the promoted-candidate cap.
- Any change to F11 protection logic, entry-admission suspension or exposure coverage.
- Rewriting, truncating, reordering, renaming or deleting canonical JSONL evidence or its manifests.
- Providing a write path that mutates canonical evidence from a browser action.
- Provisioning new infrastructure (database, cache, broker, orchestrator, droplet) or moving analytical load onto the trading host.
- Scheduling the maintenance worker from Compose in this increment: that changes the trading host runtime shape and needs its own approved deployment change.
- Deleting any production analytical row: retention runs dry-run only until separately authorized.
- Retiring the legacy dashboard route before V2 acceptance is verified.
- Declaring F10 eight-page parity, live host attestation, matured outcome attribution or a cross-host learning-droplet telemetry channel.

FROZEN BOUNDARIES:
Canonical evidence keeps its current single writer and stays immutable. F11 stays independent and no console action can bypass it. Funded authority remains absent. The derived projection is read-only for dashboards and writable only through the bounded maintenance paths. No secret is logged, stored, returned or rendered. Retention deletion stays disabled behind a separate environment gate. The existing deployment controller and owner gates are untouched by this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_control_center_v2_m02.py::test_ingestion_is_incremental_idempotent_and_partial_safe
AC-001 -> tests/test_opip_control_center_v2_m02.py::test_malformed_and_unverified_batches_are_quarantined_or_refused
AC-001 -> tests/test_opip_control_center_v2_m02.py::test_truncation_and_replacement_start_a_new_generation
AC-002 -> tests/test_opip_control_center_v2_m02.py::test_reconciliation_proves_match_and_detects_real_drift
AC-002 -> tests/test_opip_control_center_v2_m02.py::test_reconciliation_is_retention_aware_and_reports_absence
AC-002 -> tests/test_opip_control_center_v2_m02.py::test_quarantine_keeps_a_source_unverified
AC-003 -> tests/test_opip_control_center_v2_m02.py::test_retention_policy_values_and_default_are_exact
AC-003 -> tests/test_opip_control_center_v2_m02.py::test_housekeeping_defaults_to_dry_run_and_refuses_unauthorized_delete
AC-003 -> tests/test_opip_control_center_v2_m02.py::test_authorized_delete_protects_unverified_locked_and_audit_evidence
AC-003 -> tests/test_opip_control_center_v2_m02.py::test_authorized_delete_cannot_break_reconciliation
AC-004 -> tests/test_opip_control_center_v2_m02.py::test_registry_covers_eight_domains_with_complete_metadata
AC-004 -> tests/test_opip_control_center_v2_m02.py::test_validation_rejects_rather_than_coerces
AC-004 -> tests/test_opip_control_center_v2_m02.py::test_staging_is_inert_and_uses_optimistic_concurrency
AC-004 -> tests/test_opip_control_center_v2_m02.py::test_historical_preview_is_hypothetical_and_never_invented
AC-005 -> tests/test_opip_control_center_v2_m02.py::test_api_reports_absence_instead_of_inventing_data
AC-005 -> tests/test_opip_control_center_v2_m02.py::test_api_signals_are_classified_and_keyset_paginated
AC-005 -> tests/test_opip_control_center_v2_m02.py::test_api_staging_validates_and_refuses_stale_writes
AC-005 -> tests/test_opip_control_center_v2_m02.py::test_api_maintenance_is_explicit_bounded_and_inert
AC-006 -> tests/test_opip_control_center_v2_m02.py::test_ui_calls_only_allowlisted_endpoints_and_never_fabricates
AC-006 -> tests/test_opip_control_center_v2_m02.py::test_approved_brand_asset_is_installed_and_served_from_the_dashboard_base
AC-006 -> tests/test_opip_control_center_v2_m02.py::test_ui_branding_theme_and_states_are_explicit


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/__init__.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/schema.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/store.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/sources.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/ingest.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/queries.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/analytics/runtime.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/reconcile.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/analytics/queries.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/analytics/retention.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/analytics/schema.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/config/__init__.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/config/registry.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/config/validation.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/config/staging.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-005 -> OHM-Trade-Agent-v1/app/api/control_center.py
AC-005 -> OHM-Trade-Agent-v1/app/main.py
AC-005 -> OHM-Trade-Agent-v1/app/core/config.py
AC-005 -> OHM-Trade-Agent-v1/app/jobs/opip_analytics_maintenance.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_m02.py
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/index.html
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/public/assets/opip-control-center-logo-transparent.png
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/api.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/format.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/main.jsx
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/theme.css
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/style.css
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/src/style.v2.css
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/test/api.test.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/test/format.test.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/e2e/smoke.spec.mjs
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/README.md
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/package.json
AC-006 -> OHM-Trade-Agent-v1/control-center-v2/package-lock.json
AC-006 -> OHM-Trade-Agent-v1/deploy/control-center/nginx/v2.conf
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_edge.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_scope.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_control_center_v2_round2_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONTROL-CENTER-V2-M02.md
AC-006 -> OHM-Trade-Agent-v1/docs/CONTROL_CENTER_V2_ARCHITECTURE.md

DEFERRED DISCOVERIES:
- There is no persisted F11 protection-state artifact on the host, so the header reports UNVERIFIED with NO_F11_STATE_ARTIFACT until OPIP_F11_STATE_PATH is produced by the protection plane. Only the deploy log carries the strict verdict today.
- There is no cross-host telemetry channel to the Learning Droplet, so its CPU and RAM report UNAVAILABLE with NO_LEARNING_DROPLET_TELEMETRY_CHANNEL rather than a fabricated reading.
- No AI inference source is registered, so the AI workspace reports NO_EVIDENCE and no win, false-signal, missed-opportunity, calibration or latency figure is computed.
- No qualification, promotion or outcome-maturation event source is registered, so funnel qualification, attribution and strategy performance remain explicitly unavailable rather than being approximated from scores.
- The approved logo asset was located outside the repository (owner-supplied `OPIP_logo_transparent.png`, 1120x1635 RGBA, transparent corners) and is now installed at control-center-v2/public/assets/opip-control-center-logo-transparent.png with a recorded SHA-256. A replacement by the owner is a one-file change plus one digest update in the asset test.
- The projection lives at a configurable path and defaults to the trading host data volume. Running it on the approved Learning Droplet against replicated exports is the intended next step and needs that host's inventory and permissions verified first.
- Scheduling the ingestion, reconciliation and retention worker is left to a separate approved Compose change; the CLI entrypoint exists and is exercised by tests.

UNAPPROVED SCOPE CHANGES:
NONE

