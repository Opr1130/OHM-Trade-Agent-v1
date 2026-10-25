# Tradevisor V2 — Advisory Signal Integration

Status: **implementation complete, deployment NOT authorized.**
`OPIP_TRADEVISOR_ENABLED=false` (the default) means the webhook answers `404`
and the cycle phase is inert.

**Owner policy disposition (2026-10-23): shadow-only until further approval.**
G-02 requires trusted F6 evidence or a separately owner-approved deterministic
fallback before any actionable qualification; neither exists today. G-04 also
requires independently trusted **completed 4H context for SWING**; it is not
available and 1H is never a substitute. The full R4/F5/F7 hypothetical plan is
retained for measurement, but a qualifying BUY currently returns `WATCH` with
`ADVISORY_EVIDENCE_INCOMPLETE` and is **never sent as an actionable Telegram
alert**. G-03 accepts the existing `ADVISORY_NON_CANONICAL` inbox exclusively
for shadow observation and learning: it has no admission or execution authority,
and the original canonical-outage scenario is not claimed as tested. Enabling
webhook ingestion in a future approved staging run does not waive these gates.

## What this is

A versioned webhook that lets the invite-only **Tradevisor V2** TradingView
indicator hand O'Pip a BUY/SELL observation, and a pipeline that answers one
question: *does this BUY correspond to a defensibly tradable opportunity right
now?* It builds a costed hypothetical plan when the deterministic gates pass,
then returns a nonactionable `WATCH` while the mandatory F6/4H evidence is
unavailable; the notifier is gated on actual actionable qualification.

## What this is NOT

- Not a second trading system. It cannot place, modify, confirm or cancel an
  order, and it cannot admit a symbol into native selection.
- Not a source of truth. Tradevisor is *external advisory evidence*; every
  deterministic gate still runs, and a Tradevisor-only symbol is never promoted.
- Not a claim that O'Pip can query Tradevisor. O'Pip only receives alerts.
- Not an automatic execution system. Every recommendation ends with
  `Execution: Human approval required`.

## Observation provenance, native context and advisory observations

### Provenance: what O'Pip can and cannot claim

The Tradevisor V2 indicator is an **external, invite-only TradingView script**.
Its Pine source is not in this repository and is not available to O'Pip, so no
inbound field can prove what it computed.

| Recorded field | Meaning |
| --- | --- |
| `integration_version` | Version of **this** integration contract, from `OPIP_TRADEVISOR_INTEGRATION_VERSION` (default `tradevisor-ingress-v1`). Ours, operator-declared. |
| `external_indicator` | The vendor identity the alert asserts (`TRADEVISOR_V2`, a literal in the schema). |
| `declared_indicator_version` | Optional `indicator_version` string from the alert body. |
| `declared_version_verified` | Always `false`: a body string is a claim, not evidence. |
| `algorithm_verification` | Always `UNVERIFIED_EXTERNAL_SCRIPT`. |

Provenance is **never** part of the replay identity: `dedup_id` is computed from
source facts (source, indicator, exchange, symbol, timeframe, direction, bar
open) only, so a re-delivery cannot create a second observation and a changed
version string cannot defeat idempotency. Adding `indicator_version` is
backward compatible — existing alerts without it keep working, and an invalid
value is refused by the schema before any row is written.

### Native context (read-only)

Every evaluation attaches a `native_context` block. Each field records its
`source`, `status`, `observed_at`, `freshness_seconds`, `reason` and
`limitations`; an unavailable component is never reported as corroboration.

| Field | Owner | Notes |
| --- | --- | --- |
| `btc_regime` | `app.scanner.market_scanner.analyze_symbol` | BTC's own completed-1H EMA stack (`OPIP_TRADEVISOR_BTC_MARKET`, default `XBTUSD`). Not broad-market breadth. |
| `market_breadth` | `app.scanner.market_regime.evaluate_market_regime` | Refuses below 30 validated snapshots, so a single-symbol evaluation reports `UNAVAILABLE` with that reason. |
| `chase_risk` | `app.services.chase_risk.assess_chase_risk` | Band/score/late-entry; unsupplied inputs are disclosed as `INPUT_NOT_SUPPLIED:*` limitations. Advisory only. |
| `liquidity` | `app.scanner.execution_validation.evaluate_execution` | Executable spread, depth coverage and round-trip drag already measured for this evaluation. |
| `signal_quality` | `app.services.signal_scoring` | **Incompatible**: needs the scan's `SymbolFeatures`, so it reports `UNAVAILABLE` rather than an approximation. |
| `opportunity_ranking` | `app.scanner.intelligence_ranking` | **Incompatible**: ranks a candidate set, not one observation. |

`instrument_coverage()` classifies each configured symbol as `SUPPORTED`,
`UNSUPPORTED` or `UNVERIFIED_NO_VENUE_CATALOGUE`. No exchange client is created
for it, so support is never assumed; a mistyped mapping stays visible instead of
silently becoming a permanent `MARKET_DATA_UNAVAILABLE`.

### Economic qualification uses the permitted allocation

The F5 economic gate is evaluated at the account envelope
(`equity × 20%`) to size the candidate, and then **re-evaluated by the same F5
owner at the F7-permitted allocation**. The number that qualifies is the number
that is displayed; no threshold, fee or ratio changes. A candidate that clears
the minimum net profit only at capital F7 will not permit is withheld with
`ECONOMIC_REJECT_AT_FINAL_ALLOCATION` and reports no economic pass. Determinism
is unchanged (the same inputs replay byte-identically).

