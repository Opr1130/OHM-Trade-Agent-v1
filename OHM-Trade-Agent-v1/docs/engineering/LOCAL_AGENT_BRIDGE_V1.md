# O'Pip Local Agent Bridge v1 — Windows operator runbook

This is a small, standard-library Python program for **Opr1130/OHM-Trade-Agent-v1 only**. It reads one explicitly selected GitHub issue/PR conversation task, checks its OWNER approval, asks a local Cursor CLI for a bounded proposal, and validates that proposal before writing approved text files in a dedicated linked feature worktree.

**Default: dry-run. Execution requires both `--execute` and `enable_execution: true`.** Basic mode ends at `APPLIED_UNTESTED`. The bridge reads GitHub through `gh`; the only GitHub write it ever performs is the single bounded status comment per task described in section 6.2, which is created only when execution is enabled. Following the OWNER's explicit request for fully autonomous development, optional registered-policy mode additionally runs isolated tests, performs a fresh review, commits/pushes the approved feature branch, creates a draft PR and submits an exact-SHA GitHub review. See section 12. Neither mode merges, deploys, force-pushes, changes protections or activates trading.

## 1. Scope and trust boundary

V1 accepts exact, case-sensitive file paths only in these repository-root namespaces:

- Python engineering tools under `OHM-Trade-Agent-v1/tools/bridge_tasks/`
- top-level Python tests named `OHM-Trade-Agent-v1/tests/test_*.py`
- text documentation under `OHM-Trade-Agent-v1/docs/engineering/`

Only `.py`, `.md`, `.txt`, and `.json` text files may be added/replaced; tools and tests are Python-only. Every `__init__.py` and `conftest.py` is frozen at any depth because Python or pytest can execute it during import or collection. Each path must also appear in the active approved ATDD implementation map and the exact approved task. The bridge/checker, bridge tests and bridge policy documentation are frozen against self-modification. Existing gateway, governance, platform backup/restore, app/runtime, architecture, ATDD contracts, workflows, deployment files, hidden files and credential-named paths are unavailable to tasks. Deletion, rename, binary edits, wildcards, links and arbitrary commands are unsupported.

This engineering-only limit deliberately excludes R3 trading-product implementation. Expanding it requires a separately approved bridge increment and review of the execution boundary. An instruction that requests a forbidden behavior must not receive OWNER approval. Filename checks cannot prove the semantic meaning of generated code. OWNER approval includes explicit architecture/frozen-boundary clearance; the model can flag a conflict but cannot grant authority.

Cursor is invoked in `ask` mode with sandboxing enabled and deny rules for `Shell(*)`, `Read(*)`, `Write(*)`, and `WebFetch(*)`. It receives a JSON prompt on stdin containing only the selected task, contract and approved file contents. It runs in a new empty scratch directory, with a private Cursor configuration, no inherited PATH/profile/MCP configuration and no inherited GitHub, SSH, exchange or cloud credentials. Only `CURSOR_API_KEY` is forwarded for model authentication. Context sent to Cursor still reaches Cursor's model service; local execution does not mean offline inference.

**This is a policy-constrained subprocess, not an independent Windows security sandbox.** The trust boundary requires an OWNER-verified Cursor build that actually honors these restrictions. Use a separate Windows automation account with no production credentials, cloud/SSH access, GitHub write token, sensitive network shares, or personal profile access. Do not run it as Administrator. A compromised Cursor binary, Windows account, Git/gh binary or host is outside v1's guarantees. Executable hashing detects changes; it does not certify a build's safety.

## 2. Prerequisites and decisions before enabling execution

Install or use existing Python 3.12+, Git for Windows, GitHub CLI (`gh`) and a native Windows Cursor CLI. The bridge itself needs no pip package. Pytest is a development/test dependency already used by this repository.

The OWNER must decide:

1. Which dedicated non-administrator Windows account and credential-free workspace will run the bridge.
2. Which audited Cursor CLI build to use. The official Windows package observed during OWNER activation on 29 September 2026 installs `agent.cmd` / `cursor-agent.ps1` wrappers that select a versioned runtime and execute `node.exe index.js`. The bridge still rejects every `.cmd`, `.bat`, `.ps1`, WSL and arbitrary launcher command. For this packaged form, configure the absolute versioned `node.exe` path, its SHA256, the exact sibling runtime directory and the bridge-computed deterministic runtime-tree SHA256. The bridge invokes `node.exe index.js` directly with `shell=False`; it never executes the wrappers.
3. Whether other numeric GitHub user/bot IDs may submit tasks. Default `dispatch_ids: []` permits only the repository OWNER to submit. Only the repository's actual numeric OWNER ID, fetched from GitHub, may approve/revoke; collaborator labels, display names and `author_association` are not authority.
4. The bounded task, approved ATDD contract and non-secret context. OWNER approval of one comment does not authorize future comments, new HEADs, more paths or changed instructions.
5. An execution timeout (1–900 seconds), Cursor account spending controls, and operator availability for interrupted-run recovery. Timeout limits duration, not billed dollars.

No live Cursor sandbox smoke test was possible in the implementation environment. OWNER-machine activation established the current Windows packaging shape and that the vendor `agent.cmd --version` path works, but mocked tests still prove only adapter arguments, runtime pinning, environment, validation and failure handling—not actual vendor sandbox enforcement. Live execution remains disabled until section 7 is completed.

Official vendor references checked 28 September 2026:

