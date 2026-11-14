INCREMENT:
ATDD-OPIP-POSTGRES-PHASE1-V1

OWNER-APPROVED INTENT:
PostgreSQL Phase 1 (owner-approved direction): make PostgreSQL ready to become the sole authoritative store for NEW production business records after a separately approved cutover. Branch-only and disabled by default: a source-backed storage inventory and schema-gap matrix, additive migration 007 (cutover authority with DB-side split-brain guard, ordered idempotent business-event ledger, typed delivery / AI-call / signal-outcome tables), a store API and a fail-closed admission gate. No live cutover, no historical migration (Phase 2), no legacy-file retirement (Phase 3), no production wiring, no runtime configuration change, no F11 change.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/AGENTS.md: deterministic gates fail closed; external AI is advisory.
- OHM-Trade-Agent-v1/app/opip/data_platform/: existing seven-schema analytics design and migrations 001-006 are reused, not replaced.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a fresh deployment and the checked-in migrations
WHEN:
migration 007 is applied
THEN:
it only adds idempotent objects, never alters or drops existing ones, never changes migrations 001-006 (checksum-pinned) and leaves the authority as FILES

AC-002:
GIVEN:
the application source and the stream registry
WHEN:
storage is inventoried
THEN:
every /app/data path is classified, every claimed PostgreSQL target exists in a migration, claimed shipping matches a registered stream, safety-critical state has no PostgreSQL target, and the published matrix is generated from the module

AC-003:
GIVEN:
the store configuration and a database that is absent, unreachable, not authoritative or at another epoch
WHEN:
a new admission is considered
THEN:
FILES (the default, and the result of any unrecognised value) needs no database call, and POSTGRES authority admits only with proof of a reachable authoritative database at the configured epoch; driver failures are StoreUnavailable and payloads are canonical

AC-004:
GIVEN:
the application source
WHEN:
imports are inspected
THEN:
nothing outside the data-platform package imports the business store, so F11, protective exits and emergency alerts cannot depend on PostgreSQL

AC-005:
GIVEN:
a real PostgreSQL server
WHEN:
business events are appended
THEN:
the database refuses writes while FILES is authoritative, replays are no-ops, conflicts are typed, per-aggregate sequence is gap-free under concurrency, and the ledger event and typed row commit or roll back together

AC-006:
GIVEN:
an authority rollback, epoch change or restricted role
WHEN:
writers attempt to append or change authority
THEN:
stale or rolled-back writers are refused, transitions are audited and compare-and-set, epochs never decrease, the application role can append but cannot change authority or history, the ledger and its aggregate head are writable only through ops.append_business_event, and typed projection rows are refused at the database unless PostgreSQL is the authority and the ledger event belongs to the current epoch; every SECURITY DEFINER function pins search_path with pg_temp last, is not owned by an application role and is not publicly executable; a battery of direct SQL attempts by each restricted role (disable triggers, truncate, delete, update authority, change replication role, create objects or functions, alter or re-own the definer function, shadow the ledger with temp tables) fails or has no effect; and no restricted role is privileged or a member of another

AC-007:
GIVEN:
outcome rows with missing target/stop ordering
WHEN:
they are stored
THEN:
INSUFFICIENT_EVIDENCE carries no verdict or return and cannot be combined with another outcome class or verdict

AC-008:
GIVEN:
an unreachable database
WHEN:
a write or admission is attempted
THEN:
the call fails quickly with StoreUnavailable, no partial row exists and the admission gate answers no

AC-009:
GIVEN:
a migrated database
WHEN:
preflight runs
THEN:
it reports migrations, authority, role privileges and connection headroom, detects checksum drift, and states host disk, memory and restore drill as UNVERIFIED

EXPLICITLY OUT OF SCOPE:
- Any production wiring, cutover, authority change, deployment, restart or runtime configuration change.
- Historical SQLite/JSON/JSONL migration (Phase 2) and legacy-file retirement (Phase 3).
- Backup-timer activation, dump pruning, a host restore drill, F11, Learning, Committee and order authority.

