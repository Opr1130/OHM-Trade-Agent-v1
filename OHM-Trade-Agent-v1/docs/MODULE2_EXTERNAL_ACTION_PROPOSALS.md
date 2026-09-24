# Module 2 — external-action proposals

> **HISTORICAL PROPOSAL + CURRENT BOUNDARY STATUS.**
>
> The initial OFF worker deployment has since been OWNER-approved, installed, and
> proven isolated. It remains OFF: timer disabled/inactive, deny-all egress, no
> provider credential installed for Committee execution, and no provider call.
>
> The repository now contains the separately gated SHADOW runtime bridge: verified
> canonical-replica case production, bounded provider transports/executor, durable
> accounting, and an explicit no-backfill boundary. That code is still not a live
> activation. Provider credentials, provider egress, SHADOW mode, and recurring timer
> activation remain a separate OWNER-authorised action.

---

## Proposal 1 — IC-008: real governed provider transports

### What changes

The committee plane currently reaches models only through deterministic fakes. This
proposal adds one real transport so a committee seat can obtain an actual model
opinion, under the existing governed registry. The plane stays `off` by default and
`shadow` is the only value that permits any call.

### Providers and exact models

APPROVED by OWNER. Two entries from different vendors, so the disagreement matrix
has genuinely independent information rather than one vendor agreeing with itself:

| Seat | Provider family | Registered model id | Reasoning mode |
| --- | --- | --- | --- |
| Primary (4 roles) | `openai` | `gpt-5.6-terra` | `LOW` |
| Primary (3 roles) | `anthropic` | `claude-sonnet-5` | `LOW` |

**The requirement is a provider-defined fixed/versioned model id; rolling aliases are
prohibited.** It is deliberately *not* "must contain a date": Anthropic ships
`claude-sonnet-5` as a fixed id with no date suffix, and requiring a date would have
rejected a perfectly pinned model. What is refused is a floating alias such as
`latest`, because an alias that silently points at a newer model would change what
answered a role without changing the registry — exactly the drift the registry
exists to prevent.

This is enforced in code: `ModelIdKind.ROLLING_ALIAS` is refused at registry-entry
construction, and a response whose served model differs from the registered id is
refused by the router rather than credited to the role.

**Primary roles are distributed across both vendors.** If every role used the same
primary, the fallback would almost never answer and the committee population would
contain only one vendor's opinions — so there would be no independent vendor
evidence for the disagreement matrix to measure. The approved table alternates:

| Role | Primary | Fallback |
| --- | --- | --- |
| Regime Analyst | OpenAI | Anthropic |
| Liquidity / Structure Analyst | Anthropic | OpenAI |
| Event / Sentiment Analyst | OpenAI | Anthropic |
| Bull Advocate | Anthropic | OpenAI |
| Bear Advocate | OpenAI | Anthropic |
| Risk Critic | Anthropic | OpenAI |
| Decision Synthesizer | OpenAI | Anthropic |

Each role still has **at most one** approved fallback, from the other vendor.

`GOOGLE_GEMINI` and `DEEPSEEK` families stay unseated. A family with no adapter
resolves to an explicit `UNAVAILABLE` seat and is never substituted.

### Transport implementation

One adapter per provider family, each implementing the existing `CommitteeProvider`
interface behind the existing injected `ProviderTransport` protocol. No vendor SDK is
imported by the committee plane; the adapter is the only place a vendor is named. The
adapter performs exactly one attempt and no retry of its own, because the runtime
owns bounded, idempotent retry.

### Credentials required

| Provider | Credential | Scope |
| --- | --- | --- |
| `openai` | API key | model invocation only |
| `anthropic` | API key | model invocation only |

### Where credentials would live

**Owner decision required.** The constraint is that no credential may enter this
repository, this plane's source, an image layer, a log, a prompt, or a persisted
outbound record.

The only acceptable shape is an environment variable supplied at process start from
the existing secret-management/control-plane mechanism, read by the adapter and never
by the committee plane proper. Explicitly **not** proposed: committing a value,
baking it into an image, passing it in a request payload, or storing it beside
committee evidence.

