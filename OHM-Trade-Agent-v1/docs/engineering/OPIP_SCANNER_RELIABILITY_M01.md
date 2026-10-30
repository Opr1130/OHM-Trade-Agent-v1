# O'Pip — Scanner Reliability M01 (R01–R08) — implementation note

Baseline: `a25eedd9fc19fced9190921fc5d0bdf742a08433` (main, 2026-10-29)
Branch: `feature/opip-scanner-reliability-v1`
Status: draft, awaiting independent review. No deployment, activation or
production change is authorised by this document.

## 1. What changed and why

| Area | Change | Requirement |
| --- | --- | --- |
| Concurrency | `opip_scan_max_workers` (default 4, ceiling 8) replaces the hard-coded `MAX_WORKERS = 8` read path | R04 |
| Telemetry | `_BoundedCapture` replaces the unbounded `io.StringIO`; head + tail + dropped-row count within line **and** byte budgets | R04, R07 |
| JSONL reads | `iter_lines` / `read_tail_lines` added; `read_lines` is single-pass streaming; `repair_truncated_tail` seeks instead of `read_bytes()` | R10 |
| Scan outcome | `record_scan_output(outcome=…)` and `derive_scan_outcome` make COMPLETED/PARTIAL/FAILED explicit; a raised scan is recorded FAILED | R07 |
| Isolation | New profile-gated `opip-scan-worker` service with its own 224 MiB cgroup and no `env_file` | R01, R06 |
| Lifecycle | `scan_attempt` ledger: durable attempt identity, states, terminal outcomes, fencing, idempotent terminalisation | R02, R08 |
| Integrity | `scan_manifest`: two-phase write (manifest → SHA-256 sentinel) with typed failures | R06, R07 |
| Preflight | `scan_preflight`: host/cgroup/PSI/disk admission, fail closed | R03, R05 |
| Admission | `scan_supervisor`: reserve + single-use token + reconciliation gated on observed container evidence | R01, R02 |
| Worker | `app/jobs/scan_worker.py`: acquisition only, no notification/AI/paper/canonical imports | R01 |
| Cycle seam | `run_cycle` no longer executes the scan; it reserves and writes a token | R01 |
| Recovery | `recover_interrupted_search` no longer marks a live asynchronous scan FAILED | R02 |
| Scheduler | one cron entry; the dispatcher runs **after** the cycle lock is released | R01 |

## 2. Execution modes

`OPIP_SCAN_EXECUTION_MODE` is three-valued and defaults to `legacy_inprocess`,
so **merging this change is not itself a production behaviour change**.

| Mode | Behaviour |
| --- | --- |
| `legacy_inprocess` | Previous behaviour, byte-for-byte: the cycle runs the scan in-process |
| `isolated` | The cycle reserves an attempt and writes a token; the dispatcher runs the isolated worker |
| `suspended` | Discovery stops, a SKIPPED record is written, and there is **no** fallback to the in-process scanner |

## 3. Lock topology (one scheduler)

| Lock | Holder | Released |
| --- | --- | --- |
| `/var/run/ohm-unified-cycle.lock` | cron stage 1 | when the cycle command exits (seconds) |
| `/var/run/ohm-scan.lock` | dispatcher, stage 2 | when the scan container exits |
| `/app/data/.unified_cycle.lock` | cycle process | when the cycle process exits |
| `registry_lock(scan_attempts)` | core | per transaction |

Stage 2 is detached, so a scan cannot extend stage 1 and protection continues on
its normal cadence during a slow, hung or OOM-killed acquisition.

## 4. Overlap and evidence rules

* A scan is admitted only when no attempt is active — an expired deadline never
  authorises a second attempt (R02).
* Reconciliation terminalises an attempt only when the **dispatcher-observed**
  container evidence is fresh and does not list it. Missing or stale evidence
  means *unknown*, and unknown withholds (never guesses).
* `docker compose run` is used **without** `--rm` so exit code, `OOMKilled`,
  container id and `HostConfig` limits can be captured before removal.
* Ambiguous identity (orphan container, duplicate label, missing label,
  uninterruptible I/O) withholds admission rather than starting another worker.

## 5. USDT retirement readiness — source-level audit only

**Not performed: no container was stopped or removed.**

| Dependency | Finding | Action at retirement |
| --- | --- | --- |
| `docker-compose.paper.yml` | `ohm-freqtrade-paper-usdt` service, its init mounts and `pairlist_usdt.json` requirement | Remove the service block and the USDT-side init references |
| `freqtrade/user_data/config_usdt.json` | Independent `stake_currency: USDT`, `max_open_trades: 3`, own 5 000 wallet | Retain in the tree for rollback; do not delete |
| `app/services/freqtrade_result_ingest.py` | Reads **both** trade DBs (`DB_USD_FILE`, `DB_USDT_FILE`) | Narrow the live set to USD only, keep the USDT path available for history |
| `app/services/paper_trade_control.py` | Activation requires `freqtrade_dry_run_status() == OK` for **all** workers | Requires an owner decision and a behaviour test (the gate now evaluates the remaining worker set) |
| Global position cap | Enforced in `freqtrade_signal_bridge.publish_qualified_long` (`GLOBAL_POSITION_CAPACITY`) from worker `is_open == 1` rows | Unchanged; must remain the authority even though each bot also sets `max_open_trades: 3` |
| Historical preservation | `tradesv3.ohm_dry_run_usdt.sqlite` under `data/freqtrade/state` | Archive with checksum **before** any removal; never delete in place |
| Pre-retirement gate | Open trades / pending orders in the USDT DB | Must be enumerated read-only and be zero, or retirement is refused |
| Coverage | A USD-stake bot does not simulate USDT-quoted markets | Documented limitation; no instrument conversion |

Retirement ordering (when authorised): enumerative audit → archive + checksum →
stop the USDT service → verify ingest and activation gate → record rollback path.

## 6. Known gaps in this change set

1. The core does not consume the staged manifest yet. Qualification still runs
   from the in-process path; wiring the manifest into `scan_opportunities` is the
   next increment. Until then `isolated` mode produces evidence but not
   actionable candidates, so it must not be activated.
2. No Docker-level fault injection in CI (no daemon in the test environment).
   OOM/timeout/exit-state capture is covered by contract tests over the evidence
   writer, not by a real cgroup kill.
3. `opip_scan_memory_cap_mib = 224` remains provisional pending measurement.
4. Daily health audit and weekly housekeeping job level work (R09/R10) is not in
   this change set; only the JSONL read bounding is.

## 7. Rollback

Set `OPIP_SCAN_EXECUTION_MODE=legacy_inprocess` (or remove the variable) and
remove the dispatcher invocation from `deploy/cron.d/ohm-unified-cycle`. The
`opip-scan-worker` service is profile-gated, so it never starts from a plain
`docker compose up`. No data migration occurred; new artefacts are append-only
attempt records and staging manifests under `data/scan/`.

