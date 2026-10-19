# 13 — Independent Adversarial Review (HF)

Items 1–4 are defects found **in the HF work itself** during review and corrected before delivery; the rest are residual risks.

## 1. "The promotion gate reads the staging receipt as evidence" (found & fixed)

First implementation required `staging_receipt.canonical_recovered == true`. But `stage_bundle` always writes `false` (correctly — a copy is not recovery), so every promotion with a canonical generation would have been blocked, including a legitimate one. Corrected: the staging receipt is copy-only; the recovery receipt is the sole evidence. Regression `test_valid_receipt_allows_promotion`.

## 2. "A failed restore leaves the old PASS receipt" (found & fixed)

The first version wrote the receipt only on success, so a failed attempt could leave an earlier PASS in place. Corrected: every terminal path rewrites the receipt with `completed=false` (`test_failed_restore_overwrites_a_stale_pass_receipt`).

## 3. "Editing `return` statements is safe" (found & fixed)

A bulk indentation change broke `restore_canonical`'s `try/except`, and a helper insertion orphaned `stage_bundle`'s body. Both were caught by `ast.parse`/collection and repaired.

## 4. "One epoch comparison fits every path" (found & corrected)

Promotion refuses `live_epoch >= receipt_epoch` (equal epoch means commits would be discarded); the host rollback compares `>` and preserves the newer generation first. Documented separately instead of unified.

## 5. "Checking the digest first is enough"

A valid-digest archive can still contain traversal → the second mandatory test uses a *correct* digest with `../../outside-proof.txt` and fails on `ARCHIVE_MEMBERS_SAFE`. Both gates are required.

## 6. "The external digest is trustworthy"

An attacker who rewrites archive **and** sidecar defeats it. The digest is an integrity check, not authenticity: the owner must compare the received value with the digest recorded before transfer. Stated, not assumed.

## 7. "`set_attrs=False` prevents metadata abuse"

`filter="data"` (when available) + `set_attrs=False` + per-member path validation. Directory-mode abuse is not modelled; acceptable because members are validated and the destination is private.

## 8. "The preflight acknowledgement is only a flag"

The authorization step refuses whenever `blocking_incidents > 0`, and allows acknowledgement only when every reason code is a known "no incumbent state" code. Unknown codes fall back to refusal — the safe direction.

## 9. "Bootstrap reuses an image that is not certified"

The image **label** must carry the SHA, so a retagged image fails. It does not verify the image digest against a registry value (unavailable offline): live gate L-03.

## 10. "Rollback code mode is safe because the image exists"

`--previous-image` must carry `--previous-sha` in its label. Label trust is the certified mechanism; content identity is not independently proven.

## 11. "Preserving the canonical directory is enough"

The writer is stopped **before** the copy and the copy is SHA-256 compared with the live DB; cron is frozen first.

## 12. "Two writers cannot appear during rollback"

`up -d` restarts services, so uniqueness is asserted afterwards (`writers == 1`, `crons <= 1`, `pollers <= 1`); a violation refuses instead of declaring success.

## 13. "Provenance cannot be faked offline"

`identity_proof` is a string, so a crafted bundle can claim `host-evidence`. Provenance raises the cost (identity vs evidence, fresh cutoff, archive digest, non-synthetic generation) but final acceptance still requires the owner to reconcile the bundle with the host transaction.

## 14. "The tests prove production behaviour"

They prove toolchain contracts only. No container was started and no host contacted — hence HOLD.

## 15. "Exit codes are trustworthy"

Every wrapper records the child exit code; `promote` maps `SystemExit` to 3 and prints the reason. Two wrapper regressions found during development were fixed, not masked.

## Residual risk register

| Risk | Severity | Status |
|---|---|---|
| Bootstrap/rollback never executed on a host | P1 | Accepted; live gate L-01 |
| Linux restore success unproven | P1 | Accepted; live gate L-07 |
| Digest authenticity vs integrity | P2 | Documented; owner compares pre-transfer value |
| Reason-code allowlist drift | P2 | Fails closed |
| Code rollback trusts the image label | P2 | Matches certified mechanism |
