# O'Pip autonomous build report

Status: `IN PROGRESS - TWO INCREMENTS DELIVERED, TWO OWNER DECISION PACKAGES PREPARED`
Authority granted by this document: `NONE`

This report records what the autonomous build mission actually delivered, with the
evidence that proves it, and what it did **not** deliver. It is a tracking
artifact: it changes no runtime, grants no trading authority, approves no
increment and ratifies no numeric policy. Nothing here may be read as completion
of an increment that has no evidence.

## Delivered

| Increment | Status | Evidence |
| --- | --- | --- |
| ATDD-R5-F-prospective-outcome-integration | DELIVERED | draft stacked PR https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/370, commit `fd6fa7e2911ce7cbc4b15ea22c1f5b7ef76efee4`, 26 acceptance tests |
| ATDD-R6-committee-shadow-review-readiness | DELIVERED | draft stacked PR https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/371, commit `d4f4233341cec24185808473f5f533133e75cf41`, `app/opip/committee/shadow_review.py` and 18 acceptance tests |

## Outstanding

| Increment | Status | Evidence |
| --- | --- | --- |
| F10 dashboard parity | OUTSTANDING | none: no increment has been executed for the eight-page cockpit semantic model; a gap analysis is prepared below |
| R7 governed retirement | OUTSTANDING | none: no legacy writer has been stopped or retired; a decision package is prepared below |
| F10 and Telegram gap analysis | OWNER_DECISION_PACKAGE_PREPARED | prepared in this report only: no route, template, config key, alert family or numeric threshold added |
| R7 retirement decision package | OWNER_DECISION_PACKAGE_PREPARED | prepared in this report only: nothing stopped, migrated, cut over or deleted |
| Verification of the 14 host-runtime failures | ENVIRONMENTAL_CAUSE_VERIFIED / INDEPENDENT_ENVIRONMENT_UNAVAILABLE | reproduction and run table below; Linux CI confirmation is outstanding |
| ATDD-R5-F-prospective-outcome-integration | AWAITING_REVIEW | delivered as draft PR https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/370, not merged |
| ATDD-R6-committee-shadow-review-readiness | AWAITING_REVIEW | delivered as draft PR https://github.com/Opr1130/OHM-Trade-Agent-v1/pull/371, not merged |

## What the delivered increments do and do not change

- No trading authority, risk gate, execution safeguard, sizing rule, minimum
  reward-to-risk, position verification or alert-only default is changed.
- The release posture is unchanged: `EVIDENCE_SHADOW` active, `TARGET_PAPER`
  blocked, `OPIP_PAPER_V2_MODE` off, funded trading disabled.
- Both increments add measurement-only, non-authoritative evidence surfaces with
  no runtime consumer. Neither enables the Committee timer, an influence review,
  a Paper-v2 path or a live exchange path.
- No durable store is created, migrated or rewritten, and no production data is
  touched.
- Owner policy inputs (`OQ-03`..`OQ-10`) remain open and no numeric threshold is
  invented by either increment.

## Honest gaps this report is required to state

- The Committee shadow review readiness verdict for today's deployed evidence is
  **not** a pass: real prospective cases have not matured in production, the
  timer policy has no recorded owner decision, weakness findings are held in
  process rather than in a durable stream, and the attributable cost of provider
  attempts is not measured. The increment reports those gaps rather than
  smoothing them away.
- `F10` and `R7` are unstarted. The roadmap keeps them as separate retained
  obligations, and the architecture forbids starting a later phase, enabling
  Paper v2, enabling the Committee timer or redesigning the dashboard without an
  explicit owner decision.
- Windows-only full-suite failures observed while validating these increments
  reproduce at their parent commits and are not attributed to them. The 14
  `test_opip_committee_host_runtime_v1` failures were additionally reproduced
  identically at the pre-increment baseline (`main` `27f6a2f8`): the same 14 test ids
  and the same `14 failed, 4 passed`. See the verification section below. Ruff is not
  installed in the local Windows toolchain, so Linux CI remains the authoritative lint
  and full-suite gate. No test was skipped, weakened or suppressed in this session.

## Verification of the 14 host-runtime failures (independent-environment attempt)

Mission task: verify the 14 `tests/test_opip_committee_host_runtime_v1.py` failures in an
independent environment before attributing them to the host rather than to an increment.
Outcome: **the environmental cause is verified by direct reproduction and by an identical
baseline measurement, but no independent environment was reachable from this machine, so
independent confirmation remains outstanding.**

### No independent environment is reachable locally

