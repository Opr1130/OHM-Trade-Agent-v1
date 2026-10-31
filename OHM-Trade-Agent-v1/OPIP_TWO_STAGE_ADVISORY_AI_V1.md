# O'Pip Two-Stage Advisory AI v1

Status: **DRAFT — owner review required. Not activated, not deployed, not merged.**

This document records the two-stage advisory design and the evidence that
supports it. It changes no trading authority, no release profile, no operator
mode, and no production credential.

Remediation round (review of `0a67b54f`): the Stage-2 provider call no longer
runs inside the scan/Chief path; it is a dedicated bounded worker. Evidence now
comes from candidates that passed the *final* deterministic target, economic and
portfolio/capital gates. Provider spend is reserved atomically before every
attempt and every chargeable outcome is settled. The credential can only be sent
to the explicitly approved provider origin.


* Stage 1 — **Chief Analyst** on OpenAI `gpt-6.1-sol` through the existing
  router (`app/services/opip_ai_router.py`, `app/services/chief_analyst.py`).
* Stage 2 — **Independent Research & Risk Review (IRR)** on DeepSeek
  `deepseek-flash` through a new, isolated, **shadow-only** adapter
  (`app/services/independent_research_review.py`).

Architecture references (no clause is amended by this work):

* v1.5.0 §F12 — asynchronous advisory research. "No AI vote, narrative or
  wrapper changes admission, exit, size, stops, risk or probability."
* v1.5.0 §UI-02 — "AI review state and stance remain separate optional
  metadata. Missing AI is never favorable analysis; its absence does not create
  a new inline approval or veto."
* v1.5.0 §F11 — protection is independent of discovery, horizons, planner,
  forecasts, Committee, dashboard and AI. Stage 2 cannot delay or condition an
  F11 protective action.
* Canonical-writer boundary — Stage 2 writes only its own append-only shadow
  evidence journal; it never writes canonical decisions, the Feature Bus, the
  target spine, learning evidence, paper state or any exchange path.
* `EVIDENCE_SHADOW` release profile, Paper-v2 `off`, Committee `off`,
  `NEW_ENTRY_AUTHORITY=LEGACY_ONLY`, funded authority `ABSENT`, and the
  alert-only/MONITOR posture are all preserved unchanged.

## Stage 1 — Chief Analyst on `gpt-6.1-sol`

Stage 1 uses the existing router, the existing `OPENAI_API_KEY`, and the
existing `responses.create` request shape. **No router code changed.**

### Identifiers and parameters validated against provider documentation

| Item | Verified value | Notes |
| --- | --- | --- |
| Model id | `gpt-6.1-sol` | OpenAI model catalogue ("Sol", *Near-Astra performance at lower cost*). |
| API surface | Responses API | Catalogue states models are available via the Responses API; the router already calls `responses.create`. |
| Reasoning levels | `low`, `medium`, `high`, `xhigh`, `max` | `gpt-6.1-sol` has no `none` level, so reasoning is always produced. |
| O'Pip reasoning subset | `low`, `medium`, `high` | `chief_analyst._reasoning_effort()` and the router reject anything else; premium tier defaults to `high`, standard to `medium`. |
| Max output | 128K tokens (provider) | O'Pip clamps `OPENAI_MAX_OUTPUT_TOKENS` to 256–4096, so a single review stays bounded. |
| List price | $2 / 1M input, $10 / 1M output | Reasoning tokens are billed as output tokens. |
| JSON contract | Prompt-required JSON object | `invoke_chief_review` requires non-empty text, `json.loads`, and a JSON **object**; anything else raises and the Chief stage fails closed. |

### Response and cost characteristics

* One call per review, `input=[system, user]`, `max_output_tokens` capped at
  4096, `reasoning={"effort": <low|medium|high>}`.
* Typical premium call (6 finalists, ~4,000 input / 1,200 output tokens):
  `4,000 x $2/1M + 1,200 x $10/1M = $0.008 + $0.012 = $0.020`.
* Typical standard call (~4,000 input / 600 output) ≈ $0.014.
* Daily spend stays bounded by the existing `OPENAI_DAILY_CALL_BUDGET` and
  `OPENAI_DAILY_TOKEN_BUDGET` guards in `chief_runtime_guard`, plus the
  per-call output cap.

### Failure handling (unchanged, re-proven)

