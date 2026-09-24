# O'Pip Intelligence Committee — isolated shadow worker (IC-042)

> **OFF DEPLOYMENT PROVEN; SHADOW NOT ACTIVATED.**
>
> The isolated worker artifacts have been installed and independently proven in OFF
> mode. The timer remains disabled/inactive, egress remains deny-all, and no provider
> credential or provider call is active. OFF -> credentialled SHADOW remains a separate
> OWNER-authorised boundary.

## Placement: the learning/analytics plane, not the trading droplet

Option 1 was approved. The worker runs on the **existing learning/analytics plane**,
which already carries the evidence replica and the isolation boundary. It is
deliberately **not** on the trading host, so the committee never shares a host with
order authority and the trading droplet stays free of the analytics footprint.

Rationale for Option 1 over the alternative: it needs no new boundary. The evidence
replica and the plane separation already exist, so this change adds a worker rather
than an architecture.

## The exact change

| File | What it is |
| --- | --- |
| `opip-committee-shadow.service` | The worker unit. One bounded cycle per start; `OPIP_COMMITTEE_MODE=off`; hardened and resource-capped. |
| `opip-committee-shadow.timer` | A six-hourly cycle, UTC. **Disabled by default.** |
| `run-committee-shadow-cycle.sh` | The worker entry point. Runs one cycle and exits; refuses without an exact release SHA; records an explicit `SKIPPED_MODE_OFF` disposition rather than exiting silently. |
| `app/opip/committee/cycle_runner.py` | What the script invokes: reads committed evidence, runs exactly one scheduling cycle, writes dispositions and a trust report. |

The OFF installation path is:

```bash
install -m 0644 opip-committee-shadow.service /etc/systemd/system/
install -m 0644 opip-committee-shadow.timer   /etc/systemd/system/
install -m 0755 run-committee-shadow-cycle.sh /usr/local/sbin/
systemctl daemon-reload
# NOT run as part of preparing this change:
#   systemctl enable --now opip-committee-shadow.timer
```

## Requirement-by-requirement

| Requirement | How it is satisfied |
| --- | --- |
| Read-only evidence input | `ReadOnlyPaths=/var/lib/opip-learning`; SHADOW case production resolves the verified canonical-replica generation there, requires its external production-release provenance, reconstructs schema-v2 DecisionContext plus the cited canonical decision snapshot, and writes nothing to the learning tree. Canonical evidence remains read-only; the order path and trading registries are not reachable from this unit. |
| Dedicated advisory output | `ReadWritePaths=/var/lib/opip-committee` only, plus `/var/lock`. Dispositions and the trust report land in that directory and nowhere else. |
| No trading credentials | No Kraken credential, no Telegram credential, no cockpit secret, no private key is referenced. The only secret file the unit reads is `/etc/opip/committee-credentials.env`, which holds provider keys. Verify this by inspecting the deployed environment rather than trusting this table. |
| No inbound network | No port is opened or published. `RestrictAddressFamilies=AF_INET AF_INET6` permits outbound sockets only for provider HTTPS. |
| Provider-only egress | While `OPIP_COMMITTEE_MODE=off`, egress is **deny-all and fails closed**: `IPAddressDeny=any` with **no** `IPAddressAllow=` entries. No provider egress happens during the OFF installation, so the boundary is simply closed. `IPAddressAllow=` deliberately carries no host names: systemd does not turn DNS names into a reliable boundary, and provider-specific egress belongs to the later, separately OWNER-authorised `OFF -> credentialled SHADOW` activation, where it will be added with its own validation. The application-level allowlist in `transports.py` remains the primary control when SHADOW is eventually enabled. |
| CPU and memory limits | `MemoryMax=768M`, `CPUQuota=100%`, `OOMScoreAdjust=700`, `KillMode=control-group`, `TimeoutStartSec=900`. |
| One concurrent cycle initially | The service is `Type=oneshot` and does not loop; the runner performs exactly one cycle. No autoscaling, no parallel workers. |
| Exact release SHA | `OPIP_COMMITTEE_RELEASE_SHA` is required and must be a full 40-character SHA; a branch name is refused with a configuration error. The prospective path also fails closed on release drift. |
| Mode OFF by default | Both the unit environment and the module default are `off`. `run_case` refuses to execute otherwise. |
| Explicit rollback / disable | See below. |

## Economics

Approved bounds, enforced in code:

- **$0.50** maximum per complete candidate assessment.
- **$10** maximum per **UTC** calendar day, durable across restart, persisted before
  the work it authorises. The cycle's cost budget is set from the day's remaining
  allowance, so a cycle cannot spend past the day.
- A reservation whose cost cannot be bounded is **refused**, not assumed to fit.
- Settling charges the greater of the reservation and the reported cost, so an
  under-estimate cannot escape the ceiling.

## Observability

The worker writes `trust_report.json` and `cycle_dispositions.jsonl` into its advisory
directory. Reported: the ten population dispositions (every state, even at zero), the
six trust gates with explicit insufficiency reasons, the current trust stage, mean
latency with the measured/unmeasured split, and the remaining daily ceiling. Unknown
values are reported as null or `INSUFFICIENT_EVIDENCE`, never as zero.

## Rollback and disable

Two non-destructive steps, either of which stops all committee work:

1. `systemctl disable --now opip-committee-shadow.timer` — no cycle starts.
2. Set `OPIP_COMMITTEE_MODE=off` and restart — a cycle that does start performs no
   provider call and creates no case, recording `SKIPPED_MODE_OFF`.

Neither is destructive: no trading behaviour depends on this worker, and its evidence
is append-only under its own directory, so disabling it discards nothing production
relies on. A failed cycle already degrades to a recorded disposition rather than an
exception, so a broken worker is observable rather than silent.

## What this change does not do

- It does not enable the committee. Mode stays `off`.
- OFF still constructs no executor and reads no provider credential.
- The repository contains a credentialled SHADOW executor path, but it is unreachable
  unless runtime mode explicitly resolves to `shadow`. It requires both approved
  provider credentials, bounded request/cost reservations, durable provider/case
  evidence, and a verified canonical-replica source.
- SHADOW production has an explicit activation-time boundary so historical Paper-v2
  contexts are not silently backfilled into paid work.
- Provider calls use a bounded HTTPS poster that refuses redirects; the application
  adapter still enforces exact provider endpoints. Host egress remains deny-all until
  the separate activation boundary installs and proves its provider-only network policy.
- It does not grant Paper-v2, funded/live, admission, ranking, sizing, protection, or
  execution authority.

## Approval checklist

Before deploying, OWNER confirms:

1. The host is the learning/analytics plane, and no trading credential is present.
2. The resource limits are appropriate for the host.
3. The provider credentials exist at the declared path and nowhere else.
4. Mode remains `off` at install time, with activation as a separate decision.
5. The timer stays disabled and inactive at install time, with activation as a
   separate decision.
6. The rollback trigger and who invokes it.

## Deployment path

Installation goes through the owner-gated control plane, not a manual SSH path:

```text
/deploy-committee <40-char-main-sha>
```

`.github/workflows/deploy-committee.yml` mirrors `deploy-learning.yml`: owner-gated
issue-comment trigger on issue 64, `author_association == 'OWNER'`, exact 40-character
SHA, target must equal current `main`, exact-SHA `pytest.yml` must be successful, a
dedicated protected `committee-shadow` environment, and a pinned SSH identity built
from the existing learning-host connection secrets. It reuses that established host
identity deliberately and discovers no local workstation credential.

The workflow **installs and verifies only**:

- It checks out the exact approved release, uploads nothing but that release tarball,
  and invokes only the committed bootstrap from it.
- It forces `OPIP_COMMITTEE_MODE=off` and does **not** pass `--enable-timer`. The
  service and timer artifacts are installed, but the timer is left disabled and
  inactive, so no scheduled committee execution happens at all: zero provider calls and
  zero committee cases.
- It proves isolation with `verify-committee-isolation.sh`, taking the verdict from the
  machine-readable `ISOLATION_PROOF=PASS` line rather than the exit code alone.
- It publishes a receipt reporting the result, exact SHA, remote exit codes, cleanup
  result, workflow URL, and the PASS/FAIL proof lines only. It never prints environment
  contents or a secret.
- It **requires successful removal** of the temporary remote release directory. Cleanup
  is a success condition, not best effort: a proven deployment with a failed cleanup
  does not pass.
- It fails closed on a non-main target, unsuccessful exact-SHA CI, absent host secrets,
  failed install, failed isolation proof, a missing proof line, or a failed cleanup.

It does not merge, does not activate credentialled SHADOW calls, enables no provider
execution, and carries no provider API key. The `OFF -> credentialled SHADOW`
transition — provider allowlist, timer activation, and credentialled calls — requires a
separate OWNER-authorised action with its own validation.