| # | Candidate | Result |
| --- | --- | --- |
| 1 | WSL distribution | none: `HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss` has no distro entry, and neither the LxssManager nor the WSLService service is present |
| 2 | Docker CLI/engine | `docker` is not on PATH |
| 3 | Alternative MSYS/Cygwin bash | only Git-for-Windows MSYS exists: `C:\Program Files\Git\bin\bash.exe` and `...\usr\bin\bash.exe`; `C:\msys64\usr\bin\bash.exe`, `C:\cygwin64\bin\bash.exe` and `C:\Windows\System32\bash.exe` are absent |
| 4 | Linux CI, the authoritative gate | not triggered for these commits: `.github/workflows/pytest.yml` runs only on `pull_request` and `push` to `main`, while PR #370 is based on `feature/opip-r5-e-f11-independent-protection` and PR #371 on `feature/opip-r5-f-prospective-outcome-integration`. Their visible checks are Semgrep, CodeRabbit/Gitar and skipped `claude`/dispatch jobs; every `deploy-*` workflow is `skipped` |

### The host defect, reproduced with no repository code involved

Invoked directly from Python, with no repository file on the command line:

```
"C:\Program Files\Git\bin\bash.exe" -c "echo probe-ok; /bin/true; echo after-fork-ok"
```

Both `bin\bash.exe` and `usr\bin\bash.exe` print `probe-ok` and then fail with exit code
**254**, `dofork: child -1 - forked process ... died unexpectedly, retry 0, exit code
0xC0000142, errno 11` and `/usr/bin/bash: fork: Resource temporarily unavailable`. `/bin/true`
has nothing to do with this repository, so the defect is host-level: MSYS bash starts, but it
cannot fork a child process.

### Measured runs of the identical test file

`git diff main...HEAD -- tests/test_opip_committee_host_runtime_v1.py` is empty, so the file
is byte-identical on both sides and any difference in outcome is host state, not code.

| Run | Worktree and commit | Result |
| --- | --- | --- |
| 1 | R6 head, `wt-opip-r5e3f11` @ `7605b334` | `14 failed, 4 passed in 222.96s` (18 collected) |
| 2 | R6 head, repeated | `14 failed, 4 passed in 223.98s`, the same 14 test ids |
| 3 | baseline `main`, `wt-opip-r4f8` @ `27f6a2f8`, measured while another bash-heavy run was still in flight | `3 passed, 15 skipped in 8.75s` |
| 4 | baseline `main` @ `27f6a2f8`, measured alone | `14 failed, 4 passed in 224.90s`, the same 14 test ids |
| 5 | R6 head, one parametrised node with `--tb=short` | `7 failed in 112.83s` |

Runs 1, 2 and 4 agree exactly, so the increment head and the pre-increment baseline are
indistinguishable on this host. The 14 failing ids are
`test_exact_sha_is_required_and_refs_are_refused` for all seven parameters (`main`, `HEAD`,
`feature/module2h`, `AAAA…`, `aaa…`, `aaa…`, `not-a-sha`), `test_missing_sha_is_refused`,
`test_provision_installs_the_release_and_proves_import`,
`test_repeat_install_of_the_same_sha_does_not_rebuild_or_duplicate_config`,
`test_dependency_install_failure_keeps_the_previous_runtime`,
`test_symlink_in_the_release_tree_is_refused`,
`test_rollback_restores_the_previous_release` and
`test_runtime_proof_fails_closed_when_egress_or_the_timer_is_opened`. The four that pass are
`test_cycle_runner_module_imports`,
`test_the_import_proof_snippet_resolves_the_release_root`,
`test_library_cannot_be_executed_directly` and
`test_provision_scripts_do_not_open_egress_or_touch_trading_credentials`.

### Why exactly those 14 fail

These are not assertion mismatches about script behaviour; the shell's own fork error
surfaces through the assertion. Verbatim from run 5:

```
tests\test_opip_committee_host_runtime_v1.py:261: in test_exact_sha_is_required_and_refs_are_refused
    assert proc.returncode == 64
E   AssertionError: assert 254 == 64
E    +  where 254 = CompletedProcess(args=['C:\\Program Files\\Git\\bin\\bash.exe', ...
     provision-committee-host-runtime.sh: fork: Resource temporarily unavailable\n').returncode
```

The boundary is fork. A test whose script needs only in-process execution passes:
`test_library_cannot_be_executed_directly` asserts return code 64, and the library's own
direct-execution refusal exits 64 without forking. Every test whose script uses a command
substitution, a pipeline or a subshell needs a forked child, receives 254, and fails. The
committed `deploy/committee/*.sh` logic was never reached, so these failures carry no verdict
about the provisioner.

### A second manifestation of the same defect, and the file's own guard

The test file anticipates one host condition: `_BASH_LAUNCH_FAILURE = 3221225794`
(`0xC0000142`) with **empty** stdout and stderr is converted to a skip. This host produces two
different non-verdicts:

1. launch succeeds, fork fails — non-empty diagnostics and return code 254, so the guard does
   not apply and the test fails (runs 1, 2, 4, 5);
2. launch itself fails — empty output and `0xC0000142`, so the guard applies and the test
   skips (run 3, under concurrent load).

