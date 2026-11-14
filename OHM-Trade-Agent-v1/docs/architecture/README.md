# O'Pip V6 — continuous architecture and contract observations

**Current phase: architecture-first / advisory evidence.** No additional CI block, reviewer requirement, funded execution or cutover authority is introduced by this feature.

## Durable system

1. **Contract catalog**: `docs/architecture/contract_catalog.json` enumerates seven high-risk invariants, affected source paths and *references* to relevant tests/workflows. References are **not** proof that tests cover or validate an invariant.
2. **Lightweight observer**: `tools/architecture_gap_capture.py` uses stdlib-only file matching plus a narrow Python AST check for direct F11 imports from `app.opip.data_platform`. A touched seam is an **UNVERIFIED review candidate**, never a confirmed bug or security clearance. Dynamic imports, behavioral bypasses, transactions and missing contracts cannot be fully detected by this script.
3. **Automatic non-blocking capture**: `.github/workflows/architecture-observe.yml` triggers on relevant PR file changes. It runs no LLM calls, dependencies, database, network API queries or full test suite. It writes summary + JSON/Markdown artifacts. Weekly baseline checks evidence reference availability. It never creates or edits GitHub issues or merges PRs.
4. **Confirmed findings register**: [GitHub issue #589](https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/589) is the durable source of truth for triaged defects, architecture misses and missing executable contracts. Engineering conversation stays in [#569](https://github.com/Opr1130/OHM-Trade-Agent-v1/issues/569).

## Disposition

- **P0 immediate:** verified leak of actual credentials, unauthorized funded trading, F11 protection compromise, destructive storage/data-loss risk. Escalate immediately, without waiting for release readiness.
- **P1/P2 architecture findings:** triage with source/line, exact commit, evidence, affected contract, severity, remediation PR, milestone, owner and status. Do not automatically stop unrelated V6 feature PRs; any critical new safety defect still requires correction.
- **Before production:** resolve all accepted P0/P1 release blockers, verify contract tests and storage restore/failure isolation, perform a consolidated security review, then separately propose new branch protection requirements for owner approval.
- **Existing CI stays unchanged:** Gitleaks PR secret scanning, Semgrep, Bandit release gate, ATDD and mandatory safety tests are NOT relaxed. The observation workflow is **non-required**; do not add it as a branch-protection check at this phase.

## Manual local run

From repository root:

```bash
printf 'OHM-Trade-Agent-v1/app/opip/data_platform/business_store.py\n' > /tmp/v6-architecture-changed-paths.txt
python OHM-Trade-Agent-v1/tools/architecture_gap_capture.py \
  --changed-paths-file /tmp/v6-architecture-changed-paths.txt \
  --out-json /tmp/v6-architecture-findings.json \
  --out-markdown /tmp/v6-architecture-summary.md
```

Use `--baseline` with no changed paths to inventory references. Review output; do not promote heuristic candidates to defects without reproducing the issue.

## Aikido read-only pilot (requires account authorization)

- [Official GitHub connection instructions](https://help.aikido.dev/code-scanning/connect-your-source-code/connect-github-account-to-aikido) describe read-only GitHub App access and repository selection.
- Owner connects **only** `Opr1130/OHM-Trade-Agent-v1` through Aikido, if desired. This branch does not connect an Aikido account or grant access.
- Start with baseline SAST, secrets, SCA, IaC and license findings; leave PR gating, autofix, merge writes, cloud/runtime deployment access and blocking actions disabled.
- Triage only verified findings into #589, deduplicating existing Bandit, Semgrep, Gitleaks and Codex reports. Measure novel actionable findings, false positives and costs before adopting or replacing tools.
- Never paste provider secrets or credentials into GitHub issue comments or artifacts.
