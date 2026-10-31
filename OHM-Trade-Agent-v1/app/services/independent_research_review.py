"""O'Pip Stage-2 Independent Research & Risk Review (IRR), shadow-only.

F12 advisory research for architecture v1.5.0. An independent provider is asked
to challenge an **already finally-qualified** recommendation. The answer is
evidence, never influence.

Pipeline shape (this is what makes the advisory paths independent):

* **Producer** — :func:`enqueue_final_qualified` runs inside the scan after the
  final deterministic target, economic and portfolio/capital gates have passed.
  It performs bounded local append-only I/O only: **no network call ever runs on
  the scan, Chief or Telegram path**.
* **Consumer** — :func:`drain_queue` runs in its own bounded process/job
  (``python -m app.jobs.drain_irr_shadow``) on the existing scheduler, so a slow
  or hung provider cannot delay a scan, a Chief review, a Telegram delivery or
  an F11 protection cycle.

Hard boundaries (proven by ``tests/test_opip_irr_shadow_v1.py``):

* the Chief conclusion, ranking, alert set, risk, capital, canonical decisions,
  paper state, order paths and Telegram delivery are never modified;
* this module imports no ranking, risk, execution, exchange, Telegram, canonical
  or learning module;
* provider failures, malformed answers, exhausted budgets or an unreadable spend
  ledger are contained and can never block or fail the primary pipeline;
* the credential is read from ``DEEPSEEK_API_KEY`` only and is never written to
  a journal, a message or stdout;
* requests go to the one explicitly approved provider endpoint. An arbitrary
  HTTPS host is refused before any credential is attached.

No new dependency (``httpx`` is already a runtime dependency), no new agent
framework, no new service.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlsplit

import httpx

from app.services.registry_io import load_json, registry_lock, save_json_atomic


PROVIDER = "deepseek"
STAGE = "STAGE_2_INDEPENDENT_RESEARCH_REVIEW"

#: ``deepseek-flash`` is the model id published by the provider's Models &
#: Pricing page (model version ``DeepSeek-V4.1-Flash-1017``). The retired
#: ``deepseek-v4-flash`` alias is still served by the provider, but it is not a
#: documented current id, so O'Pip refuses it until it is verified again.
DEFAULT_MODEL = "deepseek-flash"
VERIFIED_MODEL_IDS = frozenset({"deepseek-flash"})

#: The explicitly approved provider origin. Nothing else is reachable: the
#: credential may only ever be sent to this host, over TLS, on the default port,
#: with no path, query, fragment or userinfo. See :func:`approved_base_url`.
APPROVED_PROVIDER_HOST = "api.deepseek.com"
APPROVED_BASE_URL = f"https://{APPROVED_PROVIDER_HOST}"
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
STATUS_NOT_QUALIFIED = "NOT_QUALIFIED"
STATUS_EVIDENCE_MISMATCH = "EVIDENCE_MISMATCH"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUS_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
STATUS_CALL_COST_LIMIT = "CALL_COST_LIMIT"
STATUS_IDEMPOTENT_REPLAY = "IDEMPOTENT_REPLAY"
STATUS_SPEND_UNVERIFIABLE = "SPEND_UNVERIFIABLE"
STATUS_QUEUE_UNAVAILABLE = "QUEUE_UNAVAILABLE"
STATUS_QUEUE_RECORD_UNREADABLE = "QUEUE_RECORD_UNREADABLE"

#: Documented DeepSeek ``deepseek-flash`` peak list prices, USD per 1M tokens.
#: Peak rates are used for every estimate so budgeting is never optimistic.
PRICE_INPUT_USD_PER_MTOK = 0.30
PRICE_OUTPUT_USD_PER_MTOK = 1.20

QUEUE_FILE = Path("/app/data/opip_irr_queue.jsonl")
QUEUE_LOCK = QUEUE_FILE.parent / ".opip_irr_queue.lock"
QUEUE_CHECKPOINT_FILE = Path("/app/data/opip_irr_queue_checkpoint.json")
QUEUE_CONSUMER_LOCK = QUEUE_FILE.parent / ".opip_irr_queue.consumer.lock"

EVIDENCE_FILE = Path("/app/data/opip_irr_shadow_evidence.jsonl")
EVIDENCE_LOCK = EVIDENCE_FILE.parent / ".opip_irr_shadow_evidence.lock"

SPEND_LEDGER_FILE = Path("/app/data/opip_irr_spend_ledger.jsonl")
SPEND_LEDGER_LOCK = SPEND_LEDGER_FILE.parent / ".opip_irr_spend_ledger.lock"

IDEMPOTENCE_FILE = Path("/app/data/opip_irr_idempotence.json")
IDEMPOTENCE_LOCK = IDEMPOTENCE_FILE.parent / ".opip_irr_idempotence.lock"
IDEMPOTENCE_MAX_KEYS = 256

#: A local queue append must never delay a Telegram delivery. A contended lock
#: is abandoned (fail-soft) rather than waited out.
PRODUCER_LOCK_TIMEOUT_SECONDS = 0.25

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

_CHIEF_CONCLUSION_FIELDS: tuple[str, ...] = (
    "rank",
    "decision",
    "risk_level",
    "confidence",
    "reason",
)

_QUALIFICATION_FIELDS: tuple[str, ...] = (
    "target_attainability_score",
    "economic_target_2_move_pct",
    "economic_validation_net_t2",
    "economic_validation_capital",
    "economic_position_notional",
    "profit_rank_score",
    "liquidity_capacity_status",
    "liquidity_capacity_ceiling_usd",
)

DETERMINISTIC_EVIDENCE_FIELDS: tuple[str, ...] = (
    "technical_score",
    "market_regime",
    "market_intelligence",
    "price_movement",
    "execution_evidence",
    "entry_plan",
)

SYSTEM_PROMPT = """You are O'Pip's STAGE-2 Independent Research & Risk Reviewer.