That is why the same commit can report `14 failed` or `15 skipped`: the count is a function of
when the host resource limit is hit, not of the commit. Neither outcome is a verification of
the scripts, which is exactly why the guard exists. This session did not modify the test file,
add a marker, add a skip, or otherwise suppress any failure.

### The check surface on these commits (verified)

Every automated check that actually ran on these commits was queried through the GitHub API, so
the "no Linux CI" statement above is a measured fact and not an assumption:

- `Semgrep` **succeeded** for `2cbb8f78` (run `38400824865`) and for the R5-F head `fd6fa7e2`.
- Five review workflows — `deepseek-quant-review.yml`, `gemini-review.yml`,
  `pr69-external-reviews.yml`, `pr69-external-reviews-manual.yml` and
  `pr69-external-reviews-repository-dispatch.yml` — fail **identically at the R5-F head and at the
  R6 head**. They are provider/dispatch configuration failures that reproduce independently of any
  change in this stack, so they carry no verdict about the delivered increments.
- No `pytest.yml` job (full suite, `atdd-scope` gate, architecture gate, security gate) and no
  `deploy-*` job ran for any of these commits; the `deploy-*` workflows appear as `skipped` on the
  PR check list.


### What remains, and the decision required

- Environmental attribution is supported by a repo-independent reproduction plus an identical
  baseline result. Independent confirmation on Linux is still outstanding.
- `pytest.yml` (the full suite plus the `atdd-scope` gate) has never run for the R5-F or R6
  commits, because it triggers only for `main`. The R6 contract itself records that "Linux CI
  remains the final-validation prerequisite", so this is a known, recorded gap rather than a
  new one.
- Owner decision needed to close it (no option is taken here): (a) authorize a temporary PR
  from the R6 head into `main` purely to run `pytest.yml` without merging, (b) authorize adding
  `workflow_dispatch` to `pytest.yml` so a feature-branch head can be validated deliberately,
  or (c) provide a Linux host for the local run. Until one is chosen, the honest status is
  "environmental attribution supported locally; authoritative Linux confirmation outstanding".

## Prepared owner decision package — F10 eight-page dashboard and decision-first Telegram (NOT IMPLEMENTED)

Prepared, not implemented. Nothing in this section is code, a route, a template, a
configuration key or a policy: it is the contract-to-implementation gap analysis an
owner needs before authorizing an F10/Telegram increment. No page, panel, alert family
or numeric threshold is invented below; contract lines are cited, and every
current-implementation line was read from this worktree at `7605b334`.

### The contract (cited)

| Source | What it requires |
| --- | --- |
| `docs/architecture/v1.5.0/ARCHITECTURE.md` | Extend the existing eight-page F10 semantic experience: reuse canonical projections, typed filters, cross-filtering, saved views and evidence drill-down; keep aggregate/detail populations and watermarks aligned; preaggregate fills and AI attempts before joins so P&L is not multiplied; no UI or Telegram action changes execution or policy. Telegram and dashboards have zero trading authority. R4 quality/notification policy renders committed decision state; notification delivery is not order authority and does not gate protection. `UI-02`: AI review state and stance stay separate optional metadata, missing AI is never favorable analysis, and committed paper state plus existing-position protection never depend on successful Telegram delivery. Named negative case: dashboard, Telegram or AI submits action-like content, or stale configuration requests target mode. |
| `docs/architecture/OPIP_CONFORMANCE_LEDGER.md` (F10 row) | The eight pages are System Pulse; Discovery and Regime; Decision Trace; Paper Portfolio; Quality and Missed Opportunity; Committee Value and AI Cost; Learning and Releases; Incidents and Infrastructure — one shared semantic model, typed filters, cross-filters, explicit unknown, saved views, watermark, decision trace, no trading authority, with section 21 Committee trust panels. Status `PARTIAL`; `DUPLICATE_OR_OVERLAPPING_PATHS` = "four surfaces, none covering all eight pages"; target authority = one semantic read service over canonical projections; cutover gate = fixture reconciliation of counts and P&L, then retire duplicate pages; delete gate = `/api/analytics/*` consumers enumerated; blockers = canonical detector, forecast and Committee facts are not on the live path. |
| `docs/architecture/OPIP_R5_REQUIREMENTS_REGISTER.md` (F10 row) | `EXTEND`; authority `NONE`; contracts `SC-08`, `SC-12`; gate `G4`; open input `OQ-10`; owner `UNASSIGNED`; read-only facts only, with no favorable state produced from missing evidence. |
| `docs/architecture/OPIP_RETIREMENT_LEDGER.md` | Legacy `/dashboard` (route stays until cutover, no new cards during R5, consumers named as `/api/analytics/summary` and `/api/analytics/intelligence`), Grafana cockpit and B/C-4 (retire only panels that duplicate the semantic service), and obsolete alert lifecycle wording (trade cards still use `send_trade_plan`; a trace id on every alert must match a canonical decision; the Telegram delivery ledger is the consumer census). |

### Current implementation (verified in this worktree at `7605b334`)

