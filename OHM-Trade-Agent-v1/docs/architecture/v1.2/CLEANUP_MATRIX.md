# Cleanup matrix

Disposition is toward the v1.2 target. Current as-built status is at pin `808a308cd274d30b55fef47b382c229b761e07df`.

Every retained comparator or compatibility path has a termination gate.

| Subsystem | Now | Target | Termination / exit gate |
| --- | --- | --- | --- |
| Kraken adapters | KEEP | KEEP / ADAPT | Verified pair normalization and rate handling |
| Technical indicators | KEEP | ADAPT | Shared feature-bus parity; then delete duplicate calculation |
| Technical score / Top-8 | KEEP (live) | FREEZE → RETIRE | Replacement discovery cutover (PR 7). Frozen at 80 / 8 (N7) |
| Movement discovery | KEEP (v2.1 live) | ADAPT → RETIRE | IGNITION + episode lifecycle covers the capability |
| Early Watch | KEEP (orchestration) | ADAPT → RETIRE | Lifecycle / reporting consumers migrated |
| Explosion / precursor | FREEZE (shadow) | ADAPT → RETIRE | Verified state/features transferred |
| Price movement radar | KEEP (shadow default) | RETIRE | Replacement lifecycle consumers verified |
| Signal Quality composite | ADAPT (flag default off) | RETIRE | Named diagnostics / calibration metrics replace it. SQ-01 not started |
| Phase3C | KEEP (learning) | ADAPT → RETIRE authority | Shared versioned label / research jobs |
| Discovery outcomes / attribution | KEEP (learning) | ADAPT → RETIRE authority | Unified outcomes / projections |
| Opportunity Accountability | KEEP | ADAPT | Preserve coverage / regret questions without a separate truth |
| Legacy JSONL writers | KEEP (WAL today) | STOP → ARCHIVE → DELETE | Consumer check + named stop-writing timestamp at cutover |
| Profit-ranking comparator | KEEP (live rank) | FREEZE → DELETE runtime | Terminate at technical cutover; bounded offline fixtures only |
| Paper engine | KEEP (native + Freqtrade) | ADAPT | One validated implementation and ledger; two named accounts (N2) |
| Dashboard | KEEP | ADAPT | Canonical read projections + consumer verification |
| AI router | KEEP (advisory) | ADAPT | Offline-only routing; remove runtime finalist hooks at cutover |
| Alert governor | KEEP | ADAPT | Incident lifecycle + bounded reminders; PR 2 first transaction |

## GitHub PR #233

Not a row in this matrix. GitHub PR #233 is not dispositioned by PR 1.

## Superseded proposed Profit Intelligence implementation path

Not merged. Not the foundation. Salvage of useful tests is deferred and optional.
