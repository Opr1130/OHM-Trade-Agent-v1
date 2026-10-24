"""Separately classified Tradevisor advisory observations.

A Tradevisor candidate that clears the market, attainability and economic gates
but is withheld by an authority/evidence gate (F6, SWING 4H) is genuinely useful
information. Silence is not: an operator cannot tell "nothing happened" from
"something qualified and was withheld".

This module renders that cohort as an OBSERVATION. Hard rules:

* it is a different message class from an actionable recommendation, with its
  own title, event type and delivery family;
* it never contains an entry instruction, limit price, stop, target or size;
* it never uses qualified/actionable wording, and the formatter refuses to
  render one that does;
* it is delivery-bounded (one digest per run, capped items), deduplicated by
  the existing notification policy, and it can never claim a delivery the
  transport refused;
* it is disabled by default (``OPIP_TRADEVISOR_OBSERVATION_ENABLED``).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.notification_policy import (
    confirm_emit,
    release_emit,
    reserve_emit,
    should_emit,
)
from app.services.telegram_delivery import send_tracked_telegram
from app.services.tradevisor_evaluation import (
    ACTIONABLE_DECISIONS,
    TradevisorDecision,
)

#: The required heading. Operators filter on it; do not localize it.
OBSERVATION_TITLE = "TRADEVISOR OBSERVATION \u2014 WATCH / NO TRADE"
OBSERVATION_CLASSIFICATION = "WATCH / NO TRADE"
OBSERVATION_FOOTER = (
    "NO TRADE RECOMMENDATION \u2014 no entry, stop, target or size is implied."
)
OBSERVATION_EVENT_TYPE = "ADVISORY_OBSERVATION"
OBSERVATION_FAMILY = "TRADEVISOR_OBSERVATION"
OBSERVATION_IDENTITY = "TRADEVISOR:OBSERVATION"

MAX_OBSERVATIONS_PER_DIGEST = 5

#: The cohort that is worth telling an operator about: everything substantive
#: passed and only an authority/evidence gate withheld the trade.
OBSERVATION_COHORT = frozenset({"ADVISORY_EVIDENCE_INCOMPLETE"})

#: Phrases that must never appear in an unqualified observation. The guard is
#: deliberately blunt: a false positive costs a rewording, a false negative
#: would read as a trade instruction.
FORBIDDEN_PHRASES = (
    "ENTER NOW",
    "BUY NOW",
    "SET LIMIT ENTRY",
    "Action:",
    "Preferred entry",
    "Do not chase",
    "QUALIFIED",
    "Execution: Human approval required",
)

STATUS_DELIVERED = "DELIVERED"
STATUS_DISABLED = "DISABLED"
STATUS_EMPTY = "EMPTY"
STATUS_SUPPRESSED_POLICY = "SUPPRESSED_POLICY"
STATUS_SEND_FAILED = "SEND_FAILED"
STATUS_TELEGRAM_DISABLED = "TELEGRAM_DISABLED"


@dataclass(frozen=True)
class ObservationOutcome:
    status: str
    observed: int = 0
    suppressed_bounded: int = 0
    delivered: bool = False
    reason: str | None = None
    message: str | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "observed": self.observed,
            "suppressed_bounded": self.suppressed_bounded,
            "delivered": self.delivered,
            "alert_family": OBSERVATION_FAMILY,
            "trading_authority_changed": False,
            "reason": self.reason,
        }


def _iso(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text.replace("+00:00", "Z") if "T" in text else text


def _as_dict(value: object) -> dict:
    """Return ``value`` when it is a mapping, else an empty mapping.

    Written as a helper (not a conditional expression) so the narrowing is
    explicit and statically checkable: ``X if isinstance(X, dict) else {}``
    does not narrow ``evidence.get(...)`` for a type checker.
    """
    return value if isinstance(value, dict) else {}


def _context_field(evidence: dict, name: str) -> dict | None:
    context = evidence.get("native_context")
    if not isinstance(context, dict):
        return None
    field = context.get(name)
    return field if isinstance(field, dict) else None


def _corroboration(direction: str, regime: str | None) -> str:
    """Independent native corroboration, stated plainly and never inflated."""
    if not regime:
        return "UNAVAILABLE"
    if direction == "SELL":
        return "CORROBORATES_BEARISH" if regime == "BEARISH" else "CONTRADICTS"
    if regime == "BULLISH":
        return "CORROBORATES_BULLISH"
    if regime == "BEARISH":
        return "CONTRADICTS"
    return "NEUTRAL"


def observation_candidate(
    decision: TradevisorDecision,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Return an observation record for the substantive-but-withheld cohort.

    Everything else - low-quality rejections, unavailable market data, SELL
    evidence, and any actionable decision - returns ``None`` so it can never be
    reported as an observation.
    """
    if decision.decision in ACTIONABLE_DECISIONS:
        return None
    if decision.rejection_code not in OBSERVATION_COHORT:
        return None

    evidence = _as_dict(decision.evidence)
    timing = _as_dict(evidence.get("timing"))
    qualification = _as_dict(evidence.get("advisory_qualification"))
    btc = _context_field(evidence, "btc_regime")
    btc_value = btc.get("value") if btc and btc.get("status") == "AVAILABLE" else None
    native_regime = decision.market_regime
    provenance = _as_dict(evidence.get("provenance"))

    return {
        "classification": OBSERVATION_CLASSIFICATION,
        "decision": decision.decision,
        "rejection_code": decision.rejection_code,
        "declared_indicator_version": provenance.get("declared_indicator_version"),
        "dedup_id": decision.dedup_id,
        "signal": decision.signal,
        "instrument": decision.instrument,
        "market": decision.market,
        "source_exchange": decision.source_exchange,
        "source_symbol": decision.source_symbol,
        "timeframe_minutes": decision.timeframe_minutes,
        "horizon": decision.horizon,
        "source_bar_closed_at": timing.get("source_bar_closed_at"),
        "evaluated_at": decision.evaluated_at.isoformat(),
        "current_price": decision.current_price,
        "native_regime": native_regime,
        "native_corroboration": _corroboration(decision.signal, native_regime),
        "btc_context": btc_value,
        "chase_risk": _context_field(evidence, "chase_risk"),
        "liquidity": _context_field(evidence, "liquidity"),
        "missing_evidence": list(qualification.get("missing") or []),
        "abstention_reason": decision.reason,
        "observed_at": (now or datetime.now(timezone.utc)).isoformat(),
        "trade_authority_changed": False,
        "measurement_only": True,
    }


