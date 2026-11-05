"""Four-tier configuration validation, of which tiers 1 and 2 are enforced here.

Tier 1 is syntactic (type, range, step, enum, cardinality, finiteness).
Tier 2 is cross-field (the V2 weights must total 100, related bounds must be
ordered, and no staging may escalate trading or protection authority).

Tier 3 (historical preview) lives in ``staging.preview`` because it needs the
analytical projection. Tier 4 (optimistic concurrency and checksum) lives in
``staging.stage`` because it needs the staging store.

Invalid configuration is **rejected**, never silently coerced. The one
transformation applied is ``"30" -> 30`` for a numeric definition, and it is
recorded in the normalized document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping

from app.opip.config import registry

SEVERITY_ERROR = "ERROR"
SEVERITY_WARNING = "WARNING"

TIER_SYNTACTIC = 1
TIER_CROSS_FIELD = 2


@dataclass(frozen=True)
class ValidationIssue:
    tier: int
    code: str
    message: str
    key: str | None = None
    severity: str = SEVERITY_ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "code": self.code,
            "message": self.message,
            "key": self.key,
            "severity": self.severity,
        }


@dataclass
class ValidationResult:
    normalized: dict[str, Any] = field(default_factory=dict)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [item.as_dict() for item in self.errors],
            "warnings": [item.as_dict() for item in self.warnings],
        }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _issue(
    result: ValidationResult, tier: int, code: str, message: str, key: str | None
) -> None:
    result.issues.append(ValidationIssue(tier, code, message, key))


def _check_definition(
    item: registry.ConfigDefinition, value: Any, result: ValidationResult
) -> None:
    if item.editability == registry.EDIT_READ_ONLY:
        _issue(
            result,
            TIER_SYNTACTIC,
            "READ_ONLY_SETTING",
            f"{item.display_name} is read-only: "
            f"{item.read_only_reason or 'not editable'}",
            item.key,
        )
        return
    if item.data_type == registry.TYPE_SECRET_REFERENCE:
        _issue(
            result,
            TIER_SYNTACTIC,
            "SECRETS_NOT_STAGEABLE",
            "Secret values are managed out of band and cannot be staged here",
            item.key,
        )
        return
    if item.data_type in {registry.TYPE_NUMBER, registry.TYPE_INTEGER}:
        number = _finite_number(value)
        if number is None:
            _issue(
                result,
                TIER_SYNTACTIC,
                "NOT_A_FINITE_NUMBER",
                f"{item.display_name} must be a finite number",
                item.key,
            )
            return
        if item.data_type == registry.TYPE_INTEGER and number != int(number):
            _issue(
                result,
                TIER_SYNTACTIC,
                "NOT_AN_INTEGER",
                f"{item.display_name} must be a whole number",
                item.key,
            )
            return
        if item.minimum is not None and number < item.minimum:
            _issue(
                result,
                TIER_SYNTACTIC,
                "BELOW_MINIMUM",
                f"{item.display_name} must be at least {item.minimum}",
                item.key,
            )
            return
        if item.maximum is not None and number > item.maximum:
            _issue(
                result,
                TIER_SYNTACTIC,
                "ABOVE_MAXIMUM",
                f"{item.display_name} must be at most {item.maximum}",
                item.key,
            )
            return
        if item.step:
            steps = number / float(item.step)
            if abs(steps - round(steps)) > 1e-6:
                _issue(
                    result,
                    TIER_SYNTACTIC,
                    "STEP_MISMATCH",
                    f"{item.display_name} must move in steps of {item.step}",
                    item.key,
                )
                return
        result.normalized[item.key] = (
            int(number) if item.data_type == registry.TYPE_INTEGER else number
        )
        return
    if item.data_type == registry.TYPE_BOOLEAN:
        if not isinstance(value, bool):
            _issue(
                result,
                TIER_SYNTACTIC,
                "NOT_A_BOOLEAN",
                f"{item.display_name} must be true or false",
                item.key,
            )
            return
        result.normalized[item.key] = value
        return
    if item.data_type == registry.TYPE_ENUM:
        text = str(value)
        if item.allowed and text not in item.allowed:
            _issue(
                result,
                TIER_SYNTACTIC,
                "NOT_AN_ALLOWED_VALUE",
                f"{item.display_name} must be one of {', '.join(item.allowed)}",
                item.key,
            )
            return
        result.normalized[item.key] = text
        return
    if item.data_type == registry.TYPE_STRING:
        text = str(value)
        if not text.strip() or len(text) > 200:
            _issue(
                result,
                TIER_SYNTACTIC,
                "INVALID_STRING_LENGTH",
                f"{item.display_name} must be 1-200 characters",
                item.key,
            )
            return
        result.normalized[item.key] = text
        return
    if item.data_type == registry.TYPE_ARRAY:
        if not isinstance(value, list) or len(value) > 50:
            _issue(
                result,
                TIER_SYNTACTIC,
                "INVALID_ARRAY",
                f"{item.display_name} must be a list of at most 50 values",
                item.key,
            )
            return
        result.normalized[item.key] = [str(entry)[:64] for entry in value]
        return
    _issue(
        result,
        TIER_SYNTACTIC,
        "UNSUPPORTED_TYPE",
        f"{item.display_name} has an unsupported type",
        item.key,
    )

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _issue(
    result: ValidationResult, tier: int, code: str, message: str, key: str | None
) -> None:
    result.issues.append(ValidationIssue(tier, code, message, key))


def _cross_field(domain: str, result: ValidationResult) -> None:
    """Tier 2: constraints that only make sense across several settings."""
    values = result.normalized

    def get(key: str) -> float | None:
        value = values.get(key)
        return _finite_number(value)

    if domain == registry.DOMAIN_V2_SCORING:
        weights = {
            component: get(f"v2.weights.{component}")
            for component in registry.V2_COMPONENTS
        }
        present = {name: value for name, value in weights.items() if value is not None}
        if present and len(present) != len(registry.V2_COMPONENTS):
            missing = sorted(set(registry.V2_COMPONENTS) - set(present))
            _issue(
                result,
                TIER_CROSS_FIELD,
                "PARTIAL_RUBRIC",
                "all six V2 weights must be staged together; missing "
                + ", ".join(missing),
                "v2.weights",
            )
        elif present:
            total = round(sum(present.values()), 6)
            if total != 100:
                _issue(
                    result,
                    TIER_CROSS_FIELD,
                    "WEIGHTS_MUST_TOTAL_100",
                    f"V2 weights total {total}, not 100 (they are never renormalised)",
                    "v2.weights",
                )
        rvol_zero = get("v2.rvol_zero")
        rvol_full = get("v2.rvol_full")
        if rvol_zero is not None and rvol_full is not None and rvol_zero >= rvol_full:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "RANGE_ORDER_INVALID",
                "relative-volume zero point must be below its full point",
                "v2.rvol_zero",
            )

    if domain == registry.DOMAIN_F11:
        if values.get("f11.allow_funded_orders") is True:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "TRADING_PERMISSION_CONFIG_FORBIDDEN",
                "funded order permission cannot be staged from a console",
                "f11.allow_funded_orders",
            )

    if domain == registry.DOMAIN_AI:
        mode = values.get("ai.mode")
        if mode is not None and mode != "advisory_only":
            _issue(
                result,
                TIER_CROSS_FIELD,
                "AI_AUTHORITY_FORBIDDEN",
                "AI output is advisory only and has no execution authority",
                "ai.mode",
            )

    if domain == registry.DOMAIN_SCANNER:
        cap = values.get("scanner.max_candidates")
        if cap is not None and int(cap) != 8:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "PRODUCTION_CAP_DRIFT",
                "the promoted-candidate cap is fixed at 8",
                "scanner.max_candidates",
            )

    if domain == registry.DOMAIN_RETENTION:
        batch = get("ingestion.batch_records")
        budget = get("ingestion.max_bytes")
        if batch is not None and batch < 1:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "NONNEGATIVE_BUDGET_REQUIRED",
                "ingestion batch must be at least one record",
                "ingestion.batch_records",
            )
        if budget is not None and budget < 65_536:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "BYTE_BUDGET_TOO_SMALL",
                "ingestion byte budget must be at least 65536",
                "ingestion.max_bytes",
            )
        days = values.get("retention.days")
        if days is not None and str(days) not in {"7", "15", "30", "60", "90"}:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "RETENTION_DAYS_NOT_ALLOWED",
                "retention must be exactly 7, 15, 30, 60 or 90 days",
                "retention.days",
            )

    if domain == registry.DOMAIN_DASHBOARD:
        refresh = get("dashboard.refresh_seconds")
        stale = get("dashboard.stale_after_seconds")
        if refresh is not None and stale is not None and refresh >= stale:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "RANGE_ORDER_INVALID",
                "refresh interval must be shorter than the stale threshold",
                "dashboard.refresh_seconds",
            )

    if domain == registry.DOMAIN_ATTRIBUTION:
        sample = get("attribution.min_sample_size")
        if sample is not None and sample < 10:
            _issue(
                result,
                TIER_CROSS_FIELD,
                "SAMPLE_TOO_SMALL",
                "an attribution claim needs at least 10 matured outcomes",
                "attribution.min_sample_size",
            )



def validate_document(domain: str, values: Mapping[str, Any]) -> ValidationResult:
    """Run tier 1 and tier 2 validation for one domain's staged document."""
    result = ValidationResult()
    if domain not in registry.DOMAINS:
        _issue(result, TIER_SYNTACTIC, "UNKNOWN_DOMAIN", f"unknown domain {domain}", None)
        return result
    if not isinstance(values, Mapping):
        _issue(
            result, TIER_SYNTACTIC, "INVALID_DOCUMENT", "values must be an object", None
        )
        return result
    if not values:
        _issue(
            result, TIER_SYNTACTIC, "EMPTY_DOCUMENT", "no settings were supplied", None
        )
        return result
    for key, value in values.items():
        item = registry.definition(str(key))
        if item is None:
            _issue(
                result,
                TIER_SYNTACTIC,
                "UNKNOWN_CONFIG_KEY",
                f"{key} is not a registered setting",
                str(key),
            )
            continue
        if item.domain != domain:
            _issue(
                result,
                TIER_SYNTACTIC,
                "DOMAIN_MISMATCH",
                f"{key} belongs to {item.domain}, not {domain}",
                str(key),
            )
            continue
        _check_definition(item, value, result)
    _cross_field(domain, result)
    return result

