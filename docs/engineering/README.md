# Engineering work record

[AGENTS.md](../../AGENTS.md) is the canonical policy. This directory supplies a passive machine-readable template, not an executor or deployment automation. It does not change existing CI, runtime configuration, permissions, or control planes.

Copy `workflow.template.json` into one task-owned record only when needed. Use a unique task ID. Keep one canonical record and evidence registry per task; reference existing evidence by ID/URI instead of copying logs, facts, review histories, or secrets. Never commit credentials or sensitive raw output. The template is unfilled and does not assert completed work.

`state` must be one of `states`. Advance only along `transitions` after the exit gate is satisfied; append a transition entry with actor, time, source SHA, destination, and evidence IDs. Set `status` to `blocked` with a reason when a gate cannot be met; never advance because time elapsed. Values for status are `active`, `blocked`, or `complete`. Gate/check results are `pending`, `pass`, `fail`, or `not_applicable` (the latter requires rationale). Null means unknown, never approval or success.

| State | Exit gate |
| --- | --- |
| RESEARCH | Verify repo/branch/head and dirty baseline; locate architecture, owners, existing rules, and canonical facts; record missing context. |
| PLAN | Define authorized scope/non-goals, PR #237 isolation, behavior/state impact, validation, and recovery plan. |
| IMPLEMENT | Make scoped changes while preserving user edits and authority boundaries. |
| TEST | Run applicable checks; record failures and evidence against the tested source. |
| REVIEW | Review correctness, authority, leakage, stateful failure modes, and scope; record findings. |
| FIX | Address findings; if none, record a no-op rationale. |
| RETEST | Re-run affected checks after fixes. If no changes, reference the same valid test evidence with rationale. Failures return to FIX. |
| PR | Human-authorized commit/push only; record PR URL and full head/base SHA with validation and unresolved risks. |
| FINAL_REVIEW | Verify fresh remote exact head, base/integration, required CI, and findings; head changes invalidate prior approval. Findings return to FIX. |
| INTEGRATE | Human merge approval for the exact reviewed SHA; record actual merge SHA and integration checks. Stop on drift. |
| DEPLOY | Separate human approval for exact tested main SHA; rollback release/procedure/triggers and health baseline ready; use existing control plane only. |
| VERIFY | Verify running SHA, health, and required release alignment. On failure, record incident and approved rollback outcome; block until resolved. |

The normal sequence is RESEARCH → PLAN → IMPLEMENT → TEST → REVIEW → FIX → RETEST → PR → FINAL_REVIEW → INTEGRATE → DEPLOY → VERIFY. Work scoped without deployment may finish at INTEGRATE with an explicit human scope reference and deployment disposition `not_requested`; do not fabricate DEPLOY/VERIFY success. Local-only work may remain at PR with `status: blocked` pending commit/push authorization. This governance task does not authorize either action.

Populate `stateful_checks` for stateful changes. Every not-applicable check needs a reason; empty evidence or self-authored approval placeholders cannot satisfy gates. Approvals must reference an actual human decision and its exact scope/SHA. Any subsequent relevant SHA or scope change requires renewed validation/approval. `history` records transitions; `evidence` stores unique references, not copies of authoritative source histories.
