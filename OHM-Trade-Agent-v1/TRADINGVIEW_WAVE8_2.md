# Wave 8.2 — TradingView Intelligence Bridge

## What this is

A second, optional way for OHM to hear about a setup: a TradingView Pine
script watches a 15-minute chart and, when its own rules agree a setup
exists, POSTs a JSON payload to OHM. That's it. TradingView is **candidate
evidence only** — nothing more.

Concretely, that means:

- TradingView cannot place an order, confirm a fill, or touch Kraken in any
  way. OHM's execution model is unchanged: a human still confirms every
  entry and exit via Telegram, exactly as before this bridge existed.
- TradingView cannot override, weaken, or skip any of OHM's existing gates.
  Every signal that reaches this bridge still has to independently clear the
  same target-attainability, economic-quality, and margin-eligibility checks
  a native scanner candidate has to clear. A TradingView signal, by itself,
  is never sufficient to produce an alert.
- The whole bridge is off by default (`TRADINGVIEW_V2_ENABLED=false`). Until
  an operator deliberately turns it on and configures the network boundary
  below, nothing described in this document runs.

## Why it exists

OHM's native scanner already has a 15-minute-bar-resolution blind spot: it
polls Kraken on its own schedule rather than reacting to a bar close the
instant it happens. This bridge lets a TradingView indicator, which *does*
see every bar close in real time, hand OHM a second, corroborating opinion
sooner and attach a second, independently delivered observation to a
candidate the native scanner also admits. It cannot promote a symbol or
direction the native selector rejected.

## How a signal moves through the system

1. **Pine script → webhook.** `tradingview/ohm_signal_indicator_v1.pine`,
   applied to a 15m chart, computes a 4H regime, a 1H setup type, and a 15m
   entry trigger, then calls `alert()` with a JSON body once a bar closes
   confirming a LONG or SHORT bias. See that file's header for the exact
   rules and the *temporal consistency* note — read it before changing the
   scoring logic.
2. **Network and deployment authentication.** TradingView cannot be
   configured to send a custom HTTP header, so a reverse proxy
   reverse proxy (`deploy/nginx/tradingview-webhook.conf.example`) is the
   only thing allowed to reach OHM's `/webhooks/tradingview/v2` route
   directly, restricted to TradingView's own published webhook source IPs,
   and stamps every forwarded request with an internal verification header
   OHM checks against `TRADINGVIEW_INTERNAL_VERIFICATION_VALUE`. Belt and
   suspenders: OHM also checks the immediate connection's source IP against
   `TRADINGVIEW_TRUSTED_PROXY_IPS` (default `127.0.0.1,::1`), so even a
   process on the same host that isn't going through the proxy is refused.
   Because those source IPs are shared by all TradingView customers, the Pine
   payload must also carry a unique, URL-safe `verification_token` matching
   `TRADINGVIEW_WEBHOOK_TOKEN`. OHM verifies and removes that token before
   schema validation; it is never persisted in the inbox or Chief context.
3. **Validation and durable queueing** (`app/services/tradingview_inbox.py`).
   The payload is schema-validated (`app.models.signal.TradingViewSignalV2`,
   `extra="forbid"`, every numeric field finite, warnings length- and
   character-bounded), checked for staleness/future-dating, and deduplicated
   by a hash of signal id + pair + direction + bar close time. A second,
   independent replay defense rejects a *new* signal id for the same
   pair/direction arriving less than one 15-minute bar after the last
   accepted one — closing the gap where an old, already-accepted payload
   could be resent with only its timestamp bumped to dodge the exact-match
   dedup hash. Accepted events are written to a durable, file-locked registry
   (`/app/data/tradingview_v2_inbox.json`) and return `202 Accepted`
   immediately; nothing else happens synchronously inside the HTTP request.
