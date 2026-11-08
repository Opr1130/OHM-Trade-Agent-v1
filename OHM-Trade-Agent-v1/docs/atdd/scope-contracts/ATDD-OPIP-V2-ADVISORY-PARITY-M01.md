INCREMENT:
ATDD-OPIP-V2-ADVISORY-PARITY-M01

OWNER-APPROVED INTENT:
On November 8, 2026 the owner approved proceeding with V2 as the only production signal strategy, leaving V1 as passive comparison, enabling private configuration editing, and validating V2 advisory signals and Telegram in production without funded trading. Before activation, correct the confirmed isolated-scanner/core active policy asymmetry: core reads the owner-activated strategy policy but the worker currently sees only the bundled default and lacks the activation flag and active policy mount. Preserve the existing scanner isolation and leave funded execution, learning, F11, release authority and the scoring threshold untouched. This increment prepares activation and fail-closed verification; it does not independently approve unsafe activation or bypass the existing production gates.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/docker-compose.yml: core and isolated acquisition worker environmental authority, profiled worker mount and memory/credential boundaries.
- OHM-Trade-Agent-v1/app/scanner/market_scanner.py: bounded cached V2 scorer and active policy digest read.
- OHM-Trade-Agent-v1/app/core/strategy_policy.py: strict bundled and active profile parsing.
- OHM-Trade-Agent-v1/app/services/scan_result_promotion.py: the sole trusted acquisition-to-qualification seam, durable PromotionRejected reasons.
- OHM-Trade-Agent-v1/app/services/scan_promotion_runner.py: records rejected promotion without entering qualification.
- OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCANNER-V2-EVIDENCE-M01.md: documented latent advisory profile asymmetry and image evidence boundary.
- OHM-Trade-Agent-v1/tests/atdd_scope.py: enforced acceptance trace and modified-file scope.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
V2 production scoring and a profile-gated isolated scanner which inherits no shared environment file
WHEN:
the active advisory profile selector is provisioned by the owner
THEN:
the core and scanner resolve the same bounded non-secret opt-in flag, the scanner reads the active strategy-admin policy directory read-only, and its credential, writable-volume, resource, F11 and funded-trading isolation remain unchanged

AC-002:
GIVEN:
a versioned active advisory policy with a V2 threshold distinct from the bundled default
WHEN:
the isolated worker computes its cached V2 scoring profile
THEN:
the selected threshold and exact policy digest both come from that validated active policy, and V2 remains the sole production selector with V1 passive

AC-003:
GIVEN:
a V2 acquisition attempt that may have used an old or unreadable advisory policy
WHEN:
the promotion boundary receives READY V2 snapshots and evaluates profile parity
THEN:
a matching active digest passes, mismatched evidence is durably rejected as V2_SCORING_POLICY_DIGEST_MISMATCH, invalid active policy is rejected as V2_ACTIVE_POLICY_UNAVAILABLE, and V1 authority or an inactive advisory profile retains existing behaviour

EXPLICITLY OUT OF SCOPE:
- Enabling live funded trading, placing exchange orders, authorizing paper admission or bypassing F11.
- Removing V1 observational comparison, altering V2 score thresholds or manufacturing BUY alerts.
- Enabling AI, learning or Committee; changing cron cadence or scanner cgroup memory.
- Provisioning administrator secrets in GitHub, logging or committing secret values.
- Merging, deploying, or activating production without the established exact-SHA and host-gated release authorization.

FROZEN BOUNDARIES:
- No funded exchange credentials, admin secret, AI key or Telegram token enter the scanner.
- Scanner keeps its own 224m cgroup, restricted read-only root, no env_file and no authority to mutate the active strategy profile.
- The promoted V2 policy digest is checked before any qualification consumes the evidence; no fallback to V1 selection.
- No automatic order execution, learning or F11 change.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_v2_advisory_policy_parity_m01.py::test_core_and_worker_share_advisory_selector_without_credentials
AC-002 -> tests/test_opip_v2_advisory_policy_parity_m01.py::test_worker_uses_the_active_policy_threshold_and_exact_digest
AC-003 -> tests/test_opip_v2_advisory_policy_parity_m01.py::test_promotion_rejects_stale_v2_policy_as_distinct_reason
AC-003 -> tests/test_opip_v2_advisory_policy_parity_m01.py::test_invalid_active_policy_never_falls_back_silently
AC-003 -> tests/test_opip_v2_advisory_policy_parity_m01.py::test_v1_authority_and_inactive_advisory_keep_existing_behavior

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_v2_advisory_policy_parity_m01.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V2-ADVISORY-PARITY-M01.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/docker-compose.yml
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_v2_advisory_policy_parity_m01.py
AC-003 -> OHM-Trade-Agent-v1/app/services/scan_result_promotion.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_v2_advisory_policy_parity_m01.py

DEFERRED DISCOVERIES:
- The production 404 for strategy administration is expected until a distinct >=32-character admin secret is provisioned in the protected host environment and core is redeployed by the owner-gated controller; never invent or publish that credential.
- Advisory activation also requires operator-controlled server flags and a saved validated draft. This code change only closes scanner/core parity before those switches are used.
- The scanner's active policy bind is intentionally read-only and confined to the existing strategy-admin policy directory because a direct bind of active.json cannot be guaranteed to exist until the first authorized activation.
- A mid-attempt policy change can be legitimately refused at promotion; it is not evidence that the market offered no signal.
- Existing V1-only observational comparison remains available without altering the V2 production selector.

UNAPPROVED SCOPE CHANGES:
NONE
