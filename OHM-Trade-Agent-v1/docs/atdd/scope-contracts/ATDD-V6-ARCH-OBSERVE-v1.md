INCREMENT:
ATDD-V6-ARCH-OBSERVE-v1

OWNER-APPROVED INTENT:
Build an advisory-only, durable Architecture and Contract Gap Register foundation during architecture-first V6 engineering. Record high-risk changed seams and possible missing proofs as UNVERIFIED candidates, not automatic defects or CI failures. Aikido is a proposed optional read-only future trial. Do not tighten mandatory gates, relax existing gates, merge, deploy, enable learning, expand F11 authority or activate PostgreSQL cutover.

ARCHITECTURE REFERENCES:
- O'Pip EVIDENCE_SHADOW release profile and independent F11 protection remain unchanged
- GitHub issue #589 is the durable, triaged contract-gap register; #569 is engineering coordination
- .github/workflows/pytest.yml preserves focused release and mandatory safety receipts
- .github/workflows/secret-scan-pr.yml remains blocking Gitleaks PR secret scanning
- .github/workflows/architecture-observe.yml is an extra non-required observer
- OHM-Trade-Agent-v1/docs/architecture/contract_catalog.json is a reference catalog, not executable verification of every invariant

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
a source change touches a catalogued high-risk implementation seam
WHEN:
the stdlib-only architectural observer captures that changed path
THEN:
it reports an UNVERIFIED review candidate, with its contract ID and evidence question; it records zero confirmed defects and never makes merge or production decisions

AC-002:
GIVEN:
a protected F11 Python source imports the business-store module directly
WHEN:
the observer inspects its parsed Python AST
THEN:
it records a precise static direct-import candidate including the source line, without claiming to prove an entire runtime dependency graph

AC-003:
GIVEN:
a contract points to evidence and test reference patterns
WHEN:
a baseline or changed-path report is produced
THEN:
the report deterministically distinguishes found, missing and changed proof references; test-file presence alone is not called validation and no issue is automatically created

AC-004:
GIVEN:
changed path input or contract catalog contains malformed, unsafe or duplicate identifiers
WHEN:
the observer validates its inputs
THEN:
unsafe paths and duplicate contract identifiers are rejected before further analysis; normal changed paths remain deterministic

AC-005:
GIVEN:
automatic architecture observation is introduced for V6 construction
WHEN:
the new workflow is reviewed
THEN:
it uses read-only GitHub permissions and uploads nonblocking evidence artifacts; existing required Pytest, mandatory safety, Bandit, Semgrep, Gitleaks and release architecture checks are not weakened

EXPLICITLY OUT OF SCOPE:
- Adding new required merge reviews or tightening branch-protection rules
- Enforcing or correcting all architecture contracts during feature implementation
- Triggering full pytest, Docker fault injection, LLM review, Aikido authentication, secret access or paid API calls
- Automatic PR comments, opening issues, auto-fixes, merges, deployment, PostgreSQL migrations, F11 runtime changes or funded trading

FROZEN BOUNDARIES:
- Existing EVIDENCE_SHADOW, F11, funded-trading OFF, Learning OFF and Committee OFF
- Existing required exact-head CI, ATDD traceability, mandatory safety and Gitleaks checks
- Human verified gap promotion to issue #589, not heuristics treated as verified defects

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_architecture_gap_capture_v1.py::test_reports_affected_contract_without_calling_it_a_confirmed_bug
AC-002 -> tests/test_architecture_gap_capture_v1.py::test_catches_direct_forbidden_import_as_candidate_not_proven_failure
AC-002 -> tests/test_architecture_gap_capture_v1.py::test_from_import_of_forbidden_submodule_is_not_missed
AC-002 -> tests/test_architecture_gap_capture_v1.py::test_unrelated_from_import_is_not_a_candidate
AC-003 -> tests/test_architecture_gap_capture_v1.py::test_baseline_scans_existing_files_for_forbidden_imports
AC-004 -> tests/test_architecture_gap_capture_v1.py::test_symlinked_source_is_not_followed
AC-003 -> tests/test_architecture_gap_capture_v1.py::test_baseline_is_inventory_only_and_existing_proof_not_claimed_validated
AC-003 -> tests/test_architecture_gap_capture_v1.py::test_absent_proof_is_reported_not_auto_created
AC-003 -> tests/test_architecture_gap_capture_v1.py::test_changed_proof_and_duplicate_paths_are_deterministic
AC-004 -> tests/test_architecture_gap_capture_v1.py::test_rejects_unsafe_changed_paths
AC-004 -> tests/test_architecture_gap_capture_v1.py::test_catalog_rejects_duplicate_ids
AC-005 -> tests/test_architecture_gap_capture_v1.py::test_advisory_workflow_does_not_change_required_gate_or_write_issues

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/architecture_gap_capture.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_architecture_gap_capture_v1.py
AC-002 -> OHM-Trade-Agent-v1/tools/architecture_gap_capture.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_architecture_gap_capture_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/contract_catalog.json
AC-003 -> OHM-Trade-Agent-v1/tools/architecture_gap_capture.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_architecture_gap_capture_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/README.md
AC-004 -> OHM-Trade-Agent-v1/tools/architecture_gap_capture.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_architecture_gap_capture_v1.py
AC-005 -> .github/workflows/architecture-observe.yml
AC-005 -> OHM-Trade-Agent-v1/tests/test_architecture_gap_capture_v1.py
AC-005 -> OHM-Trade-Agent-v1/docs/architecture/README.md
AC-005 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-V6-ARCH-OBSERVE-v1.md

DEFERRED DISCOVERIES:
- Evaluate Aikido read-only scanning, with owner-controlled account connection and no auto fixes
- Independently review uncovered runtime and SQL transaction gaps before PostgreSQL authority cutover
- Owner-gated stricter branch rules and deterministic architecture tests at release-readiness milestone

UNAPPROVED SCOPE CHANGES:
NONE
