"""F10-F decision-first binding for a committed decision card.

Increment: ``ATDD-R5-F10-F``. This module is a **pure presentation binder**. It owns no
eligibility, no dedupe, no delivery and no trading authority, and it performs no I/O.

Why it exists
-------------

The owner reads Telegram for *attention*. An alert that says "actionable" must therefore be
bound to a decision that was actually committed, still valid, and eligible under the existing
notification policy -- and every field that could be mistaken for a fact the platform does not
hold (a price it never saw, a confidence it never calibrated, a funded action it never took)
must fail closed instead of being rendered optimistically.

What it does not do
-------------------

* It never decides eligibility. The notification policy remains the only eligibility authority
  and its verdict is an **input** here: :func:`bind_decision_card` cannot produce an actionable
  card unless the caller passes the policy's own authorization.
* It never invents a price, a probability, a movement range, a health claim, a P&L or a
  Committee conclusion. An uncalibrated score is reported as withheld, never converted into a
  confidence.
* It never merges advisory text into the deterministic facts. Committee material is returned in
  its own ``advisory`` block so a reader cannot mistake an opinion for a measurement.
* It never sends anything. Transport, retry, delivery state and the audit ledger stay with
  ``app.services.telegram_delivery``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from app.services.alert_v2_format import NO_ORDER_LINE, PAPER_LINE

#: A card may only be called actionable with this disposition. Everything else is a refusal, a
#: withholding or an advisory-only presentation.
ACTIONABLE = "ACTIONABLE"
ADVISORY_ONLY = "ADVISORY_ONLY"
REFUSED = "REFUSED"
WITHHELD = "WITHHELD"

#: The modes a card may declare. Anything else, including an unstated mode, cannot be presented
#: as an actionable funded instruction.
MODE_PAPER = "PAPER"
MODE_SHADOW = "SHADOW"
MODE_ADVISORY = "ADVISORY"
MODE_FUNDED = "FUNDED"
PAPER_MODES = frozenset({MODE_PAPER, MODE_SHADOW, MODE_ADVISORY})
DECLARED_MODES = frozenset({MODE_PAPER, MODE_SHADOW, MODE_ADVISORY, MODE_FUNDED})

#: The only marker that permits a confidence to be rendered. A score without it is withheld.
CALIBRATED = "CALIBRATED"

#: Typed refusal and withholding reasons. Each one names what is missing rather than how the
#: card could have been made to look.
REASON_NO_COMMITTED_IDENTITY = "NO_COMMITTED_DECISION_IDENTITY"
REASON_NOT_AUTHORIZED = "NOT_AUTHORIZED_BY_NOTIFICATION_POLICY"
REASON_EXPIRED = "EXPIRED_EVIDENCE_NOT_ACTIONABLE"
REASON_VALIDITY_UNSTATED = "VALIDITY_UNSTATED_NOT_ACTIONABLE"
REASON_NO_PRICE = "NO_COMMITTED_PRICE_EVIDENCE"
REASON_UNCALIBRATED = "UNCALIBRATED_SCORE_IS_NOT_CONFIDENCE"
REASON_PAPER_ONLY = "PAPER_OR_ADVISORY_ONLY_NO_FUNDED_AUTHORITY"
REASON_MODE_UNSTATED = "MODE_UNSTATED"
REASON_NO_POLICY_VERDICT = "POLICY_VERDICT_NOT_SUPPLIED"

#: Fields a card may carry that are deterministic facts. An unlisted field is not rendered, so a
#: producer cannot smuggle a new claim into the card body.
FACT_FIELDS: tuple[str, ...] = (
    "decision_id",
    "instrument",
    "horizon",
    "disposition",
    "entry_evidence",
    "invalidation",
    "target_evidence",
    "capital_context",
    "valid_until",
)

#: Fields that are advisory evidence and are never merged into the facts.
ADVISORY_FIELDS: tuple[str, ...] = ("committee_note", "committee_stance")


@dataclass(frozen=True)
class PolicyAuthorization:
    """The notification policy's verdict, supplied by the caller.

    This type exists so the binder cannot become a second eligibility authority: it consumes a
    verdict, it never produces one.
    """

    authorized: bool
    reason: str | None = None


@dataclass(frozen=True)
class DecisionBinding:
    """One bound card: what may be shown, and what was withheld and why."""

    disposition: str
    reasons: tuple[str, ...] = ()
    decision_id: str | None = None
    mode: str | None = None
    horizon: str | None = None
    valid_until: str | None = None
    invalidation: str | None = None
    price_evidence: str | None = None
    confidence: float | None = None
    confidence_reason: str | None = None
    trace_link: str | None = None
    facts: Mapping[str, Any] = field(default_factory=dict)
    advisory: Mapping[str, Any] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        """Whether this card may be presented as something to act on."""
        return self.disposition == ACTIONABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "disposition": self.disposition,
            "actionable": self.actionable,
            "reasons": list(self.reasons),
            "decision_id": self.decision_id,
            "mode": self.mode,
            "horizon": self.horizon,
            "valid_until": self.valid_until,
            "invalidation": self.invalidation,
            "price_evidence": self.price_evidence,
            "confidence": self.confidence,
            "confidence_reason": self.confidence_reason,
            "trace_link": self.trace_link,
            "facts": dict(self.facts),
            "advisory": dict(self.advisory),
        }


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN is not a measurement
        return None
    return parsed


def _parse_instant(value: Any) -> datetime | None:
    """Parse a timezone-aware instant, or ``None``. A naive instant is refused, never assumed."""
    if isinstance(value, datetime):
        candidate = value
    else:
        text = _text(value)
        if text is None:
            return None
        try:
            candidate = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if candidate.tzinfo is None or candidate.utcoffset() is None:
        return None
    return candidate


def bind_decision_card(
    card: Mapping[str, Any] | None,
    *,
    authorization: PolicyAuthorization | None,
    now: datetime,
) -> DecisionBinding:
    """Bind one card to a committed decision, or refuse and say why.

    The refusals are ordered so the reason a reader sees is the first thing that is actually
    wrong: no committed identity, then no policy verdict, then the policy's own refusal, then an
    unstated or non-funded mode, then an unusable validity, then missing price evidence. Only a
    card that survives all of them can be actionable, and even then only its calibrated
    confidence is rendered -- an uncalibrated score is withheld beside it.
    """
    payload: Mapping[str, Any] = card or {}
    identity = _text(payload.get("decision_id"))
    if identity is None:
        return DecisionBinding(disposition=REFUSED, reasons=(REASON_NO_COMMITTED_IDENTITY,))
    if authorization is None:
        return DecisionBinding(
            disposition=REFUSED,
            reasons=(REASON_NO_POLICY_VERDICT,),
            decision_id=identity,
        )
    if not authorization.authorized:
        reasons = [REASON_NOT_AUTHORIZED]
        policy_reason = _text(authorization.reason)
        if policy_reason is not None:
            reasons.append(policy_reason)
        return DecisionBinding(
            disposition=REFUSED, reasons=tuple(reasons), decision_id=identity
        )

    mode = _text(payload.get("mode"))
    if mode is None or mode.upper() not in DECLARED_MODES:
        return DecisionBinding(
            disposition=REFUSED,
            reasons=(REASON_MODE_UNSTATED,),
            decision_id=identity,
        )
    mode = mode.upper()

    facts = {name: payload[name] for name in FACT_FIELDS if name in payload}
    advisory = {name: payload[name] for name in ADVISORY_FIELDS if name in payload}
    common = {
        "decision_id": identity,
        "mode": mode,
        "horizon": _text(payload.get("horizon")),
        "valid_until": _text(payload.get("valid_until")),
        "invalidation": _text(payload.get("invalidation")),
        "trace_link": _text(payload.get("trace_link")),
        "facts": facts,
        "advisory": advisory,
    }

    # Validity. An unstated or unparseable validity is not actionable: a card whose expiry the
    # platform cannot state is a card it cannot promise is current.
    valid_until = _parse_instant(payload.get("valid_until"))
    if valid_until is None:
        return DecisionBinding(
            disposition=WITHHELD, reasons=(REASON_VALIDITY_UNSTATED,), **common
        )
    if valid_until <= now:
        return DecisionBinding(disposition=REFUSED, reasons=(REASON_EXPIRED,), **common)

    # Mode. A paper, shadow or advisory card is never presented as a funded instruction.
    if mode in PAPER_MODES:
        return DecisionBinding(
            disposition=ADVISORY_ONLY, reasons=(REASON_PAPER_ONLY,), **common
        )

    # Price. An actionable card must carry committed price evidence; nothing is invented.
    price_evidence = _text(payload.get("entry_evidence"))
    if price_evidence is None:
        return DecisionBinding(
            disposition=REFUSED, reasons=(REASON_NO_PRICE,), price_evidence=None, **common
        )

    # Confidence. Only a calibrated marker with a numeric value may be rendered; an ordinal
    # score stays an ordinal score and is withheld with its reason.
    confidence: float | None = None
    confidence_reason: str | None = None
    if _text(payload.get("confidence_marker")) == CALIBRATED:
        confidence = _number(payload.get("confidence"))
        if confidence is None:
            confidence_reason = REASON_UNCALIBRATED
    else:
        confidence_reason = REASON_UNCALIBRATED

    return DecisionBinding(
        disposition=ACTIONABLE,
        price_evidence=price_evidence,
        confidence=confidence,
        confidence_reason=confidence_reason,
        **common,
    )


def render_decision_card(binding: DecisionBinding) -> str:
    """Render one binding in the decision-first order the owner reads.

    Nothing is added that the binding does not hold: an absent field is absent, a withheld
    confidence says it is withheld, and the safety line states the mode rather than implying
    funded authority.
    """
    lines: list[str] = []
    horizon = f" — {binding.horizon}" if binding.horizon else ""
    lines.append(
        f"{binding.disposition}: {binding.decision_id or '(no committed decision)'}{horizon}"
    )
    if binding.reasons:
        lines.append("Held because: " + ", ".join(binding.reasons))
    if binding.facts:
        lines.append("Committed evidence:")
        for name in FACT_FIELDS:
            if name in binding.facts and name != "decision_id":
                lines.append(f"  {name}: {binding.facts[name]}")
    if binding.price_evidence:
        lines.append(f"Entry evidence (committed): {binding.price_evidence}")
    else:
        lines.append("Entry evidence (committed): none supplied")
    if binding.invalidation:
        lines.append(f"Invalidation: {binding.invalidation}")
    if binding.valid_until:
        lines.append(f"Valid until: {binding.valid_until}")
    if binding.confidence is not None:
        lines.append(f"Calibrated confidence: {binding.confidence:g}")
    elif binding.confidence_reason:
        lines.append(f"Confidence: WITHHELD ({binding.confidence_reason})")
    if binding.trace_link:
        lines.append(f"Decision trace: {binding.trace_link}")
    if binding.advisory:
        lines.append("Advisory (not deterministic evidence, no authority):")
        for name in ADVISORY_FIELDS:
            if name in binding.advisory:
                lines.append(f"  {name}: {binding.advisory[name]}")
    if binding.mode in PAPER_MODES:
        lines.append(PAPER_LINE)
    elif binding.mode == MODE_FUNDED:
        lines.append(NO_ORDER_LINE)
    else:
        lines.append("Mode unstated. No order was placed or changed.")
    return "\n".join(lines)


__all__ = [
    "ACTIONABLE",
    "ADVISORY_FIELDS",
    "ADVISORY_ONLY",
    "CALIBRATED",
    "DECLARED_MODES",
    "DecisionBinding",
    "FACT_FIELDS",
    "MODE_ADVISORY",
    "MODE_FUNDED",
    "MODE_PAPER",
    "MODE_SHADOW",
    "PAPER_MODES",
    "PolicyAuthorization",
    "REASON_EXPIRED",
    "REASON_MODE_UNSTATED",
    "REASON_NOT_AUTHORIZED",
    "REASON_NO_COMMITTED_IDENTITY",
    "REASON_NO_POLICY_VERDICT",
    "REASON_NO_PRICE",
    "REASON_PAPER_ONLY",
    "REASON_UNCALIBRATED",
    "REASON_VALIDITY_UNSTATED",
    "REFUSED",
    "WITHHELD",
    "bind_decision_card",
    "render_decision_card",
]
