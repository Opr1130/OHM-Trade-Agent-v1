# O'Pip Adaptive Intelligence Router v1

## Purpose

O'Pip must not depend on one AI vendor. The Adaptive Intelligence Router sits
at the existing Chief advisory-review boundary and selects an inference
provider/model without changing deterministic qualification, risk, ranking,
Telegram, paper-trading, Kraken, or funded-order authority.

The production invariant is:

> AI may review already-screened opportunities. AI never bypasses a deterministic
> gate and never receives funded-trade execution authority.

## Initial production posture

The router is enabled by default but remains backward compatible with the
existing OpenAI configuration:

- provider order: `openai,digitalocean`
- existing `OPENAI_API_KEY` and `OPENAI_MODEL` continue to work
- standard finalist reviews use the existing OpenAI model and
  `OPENAI_REASONING_EFFORT` (default `medium`)
- premium finalist reviews use the existing OpenAI model with
  `OPIP_AI_OPENAI_PREMIUM_REASONING_EFFORT=high` by default
- no DigitalOcean request is possible until both a DigitalOcean inference
  credential and a DigitalOcean model id are configured

This means deployment can improve reasoning depth on the highest-value
shortlists without requiring a model-name migration or a DigitalOcean secret.

## Premium escalation

A Chief request is routed to the premium tier when any of these conditions is
true:

1. finalist count is at or below `OPIP_AI_PREMIUM_MAX_CANDIDATES` (default 3)
2. best technical score is at or above
   `OPIP_AI_PREMIUM_TECHNICAL_SCORE` (default 82)
3. price-movement stage is `CONFIRMED` or `ACTIVE` while
   `OPIP_AI_PREMIUM_ON_CONFIRMED_MOVEMENT=true`

These rules select inference effort only. They do not promote a candidate,
change a gate result, or authorize an alert/order by themselves.

## Configuration

### Router

```text
OPIP_AI_ROUTER_ENABLED=true
OPIP_AI_ADAPTIVE_ROUTER_ENABLED=true
OPIP_AI_PROVIDER_ORDER=openai,digitalocean

OPIP_AI_PREMIUM_MAX_CANDIDATES=3
OPIP_AI_PREMIUM_TECHNICAL_SCORE=82
OPIP_AI_PREMIUM_ON_CONFIRMED_MOVEMENT=true
```

### OpenAI

Existing settings remain valid:

```text
OPENAI_API_KEY=<secret>
OPENAI_MODEL=gpt-5.6
OPENAI_REASONING_EFFORT=medium
OPENAI_MAX_OUTPUT_TOKENS=1200
```

Optional router overrides:

```text
OPIP_AI_OPENAI_MODEL=<standard-model-id>
OPIP_AI_OPENAI_PREMIUM_MODEL=<premium-model-id>
OPIP_AI_OPENAI_PREMIUM_REASONING_EFFORT=high
```

If no OpenAI router override is supplied, O'Pip uses the existing
`OPENAI_MODEL`.

### DigitalOcean Serverless Inference

Do not commit credentials or guessed model ids. Obtain the current model id
from the provider model catalogue and configure it operationally.

```text
DIGITALOCEAN_INFERENCE_KEY=<secret>
OPIP_AI_DIGITALOCEAN_BASE_URL=https://inference.do-ai.run/v1
OPIP_AI_DIGITALOCEAN_MODEL=<standard-model-id>
OPIP_AI_DIGITALOCEAN_PREMIUM_MODEL=<optional-premium-model-id>
```

O'Pip deliberately does not send provider-specific reasoning controls to
DigitalOcean by default. They may be enabled only after the selected model is
verified to support them:

```text
OPIP_AI_DIGITALOCEAN_SEND_REASONING=false
OPIP_AI_DIGITALOCEAN_REASONING_EFFORT=medium
```

## Migration after OpenAI credits

No code change is required.

During OpenAI-credit use:

```text
OPIP_AI_PROVIDER_ORDER=openai,digitalocean
```

After a DigitalOcean model has passed shadow/outcome comparison:

```text
OPIP_AI_PROVIDER_ORDER=digitalocean,openai
```

If OpenAI is unavailable or its configured O'Pip daily budget is exhausted,
the router can continue with DigitalOcean when DigitalOcean is fully
configured. If every configured provider fails, Chief review remains
fail-closed and no AI-qualified recommendation is fabricated.

## Measurement and profitability attribution

Every routed attempt records measurement-only telemetry in:

```text
/app/data/opip_ai_router_usage.jsonl
```

Fields include:

- provider
- model
- route tier and route reason
- reasoning effort where applicable
- latency
- success/failure
- token usage when returned by the provider

Chief learning evidence also records the successful provider/model route.
That allows later outcome analysis to compare signal quality and realized
paper-trade performance by provider/model without letting historical learning
alter current funded-trade authority.

## Safety boundaries

The router module imports no exchange, order, position, Telegram, or execution
module. Provider routing cannot:

- place or modify a Kraken order
- create funded positions
- bypass target/economic gates
- bypass recommendation gates
- change risk limits
- send an alert independently
- change deterministic O'Pip protection

Provider telemetry is fail-open; provider review itself is fail-closed when no
provider succeeds.