The schema self-guard already fails closed: the three credential-shaped objects in
the Phase-A corpus are assembled at runtime from fragments precisely so no scannable
credential-shaped literal exists in source.

### Network egress required

Outbound HTTPS to the two provider API hosts only, on 443. No inbound. No other host.
Egress must be an allowlist, not a default route, so a future adapter cannot reach an
unapproved destination without a config change.

### Cost limits

APPROVED by OWNER, and enforced by code that exists and is tested:

| Limit | Approved value | Enforced by |
| --- | --- | --- |
| Per complete candidate assessment | **$0.50** = `500_000` microunits | `RoleBudget.check_cost`; a seat whose cost cannot be bounded is refused, not assumed to fit |
| UTC daily Committee ceiling | **$10** = `10_000_000` microunits | `DailyCeiling` (`daily_ceiling.py`), keyed on the **UTC** calendar day, durable across restart, and persisted before the work it authorises |
| Fallback reservation | shares the primary's reservation | a fallback receives no new budget, so failover cannot double a case's cost |
| Prices | `OPIP_COMMITTEE_PRICES` — **OWNER VALUE still required** | `pricing.py`; an unconfigured price yields `UNKNOWN` cost, never zero, and a malformed specification raises |

Two notes on the daily ceiling, because both are deliberate:

- The day boundary is **UTC**, so the ceiling cannot be stretched by a local timezone
  or by daylight saving.
- A reservation whose cost cannot be bounded is **refused** rather than admitted under
  an assumption, because a ceiling that cannot be evaluated cannot be enforced.

An under-estimate cannot escape the ceiling either: settling charges the greater of
the reservation and the reported cost, and a failure with unknown cost keeps its
reservation.

### Timeout and fallback limits

| Limit | Value |
| --- | --- |
| Reasoning effort | `LOW` (approved, pinned per registry entry) |
| Per-attempt deadline | registry `deadline_seconds`, **OWNER VALUE still required** |
| Per-attempt output tokens | registry `max_output_tokens`, **OWNER VALUE still required** |
| Attempts per role | at most **two**: one primary, one approved fallback |
| Fallback reservation | shares the primary's deadline, token, and monetary reservation; it never receives a fresh budget |

A schema-invalid answer is not retried, and a served-identity mismatch is not retried.

### Shadow-only enforcement

Unchanged and already tested: `OPIP_COMMITTEE_MODE` defaults to `off`; any value
outside `{off, shadow}` resolves to `off`; `run_case` raises unless enabled; the
scheduler cycle does not run when disabled. Enabling this transport does not enable
the plane.

### No tools, no trading credentials

The committee plane has no tool, shell, or browsing surface, and the Phase-A corpus
asserts a payload carrying action fields is refused. Models receive an allowlisted,
screened evidence view only, and the outbound screener fails closed on prohibited key
names, credential-formed values, and environment-dump fields. The trading host's
Kraken and Telegram credentials are never reachable from this plane.

### What OWNER must decide

1. Approve or reject the two providers and supply the **exact dated model IDs**.
2. Supply the cost ceilings, price book, deadline, and token limits.
3. Confirm the egress allowlist and the secret-injection mechanism.
4. Confirm that the credential never enters source, image, log, prompt, or evidence.

Until those are supplied, IC-008 stays MISSING. No default was invented here.

---

## Proposal 2 — IC-042: isolated shadow deployment

### What changes

The committee plane has never run as a process. This proposes running it as an
isolated shadow worker that reads committed evidence and writes advisory evidence,
with no path to trading authority.

### Target host and service

**OWNER decision required.** The constraint, per the shared governance contract, is
that the trading droplet stays free of analytics/database footprint and that
PostgreSQL/analytics workloads do not move onto the trading host. Two acceptable
shapes are proposed; the OWNER picks one:

1. **A separate small worker on the existing learning/analytics plane**, which already
   has the evidence replica and the isolation boundary.
