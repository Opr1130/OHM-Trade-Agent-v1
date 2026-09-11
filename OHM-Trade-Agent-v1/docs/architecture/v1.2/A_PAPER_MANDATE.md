# A. Paper mandate

O'Pip remains paper-only. PR 1 documents two logically separate accounts. It does not create ledgers, change control files, or enable new paper authority.

## Accounts

| Account | Starting equity (N1/N2) | Max positions (N3) | Authority |
| --- | --- | --- | --- |
| Research simulation | $10,000 | 3 | Isolated native paper + Freqtrade dry-run as they exist today |
| Approved paper allocation | $10,000 nominal | 2 initially | **Logically separate ledger.** Does not exist as a distinct store yet. No runtime split in PR 1 |

The two accounts must never share cash, reserved capital, or position capacity. The approved-allocation ledger is named now so later PRs cannot silently reuse the research pool.

## Instrument universe

- Venue: Kraken spot only.
- Quote currencies: **USD** and **USDT** (`ELIGIBLE_QUOTES` in `app/scanner/universe.py` at the pin).
- USD is canonical when both quotes exist for a base.
- Native paper v1: LONG only. SHORT/margin enrollments are recorded as unsupported.

## Supported order types (target policy)

- Market entry (ENTER NOW) at executable side-of-spread plus configured slippage.
- Limit / pullback pending entry. A touched limit does **not** automatically imply a fill.
- Residual entry-order expiry on its own deadline.
- Partial fills are first-class (`PARTIAL_FILL`).
- Fees charged on every realized leg.

Current native paper is more generous than the target (limit touch can fill from OHLC). That is an **ADAPT** item for later PRs, not a PR 1 code change.

## Credential isolation

- No funded exchange-order authority.
- No Kraken private keys on paper, Freqtrade, or the learning worker.
- Native paper sets `exchange_write_authority=False`.
- Freqtrade configs keep empty exchange keys and `dry_run=true`.
- Operator control: `/app/data/paper_trading/control.json` via `paper_trade_cli`. Unreadable control does not create new exposure.

## Existing paper-policy numbers (document only)

| Setting | Current / ratified | Notes |
| --- | --- | --- |
| Research equity | $10,000 | N1 |
| Approved-allocation equity | $10,000 separate | N2 |
| Research max positions | 3 | N3 |
| Approved-allocation max positions | 2 initially | N3 |
| Fee rate | 0.4% | existing `paper_trade_fee_rate` |
| Slippage | 10 bps | existing |
| TP1 fraction | 50% | existing |
| Pending TTL / max hold | 24h / 24h | existing |
| Paper candle interval | 15 minutes | **paper-policy / feature input only** (N10) |
| Daily loss | 1% | N4 policy only; **not enforced in runtime in PR 1** |
| Drawdown | soft 5% / hard 8% | N5; do not loosen |
| Legacy comparator risk / R:R | 0.35% / 2.5 | N6; freeze comparator; not a permanent PI invariant |
| Gross exposure / same-direction / capital fraction | 50% / 2 / 20% | existing portfolio gate; configurable |

## Explicit non-authority

Paper modules must not import or mutate Kraken private clients, live registries, pending-setup/order-intent paths, or Telegram execution callbacks. Static tests already enforce this boundary; PR 1 does not change them.

`PRODUCTION TRADE AUTHORITY CHANGED = NO`

`FUNDED TRADING ENABLED = NO`
