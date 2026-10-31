"""O'Pip Stage-2 Independent Research & Risk Review (IRR).

This is the F12 advisory-research surface of architecture v1.5.0: an
independent, *shadow-only* challenge to an already-qualified Chief
recommendation. It exists to produce evidence, never influence.

Hard boundaries (proven by ``tests/test_opip_irr_shadow_v1.py``):

* the Chief conclusion, ranking, alert set, risk limits, capital, canonical
  decisions and exchange/paper paths are never modified;
* this module imports no ranking, risk, execution, exchange, Telegram or
  canonical-writer module;
* any provider failure, malformed answer or exhausted budget is contained and
  can never block or fail the Chief review;
* the provider credential is read from ``DEEPSEEK_API_KEY`` only, is never
  written to the evidence journal, and is never printed.

The adapter is deliberately a direct, single-attempt HTTP call to the
provider's documented OpenAI-compatible Chat Completions endpoint. It adds no
dependency (``httpx`` is already a runtime dependency) and no new architecture.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx

from app.services.recommendation_gate import candidate_alert_authorized
from app.services.registry_io import load_json, registry_lock, save_json_atomic


PROVIDER = "deepseek"
STAGE = "STAGE_2_INDEPENDENT_RESEARCH_REVIEW"

#: ``deepseek-flash`` is the model id published by the provider's Models &
#: Pricing page (model version ``DeepSeek-V4.1-Flash-1017``). The retired
#: ``deepseek-v4-flash`` alias is still served by the provider, but it is not a
#: documented current id, so O'Pip refuses it until it is verified again.
DEFAULT_MODEL = "deepseek-flash"
VERIFIED_MODEL_IDS = frozenset({"deepseek-flash"})
DEFAULT_BASE_URL = "https://api.deepseek.com"
CHAT_COMPLETIONS_PATH = "/chat/completions"

MODE_OFF = "off"
MODE_SHADOW = "shadow"
ALLOWED_MODES = frozenset({MODE_OFF, MODE_SHADOW})

VERDICT_AGREE = "agree"
VERDICT_CAUTION = "caution"
VERDICT_DISAGREE = "disagree"
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
ALLOWED_VERDICTS = frozenset(
    {
        VERDICT_AGREE,
        VERDICT_CAUTION,
        VERDICT_DISAGREE,
        VERDICT_INSUFFICIENT_EVIDENCE,
    }
)

STATUS_DISABLED = "DISABLED"
STATUS_INVALID_MODE = "INVALID_MODE"
STATUS_CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
STATUS_MISSING_CREDENTIAL = "MISSING_CREDENTIAL"
STATUS_UNVERIFIED_MODEL = "UNVERIFIED_MODEL"
STATUS_NO_ELIGIBLE_RECOMMENDATIONS = "NO_ELIGIBLE_RECOMMENDATIONS"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
STATUS_CALL_COST_LIMIT = "CALL_COST_LIMIT"
STATUS_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"

#: Documented DeepSeek ``deepseek-flash`` peak list prices, USD per 1M tokens.
#: Peak rates are used for every estimate so budgeting is never optimistic.
PRICE_INPUT_USD_PER_MTOK = 0.30
PRICE_OUTPUT_USD_PER_MTOK = 1.20

EVIDENCE_FILE = Path("/app/data/opip_irr_shadow_evidence.jsonl")
EVIDENCE_LOCK = EVIDENCE_FILE.parent / ".opip_irr_shadow_evidence.lock"
IDEMPOTENCE_FILE = Path("/app/data/opip_irr_idempotence.json")
IDEMPOTENCE_LOCK = IDEMPOTENCE_FILE.parent / ".opip_irr_idempotence.lock"
IDEMPOTENCE_MAX_KEYS = 256


#: Field names that a provider answer may not contribute. They are dropped
#: before anything is persisted, so a model cannot smuggle an executable
#: instruction into O'Pip evidence.
NON_ADVISORY_FIELD_NAMES = frozenset(
    {
        "action",
        "recommended_action",
        "alert",
        "execute",
        "order",
        "orders",
        "entry",
        "exit",
        "stop_loss",
        "take_profit",
        "leverage",
        "position_size",
        "capital",
        "risk_amount",
        "rank",
        "confidence",
        "trade",
    }
)

#: Credential-shaped field names are never persisted, whatever a caller adds.
CREDENTIAL_FIELD_NAMES = frozenset(
    {
        "api_key",
        "authorization",
        "deepseek_api_key",
        "api_secret",
        "secret",
        "token",
    }
)

_CANDIDATE_EVIDENCE_FIELDS: tuple[str, ...] = (
    "technical_score",
    "price",
    "trend",
    "rsi",
    "macd_histogram",
    "atr_pct",
    "volume_ratio",
    "strengths",
    "warnings",
    "weaknesses",
    "market_structure",
    "execution_evidence",
    "price_movement_intelligence",
    "deterministic_quality_by_risk_level",
    "margin_evidence",
)

_CHIEF_CONCLUSION_FIELDS: tuple[str, ...] = (
    "rank",
    "decision",
    "risk_level",
    "confidence",
    "reason",
)

SYSTEM_PROMPT = """You are O'Pip's STAGE-2 Independent Research & Risk Reviewer.

