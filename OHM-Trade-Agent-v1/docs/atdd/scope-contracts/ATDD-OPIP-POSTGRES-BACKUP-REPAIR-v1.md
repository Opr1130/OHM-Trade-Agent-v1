INCREMENT:
ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1

OWNER-APPROVED INTENT:
On 12 November 2026 the owner approved (D3) a separate, focused repair of the learning-worker PostgreSQL backup, with no deployment until reviewed and tested. The backup has failed since late September because `docker compose exec` interpolates the whole analytics compose file and aborts on unset `OPIP_GRAFANA_*` variables. The repair must make backups recoverable, not merely present, and must respect the disk reserve (D1: 2 GiB absolute minimum). This change requires review and a separate deployment approval.

ARCHITECTURE REFERENCES:
- deploy/analytics/opip-postgres-backup.sh: dump through `docker exec`, free-space preflight, table-of-contents verification.
- deploy/analytics/opip-postgres-restore-drill.sh: same container access, free-space preflight before any database is created.
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
GIVEN: free space below 2 GiB plus the expected dump size, or unreadable
WHEN: the backup script runs
THEN: it exits 75 with a CRITICAL message before any container call, writes nothing and deletes nothing

AC-004:
GIVEN: free space below 2 GiB plus four times the dump size, or unreadable
WHEN: the restore drill runs
THEN: it exits 75 with a CRITICAL message before creating any database

AC-005:
GIVEN: either script
WHEN: its executable lines are inspected
THEN: none invokes `docker compose`

EXPLICITLY OUT OF SCOPE:
- No deployment, timer change, bootstrap change, restart, restore drill run or backup run on any host by this PR.
- No change to the 30-day dump retention, off-host copy, receiver, S2 shipment, learning export, legacy sync or learning activation.
- No deletion of existing backups, images or evidence, and no database or admin credential use by this PR.
- No trading, Kraken, Telegram, scoring or alert changes.

FROZEN BOUNDARIES:
- Script names, install paths, timer units and the sidecar `.sha256` format are unchanged.
- Existing static assertions on the restore drill (`pg_restore --exit-on-error`, `opip_restore_drill_`, `ops.schema_version`, `dropdb --if-exists --force`) still hold.
- Failures stay fail-closed; a failed backup never replaces or removes an earlier dump.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_uses_docker_exec_and_writes_verified_dump
AC-002 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_failure_leaves_no_partial_or_final_file
AC-003 -> tests/test_opip_postgres_backup_repair_v1.py::test_backup_refuses_below_free_space_floor_before_any_docker_call
AC-004 -> tests/test_opip_postgres_backup_repair_v1.py::test_restore_drill_refuses_low_space_before_touching_the_database
AC-005 -> tests/test_opip_postgres_backup_repair_v1.py::test_scripts_never_invoke_docker_compose

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-restore-drill.sh
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-005 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-005 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-restore-drill.sh
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_backup_repair_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- A restore of the existing September dump has never been proven; a restore drill on the learning worker needs its own owner approval and a free-space check first.
- Identical dump sizes (176,124,039 bytes, 9-21 Sep) suggest but do not prove an unchanged database; content identity is checked by hash in the Phase 1 inventory.
- The 30-day dump retention can delete the only good dump if backups keep failing; a keep-last-good rule is a separate decision.
- An off-host copy of dumps remains required.

UNAPPROVED SCOPE CHANGES:
NONE
