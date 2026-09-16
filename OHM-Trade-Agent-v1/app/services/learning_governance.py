from __future__ import annotations

from datetime import datetime, timezone
import math
import os
from dataclasses import dataclass
from pathlib import Path

from app.services.registry_io import load_json, registry_lock, save_json_atomic


@dataclass(frozen=True)
class LearningPromotionDecision:
    state: str
    eligible_for_review: bool
    auto_apply_allowed: bool
    reason: str


def evaluate_learning_promotion(
    *,
    samples: int,
    expectancy: float | None,
    profit_factor: float | None,
    max_drawdown_pct: float | None,
    minimum_samples: int = 30,
    minimum_profit_factor: float = 1.20,
    maximum_drawdown_pct: float = 8.0,
) -> LearningPromotionDecision:
    """Govern evidence promotion without allowing self-modifying live behavior."""
    if samples < 0 or minimum_samples <= 0:
        raise ValueError("sample counts must be valid")
    thresholds = (minimum_profit_factor, maximum_drawdown_pct)
    if not all(math.isfinite(float(value)) for value in thresholds):
        raise ValueError("promotion thresholds must be finite")
    if samples < minimum_samples:
        return LearningPromotionDecision("INSUFFICIENT_DATA", False, False, "minimum sample count not reached")

    metrics = (expectancy, profit_factor, max_drawdown_pct)
    if any(value is None for value in metrics) or not all(math.isfinite(float(value)) for value in metrics if value is not None):
        return LearningPromotionDecision("REJECT", False, False, "performance metrics are missing or non-finite")
    assert expectancy is not None and profit_factor is not None and max_drawdown_pct is not None
    if expectancy <= 0:
        return LearningPromotionDecision("REJECT", False, False, "expectancy is not positive")
    if profit_factor < minimum_profit_factor:
        return LearningPromotionDecision("REJECT", False, False, "profit factor below promotion threshold")
    if max_drawdown_pct < 0 or max_drawdown_pct > maximum_drawdown_pct:
        return LearningPromotionDecision("REJECT", False, False, "drawdown is invalid or exceeds promotion threshold")
    return LearningPromotionDecision(
        "READY_FOR_HUMAN_REVIEW",
        True,
        False,
        "evidence meets review threshold; explicit human approval remains required",
    )


# ---------------------------------------------------------------------------
# PR-A: approved calibration promotion gate
#
# Evidence alone never authorizes runtime influence. A learned calibration
# multiplier may only reach runtime sizing when an explicit, durable, human
# approved promotion is active for the exact learned profile content. Anything
# that cannot be positively proven to be approved is neutral.
#
# This is a *consumption* gate, not a promotion workflow. Producing approved
# promotions (shadow testing, acceptance, rollback orchestration) remains the
# governed lifecycle's responsibility.
# ---------------------------------------------------------------------------

CALIBRATION_PROMOTION_SCHEMA_VERSION = 1

PROMOTION_REASON_NO_APPROVED_PROMOTION = "NO_APPROVED_PROMOTION"
PROMOTION_REASON_APPROVAL_METADATA_INCOMPLETE = "APPROVAL_METADATA_INCOMPLETE"
PROMOTION_REASON_APPROVAL_IN_FUTURE = "APPROVAL_IN_FUTURE"
PROMOTION_REASON_ROLLED_BACK = "PROMOTION_ROLLED_BACK"
PROMOTION_REASON_SUPERSEDED = "PROMOTION_SUPERSEDED"
PROMOTION_REASON_NOT_YET_EFFECTIVE = "NOT_YET_EFFECTIVE"
PROMOTION_REASON_EXPIRED = "PROMOTION_EXPIRED"
PROMOTION_REASON_PROFILE_UNAVAILABLE = "PROFILE_UNAVAILABLE"
PROMOTION_REASON_PROFILE_MISMATCH = "PROFILE_VERSION_MISMATCH"
PROMOTION_REASON_APPROVED_AND_EFFECTIVE = "APPROVED_AND_EFFECTIVE"