Empty body, malformed JSON, or non-object JSON raises `ValueError` in the
router; `chief_analyst` converts that into a fail-closed `no_trade` review with
`chief_failure_code=CHIEF_UNAVAILABLE`. No recommendation is fabricated.

### OpenAI-only posture

DigitalOcean remains unsupported for this increment. Today the enforceable
mechanism is the **absence of a DigitalOcean credential and model id**:
`_digitalocean_target()` returns `None`, `invoke_chief_review` has exactly one
target, and an OpenAI failure or OpenAI budget block has no alternate provider
(it fails closed with `AIProviderUnavailable` / `CHIEF_BUDGET_LIMIT`).

> Recorded gap (not fixed here): `OPIP_AI_PROVIDER_ORDER` is a **priority list,
> not an allowlist** — `_provider_order()` always appends `openai` and
> `digitalocean`. Setting `OPIP_AI_PROVIDER_ORDER=openai` therefore does *not*
> remove a DigitalOcean fallback once a DigitalOcean credential exists. The
> behaviour is characterised by
> `tests/test_opip_two_stage_stage1_openai_v1.py::test_provider_order_is_a_priority_list_not_an_allowlist`.
> Changing it is a routing behaviour change and needs its own approved PR.

## Stage 2 — Independent Research & Risk Review (IRR)

### Purpose and posture

An independent model is asked to **challenge** an already finally-qualified
recommendation using an immutable copy of the same deterministic evidence. The
challenge is evidence, not a vote: it cannot promote, demote, delay, veto or
re-size anything, and it is never shown as an approval.

### Pipeline shape (why the primary paths stay independent)

| Stage | Process | I/O | Failure behaviour |
| --- | --- | --- | --- |
| Producer | the scan, after final qualification and after Telegram delivery | one bounded local append to `/app/data/irr/queue.jsonl` (no network, no credential read) | fail-soft: a contended or unavailable queue is recorded and the scan continues |
| Consumer | the isolated `opip-irr-shadow` Compose service (`python -m app.jobs.drain_irr_shadow`), one pass per scheduler entry | the only DeepSeek network call, in its own 96 MB cgroup | never raises; a global failure leaves the checkpoint unadvanced |

The worker takes neither the unified-cycle lock nor the scan lock and never
shares the core container's cgroup, so a slow, hung or OOM-ing provider cannot
delay a scan, a Chief review, a Telegram delivery or an F11 protection cycle.
`chief_analyst.review_candidates()` performs no Stage-2 work at all and adds no
Stage-2 field.

### When it runs (selective trigger)

1. `OPIP_IRR_MODE=shadow` (the only accepted non-`off` value; default is `off`).
2. The producer queues at most `OPIP_IRR_MAX_CANDIDATES` (default 2) alerts, and
   only those that carry the final qualification evidence:
   `target_quality_qualified is True`, `economic_qualified is True`, and a
   portfolio rank from the capital/portfolio gate.
3. The worker **re-checks** that qualification before spending, so a buggy or
   tampered queue record cannot buy a call, and it verifies the persisted
   evidence against the stored digest and request identity (see below).
4. The worker drains at most `OPIP_IRR_DRAIN_BATCH` (default 2, max 5) requests
   per pass.

### Pre-request evidence verification (before any spend)

For every queued row the worker recomputes `evidence_digest(evidence)` and
requires it to equal the digest the producer stored, requires
`request_id == sha256(digest|model|prompt_version)` (the expected request
identity), and requires the declared `stage`, `evidence_copy_version`,
`authority`, provider, symbol and direction to match the evidence itself. Any
mismatch returns `EVIDENCE_MISMATCH` with the failing field as `error_type`
(`DIGEST`, `REQUEST_ID`, `SYMBOL`, `DIRECTION`, `PROVIDER`, `STAGE`, `VERSION`,
`AUTHORITY`) and **no** reservation and **no** provider call. Proven by
`test_worker_refuses_queue_evidence_that_does_not_match_its_digest`.


### Immutable evidence copy

`build_evidence_copy()` projects the final qualification evidence
(target/economic/portfolio), the deterministic candidate evidence, the Chief
conclusion, the Chief route and the market regime into a new value detached from
the live objects (canonical JSON round-trip). The queue row records the SHA-256
of the canonical bytes, and the worker re-derives that digest before sending, so
the digest can always be recomputed from the persisted copy. Later mutation of
the live alert or ranking cannot change what is reviewed.

### Provider adapter (smallest isolated direct API)