You receive an immutable evidence copy of ONE crypto setup that has already
passed O'Pip's final deterministic target-quality, economic and portfolio gates,
together with the Chief Analyst's advisory conclusion for it. Your only job is
to challenge that conclusion independently, using the supplied deterministic
evidence.

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


class IRRUnapprovedEndpoint(IRRConfigurationError):
    """The configured endpoint is not the explicitly approved provider."""


class IRRProviderError(IndependentReviewError):
    """The provider rejected the request at the HTTP layer."""


class IRRResponseError(IndependentReviewError):
    """The provider answered, but not with the advisory schema."""


class IRRSpendUnavailable(IndependentReviewError):
    """Spend cannot be read or written, so no further spend is allowed."""


@dataclass(frozen=True)
class IRRConfig:
    """Resolved, bounded Stage-2 configuration (never carries a secret out)."""

    model: str
    base_url: str
    timeout_seconds: float
    max_output_tokens: int
    reasoning_effort: str
    max_candidates: int
    drain_batch: int
    daily_call_budget: int
    daily_token_budget: int
    daily_cost_budget_usd: float
    max_call_cost_usd: float
    api_key: str

    def public_evidence(self) -> dict[str, Any]:
        """Return the auditable configuration, with no credential material."""
        return {
            "provider": PROVIDER,
            "endpoint": self.base_url,
            "model": self.model,
            "timeout_seconds": self.timeout_seconds,
            "max_output_tokens": self.max_output_tokens,
            "reasoning_effort": self.reasoning_effort,
            "max_candidates": self.max_candidates,
            "drain_batch": self.drain_batch,
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


def approved_base_url(raw: str | None) -> str:
    """Return the approved provider origin, or refuse the endpoint.

    Only the explicitly approved provider host is reachable. An arbitrary
    ``https://`` host, a non-default port, a path, a query, a fragment or
    embedded userinfo all raise :class:`IRRUnapprovedEndpoint` before any
    credential is attached to a request.
    """
    candidate = (raw or APPROVED_BASE_URL).strip()
    if "@" in candidate:
        raise IRRUnapprovedEndpoint("OPIP_IRR_BASE_URL must not embed credentials")
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise IRRUnapprovedEndpoint("OPIP_IRR_BASE_URL is not a valid URL") from exc
    if parts.scheme != "https":
        raise IRRUnapprovedEndpoint("OPIP_IRR_BASE_URL must use https")
    if (parts.hostname or "").lower() != APPROVED_PROVIDER_HOST:
        raise IRRUnapprovedEndpoint(
            f"OPIP_IRR_BASE_URL must be {APPROVED_BASE_URL}"
        )
    if parts.port not in (None, 443):
        raise IRRUnapprovedEndpoint("OPIP_IRR_BASE_URL must use the default TLS port")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise IRRUnapprovedEndpoint(
            "OPIP_IRR_BASE_URL must be an origin with no path, query or fragment"
        )
    return APPROVED_BASE_URL


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
    base_url = approved_base_url(os.getenv("OPIP_IRR_BASE_URL"))

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
        drain_batch=_env_int("OPIP_IRR_DRAIN_BATCH", 2, minimum=1, maximum=5),
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
    live alert or ranking cannot alter the evidence that was reviewed or the
    digest that was recorded.
    """
    return json.loads(canonical_json(value))


def evidence_digest(evidence: Any) -> str:
    """Return the SHA-256 of the exact canonical evidence bytes."""
    return hashlib.sha256(canonical_json(evidence).encode("utf-8")).hexdigest()


def idempotency_key(*, digest: str, model: str) -> str:
    material = f"{digest}|{model}|{PROMPT_VERSION}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _portfolio_rank(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        rank = int(value)
    except (TypeError, ValueError):
        return None
    return rank if rank >= 1 else None


def build_evidence_copy(
    *,
    alert: Mapping[str, Any],
    chief_route: Mapping[str, Any] | None,
    market_regime: Any = None,
) -> dict[str, Any]:
    """Build the immutable Stage-2 evidence copy for one final survivor.

    Only a candidate that carries the final deterministic target, economic and
    portfolio/capital qualification evidence can produce a copy that passes
    :func:`qualification_passed`.
    """
    conclusion = {
        field: alert.get(field)
        for field in _CHIEF_CONCLUSION_FIELDS
        if field in alert
    }
    qualification = {
        "target_quality_qualified": alert.get("target_quality_qualified") is True,
        "economic_qualified": alert.get("economic_qualified") is True,
        "portfolio_rank": _portfolio_rank(alert.get("opportunity_rank")),
        **{
            field: alert.get(field)
            for field in _QUALIFICATION_FIELDS
            if field in alert
        },
    }
    deterministic = {
        field: alert.get(field)
        for field in DETERMINISTIC_EVIDENCE_FIELDS
        if field in alert
    }
    route = None
    if isinstance(chief_route, Mapping):
        route = {
            key: chief_route.get(key)
            for key in ("provider", "model", "route_tier", "reasoning_effort")
        }
    return freeze_evidence_copy(
        {
            "evidence_copy_version": "IRR-EVIDENCE-COPY-V2",
            "stage": STAGE,
            "symbol": str(alert.get("symbol") or ""),
            "direction": str(alert.get("direction") or "").upper(),
            "chief_conclusion": conclusion,
            "chief_route": route,
            "final_qualification": qualification,
            "deterministic_candidate_evidence": deterministic,
            "market_regime_context": freeze_evidence_copy(market_regime),
            "authority": "advisory_only",
            "funded_trade_authority_changed": False,
        }
    )


def qualification_passed(evidence: Mapping[str, Any]) -> bool:
    """Return whether the copy proves final target, economic and portfolio pass.

    The consumer re-checks this rather than trusting the producer: an
    unqualified or tampered queue record can never spend provider money.
    """
    qualification = evidence.get("final_qualification")
    if not isinstance(qualification, Mapping):
        return False
    if qualification.get("target_quality_qualified") is not True:
        return False
    if qualification.get("economic_qualified") is not True:
        return False
    return _portfolio_rank(qualification.get("portfolio_rank")) is not None


def evidence_identity_problem(
    request: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> str | None:
    """Return why a queued row does not match its stored identity, or ``None``.

    The worker verifies the persisted evidence against the digest and request id
    the producer wrote, and against the declared provider/stage/identity, before
    it may reserve or spend anything. A mutated or mis-filed queue row is
    refused without a provider call.
    """
    if evidence.get("stage") != STAGE:
        return "STAGE"
    if evidence.get("evidence_copy_version") != "IRR-EVIDENCE-COPY-V2":
        return "VERSION"
    if evidence.get("authority") != "advisory_only":
        return "AUTHORITY"

    recomputed = evidence_digest(evidence)
    stored_digest = request.get("evidence_digest")
    if not isinstance(stored_digest, str) or stored_digest != recomputed:
        return "DIGEST"
    expected_request_id = idempotency_key(digest=recomputed, model=DEFAULT_MODEL)
    if request.get("request_id") != expected_request_id:
        return "REQUEST_ID"

    if str(request.get("symbol") or "") != str(evidence.get("symbol") or ""):
        return "SYMBOL"
    if str(request.get("direction") or "").upper() != str(
        evidence.get("direction") or ""
    ).upper():
        return "DIRECTION"
    stored_provider = request.get("provider")
    if stored_provider is not None and stored_provider != PROVIDER:
        return "PROVIDER"
    return None



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


def _bounded_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    return _env_int(name, default, minimum=minimum, maximum=maximum)


def _ensure_record_boundary(path: Path) -> None:
    """Terminate a crashed partial tail so a later append cannot merge rows."""
    if not path.exists() or path.stat().st_size <= 0:
        return
    with path.open("rb") as reader:
        reader.seek(-1, os.SEEK_END)
        if reader.read(1) == b"\n":
            return
    with path.open("ab") as handle:
        handle.write(b"\n")
        handle.flush()


def _append_jsonl(path: Path, lock: Path, rows: list[dict], *, timeout: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with registry_lock(lock, timeout=timeout):
        _ensure_record_boundary(path)
        with path.open("a", encoding="utf-8") as handle:
            for row in rows:
                handle.write(
                    json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
                    + "\n"
                )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass


def enqueue_final_qualified(
    *,
    alerts: Iterable[Mapping[str, Any]],
    chief_route: Mapping[str, Any] | None = None,
    market_regime: Any = None,
    max_candidates: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Queue immutable evidence for the finally-qualified recommendations.

    Called from the scan **after** the final deterministic target, economic and
    portfolio/capital gates. Local append-only I/O only: no provider call, no
    credential read, and never an exception into the scan.
    """
    now = now or datetime.now(timezone.utc)
    mode = irr_mode()
    if mode not in ALLOWED_MODES:
        return {"status": STATUS_INVALID_MODE, "enqueued": 0}
    if mode == MODE_OFF:
        return {"status": STATUS_DISABLED, "enqueued": 0}

    limit = (
        max_candidates
        if max_candidates is not None
        else _bounded_int("OPIP_IRR_MAX_CANDIDATES", 2, minimum=1, maximum=3)
    )
    rows: list[dict[str, Any]] = []
    unqualified = 0
    over_limit = 0
    for alert in alerts:
        if not isinstance(alert, Mapping):
            continue
        evidence = build_evidence_copy(
            alert=alert,
            chief_route=chief_route,
            market_regime=market_regime,
        )
        if not qualification_passed(evidence):
            unqualified += 1
            continue
        if len(rows) >= limit:
            over_limit += 1
            continue
        digest = evidence_digest(evidence)
        rows.append(
            {
                "record": "REQUEST",
                "stage": STAGE,
                "provider": PROVIDER,
                "request_id": idempotency_key(digest=digest, model=DEFAULT_MODEL),
                "evidence_digest": digest,
                "evidence": evidence,
                "symbol": evidence["symbol"],
                "direction": evidence["direction"],
                "enqueued_at": now.astimezone(timezone.utc).isoformat(),
                "shadow_only": True,
                "advisory_only": True,
                "authority_changed": False,
            }
        )

    summary: dict[str, Any] = {
        "enqueued": 0,
        "skipped_unqualified": unqualified,
        "skipped_over_limit": over_limit,
    }
    if not rows:
        return {"status": STATUS_NOT_QUALIFIED, **summary}
    try:
        _append_jsonl(
            QUEUE_FILE,
            QUEUE_LOCK,
            rows,
            timeout=PRODUCER_LOCK_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutError):
        # Advisory evidence only: a contended or unavailable queue never blocks
        # a scan, a Telegram delivery or a protection cycle.
        return {"status": STATUS_QUEUE_UNAVAILABLE, **summary}
    summary["enqueued"] = len(rows)
    return {"status": "ENQUEUED", **summary}


#: Ledger lock budget. A reservation is the money gate, so it waits for the
#: lock; a stalled lock still fails closed (no call).
LEDGER_LOCK_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class SpendState:
    """Committed spend for one UTC day, worst-case until settled."""

    calls: int
    tokens: int
    cost_usd: float


def _ledger_timestamp(row: Mapping[str, Any]) -> datetime:
    raw = row.get("timestamp_utc")
    if not isinstance(raw, str) or not raw:
        raise IRRSpendUnavailable("spend ledger record has no timestamp")
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IRRSpendUnavailable(
            "spend ledger record has an invalid timestamp"
        ) from exc
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _ledger_int(row: Mapping[str, Any], key: str) -> int:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IRRSpendUnavailable(f"spend ledger record has an invalid {key}")
    return int(value)


def _ledger_float(row: Mapping[str, Any], key: str) -> float:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise IRRSpendUnavailable(f"spend ledger record has an invalid {key}")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise IRRSpendUnavailable(f"spend ledger record has an invalid {key}")
    return number


def _ledger_records() -> list[dict[str, Any]]:
    """Read the ledger strictly. Any malformed record refuses further spend."""
    if not SPEND_LEDGER_FILE.exists():
        return []
    try:
        raw = SPEND_LEDGER_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise IRRSpendUnavailable("spend ledger is unreadable") from exc
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        if not line.strip():
            raise IRRSpendUnavailable(
                "spend ledger contains a blank or partial record"
            )
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise IRRSpendUnavailable("spend ledger contains malformed JSON") from exc
        if not isinstance(row, dict):
            raise IRRSpendUnavailable("spend ledger record is not an object")
        records.append(row)
    return records


def spend_state(*, now: datetime) -> SpendState:
    """Return today's committed calls, tokens and cost.

    An open reservation counts at its worst case until it is settled, so a
    crashed, hung or concurrent attempt can never look like unspent budget.
    """
    records = _ledger_records()
    reservations: dict[str, dict[str, Any]] = {}
    for row in records:
        kind = row.get("record")
        if kind == "RESERVE":
            reservation_id = row.get("reservation_id")
            if not isinstance(reservation_id, str) or not reservation_id:
                raise IRRSpendUnavailable("reservation record has no id")
            reservations[reservation_id] = row
        elif kind != "SETTLE":
            raise IRRSpendUnavailable("spend ledger contains an unknown record type")

    today = now.astimezone(timezone.utc).date()
    calls = 0
    tokens = 0
    cost = 0.0
    for row in records:
        if row["record"] == "RESERVE":
            if _ledger_timestamp(row).date() != today:
                continue
            calls += 1
            tokens += _ledger_int(row, "reserved_tokens")
            cost += _ledger_float(row, "reserved_cost_usd")
            continue
        reservation_id = row.get("reservation_id")
        reserved = (
            reservations.get(reservation_id)
            if isinstance(reservation_id, str)
            else None
        )
        if reserved is None:
            raise IRRSpendUnavailable("settlement has no matching reservation")
        if _ledger_timestamp(reserved).date() != today:
            continue
        tokens += _ledger_int(row, "actual_tokens") - _ledger_int(
            reserved, "reserved_tokens"
        )
        cost += _ledger_float(row, "actual_cost_usd") - _ledger_float(
            reserved, "reserved_cost_usd"
        )
    return SpendState(
        calls=calls,
        tokens=max(0, tokens),
        cost_usd=round(max(0.0, cost), 6),
    )


def _append_ledger_record(record: Mapping[str, Any]) -> None:
    SPEND_LEDGER_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ensure_record_boundary(SPEND_LEDGER_FILE)
    with SPEND_LEDGER_FILE.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                dict(record), sort_keys=True, separators=(",", ":"), default=str
            )
            + "\n"
        )
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass


