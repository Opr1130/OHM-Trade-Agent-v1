# O'Pip Release Profiles

This directory defines the deterministic release-profile contract used by the O'Pip release pipeline.

## Profiles

- SAFE_BASELINE: baseline non-authoritative posture for routine code/test/docs work.
- EVIDENCE_SHADOW: authorized non-authoritative evidence activation. Exact modes are enforced.
- TARGET_PAPER: future paper transition; currently BLOCKED until the required protection and evidence gates are proven.

## Required mode contract

The release profile contract is exact and allowlisted.

- OPIP_FEATURE_BUS_MODE must resolve to off or shadow unless explicitly authorized.
- OPIP_CANONICAL_WRITER_MODE must resolve to shadow for the evidence profile.
- OPIP_TARGET_SPINE_MODE must resolve to shadow for the evidence profile.
- OPIP_PAPER_V2_MODE must remain off for EVIDENCE_SHADOW.

The architecture gate fails closed whenever a profile is missing, malformed, or inconsistent with the repository-controlled runtime posture.

## Activation authority and stale-`.env` safety

The selected release profile is the activation authority: `render_profile_environment(profile)` returns the exact fixed modes a deploy applies, and the profile-to-compose posture is asserted by `evaluate_architecture_gate`. The core-service modes are literals in `docker-compose.yml` (which override `env_file: .env`), so a stale `.env` cannot activate, widen, or silently disable capture.

## Gate and receipt

```
python -m app.services.release_profiles --profile EVIDENCE_SHADOW
```

emits a concise receipt (`ARCHITECTURE_GATE`, `PROFILE`, `NEW_ENTRY_AUTHORITY`, `FUNDED_AUTHORITY`, `PAPER_V2`, `PROTECTION`). The gate fails closed on any inconsistent posture, asserts `PAPER_V2_REMAINS_OFF`, `FUNDED_AUTHORITY_ABSENT` and `PROTECTION_INDEPENDENT`, and grants no authority.

## Rollback

Rollback is the deterministic revert to the `SAFE_BASELINE` profile (Feature Bus `off`), applied as a reviewed change and deployed through the existing owner-gated `/deploy <sha>` control plane. `TARGET_PAPER` remains BLOCKED.

## Governance

See `OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-RELEASE-PIPELINE-v1.md` for the bounded OWNER governance supersession, and `OHM-Trade-Agent-v1/docs/architecture/OPIP_F6_OWNER_ENABLEMENT_PACKETS.md` for Packet A.

