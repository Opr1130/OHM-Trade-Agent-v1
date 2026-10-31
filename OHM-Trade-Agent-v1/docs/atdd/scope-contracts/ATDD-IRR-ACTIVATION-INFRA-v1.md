INCREMENT:
ATDD-IRR-ACTIVATION-INFRA-v1

OWNER-APPROVED INTENT:

Prepare the merged Stage-2 DeepSeek Independent Research & Risk Review (IRR)
feature for separately approved production activation, without activating it.
The IRR worker becomes an isolated Compose service (`opip-irr-shadow`) on the
existing application image with its own 96 MB memory cgroup, its own bounded
command, no funded/exchange/notification credential, no core authority switch
and only the dedicated IRR state subtree mounted. The scheduler entry is
re-pointed from `compose exec` in the core container to that isolated worker,
and `deploy/remote/reconcile-scheduler.sh` gains explicit owner authorization
(a root-owned marker token), a pre-install snapshot, a rollback restore/removal
branch and a default-absent posture. Stale queue requests are refused before any
reservation using the immutable producer timestamp, so a backlog can never
consume paid provider credit. The spending ledger, evidence identity checks and
idempotence protections are preserved unchanged. Nothing is installed, enabled,
deployed or activated by this increment.

ARCHITECTURE REFERENCES:

- v1.5.0 F12: asynchronous advisory research; no AI vote, narrative or wrapper
  changes admission, exit, size, stops, risk or probability
- v1.5.0 F11 and ME-05: protection independence and reserved capacity; an
  advisory OOM must never select the core or protection workload
- OHM-Trade-Agent-v1/docker-compose.yml: the existing `opip-scan-worker` profile
  gated ephemeral worker (its own 224 MB cgroup, no env_file, read_only,
  cap_drop ALL) is the pattern reused here
- OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh: the approved
  scheduler reconciliation (explicit allowlist, pre-install snapshot, ERR
  rollback)
- OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md: the increment record
- OHM-Trade-Agent-v1/app/services/independent_research_review.py: the IRR queue,
  spend ledger, evidence identity and idempotence contract

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the isolated `opip-irr-shadow` Compose worker definition
WHEN:
its resource limits and command are inspected
THEN:
it has its own 96 MB memory and swap cgroup, is profile gated and ephemeral,
runs read-only with all capabilities dropped, ascends the OOM score, mounts only
/tmp as tmpfs, and runs the bounded IRR drain job under an in-container timeout
instead of executing inside the core container

AC-002:
GIVEN:
the same worker definition
WHEN:
its environment and mounts are inspected
THEN:
it declares no env_file, receives no funded, exchange, notification, OpenAI or
DigitalOcean credential and no core release-profile authority switch, carries
exactly one secret and the bounded OPIP_IRR_* controls, pins the provider origin
and model as literals, and mounts only the dedicated IRR state subtree

AC-003:
GIVEN:
the IRR scheduler entry
WHEN:
its invocation is inspected
THEN:
it runs the isolated worker through the profile-gated one-shot container under
the host flock, and it never executes inside the core container

AC-004:
GIVEN:
the approved scheduler reconciliation
WHEN:
the IRR entry is considered
THEN:
installation requires an explicit owner authorization token and fails closed on
any other marker content, the previous entry is snapshotted before mutation, the
ERR handler restores or removes it, an unauthorized run removes any stale entry,
the artifact is required only when authorized, and the entry is refused if it
would exec inside the core container

AC-005:
GIVEN:
an unconfigured deployment
WHEN:
the IRR mode, the Compose environment and the scheduler reconciliation defaults
are inspected
THEN:
`OPIP_IRR_MODE` resolves to `off` in code and in Compose, the drain spends
nothing and writes nothing, and the scheduler installs no entry without the
authorization marker

AC-006:
GIVEN:
a queued request and the bounded validity window
WHEN:
the worker evaluates a request whose immutable timestamp is older than the
window, in the future, or missing
THEN:
it records QUEUE_EXPIRED with no reservation, no provider call and no charge,
while a fresh request still proceeds through the unchanged identity,
reservation, ledger and idempotence path

EXPLICITLY OUT OF SCOPE:

- enabling IRR, installing the cron entry, deploying, or activating anything
- any change to the provider model, endpoint allowlist, prompt, verdict schema,
  budgets or the spend-ledger/idempotence semantics
- any change to risk sizing, gates, ranking, alerts, Telegram delivery, Paper-v2,
  Committee, learning, canonical writer or the release profile
- any change to the scanner worker, its 224 MB cgroup or its dispatcher
- any new dependency, service, scheduler, database or agent framework

FROZEN BOUNDARIES:

- `OPIP_IRR_MODE=off` remains the default in code, Compose and the scheduler
- `EVIDENCE_SHADOW`, `OPIP_PAPER_V2_MODE=off` and `OPIP_COMMITTEE_MODE=off`
  unchanged; no funded or exchange authority anywhere near the worker
- the core container keeps its 384 MB cgroup and its `.env.core`; the worker
  never joins it
- the spend ledger, evidence identity verification and idempotence protections
  are preserved unchanged, and no paid call is made by this increment
- F11 protection independence unchanged

ACCEPTANCE TEST TRACEABILITY:

AC-001 -> tests/test_opip_irr_activation_v1.py::test_worker_has_its_own_bounded_memory_cgroup
AC-001 -> tests/test_opip_irr_activation_v1.py::test_worker_command_is_the_bounded_drain_job
AC-002 -> tests/test_opip_irr_activation_v1.py::test_worker_receives_no_funded_or_core_authority_credentials
AC-002 -> tests/test_opip_irr_activation_v1.py::test_worker_mounts_only_the_irr_state_subtree
AC-003 -> tests/test_opip_irr_activation_v1.py::test_cron_entry_runs_the_isolated_worker
AC-004 -> tests/test_opip_irr_activation_v1.py::test_scheduler_authorization_is_explicit_and_absent_by_default
AC-004 -> tests/test_opip_irr_activation_v1.py::test_scheduler_snapshots_installs_rolls_back_and_removes
AC-005 -> tests/test_opip_irr_activation_v1.py::test_irr_remains_disabled_by_default
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_expired_queue_request_is_refused_without_spend
AC-006 -> tests/test_opip_irr_shadow_v1.py::test_queue_request_without_a_usable_timestamp_fails_closed

IMPLEMENTATION MAP:

AC-001 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-001 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-irr-shadow
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_irr_activation_v1.py
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_irr_activation_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/cron.d/opip-irr-shadow
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_irr_activation_v1.py
AC-004 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_irr_activation_v1.py
AC-005 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-005 -> OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh
AC-005 -> OHM-Trade-Agent-v1/tests/test_opip_irr_activation_v1.py
AC-006 -> OHM-Trade-Agent-v1/app/services/independent_research_review.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_opip_irr_shadow_v1.py
AC-006 -> OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-001 -> OHM-Trade-Agent-v1/OPIP_TWO_STAGE_ADVISORY_AI_V1.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-IRR-ACTIVATION-INFRA-v1.md
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:

- 96 MB was validated from a representative max-batch probe (five requests, 8 KB
  evidence each, 4096 max output): 33.5 MB peak working set, ~210 KB Python heap
  peak, ~3x headroom; a Linux container measurement under real provider latency
  remains an activation-time observation
- the worker's Linux RSS is expected to be higher than the Windows probe; the
  operator should confirm `docker stats` headroom on the first authorized pass
- queue retention/compaction is still a separate decision

UNAPPROVED SCOPE CHANGES:
NONE

