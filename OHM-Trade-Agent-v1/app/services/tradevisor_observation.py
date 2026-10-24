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
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.services.notification_policy import (
    DEFAULT_COOLDOWN_SECONDS,
    confirm_emit,
    is_confirmed_emission,
    release_emit,
    reserve_emit,
    should_emit,
)
from app.services.registry_io import load_json
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

#: Durable per-row acknowledgement markers. Stored in the existing notification
#: policy state (the same file the policy already owns), keyed per observed row,
#: so a settled row is never reported twice and an unreported row is never lost.
OBSERVATION_MARKER_PREFIX = "TRADEVISOR:OBSERVED:"

#: How far back the recovery path will look for unreported eligible rows.
#: The horizon must cover the time a full per-call backlog needs to drain at the
#: governed cadence, otherwise the tail of a sustained backlog ages out before it
#: can be delivered. ``DRAIN_CAPACITY_CYCLES`` is the per-call row cap expressed
#: in digest cycles (4 digests of capacity) plus one cycle of slack.
DEFAULT_RECOVERY_MINUTES = 6 * 60
DRAIN_CAPACITY_CYCLES = 5

#: Marker bookkeeping older than this is pruned on the acknowledgement path.
MARKER_MAX_AGE_DAYS = 30

#: The cohort that is worth telling an operator about: everything substantive
#: passed and only an authority/evidence gate withheld the trade.
OBSERVATION_COHORT = frozenset({"ADVISORY_EVIDENCE_INCOMPLETE"})

#: Phrases that must never appear in an unqualified observation. The guard is
#: deliberately blunt: a false positive costs a rewording, a false negative
#: would read as a trade instruction.
FORBIDDEN_PHRASES = (
    "ENTER NOW",
    "ENTER_NOW",
    "BUY NOW",
    "SET LIMIT ENTRY",
    "SET_LIMIT_ENTRY",
    "ACTIONABLE",
    "Action:",
    "Preferred entry",
    "Do not chase",
    "QUALIFIED",
    "Execution: Human approval required",
)

#: Rewrites applied to internal decision prose before it is shown to an operator.
#: The internal record keeps the verbatim decision (shadow evidence is untouched);
#: only the user-facing rendering is translated into operator language.
_NEUTRALIZATIONS = (
    ("(ENTER_NOW)", ""),
    ("(SET_LIMIT_ENTRY)", ""),
    ("ENTER_NOW", "entry"),
    ("SET_LIMIT_ENTRY", "pullback entry"),
    ("actionable qualification", "entry qualification"),
)

STATUS_DELIVERED = "DELIVERED"
STATUS_DISABLED = "DISABLED"
STATUS_EMPTY = "EMPTY"
STATUS_SUPPRESSED_POLICY = "SUPPRESSED_POLICY"
STATUS_ALREADY_DELIVERED = "ALREADY_DELIVERED"
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
    retry_after_seconds: int | None = None

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "observed": self.observed,
            "suppressed_bounded": self.suppressed_bounded,
            "delivered": self.delivered,
            "alert_family": OBSERVATION_FAMILY,
            "trading_authority_changed": False,
            "reason": self.reason,
            "retry_after_seconds": self.retry_after_seconds,
        }


