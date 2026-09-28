INCREMENT:
ATDD-R0R1-audit-reconciliation

OWNER-APPROVED INTENT:
Bring the authoritative v1.4.3 architecture pin, the conformance ledger, the recovery roadmap, the retirement ledger, and the runtime-truth record forward onto the current post-R2 production baseline with zero application, runtime, deployment, or trading-authority change. Preserve the owner-supplied v1.4.3 DOCX byte-for-byte. Reconcile by merging current main into the audit branch, resolving in favour of current main for implementation/runtime truth while preserving the audit's authoritative documents. Record R2 as completed, merged, and deployed; record R3 as the next implementation increment; preserve historical evidence rather than rewriting it. This increment authorizes documentation and its acceptance tests only.

ARCHITECTURE REFERENCES:
- O'Pip Profit Intelligence Platform Architecture v1.4.3 (22 September 2026): repository authority copy `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx`, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`.
- docs/architecture/v1.4.3/ARCHITECTURE.md: paragraph extraction; the DOCX controls if the extraction and the DOCX disagree.
- docs/architecture/v1.4.3/SOURCE.md: identity and hash pin.
- docs/atdd/scope-contracts/ATDD-000-scope-control.md: ATDD is subordinate to approved architecture and does not authorize implementation, grant trading authority, or change runtime behavior.
- AGENTS.md and CLAUDE.md: no production/runtime/trading-authority change; preserve public contracts; distinguish observed evidence from hypotheses.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the approved reconciliation scope contract and the active increment pointer
WHEN:
the pointer is read and a file outside this contract's implementation map is changed
THEN:
the pointer names ATDD-R0R1-audit-reconciliation, this contract is the single authorizer for the increment, and any unmapped changed path fails the scope check closed

AC-002:
GIVEN:
the historical R0/R1 audit base and the current post-R2 production baseline
WHEN:
CURRENT_ARCHITECTURE_STATUS.md is read
THEN:
it distinguishes the historical audit base `a416be0a068dc58543a4b6cd254d5c42fcaf4c96` from the current reconciled code/production baseline `facf8e369e1251697bf9799bc9b1c575a9cdc3ec`, and it records that the R0/R1 audit and R2 are completed, R2 is merged and deployed, the Feature Bus remains off, R3 has not started, and funded trading remains disabled

AC-003:
GIVEN:
the accepted R2 shadow parity/replay evidence and the unchanged runtime
WHEN:
the F2 conformance-ledger entry is read
THEN:
it records both the accepted deterministic point-in-time shadow replay/parity proof and the absence of any runtime authority: `OPIP_FEATURE_BUS_MODE` is off and `run_cycle` does not call the Feature Bus, without converting absence of evidence into completion for any other feature

AC-004:
GIVEN:
the completed R2 increment
WHEN:
OPIP_RECOVERY_ROADMAP.md is read
THEN:
R2 is marked complete with the actual completed proof, and R3 is the single next implementation phase with the order IGNITION pure detector `evaluate(FeatureSnapshot, DetectorState, evaluation_time)`, one opportunity lifecycle, one feasibility seam using existing vetoes, one calibrated forecast owner, and one constrained economic/portfolio selector, staying shadow/evidence-first with no Paper-v2 cutover and no deletion

AC-005:
GIVEN:
the retirement ledger
WHEN:
it is read
THEN:
nothing is deleted, no Feature Bus cutover is claimed, and retirement prerequisites are updated only where R2 evidence satisfies a prior condition while the live legacy feature/scanner path remains available

AC-006:
GIVEN:
the owner-gated production deploy for the current baseline
WHEN:
OPIP_RUNTIME_TRUTH_2026-09-28.md is read
THEN:
it records only observed facts for `facf8e369e1251697bf9799bc9b1c575a9cdc3ec` (deploy SUCCESS, post-commit health OK, learning export SUCCESS, learning readiness READY, paper-registry genesis valid, scheduler reconciliation OK, healthy core/canonical-writer/stream-worker/Freqtrade containers, `OPIP_FEATURE_BUS_MODE` pinned off, and the stream-worker reconciliation degraded rc=1 observation) and does not infer unobserved host values

AC-007:
GIVEN:
the owner-supplied v1.4.3 architecture authority
WHEN:
the DOCX bytes and the source pin are inspected
THEN:
the DOCX SHA256 equals `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`, the increment changes no application/runtime/workflow/ATDD-checker file, and no test is weakened

EXPLICITLY OUT OF SCOPE:
- Any application or runtime code change
- Any workflow, ATDD checker, or ATDD acceptance-form change
- Feature Bus activation
- Paper-v2 activation or cutover
- Committee activation or model-route change
- R3 (F3 IGNITION) implementation or any later phase
- Scheduler, deploy, risk, execution, or trading-authority change
- Owning, moving, editing, or re-hashing the v1.4.3 DOCX
- Rewriting or deleting historical evidence
- Legacy retirement or deletion
- Unrelated refactors, scanner or gate redesign
- Modifying ATDD-000

FROZEN BOUNDARIES:
- The v1.4.3 DOCX bytes are unchanged and equal to the recorded SHA256.
- `OPIP_FEATURE_BUS_MODE` remains off and `run_cycle` does not call the Feature Bus.
- Funded trading remains disabled; paper execution stays isolated from funded order endpoints.
- Risk, strategy, execution, and Committee authority are unchanged.
- The canonical writer remains the single domain write path.
- Historical runtime truth and audit documents are preserved, not rewritten.
- No merge, deploy, or activation occurs under this increment.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_r0r1_audit_reconciliation.py::test_active_increment_authorizes_only_reconciliation_docs
AC-002 -> tests/test_opip_r0r1_audit_reconciliation.py::test_status_distinguishes_historical_and_current_baseline
AC-003 -> tests/test_opip_r0r1_audit_reconciliation.py::test_conformance_records_r2_proof_and_no_runtime_authority
AC-004 -> tests/test_opip_r0r1_audit_reconciliation.py::test_roadmap_marks_r2_complete_and_r3_next
AC-005 -> tests/test_opip_r0r1_audit_reconciliation.py::test_retirement_ledger_preserves_history_and_claims_no_cutover
AC-006 -> tests/test_opip_r0r1_audit_reconciliation.py::test_runtime_truth_records_only_observed_facts
AC-007 -> tests/test_opip_r0r1_audit_reconciliation.py::test_authority_docx_hash_is_unchanged

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R0R1-audit-reconciliation.md
AC-002 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-004 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RECOVERY_ROADMAP.md
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RETIREMENT_LEDGER.md
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RUNTIME_TRUTH_2026-09-27.md
AC-006 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_RUNTIME_TRUTH_2026-09-28.md
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.3/ARCHITECTURE.md
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/v1.4.3/SOURCE.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_r0r1_audit_reconciliation.py

DEFERRED DISCOVERIES:
- The live `OPIP_PAPER_V2_MODE` value and Paper v1 `control.json` remain unobserved; the live paper authority stays `UNKNOWN_NEEDS_EVIDENCE`.
- The Committee implementation routes differ from the v1.4.3 provisional benchmark candidates; recording the drift does not resolve it, and a separate owner review or registry change is required.
- The learning worker has not been re-deployed to the current core SHA; a matching `/deploy-learning` is a separate owner action.
- The deploy-time stream-worker reconciliation emitted `degraded (rc=1)`; diagnosing it is an operations item, not this increment.
- v1.4.3 section 13 platform acceptance scenarios and R3+ remain future increments.

UNAPPROVED SCOPE CHANGES:
NONE