FROZEN BOUNDARIES:
- Migrations 001-006 are byte-identical (checksum-bound in deployed databases).
- The business store is imported by nothing outside the data-platform package.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_postgres_phase1_v1.py::test_ac_001_a_migration_007_is_additive_and_earlier_migrations_are_untouched
AC-002 -> tests/test_opip_postgres_phase1_v1.py::test_ac_002_a_every_storage_path_in_app_is_classified
AC-002 -> tests/test_opip_postgres_phase1_v1.py::test_ac_002_b_inventory_targets_and_streams_match_the_checked_in_schema
AC-002 -> tests/test_opip_postgres_phase1_v1.py::test_ac_002_c_rendered_matrix_is_in_sync_with_the_module
AC-003 -> tests/test_opip_postgres_phase1_v1.py::test_ac_003_a_disabled_by_default_and_unknown_values_never_enable_postgres
AC-003 -> tests/test_opip_postgres_phase1_v1.py::test_ac_003_b_files_authority_needs_no_database_and_cannot_fail
AC-003 -> tests/test_opip_postgres_phase1_v1.py::test_ac_003_c_postgres_authority_fails_closed_unless_proven
AC-003 -> tests/test_opip_postgres_phase1_v1.py::test_ac_003_d_driver_failures_map_to_unavailable_and_state_errors_are_typed
AC-003 -> tests/test_opip_postgres_phase1_v1.py::test_ac_003_e_payloads_are_canonical_and_reject_non_finite_values
AC-004 -> tests/test_opip_postgres_phase1_v1.py::test_ac_004_a_nothing_outside_the_data_platform_imports_the_business_store
AC-004 -> tests/test_opip_postgres_phase1_v1.py::test_ac_004_b_business_store_has_no_dependency_on_f11_or_trading_modules
AC-005 -> tests/test_opip_postgres_phase1_v1.py::test_ac_005_a_migrations_apply_once_and_authority_starts_as_files
AC-005 -> tests/test_opip_postgres_phase1_v1.py::test_ac_005_b_database_refuses_business_writes_while_files_is_authoritative
AC-005 -> tests/test_opip_postgres_phase1_v1.py::test_ac_005_c_idempotent_ordered_atomic_append
AC-005 -> tests/test_opip_postgres_phase1_v1.py::test_ac_005_d_a_rejected_projection_rolls_back_the_ledger_event
AC-005 -> tests/test_opip_postgres_phase1_v1.py::test_ac_005_e_concurrent_writers_keep_the_sequence_gap_free_and_dedupe_replays
AC-006 -> tests/test_opip_postgres_phase1_v1.py::test_ac_006_a_authority_rollback_stops_stale_writers_and_is_audited
AC-006 -> tests/test_opip_postgres_phase1_v1.py::test_ac_006_b_application_writer_role_is_least_privilege
AC-006 -> tests/test_opip_postgres_phase1_v1.py::test_ac_006_c_typed_projection_inserts_obey_authority_and_epoch
AC-006 -> tests/test_opip_postgres_phase1_v1.py::test_ac_006_d_definer_functions_pin_their_search_path_and_are_not_publicly_executable
AC-006 -> tests/test_opip_postgres_phase1_v1.py::test_ac_006_e_direct_sql_cannot_bypass_the_ledger_projection_or_authority
AC-007 -> tests/test_opip_postgres_phase1_v1.py::test_ac_007_a_outcomes_never_turn_missing_evidence_into_a_verdict
AC-008 -> tests/test_opip_postgres_phase1_v1.py::test_ac_008_a_database_outage_fails_closed_quickly_and_writes_nothing
AC-009 -> tests/test_opip_postgres_phase1_v1.py::test_ac_009_a_preflight_reports_readiness_and_detects_checksum_drift

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-001 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_data_platform_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_dashboard_freshness_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-002 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-003 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-003 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-003 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-004 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-004 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-004 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-005 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-005 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-005 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-005 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-006 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-006 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-006 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-006 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-007 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-007 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-007 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-007 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-008 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-008 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-008 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-008 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md
AC-009 -> OHM-Trade-Agent-v1/app/opip/data_platform/migrations/007_business_store.sql
AC-009 -> OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py
AC-009 -> OHM-Trade-Agent-v1/app/opip/data_platform/storage_inventory.py
AC-009 -> OHM-Trade-Agent-v1/docs/engineering/OPIP_POSTGRES_PHASE1_STORAGE_INVENTORY.md
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_phase1_v1.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-PHASE1-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
