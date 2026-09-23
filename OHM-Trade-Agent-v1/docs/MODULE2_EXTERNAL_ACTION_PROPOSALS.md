# Module 2 — external-action proposals

> **PROPOSALS ONLY. NOTHING HERE IS ACTIVATED.**
>
> These documents specify the two remaining Module 2 requirements that cannot be
> completed by an engineering agent: real provider transports (IC-008) and an
> isolated shadow deployment (IC-042). Each is written so an OWNER can approve,
> amend, or reject a concrete plan rather than answer an open question.
>
> No credential has been created, read, or moved. No process has been deployed. No
> provider has been contacted. No trading authority exists anywhere in this plane.

---

## Proposal 1 — IC-008: real governed provider transports

### What changes

The committee plane currently reaches models only through deterministic fakes. This
proposal adds one real transport so a committee seat can obtain an actual model
opinion, under the existing governed registry. The plane stays `off` by default and
`shadow` is the only value that permits any call.

### Providers and exact models

Two entries are proposed, deliberately from different vendors so the disagreement
matrix has genuinely independent information rather than one vendor agreeing with
itself:

| Seat | Provider family | Exact model ID | Reasoning mode |
| --- | --- | --- | --- |
| Primary | `openai` | **REQUIRES OWNER CONFIRMATION** — the exact dated snapshot id, not an alias | `LOW` |
| Fallback | `anthropic` | **REQUIRES OWNER CONFIRMATION** — the exact dated snapshot id, not an alias | `LOW` |

The registry requires an exact model id rather than an alias, and refuses a response
whose served model does not match the requested entry. That is deliberate: an alias
that silently rolls forward would change what answered a role without changing the
registry, which is exactly the version drift the registry exists to prevent.

`GOOGLE_GEMINI` and `DEEPSEEK` families stay unseated in this proposal. A family with
no adapter resolves to an explicit `UNAVAILABLE` seat and is never substituted.

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

Three ceilings, all enforced by code that already exists and is tested:

| Limit | Proposed value | Enforced by |
| --- | --- | --- |
| Per-case cost ceiling | `OPIP_COMMITTEE_MAX_ESTIMATED_COST_MICROUNITS` — **OWNER VALUE** | `RoleBudget.check_cost`; a seat whose cost cannot be bounded is refused, not assumed to fit |
| Per-cycle case and cost budget | `SchedulerBudget` — **OWNER VALUE** | `CommitteeScheduler`, which counts selected-before-execution so an exhausted cycle cannot keep selecting |
| Prices | `OPIP_COMMITTEE_PRICES` — **OWNER VALUE** | `pricing.py`; an unconfigured price yields `UNKNOWN` cost, never zero, and a malformed specification raises |

`0` means "no declared ceiling" and would be a deliberate OWNER choice; it is not the
proposed default.

### Timeout and fallback limits

| Limit | Value |
| --- | --- |
| Per-attempt deadline | registry `deadline_seconds`, proposed **OWNER VALUE** |
| Per-attempt output tokens | registry `max_output_tokens`, proposed **OWNER VALUE** |
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

Until then, IC-042 stays MISSING. No process was deployed.

---

## Summary for OWNER

| Requirement | What is missing | Whose decision |
| --- | --- | --- |
| IC-008 | Real transports, exact model IDs, credentials, egress, cost limits | OWNER: architecture and secrets |
| IC-042 | A deployed isolated shadow worker | OWNER: deployment and host choice |

Every other Module 2 requirement is either GREEN with lifecycle evidence or
PARTIAL solely because it awaits real evidence, live cases, or a deployed release.
No remaining item is blocked on unfinished engineering.