def budget_block_reason(
    config: IRRConfig,
    state: SpendState,
    *,
    extra_tokens: int,
    extra_cost_usd: float,
) -> str | None:
    """Return why one more attempt would exceed a budget, or ``None``."""
    if state.calls + 1 > config.daily_call_budget:
        return (
            "daily IRR call budget reached "
            f"({state.calls}/{config.daily_call_budget})"
        )
    if state.tokens + extra_tokens > config.daily_token_budget:
        return (
            "daily IRR token budget would be exceeded "
            f"({state.tokens}+{extra_tokens}/{config.daily_token_budget})"
        )
    if state.cost_usd + extra_cost_usd > config.daily_cost_budget_usd:
        return (
            "daily IRR cost budget would be exceeded "
            f"(${state.cost_usd:.4f}+${extra_cost_usd:.4f}/"
            f"${config.daily_cost_budget_usd:.2f})"
        )
    return None


def reserve_spend(
    config: IRRConfig,
    *,
    reserved_tokens: int,
    reserved_cost_usd: float,
    now: datetime,
) -> tuple[str | None, str | None]:
    """Atomically reserve the worst-case cost of one attempt.

    Returns ``(reservation_id, None)`` when the attempt may proceed, or
    ``(None, reason)`` when a budget forbids it. Any read or write failure
    raises :class:`IRRSpendUnavailable`, so spend fails closed.
    """
    try:
        with registry_lock(SPEND_LEDGER_LOCK, timeout=LEDGER_LOCK_TIMEOUT_SECONDS):
            state = spend_state(now=now)
            reason = budget_block_reason(
                config,
                state,
                extra_tokens=reserved_tokens,
                extra_cost_usd=reserved_cost_usd,
            )
            if reason is not None:
                return None, reason
            reservation_id = os.urandom(16).hex()
            _append_ledger_record(
                {
                    "record": "RESERVE",
                    "reservation_id": reservation_id,
                    "timestamp_utc": now.astimezone(timezone.utc).isoformat(),
                    "provider": PROVIDER,
                    "model": config.model,
                    "reserved_tokens": int(reserved_tokens),
                    "reserved_cost_usd": round(float(reserved_cost_usd), 6),
                }
            )
            return reservation_id, None
    except (OSError, TimeoutError) as exc:
        raise IRRSpendUnavailable("spend ledger is unavailable") from exc


