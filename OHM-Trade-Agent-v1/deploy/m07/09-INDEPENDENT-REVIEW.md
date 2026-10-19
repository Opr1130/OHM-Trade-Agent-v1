# Mission 07 R2 — Independent Adversarial Review

Reviewer stance: assume the correction is wrong until a test fails when the guard
is removed. Every finding below was reproduced against this tree; none is
theoretical.

## 1. Findings that were real defects in this correction, now fixed

| ID | Finding | How it was found | Fix | Regression |
|---|---|---|---|---|
| R2-01 | After a bulk rewrite, the bodies of `mode_prepare` and `preflight_evaluate` were left **without their headers**, leaving statements at top level. `bash -n` still passed, so a syntax check alone could not catch it. | manual re-read of the whole script after the rewrite | headers restored; `test_no_orphaned_function_bodies` fails on any statement outside a function and on unbalanced braces | R2-01 |
| R2-02 | `[[ -L "$APP_ROOT" ]] && die`, `[[ -d "$SRC/data" ]] && mounts+=(...)`, `[[ "$KIND" == "data" ]] && die` — an AND-list at statement level returns 1 when the left side is false, which aborts the whole script under `set -e`. The rollback would have exited on every non-`data` kind. | systematic scan for `]] && ` in both scripts | rewritten as explicit `if` blocks; `test_no_errexit_conditional_die` scans both files | R2-02 |
| R2-03 | `prepare` computed the configuration digest while `$APP_ROOT` was still empty, so `execute` always saw a different digest and refused every run. | walking the prepare→execute data flow by hand | digest falls back to the verified release compose file before installation (identical content by construction) | R2-03 |
| R2-04 | Two `prepare` runs in the same second produced byte-identical receipts (microseconds are truncated), so an older authorization stayed valid. | expiry/replay test failed because the digest did not change | prepared receipts carry a random `nonce`; re-preparing always invalidates an earlier authorization | `test_authorization_is_bound_to_the_prepared_receipt` |
| R2-05 | On a genuinely fresh host, `execute` created `$APP_ROOT/data` **before** installing, so the install saw a non-empty root and refused. | walking the fresh-host path by hand | install the release first; the foreign-content check ignores `data/` | `test_install_tolerates_a_precreated_data_dir` |

## 2. Findings that remain open (honest list)

| ID | Severity | Open issue | Why it cannot be closed here |
|---|---|---|---|
| O-01 | high | No Linux-native `bash -n` of the shipped bytes | no WSL, no Docker on this workstation. Mitigation: byte-level CRLF scan on the extracted archive plus `.gitattributes`. **Still required before release.** |
| O-02 | high | No fresh-host rehearsal of prepare→execute→activate-writer | needs an authorised disposable Linux host (R2-G2) |
| O-03 | high | No native canonical restore or rollback rehearsal | owner gates L-06/L-07 |
| O-04 | medium | Compose render uses the certified base file but not the real overlay | overlay only exists as an owner-staged input (L-05) |
| O-05 | medium | `mode_prepare` creates `$APP_ROOT` and `$ETC` (directories only) | unavoidable for a first install; it writes no application, config or data state. Documented, not hidden. |
| O-06 | medium | `install_release` uses `rsync --delete` | safe only because the release is manifest-verified first and `data/` and `.env` are excluded; a wrong manifest aborts before the copy |
| O-07 | low | `writer-gate` trusts the receipt's `restored` block for equality with the live DB | it also re-reads the live DB, so a forged receipt would have to match the live database exactly; a signed receipt is not implemented |
| O-08 | low | `ACTIVATE_SCHEDULER` legacy flag is accepted and ignored | deliberate: an old command line can no longer activate the scheduler |
| O-09 | low | `stage_bundle` refuses legacy staging receipts without a file index | fail-closed by design; re-staging is required after upgrade |

## 3. Attacks attempted against the new code

| Attack | Result |
|---|---|
| `../../outside-proof.txt` member in the archive | refused before extraction; file never created (HF-01) |
| Tampered staged file after the receipt | `REFUSED`, 0 writes |
| Conflict on the alphabetically later file | first file **not** copied; `writes_performed=0` |
| Symlinked ancestor in the target | `SystemExit` |
| Absolute/`..`/drive-letter destination | `SystemExit` |
| Replay of a consumed authorization | `FAIL` with "consumed" |
| Expired prepared receipt | `SystemExit` "expired" |
| Host/image/config drift between phases | `FAIL` naming the drifted field |
| Blocking preflight incident + acknowledgement | `SystemExit` |
| Recovery receipt for a different DB path | `REFUSED` |
| Recovery receipt 30 h old | `REFUSED` "stale" |
| Same epoch, newer sequence | `REFUSED` |
| Same epoch/sequence, one extra event | `REFUSED` |
| Corrupt live database | `REFUSED` |
| Snapshot onto an existing file | `SystemExit`; evidence preserved |
| A test that cannot fail (`or True`) | repo-wide scan fails the suite |

## 4. Test-integrity review

* no `monkeypatch` is used anywhere in `tests/`;
* the only skip in the suite is the symlink test when the OS denies symlink
  creation; it does not hide a product behaviour;
* the CRLF test reads raw bytes, not decoded text;
* the verdict of every negative test is an explicit value (`REFUSED`, exit 3),
  never "no exception raised";
* the R2 module is independent of the fixture bundle, so it runs in 0.22 s and
  cannot be masked by a slow or skipped fixture.

## 5. Reviewer conclusion

The corrected implementation is materially stronger than the delivered candidate:
the writer can no longer start before a bound recovery receipt exists, promotion
cannot half-apply, rollback cannot copy a hot WAL database, and every receipt is
digest-bound, expiring and single-use.

It is **not** releasable: O-01…O-04 are Linux/host facts that this workstation
cannot produce. Verdict stays **HOLD**.