| Surface | Verified entry point | What it actually is |
| --- | --- | --- |
| Legacy dashboard route | `app/main.py` mounts `app.api.dashboard.router` | `app/api/dashboard.py` (65 lines) serves `GET /dashboard` (HTML), `GET /api/analytics/summary`, `GET /api/analytics/intelligence`, `GET /api/opip/zero-trade-explanation`, each behind `_require_secret`. |
| Legacy read model | `app/services/dashboard_read_model.py` (548 lines) | `build_dashboard_read_model(scope)` returns `schema_version: 2`, `read_only: True`, `operations`, `intelligence` (including `evidence_state` `MEASURED`/`INSUFFICIENT_DATA` at `sample_count < 30`, conversion rates, latencies, `paper_performance`, `calibration_status`, `change_impact` `AWAITING_VERSIONED_CHANGE_COHORTS`, `trend` with `trend_source` `POSTGRESQL`/`FILE_WAL_FALLBACK`), `failure_eradication`, `opportunity_accountability`, `paper_engine`, `telegram_delivery` (delivery summary), `recent_events`, `data_platform`, and `guardrails` (`dashboard_can_change_rankings`, `_alerts`, `_admit_paper_trades`, `_write_exchange_orders` all `False`). |
| Cockpit analytical API | `app/api/cockpit_service.py` (read-only ASGI app mounting `app/api/cockpit.py`) | Analytics-plane service over the verified canonical replica; deliberately mounts only the Cockpit router and holds no Telegram or order authority. |
| Cockpit modules | `app/opip/cockpit/portfolio.py` (744 lines), `ledger.py`, `trust.py` | Target-side projections; the register cites `portfolio.py` as F10 evidence. Not mounted on the legacy dashboard route. |
| Grafana cockpit | `deploy/grafana/dashboards/opip-intelligence-cockpit-v1.json` | Separate read plane; per the ledger the last analytics install observed is `ef8b23aa`, not the core SHA under test. |
| Telegram delivery ledger | `app/services/telegram_delivery.py` (577 lines) | `EVENT_FILE /app/data/telegram_delivery_events.jsonl`, `STATE_FILE /app/data/telegram_delivery_state.json` plus lock; deterministic `_alert_id(identity, alert_family, event_type, fingerprint)`; `TelegramDeliveryResult(status, delivered, message_id, alert_id, attempt, retry_count, latency_ms)`; statuses `DELIVERED`, `EDITED`, `TRANSITION_PUSHED`, `SEND_FAILED`, `EDIT_FAILED`, `SUPPRESSED`, `NOT_ELIGIBLE`; `link_delivery_to_journey`, `accepted_delivery_message_id`, `canonical_message_id`, `build_delivery_summary(scope, path)` with `by_status` counts. |
| Notification policy | `app/services/notification_policy.py` (389 lines) | `STATE_FILE /app/data/notification_state.json` plus lock; `DEFAULT_COOLDOWN_SECONDS = 6 * 60 * 60`, `DEFAULT_RESERVATION_LEASE_SECONDS = 120`, `CRITICAL_EVENTS`, `NEW_TRADE_EVENT = "ACTIONABLE_TRADE"`, `NOTIFICATION_POLICY_VERSION = "r4-alert-quality-v1"`; `reserve_emit`/`confirm_emit`/`release_emit`/`should_emit`/`record_emitted`/`is_confirmed_emission`; `select_new_trade_window`, `notification_quality_suppression_reason`, `new_trade_volume_suppression_reason(max_per_window)`, `new_trade_delivery_slot_open`, `authorize_new_trade_notification`. |
| Alert taxonomy and renderer | `app/services/alert_taxonomy.py` (137 lines), `app/services/alert_v2_format.py` | `AlertClass` families `SIGNAL_OPPORTUNITY`, `SYSTEM`, `TRADE_PROTECTION`, `EXTERNAL_OPERATOR`, `COMMAND`; `SYSTEM_HEALTH` and `MONITOR_DEGRADED` families; `contains_legacy_user_facing_branding`. System incidents are decision-first; trade cards still use `chief_alert_notifier.send_trade_plan`. |

These are the pre-existing surfaces the ledger already counts as "four surfaces, none
covering all eight pages". This report adds nothing to any of them.

### Gap 1 — functional parity (the eight named pages)

No single semantic read service and no service-side page contract exist. The legacy read
model is one aggregate (`build_dashboard_read_model`) behind three JSON routes plus one
HTML page; the Cockpit modules and the Grafana dashboard are separate planes with their
own contracts. The following remain unproven or absent and are recorded as gaps, not as
implemented behaviour:

1. No page-level contract exists for System Pulse; Discovery and Regime; Decision Trace;
   Paper Portfolio; Quality and Missed Opportunity; Committee Value and AI Cost; Learning
   and Releases; Incidents and Infrastructure. Page-to-fact mapping is an owner-visible
   design decision, not something this report may assume.
