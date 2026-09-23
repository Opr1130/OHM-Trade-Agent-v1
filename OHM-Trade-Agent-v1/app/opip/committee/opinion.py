"""Strict parsing of untrusted model output into a validated opinion.

MEASUREMENT ONLY - NO PRODUCTION DECISION AUTHORITY.

Model-generated text is untrusted input. This module is the only place raw
model output becomes a committee observation, and it fails closed:

* non-JSON, non-object, or markdown-fenced output is ``MALFORMED_RESPONSE``;
* JSON that violates the contract is ``SCHEMA_VALIDATION_FAILURE``;
* unknown fields are rejected rather than stored;
* evidence references must resolve to evidence the seat actually received, so a
  hallucinated citation cannot enter the record;
* nothing is repaired, guessed, defaulted, or coerced.

A rejected response becomes an explicit INVALID observation. It is never
silently parsed into a plausible-looking opinion.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping

from app.opip.committee.contracts import (
    DirectionalAssessment,
    EvidenceSufficiency,
    ProviderFailureClass,
    ResearchAction,
    StructuredOpinion,
)

MAX_HYPOTHESIS_CHARS = 4_000
MAX_ABSTENTION_CHARS = 1_000
MAX_LIST_ITEMS = 20
MAX_LIST_ITEM_CHARS = 500

#: The complete, closed set of opinion fields a model may return.
OPINION_FIELDS = frozenset(
    {
        "schema_version",
        "evidence_sufficiency",
        "assessment",
        "hypothesis",
        "confidence",
        "supporting_evidence_refs",
        "contradicting_evidence_refs",
        "major_assumptions",
        "risk_factors",
        "missing_evidence",
        "alternative_explanations",
        "recommended_research_action",
        "abstention_reason",
    }
)

_STRING_LIST_FIELDS = (
    "supporting_evidence_refs",
    "contradicting_evidence_refs",
    "major_assumptions",
    "risk_factors",
    "missing_evidence",
    "alternative_explanations",
)


class OpinionParseError(ValueError):
    """A response could not be admitted as a structured opinion."""

    def __init__(self, message: str, *, failure_class: ProviderFailureClass) -> None:
        super().__init__(message)
        self.failure_class = failure_class


def _malformed(message: str) -> OpinionParseError:
    return OpinionParseError(
        message, failure_class=ProviderFailureClass.MALFORMED_RESPONSE
    )


def _schema(message: str) -> OpinionParseError:
    return OpinionParseError(
        message, failure_class=ProviderFailureClass.SCHEMA_VALIDATION_FAILURE
    )


def _reject_json_constant(token: str) -> None:
    raise ValueError(f"non-finite JSON numeric token {token}")


def _require_enum(payload: Mapping[str, Any], field: str, enum_type: type) -> Any:
    raw = payload.get(field)
    if not isinstance(raw, str):
        raise _schema(f"{field} must be a string enum member")
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise _schema(f"{field} is not a declared {enum_type.__name__}") from exc


def _require_string(
    payload: Mapping[str, Any], field: str, *, maximum: int
) -> str:
    raw = payload.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise _schema(f"{field} must be a non-empty string")
    text = raw.strip()
    if len(text) > maximum:
        raise _schema(f"{field} exceeds {maximum} characters")
    return text


def _require_string_list(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    """Require a declared list field to be present as a list of strings.

    An absent key is a schema failure rather than an empty list. The prompt and
    the parser both require the exact field set, and silently defaulting a missing
    field would record an incomplete response as schema-valid. An explicitly
    present empty list remains valid, because that is the model asserting "none".
    """
    if field not in payload:
        raise _schema(f"{field} is required and must be present")
    raw = payload[field]
    if not isinstance(raw, list):
        raise _schema(f"{field} must be a list of strings")
    if len(raw) > MAX_LIST_ITEMS:
        raise _schema(f"{field} exceeds {MAX_LIST_ITEMS} entries")
    items: list[str] = []
    for entry in raw:
        if not isinstance(entry, str) or not entry.strip():
            raise _schema(f"{field} entries must be non-empty strings")
        text = entry.strip()
        if len(text) > MAX_LIST_ITEM_CHARS:
            raise _schema(f"{field} entry exceeds {MAX_LIST_ITEM_CHARS} characters")
        items.append(text)
    return tuple(items)


def _require_optional_confidence(payload: Mapping[str, Any]) -> int | None:
    raw = payload.get("confidence")
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _schema("confidence must be an integer or null")
    if not 0 <= raw <= 100:
        raise _schema("confidence must be within 0..100")
    return raw


def _require_optional_abstention(payload: Mapping[str, Any]) -> str | None:
    raw = payload.get("abstention_reason")
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise _schema("abstention_reason must be a non-empty string or null")
    text = raw.strip()
    if len(text) > MAX_ABSTENTION_CHARS:
        raise _schema(f"abstention_reason exceeds {MAX_ABSTENTION_CHARS} characters")
    return text


def parse_structured_opinion(
    *,
    raw_text: str,
    case_id: str,
    provider: str,
    model: str,
    allowed_evidence_refs: Iterable[str],
) -> StructuredOpinion:
    """Parse and validate one model response into an admitted opinion."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise _malformed("response body is empty")
    try:
        decoded = json.loads(raw_text, parse_constant=_reject_json_constant)
    except ValueError as exc:
        raise _malformed("response is not well-formed JSON") from exc
    if not isinstance(decoded, dict):
        raise _malformed("response JSON must be an object")

    unknown = sorted(set(decoded) - OPINION_FIELDS)
    if unknown:
        raise _schema(f"undeclared opinion fields: {unknown}")

    if decoded.get("schema_version") != StructuredOpinion.__dataclass_fields__[
        "schema_version"
    ].default or type(decoded.get("schema_version")) is not int:
        # ``type(...) is not int`` is required, not decorative: ``True == 1`` in
        # Python, so a JSON ``true`` would otherwise satisfy the equality check and
        # be admitted as schema version 1.
        raise _schema("schema_version must be exactly 1")

    sufficiency = _require_enum(decoded, "evidence_sufficiency", EvidenceSufficiency)
    assessment = _require_enum(decoded, "assessment", DirectionalAssessment)
    action = _require_enum(
        decoded, "recommended_research_action", ResearchAction
    )
    hypothesis = _require_string(decoded, "hypothesis", maximum=MAX_HYPOTHESIS_CHARS)
    confidence = _require_optional_confidence(decoded)
    abstention = _require_optional_abstention(decoded)
    lists = {
        field: _require_string_list(decoded, field) for field in _STRING_LIST_FIELDS
    }

    allowed = set(allowed_evidence_refs)
    for field in ("supporting_evidence_refs", "contradicting_evidence_refs"):
        unknown_refs = sorted(set(lists[field]) - allowed)
        if unknown_refs:
            raise _schema(f"{field} cites evidence outside the snapshot: {unknown_refs}")

    try:
        return StructuredOpinion(
            case_id=case_id,
            provider=provider,
            model=model,
            evidence_sufficiency=sufficiency,
            assessment=assessment,
            hypothesis=hypothesis,
            confidence=confidence,
            recommended_research_action=action,
            abstention_reason=abstention,
            **lists,
        )
    except ValueError as exc:
        raise _schema(str(exc)) from exc


__all__ = [
    "MAX_ABSTENTION_CHARS",
    "MAX_HYPOTHESIS_CHARS",
    "MAX_LIST_ITEMS",
    "MAX_LIST_ITEM_CHARS",
    "OPINION_FIELDS",
    "OpinionParseError",
    "parse_structured_opinion",
]