### Advisory observations (opt-in)

A candidate that passes the market, attainability and economic gates but is
withheld by the F6 or 4H authority gate is reported as a **separate message
class** rather than silence:

```
TRADEVISOR OBSERVATION — WATCH / NO TRADE
...
NO TRADE RECOMMENDATION — no entry, stop, target or size is implied.
```

Properties: one bounded digest per drain run (max 5 items, remainder counted),
deduplicated and rate-limited by the existing notification policy, delivered
through the existing Telegram transport and ledger, never labelled qualified, and
structurally unable to carry an entry/stop/target because the formatter refuses
any instruction-like phrase. It is **disabled by default**
(`OPIP_TRADEVISOR_OBSERVATION_ENABLED=false`) and does not affect F11 protection
priority.

## Verified external facts (not assumptions)
Checked against TradingView's own help centre:

| Fact | Value |
| --- | --- |
| Webhook source IPs | `52.89.214.238`, `34.212.75.30`, `54.218.53.128`, `52.32.178.7` |
| Allowed ports | 80 and 443 only |
| Server response budget | **3 seconds** (a slower response is cancelled) |
| IPv6 | Not supported for webhooks |
| Custom headers | **Not offered** — the alert message body is the only request content the user controls |
| HTTP Basic auth in the URL | **Not offered** (`user:pass@host` is not a supported webhook URL form) |
| SSL client certificate | **Offered** — TradingView presents a client certificate on HTTPS requests; verification is done entirely by the *recipient* (see [Certificate authentication](#certificate-authentication)) |
| `{{time}}` | The triggered **bar's** time, UTC, `yyyy-MM-ddTHH:mm:ssZ` |
| `{{timenow}}` | The alert **fire** time, UTC, same format |
| `{{interval}}` | The chart interval; **`60`** for 1H, and it degenerates to `1` for price-based alerts |
| `{{ticker}}` / `{{exchange}}` | Symbol ticker and exchange (delayed feeds append `_DL`) |

Consequences baked into this implementation:

- `{{time}}` is treated as the **bar open**. Bar close is derived as
  `open + interval`. A trigger earlier than the derived close is refused, so an
  unfinished bar can never be mistaken for closed evidence.
- Because `{{interval}}` can return `1` for a price-based alert, any interval
  other than the configured one is refused.
- Because TradingView offers no custom header and no URL credentials, the only
  channel for a shared secret is the **alert message body**. That is why this
  implementation carries `ingest_token` in the body, and why the body-token
  threat model below matters more than the mechanism itself.
- TradingView's own documentation *does* offer a cryptographic mechanism — the
  SSL client certificate. This implementation does not verify it yet; that gap
  is stated explicitly in [Certificate authentication](#certificate-authentication)
  and is a staging-verification prerequisite, not a passing claim.
- Because the response budget is 3 seconds, the acknowledgement performs no
  market, AI or Telegram call. `p99 < 1s` is the design target.

## Trust model

Three independent checks, none of which the payload can assert about itself:

1. **Immediate peer** must be the trusted edge (`OPIP_TRADEVISOR_TRUSTED_PROXY_IPS`).
2. **Edge stamp**: the proxy adds `x-ohm-tradevisor-verification` whose value
   must equal `OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE`. This header is
   deliberately different from the Wave 8.2 bridge's header.
3. **Ingestion token**: `ingest_token` inside the body must equal
   `OPIP_TRADEVISOR_WEBHOOK_TOKEN`. It is required because every TradingView
   customer shares the same source IPs. It is removed before validation and is
   never persisted or logged.

`alert_config_version` binds an alert identity to a direction. It is *not*
independent proof of authenticity: it only prevents a BUY-labelled alert from
claiming SELL semantics.

In production, `Settings` refuses to boot if the verification value is the
documented default or shorter than 32 characters, or if the token is shorter
than 43 URL-safe characters.
### Certificate authentication

Source: TradingView, *Webhook authentication* —
<https://www.tradingview.com/support/solutions/43000680459-webhook-authentication/>

TradingView states that, when sending a webhook request, its server also sends an
**SSL certificate to the specified URL**, and that this certificate can be used
to verify the request really came from the TradingView server. The documented
facts:

| Item | Documented value |
| --- | --- |
| Transport | Sent **only** when the webhook URL uses HTTPS |
| Who verifies | Entirely the **recipient** application; nothing is enabled on the TradingView website |
| Subject | `C = US`, `ST = Ohio`, `L = Westerville`, `O = TradingView, Inc.`, `CN = webhook-server@tradingview.com` |
| Subject Alternative Name | `email:webhook-server@tradingview.com` |

**Status in this implementation: NOT IMPLEMENTED and NOT CERTIFIED.** The edge
example in `deploy/nginx/tradevisor-webhook.conf.example` terminates TLS but
does not request or validate a client certificate, and the FastAPI route never
sees one. Therefore:

- the documented certificate mechanism is an **available, stronger** authenticity
  control that this change does not yet use;
- authenticity today rests on peer allowlist + edge stamp + body token;
- no document in this change may claim certificate-based provenance.

If adopted later, verification belongs at the TLS-terminating edge
(`ssl_verify_client on` + `ssl_client_ca_certificate` for the TradingView CA, or
an equivalent certificate check in front of the origin, forwarding the verified
result as a distinct edge stamp). It must not be added to the application layer,
and it must not be treated as a substitute for the existing checks — it is an
additional layer.

### Body-token threat model

Because TradingView offers no custom header and no URL credentials, the shared
secret has to travel in the alert message body. TradingView's own webhook
configuration documentation warns that sensitive information such as login
credentials or passwords **should not be placed in the webhook body**. This
design therefore accepts a documented, explicitly-discouraged trade-off:

| Threat | Exposure created by a body token | Mitigation actually in place |
| --- | --- | --- |
| Token readable by anyone with access to the TradingView account | The token is stored in clear text in the alert message and is visible in the alert editor | TradingView requires 2FA on accounts that use webhook alerts; the token is a scoped ingestion value with no order, exchange or admin authority |
| Token readable by any party on the delivery path | Body content leaves TradingView and crosses the public internet to the edge | HTTPS only (ports 80/443 accepted; this route must be 443); certificate mechanism available but unverified |
| Token captured from a proxy, log or APM trace | Body logging would leak the credential | The token is removed from the payload before validation, is never persisted, and failure reasons record exception types only (`webhook::test_atdd12_ingest_token_is_never_persisted`) |
| Token replayed by an unauthorized sender | A stolen token plus a spoofed source IP would authenticate | Source-IP allowlist at the edge **and** in the app; freshness window; bar-close derivation; source-facts-only `dedup_id` |
| Token copied into source, docs, tickets | Permanent credential leak | Never committed; supplied only through the approved secret mechanism; the documented placeholder is refused at boot |
| Long-lived token, no rotation | Indefinite exposure window | Rotation is an owner action; the value is read from settings only, so rotation needs no code change |

Residual risk, stated plainly: **TLS alone does not prove the sender is
TradingView.** Source IPs are shared by every TradingView customer, so an
attacker holding the body token and able to originate traffic that the edge
accepts could submit an alert. Even then the submission cannot place, modify,
cancel or confirm an order, cannot admit a symbol into native selection, and
still passes every deterministic risk, feasibility and economic gate. The
ceiling of a successful forgery is an **unwanted advisory notification**, not
trading authority.

### Required staging verification

None of the following has been performed. All are prerequisites for any
production consideration and must be executed on a staging host:

1. **Certificate observation** — capture a genuine delivery and confirm the
   presented client certificate subject/SAN matches the documented values.
2. **Certificate enforcement** — enable recipient-side verification and prove a
   request without a valid client certificate is rejected at the edge.
3. **Token-in-body confirmation** — confirm the token arrives intact and that no
   intermediate component (proxy, ingress, APM) logs the body.
4. **Redaction check** — confirm persisted rows and application logs contain no
   token material after a genuine delivery.
5. **Authenticity-negative** — attempt a delivery from an unlisted address and
   from a listed address with a wrong token, and confirm both are refused before
   any schema detail is disclosed.
6. **Rotation drill** — rotate the token and verification value and confirm the
   dark-then-lit behaviour and that no stale value is honoured.



## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `OPIP_TRADEVISOR_ENABLED` | `false` | Master switch. Off = 404 + inert cycle phase. |
| `OPIP_TRADEVISOR_TRUSTED_PROXY_IPS` | `127.0.0.1,::1` | Only peer(s) allowed to call the route |
| `OPIP_TRADEVISOR_INTERNAL_VERIFICATION_VALUE` | documented placeholder | Edge stamp; rotate before production |
| `OPIP_TRADEVISOR_WEBHOOK_TOKEN` | unset | Ingestion token (≥43 URL-safe chars) |
| `OPIP_TRADEVISOR_MAX_PAYLOAD_BYTES` | `8192` | Hard body cap |
| `OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE` | `60` | In-process limiter (defense in depth) |
| `OPIP_TRADEVISOR_INTERVAL_MINUTES` | `60` | Required bar interval |
| `OPIP_TRADEVISOR_ALLOWED_EXCHANGES` | `COINBASE` | Source venue allowlist |
| `OPIP_TRADEVISOR_ALERT_CONFIGS` | `tradevisor-1h-buy=BUY,tradevisor-1h-sell=SELL` | Alert identity → direction |
| `OPIP_TRADEVISOR_INSTRUMENTS` | 10 Coinbase pairs | `EXCHANGE:SYMBOL=INSTRUMENT:MARKET` |
| `OPIP_TRADEVISOR_MAX_EVENT_AGE_SECONDS` | `300` | Freshness window |
| `OPIP_TRADEVISOR_CLOSE_TOLERANCE_SECONDS` | `60` | Allowed trigger slack before close |
| `OPIP_TRADEVISOR_FUTURE_SKEW_SECONDS` | `5` | Clock-skew tolerance |
| `OPIP_TRADEVISOR_MAX_PRICE_DIVERGENCE_PCT` | `1.0` | Source vs execution-venue price |
| `OPIP_TRADEVISOR_MAX_ATTEMPTS` | `5` | Attempts before `POISONED` |
| `OPIP_TRADEVISOR_RETENTION_SECONDS` | 7 days | Terminal-row retention |
| `OPIP_TRADEVISOR_PROCESSING_TIMEOUT_SECONDS` | `120` | Lease before reclaim |
| `OPIP_TRADEVISOR_PROCESSING_BATCH_LIMIT` | `2` | Events per drain batch |
| `OPIP_TRADEVISOR_MAX_INBOX_EVENTS` | `2000` | Bounded durable capacity |
| `OPIP_TRADEVISOR_ACTIONABLE_EXPIRY_MINUTES` | `90` | Recommendation lifetime |

Default instrument table (every entry is an explicit operator decision, no
USDT/USD parity is inferred):

```
COINBASE:BTCUSD=BTC/USD:XBTUSD    COINBASE:ETHUSD=ETH/USD:ETHUSD
COINBASE:SOLUSD=SOL/USD:SOLUSD    COINBASE:AVAXUSD=AVAX/USD:AVAXUSD
COINBASE:DOGEUSD=DOGE/USD:XDGUSD  COINBASE:XRPUSD=XRP/USD:XRPUSD
COINBASE:NEARUSD=NEAR/USD:NEARUSD COINBASE:MANAUSD=MANA/USD:MANAUSD
COINBASE:LINKUSD=LINK/USD:LINKUSD COINBASE:ADAUSD=ADA/USD:ADAUSD
```

Adding a Binance `SOLUSDT` alert requires an explicit
`BINANCE:SOLUSDT=SOL/USD:SOLUSD` row **and** adding `BINANCE` to
`OPIP_TRADEVISOR_ALLOWED_EXCHANGES`. Nothing is derived automatically.

## TradingView alert setup

Create **one alert per symbol and per condition**. A watchlist-wide alert that
applies Tradevisor V2 conditions across many symbols has **not been verified**
and this implementation assumes nothing about it.

For each configured symbol (e.g. `COINBASE:BTCUSD` on the **1 hour** chart):

1. Condition: **Tradevisor V2 → Buy** (and a second alert for **Sell**).
2. Interval: **1 hour** — must match `OPIP_TRADEVISOR_INTERVAL_MINUTES`.
3. Trigger: **Once Per Bar Close**.
4. Expiration/24-7: notifications must be allowed to fire around the clock.
5. Webhook URL: `https://<your-host>/webhooks/tradevisor/v1` (port 443).
6. Message: the JSON below, with `REPLACE_WITH_INGEST_TOKEN` replaced by the
   deployment's value.

BUY message:

```json
{"schema_version":"tradevisor-ingress-v1","source":"TRADEVISOR","indicator":"TRADEVISOR_V2","signal":"BUY","symbol":"{{ticker}}","exchange":"{{exchange}}","timeframe":"{{interval}}","signal_price":"{{close}}","bar_opened_at":"{{time}}","triggered_at":"{{timenow}}","alert_config_version":"tradevisor-1h-buy","ingest_token":"REPLACE_WITH_INGEST_TOKEN"}
```

SELL message (identical except two fields):

```json
{"schema_version":"tradevisor-ingress-v1","source":"TRADEVISOR","indicator":"TRADEVISOR_V2","signal":"SELL","symbol":"{{ticker}}","exchange":"{{exchange}}","timeframe":"{{interval}}","signal_price":"{{close}}","bar_opened_at":"{{time}}","triggered_at":"{{timenow}}","alert_config_version":"tradevisor-1h-sell","ingest_token":"REPLACE_WITH_INGEST_TOKEN"}
```

**Once Per Bar Close reduces, but does not eliminate, repainting.** This
implementation does not rely on it: the bar-open/bar-close derivation and the
freshness window independently reject an unfinished or stale bar.

## How a signal moves through O'Pip

1. **Receive** — edge proxy → `POST /webhooks/tradevisor/v1` → origin, edge
   stamp and token checks → strict schema → normalized signal.
2. **Record** — durably enqueued with a stable `dedup_id` (source facts only,
   receipt time excluded). Acknowledgement means *durably received*.
3. **Evaluate** (cycle phase `TRADEVISOR_ADVISORY`, last phase) — native
   evidence via the scanner, execution spread/depth via public Kraken data,
   then the existing R4 geometry kernel, F5 target attainability, F5 economic
   quality, F7 capital allocation and exposure capacity.
4. **Decide** — `ENTER NOW`, `SET LIMIT ENTRY`, `WATCH`, `NO TRADE` or
   `EXPIRED`. Only the first two would be actionable, but no path currently
   reaches them: missing F6 always forces `WATCH` and missing completed 4H
   additionally blocks SWING. The proposed entry decision is frozen as
`shadow_proposed_action`, not presented to the operator as authorization.
5. **Notify** — actionable recommendations are rendered and delivered through
   the existing Telegram transport and notification policy. Non-actionable
   observations are recorded, not broadcast.
6. **Learn** — the frozen decision record is measured prospectively. An
   explicitly tagged `SHADOW_ONLY` hypothesis may be measured using later
   candles as `SHADOW_HYPOTHESIS`, with `fill_attribution=NONE`; arbitrary WATCH
   rejections are not treated as hypothetical trades. Neither candle touches
   nor estimated net results are realized executions or permission to trade.
   No policy is promoted automatically.

### SELL semantics

A SELL alert is bearish/exit evidence only:

- no spot short is created (this path trades unleveraged spot LONG only);
- no funded position is closed;
- the event is recorded with `short_order = NOT_PERMITTED`.

### Horizons

`TACTICAL` (ATR% ≥ 4), `SWING` (1.5 ≤ ATR% < 4), `POSITION` (< 1.5). A 1H
source signal is **not** automatically classified SWING; the horizon comes from
native volatility evidence, not from the source timeframe.



## Evidence lifecycle and retention

Receipt, evaluation, learning eligibility, measurement, consumption and safe
retention are **separate** lifecycle events. Conflating them loses evidence.

| Stage | Recorded as | Does *not* prove |
| --- | --- | --- |
| Receipt | a durable inbox row | that the alert was authorized |
| Evaluation | `status = PROCESSED` / `REJECTED` | that learning consumed it |
| Eligibility | a frozen `decision_record` | that a measurement exists |
| Measurement | outcome `MEASURED` / `NO_ENTRY_TOUCH` | that the outcome was recorded anywhere |
| Consumption | `row["consumption"]` acknowledgement | policy promotion |
| Safe retention | `_retention_eligible()` | — |

**Retention is fail-closed.** A row is pruned only when it is either in a
non-learning terminal state (`POISONED`, which never produced a frozen decision)
or carries a valid durable acknowledgement: exact `dedup_id`, allowed
disposition, a **definitive** measurement status, and the **current** release
SHA. Missing, malformed, drifted, unverified or non-definitive acknowledgements
all block deletion. Duplicate webhook delivery never alters consumption state,
and retention preserves claim tokens, decisions, notification state and retry
counters.

**Operational consequence.** No deployment unit runs
`report_tradevisor_learning`, and the learning worker may not write back into
production storage, so nothing records an acknowledgement in production today.
Eligible evidence is therefore retained indefinitely; the inbox will reach its
bounded capacity and the webhook will answer `503` rather than evict evidence.
Watch `inbox_status()`: `unconsumed`, `retention_blocked`,
`oldest_unconsumed_age_seconds`, `capacity_backpressure_events` (aggregate only).
Enabling safe pruning requires an owner-approved acknowledgement writer or a
durable archival handoff — a separate decision, deliberately not taken here.

## Outcome measurement coverage

A definitive outcome requires provably complete observation.

With `t0` = decision time, `H` = measurement duration, `t1 = t0 + H` and `Δ` =
candle interval, the expected bars are the UTC epoch multiples of `Δ` starting
**strictly after** `t0`, each closing at or before `t1`. A decision at 10:23 UTC
makes 11:00 the first eligible bar; the 10:00–11:00 bar carries pre-decision
prices and is never used. Boundary remainders are recorded
(`excluded_lead_seconds`, `excluded_tail_seconds`) rather than papered over: this
is complete post-decision coverage, not continuous wall-clock coverage.

If any expected bar is missing, duplicated with conflicting prices, misaligned to
the grid, unfinished, outside the window, or carries non-finite OHLC, the outcome
is `PARTIAL_COVERAGE` — never `MEASURED` and never the definitive
`NO_ENTRY_TOUCH`. Partial coverage contributes nothing to measured counts,
false-positive rates or estimated profitability. Identical duplicate bars
collapse; unordered responses are sorted. A price touch is always evidence about
price, never a fill (`fill_attribution = NONE_TOUCH_INFERRED`).

Existing rows written before this change carry no `consumption` field. They are
treated as **not consumed** and remain protected — missing metadata never
defaults to "consumed".


## Addendum - presentation, allocation and discovery hardening (2026-10-24)

### An observation never quotes an internal instruction

Internal decision text names its own proposal (`ENTER_NOW`, `SET_LIMIT_ENTRY`).
That vocabulary is correct in the durable record and wrong in an operator
message, so the observation is rendered from neutralised operator language
(`_operator_language`) and a guard refuses any text that still contains an
instruction phrase. The durable `decision_record` and
`evidence.shadow_proposed_action` are untouched: only the presentation copy
differs, and the replay identity is unchanged.

### The final-allocation economic call is bounded, and proved to be

Re-running F5 at the F7 allocation with `max_capital_fraction=1.0` is safe
because the value handed in is F7's own output, which is already capped by
`max_position_pct` (the same 20% constant) and by the fixed per-trade risk
budget. The composition does not trust that contract: it asserts it. A
`capital_consistency` block records the status, allocation, risk, position-cap
limit and risk budget, and anything unusable is rejected explicitly:

| Situation | Rejection |
| --- | --- |
| Allocation exceeds the position cap or the risk budget | `CAPITAL_INCONSISTENT` |
| Allocation is NaN or infinite | `CAPITAL_INCONSISTENT` (no exception escapes) |
| Allocation is zero or negative | `CAPITAL_REJECT` |
| Allocation is below the executable minimum | `CAPITAL_BELOW_EXECUTABLE_MINIMUM` |
| Final net profit below the unchanged minimum | `ECONOMIC_REJECT_AT_FINAL_ALLOCATION` |

No economic PASS is ever reported when there is no usable allocation to
qualify against; the reported `economics.qualified` is false in every rejected
case above.

### Discovery coverage, and why ranking is not reused

`discovery_coverage()` reports four separate populations from data the operator
already has:

| Population | Source |
| --- | --- |
| Discovered by O'Pip | eligible markets from the native scan universe, passed in |
| Configured for an external alert | `OPIP_TRADEVISOR_INSTRUMENTS` |
| Actually observed | markets in durable Tradevisor inbox rows |
| Native with no TradingView coverage | universe minus configured |

It performs no network call and never creates a signal: `native_markets` is
supplied, and `observed_markets_from_records()` reads only what the inbox
already holds. A missing universe is reported as `UNAVAILABLE`, not assumed.

Native opportunity ranking (`app.scanner.intelligence_ranking`) is **not**
reused and is reported as `UNAVAILABLE` with reason
`REQUIRES_SCAN_CANDIDATE_SET`. Ranking orders the native scan's candidate set;
placing an externally claimed observation into that ordering would let it
compete for native admission. That is an authority decision for the owner, not
an integration detail, so it is documented here and was not implemented.

## Addendum - durable advisory delivery and evidence hardening (2026-10-24)

### Observations are delivered from durable rows, not from memory

A settled inbox row already holds the frozen decision, so it is the queue. The
drain no longer carries candidates in memory between settlement and send:

1. `pending_observations()` re-derives eligible, unreported observations from the
   existing durable rows (read-only, bounded recovery window).
2. `send_observation_digest()` sends at most one bounded digest.
3. Only a confirmed delivery writes a durable per-row acknowledgement
   (`TRADEVISOR:OBSERVED:<dedup_id>`) into the existing notification-policy
   state file.

Consequences: a crash after settlement, a refused transport, a transport
exception and a cooldown suppression all leave the rows eligible, so the next
cycle recovers them; a confirmed delivery can never be repeated; more items than
one digest carries roll forward after the cooldown. No new queue, database or
scheduler is introduced, and the row's learning-consumption and retention
semantics are untouched.

### Provenance survives the durable round trip

The inbox serializer now persists `signal.provenance`. A row reloaded for worker
evaluation or crash reclamation reproduces the provenance exactly, a legacy row
written before this change reloads as unknown provenance (never as a verified
claim), and the dedup identity is unchanged.

### BTC context needs identity and verified freshness

A returned trend is not evidence. The BTC field is AVAILABLE only when the
snapshot's symbol matches the requested market, market-data validation is present
and qualified, and that validation carries a source candle timestamp. The
reported observation time is that candle time - evaluation time is never
substituted. Anything else is `UNAVAILABLE` with a specific reason
(`MARKET_IDENTITY_MISMATCH`, `NO_MARKET_DATA_VALIDATION`,
`MARKET_DATA_UNQUALIFIED`, `NO_SOURCE_TIMESTAMP`).

### Chase risk discloses partial inputs

The native chase-risk owner remains authoritative and its formulas are not
reimplemented. When only part of its input set is supplied, the record now
carries `input_completeness` (provided/total/missing groups), marks the
conclusion `INDETERMINATE_PARTIAL_INPUTS` and the band indicative-only, and the
operator line says `partial inputs N/M - indicative only`. A partial low band is
never presented as a complete clearance.

### Observation cadence is reconciled with the notification policy

`OPIP_TRADEVISOR_OBSERVATION_MIN_INTERVAL_MINUTES` is now applied as the digest
cooldown, and the stricter of that value and the shared policy cooldown always
wins: an operator can lengthen the cadence but can never make O'Pip chattier
than the shared policy allows. A suppressed attempt reports
`retry_after_seconds` and marks nothing, so the observation stays eligible.

### Every unusable F7 result fails closed

The composition reads F7's output without trusting its shape and reports a
machine-readable `reason_code`:

| Condition | Status | Reason code |
| --- | --- | --- |
| Missing required fields, or the owner returned `None`/raised | `CAPITAL_INCONSISTENT` | `MISSING_ALLOCATION_FIELDS` |
| Non-numeric field | `CAPITAL_INCONSISTENT` | `NON_NUMERIC_ALLOCATION` |
| NaN or infinite value | `CAPITAL_INCONSISTENT` | `NON_FINITE_ALLOCATION` |
| Negative risk dollars | `CAPITAL_INCONSISTENT` | `NEGATIVE_RISK` |
| Over the position cap or the risk budget | `CAPITAL_INCONSISTENT` | `OVERSIZED_ALLOCATION` |
| Position percentage disagrees with the capital | `CAPITAL_INCONSISTENT` | `POSITION_PCT_INCONSISTENT` |
| Zero or negative capital | `CAPITAL_REJECT` | `NON_POSITIVE_CAPITAL` |
| Below the executable minimum | `CAPITAL_BELOW_EXECUTABLE_MINIMUM` | `BELOW_EXECUTABLE_MINIMUM` |

No exception escapes the evaluator, no economic PASS is reported in any of
these cases, and no F5 or F7 algorithm, threshold or risk limit is changed.

## Addendum - delivery guarantee, recovery horizon and marker lifecycle (2026-10-24)

### Stated delivery guarantee

The observation lane is **at-least-once for unknown outcomes, and at-most-once
for content already confirmed as delivered**. It is **not** exactly-once: the
Telegram transport offers no idempotency key that the receiver honours, so an
outcome of "request written, response lost" cannot be distinguished from "never
written". The implementation chooses the safe side of that ambiguity:

| Outcome | Behaviour | Consequence |
| --- | --- | --- |
| Crash before the transport call | Row stays unacknowledged, recovered next cycle | delivered once |
| Transport refused (`delivered=false`) | Reservation released, row pending | retried |
| Transport raised (timeout, network) | `SEND_FAILED`, row pending | retried, **may duplicate** |
| Delivered, acknowledgement lost | Next cycle recognises the confirmed emission from the policy state, repairs the marker, sends nothing | delivered once |
| Cooldown or budget suppression | Nothing marked, row pending | retried after the window |
| Reservation holder died | Lease expires, next worker proceeds | delivered once |

`STATUS_ALREADY_DELIVERED` is the observable signal for the repair path: the
content was already confirmed as sent, so the missing per-row acknowledgement is
written and no second message is emitted.

### Recovery horizon is sized to the drain, not to a guess

A backlog can only drain one digest per cooldown, carrying at most five rows. The
horizon is therefore `max(6h, 5 x cooldown)` - enough for a full per-call backlog
(four digests) plus one cycle of slack. The previous `2 x cooldown + 1h` horizon
(13h by default) was **shorter than the 14h a twelve-row backlog needs**, so the
tail of a sustained backlog expired before it could be delivered.

`observation_backlog()` reports, read-only, how every durable row is accounted
for: `eligible`, `already_reported`, `aged_out`, `undated`, `not_observable`,
`total_rows` and the active `window_minutes`. A non-zero `aged_out` is the signal
that evidence is outrunning the drain; nothing is ever dropped silently.

### Marker lifecycle

Acknowledgement markers are pruned on the acknowledgement path itself (and by
`compact_markers()`), keeping 30 days of history. Only marker keys are touched -
digest, alert and budget entries in the same state file are never modified. A
digest of N rows still consumes exactly **one** noncritical attention slot,
because acknowledgements are bookkeeping rather than new cards.

### Lookup cost, and the shared-state limit

The recovery scan reads the notification state **once per pass** and filters rows
by membership, so its cost is `O(rows + state)` rather than `O(rows × state)`.
Measured on a developer host (median of repeated runs): with 3,600 markers a
2,000-row scan takes ~25 ms instead of ~10.5 s; with 20,000 markers ~52 ms
instead of ~56 s. There is no zero-overhead claim: a scan still builds one
candidate per row, and the single state parse still costs ~0.1 ms (empty),
~4.9 ms (3,600 markers) and ~31 ms (20,000 markers).

The markers deliberately live in the existing notification-policy state, so the
protection path still parses that file per decision. At the governed cadence the
population is bounded by roughly 120 acknowledgements/day over the 30-day
retention (~3,600 keys, ~400 KB). **Known limit:** if observation volume grows
well beyond that, every protection decision inherits the larger parse. Isolating
observation markers into their own store is a real design change and is
deliberately **not** implemented here: it would need its own owner-approved
increment, its own durability/lock contract and its own fail-closed review. No
new persistence subsystem was introduced by this integration.

### Digests are bounded by the transport contract

Telegram rejects a message longer than 4,096 UTF-16 code units, and the
transport wrapper forwards this message family unchanged (its legacy compaction
only rewrites the old `OHM CHIEF` layout). The digest is therefore bounded
before any reservation: the renderer keeps the largest deterministic prefix of
eligible observations whose fully detailed rendering fits
`DIGEST_MESSAGE_BUDGET` (3,800 UTF-16 units, a deliberate margin), reducing the
**number** of observations rather than shortening any included one. A single
record that cannot fit at full detail is rendered compactly (instrument, market,
bar close, rejection class and an explicit omission note). If even that exceeds
the budget nothing is sent: `MESSAGE_TOO_LONG` is returned with no reservation,
confirmation or acknowledgement.

Fingerprint, reservation, transport record and durable acknowledgement are
computed from exactly the rows in the rendered digest, so excluded rows stay
pending and are delivered once eligible. This bounds a message; it changes no
trading, risk or authority behaviour.

### Account risk is reported against total equity

F7's allocation remains the economic notional. The final F5 call receives total
equity as available capital with the F7-approved fraction
(`allocation / equity`), which keeps `recommended_capital`, `position_notional`,
target profits, costs and the qualification/R:R thresholds identical while
making `account_risk_at_stop_pct` the stop-loss share of the account rather than
the raw stop distance. A non-finite, zero or negative equity denominator is
refused as `CAPITAL_INCONSISTENT / INVALID_EQUITY_DENOMINATOR`; it is never
divided by.

### One body, one meaning

The ingress body is parsed exactly once, and an ambiguous body is refused
rather than resolved. Two parser-level cases were hardened after review:

* **Duplicate JSON keys** are rejected (`_object_without_duplicate_keys`). The
  standard parser keeps the last occurrence, so an edge that inspected the
  first `signal`, `symbol` or `alert_config_version` and the application could
  disagree about what was authenticated. Such a body now gets the same opaque
  404 as malformed JSON, before any schema detail is disclosed.
* **Deeply nested bodies** (a few kilobytes of nested arrays stay inside the
  8 KiB cap but exhaust the parser stack) are refused with the same 404 instead
  of surfacing a server error.

`signal_price` additionally refuses JSON booleans. Without that guard
`"signal_price": true` coerced to `1.0` and produced a plausible-looking quote
that only failed later at the cross-venue divergence gate, which masked the real
schema violation.

## Two edge deployment modes (this integration ships mode 1 only)

The webhook boundary has exactly two supported shapes. They are **not
interchangeable**, and neither is activated by this repository.

| | Mode 1 — DNS-only (shipped) | Mode 2 — Cloudflare-proxied (documented, not shipped) |
| --- | --- | --- |
| Path | TradingView → `tv-staging.opip.io` (DNS-only) → this edge | TradingView → Cloudflare → this edge |
| `$remote_addr` | the real TradingView source address | a **Cloudflare** address |
| Source allowlist | the four documented TradingView IPs (`allow` / `deny all`) | must be the published Cloudflare ranges; the real client is then a header, not an address |
| Client identity | the peer address is the identity | `CF-Connecting-IP`, accepted **only** from a verified Cloudflare peer; no other peer may assert it |
| Rate-limit identity | `$binary_remote_addr` is correct | keying on `$binary_remote_addr` would put every customer in one bucket; key on the validated client identity, or terminate the limit at Cloudflare |
| `X-Forwarded-For` | set to `$remote_addr` (replaces any client value) | must be rebuilt from the trusted chain, never copied from the request |
| Header spoofing | impossible through the edge: the stamp is a fixed literal | safe only if the origin is firewalled to Cloudflare and the stamp is still set by this proxy |
| Extra requirements | origin firewall limited to the four IPs; TLS ≥ 1.2; 8 KiB body cap; opaque 404 | origin reachable **only** through the tunnel/proxy (no public origin port); Cloudflare WAF and rate rules reviewed; the true-client header validated against Cloudflare ranges; a separate security review |
| Status | example reviewed; **not deployed** | **not implemented**; needs its own approved review |

`deploy/nginx/tradevisor-webhook.conf.example` implements mode 1 only. Enabling
Cloudflare's proxy in front of that file would silently reject every real alert, so
mode 2 is a **different configuration**, not a DNS toggle.

### Validation an operator must run (not run by this repository)

```bash
nginx -t -c /etc/nginx/nginx.conf
```

`tests/test_tradevisor_webhook_v1.py` contains an **environment-gated** test that
performs the same `nginx -t` check inside a temporary prefix with a throwaway
self-signed certificate. It is skipped when `nginx` or `openssl` is unavailable, so a
green local suite never implies that a real nginx accepted the file. The textual
assertions in that module are contract assertions over the example, not a runtime
certification.

No DNS, certificate, Cloudflare, firewall or nginx service change is authorized or
performed by this integration.


## Aggregate rate-limit contract

Two independent limiters protect this route, at different layers and with
different identities. Their arithmetic is not additive, and neither is a
per-source budget for the application:

| Layer | Limit | Identity | Applies to |
| --- | --- | --- | --- |
| nginx edge | **30 requests per minute per TradingView source** (`rate=30r/m`, `burst=20 nodelay`) | `$binary_remote_addr` — the real TradingView address in DNS-only mode | each of the four documented source addresses separately |
| FastAPI route | **60 requests per minute per immediate peer** (`OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE`) | the immediate peer — always the single trusted reverse proxy | **one shared bucket for all four sources** |

Consequences the operator must plan for:

* the edge admits up to four sources × 30/min ≈ 120/min in aggregate (plus the
  20-request bursts), but the application then admits only 60/min in
  **aggregate**, because every alert arrives from the same proxy address;
* the application limit is therefore the tighter, **aggregate** ceiling, and a
  429 from the route means "more than 60 alerts were offered to this edge in the
  last minute, across all sources", not "this source exceeded its own budget";
* **429** is intentional throttling (either layer). **503** is queue-capacity
  backpressure from the durable inbox, which is a different operation and must
  never be retried as though it were throttling;
* a request refused by either layer receives **no durable acceptance receipt**:
  no inbox row is created, and only the duplicate path returns a `duplicate`
  status for an alert that was already stored.

**Approved initial policy.** `60` per minute per peer is the owner-approved *initial*
shared policy and is preserved here, together with the edge's 30 per minute per source.
It is **not** a production-validated capacity target: no load or soak measurement has
established 60/min aggregate as sufficient for a four-source deployment. Raising the
limit, or giving each source its own budget, would change
`OPIP_TRADEVISOR_MAX_REQUESTS_PER_MINUTE` (and, for per-source fairness, the limiter's
key) and needs its own approved increment. This increment changes neither.

## TLS policy with multiple virtual hosts

TLS policy is selected per SNI host, and a client that sends no SNI — or an
unknown one — is served by the **default server** for that listen socket. The
webhook vhost is therefore declared `listen 443 ssl default_server`, so:

* the effective minimum for any handshake that reaches this edge is **TLS 1.2**,
  regardless of what other vhosts on the host allow;
* a host carrying a second `default_server` on `:443`, or an operator dropping
  this file onto a host that already has one, fails `nginx -t` with
  "duplicate default server" — a configuration error at validation time rather
  than a silent policy downgrade;
* this vhost remains a **dedicated edge**: the only other location is
  `location / { return 404; }`, and no dashboard, health or Wave 8.2 route is
  reachable from it.

The negotiated minimum can only be proved by a real handshake, not by reading the
file; `tests/test_tradevisor_edge_contract_v1.py` contains an **opt-in** handshake
test for that (`OPIP_NGINX_HANDSHAKE_TEST=1`, requires nginx and openssl).