def _iso(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text.replace("+00:00", "Z") if "T" in text else text


def _stamp(value: object) -> datetime | None:
    """Parse a stored timestamp; unparseable values are simply unknown."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _effective_cooldown_seconds(settings) -> int:
    """Reconcile the configured observation interval with the policy default.

    The stricter of the two always wins, so an operator can lengthen the
    observation cadence but can never make O'Pip chattier than the shared
    notification-policy cooldown already allows.
    """
    try:
        configured = int(
            getattr(settings, "opip_tradevisor_observation_min_interval_minutes", 0) or 0
        )
    except (TypeError, ValueError):
        configured = 0
    return max(configured * 60, DEFAULT_COOLDOWN_SECONDS)


def _recovery_window_minutes(settings) -> int:
    """Recovery horizon: at least a full drain of the per-call backlog.

    One digest per cooldown carries ``MAX_OBSERVATIONS_PER_DIGEST`` rows, and a
    single call may surface four digests' worth. The horizon therefore has to be
    at least ``DRAIN_CAPACITY_CYCLES`` cooldowns, or a sustained backlog loses
    its tail to expiry before it can be delivered.
    """
    cooldown_minutes = max(1, int(_effective_cooldown_seconds(settings) / 60))
    return max(DEFAULT_RECOVERY_MINUTES, cooldown_minutes * DRAIN_CAPACITY_CYCLES)


def _marker_identity(dedup_id: str) -> str:
    return f"{OBSERVATION_MARKER_PREFIX}{dedup_id}"


def _policy_state() -> dict:
    try:
        return _as_dict(load_json(_notification_state_file()))
    except Exception:  # noqa: BLE001 - an unreadable ledger is "not yet reported"
        return {}


def _notification_state_file():
    from app.services import notification_policy

    return notification_policy.STATE_FILE


def observation_already_reported(dedup_id: object) -> bool:
    """True when this exact observation has already been reported durably."""
    key = str(dedup_id or "").strip()
    if not key:
        return False
    return f"{_marker_identity(key)}:{OBSERVATION_EVENT_TYPE}" in _policy_state()


def _content_fingerprint(candidate: dict) -> str:
    material = "|".join(
        (
            str(candidate.get("dedup_id") or ""),
            str(candidate.get("rejection_code") or ""),
            str(candidate.get("source_bar_closed_at") or ""),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _mark_reported(candidates: list[dict], *, now: datetime) -> int:
    """Record the durable per-row acknowledgement after a confirmed delivery.

    The acknowledgement is bookkeeping, not a new attention card, so it is
    written into the notification-policy state under that module's own lock
    without consuming the noncritical attention budget. Only the digest itself
    (one card) passes through ``confirm_emit``.
    """
    markers: dict[str, dict] = {}
    for candidate in candidates:
        dedup_id = str(candidate.get("dedup_id") or "").strip()
        if not dedup_id:
            continue
        markers[f"{_marker_identity(dedup_id)}:{OBSERVATION_EVENT_TYPE}"] = {
            "fingerprint": _content_fingerprint(candidate),
            "sent_at": now.isoformat(),
        }
    if not markers:
        return 0
    from app.services import notification_policy
    from app.services.registry_io import registry_lock, save_json_atomic

    cutoff = now - timedelta(days=MARKER_MAX_AGE_DAYS)
    try:
        with registry_lock(notification_policy.LOCK_FILE):
            state = load_json(notification_policy.STATE_FILE) or {}
            if not isinstance(state, dict):
                state = {}
            # Bound growth on the write path: stale markers are dropped while the
            # new acknowledgement is recorded, in the same locked pass.
            for key in _stale_marker_keys(state, cutoff=cutoff):
                state.pop(key, None)
            state.update(markers)
            save_json_atomic(notification_policy.STATE_FILE, state)
    except Exception:  # noqa: BLE001 - a ledger write failure must not duplicate
        return 0
    return len(markers)


def _as_dict(value: object) -> dict:
    """Return ``value`` when it is a mapping, else an empty mapping.

    Written as a helper (not a conditional expression) so the narrowing is
    explicit and statically checkable: ``X if isinstance(X, dict) else {}``
    does not narrow ``evidence.get(...)`` for a type checker.
    """
    return value if isinstance(value, dict) else {}


def _operator_language(text: object) -> str:
    """Translate internal decision prose into operator language.

    The durable decision record keeps the verbatim internal reason; this is the
    presentation copy only, so a WATCH report can never quote ``ENTER_NOW`` /
    ``SET_LIMIT_ENTRY`` back to an operator as if it were an instruction.
    """
    cleaned = str(text or "")
    for token, replacement in _NEUTRALIZATIONS:
        cleaned = cleaned.replace(token, replacement)
    cleaned = re.sub(r"\s+([;,.)])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _context_field(evidence: dict, name: str) -> dict | None:
    context = evidence.get("native_context")
    if not isinstance(context, dict):
        return None
    field = context.get(name)
    return field if isinstance(field, dict) else None


def _corroboration(direction: str, regime: object) -> str:
    """Independent native corroboration, stated plainly and never inflated."""
    normalised = str(regime).upper() if regime else None
    if not normalised:
        return "UNAVAILABLE"
    if direction == "SELL":
        return "CORROBORATES_BEARISH" if normalised == "BEARISH" else "CONTRADICTS"
    if normalised == "BULLISH":
        return "CORROBORATES_BULLISH"
    if normalised == "BEARISH":
        return "CONTRADICTS"
    return "NEUTRAL"


def _build_candidate(
    *,
    decision_name: object,
    rejection_code: object,
    dedup_id: object,
    signal_direction: object,
    instrument: object,
    market: object,
    source_exchange: object,
    source_symbol: object,
    timeframe_minutes: object,
    horizon: object,
    reason: object,
    evaluated_at: object,
    current_price: object,
    native_regime: object,
    evidence: object,
    observed_at: str,
) -> dict:
    """One candidate shape, used by both the live and the recovered path.

    Keeping a single builder means a row replayed from durable storage produces
    exactly the record a live evaluation would have produced.
    """
    evidence_map = _as_dict(evidence)
    timing = _as_dict(evidence_map.get("timing"))
    qualification = _as_dict(evidence_map.get("advisory_qualification"))
    provenance = _as_dict(evidence_map.get("provenance"))
    btc = _context_field(evidence_map, "btc_regime")
    btc_value = btc.get("value") if btc and btc.get("status") == "AVAILABLE" else None
    return {
        "classification": OBSERVATION_CLASSIFICATION,
        "decision": decision_name,
        "rejection_code": rejection_code,
        "declared_indicator_version": provenance.get("declared_indicator_version"),
        "dedup_id": dedup_id,
        "signal": signal_direction,
        "instrument": instrument,
        "market": market,
        "source_exchange": source_exchange,
        "source_symbol": source_symbol,
        "timeframe_minutes": timeframe_minutes,
        "horizon": horizon,
        "source_bar_closed_at": timing.get("source_bar_closed_at"),
        "evaluated_at": evaluated_at,
        "current_price": current_price,
        "native_regime": native_regime,
        "native_corroboration": _corroboration(str(signal_direction or ""), native_regime),
        "btc_context": btc_value,
        "chase_risk": _context_field(evidence_map, "chase_risk"),
        "liquidity": _context_field(evidence_map, "liquidity"),
        "missing_evidence": list(qualification.get("missing") or []),
        "abstention_reason": _operator_language(reason),
        "observed_at": observed_at,
        "trade_authority_changed": False,
        "measurement_only": True,
    }


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

    return _build_candidate(
        decision_name=decision.decision,
        rejection_code=decision.rejection_code,
        dedup_id=decision.dedup_id,
        signal_direction=decision.signal,
        instrument=decision.instrument,
        market=decision.market,
        source_exchange=decision.source_exchange,
        source_symbol=decision.source_symbol,
        timeframe_minutes=decision.timeframe_minutes,
        horizon=decision.horizon,
        reason=decision.reason,
        evaluated_at=decision.evaluated_at.isoformat(),
        current_price=decision.current_price,
        native_regime=decision.market_regime,
        evidence=decision.evidence,
        observed_at=(now or datetime.now(timezone.utc)).isoformat(),
    )


def candidate_from_record(
    record: object,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Rebuild an observation from a durable inbox row's frozen decision.

    Read-only: the row is never mutated, and a row that is not in the
    substantive cohort (or carries no frozen decision) yields ``None``.
    """
    row = _as_dict(record)
    frozen = _as_dict(row.get("decision_record"))
    if not frozen:
        return None
    if str(frozen.get("rejection_code") or "") not in OBSERVATION_COHORT:
        return None
    signal_record = _as_dict(row.get("signal"))
    dedup_id = row.get("dedup_id") or frozen.get("dedup_id")
    if not dedup_id:
        return None
    try:
        timeframe = int(frozen.get("timeframe_minutes") or 0)
    except (TypeError, ValueError):
        timeframe = 0
    return _build_candidate(
        decision_name=frozen.get("decision"),
        rejection_code=frozen.get("rejection_code"),
        dedup_id=str(dedup_id),
        signal_direction=frozen.get("signal") or signal_record.get("signal"),
        instrument=frozen.get("instrument"),
        market=frozen.get("market") or signal_record.get("market"),
        source_exchange=frozen.get("source_exchange")
        or signal_record.get("source_exchange"),
        source_symbol=frozen.get("source_symbol") or signal_record.get("source_symbol"),
        timeframe_minutes=timeframe,
        horizon=frozen.get("horizon"),
        reason=frozen.get("reason"),
        evaluated_at=frozen.get("evaluated_at"),
        current_price=frozen.get("current_price"),
        native_regime=frozen.get("market_regime"),
        evidence=frozen.get("evidence"),
        observed_at=(now or datetime.now(timezone.utc)).isoformat(),
    )


def _retry_after_seconds(*, now: datetime, cooldown_seconds: int) -> int | None:
    """Seconds until the reconciled observation cooldown expires, if known."""
    entry = _as_dict(
        _policy_state().get(f"{OBSERVATION_IDENTITY}:{OBSERVATION_EVENT_TYPE}")
    )
    sent_at = _stamp(entry.get("sent_at"))
    if sent_at is None:
        return None
    remaining = cooldown_seconds - (now - sent_at).total_seconds()
    return max(0, int(remaining))


def _inbox_records() -> list[dict]:
    """Read the durable inbox rows (read-only; never mutates the store)."""
    from app.services import tradevisor_inbox

    return tradevisor_inbox.iter_records()


def pending_observations(
    *,
    now: datetime | None = None,
    records: list[dict] | None = None,
    window_minutes: int | None = None,
    settings=None,
    limit: int | None = None,
) -> list[dict]:
    """Eligible, durable, not-yet-reported observations awaiting delivery.

    This is the recovery half of advisory delivery. A settled inbox row keeps its
    frozen decision forever, so a crash after settlement, a refused transport, a
    transport exception or a cooldown suppression all leave work that the next
    cycle can find. A row stops being eligible only when a durable per-row
    acknowledgement exists.

    Nothing here writes: discovery of pending work is read-only.
    """
    reference = now or datetime.now(timezone.utc)
    resolved_settings = settings
    if window_minutes is None:
        if resolved_settings is None:
            try:
                from app.core.config import get_settings

                resolved_settings = get_settings()
            except Exception:  # noqa: BLE001 - cadence falls back to the policy default
                resolved_settings = None
        window_minutes = _recovery_window_minutes(resolved_settings)
    horizon = reference - timedelta(minutes=window_minutes)

    candidates: list[dict] = []
    for row in records if records is not None else _inbox_records():
        candidate = candidate_from_record(row, now=reference)
        if candidate is None:
            continue
        stamp = _stamp(candidate.get("evaluated_at"))
        if stamp is None or stamp < horizon:
            continue
        if observation_already_reported(candidate.get("dedup_id")):
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda item: str(item.get("evaluated_at") or ""))
    cap = MAX_OBSERVATIONS_PER_DIGEST * 4 if limit is None else max(1, int(limit))
    return candidates[:cap]


