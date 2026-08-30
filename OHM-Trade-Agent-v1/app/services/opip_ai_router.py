from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from openai import OpenAI

from app.services.registry_io import registry_lock


DEFAULT_DIGITALOCEAN_BASE_URL = "https://inference.do-ai.run/v1"
ROUTER_USAGE_FILE = Path("/app/data/opip_ai_router_usage.jsonl")
ROUTER_USAGE_LOCK = ROUTER_USAGE_FILE.parent / ".opip_ai_router_usage.lock"

_ALLOWED_PROVIDERS = {"openai", "digitalocean"}
_ALLOWED_REASONING = {"low", "medium", "high"}


class AIProviderUnavailable(RuntimeError):
    """No configured AI provider completed the advisory Chief request."""


@dataclass(frozen=True)
class RouteTarget:
    provider: str
    model: str
    api_key: str
    base_url: str | None
    reasoning_effort: str | None
    send_reasoning: bool

    @property
    def cache_key(self) -> str:
        effort = self.reasoning_effort or "none"
        return f"{self.provider}:{self.model}:{effort}"


@dataclass(frozen=True)
class RoutePlan:
    route_tier: str
    route_reason: str
    targets: tuple[RouteTarget, ...]

    @property
    def cache_key(self) -> str:
        target_key = ",".join(target.cache_key for target in self.targets)
        return f"{self.route_tier}|{self.route_reason}|{target_key}"

    @property
    def has_non_openai_provider(self) -> bool:
        return any(target.provider != "openai" for target in self.targets)

    def without_provider(self, provider: str) -> "RoutePlan":
        name = provider.strip().lower()
        return replace(
            self,
            targets=tuple(target for target in self.targets if target.provider != name),
        )


@dataclass(frozen=True)
class RouterResponse:
    output_text: str
    usage: Any
    provider: str
    model: str
    route_tier: str
    route_reason: str
    reasoning_effort: str | None
    latency_ms: int
    attempts: tuple[dict[str, Any], ...]

    def route_evidence(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "route_tier": self.route_tier,
            "route_reason": self.route_reason,
            "reasoning_effort": self.reasoning_effort,
            "latency_ms": self.latency_ms,
            "attempts": [dict(item) for item in self.attempts],
            "advisory_only": True,
            "funded_trade_authority_changed": False,
        }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)).strip())
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _normalized_reasoning(value: str | None, default: str | None) -> str | None:
    raw = (value or default or "").strip().lower()
    if not raw:
        return None
    if raw not in _ALLOWED_REASONING:
        raise ValueError(
            "AI reasoning effort must be one of: low, medium, high"
        )
    return raw


def _provider_order() -> tuple[str, ...]:
    raw = os.getenv("OPIP_AI_PROVIDER_ORDER", "openai,digitalocean")
    seen: set[str] = set()
    ordered: list[str] = []
    for token in raw.split(","):
        provider = token.strip().lower()
        if provider in _ALLOWED_PROVIDERS and provider not in seen:
            ordered.append(provider)
            seen.add(provider)
    if "openai" not in seen:
        ordered.append("openai")
    if "digitalocean" not in seen:
        ordered.append("digitalocean")
    return tuple(ordered)


def _candidate_rows(request_payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (request_payload.get("candidates") or [])
        if isinstance(item, dict)
    ]


def _premium_route(request_payload: dict[str, Any]) -> tuple[bool, str]:
    if not _env_bool("OPIP_AI_ADAPTIVE_ROUTER_ENABLED", True):
        return False, "adaptive_router_disabled"

    candidates = _candidate_rows(request_payload)
    candidate_count = len(candidates)
    max_candidates = _env_int(
        "OPIP_AI_PREMIUM_MAX_CANDIDATES",
        3,
        minimum=1,
        maximum=8,
    )
    if 0 < candidate_count <= max_candidates:
        return True, f"finalist_count<={max_candidates}"

    threshold = _env_int(
        "OPIP_AI_PREMIUM_TECHNICAL_SCORE",
        82,
        minimum=0,
        maximum=100,
    )
    best_score = 0.0
    for candidate in candidates:
        try:
            best_score = max(best_score, float(candidate.get("technical_score") or 0.0))
        except (TypeError, ValueError):
            continue
    if best_score >= threshold:
        return True, f"technical_score>={threshold}"

    if _env_bool("OPIP_AI_PREMIUM_ON_CONFIRMED_MOVEMENT", True):
        for candidate in candidates:
            movement = candidate.get("price_movement_intelligence")
            if not isinstance(movement, dict):
                continue
            stage = str(movement.get("stage") or "").upper()
            if stage in {"CONFIRMED", "ACTIVE"}:
                return True, f"price_movement={stage}"

    return False, "standard_finalist_review"


