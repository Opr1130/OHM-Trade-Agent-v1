# O'Pip Two-Stage Advisory AI v1

Status: **DRAFT — owner review required. Not activated, not deployed, not merged.**

This document records the two-stage advisory design and the evidence that
supports it. It changes no trading authority, no release profile, no operator
mode, and no production credential.

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

An independent model is asked to **challenge** an already-qualified Chief
recommendation using an immutable copy of the same deterministic evidence. The
challenge is evidence, not a vote: it cannot promote, demote, delay, veto or
re-size anything, and it is never shown as an approval.

### When it runs (selective trigger)

All of the following must hold, otherwise nothing is sent and nothing is spent:

1. `OPIP_IRR_MODE=shadow` (the only accepted non-`off` value; default is `off`).
2. `DEEPSEEK_API_KEY` is present, `OPIP_IRR_MODEL` is a provider-documented id
   (`deepseek-flash`), and all bounds resolve.
3. The Chief review has at least one item that passes the **existing**
   deterministic recommendation gate
   (`recommendation_gate.candidate_alert_authorized`: `decision=alert`,
   `risk_level` in `low|medium`, direction `LONG|SHORT`, well-formed
   confidence). Stage 2 never widens this gate.
4. Only the top `OPIP_IRR_MAX_CANDIDATES` (default 2) qualified items are
   reviewed.
5. Stage 2 runs **after** the Chief cache write and after learning capture, so
   neither sees any Stage-2 field.

When the Chief stage itself is skipped, cache-reused or failed, no
`opip_irr_shadow` field is added at all: there is no Chief conclusion to
challenge, and the absence is recorded by the existing Chief stage evidence.

### Immutable evidence copy

`build_evidence_copy()` projects the deterministic candidate evidence, the
Chief conclusion, the Chief route and the market-regime context into a new
value detached from the live objects (canonical JSON round-trip). The SHA-256
of the canonical bytes (`evidence_digest`) is recorded with the attempt, and
the exact copy is persisted in the journal, so the digest can always be
recomputed. Later mutation of the live review or payload cannot change what was
reviewed.

### Provider adapter (smallest isolated direct API)

* `POST {OPIP_IRR_BASE_URL}/chat/completions` (`https://api.deepseek.com` by
  default), `Authorization: Bearer $DEEPSEEK_API_KEY`.
* Documented JSON-output mode: `response_format={"type":"json_object"}` plus
  the word "json" and an example shape in the system prompt.
* `deepseek-flash` thinking mode is enabled by default at effort `high`;
  O'Pip sends `reasoning_effort` from `low|high|max` as documented.
* `max_tokens` from `OPIP_IRR_MAX_OUTPUT_TOKENS` (default 700, clamped
  128–4096).
* `httpx.Timeout(OPIP_IRR_TIMEOUT_SECONDS, connect=min(5, timeout), read=…,
  write=…, pool=…)`; **exactly one attempt, never retried**.
* `httpx` is already a runtime dependency: no new dependency, no new
  architecture, no new service.

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

Rejected (recorded as `MALFORMED_RESPONSE`, no verdict stored): unknown
verdict, empty/missing `reasons`, non-string entries, wrong symbol, wrong
direction, non-object content, blank content, missing choices, non-JSON body.

The provider may not contribute authority-shaped fields (`action`, `order`,
`leverage`, `position_size`, `rank`, `confidence`, …). They are dropped before
anything is persisted and only their **names** are recorded in
`non_advisory_fields_ignored`.

### Attribution and evidence journal

`/app/data/opip_irr_shadow_evidence.jsonl` — append-only, one row per attempt:

`timestamp_utc`, `stage`, `provider=deepseek`, `model`, `prompt_version`
(content hash), `reasoning_effort`, `symbol`, `direction`, `status`,
`verdict`/`reasons` when valid, `evidence_digest`, evidence copy,
`chief_route` (Stage-1 provider/model/tier/effort), `latency_ms`,
`input_tokens`, `output_tokens`, `total_tokens`, `estimated_cost_usd`,
`shadow_only=true`, `advisory_only=true`, `authority_changed=false`,
`funded_trade_authority_changed=false`.

Credential-shaped keys (`api_key`, `authorization`, `secret`, `token`, …) are
stripped before the row is written; the API key never appears in the journal, in
an exception message, or on stdout.

### Idempotence

