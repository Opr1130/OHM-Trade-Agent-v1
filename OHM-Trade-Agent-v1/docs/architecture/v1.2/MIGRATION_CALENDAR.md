# Migration calendar and owners

## Owners

| Role | Name |
| --- | --- |
| Business Decision Owner | Ohm Prakash |
| Technical Release Owner | Ohm Prakash |

PR 1 uses the same human for both roles. A later human Technical Release Owner may be designated. Do not invent a second person.

## Dates

| Gate | Date / rule |
| --- | --- |
| Implementation start | 2026-09-11 |
| 30-day decision checkpoint | 2026-10-11 |
| Permitted checkpoint actions | CUT OVER / REDUCE SCOPE + bounded new date / ROLL BACK |
| Default if no decision | Freeze feature expansion on both old and new paths; continue safety, evidence preservation, and necessary maintenance |
| Comparator termination | Technical cutover (PR 7), not a calendar date invented in PR 1 |
| JSONL stop-writing timestamp | Named at cutover after consumer-check; not fabricated now |
| Obsolete-code deletion | Target 14 days after technical cutover; extension requires named reason and expiry |
| Statistical promotion | Never forced to meet a calendar date |

## Rollback

Rollback restores the last approved single authority. It does not reactivate suspect evidence as trustworthy and does not run two allocation authorities.

## GitHub PR #233

GitHub PR #233 is out of this calendar. PR 1 does not merge, close, supersede, modify, or otherwise disposition it.

## Authority statements

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`SIGNAL QUALITY SQ-01 STARTED = NO`

`FUNDED TRADING ENABLED = NO`