def settle_spend(
    *,
    reservation_id: str,
    actual_tokens: int,
    actual_cost_usd: float,
    outcome: str,
    usage_available: bool,
    now: datetime,
) -> bool:
    """Settle a reservation with the charged usage.

    Returns whether the settlement reached durable storage. A failure leaves the
    reservation counted at its worst case, which over-charges but never
    overspends.
    """
    try:
        with registry_lock(SPEND_LEDGER_LOCK, timeout=LEDGER_LOCK_TIMEOUT_SECONDS):
            _append_ledger_record(
                {
                    "record": "SETTLE",
                    "reservation_id": reservation_id,
                    "timestamp_utc": now.astimezone(timezone.utc).isoformat(),
                    "provider": PROVIDER,
                    "actual_tokens": int(actual_tokens),
                    "actual_cost_usd": round(float(actual_cost_usd), 6),
                    "outcome": outcome,
                    "usage_available": bool(usage_available),
                }
            )
        return True
    except (OSError, TimeoutError):
        return False


@dataclass(frozen=True)
class ProviderAnswer:
    """One provider answer, before any schema validation."""

    content: str
    usage: Mapping[str, Any]
    latency_ms: int


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
    if isinstance(exc, IRRSpendUnavailable):
        return "SPEND_UNAVAILABLE"
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
) -> ProviderAnswer:
    """Perform exactly one bounded provider call; never retry.

    The credential is only ever attached to the approved provider endpoint.
    """
    if config.base_url != APPROVED_BASE_URL:
        raise IRRUnapprovedEndpoint("provider endpoint is not approved")
    url = f"{APPROVED_BASE_URL}{CHAT_COMPLETIONS_PATH}"
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

    usage = payload.get("usage")
    choices = payload.get("choices")
    content = ""
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        raw_content = message.get("content")
        if isinstance(raw_content, str):
            content = raw_content
    return ProviderAnswer(
        content=content,
        usage=usage if isinstance(usage, dict) else {},
        latency_ms=latency_ms,
    )


