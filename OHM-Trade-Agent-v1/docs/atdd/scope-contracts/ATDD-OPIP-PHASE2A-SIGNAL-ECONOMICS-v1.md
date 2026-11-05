INCREMENT:
ATDD-OPIP-PHASE2A-SIGNAL-ECONOMICS-v1

OWNER-APPROVED INTENT:
Phase 2A signal economics. The shared economic default no longer applies the
historical fixed $75 net-profit, 4 percent Target 2 movement and 2.5:1
reward-to-risk floors; positive projected net profit after modelled costs is the
only default requirement, and an owner-configured versioned strategy profile may
opt into stricter bounds. A missing AI provider is recorded as a distinct
configuration state rather than a provider outage, and configured risk-per-trade
and max-positions reach the capital allocator. F11 protection, risk ceilings,
capital limits, funded authority and execution policy are unchanged. No
production deployment or strategy-profile activation is authorized. This
contract requires OWNER approval before merge.

ARCHITECTURE REFERENCES:
- docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1.md: advisory economics and its superseded execution-default assertions.
- docs/atdd/scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md: AC-007.
- docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md: AC-021.
- app/services/economic_quality_gate.py: shared versioned economic policy.

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: the shared economic gate and its execution-side callers read the versioned economic policy
WHEN: a candidate has positive projected net profit after modelled costs but less than $75
THEN: it is not rejected by a fixed dollar, movement or reward-to-risk floor by default, a negative or zero net is still rejected, and an explicitly configured stricter minimum is honoured.

AC-002:
GIVEN: the scanner reaches the AI review stage with no AI provider configured
WHEN: the stage outcome is recorded and mapped into the decision funnel
THEN: it is reported as NOT_CONFIGURED, distinct from a provider failure, and still produces no trade.

AC-003:
GIVEN: owner-configured risk-per-trade and max-open-trades settings
WHEN: the trade decision requests a capital recommendation
THEN: the configured values are forwarded to the allocator and no execution authority changes.

EXPLICITLY OUT OF SCOPE:
Activating a strategy profile, deploying production, removing modelled costs,
disabling F11 or other hard risk controls, changing exchange or order policy,
funded orders, new infrastructure.

FROZEN BOUNDARIES:
F11 independent protection, position limits, max stop-risk, capital limits,
execution credentials and authority, and evidence/canonical history remain
unchanged. AI remains advisory and cannot override deterministic gates.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_advisory_policy_v2.py::test_fixed_profit_floor_is_disabled_in_shared_default
AC-001 -> tests/test_advisory_policy_v2.py::test_no_dollar_cutoff_in_shared_default
AC-001 -> tests/test_advisory_policy_v2.py::test_execution_gate_has_no_fixed_dollar_bar
AC-001 -> tests/test_advisory_policy_v2.py::test_execution_side_paths_share_disabled_defaults
AC-002 -> tests/test_phase2a_signal_economics_v1.py::test_chief_not_configured_is_recorded_distinctly
AC-002 -> tests/test_phase2a_signal_economics_v1.py::test_ai_status_map_distinguishes_not_configured
AC-003 -> tests/test_phase2a_signal_economics_v1.py::test_trade_decision_forwards_configured_risk_sizing

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/services/economic_quality_gate.py
AC-001 -> OHM-Trade-Agent-v1/app/services/trade_action_gate.py
AC-001 -> OHM-Trade-Agent-v1/app/services/tradevisor_evaluation.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/decision/gates.py
AC-001 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-001 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-PHASE2A-SIGNAL-ECONOMICS-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-ADVISORY-NO-FIXED-PROFIT-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-TRADEVISOR-V2-ADVISORY-v1.md
AC-001 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_advisory_observability_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_tradevisor_evaluation_v1.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_wave9_action_notification_reliability.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_wave9_capacity_action_gate.py
AC-002 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/engine.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/funnel.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/models.py
AC-002 -> OHM-Trade-Agent-v1/app/opip/decision/observer.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_decision_engine_v1.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_phase2a_signal_economics_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/services/trade_decision_intelligence.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_phase2a_signal_economics_v1.py

DEFERRED DISCOVERIES:
The ADVISORY-NO-FIXED-PROFIT AC-001, AC-007 and AC-009 narrative statements that
execution defaults keep the $75 bar are superseded by this increment; their
traceability now points at the renamed tests. Production effective profile state
is not verified by this change.

UNAPPROVED SCOPE CHANGES:
NONE
