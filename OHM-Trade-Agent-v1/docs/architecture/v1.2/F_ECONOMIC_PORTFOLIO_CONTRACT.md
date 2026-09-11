# F. Economic / portfolio contract

## Primary objective

Maximize expected **portfolio net dollars** over a common evaluation window.

Subject to:

- capital limits
- concentration / common-shock limits
- liquidity / capacity
- data-quality constraints
- drawdown / loss limits
- valid execution evidence

## Benchmarks

- Cash / no-trade.
- Frozen legacy comparator (today: technical score / Top-8 + profit ranking). The comparator terminates at technical cutover.

## Trading vs operating economics

- **Trading economics** = simulated cash P&L after fees and other explicit transaction charges. Spread/slippage already embedded in executable fill prices is not deducted twice.
- **Operating economics** = trading economics minus attributable compute, storage, data, AI, and recurring operating costs.

## Reservation

- Atomic capital/risk reservation occurs against a portfolio version.
- Reserved amounts release on cancellation or expiry and adjust on fills.
- Research simulation and approved allocation **do not share** reservations (N2/N3).

## Diagnostics vs objective

Capital occupancy / time-to-exit is a comparison diagnostic. Total net dollars remains primary.

Hit rate, profit factor, and ECE are diagnostics, not the primary endpoint.

## Forbidden in v1

- Kelly sizing
- Covariance optimizers
- Leverage
- Dynamic risk parity

Hard concentration and common-shock limits are used instead. Current observed portfolio caps (gross 50%, same-direction 2, capital fraction 20%) remain configurable current practice until a later ratified selector policy replaces them.

## Legacy comparator freeze (N6)

The live qualification path stays frozen at **0.35% risk** and **2.5 minimum R:R**. Those numbers are **not** permanent Profit Intelligence architecture invariants.
