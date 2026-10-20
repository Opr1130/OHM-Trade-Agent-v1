# Mission 07 R2 — Final Verdict

## Decision

```
CERTIFICATION: HOLD
ENGINEERING CANDIDATE: acceptable for owner review, NOT deployable
RELEASE PACKAGE: OPIP-M07-R2-CANDIDATE.zip
```

The correction resolves every defect that is reproducible **offline**, and the
offline suite is green (100 passed, 0 failed, 0 skipped, exit 0, 190.6 s). It is
still a HOLD because four release facts can only be produced on Linux / on the
host, and none of them has been produced:

| Open gate | Consequence |
|---|---|
| R2-G1 Linux-native `bash -n` of the shipped bytes | the exact class of failure that invalidated the previous package is only mitigated, not re-proven |
| R2-G2 fresh-host rehearsal of prepare→execute→activate-writer | the ordering is unit-tested, never executed end to end |
| L-06/L-07 final V1 generation + native restore rehearsal | no real recovery receipt exists, so no writer can legitimately start |
| L-01…L-05, L-08…L-10 live prerequisites | no host evidence at all |

## What changed, in one line each

1. **One host root**: `/opt/OHM-Trade-Agent-v1/OHM-Trade-Agent-v1` holds Compose,
   `.env`, data and the canonical DB; `/srv/...` is only an immutable install
   source, installed with a verified manifest.
2. **Writer is a post-recovery gate**: `execute` never starts it;
   `--mode activate-writer` requires a PASS recovery receipt bound to the live DB.
3. **Scheduler is a post-acceptance gate**: it additionally requires a recorded
   HTTPS + Telegram acceptance.
4. **Promotion is two-phase**: every conflict, escape and symlink is preflighted
   before the first byte is copied.
5. **Rollback snapshots through SQLite**, proves quiescence, and refuses
   same-epoch-but-newer sequence or event history.
6. **Every receipt is digest-bound, expiring and single-use.**
7. **The shell scripts are LF-only and the archive is byte-verified.**

## Owner decision table

| Question | Answer |
|---|---|
| Is there a corrected engineering candidate? | Yes — `OPIP-M07-R2-CANDIDATE.zip`, SHA-256 in the sidecar `PACKAGE-SHA256.txt` |
| Can it be deployed today? | **No.** Four live/Linux gates are open |
| Is it independently reproducible? | The offline suite is: 99 tests, no network, no host. The certified source must be supplied at `c196dd67…` |
| What remains live-only? | L-01…L-10 plus R2-G1…G3 |
| What must change before deployment? | a Linux `bash -n` + full-suite run on the extracted ZIP; a fresh-host rehearsal; a real restore with a receipt; TLS and tracked Telegram acceptance |
| What would owner authorisation cover? | one read-only probe (L-01), staging uploads (L-05), the final V1 export (L-06) and one native restore rehearsal on a disposable host (L-07) |
| Does anything here enable trading or a production mutation? | No. No trading authority was added, no live command was executed, no merge, push or deploy was performed |

## Statement of limitations

* No production SSH, V2 activation, database restore, DNS, certificate, firewall,
  Telegram send or Git remote operation was performed.
* No secret value was read, printed or stored; only file hashes are computed
  on-host by the scripts.
* The certified tree was not modified: all edits are confined to
  `C:\Users\ohmpr\opip-mission-07-r1`.
* The pre-change baseline is preserved at
  `C:\Users\ohmpr\opip-mission-07-r1-baseline`.
* Counts in this report are from the run recorded in `05-EXECUTION-LOGS.md` and
  `evidence/r2/full-suite.log`; they were not copied from any earlier report.