- [CLI parameters](https://cursor.com/docs/cli/reference/parameters): ask mode, sandbox and structured output flags.
- [Permissions](https://cursor.com/docs/cli/reference/permissions) and [configuration](https://cursor.com/docs/cli/reference/configuration): deny rules and private configuration location.
- [Output format](https://cursor.com/docs/cli/reference/output-format): successful result envelope.
- [Headless CLI](https://cursor.com/docs/cli/headless): automated usage and Windows installation entry point. Follow vendor installation instructions separately; the bridge never installs or updates Cursor.

## 3. Prepare a clean feature worktree

Use a separate credential-free clone rather than the existing working copy that may contain `.env`, production files or unrelated edits. These are **OWNER setup commands**, not commands executed from GitHub comments. Run in PowerShell and choose your own short local paths to avoid Windows path-length problems.

```powershell
New-Item -ItemType Directory -Force C:\OpipBridge | Out-Null
git clone --bare https://github.com/Opr1130/OHM-Trade-Agent-v1.git C:\OpipBridge\repo.git
git --git-dir=C:\OpipBridge\repo.git fetch origin main
git --git-dir=C:\OpipBridge\repo.git worktree add -b feature/bridge-example C:\OpipBridge\worktrees\engineering-task FETCH_HEAD
$bridgeTree = 'C:\OpipBridge\worktrees\engineering-task'
git -C $bridgeTree status --short
git -C $bridgeTree rev-parse HEAD
```

Do not reuse an existing path/branch blindly. Inspect it and preserve all work first. A branch must match `feature/<letters-digits-underscores-hyphens>`; main, master and detached HEAD are rejected. The worktree's `.git` must be a linked-worktree file. `origin` must be exactly the fixed repository's HTTPS `.git` URL or `git@github.com:Opr1130/OHM-Trade-Agent-v1.git`.

Prepare the task's ATDD contract and acceptance tests through ordinary OWNER review before dispatch: the contract must have all existing ATDD sections, valid GIVEN/WHEN/THEN criteria, acceptance test traceability, an exact implementation map, and `UNAPPROVED SCOPE CHANGES: NONE`. Set `docs/atdd/ACTIVE_INCREMENT` to it. Commit this preparatory work only under separate explicit OWNER commit authorization. The bridge cannot create/approve its own contract. A clean committed task base is required.

The task worktree must have **no modified, untracked or ignored files**, including virtualenvs, caches and `.env`. Keep environments and bridge state outside it, run Python with `-B`, and reserve the checkout exclusively for one bridge. Assume-unchanged/skip-worktree index flags and unfinished Git operations are rejected. Do not clean/reset somebody else's work to satisfy this gate.

The bridge code may run from a separately reviewed tooling checkout containing this implementation. Set `$bridgeApp` to its application directory. Keep that tooling installation read-only to task activity. Do not run a bridge version modified by the task it is evaluating.

## 4. Create local configuration and inspect pins

Copy `docs/engineering/local-agent-bridge.example.json` to `C:\OpipBridge\config.json`. Keep it outside all task worktrees. Edit it locally; never accept its contents from a GitHub comment.

```powershell
$bridgeApp = 'C:\PATH\TO\REVIEWED\BRIDGE\OHM-Trade-Agent-v1'
Copy-Item -LiteralPath "$bridgeApp\docs\engineering\local-agent-bridge.example.json" -Destination C:\OpipBridge\config.json
notepad C:\OpipBridge\config.json
```

Set `worktree` to the linked feature checkout and `state_dir` to `C:\OpipBridge\state`. State must be outside the checkout, and not an ancestor containing it. Use one persistent state directory for every bridge invocation for this repository; never create a fresh state directory to bypass a receipt or lock. Restrict configuration/state ACLs to the automation account and OWNER. Do not place them on OneDrive, a network filesystem, an untrusted/reparse-point path, or in Git administrative storage.

Keep `enable_execution` false. Authenticate `gh` as the automation account with read access to this repository and its issue/PR comments. Do not use a production or write-capable token. Check connectivity without printing tokens:

```powershell
gh api --hostname github.com repos/Opr1130/OHM-Trade-Agent-v1 --jq '{repository: .full_name, owner_id: .owner.id}'
git -C $bridgeTree symbolic-ref --short HEAD
git -C $bridgeTree rev-parse HEAD
```

Collect the active contract's SHA256 from its **actual worktree bytes**, including local line endings:

```powershell
$increment = (Get-Content -LiteralPath "$bridgeTree\OHM-Trade-Agent-v1\docs\atdd\ACTIVE_INCREMENT" -Raw).Trim()
$contractPath = "$bridgeTree\OHM-Trade-Agent-v1\docs\atdd\scope-contracts\$increment.md"
(Get-FileHash -Algorithm SHA256 -LiteralPath $contractPath).Hash.ToLowerInvariant()
Push-Location $bridgeApp
python -B -c "import sys; from pathlib import Path; from tools.local_agent_bridge import authority_hash; print(authority_hash(Path(sys.argv[1])))" $bridgeTree
Pop-Location
```

The authority digest covers `AGENTS.md`, `CLAUDE.md`, the ATDD checker and every file under the checked-in architecture directory, including the authoritative DOCX. Changes require new task approval; a raw byte digest can change with checkout line endings, so do not reuse a digest from another machine.

## 5. Submit a task and approve its exact bytes

Use an open issue or the conversation tab of a same-repository PR. Inline code-review comments are not supported. A PR must target this repository's `main`; its current head branch/SHA must equal the task. Fork PRs are rejected. Issues let you prepare a task before a PR exists.

Post one plain comment starting with `/opip-task` followed immediately by a newline and one JSON object. Do not wrap it in Markdown fences. Replace every placeholder below; unknown JSON keys are rejected.

```text
/opip-task
{
  "schema": 1,
  "repo": "Opr1130/OHM-Trade-Agent-v1",
  "increment": "ATDD-YOUR-APPROVED-INCREMENT",
  "branch": "feature/bridge-example",
  "head": "REPLACE_WITH_FULL_40_CHARACTER_HEAD",
  "contract_sha256": "REPLACE_WITH_64_CHARACTER_CONTRACT_HASH",
  "authority_sha256": "REPLACE_WITH_64_CHARACTER_AUTHORITY_HASH",
  "files": ["OHM-Trade-Agent-v1/docs/engineering/example.md"],
  "instructions": "Describe the exact approved change and acceptance criteria. Do not broaden scope."
}
```

Files are both the allowed write set and the source context sent to Cursor. No implicit repository access occurs. At most 20 files; total context and edits are bounded to approximately 1 MB. A new file has null content and null before-hash. Other dependencies need a revised approved contract/task, not hidden agent exploration.

Obtain the numeric task comment ID from its URL (`#issuecomment-...`). Compute its hash from the GitHub-returned UTF-8 body **without adding a newline**; do not hash a terminal-rendered copy:

```powershell
$taskCommentId = 1234567890  # replace
$commentJson = gh api --hostname github.com "repos/Opr1130/OHM-Trade-Agent-v1/issues/comments/$taskCommentId"
if ($LASTEXITCODE -ne 0) { throw 'Could not read task comment' }
$commentRecord = $commentJson | ConvertFrom-Json
$taskBodyBytes = [System.Text.Encoding]::UTF8.GetBytes($commentRecord.body)
$taskSha256 = [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData($taskBodyBytes)).ToLowerInvariant()
$taskSha256
```

The hash example uses PowerShell 7. Use the OWNER's account to post a separate approval comment after reviewing instructions, source context, ATDD criteria and architecture boundaries:

```text
/opip-approve
{
  "task_comment_id": 1234567890,
  "task_sha256": "REPLACE_WITH_TASK_BODY_SHA256",
  "expires_at": "2026-09-28T18:00:00Z",
  "architecture_clear": true
}
```

Use a future UTC expiry no more than 24 hours after approval creation. `architecture_clear: true` is the OWNER's explicit clearance, not an agent-generated assertion. Comments must remain unedited (`created_at == updated_at`). To correct a task, post a new task and new approval. To renew an unattempted task's expiry, post a new approval. Editing an old approval fails closed.

Revoke with a new OWNER comment:

```text
/opip-revoke
{"task_comment_id":1234567890}
```

The highest comment ID among matching OWNER decisions wins regardless of API list order. An edited relevant decision fails closed. Revocation is checked again immediately before applying edits. Revocation after that final check cannot undo writes already starting; this is a bounded polling protocol, not instantaneous cancellation. Stop the local process for emergency interruption and follow recovery below.

## 6. Dry-run first

```powershell
python -B "$bridgeApp\tools\local_agent_bridge.py" `
  --config C:\OpipBridge\config.json `
  --issue 123 `
  --task-comment 1234567890 `
  --dry-run
if ($LASTEXITCODE -ne 0) { throw 'Bridge validation stopped; inspect its status code' }
```

Replace the issue/PR number and comment ID. `DRY_RUN_VALID` means identity, approval, repository and scope checks passed. Dry-run performs GitHub reads and local validation and appends `status.jsonl`; it does not call Cursor, claim the task, edit source, or write to GitHub. It does not prove Cursor is installed, authenticated or safe. Execution will repeat all checks.

### 6.1 Continuous discovery (`--watch`)

To avoid discovering each comment by hand, poll exactly one control issue:

```powershell
python -B "$bridgeApp\tools\local_agent_bridge.py" `
  --config C:\OpipBridge\config.json `
  --issue 123 `
  --watch --dry-run
```

`--watch` polls only the configured `--issue`, recognises only immutable `/opip-task` envelopes from the OWNER or a configured `dispatch_ids` identity, and processes eligible tasks one at a time in ascending comment-id order. It is a bounded poller: no webhook, no server, no listening port, no repository or issue scanning beyond the one configured issue, and no background registration.

Polling interval: `--poll-seconds` (default 20, allowed 10–300). Restarting the bridge never replays a task: a durable local receipt plus a discovery record under `state_dir` prevent re-execution, and a `watch.lock` prevents two watchers.

Discovery distinguishes four outcomes, and only the last two are durable decisions:

| Outcome | Behavior |
| --- | --- |
| Transient GitHub/read failure | No task consumed; the same poll retries next cycle |
| Approval not yet posted, issue closed/locked | No task consumed; retried later |
| Permanently malformed body (bad JSON, oversized, unknown key, edited, unauthorized) | Rejected once with a fixed reason code; never retried, never executed |
| Eligible | Executed once under the existing lock/receipt rules |

A malformed or oversized `/opip-task` body is translated from the transport parse code to the permanent `INVALID_TASK_ENVELOPE` at the envelope boundary, so it is refused once instead of being retried on every cycle. The transport-level parse code is deliberately left transient, so a malformed GitHub *response* is still retried rather than being mistaken for a bad comment.

A malformed OWNER decision (`/opip-approve`, `/opip-revoke`, or a policy decision) cannot be bound to a task or policy id, so it is **skipped** rather than failing the whole issue: it never authorizes anything, and it never permanently rejects an unrelated task. Only a structurally invalid decision that is clearly bound to the task under evaluation fails that task closed.

A comment that fails a temporary GitHub read is never marked "seen and done". An edited comment is never executed, and a comment whose approval is revoked before execution stops before any write. A failed or ambiguous execution still follows the section 9 recovery rules and is never silently retried.

A published draft PR awaiting CI remains **resumable**: its receipt is `PUBLISHED_WAITING_CI`, which proves coding, tests, commit and push already happened. Later polls revisit only that task's exact-SHA CI/review state and submit the existing review action once CI reaches a terminal disposition. A resume never re-invokes Cursor, never re-runs tests, never re-commits and never re-pushes. Once a disposition is submitted the task becomes terminal and is no longer rediscovered.

`--watch` with an explicit `--task-comment` is refused; use one or the other. The explicit one-shot form in section 6 remains the supported way to run one named task.

### 6.2 GitHub-visible status

When execution is enabled (`--execute`), the bridge also maintains **one** bounded status comment on the same control issue, updating it in place as the task changes state. The comment carries a fixed marker plus strict JSON only:

```text
<!-- opip-local-agent-status:v1 task=1234567890 -->
{"schema":"opip-local-agent-status/v1","task_comment_id":1234567890,"state":"running", ...}
```

States are drawn from a fixed set (`accepted`, `running`, `blocked`, `failed`, `applied`, `testing`, `pushed`, `waiting_ci`, `request_changes`, `approved`, `completed`) and the reason code is always one of the bridge's own fixed codes, never free text. The status comment never contains the task instructions, the Cursor prompt or response, file contents, environment values, API responses, exception text, local paths or tokens. Dry-run never writes to GitHub, so a dry-run has no status comment.

Writing status is a bounded GitHub **write** use of the issue conversation. It requires only issue-comment write on this one repository; it grants no merge, deployment, workflow or administration permission. `gh` is authenticated outside the bridge, or `OPIP_BRIDGE_STATUS_TOKEN` may supply a narrower token; the token never appears on the command line, in a file, in a log, or in the Cursor environment. If a status write fails the task is not reported as successful: the bridge writes a local `STATUS_WRITE_FAILED` record, leaves the task unconsumed or in `FAILED_OWNER_RECOVERY`, and requires OWNER recovery. Bridge status comments are never themselves treated as tasks.

Status comment ownership is verified, not assumed. The bridge resolves its own authenticated numeric GitHub user id once per run and will only reuse or update a marker comment authored by that identity. A marker posted by any other account is ignored: it is never overwritten and never treated as authoritative bridge status, so a foreign comment can neither hijack the bridge's status nor force a spurious write failure. If the identity cannot be resolved the bridge fails closed and performs no status write at all.

## 7. OWNER Cursor activation check

Before setting `enable_execution: true`, independently verify the selected Cursor version under the dedicated account. For the packaged Windows form, **do not execute the `.cmd` or `.ps1` wrapper from the bridge**. Pin and test the exact version directory selected by the vendor launcher.

Example discovery for the currently installed package:

```powershell
$runtime = "$env:LOCALAPPDATA\cursor-agent\versions\2026.09.28-64d2043"
& "$runtime\node.exe" "$runtime\index.js" --version
(Get-FileHash -Algorithm SHA256 -LiteralPath "$runtime\node.exe").Hash.ToLowerInvariant()

Push-Location $bridgeApp
python -B -c "import sys; from pathlib import Path; from tools.local_agent_bridge import cursor_runtime_digest; print(cursor_runtime_digest(Path(sys.argv[1])))" $runtime
Pop-Location
```

Put the absolute `node.exe` path and lowercase executable hash in `cursor_executable` / `cursor_sha256`, and the same version directory plus the bridge-computed tree hash in `cursor_runtime_root` / `cursor_runtime_sha256`. The tree hash covers every regular file recursively—including `index.js`, numbered JS chunks, native modules, `cursorsandbox.exe`, `crepectl.exe`, and `node_modules`—and excludes only the vendor's transient `.running` marker. Links/reparse points and any other runtime-byte drift fail closed. An auto-update creates or selects a different version directory/hash and therefore requires fresh OWNER verification before changing the pins.

Then verify the exact pinned runtime with the same isolation the bridge will use:

- `--print --mode ask --sandbox enabled --output-format json` accepts a stdin prompt and returns the documented success envelope when launched as `node.exe index.js ...`.
- A private `CURSOR_CONFIG_DIR` and empty HOME/USERPROFILE load only the specified deny policy, with no personal/global MCP server, browser tool, plugin, startup hook or profile inherited.
- Adversarial prompts to read a sentinel outside scratch, write a sentinel, execute a harmless shell marker, invoke an MCP or fetch a URL are all denied. Check actual filesystem/process/network evidence, not the model's claim that it complied. Do this in a disposable credential-free environment.
- Sandbox mode really works on this Windows build; unsupported sandbox/options or missing helpers must fail. No fallback to `--force`, `--yolo`, `--approve-mcps`, `--trust`, an unrestricted SDK, or wrapper execution is permitted.
- Timeout/Ctrl+C clean up the process tree in this build. The implementation attempts descendant cleanup on Windows, but crash/power-loss cases still need operator inspection.

Record the tested version, hashes and evidence outside the task worktree. If any check cannot pass, leave execution disabled. Unit tests are not substitute evidence.

Provide only the Cursor model-authentication key to the bridge process through the account's approved secret mechanism. Do not put a key in the JSON configuration, task comments, PowerShell history, source files or command-line arguments. V1 intentionally does not borrow browser login credentials from the user's profile.

## 8. Execute and review

Once the above checks pass, set `enable_execution: true` in the local config and run:

```powershell
python -B "$bridgeApp\tools\local_agent_bridge.py" `
  --config C:\OpipBridge\config.json `
  --issue 123 `
  --task-comment 1234567890 `
  --execute
```

Possible normal progression: `STARTED` → `APPLYING` → `APPLIED_UNTESTED`. A failure exits nonzero and requires inspection. The bridge passes task content through stdin, never through a shell or executable arguments. Cursor output is untrusted; an exact JSON edit schema, before-hashes, scope checks and a second live approval/repository check precede application. A malformed result, reported conflict or no-op/empty proposal stops. It never retries a claimed task automatically, even if failure occurred before Cursor started.

In basic mode, review `git diff` and newly added files before running code. Run the contract's targeted tests in your normal isolated development/test environment with no production credentials. For Python changes, run the repository-required compile check and applicable CI checks. Basic-mode commit/push still require explicit OWNER authorization under `AGENTS.md`. In autonomous mode, the approved policy grants bounded commit/push and engineering-review authority as described in section 12; merge/deploy remain separate OWNER decisions.

One invocation handles one selected comment. Repeating the command is enough to poll it. Continuous operation uses `--watch` (section 6.1), which replaces the manual per-task `--task-comment` with automatic discovery on the same control issue. If you later use Windows Task Scheduler, use the same account/config/state directory, disallow overlapping instances, and prefer dry-run until the activation checks pass. Sleep/offline/logged-out machines do not process tasks; expired approvals remain expired when the machine wakes. There is no background service installation or new scheduler in this increment.

## 9. Status, receipts and recovery

`state_dir/status.jsonl` contains schema version, UTC timestamp, repository, task comment ID and fixed status/reason code. Raw comments, model output, file contents, subprocess stderr and tokens are not logged. When execution is enabled the same transitions also maintain the bounded GitHub status comment described in section 6.2. Some early configuration/filesystem failures can only emit a generic JSON error to stdout. Capture stdout with appropriate local access controls if needed.

`state_dir/discovery.json` records discovery progress for `--watch`: the highest comment id observed (observability only, never used to skip an earlier task) and a bounded map of comment ids that already reached a permanent disposition. It is written atomically. A task that failed a temporary GitHub read is deliberately absent from it so the next poll retries.

`state_dir/watch.lock` is held for the duration of a `--watch` loop so two watchers cannot run against one state directory. `watch.lock` and `run.lock` are removed on clean exit, including Ctrl+C.

`state_dir/receipts.sqlite3` records the first claimed task ID/hash and disposition. A committed `STARTED` claim precedes the Cursor call. All subsequent observations of the same comment remain consumed; editing/reapproving it never creates another attempt. This provides **at-most-one attempt**, not guaranteed completion or exactly-once application. The single exception is a `PUBLISHED_WAITING_CI` receipt, which stays eligible for a CI-only resume as described in section 6.1; it never permits a second coding attempt. State deletion, restoring an old database, separate state directories, and running multiple hosts break that guarantee and are unsupported.

`state_dir/run.lock` serializes runs. A hard crash may leave it behind intentionally. Never automatically expire or delete it just because its timestamp/PID looks old: PIDs can be reused, and a child may still be running.

OWNER recovery procedure:

1. Stop scheduling this task and confirm every bridge/Cursor process for it has stopped. Disconnect the automation account if containment is uncertain.
2. Preserve the worktree, status log, database and any scratch/edit files. Do not reset, clean, stash, delete receipts or auto-retry.
3. Inspect current branch/HEAD, every diff and untracked path, and compare with the approved file set. `APPLYING` plus a crash may mean zero, some or all files were written. Writes are atomic per file where the filesystem supports replace, not transactional across files. Windows power-loss durability is not certified.
4. If the database is corrupt/unreadable or restored from backup, treat completion as unknown and reconcile all affected tasks before resuming. Never silently recreate receipts.
5. After verifying no process remains, the OWNER may remove only the confirmed stale `run.lock`. Preserve receipts. A new attempt requires a newly reviewed task comment and fresh approval, normally from a new clean worktree/base reflecting the OWNER's chosen recovery. Do not reuse task IDs or erase evidence.
6. Inspect and remove any abandoned scratch files only after review. Logs/state retention and backups are OWNER responsibilities; v1 performs no evidence cleanup.

## 10. Edge cases and coverage

This is a bounded threat/edge matrix, not a claim that every possible Windows, network or vendor failure has been tested.

| Scenario | Required behavior | Evidence |
| --- | --- | --- |
| New `/opip-task` comment on the one control issue | Discovered automatically in `--watch`, executed once, in id order | Discovery tests |
| Unrelated comment, bridge status comment, non-OWNER author | Ignored; never executed | Discovery tests |
| Transient GitHub read failure during discovery | No task consumed; retried next poll; no discovery record written | Discovery tests |
| Edited or revoked task before execution | Refused before any Cursor call or source write | Discovery + control-plane tests |
| Restart after a handled or claimed task | No replay; durable receipts and dispositions hold | Receipt/discovery tests |
| Two watchers or two runs against one state directory | `watch.lock`/`run.lock` refuse the second process | Discovery/lock tests |
| Polling interval outside 10–300 seconds, or `--watch` with `--task-comment` | Refused before any GitHub write | Discovery/CLI tests |
| GitHub status write fails | Task not reported successful; `STATUS_WRITE_FAILED` recorded; OWNER recovery | Status tests |
| Status comment content | Fixed marker plus bounded JSON only; no instructions, model output, paths or tokens | Status tests |
| `test`/`atdd scope`/`semgrep/ci` missing, pending or conflicting | Never `APPROVE` | Required-check tests |
| Advisory job failure (Ruff/Bandit/pip-audit/Gitleaks) or CircleCI error | Does not block approval | Required-check tests |
| Published draft PR awaiting CI | Stays resumable; later polls re-evaluate CI only, never re-code | Resume tests |
| CI pending on a later poll | No approval; task stays resumable | Resume tests |
| CI terminal on a later poll | Submits exact-SHA review once, then terminal | Resume tests |
| Permanently malformed `/opip-task` body | `INVALID_TASK_ENVELOPE` once; never retried | Envelope tests |
| Malformed OWNER decision not bound to a task | Skipped; never poisons unrelated tasks | Envelope/approval tests |
| Malformed GitHub transport *response* | Still transient and retried | Envelope/discovery tests |
| Status marker authored by another account | Ignored; never overwritten or treated as bridge status | Status-identity tests |
| Status-writer identity lookup fails | Fails closed; no POST/PATCH performed | Status-identity tests |
| Missing/wrong author or OWNER approval | No dispatch | Control-plane tests |
| Approval copied to another task or modified task body | Exact body hash mismatch stops | Control-plane tests |
| Edited task/approval, expired/future/overlong approval | Stop; fresh immutable comment required | Control-plane tests |
| New revoke, out-of-order listing, new reapproval | Latest OWNER decision wins; non-OWNER ignored | Control-plane tests |
| Missing/duplicate comment, fenced JSON, unknown keys, duplicate JSON keys, NaN, oversize | Reject | Schema/control-plane tests |
| Closed/locked issue, fork PR, wrong PR branch/SHA/base | Stop; no fork checkout | API/PR tests |
| API 401/403/404, rate limit, network outage, malformed response | Nonzero stop; no dispatch/offline fallback | API adapter tests and failure handling |
| More than 2,000 comments | Stop at bounded pagination limit | API adapter tests |
| Main/master/detached/primary checkout, wrong origin or HEAD | Stop | Repository/control-plane tests |
| Dirty/untracked/ignored source, concurrent change | Preserve and stop before dispatch/application | Repository/receipt tests |
| Missing/mismatched active contract, map or authority hashes | Stop; no fallback to historical scope | Repository/ATDD tests |
| Architecture conflict in approval or model output | Stop | Control/proposal tests |
| Traversal, drive/UNC path, ADS, case alias, reserved device, hidden path | Reject | Windows path tests |
| Symlink/junction/reparse/hardlink | Reject observed path; concurrent malicious filesystem races unsupported | File-boundary tests; exclusive-account prerequisite |
| Missing Cursor, wrapper, hash drift, auth absent, nonzero exit | Stop; never fall back to weaker launch | Cursor adapter tests |
| Cursor timeout/excess output/invalid envelope | Stop and require recovery; no application | Cursor adapter tests |
| Prompt injection requests tools or broad edits | Tool denial is vendor trust boundary; deterministic edit allowlist remains enforced | Mocked adapter/proposal tests plus mandatory live smoke test |
| Delete/rename/binary/secret-pattern/out-of-scope/duplicate edit | Reject entire proposal before first write | Proposal tests |
| File hash changed before apply | Stop, preserve external edits | Proposal tests |
| Disk full/write failure after first file | Keep partial evidence, consumed receipt, no rollback/retry | Injected partial-write test |
| Duplicate delivery, two runs, interrupt, corrupt receipts | At most one attempt; explicit recovery | Lock/receipt tests |
| Power loss, stale lock, account logout, orphan descendants | Unknown state; OWNER recovery | Documented manual procedure; not power-loss certified |
| GitHub approval revoked after final check | Cannot undo in-flight writes; inspect/recover | Explicit race boundary |
| Multiple hosts, different state directories, state deletion or rollback | Unsupported; do not claim global exactly-once | Explicit operating constraint |
| Cursor update or undocumented tool bypass | Execution stays disabled/pin stops until verified | OWNER activation gate |
| Credential embedded in otherwise allowed source | Known key patterns rejected; OWNER must approve non-secret context | Secret-pattern test; no claim of universal detection |
| Generated tests/tools execute malicious code | Basic mode never executes them; autonomous mode uses an isolated local container only | Container arguments and copied-source tests; OWNER-provisioned image |

## 11. Development verification

From the application directory, using your existing test environment:

```powershell
python -B -m pytest -q tests/test_local_agent_bridge.py tests/test_local_bridge_autonomy.py tests/test_atdd_scope_control.py tests/test_opip_r0r1_audit_reconciliation.py
python -m compileall -q app
python -m ruff check tools/local_agent_bridge.py tools/local_bridge_autonomy.py tests/test_local_agent_bridge.py tests/test_local_bridge_autonomy.py
```

The existing pytest CI collects these tests automatically. No workflow, requirements or runtime file changes are needed. `--noconftest` can isolate these stdlib-oriented tests from the existing application autouse fixtures for focused diagnostics; it is not a substitute for the normal CI run. Keep pytest temporary directories outside the target checkout; short absolute paths help on Windows.

Before commit, provide the exact changed paths (including untracked additions) to `tests/atdd_scope.py`; a Git diff of HEAD alone cannot account for uncommitted new files. After an authorized commit, use the existing `--git-base` gate against current main. If main/HEAD changes, rerun affected checks; do not claim merge/deployment readiness from historical evidence.

## 12. Fully autonomous development inside an approved work registry

This mode implements all three requested approval levels: automated task admission, local engineering review/verification, and GitHub `APPROVE` / `REQUEST_CHANGES`. The OWNER preapproves the increment and a finite registry of exact tasks once. The bridge can subsequently execute matching tasks without asking for per-task approval. It cannot invent or approve a broader increment. Scope is still the v1 engineering file surface in section 1; unrestricted product/runtime development is not enabled by this policy.

The bounded cycle is:

```text
OWNER approves exact policy hash on a control issue
  -> authorized author posts a registered task
  -> bridge automatically approves task admission
  -> Cursor proposes bounded edits
  -> bridge validates and applies them
  -> isolated local test container runs registered tests
  -> fresh Cursor session independently reviews before/after + contract
  -> ordinary feature commit and non-force push
  -> create/reuse draft PR
  -> wait for exact-SHA required GitHub checks
  -> separate GitHub reviewer submits APPROVE or REQUEST_CHANGES
  -> OWNER alone decides merge and deployment
```

“Independent” means a separate fresh agent invocation with a review prompt; it is not a guarantee of independent model errors or a substitute for required human reviews. Each task has one coding attempt. A failed test or review stops with evidence; a correction is another registered task/comment with a fresh exact HEAD. No unbounded self-remediation, automatic scope expansion, or unlimited spending loop exists.

### 12.1 Additional local prerequisites

Provision Docker Desktop with the local Linux engine, and an audited test image containing the repository's existing test dependencies. The bridge connects only to `npipe:////./pipe/dockerDesktopLinuxEngine`; it does not use a remote Docker context, install Docker, build images or pull missing images. Pin the installed image by its `@sha256:` digest. The reviewed image must not contain credentials or require production/network access.

Tests run as UID/GID 65534 with a read-only root filesystem, no network, all capabilities dropped, no new privileges, 1 GiB memory, two CPUs and 128 PIDs. A copied source snapshot is mounted read-only. Git administrative files and actual `.env` files are excluded; tracked source and example configuration remain available. There is no host worktree/state/token/home/Docker-socket mount. Temporary test files go under the container's `/tmp`. The fixed test command comes from the approved policy, never the task/model. Candidate code cannot select container options.

This adds one optional external dependency (Docker Desktop) for autonomous test isolation; basic mode still uses only Python stdlib + existing Git/gh/Cursor. Docker/Windows isolation and the reviewed image are trust boundaries, not proof against a compromised host/kernel/daemon. A selected test needing PostgreSQL, writable source or network will fail; use a separately reviewed self-contained test image/command, not a relaxation of isolation.

Use a dedicated GitHub development identity for feature push and PR creation, with repository-limited Contents and Pull requests write access and no administration/deployment/workflow authority. Provide a **different reviewer identity** through `OPIP_BRIDGE_REVIEW_TOKEN` using your approved secret mechanism. That identity needs Pull requests write and read access to the repository/checks. The bridge never exposes this token to Cursor, test containers, prompts, argv or logs. No token is created or stored by this implementation. GitHub forbids approving your own PR; same-author review fails with `SELF_APPROVAL_FORBIDDEN`. Repository rules can still require additional reviewers; this bridge does not bypass them.

Configure Git's name/email for the development identity in the dedicated clone. Set `core.autocrlf=false` **before preparing its task baseline** so staged object hashes match the reviewed file bytes:

```powershell
git --git-dir=C:\OpipBridge\repo.git config core.autocrlf false
```

If an existing worktree was already prepared with other line endings, prepare a fresh reviewed worktree instead of normalizing a dirty one. Git filters/attributes that transform candidate bytes fail the staged-content check. Commit hooks are disabled for this bounded publication; the registered isolated tests and required CI provide validation. A policy needing signing/hooks requires a separate reviewed adapter, not a silent bypass.

### 12.2 Register work and approve the policy once

Copy `local-bridge-autonomy.example.json` to `C:\OpipBridge\autonomy.json` outside the checkout. Fill in:

- Exact increment, feature branch, approved starting `base_head`, contract and architecture digests.
- `registered_tasks`: exact instructions and ordered file lists permitted without per-task human approval. Instructions must match exactly; a “similar” request is rejected.
- `max_tasks`: 1–10 attempts under this policy, including failed attempts; default example is three.
- `test_image`: audited preinstalled image digest; `test_command`: one argv array for the registered test entry point; timeout of 1–900 seconds.
- `required_checks`: exact current CI context names. `test`, `atdd scope` and `semgrep/ci` are structurally mandatory in the bridge code and cannot be removed by a policy; a policy may only add names. A missing protected context is treated as pending, never as satisfied, and a same-named legacy commit status that conflicts with a successful check-run fails closed. Success cannot be inferred from an empty/missing check set. Neutral/skipped checks do not count as success. Advisory jobs (`ruff (advisory)`, `bandit (advisory)`, `pip-audit (advisory)`, `gitleaks (advisory)`) and unrelated failures such as `CircleCI Pipeline` are deliberately not required.

Run the local hash command and review the policy's complete bytes before approval:

```powershell
(Get-FileHash -Algorithm SHA256 -LiteralPath C:\OpipBridge\autonomy.json).Hash.ToLowerInvariant()
```

From the OWNER's account, post to an **open control issue** (autonomous mode uses a stable issue while the associated PR HEAD advances):

```text
/opip-authorize-increment
{
  "policy_sha256": "REPLACE_WITH_POLICY_FILE_SHA256",
  "expires_at": "2026-09-29T18:00:00Z",
  "architecture_clear": true
}
```

Expiry must be in the future and no more than seven days after creation. The same immutable-comment/latest-OWNER-decision rules apply. To revoke the policy:

```text
/opip-revoke-increment
{"policy_sha256":"REPLACE_WITH_POLICY_FILE_SHA256"}
```

Approval grants only the operations and exact registered work in that file. Changing the policy's whitespace or content changes its hash and requires fresh approval. Task admission checks the current branch is descended from `base_head` and uses the task's exact current HEAD, contract, authority and file map. An unapproved author cannot exploit a valid registry entry. Revocation/expiry is rechecked before writes, publication and review submission, subject to the documented final-check race boundary.

### 12.3 Run autonomous dry-run, then execution

Post the `/opip-task` comment as in section 5, with instructions/files matching a registry entry. A separate `/opip-approve` is unnecessary for this mode because the OWNER already approved the registered policy.

```powershell
python -B "$bridgeApp\tools\local_agent_bridge.py" `
  --config C:\OpipBridge\config.json `
  --autonomy-policy C:\OpipBridge\autonomy.json `
  --issue 123 --task-comment 1234567890 --dry-run

# After local activation prerequisites are verified and enable_execution is true:
python -B "$bridgeApp\tools\local_agent_bridge.py" `
  --config C:\OpipBridge\config.json `
  --autonomy-policy C:\OpipBridge\autonomy.json `
  --issue 123 --task-comment 1234567890 --execute
```

Autonomous dry-run returns `AUTONOMOUS_DRY_RUN_VALID` without creating execution receipts, calling Cursor/Docker, committing, pushing or posting reviews. It validates admission/scope, not live credentials or image availability.

Publication uses an explicit `HEAD:refs/heads/feature/...` destination with **no force option**, verifies staged paths and staged content hashes, checks the resulting parent/branch, and validates both fetch and push URLs. A non-fast-forward remote, concurrent file change or staged extra file stops. A failed/ambiguous push or PR creation never automatically retries.

After publication, `WAITING_CI` is a safe resumable phase. Invoke the same command later (manually or using your existing Windows scheduler), or let `--watch` revisit it automatically: a `PUBLISHED_WAITING_CI` receipt stays rediscoverable and is re-evaluated only for its exact-SHA CI/review state, never re-coded, re-tested, re-committed or re-pushed. It rechecks policy, clean worktree/HEAD, PR identity and required checks without recoding, retesting, recommitting or repushing. A newer in-progress check run supersedes an earlier green run. Failed required checks yield `REQUEST_CHANGES`; pending/missing checks yield no approval and the task stays resumable. Passing required checks allow `APPROVE` only for the already reviewed exact SHA. After the review is submitted the task is terminal and is no longer rediscovered. Both remote reviews include `commit_id`. The PR must remain a draft with auto-merge disabled through review submission; a ready-for-review or auto-merge-enabled PR fails closed. The OWNER can mark it ready after reviewing the bridge result.

### 12.4 Autonomous failure and recovery cases

The same `receipts.sqlite3` holds the unique task claim and extended `runs` phase/evidence table, including validated structured review findings and test-output digest. Status logs still contain only fixed codes. The policy budget is counted under the same exclusive lock. There is no second database or independent source of task authority. Repeated invocation after a terminal approval does not overwrite that approval or submit another review.

| Scenario | Disposition |
| --- | --- |
| No OWNER registry approval, wrong hash, policy expiry/revocation | Reject task admission or stop at next authority check |
| Instructions/files differ by any amount | `TASK_OUTSIDE_REGISTERED_POLICY`; do not ask the model to reinterpret scope |
| Attempt budget exhausted | `POLICY_BUDGET_EXHAUSTED`; new OWNER decision required |
| Docker unavailable, nonlocal/wrong engine, missing image | Stop; no install, pull or host-test fallback |
| Test failure, timeout or excessive output | No publication; keep applied candidate for recovery |
| Independent review requests changes or is malformed | No publication; no self-approval |
| Concurrent source/staged changes, transformed staged bytes | Stop before push; preserve evidence |
| Push, PR creation or review response uncertain | `FAILED_OWNER_RECOVERY`; reconcile remote state before another task |
| CI pending, missing or neutral | `WAITING_CI`; same command resumes this phase only |
| CI failure/cancelled/timed-out | Submit exact-SHA `REQUEST_CHANGES`, then stop |
| PR author equals reviewer | Refuse self-approval; provision a distinct reviewer |
| PR HEAD changed, fork target, closed PR | Stop; no review for the wrong commit |
| Approval/review succeeded but local receipt write failed | Treat as ambiguous; inspect exact remote review before recovery; never blindly resubmit |
| Need to remediate a failed task | New registered task/comment with clean reviewed base and remaining budget; no hidden scope growth |

Run `tests/test_local_bridge_autonomy.py` with the bridge tests for policy admission, test-container controls, bounded publication, check freshness, separate review identity, pipeline failure and CI-resume coverage. Mocked pipeline tests do not certify a live Docker engine, Cursor build, Git credential setup or GitHub review account; all remain OWNER activation checks.

## 13. First live smoke test (do not run before activation)

This is the smallest end-to-end proof that the loop works. It must not run until section 7 activation checks pass and the OWNER has explicitly enabled execution. It never merges, deploys or touches production.

Aim it at one harmless development-only file inside the already-approved engineering namespace, for example `OHM-Trade-Agent-v1/docs/engineering/bridge-smoke-test.md`, listed in the active contract's implementation map.

| Step | Action | Expected evidence |
| --- | --- | --- |
| 1 | Start `--watch --dry-run` on the control issue | Poll loop starts; no Cursor call, no commit, no push |
| 2 | Post one harmless OWNER `/opip-task` (and `/opip-approve`, or a registered policy) | Comment accepted as immutable and approved |
| 3 | Wait one poll interval | The task is discovered automatically with no `--task-comment` |
| 4 | Observe the control issue | A `<!-- opip-local-agent-status:v1 task=... -->` comment appears with `state: accepted` then `running` |
| 5 | Confirm dry-run side effects | Local `status.jsonl` only: no Cursor process, no source change, no GitHub status comment, no commit, no push |
| 6 | Enable execution explicitly (`enable_execution: true` plus `--execute`) | OWNER-controlled change in local config only |
| 7 | Re-run `--watch --execute` for that one task | Exactly one Cursor invocation against the isolated worktree |
| 8 | Verify isolation | Cursor ran in scratch with deny rules; no GitHub token in its environment; proposal validated before any write |
| 9 | Verify publication (autonomous mode) | One ordinary feature commit, one non-force push of the feature branch only |
| 10 | Verify the draft PR | Draft PR created or reused; still draft; auto-merge disabled |
| 11 | Verify the check gate | `test`, `atdd scope` and `semgrep/ci` all reported for the exact SHA; missing or pending yields `waiting_ci`, not approval |
| 12 | Verify no authority escalation | No merge, no deployment, no branch-protection change, no production access, no trading change |

If any step fails, stop and follow the section 9 recovery procedure. Do not re-run a claimed task; post a new task with a fresh exact HEAD instead.