* `POST https://api.deepseek.com/chat/completions` — the **only** reachable
  origin. `OPIP_IRR_BASE_URL` is validated against it: any other HTTPS host, a
  non-default port, a path, a query, a fragment or embedded userinfo is refused
  before the credential is attached, and the call path re-checks the origin.
* Documented JSON-output mode: `response_format={"type":"json_object"}` plus the
  word "json" and an example shape in the system prompt.
* `deepseek-flash` thinking mode is on by default; O'Pip sends
  `reasoning_effort` from `low|high|max` as documented.
* `max_tokens` from `OPIP_IRR_MAX_OUTPUT_TOKENS` (default 700, clamped
  128–4096), bounded `httpx` timeouts, **exactly one attempt, never retried**.
* `httpx` is already a runtime dependency: no new dependency, no new agent
  framework, no new service.

### Response schema (strict, fail-closed)

```json
{
  "symbol": "SOLUSD",
  "direction": "LONG",
  "verdict": "agree | caution | disagree | insufficient_evidence",
  "reasons": ["1-5 cited reasons, <=400 chars each"],
  "cited_evidence": ["up to 5 field observations"],
  "conditions": ["up to 5 conditions that would change the verdict"]
}
```

Rejected (recorded as `MALFORMED_RESPONSE`): unknown verdict, empty/missing
`reasons`, non-string entries, wrong symbol, wrong direction, non-object content,
blank content, non-JSON body.

The provider may not contribute authority-shaped fields (`action`, `order`,
`leverage`, `position_size`, `rank`, `confidence`, …). They are dropped before
anything is persisted and only their **names** are recorded in
`non_advisory_fields_ignored`.

### Spend accounting (atomic reservation, strict)

Two append-only journals, both read strictly:

* `/app/data/irr/spend_ledger.jsonl` — `RESERVE` and `SETTLE` records.
* `/app/data/irr/shadow_evidence.jsonl` — one audit row per disposition.

Before every attempt the worker, under one lock, reads today's committed spend,
checks the three daily budgets plus the per-call worst case, and appends a
`RESERVE` for the worst-case tokens and cost. Only then may the call happen.
Consequences:

* **Atomic**: two concurrent attempts serialize on the ledger lock, so the sum
  can never exceed the budget (`test_concurrent_attempts_cannot_overspend`).
* **Chargeable failures**: a timeout, transport error or non-200 settles at the
  reserved worst case — the provider may have billed it.
* **Billed-but-unusable answers**: a 200 whose content is malformed still carries
  usage, and the actual usage is charged
  (`test_malformed_response_with_usage_is_charged`).
* **Unavailable usage**: a valid answer with missing/invalid usage settles at the
  reserved worst case and is marked `usage_available: false`.
* **Crash or settlement failure**: an unsettled reservation stays counted at its
  worst case, which over-charges but never overspends.
* **Malformed ledger record, unreadable ledger, unwritable ledger or stalled
  lock**: the attempt is refused (`SPEND_UNVERIFIABLE`); spend fails closed.

| Guard | Default | Enforced |
| --- | --- | --- |
| `OPIP_IRR_DAILY_CALL_BUDGET` | 24 | before every attempt (reservation) |
| `OPIP_IRR_DAILY_TOKEN_BUDGET` | 60,000 | before every attempt (worst case) |
| `OPIP_IRR_DAILY_COST_BUDGET_USD` | 1.00 | before every attempt (peak prices) |
| `OPIP_IRR_MAX_CALL_COST_USD` | 0.05 | pre-flight worst case per call |

Stage 1 and Stage 2 budgets stay separate: Stage 2 never reads or writes the
OpenAI usage journal.

### Attribution and evidence journal

Each row carries `stage`, `provider=deepseek`, `model`, `prompt_version`
(content hash), `reasoning_effort`, `symbol`, `direction`, `status`,
`verdict`/`reasons` when valid, `evidence_digest`, the immutable evidence copy,
`chief_route`, `latency_ms`, `worst_case_tokens`, `worst_case_cost_usd`,
`charged_tokens`, `charged_cost_usd`, `usage_available`, `spend_settled`,
`shadow_only=true`, `advisory_only=true`, `authority_changed=false`,
`funded_trade_authority_changed=false`.

Credential-shaped keys (`api_key`, `authorization`, `secret`, `token`, …) are
stripped before the row is written; the API key never appears in a journal, an
exception message or stdout.

### Idempotence

