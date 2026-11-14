INCREMENT:
ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1

OWNER-APPROVED INTENT:
On 12 November 2026 the owner approved (D3) a separate, focused repair of the learning-worker PostgreSQL backup, with no deployment until reviewed and tested. The backup has failed since late September because `docker compose exec` interpolates the whole analytics compose file and aborts on unset `OPIP_GRAFANA_*` variables. The repair must make backups recoverable, not merely present, and must respect the disk reserve (D1: 3 GiB normal reserve, 2 GiB absolute minimum; all storage gates are in bytes). On 12 November 2026 the owner also approved a retention rule (keep the newest 3 verified daily dumps plus 4 verified weekly dumps, always preserving the last known recoverable backup) and a conditioned, isolated restore drill with measured space requirements and no change to the live database. This change requires review and a separate deployment approval.

ARCHITECTURE REFERENCES:
- deploy/analytics/opip-postgres-backup.sh: dump through `docker exec`, free-space preflight, table-of-contents verification.
- deploy/analytics/opip-postgres-restore-drill.sh: isolated throwaway container, measured space gate, hard-floor watchdog.
- deploy/analytics/docker-compose.cockpit.yml: documents the compose interpolation failure this repair removes from the backup path.
- tests/test_opip_postgres_backup_repair_v1.py: hermetic evidence using stub `docker` and `df`.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the running opip-postgres container and at least 2 GiB plus the expected dump size free
WHEN: the backup script runs
THEN: it dumps through `docker exec` (never `docker compose`), keeps the password out of process arguments, and leaves a checksummed dump whose table of contents pg_restore can list

AC-002:
GIVEN: pg_dump fails, produces an empty file, or produces an archive whose table of contents is unreadable
WHEN: the backup script runs
THEN: it exits non-zero and leaves no partial or final dump file

AC-003:
GIVEN: free space below 2 GiB plus the expected dump size (the larger of 512 MiB and twice the newest earlier dump), or unreadable
WHEN: the backup script runs
THEN: it exits 75 with a CRITICAL message before any container call, writes nothing and deletes nothing

AC-004:
GIVEN: free bytes below 3 GiB plus 1.25 times the measured live data size, or unreadable
WHEN: the restore drill runs
THEN: it exits 75 with a CRITICAL message before creating any container or directory

AC-005:
GIVEN: either script
WHEN: its executable lines are inspected
THEN: none invokes `docker compose`

AC-006:
GIVEN: existing dumps that carry no verification marker, including ones older than 30 days
WHEN: the backup script runs
THEN: none is deleted, and the script contains no `-delete`; only dumps this script verified can ever be pruned

AC-007:
GIVEN: a dump larger than the free space above the 2 GiB floor
WHEN: the backup script runs
THEN: the write is stopped by a file-size bound at that headroom, the script exits non-zero and no file remains, so the floor is never breached during writing

AC-008:
GIVEN: verified dumps and a new verified dump just created
WHEN: retention runs
THEN: it keeps the newest 3 verified dumps, the newest verified dump of each of the 4 most recent ISO weeks, the newest verified dump and the dump named by the last restore drill; it refuses to prune more than 10 dumps in one run

AC-009:
GIVEN: a checksummed dump and enough measured space
WHEN: the restore drill runs
THEN: the restore starts only after the real server answers over TCP loopback (a readiness timeout is reported and cleans up); it runs in a throwaway container with no network and its own data directory, never against the live container or admin credentials; a failed restore removes the container and data and writes no success evidence; success writes measured byte counts to the evidence file

AC-010:
GIVEN: a failed dump or failed verification
WHEN: the backup script runs
THEN: retention does not run and no existing dump is pruned

EXPLICITLY OUT OF SCOPE:
- No deployment, timer enablement, bootstrap change or restart by this PR. The single approved restore drill is run by hand from this PR's script after Tier A cleanup; no backup run on any host.
- The 30-day deletion is removed. Retention prunes only dumps this script verified; the 21 existing unmarked dumps need a separate, reviewed pruning manifest before any removal.
- No deletion of existing backups, images or evidence by this PR, and no live database or admin credential use by the restore drill.
- No trading, Kraken, Telegram, scoring or alert changes.

FROZEN BOUNDARIES:
- Script names, install paths, timer units and the sidecar `.sha256` format are unchanged; the restore evidence file keeps `verified_at_utc`, `backup_file` and `schema_version` and only adds keys.
- Existing static assertions on the restore drill (`pg_restore --exit-on-error`, `opip_restore_drill_`, `ops.schema_version`, `dropdb --if-exists --force`) still hold.
- Failures stay fail-closed; a failed backup never replaces or removes an earlier dump.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_uses_docker_exec_and_writes_verified_dump
AC-002 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_failure_leaves_no_partial_or_final_file
AC-003 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_refuses_below_free_space_floor_before_any_docker_call
AC-004 -> tests/test_opip_postgres_backup_repair_v1.py::test_restore_drill_refuses_without_measured_space_before_creating_anything
AC-003 -> tests/test_opip_postgres_backup_repair_v1.py::test_expected_size_uses_twice_the_previous_dump
AC-005 -> tests/test_opip_postgres_backup_repair_v1.py::test_scripts_never_invoke_docker_compose
AC-006 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_never_deletes_unverified_dumps
AC-007 -> tests/test_opip_postgres_backup_repair_v1.py::test_oversized_dump_is_killed_at_the_free_space_floor
AC-008 -> tests/test_opip_postgres_backup_repair_v1.py::test_retention_keeps_three_daily_four_weekly_and_the_drilled_dump
AC-008 -> tests/test_opip_postgres_backup_repair_v1.py::test_retention_refuses_to_prune_more_than_ten_dumps
AC-009 -> tests/test_opip_postgres_backup_repair_v1.py::test_restore_drill_is_isolated_and_records_measured_bytes
AC-009 -> tests/test_opip_postgres_backup_repair_v1.py::test_failed_restore_cleans_up_and_records_no_success
AC-009 -> tests/test_opip_postgres_backup_repair_v1.py::test_restore_drill_waits_for_the_real_server_and_fails_loudly_when_not_ready
AC-010 -> tests/test_opip_postgres_backup_repair_v1.py::test_failed_backup_never_prunes_anything

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-002 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-003 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-004 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-restore-drill.sh
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-005 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-005 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-restore-drill.sh
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-006 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-007 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-008 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-008 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-009 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-restore-drill.sh
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-009 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-010 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-010 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md

DEFERRED DISCOVERIES:
- A restore of the existing September dump has never been proven; the approved isolated drill proves one dump only and runs once by hand after Tier A cleanup.
- Identical dump sizes (176,124,039 bytes, 9-21 Sep) suggest but do not prove an unchanged database; content identity is checked by hash in the Phase 1 inventory.
- Pruning the 21 existing unmarked dumps (3.35 GB) needs a separate reviewed pruning manifest; their file and logical-content hashes all differ, so none can be assumed redundant.
- An off-host copy of dumps remains required.

UNAPPROVED SCOPE CHANGES:
NONE
