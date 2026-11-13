# O'Pip — canonical writer and data scalability (v1)

Work Package **B** of the resource-remediation mission. Scope: the canonical
writer's long-lived projections and one JSONL retention reader. No trading,
paper-admission, learning, release-profile or exchange authority is touched, and
no durable evidence is rewritten.

## 1. Defects addressed

| ID | Severity | Defect | Evidence |
| --- | --- | --- | --- |
| B-1 | P1 | Every projection hydration and refresh ran one `fetchall()` over the whole per-type history, so startup memory and refresh cost grew with total history. Three hydrations run in `CanonicalWriter.__init__`, i.e. on every writer start. | `app/opip/canonical/writer.py` (pre-change `_hydrate_*` / `_refresh_*`) |
| B-2 | P1 | `_role_result_idempotency_by_id` retained one entry per role result ever recorded, for the life of the process. | same |
| B-3 | P1 | The opportunity-lifecycle projection retained every episode ever recorded — including closed/terminal ones — in three parallel dictionaries. | same |
| B-4 | P1 | The request-lifecycle projection retained every request id ever seen. | same |
| B-5 | P1 | Dead-letter retention materialised the whole JSONL file **twice** (count, then slice) in order to bound it. | `app/opip/events/storage.py` |

## 2. Requirement → test → implementation → result

Tests: `tests/test_opip_canonical_writer_scale_v1.py` (12 passed).

| Req | Acceptance test | Implementation | Result |
| --- | --- | --- | --- |
| Bound startup hydration with cursor/chunked processing | `test_ac_001_hydration_reads_one_page_at_a_time`, `test_ac_001_no_projection_fold_materialises_its_type_history`, `test_ac_002_paging_preserves_commit_order_and_terminates` | `_iter_event_rows()` pages with an exclusive `(history_epoch, local_sequence)` cursor and `LIMIT ?` over the existing `idx_events_type_cursor`; all six hydrate/refresh loops consume it | PASS |
| Eliminate permanent retention of closed identities | `test_ac_003_retention_releases_its_oldest_entry_past_the_bound`, `test_ac_007_retained_entries_stay_bounded_while_history_grows` | `_BoundedProjection` (insertion-ordered, releases oldest past `PROJECTION_RETENTION_ENTRIES` = 2048) for all five projection maps | PASS |
| DB-backed current-state where necessary | `test_ac_005_*` | Role-result identity is answered by an indexed anti-join on `idx_events_role_result_identity`; request state by `idx_events_di_request_state` | PASS |
| Released identities still answer | `test_ac_004_*`, `test_ac_007_a_released_identity_is_re_derived_from_the_durable_rows` | `_opportunity_episode_state()` / `_request_current_state()` re-fold from the identity's durable rows | PASS |
| Preserve exactly-once, idempotency, ordering, failure recovery | `tests/test_opip_r3_f4_opportunity_persistence.py`, `tests/test_opip_r3_f4_opportunity_lifecycle.py` | commit order preserved by the cursor; `UNIQUE(idempotency_key)` and every ancestry validator unchanged; the fold still fails closed on impossible history | PASS (101 + 12) |
| Projections transactional and rebuildable | `test_ac_002_*`, `test_ac_004_*` | projections are still advanced only after `COMMIT`; a restart rehydrates from the same canonical rows | PASS |
| JSONL readers must not materialise before limiting | `test_ac_006_dead_letter_retention_reads_only_the_tail` | `read_tail_lines()` (seek-based) replaces two whole-file reads | PASS |
| Immutable evidence and cursors preserved | `tests/test_opip_canonical_writer_pr2.py`, `tests/test_opip_canonical_cursor_read_plan_v1.py`, `tests/test_opip_canonical_store_safety_pr_a0.py` | no table/column change, no rewrite, no evidence migration; only additive indexes | PASS (192 passed, 5 skipped) |
| Validate against substantially larger histories | `test_ac_007_*` | 30 episodes / 60 → 30 rows with a ceiling of 4 retained entries; paged fold proven over multiple pages | PASS |
| Do not raise the RAM limit | — | no compose/resource change in this PR | PASS |

## 3. Behavioural impact and safety statement

1. **What changes.** Resident projection memory now stops at a bound instead of
   growing with history; hydration/refresh issue paged queries instead of one
   unbounded result set. Answers are unchanged: every released identity is
   re-derived from the same durable rows the retained value was folded from.
2. **Authority.** None. No admission, ranking, alert, risk or execution path is
   touched; `MIN_NET_PROFIT`, `MIN_RR` and `risk_per_trade_pct` are untouched.
3. **Durable state.** Two additive filtered indexes; no table, column, row or
   file is created, rewritten or deleted. Existing databases gain the indexes on
   the next writer schema initialisation.
4. **Documented cost.** A *released* opportunity episode is re-derived by a paged
   scan of its event type (bounded memory, O(type) time) because the generic
   schema is contractually free of this increment's vocabulary — no bespoke index
   is added there. With a 2048-entry ceiling and a handful of live episodes per
   cycle, the cold path is effectively never taken.
5. **Out of scope.** No production measurement of writer RSS was possible from
   this workstation; the bound is proven structurally and behaviourally, not by
   host observation.

## 4. Rollback and deployment dependencies

- Ships with the core image; no new secret, service, volume, cron entry or config.
- Rollback is the previous release. The added indexes are harmless to the older
  build (it ignores them), so a rollback needs no schema action.
- An older build opening a store that already carries the indexes is unaffected.

## 5. Explicitly not changed

- No merge, deploy or production activation.
- No change to any trading, paper, learning or release-profile mode.
