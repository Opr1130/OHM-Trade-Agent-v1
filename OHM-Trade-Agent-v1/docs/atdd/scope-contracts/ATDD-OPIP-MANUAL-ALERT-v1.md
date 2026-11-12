INCREMENT:
ATDD-OPIP-MANUAL-ALERT-v1

OWNER-APPROVED INTENT:
The owner requires actionable high-quality manual trading signals without selling
20 manually held Kraken assets to clear F11, and explicitly deferred exchange
fill reconciliation to V6. When the existing reconciliation subsystem is OFF,
the qualified native scanner may stamp a MANUAL ONLY advisory which can be sent
after all existing action, quality, liquidity, economics, expiry, dedupe and
notification-admission gates, without creating any Kraken order intent. The
message must explicitly say that fills and protective stops are NOT tracked.
No automatic funded trading or execution authority is enabled.

ARCHITECTURE REFERENCES:
- app/jobs/scan_opportunities.py: stamp the explicit manual advisory mode only
  when exchange reconciliation is disabled and in observe mode.
- app/services/qualified_trade_tracking.py: protected no-intent branch under
  explicit manual stamp and reconciliation disabled; otherwise existing tracking.
- app/services/chief_alert_notifier.py: retain all action/economic/notification
  admission checks, label manual advisory and use mode-specific dedup identity.
- app/services/qualified_alert_outbox.py: persist manual stamp and protect retry
  against mode changes or missing manual-only card.
- app/services/actionable_admission.py: unchanged seven independent conditions.
- app/services/pending_setup_registry.py: unchanged waiting-setup identity.
- tests/test_opip_manual_only_alerts_v1.py: focused acceptance evidence.
- tests/test_target_attainability.py: positive-only synthetic LONG execution-evidence
  fixture repair for existing CI tests; no production quality gate is changed.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a qualified candidate explicitly stamped manual-only by the native
scanner, and KRAKEN_RECONCILIATION_ENABLED=false in observe mode
WHEN: a reconciliation intent would otherwise be registered before Telegram
THEN: no exchange intent, API write, order placement, or fill lifecycle is
created; unstamped cases remain blocked, and enabled/apply retains tracking

AC-002:
GIVEN: an existing approved action and admissible market/venue/execution evidence
WHEN: a manual-only notification is rendered and sent
THEN: the card prominently states Tradevisor Alerts — MANUAL ONLY, that fills
and stops are untracked, and includes entry zone, stop, targets, expiry and
existing safety evidence; it still honors existing dedupe and queue limits

AC-003:
GIVEN: a queued notification from either manual-only or legacy tracking mode
WHEN: outbox retries delivery
THEN: only a manually stamped, still-disabled reconciliation card with a
valid manual warning and original ADMITTED/expiry evidence may skip intent
registration; legacy unstamped alerts still require tracking, and mode drift
or missing manual warning blocks replay

EXPLICITLY OUT OF SCOPE:
- Any funded trading, order placement, cancellation or fill acknowledgement.
- F11 rule, admission suspension, protective stops, manual holdings or exemptions.
- Changes to AI scoring, target geometry, market filters, liquidity or capacity.
- New notification channel, timer, scheduler, webhook route or rate-limit changes.
- Automatic activation of learning, Committee, Kraken reconciliation or paper.
- Production release until separate owner-approved deployment validation.

FROZEN BOUNDARIES:
- Strict F11 remains UNSAFE/fail-closed for the 20 unmanaged holdings.
- Manual-only is advice only; an owner manually manages orders and risk.
- Manual alerts are not evidence of Kraken position protection.
- All market/execution-quality and admission gates run BEFORE pending setup
  registration, notification reservation, delivery or retry.
- Missing/stale execution evidence, invalid venue, depth and capacity block.
- Manual mode cannot create an exchange order intent and cannot be applied when
  reconciliation is enabled or in apply mode.
- Reconciliation remains OFF; V6 redesign is separate.
- Legacy queued cards cannot be silently relabeled as manual.
- Delivery ledgers, exact emission governor and expiry remain authoritative.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_manual_only_alerts_v1.py::test_manual_only_tracking_never_creates_kraken_intent
AC-002 -> tests/test_opip_manual_only_alerts_v1.py::test_manual_only_signal_uses_existing_action_and_market_admission
AC-003 -> tests/test_opip_manual_only_alerts_v1.py::test_replay_requires_explicit_manual_stamp_and_still_requires_admission
AC-003 -> tests/test_opip_manual_only_alerts_v1.py::test_unstamped_legacy_alert_remains_tracking_required

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/app/services/qualified_trade_tracking.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_manual_only_alerts_v1.py
AC-002 -> OHM-Trade-Agent-v1/app/services/chief_alert_notifier.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_manual_only_alerts_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_target_attainability.py
AC-003 -> OHM-Trade-Agent-v1/app/services/qualified_alert_outbox.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_manual_only_alerts_v1.py
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-003 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-MANUAL-ALERT-v1.md

DEFERRED DISCOVERIES:
- Existing target-attainability positive fixtures omitted mandatory execution
  validation and caused four unrelated CI failures; they now specify synthetic
  LONG evidence, consistent with the isolated fixture repair in draft PR #521.
  Missing production evidence is still rejected; no scoring or veto bypass.
- Owner must verify production still has reconciliation disabled and no
  funded order authority before deploying; manual-alert label is not a stop.
- PR #521 final recommended notional correction is a separate release dependency
  for false notification capacity refusal. Do not weaken the capacity gate.
- No claim of real delivery from green CI. Production Telegram and F11 must be
  independently observed, and no forced real trading or restart is permitted.
- A queued card cannot survive mode drift without being requalified.

UNAPPROVED SCOPE CHANGES:
NONE
