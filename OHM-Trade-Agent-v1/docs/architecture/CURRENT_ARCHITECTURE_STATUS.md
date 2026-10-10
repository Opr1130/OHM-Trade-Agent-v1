# Current architecture status

Audit date: 2026-09-27. Reconciliation date: 2026-09-28; R4-B2 status reconciliation 2026-10-03; architecture source adoption 2026-10-10. This file is the repository hierarchy for architecture, implementation, and production truth. It does not activate, cut over, or retire any runtime.

## Architecture authority

O'Pip Profit Intelligence Platform Architecture v1.5.0, dated 9 October 2026 (Continuous Multi-Horizon Capital Intelligence), pinned in `docs/architecture/v1.5.0/SOURCE.md`. It supersedes the v1.4.4 amendment of 4 October 2026 (`docs/architecture/v1.4.4/SOURCE.md`), which amended the v1.4.3 body of 22 September 2026. The earlier statement "O'Pip Profit Intelligence Platform Architecture v1.4.3, dated 22 September 2026." is preserved as history.

Repository representation:

- `docs/architecture/v1.5.0/OPIP_Profit_Intelligence_Architecture_v1_5_0.docx` (authority) with `ARCHITECTURE.md` (paragraph extraction), `SOURCE.md` (identity and hash), and the owner feature-priority tracker (`OPIP_Feature_Priorities_v1_5_0.md`, `OPIP_Feature_Priorities_v1_5_0.csv`)
- `docs/architecture/v1.4.4/OPIP_Profit_Intelligence_Architecture_v1_4_4.docx` (amendment, retained) with `ARCHITECTURE.md` and `SOURCE.md`
- `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx` (baselined prior authority, retained unchanged) with `ARCHITECTURE.md` and `SOURCE.md`

The DOCX is the owner-supplied authority copy. It is committed in this repository at the path above, byte-for-byte, and its SHA256 is `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`. The reconciliation did not modify its bytes. The v1.4.4 copy is byte-identical to its owner file (`9eb48784b540611e2eb538866365d506b1f0e07207ef3ea33502b796f8057768`, 70601 bytes) and the v1.5.0 copy likewise (`69699ffddf87f55da5ba2d120272ec95a63a984d9d74f548a8e4542e3d5f6be2`, 63023 bytes). Neither the earlier reconciliation nor this adoption modified any authority bytes; every adopted package was added beside the existing ones.

v1.4.3 is newer than v1.4.2. It incorporates the inherited v1.2 architecture, the v1.3 integrated-platform direction, the v1.4 interactive dashboard contract, the v1.4.1 AI Model Registry, the v1.4.2 decision-first alert and evaluation protocol, and the v1.4.3 Intelligence Committee paper-trade monitoring, Weakness Finding Registry, quantitative trust, and mandatory Committee/dashboard measurements.

v1.4.4 is a focused amendment to v1.4.3 that adds the precision-agent direction, continuous sensing and account-guardian provisions, confirmation/contradiction, selective Committee analysis, measurable experience learning, causal confidence and attribution, F10 cockpit requirements, and traceability scorecard semantics. v1.5.0 is the architecture revision that defines R5 as Continuous Multi-Horizon Capital Intelligence (Continuous Market Eye, horizon intelligence, opportunity and position lifecycles, capital intelligence and accounting, portfolio planning and selection, capital rotation, autonomous paper portfolio management, expected-versus-actual learning, economics and capital-time metrics) and preserves the earlier repository R5 outcome-consolidation and cockpit milestone as `R5-LEGACY-OUTCOMES-COCKPIT`.

Adoption of these sources is documentary. It records identity, hash and repository path, re-baselines the truth documents below, and grants no authority: it approves no v1.5.0 increment, changes no risk, execution, release-profile or trading control, and activates nothing. R5-0's owner assignment and the record, horizon, accounting and performance-budget freezes named in the adopted section 14 remain open, as do R5-A to R5-F, `TARGET_PAPER`, cutover, and every production configuration change.

## Implementation truth

Two exact Git commits matter, and they are different:

