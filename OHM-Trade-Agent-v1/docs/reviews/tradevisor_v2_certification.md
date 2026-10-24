# Tradevisor V2 — Independent Engineering Certification

## Owner-policy addendum — 2026-10-23 (supersedes G-02/G-03/G-04 pending status below)

The sections below document the **original pre-policy certification** at
`8100e6b5` and must not be read as a current qualification-policy claim.
Owner decisions subsequently authorized the following corrections in PR #444:

| Gate | Policy disposition | Evidence / remaining limit |
| --- | --- | --- |
| G-02 / ATDD-29 | **DECIDED:** F6 absence blocks actionable qualification; shadow evidence is permitted | Evaluator emits `WATCH` / `ADVISORY_EVIDENCE_INCOMPLETE` even after R4/F5/F7 pass. AC-015 and evaluator ATDD-29 tests prove no alert; no trusted model or deterministic actionable fallback exists |
| G-03 / ATDD-38 | **DECIDED:** retain separately classified noncanonical shadow observations and learning, with no admission or execution authority | Approved architecture exception, **not** a PASS of the canonical-writer-outage test; no canonical handoff exists |
| G-04 | **DECIDED:** completed trusted 4H regime required for actionable SWING | Native completed 1H remains the only regime input, so an otherwise qualified SWING stays `WATCH`; 4H integration is deferred |
| G-01 | **OPEN** | Genuine TradingView delivery / certificate / body-secret verification in staging not performed |
| G-05 / ATDD-43 | **OPEN / PARTIAL** | No 10x burst, latency injection or soak proof; F11 protection independence is structural only |

Hypothetical stop, target, economics and F7 allocation remain frozen as
measurement evidence (`shadow_proposed_action`) but are never actionable
recommendations; no expiry or Telegram send is attached to `WATCH`. Only
explicit `SHADOW_ONLY` hypotheses can be scored from forward candles as
`SHADOW_HYPOTHESIS` with `fill_attribution=NONE`; ordinary rejected WATCH rows
are not counted as hypothetical trades. These
changes create no trade, deployment, credential or production-alert authority.
The historical test counts below predate this addendum; current exact-head
verification must be reported separately after the new commit and CI.

**Current disposition: POLICY CORRECTION IMPLEMENTED LOCALLY; EXACT-HEAD CI AND
INDEPENDENT MERGE REVIEW PENDING — PRODUCTION NOT AUTHORIZED.**

Worktree: `feature/tradevisor-v2-ingress` @ baseline `8100e6b5`
Scope: Tradevisor V2 advisory ingress, evaluation, notification, learning.
Verdict: **DEVELOPMENT COMPLETE — PRODUCTION NOT AUTHORIZED.**

This document records what was *executed and observed*, and separates it from
what was **not** verified. No production activation is included.

## 1. Architecture change assessment

### Reused unchanged

| Concern | Owner (unchanged) |
| --- | --- |
| Entry/stop/target geometry | `app.opip.execution_geometry` via `app.services.entry_exit_advisor` |
| Target attainability (F5) | `app.services.target_attainability` |
| Economics after costs (F5) | `app.services.economic_quality_gate` |
| Capital planning (F7) | `app.services.capital_allocation` |
| Exposure capacity | `app.services.portfolio_risk` |
| Native market evidence | `app.scanner.market_scanner.analyze_symbol` |
| Executable spread/depth | `app.scanner.execution_validation.evaluate_execution` |
| Notification policy / dedup | `app.services.notification_policy` |
| Telegram transport + ledger | `app.services.telegram_delivery.send_tracked_telegram` |
| Durable registry primitives | `app.services.registry_io` |
| Bounded HTTP body read | `app.api.routes._read_bounded_body` |
| Release profiles | `app.services.release_profiles` (untouched) |
| Wave 8.2 TradingView bridge | `app/api/routes.py` (untouched) |

### Extended additively

- `app/core/config.py` — one `OPIP_TRADEVISOR_*` block + a fail-closed boot
  validator. Off by default.
- `app/main.py` — mounts one additional router.
- `app/jobs/run_cycle.py` — one phase, strictly last, inert unless enabled.
- `app/services/tradevisor_ingress.py` — `config_from_settings`,
  `signal_from_record` added to the existing validated module.

