INCREMENT:
ATDD-SCANNER-DECLARATION-PROBE-M01

OWNER-APPROVED INTENT:
The owner directs the correction of the production deployment blocker reported against the approved release f4378e49, whose deploy refused release acceptance with OPIP_SCANNER_IMAGE=UNDECLARED_FOR_V2_RELEASE while that release's own Compose document declares opip-scan-worker behind the `scanner` profile. The blocker is a defect of evidence, not of composition: discovery of the isolated acquisition worker resolved the candidate model with a single command whose exit status and stderr were discarded (`2>/dev/null`) and then reported one boolean, so "the render did not complete" and "the approved release declares no scanner" reached the receipt as the same fact and the refusal asserted something the release never said. Profile activation is additionally the one input to that render which comes from the host's effective environment rather than from the release, so a host environment document could decide the model on its own and no marker would have said so. The correction publishes the probe's own outcome (verdict class, render exit status, the resolved service list, and a bounded redacted diagnostic), states the profile activation in the command's own environment as well as by the flag, and stops the owner's `.env` from seeding Compose's own COMPOSE_* controls into that one render. Nothing else changes: the same releases are refused, the scanner image is still built from the approved SHA and proven for V2 evidence fields and revision before anything is recreated, the rollback dispositions are untouched, F11, trading authority, funded execution, paper admission, learning and the scheduler are not touched, and no threshold, budget or risk parameter moves. No deployment, merge or activation is authorized by this increment.

ARCHITECTURE REFERENCES:
- OHM-Trade-Agent-v1/deploy/remote/ohm-deploy: the single release controller, its scanner declaration probe, the fail-closed V2 refusal, the scanner image proof and both rollback paths.
- OHM-Trade-Agent-v1/docker-compose.yml: the `opip-scan-worker` service, its `profiles: [scanner]` gate and its resolved environment.
- OHM-Trade-Agent-v1/deploy/remote/reconcile-scheduler.sh: installs /usr/local/sbin/ohm-deploy from the release that reaches reconciliation, which is why the controller that runs a deploy is the previously installed one.
- .github/workflows/pytest.yml: the `atdd scope` job that enforces the active increment against the changed-file set.
- OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py: the round-2 and round-3 scanner release contracts this increment extends without replacing.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the declaration probe that decides whether the candidate release declares the isolated scanner and therefore gates its image provenance
WHEN:
`docker compose config --services` does not complete, and separately when it completes without listing the service
THEN:
each outcome is its own recorded verdict (PROBE_FAILED, SERVICE_NOT_IN_RESOLVED_MODEL, DECLARED) with the render's exit status, service list and bounded diagnostic published before any refusal, and no refusal can state that the release declares no scanner unless the resolved model was actually read

AC-002:
GIVEN:
a host whose effective environment may carry Compose's own controls
WHEN:
the probe resolves the candidate model
THEN:
the `scanner` profile is activated by the explicit flag and in the command's environment without replacing an ambient selection, the owner's `.env` cannot seed COMPOSE_* controls into that one render, and the published diagnostic is redacted, allowlisted, flattened, byte-bounded and defaults to NONE so no value can reach a receipt or a log

AC-003:
GIVEN:
the corrected probe
WHEN:
a release is released, refused or rolled back
THEN:
every existing gate and recorded fact is unchanged: the scanner image is built from the approved SHA and proven for V2 evidence fields and revision with the acceptance marker on failure, the V2 refusal keeps its markers and reason, both rollback dispositions are untouched, and the probe itself returns an exit status without refusing, exiting or mutating anything

EXPLICITLY OUT OF SCOPE:
- Any change to what a release must declare, to the V2 scoring authority, to `docker-compose.yml`, or to the scanner service's isolation, memory cgroup or lifecycle.
- Any change to the scanner image build, its revision/schema proof, the previous-image retention or either rollback path.
- Any funded credential, F11 protection credential, order, cancellation, funding, paper admission, learning, IRR or scheduler behaviour.
- Any retry, fallback or automatic controller replacement: the installed controller is still installed only by `reconcile-scheduler.sh` or the owner-operated bootstrap.
- Any production deployment, merge or activation.

FROZEN BOUNDARIES:
- Fixed risk sizing, minimum reward-to-risk, position verification and every execution safeguard are unchanged.
- AI output remains advisory and cannot override a deterministic rejection; no new provider, model, token budget or cost boundary is introduced.
- Paper trading and Freqtrade dry-run stay isolated from production authority; no exchange or Kraken private API permission is added, widened or exercised.
- Shadow, ML and analytics planes gain no production admission, and no canonical evidence, database or durable store is rewritten.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_scan_v2_evidence_v1.py::test_declaration_probe_never_presents_a_failed_render_as_an_absent_service
AC-002 -> tests/test_opip_scan_v2_evidence_v1.py::test_declaration_probe_activation_is_hermetic_and_its_evidence_secret_free
AC-003 -> tests/test_opip_scan_v2_evidence_v1.py::test_declaration_probe_changes_no_gate_provenance_or_rollback

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-SCANNER-DECLARATION-PROBE-M01.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-002 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-003 -> OHM-Trade-Agent-v1/deploy/remote/ohm-deploy
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_scan_v2_evidence_v1.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_ai_integration_reliability_v1.py

DEFERRED DISCOVERIES:
- The production deploy log for the refused release records both profiled probes resolving a model that does not contain `opip-scan-worker` (`OPIP_SCANNER_IMAGE=UNDECLARED_FOR_V2_RELEASE` and, on the rollback path, `OPIP_ROLLBACK_SCANNER=ABSENT_FROM_RESTORED_RELEASE`), while the release itself was installed (`OPIP_RELEASE_INSTALLED=f4378e49...`) and its un-profiled render resolved normally. Both the release's own controller and its Compose document already state the profile, so the missing piece is the host's effective profile resolution, and the probe recorded nothing about it. The corrected probe makes that a recorded fact on the next attempt rather than an inference; the host's `/etc/environment`, its `.env` and its Compose version remain unread from the engineering workstation.
- Declaring this increment moves the active pointer, and the completed ATDD-OPIP-AI-INTEGRATION-RELIABILITY-v1 AC-012 test asserted that the pointer equals that increment. ATDD-SCOPE-MOVABLE-ACTIVE-POINTER-v1 forbids exactly that pin, so the assertion is corrected to resolve the pointer to a declared contract and to prove that increment's identity from its own frozen contract. Its AC-012 criterion, trace and implementation map are unchanged; the edit is called out in the pull request rather than folded in silently.
- The controller that executes a deploy is the one installed by the last release that reached `reconcile-scheduler.sh`, or by the owner-operated bootstrap. A controller correction therefore cannot be delivered by the deploy it corrects; the owner bootstrap (`bootstrap-release-tree-controller.sh --apply --sha <40-hex>`) is the only lever, and this increment does not automate it.
- Rollback discovery keeps its own tolerance for a restored release that predates the service and its existing recorded dispositions; harmonising its diagnostic shape belongs to a separate increment if the owner wants it.

UNAPPROVED SCOPE CHANGES:
NONE
