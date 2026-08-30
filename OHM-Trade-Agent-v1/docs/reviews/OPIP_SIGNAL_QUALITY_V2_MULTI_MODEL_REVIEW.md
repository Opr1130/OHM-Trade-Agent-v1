# O'Pip Signal Quality v2 — Multi-Model Architecture Review Charter

Purpose: obtain independent, adversarial review of OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md before implementation.

All reviewers must evaluate the SAME target architecture and the SAME current-production baseline. Do not redesign from scratch unless a current assumption is demonstrably unsafe or ineffective.

## Review package

Required design document:
- OPIP_SIGNAL_QUALITY_TRADE_LIFECYCLE_V2.md

Required current-production context:
- OPIP_DECISION_ENGINE_V1.md
- OPIP_REALTIME_MARKET_INTELLIGENCE_V1.md
- OPIP_EVENT_INTELLIGENCE_V1.md
- docs/opip-ml-foundation-v1.md
- app/jobs/run_cycle.py
- app/jobs/scan_movers.py
- app/jobs/scan_opportunities.py
- app/services/price_movement_radar.py
- app/services/trade_decision_intelligence.py
- app/services/chief_alert_notifier.py
- app/services/kraken_reconciliation.py
- app/services/active_trade_monitor_runner.py
- app/services/trade_monitor.py
- app/services/trade_monitor_notifier.py
- app/services/notification_policy.py
- relevant lifecycle, Telegram, decision, learning, and safety tests

## Mandatory invariants

- Advisory only; no real order-placement authority.
- Kraken read-only account truth is authoritative for exposure existence.
- No user-facing informational watch alerts in the target design.
- New-trade alerts only when actionable and fully qualified.
- Existing-holding alerts only when attention/action is required.
- Broad eligible-universe discovery; Top-15 is one channel, not the universe.
- Events/news/whale/order-flow/technical/regime evidence are fused into one decision path.
- No uncalibrated score may be marketed as probability.

## Reviewer roles

### Claude — architecture and code-integrity reviewer

Focus on:
- lifecycle/state-machine correctness
- race conditions and registry divergence
- fail-open/fail-closed behavior
- separation of discovery, qualification, alerting, and protection
- module boundaries and coupling
- data contracts and versioning
- whether the proposed refactor can be implemented cleanly from the current codebase
- testability and operational failure modes

### Gemini — adversarial signal-quality and systems reviewer

Focus on:
- false-positive / false-negative tradeoffs
- missed-opportunity failure modes
- whether discovery channels are sufficiently broad
- rank velocity, market-relative scoring, and exhaustion handling
- alert-fatigue prevention
- event/news and multi-source contradiction handling
- whether fixed thresholds should be volatility/regime adaptive
- whether target/stop/holding-horizon logic is economically coherent

### Kimi — simplification and hidden-assumption reviewer

Focus on:
- over-engineering and redundant layers
- hidden coupling between old and new paths
- scheduler/queue/notification interactions
- Kraken truth vs local lifecycle state
- opportunities to simplify without losing signal quality
- storage/learning complexity and operational cost
- what should be removed rather than migrated

### ChatGPT — synthesis and implementation-owner review

Focus on:
- reconciling reviewer disagreements
- preserving current safety invariants
- converting design into staged implementation
- defining acceptance metrics and rollback boundaries
- rejecting changes that increase complexity without measurable signal-quality benefit

## Required output from every reviewer

Return exactly these sections:

1. VERDICT: APPROVE / APPROVE WITH CHANGES / REJECT
2. TOP 5 ARCHITECTURAL RISKS
3. TOP 5 SIGNAL-QUALITY RISKS
4. MISSING DATA OR FEATURES
5. COMPONENTS TO REUSE
6. COMPONENTS TO REPLACE OR DELETE
7. CONTINUATION / EXPLOSION MODEL REVIEW
8. ENTRY / TARGET / STOP MODEL REVIEW
9. GLOBAL RANKING / CAPITAL-UTILITY REVIEW
10. EXISTING-POSITION PROTECTION REVIEW
11. EVENTS / NEWS / WHALE / ORDER-FLOW REVIEW
12. LEARNING / CALIBRATION / LABEL-LEAKAGE REVIEW
13. MIGRATION / DEPLOYMENT RISKS
14. REQUIRED TESTS BEFORE PRODUCTION
15. PROPOSED CHANGES, ranked P0 / P1 / P2

## Scoring rubric

Score 1-5 with justification:

- Signal precision
- Major-mover recall
- Entry timing quality
- Capital efficiency
- Existing-position protection
- Calibration integrity
- Operational resilience
- Explainability / auditability
- Implementation complexity
- Migration safety

## Explicit challenge questions

1. Could a future major mover be missed because it never enters the Top-15 early enough?
2. Could a coin pass continuation but still be a poor entry because it is overextended?
3. Could global ranking over-favor highly volatile low-quality assets?
4. Could news/events create look-ahead leakage in backtests?
5. Could missing cross-venue evidence incorrectly downgrade good Kraken-only opportunities?
6. Could Kraken/local-registry divergence still create an unprotected real holding?
7. Could a stable DETERIORATING state remain silent while risk materially compounds?
8. Are T1-before-stop labels robust across volatility regimes?
9. Is expected-value ranking stable enough for capital allocation before probability calibration?
10. Which existing production components should be deleted instead of adapted?

## Decision rule

No production implementation begins until the consolidated review resolves all P0 findings and the target architecture is explicitly approved.

PR #169 remains a diagnostic hotfix branch and must not be merged as the final architecture.