You receive an immutable evidence copy of ONE already-screened crypto setup and
the Chief Analyst's advisory conclusion for it. Your only job is to challenge
that conclusion independently, using the supplied deterministic evidence.

You have no authority. You cannot place, modify or cancel an order, cannot set
or change size, stops, leverage, risk or capital, cannot veto or approve any
deterministic gate, and cannot change a ranking, alert or canonical decision.
Do not propose entry, exit, order or sizing instructions at all.

Judge only whether the supplied deterministic evidence supports the Chief's
stated direction and risk level. Missing evidence is insufficient evidence, not
support. Never recalculate supplied deterministic metrics and never claim a win
probability.

Return json only in exactly this shape:
{
  "symbol": "<the supplied symbol>",
  "direction": "LONG|SHORT",
  "verdict": "agree|caution|disagree|insufficient_evidence",
  "reasons": ["short cited reason", "..."],
  "cited_evidence": ["field or observation you relied on", "..."],
  "conditions": ["what additional evidence would change your verdict", "..."]
}

Rules: 1 to 5 reasons, each at most 400 characters. Use
"insufficient_evidence" when the supplied evidence cannot support any verdict.
Cite the evidence you used; do not invent data that is not in the payload.
"""

#: Deterministic identity of the exact Stage-2 prompt, recorded with every
#: attempt so later analysis can partition challenges by the prompt that made
#: them. This is a content hash, not a semantic version.
PROMPT_VERSION = "IRR-PROMPT:" + hashlib.sha256(
    SYSTEM_PROMPT.encode("utf-8")
).hexdigest()[:12]


class IndependentReviewError(RuntimeError):
    """Base class for contained Stage-2 advisory failures."""


class IRRConfigurationError(IndependentReviewError):
    """The Stage-2 configuration is not usable; no call may be made."""


class IRRMissingCredential(IRRConfigurationError):
    """No provider credential is configured; no call may be made."""


class IRRUnverifiedModel(IRRConfigurationError):
    """The configured model id is not provider-documented; no call is made."""


class IRRProviderError(IndependentReviewError):
    """The provider rejected the request at the HTTP layer."""


class IRRResponseError(IndependentReviewError):
    """The provider answered, but not with the advisory schema."""


class IRRUsageUnavailable(IndependentReviewError):
    """Spend cannot be proven, so no further spend is allowed."""


@dataclass(frozen=True)
class IRRConfig:
    """Resolved, bounded Stage-2 configuration (never carries a secret out)."""

    model: str
    base_url: str
    timeout_seconds: float
    max_output_tokens: int
    reasoning_effort: str
    max_candidates: int
    daily_call_budget: int
    daily_token_budget: int
    daily_cost_budget_usd: float
    max_call_cost_usd: float
    api_key: str

    def public_evidence(self) -> dict[str, Any]:
        """Return the auditable configuration, with no credential material."""
        return {
            "provider": PROVIDER,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "max_candidates": self.max_candidates,
            "daily_call_budget": self.daily_call_budget,
            "daily_token_budget": self.daily_token_budget,
            "daily_cost_budget_usd": self.daily_cost_budget_usd,
            "max_call_cost_usd": self.max_call_cost_usd,
        }


def irr_mode() -> str:
    """Return the configured Stage-2 mode (``off`` unless set)."""
    return (os.getenv("OPIP_IRR_MODE") or MODE_OFF).strip().lower()


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def resolve_config() -> IRRConfig:
    """Resolve the bounded Stage-2 configuration or fail closed.

    Raises with an env-var-name-only message: credential values never appear in
    a raised message, a log line or an evidence row.
    """
    model = (os.getenv("OPIP_IRR_MODEL") or DEFAULT_MODEL).strip()
    if model not in VERIFIED_MODEL_IDS:
        raise IRRUnverifiedModel(
            "OPIP_IRR_MODEL is not a provider-documented model id; "
            "configure " + ", ".join(sorted(VERIFIED_MODEL_IDS))
        )
    base_url = (os.getenv("OPIP_IRR_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    if not base_url.startswith("https://"):
        raise IRRConfigurationError("OPIP_IRR_BASE_URL must be an https:// URL")

    effort = (os.getenv("OPIP_IRR_REASONING_EFFORT") or "high").strip().lower()
    if effort not in {"low", "high", "max"}:
        raise IRRConfigurationError(
            "OPIP_IRR_REASONING_EFFORT must be one of: low, high, max"
        )

    api_key = (os.getenv("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise IRRMissingCredential("DEEPSEEK_API_KEY is not configured")

    return IRRConfig(
        model=model,
        base_url=base_url,
        timeout_seconds=_env_float(
            "OPIP_IRR_TIMEOUT_SECONDS", 20.0, minimum=1.0, maximum=60.0
        ),
        max_output_tokens=_env_int(
            "OPIP_IRR_MAX_OUTPUT_TOKENS", 700, minimum=128, maximum=4096
        ),
        reasoning_effort=effort,
        max_candidates=_env_int("OPIP_IRR_MAX_CANDIDATES", 2, minimum=1, maximum=3),
        daily_call_budget=_env_int(
            "OPIP_IRR_DAILY_CALL_BUDGET", 24, minimum=1, maximum=1000
        ),
        daily_token_budget=_env_int(
            "OPIP_IRR_DAILY_TOKEN_BUDGET", 60000, minimum=1, maximum=10_000_000
        ),
        daily_cost_budget_usd=_env_float(
            "OPIP_IRR_DAILY_COST_BUDGET_USD", 1.0, minimum=0.001, maximum=100.0
        ),
        max_call_cost_usd=_env_float(
            "OPIP_IRR_MAX_CALL_COST_USD", 0.05, minimum=0.0001, maximum=10.0
        ),
        api_key=api_key,
    )


def canonical_json(value: Any) -> str:
    """Return the one canonical serialization used for digests and requests."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def freeze_evidence_copy(value: Any) -> Any:
    """Return a detached by-value copy of ``value``.

    The copy is built from a canonical JSON round-trip, so later mutation of the
    live Chief review or candidate payload cannot alter the evidence that was
    reviewed or the digest that was recorded.
    """
    return json.loads(canonical_json(value))