4. **Revalidation and qualification.** A later native cycle
   (`process_queued_events()`, wired into `app/jobs/run_cycle.py` right
   before the scan step) re-checks each queued event from scratch: schema
   version, freshness, and — critically — the TradingView-reported price
   against a live Kraken ticker (`TRADINGVIEW_MAX_PRICE_DIVERGENCE_PCT`,
   default 1%). It also checks the current operator mode; if OHM is not in
   `SEARCH` (quiet hours, MAINTENANCE, capacity cooldown, etc.), the event is
   rejected for this pass rather than silently held past its relevance
   window. Only after clearing all of that is an event marked
   `QUALIFIED_FOR_NATIVE_CYCLE`.
5. **Merging into the native cycle** (`app/jobs/scan_opportunities.py`).
   Qualified evidence is used in one purely additive way:
   - `merge_native_candidate_evidence()` tags a native candidate that
     independently qualified this cycle with the matching TradingView
     evidence as context — it never changes that candidate's own score,
     direction, or whether it was selected.
   `tradingview_only_candidates()` remains as a compatibility seam but always
   returns an empty list. Promoting a rejected snapshot would bypass at least
   one native selector invariant (asset de-duplication, directional choice,
   per-direction cap, or overall ranking) and would make TradingView an
   authority rather than evidence. The resulting native candidate still has to pass target
   attainability, economic quality, and (for shorts) margin eligibility
   before it can become an alert — nothing here shortcuts those gates.
6. **Chief AI context.** If a candidate carries TradingView evidence, that
   evidence is included in the Chief AI review payload
   (`app/services/chief_analyst.py`) as read-only context. It is not a
   weighting input the review can use to relax a rejection.

## Reliability details worth knowing about

- **Crash recovery.** If a process dies mid-cycle while an event is marked
  `PROCESSING`, the next cycle reclaims it back to `RETRY` after
  `TRADINGVIEW_PROCESSING_TIMEOUT_SECONDS` (default 120s) rather than leaving
  it stuck forever.
- **Poison-pill handling.** An event that fails processing
  `TRADINGVIEW_MAX_ATTEMPTS` times (default 5) is marked `POISONED` and
  stops being retried, instead of retrying forever or silently vanishing.
- **Retention.** Terminal events (`PROCESSED`, `REJECTED`, `DUPLICATE`,
  `POISONED`) older than `TRADINGVIEW_RETENTION_SECONDS` (default 7 days) are
  pruned so the inbox file cannot grow unbounded.
- **Exception hygiene.** An unexpected exception during processing is logged
  in full server-side, but only the exception type (not its message, which
  can embed request/response detail) is written into the durable,
  audit-retained inbox file.
- **Status visibility.** `GET /operator/status` includes a `tradingview_v2`
  block (queue depth, accepted/rejected/qualified/poisoned counts, etc.)
  whenever the bridge is enabled — see `operator_control.status_payload()`.

## Configuration

All of the following live in `app/core/config.py` and are read from `.env`
(none of them are secrets except `TRADINGVIEW_INTERNAL_VERIFICATION_VALUE`):