def _usage_charge(
    usage: Mapping[str, Any],
    *,
    reserved_tokens: int,
    reserved_cost_usd: float,
) -> tuple[int, float, bool]:
    """Return the chargeable (tokens, cost, usage_available) for one attempt.

    A provider may bill a call even when the answer is unusable, and usage may
    be missing. Anything unprovable is charged at the reserved worst case, so
    spend can never be understated.
    """
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    valid = all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in (prompt, completion)
    )
    if not valid:
        return reserved_tokens, reserved_cost_usd, False
    tokens = (
        int(total)
        if isinstance(total, int) and not isinstance(total, bool) and total > 0
        else int(prompt) + int(completion)
    )
    if tokens <= 0:
        return reserved_tokens, reserved_cost_usd, False
    return (
        tokens,
        estimate_cost_usd(input_tokens=int(prompt), output_tokens=int(completion)),
        True,
    )


def _text_list(value: Any, *, limit: int, max_length: int) -> list[str]:
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


def parse_challenge(content: str) -> Mapping[str, Any]:
    """Parse the provider content into a JSON object or refuse it."""
    if not isinstance(content, str) or not content.strip():
        # Documented JSON-mode caveat: the provider may return blank content.
        raise IRRResponseError("provider returned empty advisory content")
    try:
        parsed = json.loads(content)
    except ValueError as exc:
        raise IRRResponseError("provider returned malformed advisory JSON") from exc
    if not isinstance(parsed, dict):
        raise IRRResponseError("provider advisory JSON was not an object")
    return parsed


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