def evidence_digest(evidence: Any) -> str:
    """Return the SHA-256 of the exact canonical evidence bytes."""
    return hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()


def build_evidence_copy(
    *,
    recommendation: Mapping[str, Any],
    candidate_evidence: Mapping[str, Any] | None,
    chief_route: Mapping[str, Any] | None,
    market_regime_context: Any = None,
) -> dict[str, Any]:
    """Build the immutable Stage-2 evidence copy for one recommendation."""
    conclusion = {
        field: recommendation.get(field)
        for field in _CHIEF_CONCLUSION_FIELDS
        if field in recommendation
    }
    evidence_source = candidate_evidence if isinstance(candidate_evidence, Mapping) else {}
    deterministic = {
        field: evidence_source.get(field)
        for field in _CANDIDATE_EVIDENCE_FIELDS
        if field in evidence_source
    }
    route = None
    if isinstance(chief_route, Mapping):
        route = {
            key: chief_route.get(key)
            for key in ("provider", "model", "route_tier", "reasoning_effort")
        }
    return freeze_evidence_copy(
        {
            "evidence_copy_version": "IRR-EVIDENCE-COPY-V1",
            "stage": STAGE,
            "symbol": str(recommendation.get("symbol") or ""),
            "direction": str(recommendation.get("direction") or "").upper(),
            "chief_conclusion": conclusion,
            "chief_route": route,
            "deterministic_candidate_evidence": deterministic,
            "market_regime_context": freeze_evidence_copy(market_regime_context),
            "authority": "advisory_only",
            "funded_trade_authority_changed": False,
        }
    )


