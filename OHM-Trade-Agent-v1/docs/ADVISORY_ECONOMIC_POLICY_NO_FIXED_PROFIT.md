# Advisory economics policy change (draft, not deployed)

**Do not globally change `MIN_NET_PROFIT=75`**: that default is shared by
trade-action, Tradevisor, and other execution-adjacent contracts. A blanket
zero change caused seven failing regression tests and can weaken an unrelated
production policy.

The approved direction is **advisory-only**: use a validated strategy profile
for `advisory_economics.min_net_profit_usd=0` and explicitly pass this value to
the Chief Analyst's screening path. Retain the execution-compatible default
until a separate, explicitly authorized auto-trading policy is developed.

A zero advisory floor means the estimate must be nonnegative after configured
costs. All target geometry, reward-to-risk, attainability, margin, liquidity,
stop-risk and operator vetoes remain in force. No trading activation.

Integration must use the shared strategy policy loader from draft PR #486,
with independent verification of missing config, profile hash and replay. The
Chief's numerical AI upside scenarios are a separate additive output schema.