def _openai_target(
    *,
    default_model: str,
    default_api_key: str | None,
    premium: bool,
) -> RouteTarget | None:
    key = (default_api_key or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        return None

    if premium:
        model = (
            os.getenv("OPIP_AI_OPENAI_PREMIUM_MODEL")
            or os.getenv("OPIP_AI_OPENAI_MODEL")
            or default_model
        ).strip()
        effort = _normalized_reasoning(
            os.getenv("OPIP_AI_OPENAI_PREMIUM_REASONING_EFFORT"),
            "high",
        )
    else:
        model = (os.getenv("OPIP_AI_OPENAI_MODEL") or default_model).strip()
        effort = _normalized_reasoning(
            os.getenv("OPENAI_REASONING_EFFORT"),
            "medium",
        )

    if not model:
        return None
    return RouteTarget(
        provider="openai",
        model=model,
        api_key=key,
        base_url=None,
        reasoning_effort=effort,
        send_reasoning=True,
    )


def _digitalocean_target(*, premium: bool) -> RouteTarget | None:
    key = (
        os.getenv("DIGITALOCEAN_INFERENCE_KEY")
        or os.getenv("DIGITALOCEAN_TOKEN")
        or ""
    ).strip()
    if not key:
        return None

    if premium:
        model = (
            os.getenv("OPIP_AI_DIGITALOCEAN_PREMIUM_MODEL")
            or os.getenv("OPIP_AI_DIGITALOCEAN_MODEL")
            or ""
        ).strip()
    else:
        model = (os.getenv("OPIP_AI_DIGITALOCEAN_MODEL") or "").strip()
    if not model:
        return None

    send_reasoning = _env_bool("OPIP_AI_DIGITALOCEAN_SEND_REASONING", False)
    effort = (
        _normalized_reasoning(
            os.getenv("OPIP_AI_DIGITALOCEAN_REASONING_EFFORT"),
            "high" if premium else "medium",
        )
        if send_reasoning
        else None
    )
    base_url = (
        os.getenv("OPIP_AI_DIGITALOCEAN_BASE_URL")
        or DEFAULT_DIGITALOCEAN_BASE_URL
    ).strip().rstrip("/")

    return RouteTarget(
        provider="digitalocean",
        model=model,
        api_key=key,
        base_url=base_url,
        reasoning_effort=effort,
        send_reasoning=send_reasoning,
    )


def plan_chief_route(
    request_payload: dict[str, Any],
    *,
    default_model: str,
    openai_api_key: str | None,
) -> RoutePlan:
    premium, route_reason = _premium_route(request_payload)
    route_tier = "premium" if premium else "standard"

    router_enabled = _env_bool("OPIP_AI_ROUTER_ENABLED", True)
    if not router_enabled:
        target = _openai_target(
            default_model=default_model,
            default_api_key=openai_api_key,
            premium=False,
        )
        return RoutePlan(
            route_tier="compatibility",
            route_reason="router_disabled",
            targets=(target,) if target else (),
        )

    candidates: dict[str, RouteTarget | None] = {
        "openai": _openai_target(
            default_model=default_model,
            default_api_key=openai_api_key,
            premium=premium,
        ),
        "digitalocean": _digitalocean_target(premium=premium),
    }
    targets = tuple(
        target
        for provider in _provider_order()
        if (target := candidates.get(provider)) is not None
    )
    return RoutePlan(
        route_tier=route_tier,
        route_reason=route_reason,
        targets=targets,
    )


def _usage_int(usage: Any, name: str) -> int:
    return int(getattr(usage, name, 0) or 0) if usage is not None else 0


def _append_router_usage(
    *,
    target: RouteTarget,
    route_tier: str,
    route_reason: str,
    candidate_count: int,
    latency_ms: int,
    status: str,
    usage: Any = None,
    error_type: str | None = None,
) -> None:
    record = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "provider": target.provider,
        "model": target.model,
        "route_tier": route_tier,
        "route_reason": route_reason,
        "reasoning_effort": target.reasoning_effort,
        "candidate_count": int(candidate_count),
        "latency_ms": int(latency_ms),
        "status": status,
        "error_type": error_type,
        "input_tokens": _usage_int(usage, "input_tokens"),
        "output_tokens": _usage_int(usage, "output_tokens"),
        "total_tokens": _usage_int(usage, "total_tokens"),
        "advisory_only": True,
        "funded_trade_authority_changed": False,
    }
    try:
        ROUTER_USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with registry_lock(ROUTER_USAGE_LOCK):
            with ROUTER_USAGE_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
                handle.flush()
    except Exception:
        # Telemetry is intentionally fail-open to advisory review.
        return


