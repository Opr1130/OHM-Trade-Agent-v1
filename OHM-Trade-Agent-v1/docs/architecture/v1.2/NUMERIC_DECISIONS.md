# Numeric decisions (N1–N12)

Ratified 2026-09-11 by Business Decision Owner and Technical Release Owner: **Ohm Prakash**.

Every numeric limit is configurable. None of the legacy comparator numbers become permanent Profit Intelligence architecture invariants unless a later contract says so.

| ID | Decision | Ratified value | Implementation in PR 1 |
| --- | --- | --- | --- |
| N1 | Research simulation equity | $10,000 | Document only |
| N2 | Approved-allocation starting equity | $10,000 nominal, **logically separate account/ledger** from research | Document only |
| N3 | Max positions | Research = 3; approved allocation = 2 initially | Document only |
| N4 | Daily loss | 1% paper-policy limit | Document only; **no runtime enforcement** |
| N5 | Drawdown | Soft 5%, hard 8%; no loosening | Document only |
| N6 | Risk / min R:R | Freeze **legacy comparator** at 0.35% risk and 2.5 minimum R:R. **Not** a permanent PI invariant | Document / freeze; do not change code |
| N7 | Technical score / Top-8 | Freeze 80 / 8. Do not modify. Retire at technical cutover | Document / freeze; do not change code |
| N8 | Writer / protection latency | Writer txn p99 ≤ 50 ms; protection-intent queue age **p99 ≤ 1 second**. Measure and ratify in PR 2 | Document only |
| N9 | RPO / RTO | Provisional RPO ≤ 5 min / RTO ≤ 30 min, subject to PR 2 restore drill | Document only |
| N10 | Evaluation grid | **1-minute aggregate/watermark and 1-minute IGNITION evaluation** for v1. 15-minute candles may remain features or paper-policy inputs only | Document only |
| N11 | Owners | Ohm Prakash for both roles unless a later human release owner is designated | Recorded |
| N12 | Dates | Start 2026-09-11; checkpoint 2026-10-11 | Recorded |

## Explicit rejections

- Do **not** use the 15-minute paper candle interval as the IGNITION detection cadence.
- Do **not** treat 0.35% risk or 2.5 R:R as Profit Intelligence architecture constants.
- Do **not** invent a second owner.

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`