def estimate_cost_usd(*, input_tokens: int, output_tokens: int) -> float:
    """Return the conservative (peak-price) USD cost of one attempt."""
    cost = (
        max(0, int(input_tokens)) * PRICE_INPUT_USD_PER_MTOK
        + max(0, int(output_tokens)) * PRICE_OUTPUT_USD_PER_MTOK
    ) / 1_000_000
    return round(cost, 6)


def input_token_upper_bound(text: str) -> int:
    """Return a deliberately pessimistic token count for a request body."""
    return max(1, (len(text) + 2) // 3)


def _today_usage(*, now: datetime) -> tuple[int, int, float]:
    """Return (calls, tokens, cost) already spent on the current UTC day.

    Only this provider's own journal is read. The OpenAI usage journal belongs
    to Stage 1 and is never consulted or written here, so the two provider
    budgets stay strictly separate.
    """
    if not EVIDENCE_FILE.exists():
        return 0, 0, 0.0
    today = now.astimezone(timezone.utc).date()
    calls = 0
    tokens = 0
    cost = 0.0
    try:
        with EVIDENCE_FILE.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(row, dict) or row.get("status") != STATUS_SUCCEEDED:
                    continue
                if row.get("provider") not in (None, PROVIDER):
                    continue
                try:
                    stamp = datetime.fromisoformat(
                        str(row.get("timestamp_utc") or "").replace("Z", "+00:00")
                    )
                except (TypeError, ValueError):
                    continue
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                if stamp.astimezone(timezone.utc).date() != today:
                    continue
                calls += 1
                tokens += int(row.get("total_tokens") or 0)
                cost += float(row.get("estimated_cost_usd") or 0.0)
    except OSError as exc:
        raise IRRUsageUnavailable("IRR usage journal is unreadable") from exc
    return calls, tokens, round(cost, 6)


def budget_block_reason(config: IRRConfig, *, now: datetime) -> str | None:
    """Return why no further Stage-2 spend is allowed, or ``None``."""
    calls, tokens, cost = _today_usage(now=now)
    if calls >= config.daily_call_budget:
        return f"daily IRR call budget reached ({calls}/{config.daily_call_budget})"
    if tokens >= config.daily_token_budget:
        return f"daily IRR token budget reached ({tokens}/{config.daily_token_budget})"
    if cost >= config.daily_cost_budget_usd:
        return (
            "daily IRR cost budget reached "
            f"(${cost:.4f}/${config.daily_cost_budget_usd:.2f})"
        )
    return None


def build_request(*, config: IRRConfig, evidence: Mapping[str, Any]) -> dict[str, Any]:
    """Build the documented Chat Completions JSON-object request body."""
    user_content = canonical_json(evidence)
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        # Documented JSON Output mode: response_format json_object plus the word
        # "json" and an example shape in the system prompt.
        "response_format": {"type": "json_object"},
        "max_tokens": config.max_output_tokens,
        # deepseek-flash thinking mode is on by default; the documented
        # OpenAI-format effort values are low | high | max.
        "reasoning_effort": config.reasoning_effort,
        "stream": False,
    }


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "TIMEOUT"
    if isinstance(exc, httpx.HTTPError):
        return "TRANSPORT_ERROR"
    if isinstance(exc, IRRProviderError):
        return "HTTP_ERROR"
    if isinstance(exc, IRRResponseError):
        return "MALFORMED_RESPONSE"
    return type(exc).__name__