| Role | Commit | What it is |
| --- | --- | --- |
| Historical audit base | `a416be0a068dc58543a4b6cd254d5c42fcaf4c96` | The commit the R0/R1 audit recorded. Subject `Profit Intelligence: read-only semantic and economic analytics foundation (#282)`. It is history, not current implementation truth. |
| Current reconciled code baseline | `a808e84ffc2fea4912cfa5592927d1afe2956568` | The current `origin/main` after the R3 F3-F7 spine (#287-#300), R4-B1 dormant wiring (#308-#311), R4-B2 Slice 3A (#317-#324) and R4-B2 Slice 3B (#325-#326). The earlier reconciliation baseline `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` (R2, #284) is preserved as history. |
| Deployed production core | `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` (last observed) | The last deploy-time observation recorded below. No later production re-probe has been performed; the deployed SHA is not re-observed by this reconciliation. |
| Architecture source adoption baseline (R5-0 architecture source adoption, 2026-10-10) | `8b3cc2712432ca21007be4db0667301d48b89d97` | The `origin/main` commit the adopted v1.5.0 body resolved its repository sources at (v1.5.0 section 19, sources S3–S5). It is an adoption baseline only: not a deploy, not a runtime observation, and not a claim of completion. The prior baseline rows below are preserved as history. |
| Superseded status header (historical, baseline `facf8e36`) | `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` | The earlier reconciliation recorded this commit under the header "Current reconciled code/production baseline". That header and value are preserved as history rather than erased. |

`origin/main` matched `a808e84ffc2fea4912cfa5592927d1afe2956568` when this document was reconciled (2026-10-03). At the earlier 2026-09-28 reconciliation it matched `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`; that earlier statement is preserved as history. `origin/main` was `8b3cc2712432ca21007be4db0667301d48b89d97` when the architecture sources were adopted (2026-10-10); that is also the commit the adopted v1.5.0 body resolved its repository sources at. The audit tree is the isolated worktree for `chore/opip-r0-r1-architecture-recovery`. The shared checkout on `feature/p1a-decision-intelligence-foundation` was not used as implementation truth.

## Production truth

Exact deployed runtime evidence is recorded in `OPIP_RUNTIME_TRUTH_2026-09-28.md`, with `OPIP_RUNTIME_TRUTH_2026-09-27.md` preserved as the preceding historical observation.

The strongest current observation is the owner-gated production deploy run `36473910247` for `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`, completed 2026-09-28T19:47:02Z, with `OPIP_CORE_DEPLOY_STATUS=SUCCESS`, `OPIP_CORE_POSTCOMMIT_HEALTH=OK`, and `OPIP_LEARNING_READINESS=READY` for the canonical replica export. That log is deploy-time evidence. It is not a later live re-probe. A fresh SSH session was not opened, because the production SSH forced command is a deploy. The deploy's stream-worker reconciliation emitted `degraded (rc=1)` (shadow evidence unavailable or incomplete, production core unaffected); that observation is recorded, not erased.

## Recovery state

| Phase | State | Evidence |
| --- | --- | --- |
| R0/R1 audit | completed | This architecture/audit package, recorded at the historical audit base `a416be0a`. |
| R2 — Feature Bus shadow parity and deterministic replay | completed, merged, deployed | PR #284 merged to `main` as `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`; exact pre-merge head `b26dab8d58116c5560e5bbed73ce95f0c814fbeb`; exact-head `test` and `atdd scope` PASS; 0 valid unresolved non-outdated review blockers at merge; production deploy run `36473910247` SUCCESS. |
| R3 — F3 through F7 on the R2 evidence | implemented (shadow / dormant, no runtime authority) | The R3 target modules now exist and are merged (#287-#300): F3 `app/opip/detectors/ignition.py`; F4 `app/opip/opportunity_lifecycle.py` + `opportunity_persistence.py`; F5 `app/opip/feasibility.py`; F6 `app/opip/forecast.py` + `forecast_evaluation.py`; F7 `app/opip/portfolio_selector.py` + `portfolio_comparator.py`. They carry **no production runtime authority**: the Feature Bus is owner-authorized to `shadow` under the `EVIDENCE_SHADOW` release profile (`ATDD-RELEASE-PIPELINE-v1`; superseding the earlier `off` posture), the target spine is composed in `shadow` on every unified cycle (`run_cycle` → `TARGET_SPINE` phase), and because `run_cycle` supplies no snapshot source each pass is a recorded non-authoritative no-op (`TARGET_SPINE_NO_SNAPSHOT_SOURCE`) rather than a disabled gate: it writes no canonical evidence and holds no admission, reservation, order, protection, alert or writer authority, and the F6 production model registry ships empty (`NO_CALIBRATED_MODEL`). The earlier statement "R3 has not started" is preserved as history at baseline `facf8e36`. |
| R4-B2 Slice 3A/3B — controlled paper activation prerequisites | implemented (shadow / dormant) | Durable FeasibilityEvidence codec/event/reader (#318-#320), 60-second cadence bridge (#321), prospective feasibility-evidence producer (#322), SHORT/BTNL integration (#323-#324), bounded reader memory with cursor-owned dedupe (#325), and the prospective F6 evidence contract plus canonical label projection (#326). Paper-v2 is not activated and the target spine is not yet the admission source. |

`FEATURE BUS EVIDENCE ACTIVATED = YES` (owner-authorized) under the `EVIDENCE_SHADOW` release profile (`ATDD-RELEASE-PIPELINE-v1`): the core-service compose `OPIP_FEATURE_BUS_MODE` is `shadow`, but `run_cycle` still does not call the Feature Bus, so the bus owns no decision authority. The earlier reconciliation's `FEATURE BUS ACTIVATED = NO` is preserved as history. Paper v2 was not activated by this reconciliation. The Committee remains advisory/shadow only, with zero runtime trading authority. Funded trading remains disabled.

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

## Authority statements for this reconciliation

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`FUNDED TRADING ENABLED = NO`

`FEATURE BUS ACTIVATED BY THIS RECONCILIATION = NO`

`PAPER V2 ACTIVATED BY THIS RECONCILIATION = NO`

`COMMITTEE ACTIVATED BY THIS RECONCILIATION = NO`

## Authority statements for the architecture source adoption (2026-10-10)

`ARCHITECTURE SOURCE ADOPTED BY THIS ADOPTION = v1.5.0 and v1.4.4 (documentary only)`

`PRODUCTION TRADE AUTHORITY CHANGED BY THIS ADOPTION = NO`

`FUNDED TRADING ENABLED BY THIS ADOPTION = NO`

`FEATURE BUS EVIDENCE ACTIVATED BY THIS ADOPTION = NO`

`PAPER V2 ACTIVATED BY THIS ADOPTION = NO`

`COMMITTEE ACTIVATED BY THIS ADOPTION = NO`

The adopted v1.5.0 body defines R5 as Continuous Multi-Horizon Capital Intelligence and preserves the earlier outcome-consolidation and cockpit milestone in tracking as `R5-LEGACY-OUTCOMES-COCKPIT`. R5-0's owner assignment and the record, horizon, accounting and performance-budget freezes named in its section 14, R5-A to R5-F, `TARGET_PAPER`, cutover and every activation remain unapproved by this adoption.