def _invoke_target(
    target: RouteTarget,
    *,
    system_prompt: str,
    request_payload: dict[str, Any],
    max_output_tokens: int,
    client_factory=OpenAI,
) -> tuple[str, Any]:
    kwargs: dict[str, Any] = {"api_key": target.api_key}
    if target.base_url:
        kwargs["base_url"] = target.base_url
    client = client_factory(**kwargs)

    request: dict[str, Any] = {
        "model": target.model,
        "max_output_tokens": max_output_tokens,
        "input": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(request_payload)},
        ],
    }
    if target.send_reasoning and target.reasoning_effort:
        request["reasoning"] = {"effort": target.reasoning_effort}

    response = client.responses.create(**request)
    output_text = str(getattr(response, "output_text", "") or "")
    if not output_text.strip():
        raise ValueError("AI provider returned an empty response")
    return output_text, getattr(response, "usage", None)


def invoke_chief_review(
    plan: RoutePlan,
    *,
    system_prompt: str,
    request_payload: dict[str, Any],
    max_output_tokens: int,
    client_factory=OpenAI,
) -> RouterResponse:
    if not plan.targets:
        raise AIProviderUnavailable("no configured O'Pip AI provider is available")

    attempts: list[dict[str, Any]] = []
    candidate_count = len(_candidate_rows(request_payload))

    for target in plan.targets:
        started = time.perf_counter()
        try:
            output_text, usage = _invoke_target(
                target,
                system_prompt=system_prompt,
                request_payload=request_payload,
                max_output_tokens=max_output_tokens,
                client_factory=client_factory,
            )
        except Exception as exc:
            latency_ms = int((time.perf_counter() - started) * 1000)
            attempt = {
                "provider": target.provider,
                "model": target.model,
                "status": "failed",
                "error_type": type(exc).__name__,
                "latency_ms": latency_ms,
            }
            attempts.append(attempt)
            _append_router_usage(
                target=target,
                route_tier=plan.route_tier,
                route_reason=plan.route_reason,
                candidate_count=candidate_count,
                latency_ms=latency_ms,
                status="failed",
                error_type=type(exc).__name__,
            )
            continue

        latency_ms = int((time.perf_counter() - started) * 1000)
        attempt = {
            "provider": target.provider,
            "model": target.model,
            "status": "succeeded",
            "error_type": None,
            "latency_ms": latency_ms,
        }
        attempts.append(attempt)
        _append_router_usage(
            target=target,
            route_tier=plan.route_tier,
            route_reason=plan.route_reason,
            candidate_count=candidate_count,
            latency_ms=latency_ms,
            status="succeeded",
            usage=usage,
        )
        return RouterResponse(
            output_text=output_text,
            usage=usage,
            provider=target.provider,
            model=target.model,
            route_tier=plan.route_tier,
            route_reason=plan.route_reason,
            reasoning_effort=target.reasoning_effort,
            latency_ms=latency_ms,
            attempts=tuple(attempts),
        )

    summary = ", ".join(
        f"{item['provider']}:{item['error_type']}" for item in attempts
    )
    raise AIProviderUnavailable(
        "all configured O'Pip AI providers failed"
        + (f" ({summary})" if summary else "")
    )
