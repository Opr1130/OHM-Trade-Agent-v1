INCREMENT:
ATDD-OPIP-CONFIGURABLE-SCORING-v1

OWNER-APPROVED INTENT:
Add a bounded, configurable V2 technical scoring foundation and instrument
same-market V1/V2 comparison for the O'Pip owner. V2 is the target future
production scorer; V1 remains current authority until integrated cutover,
owner acceptance and separate deployment approval. No changes to live trade
execution, position limits, paper authority, learning, AI activation, or
funded trading. Read-only comparison and a private, non-activating strategy
configuration draft editor use the existing dashboard. The owner approved
self-service threshold tuning and separation of the protected admin surface.

ARCHITECTURE REFERENCES:
- docs/architecture/v1.5.0/ARCHITECTURE.md
- docs/TECHNICAL_SCORE_V2_CONFIG.md
- AGENTS.md canonical authority and fail-closed boundaries

APPROVED ACCEPTANCE CRITERIA:
AC-001:
Strategy configuration must be versioned and validate input without changing current production authorization.
GIVEN: production still uses V1, and owner has selected V2 as future production
WHEN: a versioned strategy/scoring profile is loaded
THEN: malformed or unauthorized settings fail closed and the profile does not enable V2 trading or funded execution.

AC-002:
V2 LONG/SHORT scoring uses point-in-time candle evidence only and refuses missing inputs.
GIVEN: the existing bounded Kraken scanner provides completed hourly candles
WHEN: V2 computes technical scores and its 4h, ADX, momentum and volatility inputs
THEN: no extra market-data API is needed, missing or interrupted evidence yields no score, and V1 production selection is unchanged.

AC-003:
Comparison evidence is read-only, bounded, and cannot affect trading.
GIVEN: V1 production candidates and V2 observational scores are evaluated on the same scan
WHEN: a comparison record is written and displayed
THEN: the result declares the active authority, reports unmatched candidates and evidence gaps, and never claims immature outcomes are profitable.

AC-004:
The owner-approved scoring scope is explicitly traceable.
GIVEN: the owner approved work on configurable scoring and a read-only dashboard
WHEN: CI validates the active increment and changed file mapping
THEN: this exact contract and its scoped acceptance tests authorize the listed changed files without bypassing the gate.

AC-005:
The strategy administration surface requires a separate credential and stores only validated, versioned drafts.
GIVEN: the operator wants self-service tuning and production trading must remain unchanged
WHEN: the owner loads, edits or saves strategy settings from the private dashboard
THEN: only approved advisory fields can change, a distinct admin credential is required, stale versions fail closed, saves are durable and auditable, and no draft activates trading.

AC-006:
V2 selection honors deterministic direction, qualified recovery and matched audit thresholds.
GIVEN: V2 feature scores are computed on completed candles and independently qualified
WHEN: the scorer version is explicitly selected in a controlled release
THEN: the V2 score determines top candidates, missing V2 features fail closed,
the same V2 threshold is recorded in admission evidence, a rejected SHORT
can only recover an independently qualified LONG, and the V1 comparison
remains read-only.

AC-007:
Configurable advisory economic floors and AI possible movement remain non-execution evidence.
GIVEN: historical $75 execution-compatible policy and a validated advisory-only proposal
WHEN: Chief review and final advisory economics use a selected policy and model suggests upside
THEN: the optional $0 net floor may remove advisory prefilter rejections, the shared
execution $75 default is unchanged, and model-estimated movement is bounded,
conditional, not a win probability and never bypasses deterministic risk gates.

AC-008:
A protected strategy policy draft can be promoted to an active advisory
configuration and rolled back without affecting funded trading.
GIVEN: an authenticated administrator, an explicitly enabled advisory consumer
and a saved validated draft with an expected SHA256
WHEN: the owner deliberately activates or rolls back that profile
THEN: a durable audit intent precedes a restricted atomic replace, stale edits
fail closed, the previous advisory profile is recoverable, malformed policies
are rejected, and execution authority remains unchanged.

EXPLICITLY OUT OF SCOPE:
V2 activation in a live deployment without release certification, auto-trading,
margin/portfolio policy relaxation, multi-tenant settings, code-driven learning,
changing global economic gate defaults, and a new dashboard service.

FROZEN BOUNDARIES:
V1 remains active by default; Top 8 and per-direction cap 5,
zero-overlap scanner, bounded memory, operator modes, deterministic safety,
AI qualification, paper controls, and funded-order protections unchanged.

