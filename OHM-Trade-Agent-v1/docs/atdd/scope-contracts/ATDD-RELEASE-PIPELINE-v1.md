INCREMENT:
ATDD-RELEASE-PIPELINE-v1

OWNER-APPROVED INTENT:
This is the OWNER-authorized Release Pipeline v1 increment. It introduces an allowlisted release-profile contract (`SAFE_BASELINE`, `EVIDENCE_SHADOW`, future `TARGET_PAPER`) as the explicit, fail-closed activation authority for the production evidence plane, and it records the bounded OWNER governance supersession that permits `EVIDENCE_SHADOW` to place the Feature Bus into `shadow`.

The OWNER explicitly authorizes `OPIP_FEATURE_BUS_MODE=shadow` ONLY under the selected, validated `EVIDENCE_SHADOW` release profile. This supersedes the earlier repository-current posture assertion (`OPIP_FEATURE_BUS_MODE=off`) that older increments froze because those increments were not authorized to activate it. The supersession is narrow: it does not change any safety requirement, and `TARGET_PAPER` (Paper-v2) remains BLOCKED.

Do not edit history. The older contract statements that the Feature Bus remained `off` at their increment are preserved as historical scope; the superseded *current-runtime* prohibition is replaced by the release-profile control plane, and the acceptance tests that wrongly converted historical scope into a permanent prohibition are corrected to prove both facts.

ARCHITECTURE REFERENCES:
- `docs/architecture/v1.4.3/OPIP_Profit_Intelligence_Architecture_v1_4_3.docx` (authority, SHA256 `ab494a19867831deb43087af2820bbb8eac7e3b310c6b0dab9c3f17d3c93ce83`) and `docs/architecture/v1.4.3/ARCHITECTURE.md`.
- `docs/architecture/OPIP_RECOVERY_ROADMAP.md` R3: "The Feature Bus mode stays `off` during R3 unless a separate owner approval changes it." This increment is that separate OWNER approval, bounded to `EVIDENCE_SHADOW`.
- `docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md` AC-016/AC-020/AC-021: the bounded, dual-gated, non-authoritative SHADOW capture machinery this profile authorizes.
- `docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md` AC-014: the no-weakening governance control amended narrowly here.
- `docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md` Packet A.
- `docs/architecture/v1.2/A_PAPER_MANDATE.md` and `CODING_BOUNDARY_CONTRACT.md`: paper-only authority, no funded execution.
- `docs/atdd/scope-contracts/ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1.md`: the active-increment pointer is deliberately movable.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN:
the allowlisted release-profile contract
WHEN:
the profiles are enumerated and resolved
THEN:
the profiles are exactly the allowlist `SAFE_BASELINE`, `EVIDENCE_SHADOW` and `TARGET_PAPER`; `SAFE_BASELINE` resolves to the non-authoritative baseline modes (Feature Bus `off`, canonical writer `off`, target spine `off`, Paper-v2 `off`, Committee `off`, and feasibility-capture notional `0.0`); `EVIDENCE_SHADOW` resolves to exactly Feature Bus `shadow`, canonical writer `shadow`, target spine `shadow`, Paper-v2 `off`, Committee `off`, and feasibility-capture notional `1000.0`; `TARGET_PAPER` is present but BLOCKED (it may not be selected or validated as ready); an unknown, malformed, differently-cased or whitespace-padded profile name fails closed; and the contract grants no funded, exchange, order, Committee or Telegram authority

