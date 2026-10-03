# O'Pip Release Profiles

This directory defines the deterministic release-profile contract used by the O'Pip release pipeline.

## Profiles

- SAFE_BASELINE: baseline non-authoritative rollback posture; it is not a
  production deployment selection.
- EVIDENCE_SHADOW: authorized non-authoritative evidence activation. Exact modes are enforced.
- TARGET_PAPER: future paper transition; currently BLOCKED until the required protection and evidence gates are proven.

## Required mode contract

The release profile contract is exact and allowlisted.

- OPIP_FEATURE_BUS_MODE must resolve to off or shadow unless explicitly authorized.
- OPIP_CANONICAL_WRITER_MODE must resolve to shadow for the evidence profile.
- OPIP_TARGET_SPINE_MODE must resolve to shadow for the evidence profile.
- OPIP_PAPER_V2_MODE must remain off for EVIDENCE_SHADOW.
- OPIP_COMMITTEE_MODE must remain off for every release profile.
- OPIP_FEASIBILITY_CAPTURE_NOTIONAL_USD is the fixed F5 validation notional for
  the first epoch: `1000.0` for EVIDENCE_SHADOW and `0.0` for SAFE_BASELINE
  (capture disabled). It is a repo-controlled evidence constant, never derived
  from live equity, and is not varied within an epoch; an arbitrary `--notional-usd`
  override is refused by the capture.

The architecture gate fails closed whenever a profile is missing, malformed, or inconsistent with the repository-controlled runtime posture.

## Activation authority and stale-`.env` safety

The selected release profile is the activation authority: `render_profile_environment(profile)` returns the exact fixed mode contract, and `evaluate_architecture_gate` asserts that the checked-in core-service literals match it. The core-service modes are literals in `docker-compose.yml` (which override `env_file: .env`), so a stale `.env` cannot activate, widen, or silently disable capture. The current Compose posture and only deployable profile are EVIDENCE_SHADOW. SAFE_BASELINE remains rollback-only; a profile command cannot override the SHA's configuration.

## Gate and receipt

```
python -m app.services.release_profiles --profile EVIDENCE_SHADOW
```

emits a concise receipt (`ARCHITECTURE_GATE`, `PROFILE`, `NEW_ENTRY_AUTHORITY`, `FUNDED_AUTHORITY`, `PAPER_V2`, `COMMITTEE_MODE`, `PROTECTION`). The gate fails closed on any inconsistent posture, asserts `PAPER_V2_REMAINS_OFF`, `COMMITTEE_RUNTIME_AUTHORITY_ABSENT`, `FUNDED_AUTHORITY_ABSENT` and `PROTECTION_INDEPENDENT`, and grants no authority.

The `pytest` workflow runs that gate on pull requests and pushes to `main`,
alongside pytest, ATDD scope and the blocking Bandit release-security job. A
successful main run emits a SHA-pinned `RELEASE_CANDIDATE=READY` receipt with
`DEPLOY_RESULT=NOT_REQUESTED`; candidate creation never deploys.

Production approval is recorded on issue #64 with
`/deploy-profile EVIDENCE_SHADOW <40-character-main-sha>`. The workflow accepts
only a repository-owner comment on issue #64 (or repository-owner-only manual
dispatch), rechecks current `main`, requires the
successful exact-SHA `pytest.yml` run, checks out that exact SHA, and reruns the
profile architecture evaluator against its Compose profile marker. The
backward-compatible `/deploy <sha>` spelling is subject to the same
`EVIDENCE_SHADOW` gate. `TARGET_PAPER` is rejected by the evaluator. Deployment
continues through the existing `deploy <sha>` forced command; no new SSH command
is introduced.

## Rollback and runtime verification

The host deploy verifies the built core image's revision label, active Compose
modes, unique bounded scheduler entries, and a successful unified-cycle marker.
The read-only EVIDENCE_SHADOW runtime verifier is limited to 360 seconds and
requires two fresh consecutive FeatureSnapshots on an exact 60-second grid plus
matching prospective F5 evidence, HEALTHY protection, and an inert
no-source/no-handoff target spine. It
runs before the deployment commit point; missing, stale, malformed, or late
evidence invokes the existing rollback transaction. Rollback overlays and
rechecks explicit SAFE_BASELINE modes, including feasibility-capture notional
`0.0`, before reporting success. The approval-issue receipt reports exact-SHA
CI, architecture, capture, protection, unified-cycle, scheduler, rollback, and
verification-time dispositions. A CI candidate alone remains no proof of
deployment or runtime health; production evidence is only available after the
owner-gated deployment flow runs. `TARGET_PAPER` remains BLOCKED.

## Governance

See `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md` for the bounded OWNER governance supersession, and `OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md` for Packet A.