def observation_backlog(
    *,
    now: datetime | None = None,
    settings=None,
    records: list[dict] | None = None,
    window_minutes: int | None = None,
) -> dict:
    """Read-only accounting of every durable row against the delivery state.

    Nothing undelivered is ever *silently* dropped: a row is either eligible, or
    counted as already reported, aged out of the recovery horizon, or not
    observable at all. Callers that want to know whether evidence is being lost
    to expiry read ``aged_out`` here.
    """
    reference = now or datetime.now(timezone.utc)
    resolved_settings = settings
    if window_minutes is None:
        if resolved_settings is None:
            try:
                from app.core.config import get_settings

                resolved_settings = get_settings()
            except Exception:  # noqa: BLE001 - cadence falls back to the policy default
                resolved_settings = None
        window_minutes = _recovery_window_minutes(resolved_settings)
    horizon = reference - timedelta(minutes=window_minutes)

    counts = {
        "total_rows": 0,
        "not_observable": 0,
        "already_reported": 0,
        "aged_out": 0,
        "undated": 0,
        "eligible": 0,
    }
    for row in records if records is not None else _inbox_records():
        counts["total_rows"] += 1
        candidate = candidate_from_record(row, now=reference)
        if candidate is None:
            counts["not_observable"] += 1
            continue
        stamp = _stamp(candidate.get("evaluated_at"))
        if stamp is None:
            counts["undated"] += 1
            continue
        if observation_already_reported(candidate.get("dedup_id")):
            counts["already_reported"] += 1
            continue
        if stamp < horizon:
            counts["aged_out"] += 1
            continue
        counts["eligible"] += 1

    return {
        "schema_version": "tradevisor-observation-backlog-v1",
        "authority": "READ_ONLY_DIAGNOSTIC",
        "window_minutes": window_minutes,
        "horizon": horizon.isoformat(),
        **counts,
    }