`idempotency_key = sha256(evidence_digest | model | prompt_version)`, kept in
`/app/data/opip_irr_idempotence.json` (bounded to 256 keys). A repeated
identical evidence copy returns `IDEMPOTENT_REPLAY` with the previous verdict
and **no** provider call and **no** cost. Changed evidence produces a new digest
and therefore a new review.

### Spending limits (strict, per provider)

Budgets are read from this provider's own journal only; the OpenAI usage journal
is never read or written by Stage 2, so the two provider budgets stay separate
and cannot mask each other.

| Guard | Default | Enforced |
| --- | --- | --- |
| `OPIP_IRR_DAILY_CALL_BUDGET` | 24 | before every call |
| `OPIP_IRR_DAILY_TOKEN_BUDGET` | 60,000 | before every call |
| `OPIP_IRR_DAILY_COST_BUDGET_USD` | 1.00 | before every call (peak-price accounting) |
| `OPIP_IRR_MAX_CALL_COST_USD` | 0.05 | pre-flight worst case per call |

Cost uses the documented **peak** `deepseek-flash` prices ($0.30 / 1M input,
$1.20 / 1M output), so budgeting is never optimistic. If the journal cannot be
read, spend cannot be proven and the call is refused (`USAGE_UNAVAILABLE`).

Estimated cost at the defaults: one qualified candidate ≈ 1,200–1,700 input
tokens plus ≤700 output tokens ≈ **$0.0013 worst case**; a two-candidate scan
≈ **$0.0026**; the hard ceiling is **$1.00/day**.

### Failure containment

Every failure mode returns metadata with `status` and `error_type`, and the
Chief review is returned unchanged:

| Situation | Status |
| --- | --- |
| mode unset | `DISABLED` (no key required, no call) |
| mode not `off`/`shadow` | `INVALID_MODE` |
| key missing | `MISSING_CREDENTIAL` |
| model not provider-documented | `UNVERIFIED_MODEL` |
| no qualified recommendation | `NO_ELIGIBLE_RECOMMENDATIONS` |
| timeout | `FAILED`, `error_type=TIMEOUT` |
| transport error | `FAILED`, `error_type=TRANSPORT_ERROR` |
| non-200 | `FAILED`, `error_type=HTTP_ERROR` (status only, never the body) |
| malformed/blank answer | `FAILED`, `error_type=MALFORMED_RESPONSE` |
| budget or per-call worst case | `BUDGET_EXHAUSTED` / `CALL_COST_LIMIT` |
| repeated identical evidence | `IDEMPOTENT_REPLAY` (no spend) |

### Zero-authority guarantees

* `opip_irr_shadow` is a separate, optional top-level key added **last**; no
  existing key of the Chief review is read for mutation or rewritten.
* Ranking, alert qualification, risk limits, capital, `recommended_action`,
  `top_candidates`, learning capture, canonical decisions, paper state, order
  paths and Telegram delivery are untouched.
* The module imports only `httpx`, `recommendation_gate` and `registry_io`; an
  AST test fails the build if a ranking, risk, execution, exchange, Telegram,
  paper, canonical, committee or learning module is ever imported.
* F11 protection keeps its own independent path: Stage 2 runs inside the
  advisory review, is bounded by a single timeout, and nothing in protection
  waits on it.

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
| `OPIP_IRR_BASE_URL` | `https://api.deepseek.com` | Must be `https://`. |
| `OPIP_IRR_TIMEOUT_SECONDS` | `20` | Clamped 1–60; single attempt. |
| `OPIP_IRR_MAX_OUTPUT_TOKENS` | `700` | Clamped 128–4096. |
| `OPIP_IRR_REASONING_EFFORT` | `high` | `low`, `high` or `max` (documented values). |
| `OPIP_IRR_MAX_CANDIDATES` | `2` | Clamped 1–3 qualified recommendations per review. |
| `OPIP_IRR_DAILY_CALL_BUDGET` | `24` | Calls per UTC day. |
| `OPIP_IRR_DAILY_TOKEN_BUDGET` | `60000` | Tokens per UTC day. |
| `OPIP_IRR_DAILY_COST_BUDGET_USD` | `1.0` | USD per UTC day at peak prices. |
| `OPIP_IRR_MAX_CALL_COST_USD` | `0.05` | Refuse a call whose worst case exceeds this. |

No variable is added to `docker-compose.yml` and no literal is added to the
`environment` block, so a stale `.env` cannot activate Stage 2 silently: the
container only sees what the deploy controller writes into `.env.core`, and
`OPIP_IRR_MODE` must be an explicit `shadow` value there.

## Key installation procedure (owner only)

