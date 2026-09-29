INCREMENT:
ATDD-BRIDGE-v1

OWNER-APPROVED INTENT:
Implement the user's 28 September 2026 request for a minimal Windows-local GitHub-to-Cursor engineering bridge in a new feature worktree from current main. This contract records that explicitly requested scope before implementation. The subsequent explicit request to enable all review/approval options and fully autonomous development additionally authorizes bounded autonomous task admission, sandboxed tests, feature-branch commit/non-force-push, draft PR publication and separate-identity GitHub reviews. Future tasks require either exact OWNER approval or an OWNER-approved, hash-pinned registered-work policy. V1 uses a tool-denied Cursor proposal followed by deterministic bounded file writes. No production, runtime, trading, merge or deployment authority is granted.

ARCHITECTURE REFERENCES:
- Base main: ca9d6e308e3a2b63c5185e9ae10d5c45fa107628, verified against GitHub on 28 September 2026.
- AGENTS.md and CLAUDE.md at that base: feature-branch isolation, credentials protection, validation, explicit commit/push authorization.
- docs/architecture/v1.4.3/SOURCE.md and authoritative OPIP_Profit_Intelligence_Architecture_v1_4_3.docx, SHA256 ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83: paper-only authority and separate human release control.
- docs/architecture/v1.2/CODING_BOUNDARY_CONTRACT.md and A_PAPER_MANDATE.md: no runtime imports or funded authority.
- docs/atdd/README.md and tests/atdd_scope.py: exact implementation map and acceptance traceability.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
a GitHub issue or PR conversation containing typed task and approval comments
WHEN:
the bridge reads an explicitly selected task
THEN:
it accepts only the fixed repository, authenticated allowed task author, immutable comment, exact OWNER approval of the task-body hash, non-expired approval, open issue and latest approval/revocation decision; malformed, ambiguous, oversized, unavailable or mismatching data fails closed

AC-002:
GIVEN:
an approved task and a dedicated clean linked feature worktree
WHEN:
the bridge checks branch, full HEAD, repository identity, active increment, ATDD contract, architecture pins and exact file map
THEN:
wrong repository, main/master/detached checkout, stale HEAD, dirty files, missing contract, unapproved scope, changed authority, frozen or unsafe Windows paths and path aliases stop execution

AC-003:
GIVEN:
a valid task in default dry-run mode
WHEN:
the bridge evaluates the task
THEN:
it records structured validation status without calling Cursor, claiming the task, editing source, or posting to GitHub

AC-004:
GIVEN:
explicit local execution enablement and an OWNER-verified pinned Cursor executable
WHEN:
the bridge requests a proposal
THEN:
Cursor receives only approved context in an empty scratch workspace, a private configuration denying shell/read/write/fetch tools, sandbox enabled, a minimal environment without GitHub/production credentials, bounded time/output, and no force/yolo/MCP approval; launch failures and invalid output stop without applying changes

AC-005:
GIVEN:
Cursor's untrusted structured proposal
WHEN:
the bridge validates and applies it after rechecking approval and repository state
THEN:
only unique explicitly approved text files in the dedicated bridge-tool namespace, top-level test-module namespace or engineering-documentation namespace may be added or replaced; all `__init__.py` and `conftest.py` files and existing gateway/governance/platform tools are frozen; no deletion/rename/binary/symlink/junction/hardlink/credential/frozen path is allowed, every before-hash must match, all edits validate before any write, and a detected conflict or partial application requires OWNER recovery without automatic rollback

AC-006:
GIVEN:
duplicate delivery, overlapping processes, interruption or an ambiguous crash
WHEN:
the same task is seen again
THEN:
a local exclusive run lock and durable SQLite claim permit at most one attempt per task comment; started/failed/partial/applied tasks never automatically rerun, and all status messages omit raw instructions, credentials and model output

AC-007:
GIVEN:
the completed bridge, examples and Windows runbook
WHEN:
the OWNER follows setup, dry-run, activation and recovery instructions
THEN:
the documentation distinguishes tested behavior from unverified live Cursor integration, identifies required OWNER decisions, maps edge cases to tests or manual checks, and preserves existing CI and ATDD scope gates without application imports

AC-008:
GIVEN:
the OWNER's follow-up authorization for fully autonomous development and a local registered-work policy
WHEN:
a GitHub OWNER comment approves that exact policy hash with a bounded expiry and a task matches its pinned increment, branch, contract, authority, instructions and files
THEN:
the bridge may admit that coding task without a second human task approval, but mismatching tasks, revocation, exhausted attempt budget, policy drift and architecture conflicts stop; no task or model may broaden its own policy

AC-009:
GIVEN:
an autonomously admitted task and applied candidate edits
WHEN:
verification and publication run
THEN:
tests execute only in a local Docker Desktop Linux container with no network, no credentials or Git metadata, a read-only copied source snapshot, bounded time/resources/output and an OWNER-pinned preinstalled image and fixed command; only passing tests and an independent fresh Cursor review allow an ordinary feature commit/non-force push and draft PR, with durable phase evidence and no ambiguous-action retries

AC-010:
GIVEN:
a published draft PR from that autonomous task
WHEN:
required GitHub checks and an independent review are evaluated for its exact current SHA
THEN:
the bridge may submit APPROVE or REQUEST_CHANGES using a reviewer identity different from the PR author; missing/failed/pending checks prevent approval, stale SHA or policy stops, retries resume only the pending-check phase without recoding, and no result permits merge/deploy or changes repository protections