### Created

`app/models/tradevisor.py`, `app/api/tradevisor.py`,
`app/services/tradevisor_{ingress,inbox,market,evaluation,notifier,pipeline,learning}.py`,
`app/jobs/drain_tradevisor_inbox.py`, `tools/tradevisor_alert_preview.py`,
`deploy/nginx/tradevisor-webhook.conf.example`, `TRADEVISOR_V2.md`,
`docs/atdd/tradevisor_v2_atdd.md`, and seven test modules.

### Explicitly left unchanged

Order path, risk defaults, fixed sizing, minimum R:R, canonical writer,
selector, paper/Freqtrade isolation, F11 protection, learning worker SHA
contract, deploy control plane, release-profile key set, Wave 8.2 route.

## 2. Authority review

- No Tradevisor module references an order, cancel, confirm, selector or
  canonical-writer symbol (`boundaries::test_atdd35_*`).
- The webhook acknowledgement path imports no market, AI or Telegram module.
- Market evidence is public-only (`KrakenPublicTransport`); no signing keys.
- A SELL alert cannot create a short or close a position.
- "Exposure not evaluated" is recorded as `EXPOSURE_NOT_EVALUATED`, never as
  clear.
## 3. Verification (executed commands)

Environment: Windows 11, Python 3.13.15, pytest 8.4.2.
No `.env` exists in this worktree, so `WEBHOOK_SECRET` was supplied as a
process-local test value (never written to disk). Tests that shell out to
`bash` cannot run reliably here (`bash: dofork ... died unexpectedly`), which is
a host limitation, not a product defect; those files were verified to fail
identically on the pristine `8100e6b5` baseline.

| Command | Exit | Result |
| --- | --- | --- |
| `python -m compileall -q app` | 0 | clean |
| all 8 `tests/test_tradevisor_*.py` (explicit list; PowerShell does not expand the glob for pytest) | 0 | **156 passed** (pre-review state: 139) |
| `pytest tests/test_tradevisor_*.py tests/test_wave8_2_tradingview_bridge.py tests/test_tradingview_*` | 0 | **172 passed** (pre-review correction state) |
| `pytest tests/test_[t-z]*.py -q` | 0 | 656 passed |
| `pytest tests/test_[a-c]*.py -q` | 1 | 662 passed, 28 skipped, **1 pre-existing env failure** |
| `pytest tests/test_[d-l]*.py -q` | 1 | 680 passed, 93 skipped, **15 pre-existing env failures** |
| `pytest tests/test_[s]*.py -q` | 0 | 341 passed |
| `pytest tests/test_[m]*.py` (market/movement) | 0 | 58 passed |
| `pytest tests/test_maintenance_shell_exit_precedence_v1.py` | 1 | 10 failures (bash unavailable; identical on baseline) |
| curated integration set (cycle wiring, release profiles, dashboards, Settings, lifecycle webhook, notification/risk/learning contracts) | 0 | 153 passed, 2 skipped |
| per-file sweep `test_[npqr]*.py` | mixed | 38 files clean; 2 files fail only on bash-dependent tests (identical on baseline) |
| `test_o*` (195 files, ~5000 tests) | interrupted | partial: first 4% clean, stopped at the 240s wall-clock budget; not a failure |

Every observed failure was re-executed on a pristine `8100e6b5` worktree and
failed identically there. **No failure was introduced by this change.**

Full-suite single-run caveat: `pytest tests` does not complete on this host
because some shell-script tests block in `bash` fork retries. The suite was
therefore executed in bounded chunks and per-file sweeps instead of one
overlapping run.

## 4. Security and threat model

