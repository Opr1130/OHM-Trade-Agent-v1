INCREMENT:
ATDD-OPIP-V6-SIMULATOR-v1

OWNER-APPROVED INTENT:
Provide the smallest useful private strategy simulator: an offline, read-only
tool that applies configurable policies (score, confidence, liquidity, Early
Watch band, spread, slippage, costs, reward-to-risk, net profit, BTC backdrop)
to supplied or synthetic candidates, shows which candidates qualify or are
rejected and why, and exports baseline-versus-variant funnel evidence. It has
no order authority, cannot change production settings, and labels its evidence
class. It is not wired into the scanner and is not deployed. Draft for owner review.

ARCHITECTURE REFERENCES:
- app/opip/simulator/engine.py: pure evaluation, reason codes, comparison.
- app/opip/simulator/cli.py and __main__.py: offline CLI.
- app/core/strategy_policy.py: validated reference policy used only as the baseline source (read-only).
- docs/V6_SPEC.md section 7 safety boundary; docs/v6/V6_ARCHITECTURE_DECISIONS.md (D1).

APPROVED ACCEPTANCE CRITERIA:
AC-001:
GIVEN: a candidate and a simulator policy with one threshold tightened
WHEN: the candidate is evaluated
THEN: it is rejected with exactly that threshold's stable reason code, the
Early Watch liquidity band is honoured, and net economics subtract costs and slippage

AC-002:
GIVEN: a candidate with missing, non-finite or geometrically invalid data
WHEN: it is evaluated under any policy
THEN: it is rejected with an explicit DATA_MISSING or GEOMETRY_INVALID code and never qualified

AC-003:
GIVEN: a candidate dataset, a baseline policy and a variant policy
WHEN: the comparison runs
THEN: the report carries both funnels, per-reason counts, the changed
candidates, both policy digests and an evidence class (SYNTHETIC or
SUPPLIED_UNVERIFIED) with a notice that it is not a profitability claim, and is deterministic

AC-004:
GIVEN: the simulator package and its CLI
WHEN: they are imported and run
THEN: they import no exchange, order, notification or network module, write
only the requested report (atomically, never over an input or policy file),
reject unknown, nonfinite or out-of-range parameters, and leave the production policy file unchanged

EXPLICITLY OUT OF SCOPE:
- Any wiring into the scanner, notifications, Telegram, reconciliation, F11 or trading.
- Changing production thresholds or the production strategy policy.
- Historical backtests, profitability claims, paper trading or learning.
- Hosting, UI or deployment of the simulator.

FROZEN BOUNDARIES:
- app/core/strategy_policy.py and strategy_policy.json are unchanged.
- No new dependency, service, scheduler or database.
- Funded execution, F11, AI Committee and learning stay unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_opip_v6_strategy_simulator_v1.py::test_each_threshold_rejects_with_its_own_reason_code
AC-001 -> tests/test_opip_v6_strategy_simulator_v1.py::test_early_watch_band_and_net_economics_are_explicit
AC-002 -> tests/test_opip_v6_strategy_simulator_v1.py::test_missing_or_invalid_data_fails_closed
AC-002 -> tests/test_opip_v6_strategy_simulator_v1.py::test_missing_score_for_the_selected_scorer_is_data_missing
AC-002 -> tests/test_opip_v6_strategy_simulator_v1.py::test_invalid_slippage_fails_closed_even_without_a_limit
AC-002 -> tests/test_opip_v6_strategy_simulator_v1.py::test_missing_slippage_is_rejected_when_a_limit_is_set_and_flagged_otherwise
AC-003 -> tests/test_opip_v6_strategy_simulator_v1.py::test_comparison_reports_funnel_changes_labels_and_digests
AC-003 -> tests/test_opip_v6_strategy_simulator_v1.py::test_synthetic_data_is_deterministic_and_the_baseline_matches_the_reference_policy
AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_simulator_has_no_order_network_or_notification_dependencies
AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_cli_writes_only_the_report_and_never_changes_production_policy

AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_invalid_policy_parameters_are_rejected_at_the_boundary
AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_regimes_must_be_a_list_of_names_not_a_string
AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_report_never_overwrites_an_input_file
AC-004 -> tests/test_opip_v6_strategy_simulator_v1.py::test_report_output_must_be_json_in_an_existing_directory_and_leaves_no_temp

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/opip/simulator/__init__.py
AC-001 -> OHM-Trade-Agent-v1/app/opip/simulator/engine.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-001 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-V6-SIMULATOR-v1.md
AC-002 -> OHM-Trade-Agent-v1/app/opip/simulator/engine.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/simulator/engine.py
AC-003 -> OHM-Trade-Agent-v1/app/opip/simulator/cli.py
AC-003 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/simulator/cli.py
AC-004 -> OHM-Trade-Agent-v1/app/opip/simulator/__main__.py
AC-004 -> OHM-Trade-Agent-v1/tests/test_opip_v6_strategy_simulator_v1.py
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_BASELINE.md
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_ARCHITECTURE_DECISIONS.md
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_BLOCKERS.md
AC-004 -> OHM-Trade-Agent-v1/docs/v6/V6_OWNER_REVIEW.md

DEFERRED DISCOVERIES:
- Real historical candidate datasets are not bundled; the simulator accepts a JSONL the owner supplies and labels it SUPPLIED_UNVERIFIED.
- Reusing the production gate functions directly (instead of the simulator's parallel parameterised rules) needs a pure, versioned gate interface; deferred as a D2 decision.
- Private hosting needs verified access controls and separate authorization.

UNAPPROVED SCOPE CHANGES:
NONE
