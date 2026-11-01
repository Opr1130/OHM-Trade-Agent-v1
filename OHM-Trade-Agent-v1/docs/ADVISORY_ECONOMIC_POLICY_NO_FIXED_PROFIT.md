# Advisory economic gate policy: fixed-dollar threshold removal

Owner direction (2026-11-01): eliminate the $75 fixed minimum from
candidate economics. `MIN_NET_PROFIT` is now **0**, meaning a candidate
must still achieve nonnegative *estimated* net return at Target 2 after
configured transaction and margin costs. A zero minimum is not a profit
forecast and does not mean execution is guaranteed profitable.

Other gates are unchanged: stop exposure, capital envelope used for
validation, target movement, 2.5:1 reward/risk, margin eligibility,
target attainability, execution evidence, risk approval and Chief AI
recommendation. This change does not enable autonomous orders, adjust
risk limits, or switch V1/V2 scoring.

The Chief currently receives deterministic price targets and context,
and returns ALERT/WATCH/REJECT and reasons. It **does not** yet output
independent numerical upside scenarios with horizons. Add a strictly
structured, evidence-based optional potential-move scenario in a follow-up
reviewed integration, as an advisory field only. Avoid inventing
win probabilities or treating model confidence as a probability.

The impact must be measured: compare historical prefilter veto reasons
and confirm candidates under the former $75 constraint can reach
AI without bypassing deterministic safety.