def _invoke(
    config: IRRConfig,
    body: Mapping[str, Any],
    *,
    post: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], int]:
    """Perform exactly one bounded provider call; never retry."""
    url = f"{config.base_url}{CHAT_COMPLETIONS_PATH}"
    timeout = httpx.Timeout(
        config.timeout_seconds,
        connect=min(5.0, config.timeout_seconds),
        read=config.timeout_seconds,
        write=config.timeout_seconds,
        pool=config.timeout_seconds,
    )
    poster = post or httpx.post
    started = time.perf_counter()
    response = poster(
        url,
        headers={
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=body,
        timeout=timeout,
    )
    latency_ms = int((time.perf_counter() - started) * 1000)

    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        # Status only: a provider error body may echo the prompt or request.
        raise IRRProviderError(f"provider returned HTTP {status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise IRRResponseError("provider returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise IRRResponseError("provider response was not a JSON object")

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise IRRResponseError("provider response carried no choices")
    first = choices[0] if isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        # Documented JSON-mode caveat: the provider may return blank content.
        raise IRRResponseError("provider returned empty advisory content")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise IRRResponseError("provider returned malformed advisory JSON") from exc
    if not isinstance(parsed, dict):
        raise IRRResponseError("provider advisory JSON was not an object")

    usage = payload.get("usage")
    return parsed, usage if isinstance(usage, dict) else {}, latency_ms


def _text_list(
    value: Any,
    *,
    limit: int,
    max_length: int,
) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())
        if not text:
            continue
        items.append(text[:max_length])
        if len(items) >= limit:
            break
    return items


def validate_challenge(
    parsed: Mapping[str, Any],
    *,
    symbol: str,
    direction: str,
) -> dict[str, Any]:
    """Validate and normalize one independent challenge.

    Anything outside the advisory schema is refused. Any advisory-shaped field
    a model tries to add (order, size, rank, ...) is dropped and only its name
    is recorded, so a provider answer can never carry executable instruction.
    """
    verdict = parsed.get("verdict")
    normalized_verdict = (
        verdict.strip().lower() if isinstance(verdict, str) else ""
    )
    if normalized_verdict not in ALLOWED_VERDICTS:
        raise IRRResponseError("challenge verdict is not in the advisory schema")

    reasons = _text_list(parsed.get("reasons"), limit=5, max_length=400)
    if not reasons:
        raise IRRResponseError("challenge cited no reasons")

    returned_symbol = parsed.get("symbol")
    if returned_symbol is not None:
        if str(returned_symbol).strip().upper() != symbol.strip().upper():
            raise IRRResponseError("challenge referenced a different symbol")
    returned_direction = parsed.get("direction")
    if returned_direction is not None:
        if str(returned_direction).strip().upper() != direction.strip().upper():
            raise IRRResponseError("challenge referenced a different direction")

    ignored = sorted(
        str(key) for key in parsed if str(key).strip().lower() in NON_ADVISORY_FIELD_NAMES
    )
    return {
        "verdict": normalized_verdict,
        "reasons": reasons,
        "cited_evidence": _text_list(
            parsed.get("cited_evidence"), limit=5, max_length=300
        ),
        "conditions": _text_list(parsed.get("conditions"), limit=5, max_length=300),
        "non_advisory_fields_ignored": ignored,
        "advisory_only": True,
        "authority_changed": False,
        "funded_trade_authority_changed": False,
    }



def _idempotency_key(*, digest: str, model: str) -> str:
    material = f"{digest}|{model}|{PROMPT_VERSION}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _idempotence_lookup(key: str) -> dict[str, Any] | None:
    try:
        with registry_lock(IDEMPOTENCE_LOCK):
            state = load_json(IDEMPOTENCE_FILE)
    except Exception:
        return None
    row = state.get(key)
    return dict(row) if isinstance(row, dict) else None


def _idempotence_store(key: str, row: Mapping[str, Any], *, now: datetime) -> None:
    try:
        with registry_lock(IDEMPOTENCE_LOCK):
            state = load_json(IDEMPOTENCE_FILE)
            state[key] = {**dict(row), "recorded_at": now.isoformat()}
            if len(state) > IDEMPOTENCE_MAX_KEYS:
                ordered = sorted(
                    state.items(),
                    key=lambda item: str((item[1] or {}).get("recorded_at") or ""),
                    reverse=True,
                )
                state = dict(ordered[:IDEMPOTENCE_MAX_KEYS])
            save_json_atomic(IDEMPOTENCE_FILE, state)
    except Exception:
        # A missing ledger can only cause a repeated call, never a missed gate;
        # the daily budget still bounds the total spend.
        return


def _append_evidence(record: Mapping[str, Any], *, now: datetime) -> None:
    """Append one audit row; never raise into the Chief review."""
    row = {
        "timestamp_utc": now.astimezone(timezone.utc).isoformat(),
        "stage": STAGE,
        "provider": PROVIDER,
        "prompt_version": PROMPT_VERSION,
        "shadow_only": True,
        "advisory_only": True,
        "authority_changed": False,
        "funded_trade_authority_changed": False,
        **dict(record),
    }
    row = {
        key: value
        for key, value in row.items()
        if str(key).strip().lower() not in CREDENTIAL_FIELD_NAMES
    }
    try:
        EVIDENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with registry_lock(EVIDENCE_LOCK):
            with EVIDENCE_FILE.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(row, separators=(",", ":"), default=str) + "\n"
                )
                handle.flush()
    except Exception:
        # Evidence is append-only and fail-open; it must never block a scan.
        return