`idempotency_key = sha256(evidence_digest | model | prompt_version)`, kept in
`/app/data/irr/idempotence.json` (bounded to 256 keys). A repeated identical
evidence copy returns `IDEMPOTENT_REPLAY` with the previous verdict and no
provider call and no charge. The queue uses a byte-offset checkpoint; a failed
checkpoint write only re-processes, and the idempotence ledger still prevents a
second paid call.

### Failure containment

| Situation | Status |
| --- | --- |
| mode unset | `DISABLED` (no key required, no call) |
| mode not `off`/`shadow` | `INVALID_MODE` |
| key missing | `MISSING_CREDENTIAL` (queue untouched) |
| model not provider-documented | `UNVERIFIED_MODEL` |
| endpoint not approved | `CONFIGURATION_ERROR` (no call) |
| evidence lacks final qualification | `NOT_QUALIFIED` (no spend) |
| evidence/digest/identity mismatch | `EVIDENCE_MISMATCH` (no spend, no call) |
| queue request expired, future-dated or undatable | `QUEUE_EXPIRED` (no spend, no call) |
| timeout / transport / non-200 | `FAILED` with `TIMEOUT` / `TRANSPORT_ERROR` / `HTTP_ERROR`, charged |
| malformed or blank answer | `FAILED` with `MALFORMED_RESPONSE`, charged |
| spend unverifiable | `SPEND_UNVERIFIABLE` (no call) |
| budget or per-call worst case | `BUDGET_EXHAUSTED` / `CALL_COST_LIMIT` (no call) |
| repeated identical evidence | `IDEMPOTENT_REPLAY` (no charge) |
| unreadable queue row | `QUEUE_RECORD_UNREADABLE` (disposition recorded) |

### Zero-authority guarantees

* Stage 2 never touches the Chief review object; the verdict lives only in the
  shadow evidence journal.
* Ranking, alert qualification, risk limits, capital, canonical decisions, paper
  state, order paths and Telegram delivery are untouched.
* The producer runs after final qualification and after delivery, is bounded to a
  single short local append, and cannot raise into the scan.
* The modules import only `httpx`, `registry_io` (and the drain job imports the
  IRR module); an AST test fails the build if a ranking, risk, execution,
  exchange, Telegram, paper, canonical, committee or learning module is imported.
* F11 protection keeps its own independent process: the worker takes no
  protection or cycle lock.

## Configuration reference

### Stage 1 (existing variables, no new names)

```text
OPENAI_API_KEY=<secret, already installed>
OPENAI_MODEL=gpt-6.1-sol              # or OPIP_AI_OPENAI_MODEL / OPIP_AI_OPENAI_PREMIUM_MODEL
OPENAI_REASONING_EFFORT=medium        # premium tier escalates to high
OPENAI_MAX_OUTPUT_TOKENS=1200         # clamped 256-4096
OPENAI_DAILY_CALL_BUDGET=<n>          # existing Stage-1 spend guard
OPENAI_DAILY_TOKEN_BUDGET=<n>         # existing Stage-1 spend guard
# Do NOT configure DIGITALOCEAN_* for this increment.
```

### Stage 2 (new variables, all default-off / bounded)

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPIP_IRR_MODE` | `off` | `off` or `shadow` only. Anything else is `INVALID_MODE`. |
| `DEEPSEEK_API_KEY` | — (secret) | Provider credential. Absent = `MISSING_CREDENTIAL`, no call. |
| `OPIP_IRR_MODEL` | `deepseek-flash` | Must be a provider-documented id. |
| `OPIP_IRR_BASE_URL` | `https://api.deepseek.com` | Only this exact origin is accepted. |
| `OPIP_IRR_TIMEOUT_SECONDS` | `20` | Clamped 1–60; single attempt. |
| `OPIP_IRR_MAX_OUTPUT_TOKENS` | `700` | Clamped 128–4096. |
| `OPIP_IRR_REASONING_EFFORT` | `high` | `low`, `high` or `max` (documented values). |
| `OPIP_IRR_MAX_CANDIDATES` | `2` | Producer cap per scan (1–3). |
| `OPIP_IRR_DRAIN_BATCH` | `2` | Requests per worker pass (1–5). |
| `OPIP_IRR_QUEUE_MAX_AGE_SECONDS` | `1800` | Refuse a queued request older than this (60–86400); prevents paid calls on stale evidence. |
| `OPIP_IRR_DAILY_CALL_BUDGET` | `24` | Reserved calls per UTC day. |
| `OPIP_IRR_DAILY_TOKEN_BUDGET` | `60000` | Reserved tokens per UTC day. |
| `OPIP_IRR_DAILY_COST_BUDGET_USD` | `1.0` | Reserved USD per UTC day (peak prices). |
| `OPIP_IRR_MAX_CALL_COST_USD` | `0.05` | Refuse a call whose worst case exceeds this. |