def _item_lines(candidate: dict) -> list[str]:
    """Body of one observation. Contains no instruction of any kind."""
    direction = str(candidate.get("signal") or "?")
    horizon = str(candidate.get("horizon") or "UNCLASSIFIED")
    lines = [
        f"{candidate.get('instrument')} ({candidate.get('source_exchange')} "
        f"{candidate.get('source_symbol')} -> Kraken {candidate.get('market')})",
        f"  External observation: {direction} {horizon} "
        f"(version {candidate.get('declared_indicator_version') or 'undeclared'}, "
        "algorithm unverified)",
        f"  Source bar closed: {_iso(candidate.get('source_bar_closed_at'))}"
        f" | Evaluated: {_iso(candidate.get('evaluated_at'))}",
    ]

    corroboration = str(candidate.get("native_corroboration") or "UNAVAILABLE")
    regime = candidate.get("native_regime") or "UNAVAILABLE"
    lines.append(f"  Native 1H regime: {regime} \u2014 {corroboration}")

    btc = candidate.get("btc_context")
    lines.append(f"  BTC context: {btc or 'UNAVAILABLE'}")

    chase = candidate.get("chase_risk")
    if isinstance(chase, dict) and chase.get("status") == "AVAILABLE":
        value = chase.get("value") or {}
        lines.append(
            f"  Chase risk: {value.get('band')} (score {value.get('score')}), "
            "advisory only"
        )
    else:
        lines.append(
            "  Chase risk: UNAVAILABLE"
            + (f" ({chase.get('reason')})" if isinstance(chase, dict) else "")
        )

    liquidity = candidate.get("liquidity")
    if isinstance(liquidity, dict) and liquidity.get("status") == "AVAILABLE":
        value = liquidity.get("value") or {}
        lines.append(
            f"  Executable liquidity: spread {value.get('spread_bps')} bps, "
            f"round-trip drag {value.get('round_trip_market_drag_pct')}%"
        )
    else:
        lines.append("  Executable liquidity: UNAVAILABLE")

    missing = candidate.get("missing_evidence") or []
    lines.append(
        "  Withheld by: "
        + (", ".join(str(item) for item in missing) if missing else "UNSPECIFIED_GATE")
    )
    reason = str(candidate.get("abstention_reason") or "").strip()
    if reason:
        lines.append(f"  Assessment: {reason}")
    return lines


def _guard(text: str) -> str:
    """Refuse to render anything that reads like a trade instruction."""
    upper = text.upper()
    for phrase in FORBIDDEN_PHRASES:
        if phrase.upper() in upper:
            raise ValueError(f"observation text contains a forbidden instruction: {phrase}")
    return text


def format_observation(candidate: dict) -> str:
    """Render one observation block (single-candidate form)."""
    lines = [OBSERVATION_TITLE, ""]
    lines.extend(_item_lines(candidate))
    lines.extend(["", OBSERVATION_FOOTER])
    return _guard("\n".join(lines))