| Variable | Default | Purpose |
|---|---|---|
| `TRADINGVIEW_V2_ENABLED` | `false` | Master switch. Everything above is inert until this is `true`. |
| `TRADINGVIEW_TRUSTED_PROXY_IPS` | `127.0.0.1,::1` | Immediate-connection allowlist, defense in depth behind the reverse proxy. |
| `TRADINGVIEW_INTERNAL_VERIFICATION_VALUE` | `verified-by-trusted-proxy` (**must be rotated in production**) | Shared value the reverse proxy stamps on forwarded requests. In production it must differ from the documented default and contain at least 32 characters. |
| `TRADINGVIEW_WEBHOOK_TOKEN` | none (**required when enabled**) | Per-deployment URL-safe bearer token placed in the Pine payload. Must contain at least 43 characters; it is stripped before persistence. Generate with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `TRADINGVIEW_MAX_EVENT_AGE_SECONDS` | 300 | Reject a payload older than this at both ingest and (re-checked) processing time. |
| `TRADINGVIEW_FUTURE_TOLERANCE_SECONDS` | 30 | Reject a payload timestamped further in the future than this (clock skew tolerance). |
| `TRADINGVIEW_MAX_PRICE_DIVERGENCE_PCT` | 1.0 | Reject if TradingView's reported close diverges from a live Kraken ticker by more than this. |
| `TRADINGVIEW_DEDUP_WINDOW_SECONDS` | 900 | How long a qualified event remains eligible to be merged into a native cycle. |
| `TRADINGVIEW_MAX_PAYLOAD_BYTES` | 32768 | Hard cap on the raw request body size. |
| `TRADINGVIEW_EXPECTED_SCHEMA_VERSION` | `"2"` | Payload `schema_version` must match exactly. |
| `TRADINGVIEW_ALLOWED_SCRIPT_VERSIONS` | `ohm-signal-v1` | Comma-separated allowlist of `script_version` values. |
| `TRADINGVIEW_MAX_ATTEMPTS` | 5 | Attempts before an event is marked `POISONED`. |
| `TRADINGVIEW_RETENTION_SECONDS` | 604800 (7 days) | How long terminal events are kept before pruning. |
| `TRADINGVIEW_PROCESSING_TIMEOUT_SECONDS` | 120 | How long an event can sit in `PROCESSING` before being reclaimed as crashed. |
| `TRADINGVIEW_PROCESSING_BATCH_LIMIT` | 2 | Maximum queued events processed immediately before one due scan (hard maximum 3). |
| `TRADINGVIEW_KRAKEN_TIMEOUT_SECONDS` | 5 | Per-event public Kraken ticker timeout (hard maximum 10 seconds). |
| `TRADINGVIEW_MAX_INBOX_EVENTS` | 5000 | Hard capacity bound after terminal-event pruning. |

## Rollout plan

1. Deploy with `TRADINGVIEW_V2_ENABLED=false` (the default) — no behavior
   change at all.
2. Set up the reverse proxy from
   `deploy/nginx/tradingview-webhook.conf.example`, replacing the placeholder
   IP allowlist with TradingView's current published ranges and the
   placeholder verification value with a unique, private one.
3. Generate `TRADINGVIEW_WEBHOOK_TOKEN`, configure the same token in the
   Pine script's **OHM webhook token** input, and keep it private.
4. Load `tradingview/ohm_signal_indicator_v1.pine` on a Kraken 15m chart in
   TradingView, confirm it compiles, and paper-watch its alerts against the
   chart for a few sessions before pointing its webhook at production.
5. Flip `TRADINGVIEW_V2_ENABLED=true` and rotate
   `TRADINGVIEW_INTERNAL_VERIFICATION_VALUE` away from the documented
   default (required in production — OHM will refuse to start otherwise).
6. Watch `GET /operator/status`'s `tradingview_v2` block for a while before
   trusting it unattended.

## Known limitations / honest gaps

- **Compiler verification is not strategy validation.** Both Pine v6 scripts
  were compiled successfully in TradingView on a Kraken BTCUSD 15-minute
  chart on 2026-08-16, including the dynamic `alert()` payload and the
  uniformly closed 4H/1H series. The hosted run also exposed and corrected a
  direction-asymmetry defect in the first draft's short trigger and scoring.
  This is only syntax/runtime validation. The backtest companion defaults to
  a conservative directional-quality gate and a 0.4% maker-fee baseline, but
  it must still be evaluated across symbols, regimes, out-of-sample periods,
  and the operator's actual Kraken fee/slippage assumptions. Keep the bridge
  in paper observation until that evidence is acceptable.
- **The bridge deliberately cannot create TradingView-only candidates.** Its
  value is earlier corroborating evidence on a native-qualified setup. This
  is a safety boundary, not an unfinished selector approximation.
- **Python verification:** the full repository suite was run against this
  change in a clean review environment. Pine still requires the hosted
  compiler/runtime validation described above.