No variable is added to the core service's `environment` block, so a stale
`.env` cannot activate Stage 2 silently. The isolated `opip-irr-shadow` service
declares these names explicitly; `OPIP_IRR_MODE` defaults to `off` and
`OPIP_IRR_MODEL` / `OPIP_IRR_BASE_URL` are pinned literals in Compose, so even a
stale `.env` cannot change the provider or the model. `DEEPSEEK_API_KEY` reaches
only that service, never the core container.

## Key and authorization installation procedure (owner only)

Nothing below is executed by this PR. No paid API is invoked, and no credential
is created, changed or read here. The provider key and the scheduler
authorization are two independent, owner-gated acts.

1. Owner creates a DeepSeek API key in the provider console and sets a spend
   limit appropriate to the $1.00/day ceiling above.
2. Owner adds `DEEPSEEK_API_KEY=<value>` to the deployment `.env` on the trading
   host (the single source of truth). The deploy controller generates
   `.env.core` for the core container; `docker-compose.yml` interpolates only
   this one variable into the isolated `opip-irr-shadow` service, so the core
   container never receives it. `.env*` files are docker-ignored and never
   committed. Do not paste the value into shell history, a log, a ticket, a
   prompt, or any file under version control.
3. Owner deploys the exact approved `main` SHA through the existing owner-gated
   `/deploy <sha>` path (EVIDENCE_SHADOW profile). The scheduler reconciliation
   runs inside that deploy and installs the IRR entry **only** if step 4 has
   already authorized it.
4. Optional, later, separate activation of the scheduler entry: the owner
   creates the root-owned authorization marker on the trading host

   ```bash
   # owner, via the approved control plane; content is exactly one token
   install -o root -g root -m 0644 /dev/null /var/lib/ohm-deploy/irr-shadow-scheduler.enabled
   printf 'installed\n' > /var/lib/ohm-deploy/irr-shadow-scheduler.enabled
   ```

   The next owner-gated deploy then installs `/etc/cron.d/opip-irr-shadow`
   (printing `irr_shadow_scheduler=INSTALLED`). Without the marker the same
   deploy removes any stale entry (`irr_shadow_scheduler=ABSENT`).
5. `OPIP_IRR_MODE=shadow` in the owner `.env` is the final, independent switch:
   with the mode still `off`, an installed cron entry and a configured key are
   an inert no-op that spends nothing.
6. Verification without printing the secret:

   ```bash
   # presence only; never echo the value
   test -n "$DEEPSEEK_API_KEY" && echo DEEPSEEK_PRESENT
   grep -c 'DEEPSEEK_API_KEY' /app/data/irr/shadow_evidence.jsonl || true   # must be 0
   tail -n 1 /app/data/irr/shadow_evidence.jsonl    # provider=deepseek, model=deepseek-flash
   tail -n 2 /app/data/irr/spend_ledger.jsonl       # RESERVE then SETTLE
   docker stats --no-stream opip-irr-shadow         # memory well under 96 MiB
   ```

7. Rotation/removal: replace or delete the value in the owner `.env`, redeploy
   the approved SHA, and revoke the old key in the provider console. Removing
   `OPIP_IRR_MODE` (or setting it to `off`) makes both the producer and the
   worker inert without a code change; deleting the authorization marker and
   redeploying removes the scheduler entry.


## Scheduler installation and removal (authorized, not executed)

`deploy/cron.d/opip-irr-shadow` is a release-tree artifact, and
`deploy/remote/reconcile-scheduler.sh` is the approved scheduler process. That
script is an explicit allowlist (fixed artifact set, pre-install snapshot,
`trap rollback ERR`) and it now owns the IRR entry:

* **Authorization is explicit.** The entry is installed only when the root-owned
  marker `/var/lib/ohm-deploy/irr-shadow-scheduler.enabled` contains exactly
  `installed`. No marker (the default) means "not installed", and that run also
  removes any stale `/etc/cron.d/opip-irr-shadow`. Any other content, or a
  marker that is not a readable regular file, fails closed (exit 65) before any
  mutation, so a typo can neither activate nor silently drop the entry.
