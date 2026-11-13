# O'Pip v6 spec

Status: approved direction, built slice by slice by the automated loop. Date: 2026-11-12.
Inputs: `docs/V6_AUDIT.md` (what to keep, migrate, retire) and the owner's decisions below.

## 1. Goal

O'Pip stays an advisory Kraken spot scanner with Telegram alerts. v6 makes it measurable and safe to change:
every signal carries the strategy, model and gate policy that produced it, a Scorecard compares them, data is
tiered and bounded, and unused code is retired in small reviewed steps.

## 2. Operating model (no human handover)

- Claude is tech lead and designer: writes this spec, scope contracts and slice issues, reviews risky PRs.
- Cline with DeepSeek (Flash first, Pro on retry) builds each slice in GitHub Actions.
- GitHub Actions orchestrates: `build` label starts a slice, CI verifies, a failed check gets at most two automatic fixes, a merge advances the next `v6-queue` slice.
- The owner approves and merges. Nothing else needs a human.
- Hard limits: no trading authority, no Kraken order permissions, builder never pushes to main or edits `.github/`, `deploy/`, `CLAUDE.md`, `AGENTS.md`, or the loop tooling.

## 3. Owner decisions in force

- Postgres goes on a separate data box; two droplets in nyc1 around slice 8.
- Hot data window is 14 days; older data ships to the data box and is deleted only after hash verification.
- Paper trading stays off until after migration. Off means off; scans are resource-aware.
- Alerts: manual-only while reconciliation tracking is off (PR #526, deployed). F11 (20 unmanaged holdings) is an owner decision, not a slice.
- PR #521 (capacity sizing) is skipped.

## 4. Slice rules

- One small change per issue, with GIVEN/WHEN/THEN criteria, files likely involved, out of scope and dependencies.
- Additive first: new pure modules and read-only tools before any wiring into the scanner path.
- Every slice has a scope contract, acceptance tests and passes `pytest` and `compileall`.
- Retirements follow disable, observe two weeks, then delete; one theme per PR (audit section 11).

## 5. First slices (queue order)

| # | Slice | Depends on |
|---|---|---|
| A | Version stamp module: strategy, model, gate fingerprint in one dict, pure and tested | none |
| B | Archive root design notes to `docs/archive/` with an index (audit R1) | none |
| C | Read-only retention dry-run report: sizes per dataset and what the 14-day policy would prune | none |
| D | Stamp qualification records with the version stamp (additive field, gating unchanged) | A |
| E | Scorecard core: pure hit-rate per strategy version from outcome records | A, D |

## 6. Later slices (not yet queued)

Canonical file roll and shipping, data box migration, Ops Center config and data pages, event intelligence
activation, Freqtrade results into the Scorecard, retirement themes R2 to R6, committee keep-or-retire decision.

## 7. Safety boundary

External AI is advisory. Deterministic gates fail closed when it is unavailable. No slice may add or widen order
placement, modification, cancellation or confirmation. Paper and production authority stay isolated.