| Threat | Control | Evidence |
| --- | --- | --- |
| Anyone on the internet posts a fake alert | Trusted-edge peer check + edge stamp + per-deployment body token | `webhook::test_atdd11_*`, `test_atdd12_*` |
| Replay of a captured alert | Source-facts-only `dedup_id` (receipt time excluded) + durable row | `webhook::test_atdd23/24`, `inbox::test_repeat_receipt_*` |
| Replay with a modified timestamp | Timestamp fields are validated for alignment, freshness and future skew before dedup | `webhook::test_atdd20/21/22` |
| Payload smuggling authority | `extra="forbid"`; no risk/order/AI field is representable | `webhook::test_atdd34`, `ingress::test_atdd34` |
| Oversized body DoS | Incremental bounded read + edge `client_max_body_size` | `webhook::test_atdd18` |
| Unbounded rate | Edge IP allowlist + `limit_req` + in-process 60/min limiter | `webhook::test_rate_limit_is_bounded` |
| Secret leakage | Token popped before validation; never persisted; failure reasons are exception types only | `webhook::test_atdd12_ingest_token_is_never_persisted`, `inbox::test_repeated_failure_*` |
| Unauthenticated schema oracle | Authentication precedes every validation detail (404) | `webhook::test_atdd11_*` |
| Stale actionable advice | Expiry enforced at delivery, not only at creation | `notifier::test_atdd42_*` |
| Duplicate material alerts | Existing notification policy + cross-source native suppression | `notifier::test_atdd48_*`, `test_second_identical_*` |

Known trust limitation: TradingView offers **no custom request headers and no
credentials in the webhook URL**, so the only channel for a shared secret is the
alert message body. This implementation therefore carries `ingest_token` in the
body. TradingView's own webhook-configuration guidance explicitly advises against
placing login credentials or passwords in the webhook body; that guidance is
accepted here as a documented, consciously-taken trade-off, not silently
overridden.

### 4.1 TradingView certificate authentication — corrected