* **Snapshot** happens before any mutation (`irr-shadow.before` in the
  reconciliation temp directory, plus a `had_irr_shadow` flag), whether or not
  the entry is currently authorized.
* **Rollback** (`trap rollback ERR`) restores that snapshot when one existed and
  removes the entry when this run created it.
* **Removal** is the same path with the marker deleted or set to `absent`; the
  script also carries the explicit `rm -f /etc/cron.d/opip-irr-shadow`, so
  revoking authorization is a normal owner-gated deploy.
* **Post-condition.** An installed entry must reference `opip-irr-shadow` and
  `--profile irr-shadow run --rm --no-deps`, and is refused if it would
  `compose exec` inside the core container. An unauthorized run must leave no
  entry at all.

The run prints `irr_shadow_scheduler=INSTALLED|ABSENT` and
`irr_shadow_authorization=<marker path>`. Nothing is installed by this PR.

## Memory and resource isolation (resolved by design, pending live measurement)

The worker no longer shares the core container's 384 MB cgroup. It is the
isolated `opip-irr-shadow` Compose service, created per scheduler pass:

| Control | Value |
| --- | --- |
| Memory / swap cgroup | `mem_limit: 96m`, `memswap_limit: 96m` (own container) |
| Lifecycle | `profiles: [irr-shadow]`, `restart: "no"`, removed by `run --rm` |
| Filesystem | `read_only: true`, `tmpfs: /tmp`, only `./data/irr` mounted rw |
| Privileges | `cap_drop: ALL`, `no-new-privileges:true`, `pids_limit: 64` |
| OOM posture | `oom_score_adj: 300` (dies first, never the core or protection) |
| Wall clock | in-container `timeout --signal=TERM --kill-after=5s 150` |

Representative max-load probe (five queued requests, 8 KB evidence each,
`OPIP_IRR_MAX_OUTPUT_TOKENS=4096`, 0.5 s provider latency per call):

| Metric | Value |
| --- | --- |
| Peak working set | 33.5 MB |
| Python heap peak (`tracemalloc`) | ~210 KB |
| Growth from import to full batch | ~1.0 MB |
| Headroom at the 96 MB limit | ~64 MB (~3x the measured peak) |

The Windows probe understates a Linux container; a Linux CPython 3.12 + httpx
worker typically sits at 45-55 MB, which still leaves roughly 40 MB (>= 1.7x)
headroom. If the limit is ever reached, the cgroup OOM can only select this
container. The first authorized pass should confirm `docker stats` headroom; if
a future change raises the batch size or output cap, re-measure before raising
`mem_limit`.

## Tests added by this PR

```text
tests/test_opip_two_stage_stage1_openai_v1.py   6 test functions (9 cases)  Stage-1 contract
tests/test_opip_irr_shadow_v1.py               36 tests                     Stage-2 ATDD
tests/test_opip_irr_activation_v1.py            7 tests                     activation infrastructure
```

Activation-infrastructure coverage (this increment):

* `test_worker_has_its_own_bounded_memory_cgroup`,
  `test_worker_command_is_the_bounded_drain_job` — own 96 MB cgroup, profile
  gated, read-only, capability-free, OOM-first, bounded command.
* `test_worker_receives_no_funded_or_core_authority_credentials`,
  `test_worker_mounts_only_the_irr_state_subtree` — one secret, no funded /
  exchange / notification / OpenAI credential, no core authority switch, only
  `./data/irr` mounted.
* `test_cron_entry_runs_the_isolated_worker` — profile-gated one-shot container
  under flock, never `compose exec` in the core.
* `test_scheduler_authorization_is_explicit_and_absent_by_default`,
  `test_scheduler_snapshots_installs_rolls_back_and_removes` — explicit marker,
  snapshot, rollback restore/removal and default-absent posture.
* `test_irr_remains_disabled_by_default` — code, Compose and scheduler default.
* `test_expired_queue_request_is_refused_without_spend`,
  `test_queue_request_without_a_usable_timestamp_fails_closed` — stale or
  undatable evidence never reaches a reservation.

Earlier rounds (retained): hung provider, evidence identity, qualification
gate, reservation ledger, concurrency, chargeable failures, approved endpoint,
credential hygiene and the AST import boundary.