AC-002:
GIVEN:
the selected release profile and the repository-controlled production compose
WHEN:
the profile is applied and the architecture gate is evaluated
THEN:
the deploy resolves the selected profile into the exact fixed modes of its allowlist entry (a profile-to-environment resolver returning only the profile's declared keys), the core service `environment` block carries those exact literal modes including `OPIP_PAPER_V2_MODE=off`, `OPIP_COMMITTEE_MODE=off` and `OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD=1000.0` for EVIDENCE_SHADOW so `env_file: .env` cannot override them, the gate PASSES only when the observed runtime posture equals the selected profile's allowed modes and FAILS CLOSED otherwise, and a stale or free-form `.env` value cannot elevate `SAFE_BASELINE` to `EVIDENCE_SHADOW`, inject an unexpected `OPIP_*` mode key, or change any mode

AC-003:
GIVEN:
the older increments that froze the Feature Bus `off` and this later OWNER authorization
WHEN:
the historical contracts and the supersession are inspected
THEN:
the older contracts still record their historical `off` posture and their historical intent (nothing is rewritten to claim they authorized activation), a later OWNER-authorized supersession contract records the bounded `EVIDENCE_SHADOW` authorization, the current repository posture is governed by the release profile rather than by a permanent prohibition, and the supersession changes no safety requirement (one canonical writer/history, one Feature Bus, one F3-F7 spine, no second scheduler/evidence store/calibration system, point-in-time correctness, bounded capture, protection independence, stale `.env` cannot activate, legacy sole new-entry authority, Paper-v2 off, target spine non-authoritative)

AC-004:
GIVEN:
the lifecycle/no-weakening governance guards (the bridge AC-014 guard and the movable-pointer scope-control guard)
WHEN:
they are audited against the supersession
THEN:
neither guard converts an historical runtime literal into a permanent prohibition, both still prove that a completed increment remains identifiable through its own scope contract and that its substantive isolation, authority and feature-bus assertions remain present in its acceptance module, they record that a later OWNER-approved increment may supersede an old runtime posture only with explicit owner provenance and its own acceptance tests, and a mutation/adversarial check proves that an unapproved deletion or weakening of the isolation assertions still fails

AC-005:
GIVEN:
the release architecture gate and its receipt
WHEN:
a release posture is evaluated for a profile
THEN:
the gate fails closed on any missing, malformed or inconsistent profile or runtime posture, including drift in any fixed feasibility-capture notional, reports a deterministic verdict and a concise receipt (profile, new-entry authority, funded authority, Paper-v2 and Committee state, protection posture, and each named check), asserts `PAPER_V2_REMAINS_OFF`, `COMMITTEE_RUNTIME_AUTHORITY_ABSENT`, `FUNDED_AUTHORITY_ABSENT`, `FUNDED_CREDENTIAL_PATH_ABSENT_FROM_PAPER` and `PROTECTION_INDEPENDENT`, keeps `NEW_ENTRY_AUTHORITY=LEGACY_ONLY` for `SAFE_BASELINE` and `EVIDENCE_SHADOW`, and grants no authority and performs no write

AC-006:
GIVEN:
the activated evidence plane
WHEN:
the runtime posture is inspected
THEN:
`OPIP_PAPER_V2_MODE` and `OPIP_COMMITTEE_MODE` are explicitly pinned to `off` in the core service, the EVIDENCE_SHADOW feasibility-capture notional is pinned to `1000.0`, the legacy path remains the sole new-entry paper authority, the target spine is shadow and non-authoritative, the bounded capture cron entries remain the scheduler mechanism and are never invoked from inside the protected unified cycle, the rollback posture is `SAFE_BASELINE` (the rollback Compose override deterministically disables capture even when the previous code SHA has a different profile marker), and no funded/live, exchange, order, margin or Committee authority is introduced

AC-007:
GIVEN:
the F5 feasibility-validation notional for the first prospective evidence epoch
WHEN:
the profile, the production compose and the capture are inspected and exercised
THEN:
the notional is a fixed repo-controlled evidence constant: `EVIDENCE_SHADOW` resolves `OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD=1000.0` and the core service pins exactly `"1000.0"` as a literal (never derived from live account equity, and not varied within the epoch), while `SAFE_BASELINE` resolves `0.0` so capture stays disabled; `validate_profile_contract` rejects an arbitrary or free-form notional (a non-`1000.0` value for `EVIDENCE_SHADOW`, any non-zero value for `SAFE_BASELINE`); the architecture gate passes only when the configured notional equals the profile value and fails closed on drift; the capture resolves the notional from the configured value and refuses a `--notional-usd` override that differs from it (so an arbitrary notional can never influence an epoch); a read-only diagnostics probe reports the committed `feature.snapshot.recorded` and `feasibility.evidence.recorded` counts, the canonical `local_sequence` high-water figure and the latest feasibility `validation_notional_usd` by reading the exported canonical replica read-only (it opens no live store, takes no explicit lock and mutates nothing, and reports `canonical_evidence_counts=UNAVAILABLE` rather than a fabricated zero when the replica is absent or unreadable); and the change grants no new-entry, reservation, order, exchange, funded or Committee authority

AC-008:
GIVEN:
a pull request or a commit pushed to `main`
WHEN:
the release CI workflow evaluates the commit
THEN:
it runs the exact allowlisted `EVIDENCE_SHADOW` architecture evaluator, emits `ARCHITECTURE_GATE`, `PROFILE`, `NEW_ENTRY_AUTHORITY`, `FUNDED_AUTHORITY`, `PAPER_V2`, `COMMITTEE_MODE`, `PROTECTION`, `FEATURE_BUS`, `CANONICAL_WRITER` and `TARGET_SPINE`, retains a bounded receipt, and a successful main run produces an exact-SHA release-candidate receipt only after pytest, ATDD scope, architecture and security gates pass; the candidate does not deploy

AC-009:
GIVEN:
an exact-SHA release candidate and the existing issue #64 deployment control plane
WHEN:
the owner requests `/deploy-profile <PROFILE> <40-char-sha>`
THEN:
only the repository OWNER on issue #64 (or repository-owner-only manual dispatch) is accepted; `EVIDENCE_SHADOW` is the only deployable profile, `SAFE_BASELINE` is rollback-only, and `TARGET_PAPER` remains blocked; the SHA equals the current `main`; the exact-SHA pytest/ATDD/security/architecture workflow is green; the checked-out SHA's Compose profile marker and literal modes match the requested profile; the host refuses untracked or unexpected ignored files in the application build context, and Docker excludes secrets and generated caches; approval is auditable in the workflow receipt; and deployment uses only the existing forced-command `deploy <sha>` path, with the legacy `/deploy <sha>` spelling subject to identical `EVIDENCE_SHADOW` gates

AC-010:
GIVEN:
an owner-approved exact-SHA EVIDENCE_SHADOW deployment
WHEN:
the candidate services and scheduler have started
THEN:
the host records read-only canonical cursors before deployment, verifies the running container image label and every fixed mode literal (including the fixed feasibility-capture notional) against the approved SHA, and invokes a read-only verifier bounded to 360 seconds; that verifier requires two fresh consecutive 60-second snapshots on an exact 60-second grid created after candidate readiness, at least one matching prospective F5 record whose source cutoff is not later than its evaluation time, HEALTHY read-only protection, and the target spine's inert no-source/no-handoff posture; the host also proves each unified/capture scheduler entry occurs once and retains flock/timeout bounds; any missing or malformed evidence fails before the core commit point and enters the existing rollback transaction

AC-011:
GIVEN:
the runtime verifier or a pre-commit deploy assertion fails
WHEN:
the existing `ohm-deploy` rollback runs
THEN:
it restores the previous code SHA but overlays explicit SAFE_BASELINE literals for Feature Bus, writer, target spine, Paper-v2 and Committee and sets feasibility-capture notional to `0.0`; core, writer and paper topology health must pass and the running core's effective mode literals must be re-read before the host emits `OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS`; otherwise rollback remains unproven and the workflow fails closed; the deployment receipt cannot report runtime success without the exact approved SHA, runtime marker, capture, protection, scheduler and rollback dispositions

AC-012:
GIVEN:
the deploy-time release-scheduler contract verification and the two bounded evidence producers
WHEN:
the scheduler lock invariant is verified
THEN:
the verification checks the REAL invariant rather than a stale symbol name: the shared in-container process-level advisory lock implementation (`class CaptureProcessLock`, `run_capture_locked`) is present and BOTH producers use the locked capture entrypoint; each producer's lock identity is a genuine path assignment; the Feature Bus identity (`FEATURE_BUS_CAPTURE_LOCK_PATH`, with `DEFAULT_PROCESS_LOCK_PATH` retained as a same-value backwards-compatible alias) is DISTINCT from the feasibility identity (`FEASIBILITY_CAPTURE_LOCK_PATH`); and the host cron locks remain unique and bounded (one `flock -n` entry per producer on its own `/var/run` lock, no duplicate command in root crontab); the check is explicit and diagnostic (each failure names the missing guard or the shared identity on stderr and returns non-zero) rather than a silent `grep -Fq` that aborts the release, and the identity extraction itself cannot abort silently (a no-match is captured and reported as a diagnostic); it emits `OPIP_RELEASE_SCHEDULER=UNIQUE_BOUNDED` on success; an adversarial test runs the actual verification logic against the real producer sources and proves it fails closed when the process-lock guard is removed, when the lock identity is undefined, and when the two producers share an identity; and the change alters no release profile, mode, capture authorization, notional, or trading/paper/funded/Committee authority

AC-013:
GIVEN:
the deploy-time runtime verification that observes the unified cycle read-only and then runs the release runtime verifier
WHEN:
the two verification stages are budgeted and exercised
THEN:
the unified-cycle observation and the runtime verifier have SEPARATE, independently named budgets (no single shared deadline, and the verifier never receives only the leftover of the cycle observation); the unified cycle is a LEGACY observation surface and is NOT part of the EVIDENCE_SHADOW evidence plane, so its completion is NOT a release gate: the deploy observes it read-only for a short, bounded window (`UNIFIED_CYCLE_OBSERVATION_SECONDS`), reports what it saw (`OPIP_UNIFIED_CYCLE_OBSERVED` = `SUCCESS`/`DEGRADED`/`NONE`, plus `OPIP_UNIFIED_CYCLE=HEALTHY`/`DEGRADED`/`NOT_OBSERVED`), and ALWAYS proceeds to the authoritative runtime verifier without failing the deploy and without adding a second long wait; the scheduler hard runtime bound declared in the installed scheduler entry (`timeout --signal=TERM --kill-after=Ns <BOUND> ... app.jobs.run_cycle`) is still DERIVED (never hardcoded) so the receipt stays aligned with the authorized scheduler, and failure to derive it fails closed rather than using an arbitrary value; the runtime verifier keeps its own bounded window equal to `app.services.release_runtime_verifier.MAX_WAIT_SECONDS` and remains the authoritative prospective-evidence gate; the observation reports a fresh completion only when its completion time is at or after candidate readiness (a stale pre-readiness completion is reported as `NONE`), and a fresh `DEGRADED` cycle is reported as `DEGRADED` without failing the deploy; it emits the machine-readable markers `OPIP_UNIFIED_CYCLE_WAIT_SECONDS`, `OPIP_RUNTIME_VERIFY_BUDGET_SECONDS`, `OPIP_DEPLOY_PHASE=UNIFIED_CYCLE_OBSERVATION_BEGIN`/`_END` and `OPIP_DEPLOY_PHASE=RUNTIME_VERIFICATION_BEGIN`/`_END`; it launches no second cycle or scheduler and mutates no protection semantics; and adversarial tests prove a healthy cycle completing beyond the former 360-second window is observed, that `DEGRADED`, stale success and no-completion are all observed without failing the deploy, and that the scheduler hard-bound derivation fails closed when it cannot be determined; the canonical cycle records durable progress so a bound kill is attributable rather than silent: it emits one explicitly flushed `OPIP_UNIFIED_CYCLE_PHASE=<NAME>` marker on entering each top-level phase (including the lock-contention skip and interrupted-search recovery) and flushes both terminal markers, so the last phase reached survives the scheduler's `timeout` termination -- which discards block-buffered stdout over the scheduler's file-redirected pipe -- is retained in the cycle log, and is reported as `OPIP_UNIFIED_CYCLE_LAST_PHASE` (or `NONE` when the log carries no phase), without changing any phase's behavior, order, timing, authority or release semantics; the log/entry test seams are inert in production (honored only under an explicit `OPIP_DEPLOY_TEST_SEAMS=1` marker) so a forged log or an arbitrary scheduler entry cannot fake the observation signal or extend the window; an EMPTY completion value is never handed to `date -d` (GNU `date -d ""` resolves to midnight of the current day, which would fabricate a plausible-looking age for a cycle that never completed), so `OPIP_UNIFIED_CYCLE_LAST_COMPLETION_AGE_SECONDS` reports `UNKNOWN` unless the value is non-empty and matches the emitted timestamp shape; and a bounded observation with no fresh completion is diagnosable from the deploy log alone: the controller emits a bounded, structured, strictly read-only evidence block (`OPIP_UNIFIED_CYCLE_WAIT_FAILURE=NO_FRESH_COMPLETION`) covering the cycle log's existence/size/mtime, tail counts for `SUCCESS`/`DEGRADED`/completions/lock-contention skips/tracebacks, the latest status plus completion age and the last durable cycle phase reached, the installed cron entry's existence/schedule/log target, the host wrapper lock's path and `PRESENT`/`HELD` ownership as read from the kernel lock table by the lock's own device:inode, and a line-, match-, byte- and per-line-bounded tail allowlisted to release markers and exception frames; the reporter opens no lock, takes no lock, starts no cycle/container/scheduler, creates/truncates/deletes no file, never inspects the container, degrades each undeterminable probe to `UNKNOWN` rather than guessing and never asserts a cause the evidence cannot support, and runs only after the observation window has elapsed, so it cannot change the observation's semantics or mask the original reason

AC-014:
GIVEN:
an in-flight production deploy whose controller has already snapshotted `/usr/local/sbin/ohm-deploy` into exactly one `/var/lib/ohm-deploy/scheduler-before.*` transaction and then checked out the target SHA
WHEN:
that target SHA's `deploy/remote/reconcile-scheduler.sh` runs, before it alters the transaction snapshot
THEN:
it proves the repository HEAD is a 40-character SHA, proves `deploy/remote/ohm-deploy` is a non-empty regular file, passes `bash -n` on that target controller, requires exactly one real `scheduler-before.*` directory, requires that snapshot's `remote-op-ohm-deploy.present` marker and a non-empty regular snapshot controller, and compares the snapshot controller to the target; when they differ it atomically replaces only `scheduler-before.*/remote-op-ohm-deploy` with the target controller at mode `0755` (so a later rollback can execute it) and emits `OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=ARMED` plus `OPIP_DEPLOY_CONTROLLER_BOOTSTRAP_SHA=<target SHA>`; when they already match it emits `OPIP_DEPLOY_CONTROLLER_BOOTSTRAP=NOT_NEEDED` and does not rewrite the snapshot; zero snapshot directories, multiple snapshot directories, a missing present marker, a non-regular or empty snapshot controller, a non-regular target, a `bash -n` failure, or a HEAD that is not a 40-character SHA fail closed without replacing the snapshot; the SSH gateway, learning reader, learning diagnostics, cron snapshots, root crontab snapshot, `last-good-sha`, and SAFE_BASELINE application rollback are not modified; a reconcile with no `scheduler-before.*` entry (initial host bootstrap) does not invent a snapshot and does not fail; path overrides are inert unless `OPIP_DEPLOY_TEST_SEAMS=1`; Paper-v2 stays `off`, legacy remains the sole new-entry authority, funded/live authority stays absent, Committee stays `off`, and `TARGET_PAPER` stays blocked

AC-015:
GIVEN:
the production Release Pipeline failure of run 37174893501 (a healthy unified cycle, no runtime-verifier PASS/FAIL receipt, a deploy that exited `124`, and a rollback after which `opip-canonical-writer` was unhealthy and `SAFE_BASELINE_ROLLBACK` was `UNPROVEN`)
WHEN:
the release pipeline's canonical-writer authority, runtime-verifier window and rollback ordering are exercised
THEN:
(a) `opip-canonical-writer` is the SOLE writable canonical-store owner (it holds `CanonicalStoreLock` for its process lifetime and acquisition fails closed with no wait-and-retry), the F5 feasibility-evidence producer opens no second writable store handle and submits `feasibility.evidence.recorded` as a `WriterIntent` through the canonical writer client/service, a real writer server owning the store still refuses a second writable `CanonicalWriter`, and a submission over the client path becomes readable canonical history with its existing idempotency key, event schema, LOW priority, correlation id, cursor and retry semantics unchanged; no second writer architecture, store owner or lock-file deletion is introduced
(b) failure to reach the canonical writer is fail-closed and retryable: the producer counts a retryable disposition, publishes nothing, and does NOT advance or drop the evidence cursor, so the record is re-attempted rather than silently skipped
(c) `MAX_WAIT_SECONDS` stays exactly 360 and every operation inside the runtime verifier's polling loop is bounded against the verifier's own deadline: the deadline is tested BEFORE each read, the inter-attempt sleep is clamped to the remaining budget, and a single read exceeding the declared `MAX_SINGLE_READ_SECONDS` bound stops the loop with an explicit `READ_OVERRUN` reason, so an ordinary "matching F5 evidence never arrived" outcome terminates under the verifier's own control within its own budget and emits a machine-readable FAIL receipt (`OPIP_RELEASE_RUNTIME_VERIFICATION=FAIL`, the failure class, stage, attempts, observed seconds, and the last observed feature-snapshot count, fresh-instrument count, consecutive-60s-snapshot status, feasibility-evidence count and matching-F5 status) instead of the outer watchdog's `124`; a slow but successful read is never falsely failed; and the deploy's outer containment exceeds the verifier window plus the declared single-read bound, so that watchdog is emergency containment only and never the normal timeout mechanism
(d) rollback quiesces candidate evidence producers BEFORE any rebuild: it removes the candidate producer schedule and terminates, then SIGKILL-escalates, every process matching the two evidence-producer modules by scanning `/proc` with the container's own interpreter (no `pkill`/`pgrep` dependency), all under a bounded host exec; cron removal alone is NOT sufficient, because cron may already have spawned a host wrapper (`flock -n <lock> ... docker compose exec ...`) whose in-container producer has not yet appeared, so quiescence ALSO takes exclusive ownership of the two EXISTING producer host launch-lock identities (`/var/run/opip-feature-bus-capture.lock`, `/var/run/opip-feasibility-capture.lock`) with a finite bounded wait and HOLDS them on stable file descriptors across the whole critical section — previous SHA reset, SAFE_BASELINE override creation, previous writer rebuild/start, previous core rebuild/start, core health proof, writer health proof and SAFE_BASELINE mode validation — so an already-launched wrapper is waited for and no late wrapper can start a producer; ownership is never proven by deleting a lock file; quiescence returns NONZERO, and rollback aborts before `git reset --hard`, before rebuilding or starting the previous writer, emitting `OPIP_EVIDENCE_PRODUCER_QUIESCENCE=FAILED` with `OPIP_EVIDENCE_PRODUCER_QUIESCENCE_REASON=<reason>`, `OPIP_ROLLBACK_ABORTED=PRODUCER_QUIESCENCE_UNPROVEN` and `OPIP_SAFE_BASELINE_ROLLBACK=UNPROVEN`, whenever the bounded launch-lock wait expires, the producer process state cannot be established, the `docker compose exec` fails, a matching producer survives TERM/KILL, or any other proof of launch-barrier ownership plus zero in-container producers fails; a fail-closed abort never deletes the pre-deploy scheduler transaction — it is preserved under a `scheduler-recovery.*` name outside the `scheduler-before.*` namespace the deploy-controller bootstrap counts, so the next deploy's exactly-one-transaction proof still holds, its path is published as `OPIP_ROLLBACK_SCHEDULER_SNAPSHOT_PRESERVED=<path>`, and only the SAFE_BASELINE override is dropped; the held launch locks are released only AFTER the previously snapshotted scheduler state has been restored, so restored cron entries cannot relaunch a producer before SAFE_BASELINE is proven; only then does it continue to restore the previous SHA, apply the SAFE_BASELINE override, prove core health, prove writer health when the writer is part of that SHA, validate SAFE_BASELINE modes, restore the paper topology, and finally emit `OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS`; a failed quiescence exec is reported `UNKNOWN` and surviving processes are reported `NOT_QUIESCED` rather than assumed away, and a writer health failure additionally emits bounded classified diagnostics distinguishing store-lock contention, SQLite/schema/integrity failure, a stale Unix socket, a writer process crash, and a container healthcheck/startup failure
(e) no authority is widened anywhere in this increment: Paper-v2 stays `off`, the Committee stays absent, funded/live and exchange/order authority remain absent, the legacy path remains the sole new-entry authority, and `TARGET_PAPER` remains BLOCKED

AC-016:
GIVEN:
the local engineering support tooling added for this repository: the Microsoft VS Code workspace configuration under `.vscode/`, the local Aider editing bootstrap, `.aiderignore`, and the local reproducibility dependencies recorded in `requirements-dev.txt`
WHEN:
the local tooling bootstrap is inspected for scope, authority and reproducibility
THEN:
Microsoft VS Code and Aider are local engineering support tooling only and hold no authority; GitHub Linux CI remains the canonical full regression and Linux execution authority; the Windows targeted pytest task is not equivalent to the canonical full Linux regression; the Windows targeted coverage task is local and advisory only; the Engineering Health task remains advisory; the local tooling grants no runtime authority, no production deployment authority, no live or funded trading authority, no Paper-v2 activation authority, no Committee activation authority and no PR merge authority; it introduces no automatic git push and no automatic commit; `.aiderignore` excludes secrets and generated/runtime artifacts; `pytest-cov` and `pyright` are reproducible DEVELOPMENT-only dependencies recorded in `requirements-dev.txt`; `requirements.txt` remains the production/runtime dependency authority and is unchanged; and the existing release-profile, trading, scheduler, protection, writer, Feature Bus and deployment semantics are unchanged

AC-017:
GIVEN:
two PROVEN `EVIDENCE_SHADOW` runtime-qualification blockers: a Feature Bus capture pass whose retry/backoff sequence can consume the whole bounded pass and is killed by cron containment before it records anything (`CAPTURE_PASS_OVERRUN`), and an F5 feasibility source cutoff derived only from the close of the latest completed 60-minute candle, which can satisfy the UNCHANGED 120-second runtime source-age contract only in the ~two minutes after an hour boundary
WHEN:
the two evidence producers are exercised against the production timing and containment contracts
THEN:
(a) every Feature Bus acquisition obeys the remaining pass/wave deadline: the transport's FULL sequence -- every attempt, every inter-attempt backoff+jitter sleep and every rate-limiter wait -- is bounded by the pass-scoped client's absolute monotonic deadline and by a per-attempt timeout DERIVED so `attempts * attempt_timeout * KRAKEN_ATTEMPT_PHASE_BOUND + worst_case_backoff + rate_wait <= wave budget` (never the naive `budget / attempts`), no retry begins when its worst-case cost cannot fit the remaining budget, the pass splits its declared budget into an acquisition deadline and a reserved Phase-B materialization window so an acquisition wave can never consume the time needed to COMMIT what it acquired, no snapshot is materialized when acquisition failed, and one pass-scoped client (rather than repeated default-timeout clients) serves both the instrument provider and the minute source
(b) producer progress is durable even when the outer 50-second cron containment kills the process: each pass emits explicitly flushed, line-buffered `OPIP_FEATURE_BUS_CAPTURE_PHASE=` / `OPIP_FEASIBILITY_CAPTURE_PHASE=` markers for start, universe/refresh readiness, acquire, acquire_complete, materialize and done, plus explicit dispositions for deadline exhaustion, request timeout, acquisition failure, lock contention and zero materialized snapshots, so a bounded pass is never a silent evidence drop and the last durable marker names the stage that overran; the outer containment remains emergency termination only, not the normal producer stop
(c) the F5 feasibility producer keeps its EXISTING 60-minute analytical horizon (interval, thresholds, continuity and spike semantics unchanged) and SEPARATELY acquires a fresh closed one-minute Kraken observation of the SAME instrument during the same acquisition, using that anchor's close as the canonical `source_cutoff` -- the freshest market datum that genuinely supports the determination -- so `F5 commit time - source_cutoff <= MAX_FEV_SOURCE_AGE` holds for a normally functioning one-minute capture regardless of where the pass falls within the hour
(d) the evidence records BOTH provenance planes explicitly as version-compatible `source_evidence_refs` tokens (the analytical interval/bars/latest hourly cutoff AND the freshness anchor's interval, bar open/close, acquisition instant and calculated source age) with NO schema change, and F5 lineage still names exactly the originating `FeatureSnapshot`
(e) a missing, not-yet-visible, after-epoch or over-age freshness anchor fails closed (no synthetic or backdated timestamp is ever manufactured) and is recorded as a durable flushed disposition, while the UNCHANGED `release_runtime_verifier` still rejects the old hourly-anchored cutoff at the production timings and accepts only the corrected freshness-anchor semantics
(f) no release-profile mode, freshness window, 3600/3720 timing contract, trading/paper/funded/Committee authority, Feature Bus/canonical-writer/spine mode, protection semantics or deploy control plane is changed

AC-018:
GIVEN:
an EVIDENCE_SHADOW production qualification where Feature Bus failed closed during restore_continuity with INSUFFICIENT_SETUP_BUDGET before acquisition, producing zero FeatureSnapshots and therefore zero F5 evidence

WHEN:
continuity is restored for the committed instrument batch

THEN:
(a) production scans FEATURE_CHECKPOINT_RECORDED at most once per restore batch and MARKET_OBSERVATION_RECORDED at most once per restore batch and reconstructs per-instrument state/ledger/watermark;

(b) canonical ordering, validation/filter order, feature-version semantics, interval semantics, independent since_interval_epoch, revision/conflict behavior remain unchanged;

(c) the same absolute setup deadline and clock bind SQLite and Python reconstruction, failure is fail-closed, no partial output, no continuity skip/cold-start fallback;

(d) single-instrument loader APIs and historical injected restore callbacks remain compatible;

(e) bounded telemetry attributes checkpoint, ledger and total restore time;

(f) no timeout, verifier, F5, cadence, scheduler, deploy, Paper-v2, Committee or authority change.

AC-019:
GIVEN:
an EVIDENCE_SHADOW production qualification where continuity restoration completed and acquisition reached Phase B, but the first Feature Bus cold start inherited an oversized venue-history batch, the declared canonical-submit workload could not fit the unchanged materialization budget, the producer failed closed with MATERIALIZE_INCOMPLETE, and zero FeatureSnapshots/F5 evidence were produced

WHEN:
a production/default Feature Bus source has no restored source watermark for an instrument

THEN:
(a) the cold-start market request is bounded to exactly the feature engine's declared MINIMUM_WARMUP_INTERVALS ending at the current latest closed cutoff, derived from the engine constant rather than a duplicated number;

(b) rows older than that warm-up floor are not admitted even if the venue returns more history than requested, so upstream behavior cannot silently inflate the canonical write workload;

(c) the pass still executes exactly one current-cutoff Feature Bus cycle/snapshot for the instrument and never emits a backdated catch-up snapshot series;

(d) once a real source watermark exists, the existing resumed tip/revision/correction semantics are unchanged;

(e) the existing Phase-B submit-bound admission, absolute writer deadline, 45/50-second pass budget, 60-second cadence, runtime verifier, continuity semantics and fail-closed behavior are unchanged and are never weakened to force a cold start through;

(f) no release-profile, F5, scheduler, deploy, Paper-v2, Committee, funded/live/exchange/order or other authority change is introduced.

AC-020:
GIVEN:
an EVIDENCE_SHADOW production qualification where continuity restoration returned a real but STALE historical source watermark for a committed instrument, so the resumed source path requested an unbounded historical catch-up batch, the declared canonical-submit workload could not fit the unchanged materialization budget, and the producer failed closed with MATERIALIZE_INCOMPLETE while zero FeatureSnapshots and zero F5 evidence were produced

WHEN:
continuity restoration returns a source watermark whose tip is older than the current declared feature warm-up window ending at the latest closed cutoff

THEN:
(a) market acquisition is bounded to exactly the feature engine's declared MINIMUM_WARMUP_INTERVALS ending at the current latest closed cutoff, derived from the engine constant and the source interval rather than a magic number, so a stale durable watermark can never request an arbitrary historical catch-up;

(b) rows older than that warm-up floor are rejected before normalization, so even a venue that returns more history than requested cannot inflate normalization work, ingestion ordering or the declared canonical-submit workload;

(c) the re-acquisition is a RESTART_WARMUP, not a NEW_LISTING_COLD_START: the stale watermark is carried rather than reset, ingestion order and watermark lineage stay monotonic and advance truthfully to the new current tip, and the existing RollingState gap/reset/restart logic records the discontinuity instead of a second state machine;

(d) the exact stale boundary is derived from the declared warm-up window and interval -- a tip strictly older than the warm-up floor is stale while a tip exactly at the floor is an ordinary bounded resume -- and both sides of that boundary are tested;

(e) a current or recent restored watermark keeps the existing resumed tip re-admission, OHLC correction, revision/superseding, ingestion order, source sequence and coverage semantics unchanged, and the no-watermark AC-019 cold-start behavior is unchanged;

(f) a default/production-composition run (continuity restore -> stale source watermark -> default Kraken source -> run_cycle) produces exactly one current-cutoff FeatureSnapshot and results in a 140-observation batch that is admissible under the unchanged Phase-B submit-bound budget;

(g) no timeout, verifier, F5, cadence, scheduler, deploy, writer-budget, materialization-reserve, release-profile, Paper-v2, Committee, funded/live/exchange/order or other authority change is introduced.

AC-022:
GIVEN:
an EVIDENCE_SHADOW runtime-verification posture failure where the read-only Kraken exposure resolver completed but could not prove complete exposure coverage

WHEN:
the protection-health report and runtime failure receipt are produced

THEN:
(a) the read-only protection report preserves a non-empty resolver diagnostic as `resolution_reason` without changing the deterministic protection decision, so incomplete coverage remains `UNAVAILABLE`, admissions remain suspended and `EXPOSURE_COVERAGE_INCOMPLETE` remains present;

(b) a protection-posture failure carries that diagnostic into the release runtime receipt as the single-line machine-readable `OPIP_RELEASE_RUNTIME_PROTECTION_REASON`, with ASCII control-character runs replaced by one space while ordinary printable text is preserved;

(c) the diagnostic is observation-only: it is not an authority input, does not participate in HEALTHY/UNSAFE/UNAVAILABLE classification, does not change qualification or rollback semantics, and emits no raw balances, account identifiers, credentials, secrets or arbitrary account payloads;

(d) non-protection posture failures such as release-mode mismatch or target-spine mismatch do not fabricate `OPIP_RELEASE_RUNTIME_PROTECTION_REASON`;

(e) existing fail-closed protection, evidence counters, runtime-verifier bounds, release profiles, scheduler, Paper-v2, Committee, funded/live/exchange/order authority and rollback semantics are unchanged.

AC-023:
GIVEN:
an EVIDENCE_SHADOW live-posture failure where read-only protection health was not HEALTHY because Kraken balance identities ADA.S, ETH2.S, SEI.B, SUI.B and TAO.B could not be priced, while the evidence plane itself had already produced fresh Feature Bus snapshots and matching F5 evidence

WHEN:
the read-only exposure resolver observes a non-zero Kraken balance whose asset code is a documented balance extension or the ETH2 staking receipt

THEN:
(a) a documented balance extension (.S staked, .M opt-in rewards, .B yield-bearing/Earn, .F Kraken Rewards, .P parachain, .T tokenized) maps to its base asset, and the ETH2/ETH2.S staking receipt maps to ETH, using that underlying asset's preferred USD/USDT/USDC pair and computing notional as quantity times the observed price;

(b) the mapping is explicit and evidence-backed (Kraken's published balance-extension contract and the public pair catalog) and is applied only to balance identities, not to generic pair parsing;

(c) an unrecognized decorated form is not stripped or guessed, stays unpriced, and still produces EXPOSURE_COVERAGE_INCOMPLETE with admissions suspended;

(d) a priced holding with no matching lifecycle trade remains VERIFIED_UNMANAGED and still produces UNMANAGED_EXPOSURE_REQUIRES_REVIEW;

(e) protection-health classification, incident checks, admissions suspension, authentication handling and the absence of any exchange mutation path are unchanged, and no Paper-v2, Committee, funded/live/exchange/order or TARGET_PAPER authority is introduced.

EXPLICITLY OUT OF SCOPE:
- Activating TARGET_PAPER, Paper-v2, the Committee, or any funded/live/exchange/order authority
- Deleting, rewriting or rescoping the historical ATDD increments that recorded the Feature Bus `off`
- A second scheduler, evidence store, Feature Bus, calibrator, canonical writer or F3-F7 spine
- Replacing the compose pin with an unconstrained `${...}` expansion that a stale `.env` could drive
- Modifying the v1.4.3 authority DOCX
- Weakening the bridge no-weakening protection into a generic relaxation

FROZEN BOUNDARIES:
Release profile as the activation authority. The only mechanism that may place the Feature Bus into `shadow` is the explicit selection and validation of the `EVIDENCE_SHADOW` release profile. Free-form `.env` mode values are never the activation authority, and the core-service modes are literals that override `env_file: .env`. `SAFE_BASELINE` remains the rollback posture and resolves the Feature Bus `off` and feasibility-capture notional `0.0`.

History preserved. The supersession edits only *current-runtime* assertions. Every historical contract statement that the Feature Bus remained `off` at that increment is preserved and labelled as history; no historical intent is rewritten.

Bounded authorization. `EVIDENCE_SHADOW` authorizes exactly Feature Bus `shadow` + canonical writer `shadow` + target spine `shadow` + Paper-v2 `off` + Committee `off` + feasibility-capture notional `1000.0`. It grants no new-entry, reservation, order, exchange, funded, margin, Committee, dashboard or Telegram authority, and it does not authorize `TARGET_PAPER`. The bounded SHADOW capture remains dual-gated (Feature Bus AND writer exactly `shadow`) and never runs inside the protected unified cycle.

Rollback writer-recovery barrier. Before rollback may touch the canonical writer, rollback must have exclusive ownership of BOTH scheduled-producer host launch locks and must prove that no candidate evidence producer process remains inside the candidate core container. The launch locks are the existing producer flock identities the cron entries already use; their acquisition is a finite, fail-closed wait, never an unbounded block or an arbitrary sleep, and never a lock-file deletion. Ownership is held on stable file descriptors through previous-SHA reset, SAFE_BASELINE override creation, previous writer rebuild/start, previous core rebuild/start, core health proof, writer health proof and SAFE_BASELINE mode validation, and is released only after the previously snapshotted scheduler state is restored. Removing the candidate cron entries alone is explicitly NOT a quiescence proof. `UNKNOWN`, `NOT_QUIESCED` and launch-lock timeout all fail closed: rollback must not reset the SHA or restore the writer, and `OPIP_SAFE_BASELINE_ROLLBACK=SUCCESS` is never emitted in that case. A fail-closed abort preserves the pre-deploy scheduler transaction as durable recovery evidence and never deletes it; it is moved out of the `scheduler-before.*` namespace so the deploy-controller bootstrap's exactly-one-transaction proof is not broken on the next deploy. Paper-v2 stays `off`, the Committee stays `off`, funded/live authority stays absent, legacy remains the sole new-entry authority, and `TARGET_PAPER` stays BLOCKED.

Local engineering tooling. The `chore/vscode-aider-bootstrap-v2` local bootstrap (VS Code tasks/settings, Aider ignore rules and its development-only `pytest-cov`/`pyright` pins) is not an architecture or authority change. It adds no deploy, trading, Paper-v2, Committee or merge capability, and it does not alter the release-profile, scheduler, protection, writer or Feature Bus contracts above.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_release_pipeline_v1.py::test_ac_001_profile_allowlist_and_exact_modes
AC-001 -> tests/test_opip_release_pipeline_v1.py::test_ac_001_unknown_profile_fails_closed
AC-001 -> tests/test_release_runtime_verifier.py::test_runtime_verification_rejects_blocked_target_paper
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_profile_resolves_compose_and_gate_passes
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_stale_env_cannot_elevate_baseline
AC-002 -> tests/test_opip_release_pipeline_v1.py::test_ac_002_arbitrary_and_injection_overrides_fail
AC-003 -> tests/test_opip_release_pipeline_v1.py::test_ac_003_history_preserved_and_superseded
AC-004 -> tests/test_opip_release_pipeline_v1.py::test_ac_004_bridge_guard_allows_authorized_supersession
AC-004 -> tests/test_opip_release_pipeline_v1.py::test_ac_004_bridge_guard_still_fails_unapproved_weakening
AC-005 -> tests/test_opip_release_pipeline_v1.py::test_ac_005_gate_fails_closed_and_receipt
AC-006 -> tests/test_opip_release_pipeline_v1.py::test_ac_006_paper_v2_off_legacy_sole_authority
AC-007 -> tests/test_opip_release_pipeline_v1.py::test_ac_007_evidence_notional_is_repo_controlled
AC-007 -> tests/test_opip_release_pipeline_v1.py::test_ac_007_capture_refuses_a_free_form_notional_override
AC-007 -> tests/test_opip_release_pipeline_v1.py::test_ac_007_read_only_canonical_evidence_verifier
AC-012 -> tests/test_release_scheduler_lock_verifier.py::test_ac_012_verifier_checks_the_real_lock_invariant
AC-012 -> tests/test_release_scheduler_lock_verifier.py::test_ac_012_feature_bus_lock_identity_is_present_and_distinct
AC-012 -> tests/test_release_scheduler_lock_verifier.py::test_ac_012_verifier_accepts_the_real_producers_and_fails_on_weakening
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_cycle_wait_and_verifier_budgets_are_separated
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_verifier_budget_matches_the_app_max
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_wait_budget_aligns_with_the_scheduler_hard_bound
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_no_second_scheduler_or_cycle_is_launched
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_observes_a_fresh_success
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_observes_a_cycle_that_exceeded_the_old_window
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_observes_degraded_without_failing
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_ignores_a_stale_pre_readiness_success
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_no_completion_within_the_window_does_not_fail
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_scheduler_bound_is_derived_and_fails_closed
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_test_seams_are_gated_by_an_explicit_marker
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_wait_failure_emits_bounded_structured_evidence
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_wait_failure_evidence_is_read_only
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_no_completion_reports_structured_evidence
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_degraded_is_observed_without_failing
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_host_lock_probe_distinguishes_held_from_free
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_functional_evidence_classifies_a_silent_log
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_names_the_last_cycle_phase_reached_before_a_bound_kill
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_reports_no_phase_when_the_log_carries_none
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_ac_013_empty_completion_timestamp_is_never_parsed_as_midnight
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_unified_cycle_phase_markers_are_flushed_so_a_bound_kill_keeps_them
AC-013 -> tests/test_opip_deploy_verify_budget_v1.py::test_unified_cycle_phase_markers_cover_the_ordered_phases_and_terminal_status
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_changed_controller_updates_only_the_deploy_snapshot
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_identical_controller_is_idempotent
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_zero_snapshots_fail_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_multiple_snapshots_fail_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_non_regular_snapshot_fails_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_missing_present_marker_fails_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_bash_invalid_target_fails_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_malformed_head_fails_closed
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_no_transaction_reconcile_does_not_invent_a_snapshot
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_test_seams_are_inert_without_the_marker
AC-014 -> tests/test_opip_deploy_controller_bootstrap.py::test_ac_014_safe_baseline_rollback_and_authority_remain_unchanged
AC-008 -> tests/test_opip_release_pipeline_v1.py::test_ac_008_ci_gate_and_main_candidate_are_non_deploying
AC-009 -> tests/test_opip_release_pipeline_v1.py::test_ac_009_profile_approval_is_owner_only_exact_sha_and_gated
AC-009 -> tests/test_release_runtime_verifier.py::test_safe_baseline_is_not_a_deploy_candidate
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_requires_consecutive_fresh_snapshots_and_matching_fev
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_backfill_gaps_and_late_source_cutoffs
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_non_60_second_snapshot_grid
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_stale_snapshots_and_unmatched_fev
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_evidence_rejects_future_timestamps
AC-010 -> tests/test_release_runtime_verifier.py::test_runtime_posture_requires_profile_notional
AC-010 -> tests/test_opip_release_pipeline_v1.py::test_ac_010_runtime_verifier_precedes_commit_and_rolls_back_to_baseline
AC-010 -> tests/test_opip_release_pipeline_v1.py::test_ac_010_deployment_receipt_requires_runtime_verifier_and_baseline_rollback
AC-011 -> tests/test_opip_release_pipeline_v1.py::test_ac_011_rollback_success_requires_verified_safe_baseline_modes
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_sole_writer_owns_the_store_and_refuses_a_second_writer
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_feasibility_capture_publishes_through_the_writer_client_into_canonical_history
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_writer_client_outage_is_fail_closed_and_does_not_advance_or_drop_evidence
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_production_submitter_is_a_client_and_the_module_opens_no_writable_store
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_valid_evidence_returns_pass_within_the_verifier_budget
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_missing_matching_f5_evidence_returns_structured_fail_within_the_budget
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_receipt_cli_reports_structured_fail_instead_of_the_outer_watchdog
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_slow_read_stops_at_the_declared_read_bound_without_overrunning
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_slow_but_successful_read_is_not_falsely_failed
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_deploy_containment_exceeds_the_verifier_window_and_is_not_the_normal_timeout
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_rollback_quiesces_producers_before_the_writer_restore
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_rollback_never_claims_unproven_producer_quiescence
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_writer_health_failure_is_classified_for_the_operator
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_rollback_launch_lock_barrier_waits_for_an_in_flight_wrapper
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_rollback_launch_lock_barrier_blocks_a_delayed_producer
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_quiescence_survivor_after_sigkill_is_fail_closed
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_quiescence_exec_failure_is_fail_closed
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_producer_barrier_precedes_writer_restoration
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_clean_quiescence_holds_locks_until_scheduler_restore_then_succeeds
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_quiescence_abort_preserves_the_scheduler_snapshot
AC-015 -> tests/test_opip_canonical_single_writer_feasibility_v1.py::test_ac_015_no_authority_is_widened_by_this_increment
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_vscode_tasks_are_valid_json
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_targeted_pytest_task_defers_to_canonical_regression
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_targeted_coverage_task_is_local_and_advisory
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_pre_pr_local_gate_is_not_a_full_suite_run
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_engineering_health_remains_advisory
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_no_task_grants_deploy_trading_or_merge_authority
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_aiderignore_excludes_secrets_and_generated_artifacts
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_dev_dependencies_are_reproducible_and_local_only
AC-016 -> tests/test_opip_local_engineering_bootstrap_v1.py::test_ac_016_requirements_txt_remains_runtime_authority
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_analytical_horizon_and_fresh_anchor_are_separately_acquired
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_anchor_beyond_the_max_source_age_fails_closed
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_evidence_lineage_points_to_the_exact_source_snapshot
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_freshness_anchor_holds_anywhere_within_the_hour
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_freshness_dispositions_are_flushed_durable_markers
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_hourly_cutoff_cannot_satisfy_the_verifier_but_the_fresh_anchor_does
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_in_epoch_live_reads_are_admitted_with_their_own_cutoff
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_pending_anchor_retains_the_cursor_then_publishes_once_it_publishes
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_point_in_time_audit_refuses_a_post_epoch_input
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_point_in_time_violation_is_terminal_and_never_published
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_post_epoch_live_reads_are_not_published_as_point_in_time_support
AC-017 -> tests/test_opip_r4_b2_feasibility_producer.py::test_ac_021_stale_freshness_anchor_fails_closed_without_synthetic_freshness
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_absolute_deadline_cut_emits_a_durable_materialize_incomplete
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_absolute_writer_deadline_bounds_every_recv_of_a_multichunk_roundtrip
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_capture_client_declares_a_bounded_public_only_request_budget
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_cycle_whose_declared_maximum_cannot_fit_is_not_started
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_deadline_exhaustion_emits_a_durable_disposition_marker
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_failed_acquisition_emits_a_durable_disposition_marker
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_first_attempt_rate_limit_wait_is_inside_the_declared_wave_bound
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_materialization_admission_bounds_every_submit_of_a_cycle
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_materialization_reserve_is_retained_for_phase_b
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_no_deadline_roundtrip_keeps_the_full_per_operation_timeout
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_phase_b_binds_one_absolute_deadline_shared_by_every_submit
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_production_publisher_resolves_one_client_and_keeps_the_timeout_clamp
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_retry_and_backoff_never_start_without_remaining_budget
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_slow_persistent_writer_cannot_exceed_the_materialize_deadline
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_stalled_request_cannot_consume_the_complete_pass_budget
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_two_minute_passes_satisfy_the_runtime_verifier
AC-017 -> tests/test_opip_r4_b2_shadow_cadence.py::test_ac_020_zero_materialization_emits_a_durable_disposition_marker
AC-017 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_point_in_time_provenance_round_trips_epoch_invariance
AC-017 -> tests/test_opip_r4_b2_feasibility_short.py::test_ac_022_short_margin_venue_provenance_survives_the_durable_audit
AC-017 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_016_configured_budget_is_read_from_settings
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_batch_restores_multiple_instruments_in_one_scan
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_preserves_canonical_validation_precedence
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_batch_preserves_feature_version_semantics
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_batch_malformed_evidence_matches_single_loader
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_batch_deadline_returns_no_partial_result
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_restores_multiple_instruments_in_one_scan
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_decodes_each_row_once
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_requires_interval_mapping
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_requires_since_mapping
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_checkpoint_batch_deadline_during_post_scan_selection
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_preserves_per_instrument_semantics
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_preserves_revision_ordering
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_conflicting_duplicate_fails_closed
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_malformed_evidence_matches_single_loader
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_revision_ledger_batch_deadline_returns_no_partial_map
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_production_batch_restore_calls_each_loader_once
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_production_batch_restore_outputs_are_correct
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_historical_single_instrument_callbacks_are_preserved
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_historical_callbacks_receive_deadline_kwargs_when_supplied
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_observer_receives_bounded_timing_attribution
AC-018 -> tests/test_opip_feature_bus_continuity_batch.py::test_ac_018_observer_attributes_failed_restore_phase
AC-018 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_018_default_capture_composes_batch_restore_observer_without_marker_collision
AC-019 -> tests/test_opip_feature_bus_pr3_integrity.py::test_source_cold_start_horizon_bounds_request_and_admitted_history
AC-019 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_019_default_capture_uses_exact_feature_warmup_horizon
AC-020 -> tests/test_opip_feature_bus_pr3_integrity.py::test_source_stale_watermark_is_bounded_restart_warmup
AC-020 -> tests/test_opip_feature_bus_pr3_integrity.py::test_source_stale_boundary_equality_remains_a_normal_resume
AC-020 -> tests/test_opip_feature_bus_pr3_integrity.py::test_source_recent_watermark_resume_semantics_are_unchanged
AC-020 -> tests/test_opip_feature_bus_pr3_integrity.py::test_stale_restored_checkpoint_records_a_gap_restart_not_a_cold_start
AC-020 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_020_default_composition_bounds_stale_restored_watermark
AC-020 -> tests/test_opip_r4_b2_shadow_capture.py::test_ac_020_bounded_restart_stays_admissible_after_setup_delay
AC-022 -> tests/test_opip_f11_precutover_protection.py::test_report_exposes_incomplete_resolution_reason_without_changing_gate
AC-022 -> tests/test_release_runtime_verifier.py::test_posture_failure_receipt_is_actionable
AC-022 -> tests/test_release_runtime_verifier.py::test_non_protection_posture_failure_does_not_emit_protection_reason
AC-023 -> tests/test_kraken_balance_identity_v1.py::test_ac_023_documented_balance_identities_map_to_underlying
AC-023 -> tests/test_kraken_balance_identity_v1.py::test_ac_023_decorated_balance_prices_on_the_underlying_usd_pair
AC-023 -> tests/test_kraken_balance_identity_v1.py::test_ac_023_unknown_decoration_stays_unpriced_and_coverage_incomplete

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_release_profiles.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/release/README.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-002 -> OHM-Trade-Agent-v1/docs/release/README.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/CURRENT_ARCHITECTURE_STATUS.md
AC-003 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_CONFORMANCE_LEDGER.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-BRIDGE-v1.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_local_agent_bridge.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_atdd_scope_control.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f3_ignition_detector.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-005 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-006 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_current_runtime_posture.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_shadow_activation_v1.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_lifecycle.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f4_opportunity_persistence.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f5_feasibility.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f6_forecast_engine.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r3_f7_economic_portfolio_selector.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_r2_shadow_parity.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_composition.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b0_spine_contract_closure.py
AC-006 -> .github/copilot-instructions.md
AC-006 -> AGENTS.md
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-006 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-007 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py
AC-007 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-007 -> OHM-Trade-Agent-v1/deploy/remote/diagnose-opip-learning.sh
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_opip_unified_cycle_diagnostics_v1.py
AC-007 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-007 -> OHM-Trade-Agent-v1/docs/release/README.md
AC-007 -> OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md
AC-008 -> .github/workflows/pytest.yml
AC-008 -> OHM-Trade-Agent-v1/app/services/release_profiles.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-009 -> .github/workflows/deploy-production.yml
AC-009 -> .github/workflows/pytest.yml
AC-009 -> OHM-Trade-Agent-v1/.dockerignore
AC-009 -> OHM-Trade-Agent-v1/tests/test_opip_release_pipeline_v1.py
AC-010 -> OHM-Trade-Agent-v1/app/services/release_runtime_verifier.py
AC-010 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-010 -> OHM-Trade-Agent-v1/app/jobs/run_cycle.py
AC-010 -> OHM-Trade-Agent-v1/Dockerfile
AC-010 -> OHM-Trade-Agent-v1/tests/test_release_runtime_verifier.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_deployment_transaction_boundary_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_ml_scheduler_isolation_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_streaming_safety_v1.py
AC-010 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_writer_pr2.py
AC-011 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-011 -> .github/workflows/deploy-production.yml
AC-012 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-012 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-012 -> OHM-Trade-Agent-v1/tests/test_release_scheduler_lock_verifier.py
AC-012 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-013 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-013 -> OHM-Trade-Agent-v1/tests/test_opip_deploy_verify_budget_v1.py
AC-013 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-014 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-014 -> OHM-Trade-Agent-v1/tests/test_opip_deploy_controller_bootstrap.py
AC-014 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-015 -> OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py
AC-015 -> OHM-Trade-Agent-v1/app/services/release_runtime_verifier.py
AC-015 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-015 -> OHM-Trade-Agent-v1/tests/test_opip_canonical_single_writer_feasibility_v1.py
AC-015 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-016 -> .aiderignore
AC-016 -> .vscode/settings.json
AC-016 -> .vscode/tasks.json
AC-016 -> OHM-Trade-Agent-v1/requirements-dev.txt
AC-016 -> OHM-Trade-Agent-v1/tests/test_opip_local_engineering_bootstrap_v1.py
AC-016 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-017 -> OHM-Trade-Agent-v1/app/exchanges/kraken.py
AC-017 -> OHM-Trade-Agent-v1/app/jobs/capture_feasibility_evidence_shadow.py
AC-017 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-017 -> OHM-Trade-Agent-v1/app/jobs/run_feature_bus_pilot.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/canonical/client.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/canonical/protocol.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/features/pipeline.py
AC-017 -> OHM-Trade-Agent-v1/app/opip/features/publisher.py
AC-017 -> OHM-Trade-Agent-v1/app/services/kraken_transport.py
AC-017 -> OHM-Trade-Agent-v1/app/services/opip_feature_bus_market_source.py
AC-017 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feasibility-evidence-capture
AC-017 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-feature-bus-capture
AC-017 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-017 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-R4-B2-controlled-paper-activation.md
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_producer.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_feasibility_short.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_cadence.py
AC-017 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-018 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-018 -> OHM-Trade-Agent-v1/app/jobs/run_feature_bus_pilot.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/features/checkpoint_store.py
AC-018 -> OHM-Trade-Agent-v1/app/opip/features/revision_ledger.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_continuity_batch.py
AC-018 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-018 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-019 -> OHM-Trade-Agent-v1/app/jobs/capture_feature_bus_shadow.py
AC-019 -> OHM-Trade-Agent-v1/app/opip/market/source.py
AC-019 -> OHM-Trade-Agent-v1/app/services/opip_feature_bus_market_source.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_pr3_integrity.py
AC-019 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-019 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-020 -> OHM-Trade-Agent-v1/app/opip/market/source.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_feature_bus_pr3_integrity.py
AC-020 -> OHM-Trade-Agent-v1/tests/test_opip_r4_b2_shadow_capture.py
AC-020 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-022 -> OHM-Trade-Agent-v1/app/jobs/report_protection_health.py
AC-022 -> OHM-Trade-Agent-v1/app/services/release_runtime_verifier.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_opip_f11_precutover_protection.py
AC-022 -> OHM-Trade-Agent-v1/tests/test_release_runtime_verifier.py
AC-022 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md
AC-023 -> OHM-Trade-Agent-v1/app/exchanges/kraken_identity.py
AC-023 -> OHM-Trade-Agent-v1/app/services/kraken_exposure_resolver.py
AC-023 -> OHM-Trade-Agent-v1/tests/test_kraken_balance_identity_v1.py
AC-023 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md

DEFERRED DISCOVERIES:
- `TARGET_PAPER` remains BLOCKED. Activating it (Paper-v2) requires the ATDD-R4-B2 AC-011 comparator evidence, F11 protection READY, legacy drain READY and explicit OWNER approval, and is a separate OWNER increment; this contract does not authorize it.
- Production runtime evidence has not been observed in this coding session. The verifier is implemented as a deployment gate, but only an owner-authorized run on production can produce its runtime receipt; a CI candidate is not runtime proof.

UNAPPROVED SCOPE CHANGES:
NONE