Nothing below is executed by this PR. No paid API is invoked, and no credential
is created, changed or read here.

1. Owner creates a DeepSeek API key in the provider console and sets a spend
   limit appropriate to the $1.00/day ceiling above.
2. Owner adds `DEEPSEEK_API_KEY=<value>` to the deployment `.env` on the trading
   host (the single source of truth). The deploy controller generates
   `.env.core` from it; `.env*` files are docker-ignored and never committed.
   Do not paste the value into shell history, a log, a ticket, a prompt, or any
   file under version control.
3. Owner adds `OPIP_IRR_MODE=shadow` (and any optional overrides) to the same
   file.
4. Owner deploys the exact approved `main` SHA through the existing
   owner-gated `/deploy <sha>` path (EVIDENCE_SHADOW profile). This PR does not
   deploy.
5. Verification without printing the secret:

   ```bash
   # presence only; never echo the value
   test -n "$DEEPSEEK_API_KEY" && echo DEEPSEEK_PRESENT
   grep -c 'DEEPSEEK_API_KEY' /app/data/opip_irr_shadow_evidence.jsonl || true   # must be 0
   tail -n 1 /app/data/opip_irr_shadow_evidence.jsonl    # provider=deepseek, model=deepseek-flash
   ```

6. Rotation/removal: replace or delete the value in the owner `.env`, redeploy
   the approved SHA, and revoke the old key in the provider console.

## Tests added by this PR

```text
tests/test_opip_two_stage_stage1_openai_v1.py   8 tests   Stage-1 model/API contract
tests/test_opip_irr_shadow_v1.py               37 tests   Stage-2 positive + negative ATDD
```

Coverage: routing to the documented endpoint, model/parameter contract, missing
credentials, unverified model id, non-qualified items never sent, malformed and
blank answers, transport/HTTP failures, unreadable spend journal, daily
call/cost budgets, per-call cost ceiling, idempotent replay, changed-evidence
re-review, immutable evidence digest, dropped authority-shaped fields, secret
hygiene, additive-only Chief evidence, and an AST import-boundary guard.

## Rollback

1. Set `OPIP_IRR_MODE=off` (or remove `DEEPSEEK_API_KEY`) in the owner `.env`
   and deploy the approved exact SHA. Stage 2 becomes inert
   (`status=DISABLED` / `MISSING_CREDENTIAL`) with no provider call.
2. Stage 1 is config-only: restoring the previous `OPENAI_MODEL` value returns
   the prior Chief behaviour; no code revert is required.
3. The shadow evidence journal and the idempotence ledger are **durable
   evidence**. Do not truncate, rewrite or delete them; they record advisory
   attempts only and carry no authority.

## Remaining blockers and open risks

1. **Owner activation is not approved.** `OPIP_IRR_MODE` is unset everywhere and
   no DeepSeek credential exists in this repository or its CI.
2. **Live provider compatibility is unverified.** No paid API call was made, so
   the documented request shape is validated against provider documentation and
   injected-transport tests only. The first live shadow call is the acceptance
   observation.
3. **`OPIP_AI_PROVIDER_ORDER` is not an allowlist.** OpenAI-only depends on the
   absence of DigitalOcean credentials. Making the order strict is a separate
   routing change that requires its own approved PR.
4. **`gpt-6.1-sol` is not the code default.** `Settings.openai_model` remains
   `gpt-5.6`; switching to `gpt-6.1-sol` is an owner configuration change,
   documented here but not applied.
5. **Cached Chief reviews skip Stage 2.** When `CHIEF_REVIEW_CACHE_TTL_SECONDS`
   is non-zero, a cache hit returns the stored review without an IRR field; the
   idempotence ledger would prevent duplicate spend anyway.
6. **No operator surface yet.** The IRR verdict is available in the review dict
   and the journal but is deliberately not shown in Telegram or the dashboard
   (v1.5.0 §UI-02 optional metadata; a cockpit view is out of scope here).
7. **DeepSeek peak/off-peak pricing.** Cost estimates use peak rates; off-peak
   spend is about half, so estimates are conservative, not exact.

## Explicitly out of scope

* No change to risk sizing, minimum R:R, position verification, deterministic
  gates, alert qualification, ranking, or Telegram delivery.
* No Committee, learning, Paper-v2, canonical-writer, target-spine or Feature
  Bus change; none is activated or consumed by Stage 2.
* No exchange, Kraken, order, position or funded-credential access. No new
  dependency, service, scheduler, database or droplet.
* No release-profile change, no deployment, no merge, no operator-mode change.


