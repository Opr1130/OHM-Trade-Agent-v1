# OHM Trade Agent v1

A conservative, **alert-only** trade-signal service designed for someone who works full time and wants to review only high-quality candidates during scheduled breaks.

## What v1 does

1. Receives a TradingView webhook.
2. Validates the payload and webhook secret.
3. Scores trend, momentum, volume, breakout, and market regime.
4. Calculates fixed-risk position size and reward-to-risk.
5. Optionally asks `gpt-5.6-terra` to review the setup.
6. Returns `alert`, `watch`, or `reject`.
7. Writes every decision to a local JSONL journal.

**It does not place orders. Live execution is intentionally disabled.**

## Safety defaults

- 0.35% account risk per candidate
- Minimum 2:1 reward-to-risk
- AI cannot override deterministic risk rejection
- AI disabled until explicitly enabled
- No broker or Kraken secret required for v1

## Windows setup

```powershell
 git clone https://github.com/Opr1130/OHM-Trade-Agent-v1.git
 cd OHM-Trade-Agent-v1
 py -m venv .venv
 .\.venv\Scripts\Activate.ps1
 pip install -r requirements.txt
 Copy-Item .env.example .env
```

Edit `.env` and replace `WEBHOOK_SECRET`. Leave `AI_ENABLED=false` for the first test.

Run:

```powershell
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` for the interactive API screen.

## Test locally

```powershell
$headers = @{ "X-Webhook-Secret" = "your-secret-from-env" }
$body = @{
  symbol = "BTCUSD"
  asset_class = "crypto"
  timeframe = "4h"
  side = "long"
  price = 100
  stop_price = 95
  target_price = 112
  rsi = 60
  volume_ratio = 1.7
  ema_fast = 101
  ema_slow = 98
  breakout = $true
  market_regime = "bullish"
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri "http://127.0.0.1:8000/webhooks/tradingview" `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

## Enable AI review

Add an OpenAI API key to `.env`, then set:

```env
OPENAI_API_KEY=your-key
OPENAI_MODEL=gpt-5.6-terra
AI_ENABLED=true
```

Restart the server. Never commit `.env` or API keys.

## TradingView webhook payload

TradingView must send JSON compatible with the local test body. TradingView cannot send a custom HTTP header, so production deployment should place the secret in the webhook URL or signed body through a secure gateway. The current header-based endpoint is intended for safe local testing first.

## TradingView Intelligence Bridge (v2, optional)

A second, optional webhook (`POST /webhooks/tradingview/v2`) accepts
candidate evidence from a companion Pine indicator on a faster bar-close
cadence than the native scanner polls. It is disabled by default
(`TRADINGVIEW_V2_ENABLED=false`) and, like the legacy webhook above, can
never place, confirm, or override anything — it only ever contributes
context that still has to pass every one of OHM's existing gates. See
[`TRADINGVIEW_WAVE8_2.md`](TRADINGVIEW_WAVE8_2.md) for the full design, the
network-boundary setup required before enabling it, and known limitations.

## Run tests

```powershell
pytest -q
```

## Roadmap

- v0.1: Local webhook, deterministic scoring, risk engine, journal
- v0.2: Secure internet endpoint and phone notifications
- v0.3: Market-data freshness and duplicate-signal protection
- v0.4: Paper portfolio and daily loss lockout
- v0.5: Human-approved Kraken execution