ACCEPTANCE TEST TRACEABILITY:
AC-001 -> tests/test_strategy_scoring_scope_v1.py::test_strategy_profile_does_not_change_live_trading_authority
AC-002 -> tests/test_strategy_scoring_scope_v1.py::test_v2_requires_complete_point_in_time_feature_evidence
AC-003 -> tests/test_strategy_scoring_scope_v1.py::test_comparison_is_observational_and_has_no_qualified_outcome_claim
AC-004 -> tests/test_strategy_scoring_scope_v1.py::test_active_increment_scope_contract_resolves
AC-005 -> tests/test_strategy_admin_drafts.py::test_advisory_draft_saves_atomically_without_runtime_activation
AC-005 -> tests/test_strategy_admin_drafts.py::test_protected_trading_authority_cannot_be_changed_by_draft
AC-005 -> tests/test_strategy_admin_drafts.py::test_stale_expected_digest_is_rejected
AC-005 -> tests/test_strategy_admin_drafts.py::test_separate_admin_credential_required
AC-005 -> tests/test_strategy_admin_drafts.py::test_only_allowed_economic_and_v2_fields_are_editable
AC-006 -> tests/test_v2_production_selection_contract.py::test_v2_selection_uses_v2_long_and_short_not_v1_scores
AC-006 -> tests/test_v2_production_selection_contract.py::test_missing_v2_features_are_unavailable_not_v1_fallback
AC-006 -> tests/test_v2_production_selection_contract.py::test_v2_margin_fallback_requires_independent_v2_long_qualification
AC-006 -> tests/test_v2_production_selection_contract.py::test_selector_and_screening_rows_share_v2_threshold
AC-006 -> tests/test_v2_production_selection_contract.py::test_comparison_relabels_authority_correctly_on_v2_cutover
AC-007 -> tests/test_advisory_policy_v2.py::test_fixed_profit_floor_remains_in_execution_default_but_not_advisory_override
AC-007 -> tests/test_advisory_policy_v2.py::test_invalid_ai_return_potential_cannot_become_trade_claim
AC-008 -> tests/test_strategy_admin_drafts.py::test_advisory_activation_and_rollback_are_atomic_and_versioned
AC-008 -> tests/test_strategy_admin_drafts.py::test_activation_refuses_drafts_without_saved_version
AC-008 -> tests/test_strategy_admin_drafts.py::test_unapproved_activation_endpoint_is_disabled

IMPLEMENTATION MAP:
AC-001 -> OHM-Trade-Agent-v1/app/core/config.py
AC-001 -> OHM-Trade-Agent-v1/app/core/strategy_policy.json
AC-001 -> OHM-Trade-Agent-v1/app/core/strategy_policy.py
AC-001 -> OHM-Trade-Agent-v1/tests/test_strategy_policy_config.py
AC-002 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-002 -> OHM-Trade-Agent-v1/app/scanner/models.py
AC-002 -> OHM-Trade-Agent-v1/app/scanner/technical_score_v2.json
AC-002 -> OHM-Trade-Agent-v1/app/scanner/technical_score_v2.py
AC-002 -> OHM-Trade-Agent-v1/app/scanner/v2_features.py
AC-002 -> OHM-Trade-Agent-v1/docs/TECHNICAL_SCORE_V2_CONFIG.md
AC-002 -> OHM-Trade-Agent-v1/tests/test_technical_score_v2_config.py
AC-002 -> OHM-Trade-Agent-v1/tests/test_v2_features.py
AC-003 -> OHM-Trade-Agent-v1/app/api/dashboard.html
AC-003 -> OHM-Trade-Agent-v1/app/api/dashboard.py
AC-003 -> OHM-Trade-Agent-v1/app/api/scoring_comparison.html
AC-003 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-003 -> OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/ACTIVE_INCREMENT
AC-004 -> OHM-Trade-Agent-v1/docs/atdd/scope-contracts/ATDD-OPIP-CONFIGURABLE-SCORING-v1.md
AC-004 -> OHM-Trade-Agent-v1/tests/test_strategy_scoring_scope_v1.py

AC-005 -> OHM-Trade-Agent-v1/app/api/dashboard.py
AC-005 -> OHM-Trade-Agent-v1/app/api/dashboard.html
AC-005 -> OHM-Trade-Agent-v1/app/api/strategy_admin.html
AC-005 -> OHM-Trade-Agent-v1/app/core/config.py
AC-005 -> OHM-Trade-Agent-v1/app/core/strategy_policy.py
AC-005 -> OHM-Trade-Agent-v1/app/services/strategy_admin_drafts.py
AC-005 -> OHM-Trade-Agent-v1/tests/test_strategy_admin_drafts.py

AC-006 -> OHM-Trade-Agent-v1/app/scanner/directional_candidates.py
AC-006 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-006 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-006 -> OHM-Trade-Agent-v1/app/core/config.py
AC-006 -> OHM-Trade-Agent-v1/app/services/technical_scoring_comparison.py
AC-006 -> OHM-Trade-Agent-v1/tests/test_v2_production_selection_contract.py

AC-007 -> OHM-Trade-Agent-v1/app/core/config.py
AC-007 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-007 -> OHM-Trade-Agent-v1/app/services/chief_analyst.py
AC-007 -> OHM-Trade-Agent-v1/tests/test_advisory_policy_v2.py

AC-008 -> OHM-Trade-Agent-v1/app/api/dashboard.py
AC-008 -> OHM-Trade-Agent-v1/app/api/strategy_admin.html
AC-008 -> OHM-Trade-Agent-v1/app/core/config.py
AC-008 -> OHM-Trade-Agent-v1/app/core/strategy_policy.py
AC-008 -> OHM-Trade-Agent-v1/app/scanner/market_scanner.py
AC-008 -> OHM-Trade-Agent-v1/app/scanner/models.py
AC-008 -> OHM-Trade-Agent-v1/app/jobs/scan_opportunities.py
AC-008 -> OHM-Trade-Agent-v1/app/services/strategy_admin_drafts.py
AC-008 -> OHM-Trade-Agent-v1/tests/test_strategy_admin_drafts.py

DEFERRED DISCOVERIES:
Production V2 cutover is guarded behind a disabled-by-default configuration
and requires owner deployment approval. Dashboard configuration activation,
rollback and downstream outcome certification remain release blockers.

UNAPPROVED SCOPE CHANGES:
NONE