def _select_recommendations(
    review: Mapping[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Return the eligible, already-qualified Chief recommendations to review.

    Selection reuses the deterministic recommendation gate
    (:func:`candidate_alert_authorized`); Stage 2 never widens it.
    """
    selected: list[dict[str, Any]] = []
    rows = review.get("top_candidates")
    if not isinstance(rows, list):
        return selected
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not candidate_alert_authorized(row):
            continue
        if not str(row.get("symbol") or "").strip():
            continue
        selected.append(dict(row))
        if len(selected) >= limit:
            break
    return selected


def _candidate_evidence(
    request_payload: Mapping[str, Any] | None,
    *,
    symbol: str,
    direction: str,
) -> dict[str, Any] | None:
    if not isinstance(request_payload, Mapping):
        return None
    rows = request_payload.get("candidates")
    if not isinstance(rows, list):
        return None
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").strip().upper() != symbol.upper():
            continue
        if str(row.get("direction") or "").strip().upper() != direction.upper():
            continue
        return dict(row)
    return None


def _empty_summary(
    status: str,
    *,
    config: IRRConfig | None = None,
    now: datetime,
    skipped_reason: str | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "status": status,
        "stage": STAGE,
        "shadow_only": True,
        "advisory_only": True,
        "authority_changed": False,
        "funded_trade_authority_changed": False,
        "provider": PROVIDER,
        "model": config.model if config is not None else None,
        "prompt_version": PROMPT_VERSION,
        "evaluated_at": now.astimezone(timezone.utc).isoformat(),
        "reviews": [],
        "budget": config.public_evidence() if config is not None else None,
    }
    if skipped_reason:
        summary["skipped_reason"] = skipped_reason
    return summary



def _review_one(
    *,
    recommendation: Mapping[str, Any],
    config: IRRConfig,
    request_payload: Mapping[str, Any] | None,
    chief_route: Mapping[str, Any] | None,
    post: Callable[..., Any] | None,
    now: datetime,
) -> dict[str, Any]:
    symbol = str(recommendation.get("symbol") or "").strip()
    direction = str(recommendation.get("direction") or "").strip().upper()
    base: dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
        "provider": PROVIDER,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "chief_route": dict(chief_route) if isinstance(chief_route, Mapping) else None,
        "advisory_only": True,
        "authority_changed": False,
    }

    try:
        evidence = build_evidence_copy(
            recommendation=recommendation,
            candidate_evidence=_candidate_evidence(
                request_payload, symbol=symbol, direction=direction
            ),
            chief_route=chief_route,
            market_regime_context=(
                request_payload.get("market_regime_context")
                if isinstance(request_payload, Mapping)
                else None
            ),
        )
    except (TypeError, ValueError):
        row = {**base, "status": STATUS_FAILED, "error_type": "EVIDENCE_COPY_ERROR"}
        _append_evidence(row, now=now)
        return row

    digest = evidence_digest(evidence)
    key = _idempotency_key(digest=digest, model=config.model)
    base.update(
        {
            "evidence_digest": digest,
            "evidence_idempotency_key": key,
            "evidence": evidence,
        }
    )

    replayed = _idempotence_lookup(key)
    if replayed is not None:
        row = {
            **base,
            "status": STATUS_IDEMPOTENT_REPLAY,
            "verdict": replayed.get("verdict"),
            "reasons": list(replayed.get("reasons") or []),
            "estimated_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    try:
        blocked = budget_block_reason(config, now=now)
    except IRRUsageUnavailable as exc:
        row = {
            **base,
            "status": STATUS_FAILED,
            "error_type": "USAGE_UNAVAILABLE",
            "skipped_reason": str(exc),
        }
        _append_evidence(row, now=now)
        return row
    if blocked:
        row = {
            **base,
            "status": STATUS_BUDGET_EXHAUSTED,
            "skipped_reason": blocked,
            "estimated_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    body = build_request(config=config, evidence=evidence)
    worst_case = estimate_cost_usd(
        input_tokens=input_token_upper_bound(
            SYSTEM_PROMPT + canonical_json(evidence)
        ),
        output_tokens=config.max_output_tokens,
    )
    base["worst_case_cost_usd"] = worst_case
    if worst_case > config.max_call_cost_usd:
        row = {
            **base,
            "status": STATUS_CALL_COST_LIMIT,
            "skipped_reason": (
                "worst-case call cost "
                f"${worst_case:.4f} exceeds OPIP_IRR_MAX_CALL_COST_USD "
                f"${config.max_call_cost_usd:.4f}"
            ),
            "estimated_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    try:
        parsed, usage, latency_ms = _invoke(config, body, post=post)
        challenge = validate_challenge(parsed, symbol=symbol, direction=direction)
    except Exception as exc:  # contained: Stage 2 may never fail the scan
        row = {
            **base,
            "status": STATUS_FAILED,
            "error_type": _error_code(exc),
            "estimated_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    input_tokens = int(usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    cost = estimate_cost_usd(
        input_tokens=input_tokens, output_tokens=output_tokens
    )
    row = {
        **base,
        "status": STATUS_SUCCEEDED,
        "latency_ms": latency_ms,
        "challenge": challenge,
        "verdict": challenge["verdict"],
        "reasons": challenge["reasons"],
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "estimated_cost_usd": cost,
    }
    _append_evidence(row, now=now)
    _idempotence_store(
        key,
        {
            "verdict": challenge["verdict"],
            "reasons": challenge["reasons"],
            "evidence_digest": digest,
        },
        now=now,
    )
    return row



def _overall_status(rows: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("status") or "") for row in rows}
    if not statuses:
        return STATUS_NO_ELIGIBLE_RECOMMENDATIONS
    if statuses <= {STATUS_SUCCEEDED, STATUS_IDEMPOTENT_REPLAY}:
        return STATUS_SUCCEEDED
    if statuses & {STATUS_SUCCEEDED, STATUS_IDEMPOTENT_REPLAY}:
        return "PARTIAL"
    for status in (STATUS_FAILED, STATUS_BUDGET_EXHAUSTED, STATUS_CALL_COST_LIMIT):
        if status in statuses:
            return status
    return STATUS_FAILED


def run_independent_shadow_review(
    *,
    review: Mapping[str, Any],
    request_payload: Mapping[str, Any] | None = None,
    post: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run Stage 2 for the eligible, qualified Chief recommendations.

    This is the only public entry point. It never raises and never returns
    authority: the result is advisory metadata for the shadow evidence journal
    and for operator inspection only.
    """
    now = now or datetime.now(timezone.utc)
    mode = irr_mode()
    if mode not in ALLOWED_MODES:
        return _empty_summary(
            STATUS_INVALID_MODE,
            now=now,
            skipped_reason="OPIP_IRR_MODE must be one of: off, shadow",
        )
    if mode == MODE_OFF:
        return _empty_summary(STATUS_DISABLED, now=now)

    try:
        config = resolve_config()
    except IRRMissingCredential as exc:
        return _empty_summary(STATUS_MISSING_CREDENTIAL, now=now, skipped_reason=str(exc))
    except IRRUnverifiedModel as exc:
        return _empty_summary(STATUS_UNVERIFIED_MODEL, now=now, skipped_reason=str(exc))
    except IRRConfigurationError as exc:
        return _empty_summary(
            STATUS_CONFIGURATION_ERROR, now=now, skipped_reason=str(exc)
        )

    recommendations = _select_recommendations(review, limit=config.max_candidates)
    if not recommendations:
        summary = _empty_summary(
            STATUS_NO_ELIGIBLE_RECOMMENDATIONS, config=config, now=now
        )
        return summary

    chief_route = review.get("opip_ai_route")
    rows: list[dict[str, Any]] = []
    for recommendation in recommendations:
        try:
            rows.append(
                _review_one(
                    recommendation=recommendation,
                    config=config,
                    request_payload=request_payload,
                    chief_route=chief_route if isinstance(chief_route, Mapping) else None,
                    post=post,
                    now=now,
                )
            )
        except Exception as exc:  # the whole stage stays contained
            rows.append(
                {
                    "symbol": str(recommendation.get("symbol") or ""),
                    "direction": str(recommendation.get("direction") or "").upper(),
                    "provider": PROVIDER,
                    "model": config.model,
                    "status": STATUS_FAILED,
                    "error_type": type(exc).__name__,
                    "advisory_only": True,
                    "authority_changed": False,
                }
            )

    summary = _empty_summary(_overall_status(rows), config=config, now=now)
    summary["reviews"] = rows
    summary["recommendation_count"] = len(recommendations)
    summary["estimated_cost_usd"] = round(
        sum(float(row.get("estimated_cost_usd") or 0.0) for row in rows), 6
    )
    return summary
