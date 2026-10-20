# R3 — Security Review (adversarial)

Scope: `OHM-Trade-Agent-v1/deploy/m07` at commit `c3337d51`. No production access
was used; every finding was reproduced offline.

## 1. Findings in the R2 candidate that R3 fixes

| ID | Severity | Finding | Why it mattered | Fix |
|---|---|---|---|---|
| R3-01 | **critical** | `bootstrap-v2.sh` layered an owner-staged `/tmp/opip-v2/docker-compose.v2.yml` plus a generated `nginx-mount.override.yml` onto the core Compose project | an owner-supplied file could inject **any** service, volume, capability or `privileged` flag into the trading stack, and the running stack would differ from what the certified controller (`cd "$APP_ROOT"; docker compose`) manages | overlay removed entirely; both scripts now use the controller's exact invocation |
| R3-02 | high | the toolkit lived in no commit | no code review, no provenance, no reproducibility; the R2 ZIP could not be rebuilt from source | committed under `deploy/m07`, byte-reconciled against the ZIP (57 identical, 5 corrected, 4 new) |
| R3-03 | medium | `config_digest_value` hashed `/tmp/opip-v2/*` files that frequently do not exist, so the digest was a constant `MISSING` string | the receipt's "configuration digest" did not bind any real configuration | digest now covers `docker-compose.yml` + `.env` only |
| R3-04 | medium | `TOOLS="$SRC/scripts"` pointed at a directory that does not exist in the certified app | every receipt/writer-gate call would fail at runtime on the host | `$SRC/deploy/m07/scripts` |
| R3-05 | medium | `tests/conftest.py` defaulted the certified source to a Windows-only absolute path | the committed tests could not run on any other machine | resolved from the tree, env override retained |
| R3-06 | low | a host-script test asserted on a bare substring, so a *presence check* for the reconciler looked like an invocation | a future real invocation could have slipped through | assertions now target invocations |

## 2. Attack surface review of the final code

### 2.1 Command execution
* No `eval`, no `bash -c` with interpolated user input except two deliberate,
  constant-script uses (`crontab -l | grep -v … | crontab -`, `sh -c` none).
* Every external command is an argument vector; no string reassembly of paths.
* `set -Eeuo pipefail`; `run()` aborts on the first non-zero exit; no `|| true`
  on a mutating command (a structural test enumerates the permitted probes).

### 2.2 Privilege and authority
* Bootstrap requires `EUID == 0` and the droplet metadata identity; refusal is
  exit 3 before any mutation.
* The toolkit never starts a writer, never starts a scheduler, and never places
  an order. `activate-writer` is the only writer path and is receipt-gated.
* No exchange credentials, endpoints or order APIs are referenced anywhere.

### 2.3 Secrets
* Committed-tree scan for private keys, `sk-…`, `xox[baprs]-…`, `AKIA…`, `ghp_…`:
  **0 hits**.
* `.env` is only ever tested with `-s` and hashed as bytes; its content is never
  logged, echoed or written into a receipt.
* Receipts record host, SHA, image id and digests — no secret material.

### 2.4 Path safety
* Archive extraction is digest-first and rejects traversal (`HF-01`, 8 tests,
  including a `../../outside-proof.txt` member that must not be created).
* `_safe_destination` rejects absolute paths, dot components, escapes and
  symlinked ancestors for both staging and promotion.
* Promotion preflights **every** file (index hash, containment, symlink,
  conflict) before the first byte is copied; refusal performs 0 writes.

### 2.5 TOCTOU and replay
* Checksums are recomputed immediately before promotion, and each promotion
  re-verifies its destination after copy.
* Receipts carry a SHA-256 over their own body, a nonce and a 60-minute expiry;
  `execute` re-reads host/SHA/image/config, then marks the authorization
  consumed. Replay, expiry, re-prepare and drift are all refused with tests.
* The writer gate re-reads the live database (epoch, sequence, event count,
  integrity, FK) instead of trusting the receipt alone, and refuses receipts
  older than 24 h.

### 2.6 Failure atomicity
* Every refusal happens before `compose up`; the writer and scheduler stay OFF.
* Rollback freezes the scheduler **first**, proves the writer stopped, snapshots
  through SQLite, and only then stops the remaining services.
* A failed restore overwrites a stale PASS receipt with a failure record.

## 3. Residual risk (not closed here)

| Risk | Severity | Note |
|---|---|---|
| Linux-native execution of the shell scripts | high | MSYS `bash -n` is not Linux; the LF bytes are proven but the executable behaviour is not |
| Compose render with a real daemon | high | no container runtime available; the contract is asserted structurally against the certified file |
| Native restore / rollback rehearsal | high | requires owner gates L-06/L-07 |
| `writer-gate` trusts the receipt's `restored` block for *equality* | low | it also re-reads the live DB, so a forged receipt must match the live DB exactly; no signature scheme exists |
| V2 resource overlay (Phase 5E) is not in the certified commit | medium | the controller would apply V1 limits on a V2 host until that overlay is merged; out of scope for this toolkit |

## 4. Reviewer statement

The final code is **fail-closed by construction** on every path that could touch
live state, and it no longer accepts configuration from outside the certified
release. Remaining risk is environmental (Linux, daemon, host), not logical.