def format_observation_digest(
    candidates: list[dict],
    *,
    now: datetime,
    suppressed: int = 0,
) -> str:
    """Render a bounded digest. One heading, one footer, many items."""
    bounded = candidates[:MAX_OBSERVATIONS_PER_DIGEST]
    header = (
        f"{OBSERVATION_TITLE}\n"
        f"{now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} | "
        f"{len(bounded)} observation(s) | "
        f"{max(0, int(suppressed))} further withheld (bounded)"
    )
    blocks: list[str] = [header]
    for index, candidate in enumerate(bounded, start=1):
        blocks.append("")
        item = _item_lines(candidate)
        blocks.append(f"{index}. {item[0]}")
        blocks.extend(item[1:])
    blocks.extend(["", OBSERVATION_FOOTER])
    return _guard("\n".join(blocks))


def _fingerprint(candidates: list[dict]) -> str:
    material = "|".join(
        sorted(
            f"{row.get('market')}:{row.get('rejection_code')}:"
            f"{row.get('source_bar_closed_at')}"
            for row in candidates[:MAX_OBSERVATIONS_PER_DIGEST]
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def send_observation_digest(
    candidates: list[dict],
    *,
    settings,
    now: datetime | None = None,
    sender=None,
) -> ObservationOutcome:
    """Deliver at most one bounded observation digest through existing policy.

    Fail-closed on every ambiguity: disabled configuration, no candidates, no
    Telegram, policy suppression, or a transport refusal all return an explicit
    status and never a pretended delivery.
    """
    now = now or datetime.now(timezone.utc)
    if not bool(getattr(settings, "opip_tradevisor_observation_enabled", False)):
        return ObservationOutcome(
            status=STATUS_DISABLED, reason="observations are disabled by configuration"
        )

    rows = [row for row in (candidates or []) if isinstance(row, dict)]
    if not rows:
        return ObservationOutcome(status=STATUS_EMPTY)

    bounded = rows[:MAX_OBSERVATIONS_PER_DIGEST]
    suppressed = len(rows) - len(bounded)

    bot_token = getattr(settings, "telegram_bot_token", None)
    chat_id = getattr(settings, "telegram_chat_id", None)
    if not (
        bool(getattr(settings, "telegram_enabled", False)) and bot_token and chat_id
    ):
        return ObservationOutcome(
            status=STATUS_TELEGRAM_DISABLED,
            observed=len(bounded),
            suppressed_bounded=suppressed,
            reason="Telegram delivery is not enabled or configured",
        )

    message = format_observation_digest(bounded, now=now, suppressed=suppressed)
    fingerprint = _fingerprint(bounded)

    if not should_emit(
        identity=OBSERVATION_IDENTITY,
        event_type=OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
    ):
        return ObservationOutcome(
            status=STATUS_SUPPRESSED_POLICY,
            observed=len(bounded),
            suppressed_bounded=suppressed,
            reason="notification policy already reported this observation set",
            message=message,
        )

    reservation = reserve_emit(
        identity=OBSERVATION_IDENTITY,
        event_type=OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
    )
    if reservation is None:
        return ObservationOutcome(
            status=STATUS_SUPPRESSED_POLICY,
            observed=len(bounded),
            suppressed_bounded=suppressed,
            reason="another worker holds the observation reservation",
            message=message,
        )

    deliver = sender or send_tracked_telegram
    delivered = False
    try:
        result = deliver(
            bot_token=bot_token,
            chat_id=chat_id,
            message=message,
            identity=OBSERVATION_IDENTITY,
            alert_family=OBSERVATION_FAMILY,
            event_type=OBSERVATION_EVENT_TYPE,
            fingerprint=fingerprint,
            symbol="OBSERVATION",
            pair="MULTI",
            signal_id=fingerprint[:16],
        )
        delivered = bool(getattr(result, "delivered", False))
    finally:
        if delivered:
            confirm_emit(
                identity=OBSERVATION_IDENTITY,
                event_type=OBSERVATION_EVENT_TYPE,
                fingerprint=fingerprint,
                reservation_token=reservation,
            )
        else:
            # Release immediately so the next cycle can retry honestly.
            release_emit(
                identity=OBSERVATION_IDENTITY,
                event_type=OBSERVATION_EVENT_TYPE,
                reservation_token=reservation,
            )

    if not delivered:
        return ObservationOutcome(
            status=STATUS_SEND_FAILED,
            observed=len(bounded),
            suppressed_bounded=suppressed,
            reason="Telegram transport did not accept the message",
            message=message,
        )
    return ObservationOutcome(
        status=STATUS_DELIVERED,
        observed=len(bounded),
        suppressed_bounded=suppressed,
        delivered=True,
        message=message,
    )