#: Neutral multiplier. Learning influence is exactly this unless approved.
NEUTRAL_CALIBRATION_MULTIPLIER = 1.0


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_utc(value: object, *, field_name: str) -> datetime:
    if isinstance(value, datetime):
        return _as_utc(value, field_name=field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    return _as_utc(parsed, field_name=field_name)


@dataclass(frozen=True)
class ApprovedCalibrationPromotion:
    """Durable evidence that a human approved one exact learned profile.

    ``profile_id`` is content-derived, so approving one learned profile can
    never silently authorize a later, different one.
    """

    promotion_id: str
    profile_id: str
    approving_principal: str
    approved_at_utc: datetime
    effective_at_utc: datetime
    learning_id: str | None = None
    expires_at_utc: datetime | None = None
    superseded_by: str | None = None
    rolled_back_at_utc: datetime | None = None
    schema_version: int = CALIBRATION_PROMOTION_SCHEMA_VERSION
    measurement_only: bool = True
    automatic_activation: bool = False
    trade_authority_changed: bool = False

    def __post_init__(self) -> None:
        if int(self.schema_version) != CALIBRATION_PROMOTION_SCHEMA_VERSION:
            raise ValueError("unsupported calibration promotion schema version")
        for name in ("promotion_id", "profile_id"):
            if not str(getattr(self, name) or "").strip():
                raise ValueError(f"{name} is required")
        if not str(self.approving_principal or "").strip():
            raise ValueError("approving_principal is required for an approved promotion")
        if self.learning_id is not None and not str(self.learning_id).startswith("LEARN:"):
            raise ValueError("learning_id must use LEARN: identity")
        object.__setattr__(
            self, "approved_at_utc", _as_utc(self.approved_at_utc, field_name="approved_at_utc")
        )
        object.__setattr__(
            self, "effective_at_utc", _as_utc(self.effective_at_utc, field_name="effective_at_utc")
        )
        if self.expires_at_utc is not None:
            object.__setattr__(
                self, "expires_at_utc", _as_utc(self.expires_at_utc, field_name="expires_at_utc")
            )
            if self.expires_at_utc <= self.effective_at_utc:
                raise ValueError("expires_at_utc must be after effective_at_utc")
        if self.rolled_back_at_utc is not None:
            object.__setattr__(
                self,
                "rolled_back_at_utc",
                _as_utc(self.rolled_back_at_utc, field_name="rolled_back_at_utc"),
            )
        if self.automatic_activation:
            raise ValueError("automatic promotion activation is prohibited")
        if self.trade_authority_changed:
            raise ValueError("a calibration promotion cannot change trading authority")

    def as_dict(self) -> dict:
        return {
            "schema_version": int(self.schema_version),
            "promotion_id": self.promotion_id,
            "profile_id": self.profile_id,
            "learning_id": self.learning_id,
            "approving_principal": self.approving_principal,
            "approved_at_utc": self.approved_at_utc.isoformat(),
            "effective_at_utc": self.effective_at_utc.isoformat(),
            "expires_at_utc": (
                self.expires_at_utc.isoformat() if self.expires_at_utc is not None else None
            ),
            "superseded_by": self.superseded_by,
            "rolled_back_at_utc": (
                self.rolled_back_at_utc.isoformat()
                if self.rolled_back_at_utc is not None
                else None
            ),
            "measurement_only": True,
            "automatic_activation": False,
            "trade_authority_changed": False,
        }


@dataclass(frozen=True)
class CalibrationPromotionResolution:
    """Outcome of resolving an approved promotion against the live profile."""

    active: bool
    profile_id: str | None
    reason: str
    promotion_id: str | None = None


def _promotion_from_dict(raw: dict) -> ApprovedCalibrationPromotion:
    return ApprovedCalibrationPromotion(
        promotion_id=str(raw["promotion_id"]),
        profile_id=str(raw["profile_id"]),
        approving_principal=str(raw["approving_principal"]),
        approved_at_utc=_parse_utc(raw["approved_at_utc"], field_name="approved_at_utc"),
        effective_at_utc=_parse_utc(raw["effective_at_utc"], field_name="effective_at_utc"),
        learning_id=(str(raw["learning_id"]) if raw.get("learning_id") else None),
        expires_at_utc=(
            _parse_utc(raw["expires_at_utc"], field_name="expires_at_utc")
            if raw.get("expires_at_utc")
            else None
        ),
        superseded_by=(str(raw["superseded_by"]) if raw.get("superseded_by") else None),
        rolled_back_at_utc=(
            _parse_utc(raw["rolled_back_at_utc"], field_name="rolled_back_at_utc")
            if raw.get("rolled_back_at_utc")
            else None
        ),
        schema_version=int(raw.get("schema_version", CALIBRATION_PROMOTION_SCHEMA_VERSION)),
    )


def calibration_promotion_dir() -> Path:
    override = os.environ.get("OPIP_LEARNING_GOVERNANCE_DIR", "").strip()
    return Path(override) if override else Path("/app/data/learning")


def approved_calibration_promotion_path() -> Path:
    return calibration_promotion_dir() / "approved_calibration_promotion.json"


def load_approved_calibration_promotion(
    path: Path | None = None,
) -> ApprovedCalibrationPromotion | None:
    """Load the approved promotion. Never raises.

    Fail-neutral by design: a promotion that cannot be read and validated
    cannot authorize runtime influence, so every failure path returns ``None``
    (which resolves to the neutral multiplier). The file is left untouched.
    """
    target = Path(path) if path is not None else approved_calibration_promotion_path()
    try:
        if not target.exists():
            return None
        payload = load_json(target)
        if not isinstance(payload, dict):
            return None
        record = payload.get("approved_calibration_promotion")
        if not isinstance(record, dict):
            return None
        return _promotion_from_dict(record)
    except Exception:
        return None


def save_approved_calibration_promotion(
    promotion: ApprovedCalibrationPromotion,
    *,
    path: Path | None = None,
) -> Path:
    """Persist an approved promotion (operator or test controlled)."""
    target = Path(path) if path is not None else approved_calibration_promotion_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / f".{target.name}.lock"
    with registry_lock(lock):
        save_json_atomic(
            target,
            {
                "schema_version": CALIBRATION_PROMOTION_SCHEMA_VERSION,
                "approved_calibration_promotion": promotion.as_dict(),
            },
        )
    return target


def rollback_calibration_promotion(
    promotion: ApprovedCalibrationPromotion,
    *,
    rolled_back_at_utc: datetime,
) -> ApprovedCalibrationPromotion:
    """Return a rolled-back copy. History is added to, never rewritten."""
    from dataclasses import replace

    return replace(
        promotion,
        rolled_back_at_utc=_as_utc(rolled_back_at_utc, field_name="rolled_back_at_utc"),
    )


def supersede_calibration_promotion(
    promotion: ApprovedCalibrationPromotion,
    *,
    superseded_by: str,
) -> ApprovedCalibrationPromotion:
    """Return a superseded copy so an older approval cannot remain active."""
    from dataclasses import replace

    if not str(superseded_by or "").strip():
        raise ValueError("superseded_by is required")
    return replace(promotion, superseded_by=str(superseded_by))


def resolve_calibration_promotion(
    *,
    promotion: ApprovedCalibrationPromotion | None,
    active_profile_id: str | None,
    now: datetime,
) -> CalibrationPromotionResolution:
    """Decide whether a promotion authorizes the live learned profile.

    Fail-closed toward neutral: every condition that is not positively
    satisfied, current, and matched to the exact profile content returns
    ``active=False``.
    """
    moment = _as_utc(now, field_name="now")
    if promotion is None:
        return CalibrationPromotionResolution(False, None, PROMOTION_REASON_NO_APPROVED_PROMOTION)
    if promotion.rolled_back_at_utc is not None:
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_ROLLED_BACK, promotion.promotion_id
        )
    if promotion.superseded_by:
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_SUPERSEDED, promotion.promotion_id
        )
    if not str(promotion.approving_principal or "").strip():
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_APPROVAL_METADATA_INCOMPLETE, promotion.promotion_id
        )
    if moment < promotion.approved_at_utc:
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_APPROVAL_IN_FUTURE, promotion.promotion_id
        )
    if moment < promotion.effective_at_utc:
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_NOT_YET_EFFECTIVE, promotion.promotion_id
        )
    if promotion.expires_at_utc is not None and moment >= promotion.expires_at_utc:
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_EXPIRED, promotion.promotion_id
        )
    if not str(active_profile_id or "").strip():
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_PROFILE_UNAVAILABLE, promotion.promotion_id
        )
    if str(active_profile_id) != str(promotion.profile_id):
        return CalibrationPromotionResolution(
            False, None, PROMOTION_REASON_PROFILE_MISMATCH, promotion.promotion_id
        )
    return CalibrationPromotionResolution(
        True,
        str(promotion.profile_id),
        PROMOTION_REASON_APPROVED_AND_EFFECTIVE,
        promotion.promotion_id,
    )