2. **A separate container on the trading host with no database footprint**, reading
   the read-only evidence replica only.

Option 1 is preferred, because it needs no new boundary.

### Process and container isolation

- Runs as its own Compose service with its own container; it is not added to any
  trading service.
- No host network, no privileged mode, no host filesystem mount beyond the read-only
  evidence path and its own write-only advisory path.
- CPU and memory limits set so a hung worker cannot degrade the trading host.

### Read-only evidence source

Reads committed evidence only, through the existing evidence path, mounted read-only.
It must not write to canonical evidence, the decision-intelligence streams, the order
path, or any registry.

### No trading credentials

No Kraken credential, no Telegram credential, no cockpit secret, and no private key
is mounted into this service. This is a structural claim, and it should be verified by
inspecting the deployed service's environment rather than by trusting this document.

### Default OFF, SHADOW only

`OPIP_COMMITTEE_MODE=off` in the deployed environment. The service starts, performs no
provider call, and creates no case. Switching to `shadow` is a separate OWNER action.

### Resource limits

Proposed starting point, to be set by OWNER: a single small instance, CPU and memory
capped, one concurrent cycle, no autoscaling. The module's own concurrency is already
bounded (`MAX_ROLE_CONCURRENCY`, bounded scheduler budget), so the container ceiling is
a backstop rather than the only control.

### Network policy

- Outbound: only the provider allowlist from Proposal 1, if and when IC-008 is
  approved.
- Inbound: none.
- No access to the order path, the exchange API, or the trading host's internal
  services.

### Rollback and disable path

- The service is disabled by setting mode `off` and stopping the container; both are
  non-destructive, and no trading behaviour depends on it.
- Its evidence is append-only under its own streams, so a rollback discards nothing
  that trading relies on.
- A failed cycle already degrades to a recorded disposition rather than an exception,
  so a broken worker is observable rather than silent.

### Observability

The Trust Report and the scheduler's population tally already expose: eligible,
selected, skipped-budget, skipped-capacity, expired, invalid, failed, unavailable,
late, and completed counts; per-attempt latency and the measured/unmeasured split;
attribution counts; and the six trust gates with their insufficiency reasons. The
deployed worker would emit those as its health surface, with `INSUFFICIENT_EVIDENCE`
reported as such rather than as a zero.

### Exact release SHA binding

The service runs an exact approved SHA, not a branch. The prospective path already
fails closed on release drift and records an explicit ineligible disposition, so a
worker running a different release cannot contribute prospective evidence.

### What OWNER must decide

1. Which host option, and whether a new service is acceptable.
2. The resource limits and the compose/deploy change, reviewed as its own change.
3. Confirmation that default mode is `off` and that no trading credential is mounted.
4. The rollback trigger and who invokes it.

IC-042 OFF installation is now **DEPLOYED_AND_ISOLATION_PROVEN**. Credentialled
SHADOW activation remains deliberately separate and has not occurred.

---

## Summary for OWNER

| Requirement | Status | What remains |
| --- | --- | --- |
| IC-008 | **IMPLEMENTED_AWAITING_CREDENTIALLED_SHADOW_VALIDATION** | Approved OpenAI/Anthropic adapters, fixed model identities, price book, 45-second deadline, 1,200-token ceiling, cost accounting, bounded HTTPS transport, durable executor, and fail-closed credential handling are implemented. Real credentialled canary evidence is still intentionally absent. |
| IC-042 | **OFF_DEPLOYMENT_PROVEN / SHADOW_NOT_ACTIVATED** | The isolated worker is installed and proven OFF. The repository-side SHADOW bridge consumes only verified read-only replica evidence and remains unreachable until a separate OWNER-authorised mode/credential/egress activation. The timer stays disabled until a later recurring-work decision. |

The repository engineering needed for a bounded provider-family SHADOW canary is now
present in PR #268, subject to its exact-head tests/reviews. This does **not** mean the
seven-role `RoleRouter` research layer has been made the deployed orchestration path;
that semantic orchestration remains a separate bounded integration decision rather
than being silently implied by provider-family execution.
