# K. Legacy disposition and migration governance

## JSONL evidence

Legacy JSONL evidence is **non-authoritative** for new calibration and promotion after the dependency-checked stop-writing timestamp.

That timestamp is named at technical cutover after a consumer census, not invented as a PR 1 calendar date.

Archive and provenance are required. Historical evidence is not deleted in the later removal PR.

## Superseded proposed Profit Intelligence implementation path

A previously proposed Profit Intelligence implementation path (feature-bus / recovery design) is **superseded**. It is not the foundation of this platform.

Useful defect descriptions or regression fixtures from that path may be salvaged later as design or test evidence.

Do **not** refer to that path by a GitHub pull-request number.

## GitHub PR #233

GitHub PR #233 (`fix/discovery-rotation-aware-checkpoint`) is a **different existing artifact**.

PR 1 MUST NOT merge, close, supersede, modify, or otherwise disposition GitHub PR #233.

## Owners and dates

| Role | Name |
| --- | --- |
| Business Decision Owner | Ohm Prakash |
| Technical Release Owner | Ohm Prakash |

| Gate | Value |
| --- | --- |
| Implementation start | 2026-09-11 |
| 30-day checkpoint | 2026-10-11 |
| Permitted actions | CUT OVER / REDUCE SCOPE + bounded new date / ROLL BACK |
| Default if no decision | Freeze feature expansion on both paths |
| Comparator termination | Technical cutover |
| Obsolete-code deletion | 14 days after cutover unless a named extension is recorded |

Do not force statistical promotion to meet a calendar date.

Rollback restores the last approved single authority. It does not reactivate suspect evidence as trustworthy or run two allocation authorities.

See [CLEANUP_MATRIX.md](CLEANUP_MATRIX.md) and [MIGRATION_CALENDAR.md](MIGRATION_CALENDAR.md).
