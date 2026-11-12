INCREMENT:
ATDD-OPIP-V6-RETENTION-DRYRUN-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. V6 slice C
adds one read-only tool that reports the size of each data dataset and the files
a 14-day hot window would prune. It is the measurement half of the owner's
retention decision ("hot data window is 14 days; older data ships to the data
box and is deleted only after hash verification") and nothing more: the tool
only reads directory entries and file metadata and prints a report. No file is
deleted, moved, renamed, compressed or written, no database is opened, no
scheduler, compose, deploy or shipping path changes, and no trading, exchange,
Telegram or learning authority is added or widened. Deletion, shipping and
wiring the report into any runtime path stay out of this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 5 slice C (read-only retention dry-run report: sizes per dataset and what the 14-day policy would prune), section 3 owner decision on the 14-day hot window with hash-verified shipping before deletion, section 4 (additive first: new pure modules and read-only tools before any wiring), and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 9 (data and retention, including the existing retention environment values and the default-off retention deletion) and the `app/opip/data_platform` row of section 4 (keep; extend for the data box and retention policy).
- OHM-Trade-Agent-v1/AGENTS.md: principles 8 (durable evidence is not destructively mutated) and 9 (separation between production, shadow and offline data planes).
- OHM-Trade-Agent-v1/CLAUDE.md: engineering operating model (smallest change, reuse existing data-plane conventions, no new infrastructure) and trading safety boundary.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a data directory whose datasets hold files inside and outside the 14-day hot window
WHEN: the retention dry-run tool runs against that directory
THEN: it prints every dataset with its size and, per dataset, the files older than the window that a prune would select

AC-002:
GIVEN: any input, including a data directory with old files, a missing directory, JSON output and a custom window
WHEN: the retention dry-run tool runs
THEN: no file or directory is changed or removed, and the on-disk state is identical before and after

EXPLICITLY OUT OF SCOPE:
- Deleting, moving, renaming, compressing, truncating or truncating-by-rewrite of any data file, and any `--apply`, `--prune` or force flag.
- Shipping to the data box, hash verification, manifests and the canonical writer roll.
- Scheduler, systemd timer, cron, compose, Dockerfile, deploy script or workflow wiring; the tool is invoked manually only.
- Database, Postgres, analytics-plane, canonical writer or learning evidence changes.
- Trading authority, Kraken order placement, modification, cancellation or confirmation of any kind, Telegram execution, and any widening of Paper or production admission.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority and no exchange permissions, and this tool has no mutation authority over data either.
- Deterministic gates keep failing closed; external AI stays advisory evidence and is not involved.
- Data tiering policy values are unchanged; this increment observes them and does not set, loosen or activate retention deletion.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior.
- No secret, credential or `.env` value is read, printed or written; the report prints only paths, sizes, ages and counts.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_retention_dry_run_v1.py::test_report_prints_each_dataset_size_and_files_older_than_window
AC-002 -> tests/test_v6_retention_dry_run_v1.py::test_dry_run_never_changes_or_removes_a_file


IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/retention_dry_run.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_retention_dry_run_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-DRYRUN-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/retention_dry_run.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_retention_dry_run_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-DRYRUN-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The canonical store is still one SQLite file with a WAL and a single-writer lock, so there is nothing to prune by segment until the writer rolls to a new file per day or week. Until then the report will show the live canonical file as young and cannot free space; the writer roll and hash-verified shipping belong to a later slice.
- Applying a prune, an Operations Center preview page and a retention policy table with a minimum floor per dataset are not implemented here; they need their own contract once shipping and hash verification exist.
- The dry-run walks the whole data root, so an operator should point it at one data directory at a time on a large host; a bounded or cached variant is not needed yet.

UNAPPROVED SCOPE CHANGES:
NONE