An earlier draft of this document stated that TradingView offers "no client
certificates". **That statement was wrong and is retracted.** Per TradingView,
*Webhook authentication*
(<https://www.tradingview.com/support/solutions/43000680459-webhook-authentication/>):

- the webhook server **sends an SSL certificate to the specified URL**, which the
  recipient can use to verify the request genuinely came from TradingView;
- the certificate is sent **only** when the webhook URL uses HTTPS;
- enabling and configuring verification is carried out **entirely on the
  recipient side**; nothing is enabled on the TradingView website;
- verifiable fields: `Subject: C = US, ST = Ohio, L = Westerville,
  O = TradingView, Inc., CN = webhook-server@tradingview.com` and
  `Subject Alternative Name: email:webhook-server@tradingview.com`.

**This implementation does not perform certificate verification.** The example
edge (`deploy/nginx/tradevisor-webhook.conf.example`) terminates TLS without
requesting a client certificate, and the application never receives one.
Consequences, recorded honestly:

- Authenticity today is **network + shared-secret**, and the documented
  cryptographic mechanism is **available but unused**;
- the certificate path is the correct place to strengthen provenance later, but
  it must not be claimed until it has been observed and enforced on staging;
- adopting it is an edge change plus a staging verification, not an
  application-layer change, and it does not replace the existing checks.

### 4.2 Body-token threat model

| Threat | Exposure | Control actually in place |
| --- | --- | --- |
| Anyone with TradingView account access reads the token | The token sits in clear text in the alert message, visible in the alert editor | TradingView requires 2FA for webhook alerts; the token is scoped to ingestion and carries no order, exchange or admin authority |
| Token observed on the delivery path | Body content crosses the public internet to the edge | HTTPS on port 443 only; certificate mechanism available but unverified (§4.1) |
| Token captured by proxy/APM/application logging | Body logging would leak the credential | Token popped before validation; never persisted; failure reasons record exception types only (`webhook::test_atdd12_ingest_token_is_never_persisted`) |
| Token replayed from an unauthorized origin | Stolen token plus spoofed listed IP would authenticate | Source-IP allowlist at edge **and** app; freshness window; bar-close derivation; source-facts-only `dedup_id` |
| Token committed or pasted into documents | Permanent leak | Never committed; placeholder refused at boot; supplied only through the approved secret mechanism |
| Token never rotated | Indefinite exposure window | Rotation is an owner action with no code change (settings-only value) |

**Residual risk (stated plainly):** TLS alone does not establish that the sender
is TradingView, and the four documented source IPs are shared by every
TradingView customer. An attacker holding the token and able to originate
traffic the edge accepts can submit an alert. The ceiling of such a forgery is an
**unwanted advisory notification**: it cannot place, modify, cancel or confirm an
order, cannot admit a symbol into native selection, and still passes every
deterministic risk, feasibility and economic gate.

### 4.3 Required staging verification (not performed)

1. Observe the presented client certificate on a genuine delivery and compare
   subject/SAN to the documented values.
2. Enable recipient-side verification and prove a request without a valid
   certificate is refused at the edge.
3. Confirm no proxy, ingress or APM retains the request body.
4. Confirm persisted rows and logs contain no token material.
5. Authenticity-negative: unlisted source address, and listed address with wrong
   token — both refused before any schema detail is disclosed.
6. Rotation drill: rotate token and verification value; confirm dark-then-lit
   behaviour and that no stale value is honoured.

## 5. Signal lifecycle evidence

`received → validated → durably recorded → evaluated → qualified/rejected →
notification-eligible → measured`

- Received: `webhook::test_atdd01_valid_buy_is_durably_accepted` (202 + row).
- Validated: `webhook` negative matrix (ATDD-13..25).
- Recorded: `inbox::test_durable_receipt_survives_a_crash_before_handoff`.
- Evaluated: `evaluation::test_atdd06_*` (full F5/F7/R4 chain).
- Qualified/rejected: `evaluation::test_atdd26..33`.
- Notification-eligible: `notifier::test_atdd08/09/41/42/48`.
- Measured: `learning::test_outcome_measures_forward_path_only`,
  `learning::test_cohort_summary_reports_rates_and_breakdowns`.
- Terminal failures: `inbox::test_repeated_failure_becomes_an_explicit_poisoned_disposition`.

## 6. Certification matrix

| # | Area | Outcome |
| --- | --- | --- |
| 1 | Source integration contract | PASS (synthetic) |
| 2 | Signal validation | PASS |
| 3 | Canonical evidence integrity | PASS for "no competing writer"; canonical handoff **NOT IMPLEMENTED** (G-03) |
| 4 | Native O'Pip compatibility | PASS |
| 5 | Swing/horizon classification | PASS (baseline has no general horizon classifier; documented policy) |
| 6 | Deterministic risk/economics | PASS (owners reused unchanged) |
| 7 | Advisory entry geometry | PASS (R4 kernel only) |
| 8 | Telegram notification correctness | PASS (synthetic render; no live send) |
| 9 | Outcome learning | PASS (no look-ahead, no synthetic fills) |
| 10 | Protection independence | **PARTIAL — STRUCTURAL ONLY.** Phase ordering, inert-when-disabled and failure containment are proven; overload/soak behaviour is **NOT CERTIFIED** (ATDD-43, G-05) |
| 11 | Security | PASS for the implemented controls, with two explicit limits: TradingView's documented client-certificate mechanism is **not verified** (§4.1) and the body-token model carries the documented trade-off (§4.2) |
| 12 | Performance | **NOT VERIFIED** (no latency/soak measurement) |
| 13 | Full regression | PASS for all executed chunks; single-run suite blocked by host `bash` |
| 14 | Production-release boundaries | PASS (default-off, no deploy, no secrets) |

## 7. Verdict

**DEVELOPMENT COMPLETE — PRODUCTION NOT AUTHORIZED.**

Three acceptance rows are **not passes** and must not be counted as such:

| Row | Original requirement | Reality | Open decision |
| --- | --- | --- | --- |
| ATDD-29 | Abstain when F6 evidence is unavailable | F6 is never consulted; `f6_forecast_status` is a recorded constant and evaluation proceeds | G-02 |
| ATDD-38 | Fail closed when the canonical writer is unavailable | No canonical handoff exists; the store is `ADVISORY_NON_CANONICAL` | G-03 |
| ATDD-43 | F11 protection independent under overload | Structural independence only; no overload/soak run | G-05 |

Blocking items before any production consideration: a genuine webhook delivery
(G-01), owner generation of production secrets, staging certificate/credential
verification (§4.3), latency/soak evidence (G-05), and the three **pending owner
policy decisions** (G-02, G-03, G-04).

## 8. Independent pre-PR review (second pass)

The prior report was re-verified against source. Two claims were **wrong** and
three gaps were **understated**; all are now corrected.

| ID | Severity | Finding | Status |
| --- | --- | --- | --- |
| F-1 | **P1** | `SEND_FAILED` was persisted but nothing retried it: ATDD-41 evidence only proved reservation release, not a retry path | **FIXED** — bounded retry pass in the cycle phase; retries re-evaluate rather than re-send |
| F-2 | **P1** | Persisted decision records had **no consumer**; learning evidence was orphaned (governance principle 21) | **FIXED** — `app/jobs/report_tradevisor_learning.py` consumes it under the exact-SHA gate |
| F-3 | **P1** | A retry inside the 90-minute window could have delivered a plan computed from prices up to 90 minutes stale | **FIXED** — retry candidates are excluded after expiry and always re-evaluated |
| F-4 | P2 | Missing F6/4H evidence was recorded in the decision record but never disclosed in the human-facing alert | **FIXED** — explicit `Evidence:` line |
| F-5 | P2 | `inbox_status()` did not state that the store is not the canonical ledger | **FIXED** — `evidence_class` / `canonical_handoff` / `consumption` |
| F-6 | P2 | The ack-path "no I/O" test asserted source text instead of the import graph | **FIXED** — AST allowlist naming the single reviewed coupling |
| F-7 | P3 | Suspected defect: `_active_trades()` might coerce a dict registry into constituent strings | **NOT A DEFECT** — `get_active_trades()` returns `list[ActiveTrade]` |
| F-8 | P3 | `app.api.tradevisor` imports `app.api.routes` for `_read_bounded_body` | Accepted coupling (avoids a parallel implementation); now asserted explicitly |
| F-9 | **P2** | `tradevisor_learning` **re-implemented** `classify_release_compatibility`, although `app.opip.learning.job_disposition` already owns the exact-SHA contract | **FIXED** — the owner's function object is re-exported (identity asserted), and consumption writes the learning plane's own disposition file |

### Post-correction evidence

| Command | Exit | Result |
| --- | --- | --- |
| `python -m compileall -q app tools` | 0 | clean |
| all 8 Tradevisor test files | 0 | **156 passed** |
| Wave 8.2 + Wave 9 notification + cycle wiring + release profiles + learning-disposition consumers + legacy lifecycle | 0 | **177 passed, 93 skipped** |

### Re-verified claims that held

- `secret_matches` uses `hmac.compare_digest` (constant time).
- The acknowledgement path performs no market/AI/Telegram call.
- `get_active_trades()` returns a list; exposure refusal cannot be bypassed.
- `evaluate_forecast` has **no production caller** (only its own definition and
  `__all__`), so F6 is dormant at this baseline — the Tradevisor path is
  consistent with production, not a silent relaxation.
- The Wave 8.2 route, schema, inbox and settings are untouched.

## 9. Pre-PR certification verdict

**PRE-PR REVIEW PASS — COMMIT/PR ELIGIBLE**, with these conditions attached:

1. Merge requires the supported-environment CI run (Linux) — this host cannot
   execute the `bash`-based contract tests.
2. G-01 (genuine webhook delivery) and owner secret generation remain
   outstanding; they block *activation*, not the PR.
3. G-02 (F6 blocking), G-03 (canonical promotion) and G-04 (4H regime) remain
   **pending owner policy decisions**; the current behaviour is classified,
   disclosed and consumed.
4. Staging certificate and body-token verification (§4.3) is a prerequisite for
   any production consideration and has not been performed.
5. ATDD-29, ATDD-38 and ATDD-43 are recorded as **not passed** and no artefact
   from this branch may describe them as passed.

## 10. Certification-claim corrections (pre-commit pass)

This pass changed **documentation only**. No application behaviour was altered,
and nothing was changed in order to make a statement read green.

| Claim as previously written | Correction |
| --- | --- |
| ATDD-29 "F6 evidence unavailable" = PASS | **NOT MET — DEVIATION.** The requirement asked for abstention when F6 is unavailable; the implementation never consults F6 and records `UNAVAILABLE_NO_TRUSTED_MODEL` instead. The cited test covers *execution* evidence. Behaviour is baseline-consistent (`evaluate_forecast` has no production caller) but the requirement is unmet |
| ATDD-38 "Canonical writer unavailable" = PASS | **NOT EXERCISED AS SPECIFIED — DEVIATION.** The original scenario presumes a canonical handoff. No handoff exists; evidence is `ADVISORY_NON_CANONICAL` / `NOT_PERFORMED`. Absence of a dependency is not the same as correct behaviour when it fails |
| ATDD-43 "F11 protection independent under overload" = PASS | **PARTIAL — STRUCTURAL ONLY.** Ordering, inertness and containment are proven; no overload or soak test was run, so the overload property is uncertified |
| "TradingView documents no client certificates" | **WRONG — RETRACTED.** TradingView does present an SSL client certificate on HTTPS deliveries (§4.1). This implementation does not verify it, and that gap is now stated as a security limitation and a staging prerequisite |
| "authenticity is network + shared-secret" with no further qualification | Retained as the *current* state, now accompanied by the body-token threat model (§4.2), the credential-handling limitation, and the required staging verification (§4.3) |
| G-02 / G-03 / G-04 described as criteria to satisfy | Reclassified as **PENDING OWNER POLICY DECISIONS** with explicit options for each. The owner's Git authorization for this commit and draft PR is **not** approval of any of them |

### Scope of the owner's Git authorization

The authorization covers: pre-commit verification, commit on
`feature/tradevisor-v2-ingress`, push to `origin`, and opening a **draft** pull
request. It does **not** cover merging, deployment, alert activation, credential
creation or rotation, Telegram production activation, paper or funded execution,
or architecture-policy promotion. Nothing in this document may be read as
granting any of those.





## 11. Independent review remediation (post-certification pass)

Four findings were raised against the certified head. Each was reproduced with a
failing test first, then fixed with the smallest production change. This pass
**narrows** behaviour; it adds no authority and changes no owner-approved policy.

| ID | Finding | Before | After |
| --- | --- | --- | --- |
| **P1-01** | Settlement was fenced only by `status == PROCESSING` | After a lease expiry the row returns to `PROCESSING` under a new owner, so the previous worker's status-only check still passed and it could overwrite the new owner's decision, notification state or poison disposition | Every claim mints a persisted unique `claim_token`. `_reclaim_stale` clears it on reclaim; `_settle` requires the caller's exact token and clears it on success. A late, success, failure or poison settle from a lapsed owner is a no-op. The check-and-write stays inside the existing registry lock — no new store, queue or lock service. Retry bounding and notification reservation/dedup are unchanged |
| **P1-02** | Expired `PROCESSING` rows stranded | `pending_count()` counts only `QUEUED`/`RETRY`, and reclamation runs only inside `process_queued_events()`; with an all-`PROCESSING` inbox the phase skipped the drain entirely and the row was unrecoverable without a new webhook or an operator drain | The advisory phase always attempts at least one drain pass, which reclaims expired leases before claiming. Healthy leases are not stolen, work stays bounded to four batches, protection still runs first, and failures still never abort the cycle |
| **P1-03** | Exact-SHA gate executed too late | `build_report()` invoked the candle provider and computed every outcome, then `build_learning_report()` applied the gate; cohorts were cleared but the computed outcomes were still returned and their measured count persisted | Compatibility is classified with the existing `classify_release_compatibility` **before** any measurement. Under `RELEASE_DRIFT`/`RELEASE_UNVERIFIED` no provider is called, `outcomes == {}`, the consumption disposition stays `BLOCKED_RELEASE_DRIFT`, and the measured count is zero. No second classifier was added |
| **P2-04** | Candle window admitted out-of-window prices | A candle opening exactly at `window_end` was admitted, a candle opening before but closing after it was admitted, and the still-forming Kraken candle was admitted; their highs/lows could create touches, stops and MFE/MAE outside the declared 24-hour window | A candle is admissible only if it opens before `window_end`, its full interval closes at or before `window_end` **and** at or before the measurement instant, and it is provably complete (explicit completion flag, or `open + interval`). Unprovable completeness is excluded rather than guessed. Frozen decision, geometry, and touch-versus-fill semantics are unchanged |

### Regression evidence (executed at the corrected head)

| Scenario group | Tests | Result |
| --- | --- | --- |
| P1-01 lease fencing | 3 | pass |
| P1-02 cycle recovery | 2 | pass |
| P1-03 SHA ordering | 3 | pass |
| P2-04 candle window | 5 | pass |
| All Tradevisor modules | 188 collected | **188 passed** |

Static analysis was held at the pre-change baseline: Ruff **87** errors (0 in
Tradevisor files) and Bandit **73** findings (59 low / 14 medium / 0 high, 0 in
Tradevisor files). One `B105` false positive introduced by the new
`claim_token` field name was dispositioned with the repository's documented
`# nosec B105` convention for identifier constants.

### Boundary confirmation

No trading authority was added or widened. `OPIP_TRADEVISOR_ENABLED` remains
false by default; the F6 fail-closed `WATCH` policy, the trusted-4H SWING
requirement, the G-03 noncanonical shadow exception, the R4/F5/F7 owners, the
notification policy and Telegram ledger, canonical-writer isolation, the
learning-worker exact-SHA contract, F11 protection priority, the Wave 8.2
bridge and paper/live isolation are all unchanged. Nothing was deployed or
activated.



## 12. Learning-integrity remediation — N-01 / N-02 (2026-10-24)

Two review findings about learning evidence. Each was reproduced with a failing
test first, then fixed with the smallest change that preserves the architecture.

| ID | Finding | Before | After |
| --- | --- | --- | --- |
| **N-01** (P1) | Evidence pruned before durable consumption | `_prune()` deleted any terminal row past retention, so a `PROCESSED` row could be removed before its 24-hour measurement window elapsed — and a legal 1-hour retention removed it immediately | Retention requires either a non-learning terminal state (`POISONED`) or a valid durable acknowledgement (exact `dedup_id`, allowed disposition, **definitive** measurement status, **current** release SHA). Everything else is retained; capacity enforces the existing 503 backpressure; backlog is exposed in `inbox_status()` |
| **N-02** (P2) | Partial history classified as a definitive outcome | `evaluate_outcome()` accepted a single admissible candle, so truncated history produced `MEASURED` or the definitive `NO_ENTRY_TOUCH` | Expected opens are derived from `t0`, `t1` and `Δ` on the UTC epoch grid; every expected bar must be present exactly once, aligned, complete and inside the window, else the new non-definitive `PARTIAL_COVERAGE` with bounded diagnostics. Partial coverage never reaches measured counts or profitability statistics |

### Evidence lifecycle contract

`receipt → evaluation → eligibility → measurement → consumption → safe retention`.
Only `_retention_eligible()` authorises deletion, and it can never be satisfied
by transport status alone. A release-drift or unverified acknowledgement cannot
authorise pruning; neither can a `PENDING`/`PARTIAL_COVERAGE` measurement; an
aggregate `CONSUMED_OK` report is not a per-record acknowledgement.

### Operational limitation (disclosed, not hidden)

No deployment unit invokes `report_tradevisor_learning`, and the learning worker
has no reverse-write authority into production storage (frozen boundary P), so
**no production process records an acknowledgement today**. Therefore eligible
evidence is retained indefinitely, the inbox will eventually reach capacity, and
ingress will return explicit 503 backpressure. That is the deliberate
fail-closed trade-off: it prevents silent evidence loss and does not block
trading, but the end-to-end consumption lifecycle is **not** fully operational
until an owner authorises an acknowledgement writer or durable archival handoff.

### Boundaries preserved

No trading authority, order path, risk default, sizing, minimum R:R, R4 geometry,
F5 attainability/economics, F7 capital, exposure limit, F11 priority,
notification policy, canonical writer, selector, release-profile key, paper/live
isolation or Wave 8.2 behaviour changed. `OPIP_TRADEVISOR_ENABLED` remains false
by default; F6-absent still yields shadow-only `WATCH`; SWING still requires
trusted completed 4H; G-03 remains the approved noncanonical shadow exception.
No new service, database, queue, scheduler or host was introduced.


## 13. Advisory observability and native-context increment - TV2-FR-01 (2026-10-24)

Local, unpushed work on `feature/tradevisor-advisory-observability-v1` branched
from `294d90c7` (PR #444 head). Not deployed, not activated, not merged.

### What changed

| WP | Change | ACs |
| --- | --- | --- |
| WP-01 | External-observation provenance: vendor identity, source bar, operator integration version, and a declared script version recorded as `declared_version_verified=false` / `UNVERIFIED_EXTERNAL_SCRIPT`; excluded from dedup identity | AC-016, AC-017 |
| WP-02 | Read-only native context: BTC regime via the native scanner, breadth via the native regime owner (refused below 30 snapshots), chase risk via `assess_chase_risk` with unsupplied inputs disclosed, liquidity from execution evidence, and explicit incompatibility declarations for signal-quality stage scoring and ranking; instrument-coverage diagnostics | AC-018, AC-019, AC-020 |
| WP-03 | Final-allocation economic qualification: the F5 owner re-evaluates at the F7-permitted notional; a candidate below the unchanged minimum is withheld as `ECONOMIC_REJECT_AT_FINAL_ALLOCATION` and reports no economic PASS | AC-021 |
| WP-04 | Separately classified, bounded, opt-in `TRADEVISOR OBSERVATION - WATCH / NO TRADE` digest with instruction-phrase guard, policy dedup/reservation, honest transport failure, and noncritical budget class | AC-022, AC-023, AC-024, AC-026 |
| WP-05 | Approved-format additions: evidence-as-of / bar-close / receipt / evaluation timestamps, book age, venue, F7-permitted capital basis, horizon with native ATR and explicit no-duration statement | AC-025 |

### Measured effect of WP-03 (offline fixtures, real owners)

Same plan, equity 10,000: envelope net $168.90 qualified and F7 permitted
$1,066.23, so the old display showed $90.04 against a $75 bar. Equity 8,000:
envelope net $135.12 cleared the bar while the permitted allocation implied
$72.03, which previously still qualified. It now returns
`ECONOMIC_REJECT_AT_FINAL_ALLOCATION`. No threshold was changed.

### Executed evidence

| Check | Result |
| --- | --- |
| Tradevisor suite (10 files, incl. new AC-016..026) | 236 passed, 3.15s, exit 0 |
| Adjacent regressions (`atdd_scope_control`, `operator_cycle_v2`, `r4_f8_cutover_readiness`, `signal_evidence_contracts`) | 78 passed, 48.01s, exit 0 |
| `python -m compileall -q app` | exit 0 |
| `python tests/atdd_scope.py --git-base 294d90c7` | PASS, exit 0 |
| `git diff --check` | exit 0 |
| Frozen files (learning, inbox, geometry, economics, attainability, capital, portfolio, notification policy, release profiles, freqtrade) | unchanged |

`ruff` and `bandit` are not installed in this local environment, so those
advisory CI jobs were not reproduced here; CI remains authoritative for them. Two substitutes were used instead, and neither required installing anything:

- A stdlib AST check covering the rule families CI enforces (`F401`/`F811`/`E711`/`E712`/`E303`) over every changed file. The only findings are two pre-existing `E303` blank-line runs in `tradevisor_evaluation.py`, proven present at the baseline by running the same check against `git show 294d90c7:...`, so this increment adds **zero** new lint findings. No bandit-sensitive construct (`eval`, `exec`, `subprocess`, `os.system`, `pickle`, `yaml.load`, `shell=True`) exists in the new modules.
- `pyright` (already installed as a local tool) over the new and modified modules. The two errors it found in the new code were corrected by making the narrowing explicit (`_as_dict`, `_provenance_from_record`) and by refusing an unexpected provider return object instead of treating it as market evidence. The final state is **parity with the baseline: 4 errors before, 4 after**, all pre-existing (`_resolve_snapshot` return typing and three `signal_from_record` casts); the two brand-new modules report 0 errors and 0 warnings.

### Still NOT verified

- **G-01**: no genuine TradingView to O'Pip delivery has been observed. All
evidence remains synthetic.
- **G-05**: no burst, latency injection or soak measurement. F11 independence
remains structural (phase ordering plus the noncritical budget class).
- **G-02 / G-04**: unchanged. No trusted F6 model exists and no completed-4H
provider exists, so no BUY can become actionable and every qualified SWING
stays WATCH.
- The external indicator''s internal BUY/SELL algorithm remains unverified by
design; provenance records a claim, never a certification.

### Defect found and corrected in passing

`tools/tradevisor_alert_preview.py` could not run at the previous head: it
passed a `WATCH` decision to `format_tradevisor_alert`, which correctly refuses
non-actionable decisions. It now derives a labelled synthetic actionable object
only from decisions that genuinely reached the withheld-but-substantive cohort,
and otherwise prints the real decision and rejection code.
