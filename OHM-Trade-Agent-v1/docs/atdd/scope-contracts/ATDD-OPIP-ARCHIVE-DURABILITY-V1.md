INCREMENT:
ATDD-OPIP-ARCHIVE-DURABILITY-V1

OWNER-APPROVED INTENT:
Prove, on real pg_dump archives in disposable fixtures, which integrity checks actually detect a damaged backup, and close the gap found: the backup script verified only the archive table of contents, which stays readable when the data blocks behind it are truncated or corrupted. Add a streaming full-archive read to the script, and record in the platform backup manifest whether a copied line ledger ends on a complete row. Branch-only: no backup timer activation, no pruning, no host restore drill, no production file touched, no deployment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh: verification before a dump is marked verified.
- OHM-Trade-Agent-v1/tools/opip_platform_backup.py: file copy with a SHA-256 manifest.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-POSTGRES-BACKUP-REPAIR-v1.md: retention and restore-drill rules, unchanged.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a real custom-format pg_dump archive of a disposable database
WHEN:
it is listed, fully read, checksummed and restored into a second disposable database
THEN:
every step succeeds and the restored table has the same row count and content hash as the source

AC-002:
GIVEN:
the same archive truncated to half its size, or with a block of bytes inverted, or empty
WHEN:
it is checked by table-of-contents listing, by a full streaming read and by SHA-256
THEN:
listing accepts the truncated and the inverted archive, the full read rejects them, SHA-256 differs, and an empty archive fails both reads

AC-003:
GIVEN:
a dump whose table of contents is readable but whose data blocks are not
WHEN:
the backup script runs
THEN:
it exits non-zero with a data-blocks message, leaves no dump, checksum or verified marker, and a readable dump is still checksummed and marked verified

AC-004:
GIVEN:
line-ledger files (jsonl or ndjson) that end on a complete row, end mid-row or are empty, and other files
WHEN:
the platform backup copies them
THEN:
the manifest records whether each line-ledger copy ends on a complete row, adds the field for no other file, hashes the copy as before, and leaves the source files byte-identical

EXPLICITLY OUT OF SCOPE:
- Enabling the backup timer, pruning any dump, running a restore drill on the host, modifying production comparison or ledger files, deployment or restarts.
- Changing retention rules, space floors, the restore drill, the platform restore verifier or any PostgreSQL schema.

FROZEN BOUNDARIES:
- tests/test_opip_postgres_backup_repair_v1.py and its contract are unchanged and still pass.
- The manifest gains one additive key for line ledgers only; the restore verifier ignores unknown keys.
- No trading, order or Kraken authority is added or widened.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_postgres_archive_fixtures_v1.py::test_ac_001_real_archive_passes_both_reads_and_restores_identically
AC-002 -> tests/test_opip_postgres_archive_fixtures_v1.py::test_ac_002_table_of_contents_alone_accepts_damaged_archives_full_read_does_not
AC-003 -> tests/test_opip_postgres_archive_fixtures_v1.py::test_ac_003_backup_discards_a_dump_whose_data_blocks_cannot_be_read
AC-004 -> tests/test_opip_postgres_archive_fixtures_v1.py::test_ac_004_manifest_flags_a_line_ledger_copied_mid_row_and_leaves_the_source_alone

IMPLEMENTATION MAP:
AC-003 -> OHM-Trade-Agent-v1/deploy/analytics/opip-postgres-backup.sh
AC-004 -> OHM-Trade-Agent-v1/tools/opip_platform_backup.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_archive_fixtures_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_postgres_archive_fixtures_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ARCHIVE-DURABILITY-V1.md

DEFERRED DISCOVERIES:
NONE

UNAPPROVED SCOPE CHANGES:
NONE
