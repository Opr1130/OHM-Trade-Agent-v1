# 04 — Revised Migration Procedure (R1)

Engine: `scripts/opip_bundle.py` (files) + `scripts/opip_recovery.py` (database).
Entry point: `scripts/Invoke-OpipDataMigration.ps1`.

## What changed from R0

| Defect | R0 behaviour | R1 behaviour |
|---|---|---|
| >2000 files | manifest listed 50 samples | every file listed; `totals.files` must equal the listed count |
| Archive transfer | archive excluded the manifest | archive carries manifest + digest + hash list; `bundle.sha256` travels beside it |
| Verification | partial, per file during copy | whole-bundle verify **before** any staging |
| Canonical | file copy only | explicit `RestoreCanonical` step using the certified native restore |
| Promotion | possible without recovery | refused unless a canonical recovery is recorded |
| Newer history | not checked | refused when live epoch >= generation epoch |

## Sequence (owner-gated)

```
# 0. inventory (read-only; V1 powered off → UNPROVEN, not a failure of the tool)
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Inventory -SourceRoot <v1-root>

# 1. export (dry-run without -Authorize)
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Export -SourceRoot <v1-root> `
  -CanonicalDir <v1-root>\data\opip\canonical\backups -ChunkMB 256 -Authorize

# 2. transfer: archive + bundle.sha256 (+ chunks/ when used)

# 3. verify AFTER transfer, from the archive alone
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Verify -BundleDir <received>

# 4. stage: verify every file, then copy (idempotent)
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Stage -BundleDir <received> -StagingDir <staging>

# 5. canonical recovery (the ONLY step that claims database recovery; Linux)
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode RestoreCanonical -BundleDir <received> `
  -AppRoot /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1 `
  -LiveDb /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1/data/opip/canonical/opip_canonical_v1.sqlite3 `
  -Authorize

# 6. promote (refuses without a persisted PASS recovery receipt)
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Promote -StagingDir <staging> `
  -TargetRoot /opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1 `
  -RecoveryReceipt /var/lib/opip-migration/canonical-recovery-receipt.json -Authorize

# 7. reconcile
pwsh -File scripts/Invoke-OpipDataMigration.ps1 -Mode Reconcile -BundleDir <received> -TargetRoot <live>
```

## Canonical recovery contract (step 5)

`restore_canonical()` in `opip_recovery.py`:

1. refuses when the bundle declares no generation or has more than one;
2. refuses when a live store exists with `history_epoch >= generation epoch` (newer history);
3. refuses while the writer is live — it takes the certified `registry_lock` with `timeout=0` and also refuses if `writer.sock` exists;
4. calls the certified `restore_from_backup(..., advance_epoch=True)`;
5. validates **integrity_check=ok**, **foreign_key_check empty**, **event_count == manifest**, **epoch == manifest+1**, **next_local_sequence == 1**;
6. sets `canonical_recovered: true` only when all of the above pass. `file_copy` stays `false` throughout — a copy is never reported as recovery.

On a platform where directory durability is unprovable the certified publisher refuses (`CanonicalDurabilityError`); the result is `PLATFORM_BLOCKED` (exit 2), not a silent success. This is why step 5 runs on Linux.

## Restartability and non-destructiveness

* Export: identical payload files are skipped (`SKIPPED_IDENTICAL`); a differing file requires `--force`.
* Stage: re-runs skip identical files; a differing staged file aborts instead of overwriting.
* Promote: existing identical files are skipped; existing different files are refused unless `--force`.
* Source artifacts are never deleted; V1 is only read.

## Verification checklist (executed tonight)

| Check | Evidence |
|---|---|
| 2600-file dataset fully enumerated/verified/staged | `test_large_dataset_is_fully_exported_and_staged` |
| archive-only verification | `test_archive_alone_is_verifiable_after_transfer` |
| tampered manifest rejected | `test_corrupted_manifest_is_detected` |
| truncated payload rejected | `test_truncated_payload_is_detected` |
| interrupted export/import resumes | `test_interrupted_export_resumes_without_loss`, `test_interrupted_import_resumes_idempotently` |
| promotion gated on recovery | `test_promotion_requires_recorded_canonical_recovery` |
| newer history refused | `test_existing_newer_canonical_history_is_refused` |
| platform durability block reported | `test_canonical_restore_reports_platform_block` |
| CLI exit contract (77 dry-run, 0 verified) | `test_cli_exit_codes` |

## Still pending live

The **real** certified generation must be produced on V1/Linux (`publish_backup_generation`), and step 5 must be executed once on V2. The fixture proves the toolchain, not the production data.
