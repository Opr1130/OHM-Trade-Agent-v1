INCREMENT:
ATDD-OPIP-V6-RETENTION-POLICY-v1

OWNER-APPROVED INTENT:
Proposed for owner approval; merging this contract is that approval. This v6
slice adds one pure retention policy table: per data dataset, the hot window in
days and the minimum floor that window may never go below. It also adds a
deterministic validator that rejects a row whose hot window is below its floor,
and it makes the already-approved read-only dry-run report
(`app/opip/data_platform/retention_dry_run.py`) take each dataset's window from
that table instead of assuming a uniform window. The default table states the
owner's 14-day hot window. This is the policy half of the owner's retention
decision ("hot data window is 14 days; older data ships to the data box and is
deleted only after hash verification") and nothing more: nothing is deleted,
moved, shipped, compressed or rewritten, no `--apply`/`--prune` flag exists, the
configuration registry is untouched, and no scheduler, compose, deploy, trading,
exchange, Telegram or learning authority is added or widened.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docs/V6_SPEC.md: section 3 (owner decision: 14-day hot window with hash-verified shipping before deletion), section 5 slice C (the read-only retention dry-run report this table feeds), section 4 (additive first: new pure modules and read-only tools before any wiring) and section 7 (safety boundary).
- OHM-Trade-Agent-v1/docs/V6_AUDIT.md: section 9 (data and retention, including the existing retention environment values, the default-off retention deletion and the statement that "the v6 retention policy table puts these behind the Operations Center with a dry-run preview and a minimum floor per dataset") and section 4's `app/opip/data_platform` row (keep; extend for the data box and retention policy).
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-DRYRUN-v1.md: the approved read-only report and its DEFERRED DISCOVERIES entry naming "a retention policy table with a minimum floor per dataset" as its own later contract.
- OHM-Trade-Agent-v1/app/opip/data_platform/retention_dry_run.py: the approved read-only report whose per-dataset windows now come from the table, and whose `--hot-window-days` flag stays an ad-hoc, non-persisted reporting lens.
- OHM-Trade-Agent-v1/AGENTS.md: principle 8 (durable evidence is never destructively mutated), principle 9 (production, shadow and offline data planes stay separate) and principle 20 (state behavioral impact before a retention-related change).
- OHM-Trade-Agent-v1/CLAUDE.md: engineering operating model (smallest change, reuse existing data-plane conventions, no new infrastructure) and trading safety boundary.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN: a retention policy table that states a dataset, its hot window in days and its minimum floor in days
WHEN: the table is validated
THEN: a hot window below that dataset's floor is rejected with an error instead of being clamped, applied or silently accepted, while a window equal to or above the floor is accepted unchanged

AC-002:
GIVEN: the read-only retention dry-run report and a data directory whose datasets hold files inside and outside the windows the table states
WHEN: the report runs
THEN: it selects prune candidates per dataset using the table's windows (14 days by default), reports each dataset's window and floor, and still creates, changes and removes nothing on disk

EXPLICITLY OUT OF SCOPE:
- Deleting, moving, renaming, compressing, truncating or rewriting any data file, and any `--apply`, `--prune` or force flag.
- Shipping to the data box, hash verification, manifests and the canonical writer roll.
- The Operations Center page, the schema-driven configuration registry, environment or `.env` activation, and any other way to change the table at runtime; the table is a checked-in pure default.
- Scheduler, cron, systemd timer, compose, Dockerfile, deploy script or workflow wiring; the report stays manually invoked.
- Database, Postgres, analytics-plane, canonical writer, learning evidence and reconciliation changes.
- Trading authority, Kraken order placement, modification, cancellation or confirmation of any kind, Telegram execution, and any widening of Paper or production admission.
- `.github/`, `OHM-Trade-Agent-v1/deploy/`, `CLAUDE.md`, `AGENTS.md`, `OHM-Trade-Agent-v1/tests/atdd_scope.py` and `OHM-Trade-Agent-v1/tools/v6_agent/`.

FROZEN BOUNDARIES:
- Advisory and read-only: coding automation has no trading authority, no exchange permissions and no mutation authority over data.
- Deterministic validation fails closed: an unusable table raises and is never applied in corrected or clamped form.
- The default table keeps the 14-day hot window and never sets a floor above its own window.
- A dataset the table does not list gets the table's 14-day default and 7-day floor; it is never dropped from the report and never inherits an unstated laxer window.
- Existing modules, tests, deploy paths and other increments' contracts keep their current behavior; the legacy `--hot-window-days` invocation still returns 0 and still changes nothing.
- No secret, credential or `.env` value is read, printed or written; the table and report contain only dataset names, day counts, paths and sizes.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_v6_retention_policy_v1.py::test_policy_table_rejects_a_window_below_its_floor
AC-002 -> tests/test_v6_retention_policy_v1.py::test_dry_run_uses_policy_windows_and_changes_nothing

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/data_platform/retention_policy.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_v6_retention_policy_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-POLICY-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/retention_policy.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/data_platform/retention_dry_run.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v6_retention_policy_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-RETENTION-POLICY-v1.md
AC-002 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- The table is a checked-in pure default, not a configuration-registry domain and not an environment value. Making it operator-editable through the existing registry needs its own contract and the existing activation-authority rules.
- The dry-run's `--hot-window-days` flag stays an ad-hoc, per-invocation reporting lens that is never persisted; it is not the policy and does not rewrite the table. Whether an operator override should also be floor-checked is a policy decision for the registry slice.
- The default table covers `canonical`, `events` and the root-level `(root)` dataset, which are the datasets the audit names today; other live data-plane names inherit the 14-day default and 7-day floor until a later slice gives them their own row.
- Applying a prune, the Operations Center preview page and hash-verified shipping remain later slices; this increment only states and validates policy and observes it read-only.

UNAPPROVED SCOPE CHANGES:
NONE
