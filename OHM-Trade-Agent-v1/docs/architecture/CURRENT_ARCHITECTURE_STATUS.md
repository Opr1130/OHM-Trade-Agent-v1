# Current architecture status

Audit date: 2026-09-27. This file is the repository hierarchy for architecture, implementation, and production truth. It does not activate, cut over, or retire any runtime.

## Architecture authority

O’Pip Profit Intelligence Platform Architecture v1.4.3, dated 22 September 2026.

Repository representation:

- `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx` (authority)
- `docs/architecture/v1.4.3/ARCHITECTURE.md` (paragraph extraction)
- `docs/architecture/v1.4.3/SOURCE.md` (identity and hash)

v1.4.3 is newer than v1.4.2. It incorporates the inherited v1.2 architecture, the v1.3 integrated-platform direction, the v1.4 interactive dashboard contract, the v1.4.1 AI Model Registry, the v1.4.2 decision-first alert and evaluation protocol, and the v1.4.3 Intelligence Committee paper-trade monitoring, Weakness Finding Registry, quantitative trust, and mandatory Committee/dashboard measurements.

## Implementation truth

Exact Git commit under this audit:

`a416be0a068dc58543a4b6cd254d5c42fcaf4c96`

Subject: `Profit Intelligence: read-only semantic and economic analytics foundation (#282)`

`origin/main` matched that SHA at audit start and was rechecked before this document was written. The audit tree is the isolated worktree for `chore/opip-r0-r1-architecture-recovery`. The shared checkout on `feature/p1a-decision-intelligence-foundation` was not used as implementation truth.

## Production truth

Exact deployed runtime evidence is recorded in `OPIP_RUNTIME_TRUTH_2026-09-27.md`.

The strongest observation is the owner-gated production deploy log for this same SHA, completed 2026-09-27T15:04:03Z, with `OPIP_CORE_DEPLOY_STATUS=SUCCESS`, `OPIP_CORE_POSTCOMMIT_HEALTH=OK`, and `OPIP_LEARNING_READINESS=READY` for the canonical replica export. That log is deploy-time evidence. It is not a later live re-probe. A fresh SSH session was not opened, because the production SSH forced command is a deploy.

## Historical architecture and contracts

`docs/architecture/v1.2/` remains the clause-level history and the unchanged v1.2 constraints. Its `SOURCE_PIN.md` still records `808a308cd274d30b55fef47b382c229b761e07df` as the PR 1 contracts pin. That pin is historical. It does not override explicit v1.4.3 amendments, and it is not the current `main` SHA.

`OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md` is a design for review. It refines parts of F1–F7 and F11. It is not a second architecture authority.

## Evidence classes that are not architecture authority

| Class | Use |
| --- | --- |
| PR descriptions | Implementation evidence only |
| GitHub issues and workflow logs | Control-plane and deploy evidence only |
| Agent and chat summaries | Context only. Never implementation proof |
| Repository v1.2 documents | Inherited requirements where v1.4.3 has not superseded them |
| v1.4.2 file in the owner folder | Superseded by the v1.4.3 body |

## Core feature set

F1 through F12 are defined in v1.4.3 Appendix A and amended by sections 1–21. The conformance ledger records code and runtime status for each feature plus the adjacent authorities named in the recovery audit.

## Authority statements for this audit

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`FUNDED TRADING ENABLED = NO`

`FEATURE BUS ACTIVATED BY THIS AUDIT = NO`

`PAPER V2 ACTIVATED BY THIS AUDIT = NO`

`COMMITTEE ACTIVATED BY THIS AUDIT = NO`
