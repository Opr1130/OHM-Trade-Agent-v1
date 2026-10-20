# 07 — Independent Negative-Path Review (R1)

Adversarial review of the R1 corrections. Each item states the assumption challenged, the finding, and what was changed or accepted.

## 1. "The bootstrap skips the certified preflight"

**Challenge:** a fresh-host path that does not run `ohm-deploy` could quietly bypass the protection gate.
**Finding:** valid risk in the first draft. The plan now executes the **same module** (`app.jobs.preflight_protection_health`) inside a read-only container and stores `$STATE_DIR/protection-preflight.json`, and the mutation path refuses without `-AcknowledgeFreshHostPreflight`. A blocked verdict on a fresh host is expected and recorded, never skipped.
**Residual:** the acknowledgement is a human gate; it cannot be verified by the tool.

## 2. "`fresh` is asserted, not proven"

**Challenge:** `-ContainerPresent no` is operator-supplied.
**Finding:** proven. The classifier now accepts a **facts file** produced by an authorised read-only probe and records `evidence_source`. A local path can only return `2` (INSUFFICIENT); mutation requires host facts (`FRESH_READY`). `UNKNOWN` container presence is never treated as fresh.

## 3. "The rollback overlay is still the controller's"

**Challenge:** maybe the durable overlay is just a copy of the transient one.
**Finding:** no — it is generated from a constant in `opip_recovery.py`, written to `/var/lib/opip-rollback/<sha>/`, re-read and hashed, and asserted not to reference `safe-baseline-rollback.override.yml`.

## 4. "A file copy is being called recovery"

**Challenge:** `promote` could quietly restore a database by copying the sqlite file.
**Finding:** two separate gates. `stage` records `canonical_recovered: false` and says a copy is not recovery; `promote` refuses when canonical files are staged without a recorded recovery; `restore_canonical` sets `file_copy: false` and validates epoch/sequence/integrity/FK/counts.

## 5. "The archive verification trusts the archive's own manifest"

**Challenge:** a tampered archive could carry a matching manifest.
**Finding:** the manifest carries a **content digest** (`dataset_digest`) over datasets + source + canonical + statuses, plus a separate `MANIFEST-SHA256`. Tampering with a size or hash breaks one of them. Transfer integrity additionally depends on `bundle.sha256` sent out-of-band — if an attacker can rewrite both the archive and that sidecar, only an owner-known digest helps. Mitigation: compare the archive digest with the value recorded at export time (kept in Mission 07 evidence / owner channel).

## 6. "2600 files prove scale"

**Challenge:** 2600 tiny files is not 1.2 GB of canonical data.
**Finding:** accurate. It proves the **enumeration/manifest/stage** path, not I/O throughput or peak memory. Real package size and restore duration remain unmeasured (`ESTIMATE`).

## 7. "Epoch comparison is enough to protect newer history"

**Challenge:** `live epoch >= generation epoch` could block a legitimate restore of an equal-epoch generation.
**Finding:** intentional. Equal epoch means the live store already reached that epoch, so restoring would discard commits. The owner must archive the newer store first; `--allow-newer-history` exists only for a rehearsed, empty target.

## 8. "The writer is quiescent because the lock is free"

**Challenge:** the lock file may not exist while the writer is running.
**Finding:** partially true. The check refuses if `writer.sock` exists, and if the lock is held. It cannot prove quiescence when neither exists — so the procedure additionally stops the container and the plan order stops the writer first. Marked as a defence-in-depth check, not a proof.

## 9. "Tracking proves delivery"

**Challenge:** `send_tracked_telegram` still calls Telegram.
**Finding:** in R1 it is called with a **mocked transport**; what is proven is the recording/dedupe contract. Live connectivity is explicitly `INSUFFICIENT` (exit 2) until executed on V2.

## 10. "The paper stack stays off"

**Challenge:** `wait_paper_health` requires two healthy Freqtrade workers, and `docker-compose.paper.yml` exists in the tree.
**Finding:** bootstrap never passes that file to `docker compose` and starts only `ohm-trade-agent` / `opip-canonical-writer`. Readiness refuses when `docker compose config` for the V2 project lists any `ohm-freqtrade-paper*` service.

## 11. "P1 retirement will not run"

**Challenge:** the retirement script lives in the tree the bootstrap checks out.
**Finding:** it is invoked only from the controller's post-commit block (`ohm-deploy:1815`), which bootstrap never runs. Recorded as `P1_RETIREMENT_EXCLUDED`.

## 12. "Windows tests prove Linux behaviour"

**Challenge:** the canonical restore is tested on Windows and returns `PLATFORM_BLOCKED`.
**Finding:** correct limitation. Only the refusal is proven locally; the successful restore must be executed on Linux. The exact command and expected values are documented, and the result is treated as `PENDING LIVE VALIDATION`.

## 13. "Idempotency is complete"

**Challenge:** a retried `promote` after a partial copy could duplicate history.
**Finding:** promotion is file-level idempotent (identical → skip, different → refuse). Canonical history duplication is prevented by the recovery gate and the epoch guard, not by file comparison.

## 14. "Exit codes are trustworthy"

**Challenge:** a wrapper could return 0 while the child failed.
**Finding:** every wrapper records the child exit code in a receipt and maps `0/1/2/3/64/77`. The two failing wrapper runs during development (bootstrap returning 2 because facts were not read) were caused by a missing `-EvidenceJson` parameter and were fixed, not masked.

## Unresolved

* Real V1 extraction, real transfer throughput and real restore duration are unmeasured.
* The protection-preflight verdict on a genuinely fresh host is expected to be `BLOCKED`; the owner acknowledgement is the only control.
* No live TLS, Telegram or scheduler evidence exists yet.