def _append_evidence(record: Mapping[str, Any], *, now: datetime) -> None:
    """Append one audit row; never raise into the consumer loop."""
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
        _append_jsonl(EVIDENCE_FILE, EVIDENCE_LOCK, [row], timeout=LEDGER_LOCK_TIMEOUT_SECONDS)
    except (OSError, TimeoutError):
        # Evidence is append-only and fail-open; it must never block the worker.
        return


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
        # the reservation ledger still bounds the total spend.
        return


def _load_checkpoint() -> int:
    try:
        raw = QUEUE_CHECKPOINT_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return 0
    except OSError as exc:
        raise IndependentReviewError("queue checkpoint is unreadable") from exc
    try:
        payload = json.loads(raw)
        return max(0, int(payload.get("next_line", 0)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise IndependentReviewError("queue checkpoint is unreadable") from exc


def _save_checkpoint(next_line: int) -> None:
    save_json_atomic(QUEUE_CHECKPOINT_FILE, {"next_line": int(next_line)})


def queue_slice(
    start: int,
    limit: int,
) -> tuple[list[tuple[int, str]], int]:
    """Return up to ``limit`` queue rows from ``start`` and the total row count."""
    if not QUEUE_FILE.exists():
        return [], 0
    rows: list[tuple[int, str]] = []
    total = 0
    try:
        with QUEUE_FILE.open("r", encoding="utf-8") as handle:
            for index, line in enumerate(handle):
                total = index + 1
                if index < start:
                    continue
                if len(rows) >= limit:
                    continue
                rows.append((index, line))
    except OSError as exc:
        raise IndependentReviewError("queue is unreadable") from exc
    return rows, total


def _process_queue_row(
    raw: str,
    *,
    config: IRRConfig,
    post: Callable[..., Any] | None,
    now: datetime,
) -> dict[str, Any]:
    """Dispose exactly one queue row: review it, or record why it cannot be."""
    base: dict[str, Any] = {
        "provider": PROVIDER,
        "model": config.model,
        "reasoning_effort": config.reasoning_effort,
        "advisory_only": True,
        "authority_changed": False,
    }
    try:
        request = json.loads(raw)
    except ValueError:
        row = {**base, "status": STATUS_QUEUE_RECORD_UNREADABLE, "error_type": "JSON"}
        _append_evidence(row, now=now)
        return row
    if not isinstance(request, dict) or request.get("record") != "REQUEST":
        row = {
            **base,
            "status": STATUS_QUEUE_RECORD_UNREADABLE,
            "error_type": "SCHEMA",
        }
        _append_evidence(row, now=now)
        return row

    evidence = request.get("evidence")
    if not isinstance(evidence, dict) or not qualification_passed(evidence):
        row = {
            **base,
            "status": STATUS_NOT_QUALIFIED,
            "symbol": request.get("symbol"),
            "direction": request.get("direction"),
            "error_type": "QUALIFICATION",
        }
        _append_evidence(row, now=now)
        return row

    identity_problem = evidence_identity_problem(request, evidence)
    if identity_problem is not None:
        row = {
            **base,
            "status": STATUS_EVIDENCE_MISMATCH,
            "symbol": request.get("symbol"),
            "direction": request.get("direction"),
            "error_type": identity_problem,
        }
        _append_evidence(row, now=now)
        return row

    symbol = str(evidence.get("symbol") or "").strip()
    direction = str(evidence.get("direction") or "").strip().upper()
    if not symbol or direction not in {"LONG", "SHORT"}:
        row = {
            **base,
            "status": STATUS_NOT_QUALIFIED,
            "error_type": "IDENTITY",
            "symbol": symbol,
            "direction": direction,
        }
        _append_evidence(row, now=now)
        return row
    digest = evidence_digest(evidence)
    key = idempotency_key(digest=digest, model=config.model)
    base.update(
        {
            "symbol": symbol,
            "direction": direction,
            "evidence_digest": digest,
            "evidence_idempotency_key": key,
            "chief_route": evidence.get("chief_route"),
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
            "charged_tokens": 0,
            "charged_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    body = build_request(config=config, evidence=evidence)
    reserved_input = input_token_upper_bound(SYSTEM_PROMPT + canonical_json(evidence))
    reserved_output = config.max_output_tokens
    reserved_tokens = reserved_input + reserved_output
    reserved_cost = estimate_cost_usd(
        input_tokens=reserved_input, output_tokens=reserved_output
    )
    base["worst_case_cost_usd"] = reserved_cost
    base["worst_case_tokens"] = reserved_tokens
    if reserved_cost > config.max_call_cost_usd:
        row = {
            **base,
            "status": STATUS_CALL_COST_LIMIT,
            "skipped_reason": (
                f"worst-case call cost ${reserved_cost:.4f} exceeds "
                f"OPIP_IRR_MAX_CALL_COST_USD ${config.max_call_cost_usd:.4f}"
            ),
            "charged_tokens": 0,
            "charged_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    try:
        reservation_id, blocked = reserve_spend(
            config,
            reserved_tokens=reserved_tokens,
            reserved_cost_usd=reserved_cost,
            now=now,
        )
    except IRRSpendUnavailable as exc:
        row = {
            **base,
            "status": STATUS_SPEND_UNVERIFIABLE,
            "skipped_reason": str(exc),
            "charged_tokens": 0,
            "charged_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row
    if reservation_id is None:
        row = {
            **base,
            "status": STATUS_BUDGET_EXHAUSTED,
            "skipped_reason": blocked,
            "charged_tokens": 0,
            "charged_cost_usd": 0.0,
            "latency_ms": 0,
        }
        _append_evidence(row, now=now)
        return row

    usage: Mapping[str, Any] = {}
    latency_ms = 0
    challenge: dict[str, Any] | None = None
    error_type: str | None = None
    try:
        answer = _invoke(config, body, post=post)
        usage = answer.usage
        latency_ms = answer.latency_ms
        challenge = validate_challenge(
            parse_challenge(answer.content), symbol=symbol, direction=direction
        )
    except Exception as exc:  # contained: Stage 2 may never fail the primary path
        error_type = _error_code(exc)

    charged_tokens, charged_cost, usage_available = _usage_charge(
        usage,
        reserved_tokens=reserved_tokens,
        reserved_cost_usd=reserved_cost,
    )
    settled = settle_spend(
        reservation_id=reservation_id,
        actual_tokens=charged_tokens,
        actual_cost_usd=charged_cost,
        outcome="SUCCEEDED" if challenge is not None else "CHARGED_FAILURE",
        usage_available=usage_available,
        now=now,
    )

    row = {
        **base,
        "latency_ms": latency_ms,
        "charged_tokens": int(charged_tokens),
        "charged_cost_usd": round(float(charged_cost), 6),
        "usage_available": usage_available,
        "spend_settled": settled,
    }
    if challenge is None:
        row["status"] = STATUS_FAILED
        row["error_type"] = error_type or "MALFORMED_RESPONSE"
        _append_evidence(row, now=now)
        return row

    row.update(
        {
            "status": STATUS_SUCCEEDED,
            "challenge": challenge,
            "verdict": challenge["verdict"],
            "reasons": challenge["reasons"],
            "error_type": None,
        }
    )
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
        return "EMPTY"
    if statuses <= {STATUS_SUCCEEDED, STATUS_IDEMPOTENT_REPLAY}:
        return STATUS_SUCCEEDED
    if statuses & {STATUS_SUCCEEDED, STATUS_IDEMPOTENT_REPLAY}:
        return "PARTIAL"
    for status in (
        STATUS_SPEND_UNVERIFIABLE,
        STATUS_BUDGET_EXHAUSTED,
        STATUS_CALL_COST_LIMIT,
        STATUS_FAILED,
    ):
        if status in statuses:
            return status
    return STATUS_FAILED


def drain_queue(
    *,
    batch_limit: int | None = None,
    post: Callable[..., Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Process a bounded batch of queued requests in the dedicated worker.

    This is the only place Stage 2 performs provider I/O. It never raises, and a
    global configuration or spend failure leaves the checkpoint unadvanced so no
    evidence is silently dropped.
    """
    now = now or datetime.now(timezone.utc)
    mode = irr_mode()
    if mode not in ALLOWED_MODES:
        return {"status": STATUS_INVALID_MODE, "processed": 0}
    if mode == MODE_OFF:
        return {"status": STATUS_DISABLED, "processed": 0}

    try:
        config = resolve_config()
    except IRRMissingCredential as exc:
        return {
            "status": STATUS_MISSING_CREDENTIAL,
            "processed": 0,
            "skipped_reason": str(exc),
        }
    except IRRUnverifiedModel as exc:
        return {
            "status": STATUS_UNVERIFIED_MODEL,
            "processed": 0,
            "skipped_reason": str(exc),
        }
    except IRRConfigurationError as exc:
        return {
            "status": STATUS_CONFIGURATION_ERROR,
            "processed": 0,
            "skipped_reason": str(exc),
        }

    limit = batch_limit if batch_limit is not None else config.drain_batch
    limit = max(1, min(int(limit), 5))

    try:
        with registry_lock(QUEUE_CONSUMER_LOCK, timeout=LEDGER_LOCK_TIMEOUT_SECONDS):
            start = _load_checkpoint()
            rows, total = queue_slice(start, limit)
            results = [
                _process_queue_row(raw, config=config, post=post, now=now)
                for _index, raw in rows
            ]
            next_line = start + len(rows)
            if rows:
                try:
                    _save_checkpoint(next_line)
                except OSError:
                    # A failed checkpoint only re-processes; the idempotence
                    # ledger prevents a second paid call for the same evidence.
                    next_line = start
    except TimeoutError:
        return {"status": STATUS_QUEUE_UNAVAILABLE, "processed": 0}
    except IndependentReviewError as exc:
        return {
            "status": STATUS_QUEUE_UNAVAILABLE,
            "processed": 0,
            "skipped_reason": str(exc),
        }

    return {
        "status": _overall_status(results),
        "processed": len(results),
        "queue_total_rows": total,
        "next_line": next_line,
        "results": results,
        "budget": config.public_evidence(),
        "shadow_only": True,
        "advisory_only": True,
        "authority_changed": False,
        "funded_trade_authority_changed": False,
    }