AC-011:
GIVEN:
the bridge is started in continuous mode with one configured control issue
WHEN:
new immutable /opip-task comments appear
THEN:
the bridge discovers eligible unseen tasks automatically, processes them one at a time, preserves durable cursor/idempotency state across restart, does not require the operator to manually supply --task-comment for every task, and never executes comments that fail existing authentication/approval/policy checks

AC-012:
GIVEN:
an eligible task is accepted, running, blocked, failed, waiting for CI, pushed, or completed
WHEN:
the bridge changes state
THEN:
it posts or updates a bounded machine-readable status on the control issue without exposing prompts, model transcripts, credentials, environment values, or untrusted exception bodies; posting failure must not incorrectly report task success and must recover conservatively

AC-013:
GIVEN:
autonomous mode evaluates exact-SHA GitHub checks before review approval
WHEN:
required checks are configured/evaluated
THEN:
the bridge requires at minimum the repository's protected checks test, atdd scope, and semgrep/ci, and cannot approve with any of them missing, pending, failed, cancelled, stale, or represented only by a conflicting legacy status

EXPLICITLY OUT OF SCOPE:
- Runtime/app, deploy, architecture, CI, agent-governance, risk, strategy, exchange, paper authority and canonical evidence changes.
- Force-push, merge, deployment, arbitrary shell/test execution or dependency installation. Autonomous ordinary feature commit/push and isolated registered tests are authorized only under AC-008 through AC-010.
- Unrestricted Cursor tool execution, MCP integrations, session resume, cloud agents and autonomous semantic architecture adjudication.
- SonarQube replacement, R3 product implementation, new services/databases/schedulers (SQLite is a local receipt file only).
- Posting real GitHub tasks/approvals or running a paid Cursor task during implementation. Implementing the autonomous feature does not enroll live credentials or invent OWNER work-policy approval.

FROZEN BOUNDARIES:
- All runtime, trading and production authority stays unchanged; no production credentials are read or inherited by Cursor.
- GitHub tasks cannot edit architecture, ATDD contracts, agent policy, workflow files or the bridge's own implementation.
- V1 may edit only exact approved engineering documentation, tools and test files; the local bridge itself is frozen against self-modification.
- Semantic conflict clearance is an explicit OWNER assertion bound to the task hash, not an LLM safety score. Any agent-reported conflict stops.
- Cursor's verified tool-denial behavior is a required trust boundary, not a claim that a Python subprocess is an OS security sandbox.
- Partial writes remain visible for OWNER review; no destructive reset or automatic rollback.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_local_agent_bridge.py::test_control_plane
AC-002 -> tests/test_local_agent_bridge.py::test_repository_and_scope
AC-003 -> tests/test_local_agent_bridge.py::test_dry_run
AC-004 -> tests/test_local_agent_bridge.py::test_cursor_boundary
AC-005 -> tests/test_local_agent_bridge.py::test_proposal_boundary
AC-006 -> tests/test_local_agent_bridge.py::test_receipts_and_failures
AC-007 -> tests/test_local_agent_bridge.py::test_contract_and_runbook
AC-008 -> tests/test_local_bridge_autonomy.py::test_registered_policy
AC-009 -> tests/test_local_bridge_autonomy.py::test_isolated_verification
AC-010 -> tests/test_local_bridge_autonomy.py::test_review_gate
AC-011 -> tests/test_local_agent_bridge.py::test_continuous_discovery
AC-012 -> tests/test_local_agent_bridge.py::test_github_status_reporting
AC-013 -> tests/test_local_bridge_autonomy.py::test_protected_required_checks

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-002 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-003 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-004 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-005 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-006 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-007 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-007 -> OHM-Trade-Agent-v1/docs/engineering/local-agent-bridge.example.json
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md
AC-007 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-008 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-008 -> OHM-Trade-Agent-v1/tools/local_bridge_autonomy.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_local_bridge_autonomy.py
AC-008 -> OHM-Trade-Agent-v1/docs/engineering/local-bridge-autonomy.example.json
AC-008 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-009 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-009 -> OHM-Trade-Agent-v1/tools/local_bridge_autonomy.py
AC-009 -> OHM-Trade-Agent-v1/tests/test_local_bridge_autonomy.py
AC-009 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-010 -> OHM-Trade-Agent-v1/tools/local_bridge_autonomy.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_local_bridge_autonomy.py
AC-010 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-011 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-011 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-011 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-012 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-012 -> OHM-Trade-Agent-v1/tools/local_bridge_autonomy.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-012 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-013 -> OHM-Trade-Agent-v1/tools/local_agent_bridge.py
AC-013 -> OHM-Trade-Agent-v1/tools/local_bridge_autonomy.py
AC-013 -> OHM-Trade-Agent-v1/tests/test_local_bridge_autonomy.py
AC-013 -> OHM-Trade-Agent-v1/docs/engineering/LOCAL_AGENT_BRIDGE_V1.md
AC-013 -> OHM-Trade-Agent-v1/docs/engineering/local-bridge-autonomy.example.json

DEFERRED DISCOVERIES:
- Native Cursor executable availability, verified tool-denial behavior and separate automation-account setup require OWNER machine validation before enablement.
- Autonomous candidate tests require OWNER provisioning of a pinned local Docker Desktop Linux image; no image is installed/pulled automatically. Until provisioned, autonomous execution fails closed.
- V1 supports issue/PR conversation comments, not inline review comments; bounded one-shot polling may be invoked manually or by existing Windows scheduling.

UNAPPROVED SCOPE CHANGES:
NONE