2. Typed filters, cross-filtering, explicit-unknown representation, saved views and
   saved-view migration are not present as one shared contract; the legacy read model
   takes a single `scope` argument.
3. Watermark semantics are split: the read model derives `trend_source` (`POSTGRESQL` vs
   `FILE_WAL_FALLBACK`), while Cockpit/Grafana depend on analytics installs not proven for
   this SHA. A shared watermark is a prerequisite for the cutover gate.
4. Decision trace and evidence drill-down from a displayed number back to its canonical
   decision are not implemented as a route or a service contract.
5. The ledger names the blocking fact directly: canonical detector, forecast and Committee
   facts are not on the live path, so pages that would display them cannot be truthful
   today even if they were built.

### Gap 2 — alert quality (decision-first presentation)

1. Two presentation contracts coexist: system incidents use the decision-first renderer
   (`alert_v2_format.py`), while trade cards still use
   `chief_alert_notifier.send_trade_plan`. The retirement ledger keeps the old wording
   until one release of the new renderer exists, so a parity increment must name which
   surface renders which family.
2. The contract requires a trace id on every alert that matches a canonical decision. The
   delivery ledger's `_alert_id` is derived from `(identity, alert_family, event_type,
   fingerprint)`; whether every alert family can supply a canonical decision trace id is
   unverified and is a named prerequisite, not an assumption.
3. Suppression must be explainable. `record_telegram_suppression` and
   `record_telegram_not_eligible` already emit `SUPPRESSED` and `NOT_ELIGIBLE` states, so
   the audit half exists; the missing half is proving every suppression decision is
   attributable to exactly one policy reason.
4. `UI-02` forbids favourable inference from missing AI. No current dashboard or alert
   surface is proven to satisfy that rule; it needs a negative test before either surface
   is extended.
5. Alert families are classified by `alert_taxonomy.py`; an F10/alert increment must not
   add a family without owner approval, because a new family is new presentation
   authority.

### Gap 3 — deduplication and idempotency

1. Delivery deduplication is derived, not stored as a key: `_alert_id` hashes
   `(identity, alert_family, event_type, fingerprint)` and the last state is kept in
   `telegram_delivery_state.json`. Whether that identity is stable across a restart, a
   retry and an edited message for every family is unverified per family.
2. Two further dedupe authorities already exist and must not be multiplied:
   `notification_policy.py` emission records (`reserve_emit`/`confirm_emit`/
   `release_emit`, `DEFAULT_COOLDOWN_SECONDS`, `DEFAULT_RESERVATION_LEASE_SECONDS`) and
   `app/opip/events/storage.py`'s `dedupe_key` for `OPipEvent`. The canonical ordering
   authority remains the canonical cursor `(history_epoch, local_sequence)`; an F10/alert
   increment must not become a second dedupe authority.
3. Interactive replies are outside the ledger: `telegram_callback_listener.py` sends via
   raw `send_telegram_message` (four call sites) and `telegram_command_center.py` has one
   raw send path, while eleven other senders use `send_tracked_telegram`. Alert delivery
   is therefore auditable, but command/callback replies are not recorded in the delivery
   ledger. Whether operator replies must be auditable is an owner decision; this report
   records the gap and does not decide it.
4. Bounded reminders are required by the owner's phase text and are not a current
   delivered contract; reminder bounds must be an explicit owner input, never a number
   invented by an increment.

### Gap 4 — configuration

1. Configuration that already exists and must be reused rather than duplicated:
   `telegram_enabled`, `telegram_bot_token`, `telegram_chat_id`,
   `opip_actionable_only_alerts`, `price_movement_mode`,
   `price_movement_alert_cooldown_seconds` (eligibility gating in the scan path), plus the
   `notification_policy.py` constants above.
2. Every configurable user threshold has to remain a presentation/notification
   eligibility control over already-produced canonical target decisions. It must not
   mutate F3/F5/F6/F7 economics and must not silently create a new selector; that is a
   design constraint on the increment, not a value this report may pick.
3. The release profile is not an F10/Telegram knob. `SAFE_BASELINE`, `EVIDENCE_SHADOW` and
   the blocked `TARGET_PAPER` profile are exact allowlisted profiles; F10/Telegram work
   may not be activated by a stale `.env` or a new mode string.
4. A parallel alert-quality branch exists in another worktree
   (`feat/alert-notification-quality-gate`, commit `147c28a8`) and is not merged. An
   F10/Telegram increment must coordinate with that work instead of creating a second
   notification-policy owner.

### Gap 5 — negative cases that must fail closed

These are the negative cases the contract names, restated as tests an increment would owe:

1. Dashboard, Telegram or AI submits action-like content, or a stale configuration
   requests target mode — must be refused, with no execution or policy change.
2. Missing evidence is never favourable: a missing population, an unknown cost, a stale
   watermark or a missing AI opinion must render as explicit unknown, never as a
   favourable state.
3. A displayed ordinal score must not masquerade as a probability.
4. Committed paper state and existing-position protection must not depend on successful
   Telegram delivery. Delivery failure is an audit fact, not a protection dependency.
5. Queued/retried alerts after expiry, plus every delivery state
   (`DELIVERED`/`EDITED`/`TRANSITION_PUSHED`/`SEND_FAILED`/`EDIT_FAILED`/`SUPPRESSED`/
   `NOT_ELIGIBLE`), must be asserted end-to-end against the delivery ledger.

### Gap 6 — end-to-end evidence

1. One shared semantic service, not four surfaces: today the operators' answers come from
   the legacy HTML page, three JSON routes, the Cockpit API and Grafana.
2. Fixture reconciliation of counts and P&L against canonical fixtures is the named
   cutover gate; it does not exist yet and is the prerequisite for retiring any duplicate
   page.
3. Consumer census before deletion: the delete gate is "consumers of `/api/analytics/*`
   enumerated", so a census is required before route removal.
4. Operator acceptance and accessibility review must be recorded, per the architecture's
   acceptance-evidence line for the F10 cockpit.
5. Trace links end-to-end: an alert's trace id must resolve to its canonical decision, and
   a dashboard number must drill down to the same decision.

### What this package deliberately does not decide

It does not pick page layouts, panel sets, alert families, reminder bounds, cooldown
values, watermark windows, saved-view scope, cutover dates or profile changes. It
implements nothing: no route, template, config key, scheduler entry, Telegram path or
dashboard asset was added or modified in this work session.

## Prepared owner decision package — R7 technical cutover and legacy retirement (NOT IMPLEMENTED)

Prepared, not implemented. No legacy writer was stopped, no route or panel was removed,
no store was migrated, no release profile was changed and no cutover was performed. The
roadmap line R7 answers is short: *"One paper authority. Then stop legacy writers.
Deletion is a later owner decision after the retirement ledger gates."* The retirement
ledger adds the rule this package obeys: deletion requires a proven replacement, a
completed cutover, a consumer census, preserved history, a rollback path and a separate
owner approval.

### 1. The decision R7 asks for

In order: (a) name **one** paper authority; (b) stop the legacy writers that the named
authority replaces; (c) only later, after the ledger's delete gates, delete anything.
(a) is an owner authority change. (b) is an owner-approved stop time per writer. (c) is a
separate owner approval. This package supplies the evidence each step needs and takes
none of the steps.

### 2. Current authorities (verified in this worktree at `7605b334`)

| Authority | Verified implementation | Observed state |
| --- | --- | --- |
| Paper authority resolver | `app/jobs/scan_opportunities.py`: `_resolve_paper_authority(settings)`, `_legacy_drain_status(settings)`, `PaperAuthority`, granted values including `AUTHORITY_PAPER_V2_DRAINING = "PAPER_V2_DRAINING"`, engine constants `PAPER_ENGINE_PAPER_V2_DRAINING = "OPIP_PAPER_V2_DRAINING"`, plus a deprecated `_resolve_legacy_paper_authority` shim | The resolver can express legacy, draining and unavailable; only legacy may create new legacy obligations, while draining and unavailable exist to stop them |
| Paper-v2 mode switch | `app/core/config.py:122` `opip_paper_v2_mode` | Settings default is off; the live host value is `UNKNOWN_NEEDS_EVIDENCE` per the roadmap and conformance ledger |
| Legacy drain evaluator | `app/services/paper_v2_cutover_readiness.py`: `evaluate_legacy_drain(starting_equity=...)`, drain states `DRAIN_READY`/`DRAIN_DRAINING`/`DRAIN_UNAVAILABLE`, readiness `READINESS_READY = "READY_FOR_OWNER_ACTIVATION"` | The typed gate vocabulary already exists; it never selects Paper v2 by itself |
| Cutover gate reason codes | same module: `PAPER_V2_MODE_EVIDENCE_UNAVAILABLE`, `LEGACY_DRAIN_DRAINING`, `LEGACY_DRAIN_UNAVAILABLE`, `PROTECTION_EVIDENCE_UNAVAILABLE`, `PROTECTION_UNSAFE`, `PROTECTION_DEPENDS_ON_DISCOVERY`, `UNIVERSE_METADATA_GATE_ABSENT`, `UNIVERSE_METADATA_NOT_OBSERVED`, `LONG_AUTHORITY_MISSING`, `SHORT_AUTHORITY_MISSING`, `PENDING_MANDATE_UNKNOWN`, `SELECTOR_HANDOFF_INCOMPLETE`, `SELECTOR_HANDOFF_UNAVAILABLE`, `CANONICAL_WRITER_UNAVAILABLE`, `ROLLBACK_PATH_UNAVAILABLE`; `PROTECTION_INDEPENDENT_OF_DISCOVERY = True`; `REQUIRED_SELECTOR_CANDIDATE_BRIDGE = "PCAND_TO_OPIPC"` | These are the exact blocked reasons a cutover would have to clear; this package invents no additional gate |
| Paper v1 | JSON ledger (`state.json`, `events.jsonl`) written by the registry, monitor on the unified cycle, new entries gated by `control.json` | Legacy-active; `control.json` contents are unobserved |
| Freqtrade dry-run | Containers used by the scan when Paper v2 is not requested | Observed healthy in the 2026-09-27/28 deploy logs; the isolation contract still stands and it must not be pointed at funded keys |
| Canonical writer | `paper_outcome_outbox.py` into `opip_canonical_v1.sqlite3`, replicated to the learning plane | Stays. It is the target evidence store and is currently shadow |

### 3. Retirement-candidate inventory (each row's own gates, from `OPIP_RETIREMENT_LEDGER.md`)

| Component | Target authority | Retirement trigger | Delete trigger |
| --- | --- | --- | --- |
| `scan_opportunities` admission | Detector → lifecycle → feasibility → forecast → selector | R3 selector is the admission source | After one release with no scan admissions and a named stop time |
| Movement discovery | F1–F4 observation and lifecycle | Canonical observation covers the same pairs and windows | After consumers read the projection only |
| Top-8 technical gate (`app/scanner/candidates.py`, score ≥ 80, keep 8) | F7 selector | Owner records technical cutover | v1.2's 14-day post-cutover gate, only with owner approval |
| Profit-ranking comparator (`rank_profit_opportunities` in the scan) | F7 net-dollar selector | Selector is admission source | Same delete gate as Top-8 |
| Duplicate technical features (scanner scorers, `signal_features.py`, explosion vector) | Feature Bus | Bus is the feature source for admission | After scorer callers are listed and moved |
| Paper v1 (JSON ledger plus monitor) | Paper v2 on the canonical ledger | New enrollments stopped and the open book is flat | Monitor removed only after zero open obligations |
| Freqtrade dry-run as the O'Pip paper engine | Paper v2 | O'Pip paper entries no longer route there | Container retirement is a separate owner decision |
| Legacy JSONL writers (census in `docs/architecture/v1.2/CONSUMER_CENSUS.md`; `p1_shadow_outbox` is already retired and pinned off) | Canonical writer for operational facts, with rebuildable projections | Named stop time per file | After archive checksum and consumer sign-off |
| Phase3C and discovery outcome files | One outcome grain in F9 | One writer declared per fact | After consumers use the projection |
| Trade outcome registry (`trade_outcomes.json`, written by alerts and the active-trade monitor) | Canonical paper and decision outcomes | Field mapping accepted | After writers move |
| Legacy `/dashboard` (mounted in `app/main.py`) | v1.4.3 semantic pages | R5 pages serve the same questions | Route removed after census |
| Grafana cockpit and B/C-4 | Same semantic service, Grafana as a replaceable UI | Semantic service is the source | Only panels that duplicate the semantic service |
| Obsolete alert lifecycle wording (trade cards still use `send_trade_plan`) | v1.4.2 renderer over the canonical disposition | R3 disposition exists | Old template after one release of the new renderer |

The ledger's explicit non-deletions also stand and constrain this package: active-trade
protection stays until R4 proves an independent replacement; the canonical writer stays;
the Committee package stays because it is advisory; the Feature Bus code stays because R2
proved it in shadow and the compatibility pin remains `off`; Paper v2 code stays because it
is inactive, not obsolete.

### 4. Cutover dependencies (what must be true before any flip is even proposed)

1. The target F7 selector must be the admission source being papered. Papering Paper v2
   while the legacy selector still admits would paper the legacy selector.
2. The legacy drain must evaluate `READY`: no outstanding Paper v1 obligation and no
   reserved legacy capital, and drain `READY` includes zero Freqtrade opens.
3. Protection must be re-proven independent at the cutover SHA
   (`PROTECTION_DEPENDS_ON_DISCOVERY` is an explicit blocked reason).
4. The remaining typed gates must clear: universe metadata observed, long and short
   authority present, pending mandate known, selector handoff
   (`REQUIRED_SELECTOR_CANDIDATE_BRIDGE = "PCAND_TO_OPIPC"`) complete, canonical writer
   available, and a rollback path available (`ROLLBACK_PATH_UNAVAILABLE` is a blocked
   reason, so rollback is a gate, not a hope).
5. Matched-window pre-cutover comparison evidence of the target selector against
   cash/no-trade and the frozen profit-ranking comparator, under matched capital, timing,
   execution model and fee policy, over the full intent population including no-fills and
   rejects, must exist before the legacy admission source is replaced.
6. The release profile may not be used as a shortcut. `TARGET_PAPER` remains blocked until
   every prerequisite is proven; `EVIDENCE_SHADOW` is the current posture and
   `OPIP_PAPER_V2_MODE` is off.
7. Learning compatibility is exact-SHA: the learning worker must be deployed for the same
   SHA (`/deploy-learning`) before learning compute is healthy, and capture/outcomes fail
   closed on `RELEASE_DRIFT`.
8. F10 parity is not a paper-authority gate, but it *is* a delete gate for the duplicate
   read surfaces: legacy `/dashboard` and the Grafana panels cannot be retired before the
   fixture reconciliation and consumer census exist.

### 5. Rollback controls that already exist and must be preserved

| Asset | Existing rollback |
| --- | --- |
| Legacy scan admission | Compose and the cycle still call the scan; the ranker call remains in the scan |
| Feature Bus | Mode pin returns to `off` |
| Paper v2 | Mode `off`, with the Paper v1 monitor still running |
| Legacy `/dashboard` | Route stays mounted until cutover |
| Grafana cockpit | Previous dashboard JSON restored |
| Alert presentation | Previous template restored after one release of the new renderer |
| Canonical writer | Stays; it is the target store and is currently shadow |
| Release profile | Profiles are exact and SHA-pinned; a rollback restores the prior approved profile |

### 6. Owner decisions this package is waiting on (no values invented here)

1. Record the Committee schedule policy (timer or oneshot) — the R6 prerequisite that this
   report also records as unsatisfied.
2. Authorize or refuse the paper-authority change and the evidence above.
3. Name a stop time per legacy writer before any writer is stopped.
4. Approve the consumer census, archive checksum and rollback proof before any deletion.
5. Ratify the open owner inputs (`OQ-03`..`OQ-10`), including `OQ-10` for F10.
6. Decide whether interactive Telegram command/callback replies must be delivery-ledger
   auditable (Gap 3.3 above).
7. Decide whether a dedicated in-repo analysis artifact (for example under
   `docs/atdd/evidence/`) is authorized for these two packages. They are recorded here,
   inside a path the active increment contract already maps, because adding a new
   documentation path without an owner-approved contract change would fail the
   `atdd-scope` gate as an unauthorized changed file.

### 7. What this package does not do

No writer was stopped, no store was migrated or deleted, no route or panel was removed, no
release profile was changed, no cutover was performed, no timetable and no numeric
threshold were invented, and no legacy component was declared obsolete. F10 and R7 remain
owner-gated: they must not be implemented until an owner approves an increment contract
for them.


## Traceability and validation for this session

| Claim | Source of truth | How it was verified |
| --- | --- | --- |
| The 14 host-runtime failures are environmental | the host's own MSYS shell | reproduced with no repository code: `bash -c 'echo probe-ok; /bin/true; echo after-fork-ok'` returns 254 with `fork: Resource temporarily unavailable`; five measured runs of the test file are tabulated above |
| The increments did not modify the failing test file | git | `git diff main...HEAD -- tests/test_opip_committee_host_runtime_v1.py` is empty |
| No Linux CI evidence exists for R5-F or R6 | `.github/workflows/pytest.yml` plus the GitHub API | `pytest.yml` triggers only on `main`; `gh pr view 370/371` shows feature-branch bases; `gh run list --commit` for `fd6fa7e2`, `7605b334` and `2cbb8f78` returns only `Semgrep` (success) and the five review workflows that fail at every commit of the stack; `gh pr checks` lists no pytest or `atdd-scope` job |
| The F10 contract and its gaps | `docs/architecture/v1.5.0/ARCHITECTURE.md`, `v1.4.4/ARCHITECTURE.md`, `OPIP_CONFORMANCE_LEDGER.md`, `OPIP_R5_REQUIREMENTS_REGISTER.md`, `OPIP_RETIREMENT_LEDGER.md` | contract lines cited above; current-implementation rows read from this worktree at `7605b334` |
| The R7 authorities, gates and rollback controls | `app/jobs/scan_opportunities.py`, `app/core/config.py`, `app/services/paper_v2_cutover_readiness.py`, `OPIP_RETIREMENT_LEDGER.md`, `docs/architecture/v1.2/CONSUMER_CENSUS.md` | symbols and constants read from this worktree; ledger rows quoted above |
| R6 and R5-F still pass | the increments' acceptance tests | `python -m pytest tests/test_opip_r6_committee_shadow_review_readiness.py tests/test_opip_r5_f_prospective_outcome_integration.py -q` → `44 passed in 3.61s` |
| The changed-file set is authorized by the active increment | the `atdd-scope` gate | `python tests/atdd_scope.py --increment ATDD-R6-committee-shadow-review-readiness --git-base fd6fa7e2911ce7cbc4b15ea22c1f5b7ef76efee4` → `PASS` |
| This session changed documentation only | git and the compiler | `git status --porcelain` lists only `OHM-Trade-Agent-v1/AUTONOMOUS_BUILD_REPORT.md`; `python -m compileall -q app` exit code `0` |

No merge, deployment, activation, timer enablement, Feature Bus widening, Paper-v2
enablement, writer stop or deletion was performed, and none is proposed by this report.

