# Waves 3–7 completion stack

This stack is intentionally layered on the verified Wave 2 head and does not modify `main`.

## Wave 3 — Intelligence expansion
- deterministic setup classification
- regime alignment context
- liquidity quality tiers
- news/catalyst activity context
- context-aware finalist ranking
- context score is review evidence, not win probability and not a bypass around existing gates

## Wave 4 — Learning governance
- minimum sample requirement
- positive expectancy / profit factor / drawdown thresholds
- evidence may become READY_FOR_HUMAN_REVIEW
- learned evidence can never auto-apply or silently loosen live controls

## Wave 5 — Execution optimization
- execution posture responds to spread, observed slippage, liquidity, and catalyst proximity
- thin/adverse markets force passive limit behavior
- imminent catalysts suppress urgency and price chasing

## Wave 6 — Operations and autonomy
- unattended operation requires green CI, healthy reconciliation and alerting, fresh data, and zero unresolved execution errors
- any failed operational gate blocks autonomy

## Wave 7 — Controlled production scaling
- PAPER -> TINY_LIVE -> LIMITED_LIVE -> SCALED_LIVE only one stage at a time
- every capital increase requires explicit human approval
- edge, out-of-sample profitability, drawdown, execution quality, and operations must all pass
- controlled scaling remains capped; no automatic full-capital mode exists

## Validation policy
The existing repository PR workflow remains the authoritative full-suite gate (`pytest` plus `compileall`). No merge or deployment is authorized by this completion stack.
