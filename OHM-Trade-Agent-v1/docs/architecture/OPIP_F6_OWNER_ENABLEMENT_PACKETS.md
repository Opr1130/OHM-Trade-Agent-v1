# O'Pip — F6 / R4-B2 OWNER control-plane packets (PREPARED ONLY)

Status: **PREPARED ONLY — NOT EXECUTED.** Nothing in this document changes a
production mode, activates SHADOW, activates Paper-v2, or grants any authority.
These are the exact packets an OWNER executes later through the existing
control plane. Coding automation has no authority to run them.

Baseline at preparation: `origin/main = 1e3dea3f52fea9e59bf6650ed599de4185d72ef4`.
The `EXACT_SHA` for each packet is the exact approved `main` SHA at request time
and **must be re-verified immediately before the operation**; a packet is never
executed against a moving or unverified SHA.

The one control-plane mechanism is the existing owner-gated production deploy
(`.github/workflows/deploy-production.yml`, triggered by an owner `/deploy
<40-char-sha>` command on issue #64 with the exact approved `main` SHA). This
increment adds no alternate deploy path.

**Activation authority (Release Pipeline v1).** The production evidence-plane
modes are expressed as repo-controlled literals in the core service
`environment` block of `OHM-Trade-Agent-v1/docker-compose.yml`, selected by the
allowlisted release profile resolved in `OHM-Trade-Agent-v1/app/services/release_profiles.py`
(`SAFE_BASELINE`, `EVIDENCE_SHADOW`; `TARGET_PAPER` remains BLOCKED). Packet A
corresponds to the `EVIDENCE_SHADOW` profile. The `.env` file is never the
activation authority; see `ATDD-RELEASE-PIPELINE-v1`.

---

## Packet A — SHADOW evidence enablement (F6 prospective evidence clock)

**Purpose.** Start the prospective F6 evidence clock: the bounded, non-authoritative
Feature Bus SHADOW capture and the F5 feasibility-evidence SHADOW producer run so
that PIT feature snapshots and feasibility evidence accumulate from now forward.
This creates **no new-entry authority** and **no trading authority**.

**EXACT_SHA.** `<verify origin/main at request>` (baseline `1e3dea3f`).

**Exact modes to change (owner-controlled, production only).**

- `OPIP_FEATURE_BUS_MODE`: `off` → `shadow`
- `OPIP_CANONICAL_WRITER_MODE`: `off`/`shadow` → `shadow`
- `OPIP_TARGET_SPINE_MODE`: `off` → `shadow` (composes the F3-F7 target spine as a
  recorded, non-authoritative no-op until a snapshot source is supplied)
- `OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD`: fixed evidence constant `1000.0` (the
  intended paper trade size; never derived from live equity and not varied within
  the epoch). `SAFE_BASELINE` keeps it `0.0` so F5 feasibility capture is disabled.

The two existing, bounded, non-overlapping cron entries
(`deploy/cron.d/opip-feature-bus-capture` and
`deploy/cron.d/opip-feasibility-evidence-capture`) become authorised only in this
combination; neither runs inside the protected unified cycle.

**Prerequisites.** Feature Bus shadow parity accepted (R2); canonical writer
shadow healthy; the two cron entries installed by `deploy/remote/reconcile-scheduler.sh`;
`OPIP_FEATURE_BUS_CAPTURE_*` and `OPIP_FEASIBILITY_CAPTURE_*` bounds within their
minute slot.

**Preflight commands (read-only).**

```
python -m pytest -q tests/test_opip_r4_b2_shadow_cadence.py tests/test_opip_r4_b2_shadow_capture.py tests/test_opip_r4_b2_feasibility_producer.py
python -m pytest -q tests/test_opip_canonical_shadow_activation_v1.py
python tests/atdd_scope.py --increment "$(tr -d '\r\n' < docs/atdd/ACTIVE_INCREMENT)" --git-base origin/main
python -m app.jobs.report_protection_health   # protection healthy before enabling capture
```

**Rollback.** Set `OPIP_FEATURE_BUS_MODE=off` (and `OPIP_TARGET_SPINE_MODE=off`);
the cron entries record an inert/refused no-op and open nothing. No canonical-data
migration and no code revert are required.

**Post-deploy verification.** Confirm `OPIP_FEATURE_BUS_MODE=shadow` and
`OPIP_CANONICAL_WRITER_MODE=shadow` from runtime evidence (not defaults);
confirm the two cron entries produce committed `feature.snapshot.recorded` and
`feasibility.evidence.recorded` rows on the 60-second grid; confirm the protected
unified cycle is never delayed or skipped; confirm no new canonical event type and
no new authority exist.

**AUTHORITY_COLLISION proof.** SHADOW capture is read-only evidence publication:
it creates no admission, reservation, order, fill or paper execution. The legacy
admission path remains the sole new-entry authority. There is exactly one
new-entry authority before, during and after this packet — the legacy path.

**PROTECTION_HEALTH proof.** Protection (`run_cycle` → `monitor_active_trades`)
runs on its own one-minute slot and is never contended by the capture cron; the
capture entries hold their own process-level locks and never take the unified-cycle
lock.

**LEGACY_DRAIN requirements.** None for SHADOW (no authority changes).

**Current blockers.** Feature Bus is pinned `off` in `docker-compose.yml`; the
P1 evidence-ledger source feeding ML feature capture is owner-retired; this packet
requires the OWNER to enable the SHADOW modes and confirm the evidence source.

---

## Packet B — R4-B2 controlled Paper-v2 cutover (later, OWNER-gated)

**Purpose.** Make the target F7 selector the live **new-entry paper** admission
source via the Paper-v2 route, in strict paper-only isolation. This is a distinct
owner-controlled switch. This packet does **not** set it.

**EXACT_SHA.** `<verify origin/main at request>`.

**Exact mode to change.**

- `OPIP_PAPER_V2_MODE`: `off` → `active`

**Prerequisites (all objectively observed; a missing, unreadable or unproven gate
withholds activation).**

- the target F7 selector is the admission source being papered (not the legacy
  selector);
- F11 protection health proven (no silent/unmanaged holding, coverage complete,
  target protection not withholding);
- `LEGACY_DRAIN` READY: zero Freqtrade open trades and outstanding signals, zero
  Paper-v1 pending entries and open positions, zero unresolved or quarantined
  Paper-v1 lifecycle, and zero retained legacy reserved capital;
- universe metadata present; direction coverage resolved (LONG and SHORT);
- the target spine reachable; the pre-cutover matched-window comparison evidence
  (AC-011) recorded.

**Preflight commands (read-only).**

```
python -m app.jobs.report_paper_v2_cutover_readiness
python -m app.jobs.report_protection_health
python -m pytest -q tests/test_opip_r4_b2_controlled_paper_activation.py tests/test_opip_r4_f8_cutover_readiness.py
python tests/atdd_scope.py --increment "$(tr -d '\r\n' < docs/atdd/ACTIVE_INCREMENT)" --git-base origin/main
```

**Rollback (fail-closed).** Set `OPIP_PAPER_V2_MODE=off`: new target admissions
stop immediately, but legacy new-entry authority resumes **only** after the
rollback-ready gate proves the target exposure has drained (no open position, no
pending admission/order, no committed reservation, no in-progress terminal
reconciliation) or a proven shared collision mechanism makes it safe. Rollback is
this switch plus a code revert; no canonical-data migration; the former comparator
artifacts are retained.

**Post-deploy verification.** Observe runtime truth (never infer from defaults):
confirm `OPIP_PAPER_V2_MODE=active`; confirm **exactly one new-entry paper
authority**; confirm funded credentials and funded order authority are absent from
the paper path; confirm the protection sweep is healthy.

**AUTHORITY_COLLISION proof.** The legacy admission path (Top-8/profit-ranking)
and the target admission path (F7 → Paper-v2) may never create two independent
executions for one economic opportunity. F7 and Paper-v2 are one authority (source
and sink). At every instant exactly one new-entry authority exists (none, the
draining target, or legacy) — never two.

**PROTECTION_HEALTH proof.** Protection is higher authority than discovery; F11 is
not bypassed. Protection continues when discovery, forecast, Committee, dashboard
and Telegram are down. A suspended safety state is not cleared by an instantaneous
healthy result; resumption requires an explicit authorised resume gate
(`app/services/system_incidents`, `requires_owner_recovery_cycles`).

**LEGACY_DRAIN requirements.** See the prerequisites above; the drain is proven
only when every legacy obligation class is cleared **and** no legacy capital
remains reserved.

**Current blockers.** `OPIP_PAPER_V2_MODE` defaults `off`; the target F7 selector
is not yet the live admission source; the pre-cutover comparison evidence does not
yet exist; the F6 forecast artifact is not qualified (`NO_CALIBRATED_MODEL`).
`SAFETY_OR_AUTHORITY_BLOCK` until the OWNER authorises the switch.

---

## Prepared-but-not-executed statement

Both packets are prepared for the OWNER. Executing either is an OWNER
control-plane action. This document grants no funded, exchange, Committee,
dashboard or Telegram authority, and it changes no production mode.