def _stale_marker_keys(state: dict, *, cutoff: datetime) -> list[str]:
    stale: list[str] = []
    for key, value in state.items():
        if not str(key).startswith(OBSERVATION_MARKER_PREFIX):
            continue
        sent_at = _stamp(_as_dict(value).get("sent_at"))
        if sent_at is not None and sent_at < cutoff:
            stale.append(str(key))
    return stale


def compact_markers(*, now: datetime | None = None, max_age_days: int = MARKER_MAX_AGE_DAYS) -> int:
    """Prune acknowledgement markers older than ``max_age_days``.

    Only this module's own marker keys are touched; digest, alert and budget
    entries in the same state file are never modified.
    """
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=max_age_days)
    from app.services import notification_policy
    from app.services.registry_io import registry_lock, save_json_atomic

    try:
        with registry_lock(notification_policy.LOCK_FILE):
            state = load_json(notification_policy.STATE_FILE) or {}
            if not isinstance(state, dict):
                return 0
            stale = _stale_marker_keys(state, cutoff=cutoff)
            for key in stale:
                state.pop(key, None)
            if stale:
                save_json_atomic(notification_policy.STATE_FILE, state)
        return len(stale)
    except Exception:  # noqa: BLE001 - compaction is best-effort bookkeeping
        return 0


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
        completeness = value.get("input_completeness") or {}
        if value.get("band_is_indicative_only"):
            lines.append(
                f"  Chase risk: {value.get('band')} (score {value.get('score')}), "
                f"partial inputs {completeness.get('provided')}/"
                f"{completeness.get('total')} - indicative only, advisory only"
            )
        else:
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
    cooldown_seconds = _effective_cooldown_seconds(settings)

    if not should_emit(
        identity=OBSERVATION_IDENTITY,
        event_type=OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
        cooldown_seconds=cooldown_seconds,
        now=now,
    ):
        # A confirmed emission for this exact content means the transport already
        # accepted it; only the per-row acknowledgement is missing (for example a
        # crash or ledger fault between confirmation and marking). Repair it
        # instead of stranding the rows as permanently suppressed, and never send
        # the same content twice.
        if is_confirmed_emission(
            identity=OBSERVATION_IDENTITY,
            event_type=OBSERVATION_EVENT_TYPE,
            fingerprint=fingerprint,
        ):
            repaired = _mark_reported(bounded, now=now)
            return ObservationOutcome(
                status=STATUS_ALREADY_DELIVERED,
                observed=len(bounded),
                suppressed_bounded=suppressed,
                delivered=False,
                reason=f"already confirmed delivered; acknowledgement repaired={repaired}",
            )
        return ObservationOutcome(
            status=STATUS_SUPPRESSED_POLICY,
            observed=len(bounded),
            suppressed_bounded=suppressed,
            reason="notification policy already reported this observation set",
            message=message,
            retry_after_seconds=_retry_after_seconds(now=now, cooldown_seconds=cooldown_seconds),
        )

    reservation = reserve_emit(
        identity=OBSERVATION_IDENTITY,
        event_type=OBSERVATION_EVENT_TYPE,
        fingerprint=fingerprint,
        cooldown_seconds=cooldown_seconds,
        now=now,
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
    failure_reason = "Telegram transport did not accept the message"
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
    except Exception as exc:  # noqa: BLE001 - a transport fault is a failed send,
        # not a lost observation: the reservation is released and the rows stay
        # pending for the next cycle.
        delivered = False
        failure_reason = f"transport raised {type(exc).__name__}: {exc}"
    finally:
        if delivered:
            confirm_emit(
                identity=OBSERVATION_IDENTITY,
                event_type=OBSERVATION_EVENT_TYPE,
                fingerprint=fingerprint,
                reservation_token=reservation,
                now=now,
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
            reason=failure_reason,
            message=message,
        )
    # Durable per-row acknowledgement: only a confirmed delivery marks the rows
    # reported, so a retry can never duplicate and a failure can never discard.
    marked = _mark_reported(bounded, now=now)
    return ObservationOutcome(
        status=STATUS_DELIVERED,
        observed=len(bounded),
        suppressed_bounded=suppressed,
        delivered=True,
        message=message,
        reason=f"marked_reported={marked}",
    )


