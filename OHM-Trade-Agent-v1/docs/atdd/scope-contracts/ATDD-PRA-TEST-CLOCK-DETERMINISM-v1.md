INCREMENT:
ATDD-PRA-TEST-CLOCK-DETERMINISM-v1

OWNER-APPROVED INTENT:
Repair a date-dependent test fixture in the PRA learning-safety-gate suite. The fixture pinned an absolute window (`FUTURE = NOW + 30 days`, `NOW = 2026-09-16 12:00 UTC`), and the promotion resolver is timestamp-based (`moment >= expires_at_utc` expires), so the "approved and effective" test began failing once the machine clock passed the pinned expiry instant — first on `main` itself (post-merge `test` job: 1 failed, 8057 passed, 9 skipped) and identically in CI on every R5-E integration candidate. The repair pins the runtime clock **at its lookup location** so the module's fixed instant is used, keeps the real resolver, persistence, profile matching and multiplier calculation executing, and adds deterministic coverage of the expiry boundary. No application behaviour, approval policy, expiry comparator, multiplier bound, trading authority, dependency, workflow or architecture changes.

ARCHITECTURE REFERENCES:
- Architecture v1.5.0 (frozen) clause set governing learned influence: learning may not authorize its own runtime influence, and an unprovable approval stays neutral (`PA-20` posture; `LE-03` evidence-of-learning).
- `OHM-Trade-Agent-v1/app/services/learning_governance.py`: `resolve_calibration_promotion` (fail-closed toward neutral; `PROMOTION_REASON_APPROVED_AND_EFFECTIVE`, `..._EXPIRED`) and `NEUTRAL_CALIBRATION_MULTIPLIER`.
- `OHM-Trade-Agent-v1/app/services/trade_decision_intelligence.py`: `_effective_calibration_multiplier` resolves with `now=datetime.now(timezone.utc)` at the lookup site.
- `AGENTS.md` / `CLAUDE.md`: smallest change that solves the proven problem; no weakening of safety gates; no policy ratification.

APPROVED ACCEPTANCE CRITERIA:

AC-001:
GIVEN:
the suite's pinned instant and an approved, effective, unexpired promotion for the live learned profile
WHEN:
the runtime calibration multiplier is read with the runtime clock pinned at its lookup location
THEN:
the promoted multiplier is returned with the approved-and-effective reason at that fixed instant, and the result does not depend on the machine's date

AC-002:
GIVEN:
the real `resolve_calibration_promotion` and a promotion whose `expires_at_utc` is the pinned instant
WHEN:
the resolver is evaluated one second before, exactly at, and one second after that instant
THEN:
it authorizes only before the boundary, and returns the expired reason at and after it, because the comparator is `moment >= expires_at_utc`

AC-003:
GIVEN:
an approved and effective promotion and a runtime clock pinned past its expiry
WHEN:
the runtime calibration multiplier is read
THEN:
influence stays neutral with the expired reason (fail-neutral preserved), and the repair introduces no change to the resolver, the approval policy, the expiry comparator, the multiplier bounds or trading authority

EXPLICITLY OUT OF SCOPE:
- Any change to `app/**`: the promotion resolver, the expiry comparator, approval metadata rules, persistence format, profile matching, multiplier bounds, risk sizing, R:R and trading authority all stay exactly as shipped.
- Any change to the pinned `NOW`/`PAST`/`FUTURE` values themselves, any move of `FUTURE` "further into the future", any `datetime.now()`-relative fixture, any mock of an approval result, any removal of expiry, any skipped or weakened assertion, and any replacement of the safety gate.
- Any dependency, workflow, release-profile, architecture or ATDD-contract change beyond this repair's own contract and the `ACTIVE_INCREMENT` pointer.

FROZEN BOUNDARIES:
- The promotion resolver remains the only authority for learning influence and stays fail-closed toward `NEUTRAL_CALIBRATION_MULTIPLIER`.
- Expiry stays enforced at the boundary instant (`moment >= expires_at_utc` expires); expired or otherwise unauthorized promotions still return neutral influence.
- Tests stay deterministic: no assertion may depend on the machine's date or wall clock.
- No new runtime behaviour, store, event or authority is introduced by this repair.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_pra_learning_safety_gate.py::test_runtime_authorizes_at_the_pinned_instant
AC-001 -> tests/test_opip_pra_learning_safety_gate.py::test_approved_and_effective_authorizes_multiplier
AC-002 -> tests/test_opip_pra_learning_safety_gate.py::test_approved_promotion_authorizes_immediately_before_expiry
AC-002 -> tests/test_opip_pra_learning_safety_gate.py::test_approved_promotion_is_not_effective_exactly_at_expiry
AC-002 -> tests/test_opip_pra_learning_safety_gate.py::test_approved_promotion_is_expired_after_expiry
AC-003 -> tests/test_opip_pra_learning_safety_gate.py::test_runtime_is_neutral_when_the_clock_is_past_expiry

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_pra_learning_safety_gate.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_pra_learning_safety_gate.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_pra_learning_safety_gate.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-PRA-TEST-CLOCK-DETERMINISM-v1.md
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT

DEFERRED DISCOVERIES:
- Whether the product intends expiry at instant or UTC-day granularity is NOT decided here; the comparator is timestamp-based as shipped and is left unchanged. If day granularity were ever intended it would be a separate, owner-approved semantic change.
- A reusable clock seam in the runtime module would let future suites freeze time without patching `datetime`; whether to add one is an owner decision and is out of scope here.
- The five R5-E integration candidates inherit this failure; propagating the repair is an integration action (re-base after this lands), not part of this repair.

UNAPPROVED SCOPE CHANGES:
NONE