Focus of the remediation round (one test per corrected finding, plus the two
mandated proofs):

* `test_chief_review_performs_no_provider_io`,
  `test_hung_provider_never_delays_the_primary_pipeline` (slow/hung provider).
* `test_producer_enqueues_only_finally_qualified_survivors`,
  `test_worker_refuses_evidence_without_final_qualification`,
  `test_worker_refuses_queue_evidence_that_does_not_match_its_digest`,
  `test_evidence_copy_is_immutable_and_digest_verifiable`.
* `test_reservation_is_written_before_the_provider_call`,
  `test_concurrent_attempts_cannot_overspend`,
  `test_failed_call_is_chargeable`,
  `test_malformed_response_with_usage_is_charged` (malformed + billed usage),
  `test_failure_without_usage_charges_the_reserved_worst_case`,
  `test_malformed_spend_ledger_fails_closed`,
  `test_spend_persistence_failure_fails_closed`,
  `test_daily_cost_budget_blocks_before_spending`,
  `test_repeat_evidence_is_not_charged_twice`.
* `test_only_the_approved_provider_endpoint_is_accepted`,
  `test_approved_origin_is_normalised`,
  `test_unapproved_endpoint_makes_no_call`,
  `test_invoke_refuses_a_rogue_config`.
* Retained: inert default, missing credential, non-advisory field dropping,
  journal credential hygiene, AST import boundary.

## Rollback

1. Set `OPIP_IRR_MODE=off` (or remove `DEEPSEEK_API_KEY`) in the owner `.env`
   and deploy the approved exact SHA. The producer stops queueing and the worker
   exits `DISABLED` / `MISSING_CREDENTIAL` with no provider call.
2. Remove the scheduler entry by deleting the authorization marker
   `/var/lib/ohm-deploy/irr-shadow-scheduler.enabled` (or writing `absent`) and
   redeploying the approved SHA. The reconciliation then removes
   `/etc/cron.d/opip-irr-shadow` itself; manual `rm -f` of that file is safe but
   the marker path is the governed one. Any in-flight worker container exits on
   its own bounded timeout and is removed by `run --rm`.
3. Stage 1 is config-only: restoring the previous `OPENAI_MODEL` value returns
   the prior Chief behaviour; no code revert is required.
4. The queue, spend ledger, evidence journal and idempotence ledger are durable
   evidence. Do not truncate, rewrite or delete them; they record advisory
   attempts and reservations only and carry no authority.

## Remaining blockers and open risks

1. **Owner activation is not approved.** `OPIP_IRR_MODE` is unset everywhere, no
   DeepSeek credential exists in this repository or its CI, no authorization
   marker exists on the host, and no scheduler entry is installed.
2. **Live provider compatibility is unverified.** No paid API call was made, so
   the documented request shape is validated against provider documentation and
   injected-transport tests only. The first live shadow call is the acceptance
   observation.
3. **`OPIP_AI_PROVIDER_ORDER` is not an allowlist.** OpenAI-only depends on the
   absence of DigitalOcean credentials. Making the order strict is a separate
   routing change that requires its own approved PR.
4. **`gpt-6.1-sol` is not the code default.** `Settings.openai_model` remains
   `gpt-5.6`; switching is an owner configuration change.
5. **Container memory headroom is measured, not yet observed in production.**
   The 96 MB limit carries ~3x headroom against the representative probe and the
   worker can only OOM its own container, but the first authorized pass should
   confirm `docker stats` before the batch or output caps are ever raised.
6. **Queue retention.** The IRR queue is append-only and bounded per scan; it is
   not compacted. Compaction/archival is a separate retention decision.
7. **No operator surface yet.** The verdict is available in the evidence journal
   but is deliberately not shown in Telegram or the dashboard (v1.5.0 UI-02
   optional metadata).
8. **Cost estimates use peak prices.** Off-peak is about half, so the estimate is
   conservative, not exact.

## Explicitly out of scope

* No change to risk sizing, minimum R:R, position verification, deterministic
  gates, alert qualification, ranking, or Telegram delivery.
* No Committee, learning, Paper-v2, canonical-writer, target-spine or Feature Bus
  change; none is activated or consumed by Stage 2.
* No exchange, Kraken, order, position or funded-credential access. No new
  dependency, service, scheduler, database, droplet or agent framework.
* No scanner acquisition, promotion or reliability change.
* No release-profile change, no deployment, no merge, no operator-mode change.






