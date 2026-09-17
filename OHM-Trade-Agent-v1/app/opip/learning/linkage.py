"""Exact evidence linkage and cohort normalization for O'Pip Sequence 5.

This module is intentionally pure/evidence-only. It joins records only through
explicit immutable identifiers already present in O'Pip evidence. It never
falls back to symbol/time proximity or other fuzzy matching.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import gzip
import math
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


PROVISIONAL_PHASE3C_SOURCE = "PROVISIONAL_EVENT_SAMPLED_FULL_MARKET_OBSERVATIONS"


class LearningCohort(str, Enum):
    QUALIFIED_PAPER = "QUALIFIED_PAPER"
    COUNTERFACTUAL_REJECTED = "COUNTERFACTUAL_REJECTED"
    OBSERVATION_ONLY = "OBSERVATION_ONLY"
    INELIGIBLE_UNLINKED = "INELIGIBLE_UNLINKED"


class OutcomeSourceQuality(str, Enum):
    FINAL_PAPER = "FINAL_PAPER"
    PROVISIONAL_MARKET = "PROVISIONAL_MARKET"
    MISSING = "MISSING"
    UNUSABLE = "UNUSABLE"


class LinkageStatus(str, Enum):
    COMPLETE_FINAL = "COMPLETE_FINAL"
    COMPLETE_PROVISIONAL = "COMPLETE_PROVISIONAL"
    FEATURE_LINKED_NO_OUTCOME = "FEATURE_LINKED_NO_OUTCOME"
    MISSING_FEATURE_SNAPSHOT = "MISSING_FEATURE_SNAPSHOT"
    AMBIGUOUS_PAPER_LINK = "AMBIGUOUS_PAPER_LINK"


@dataclass(frozen=True)
class NormalizedOutcomeEvidence:
    source_quality: OutcomeSourceQuality
    source_name: str
    canonical_snapshot_id: str | None
    episode_id: str | None
    direction: str | None
    outcome_record_id: str | None
    outcome_revision: int | None
    paper_trade_id: str | None
    horizon_returns: Mapping[str, float | None]
    mfe: float | None
    mae: float | None
    time_to_mfe_seconds: float | None
    time_to_mae_seconds: float | None
    censored: bool
    data_gap: bool
    execution_path_ambiguous: bool
    fee_model_version: str | None
    slippage_model_version: str | None
    terminal_outcome: str | None
    net_pnl: float | None
    net_pnl_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        """Return as dict."""
        row = asdict(self)
        row["source_quality"] = self.source_quality.value
        row["horizon_returns"] = dict(self.horizon_returns)
        return row


@dataclass(frozen=True)
class LearningLinkageRecord:
    canonical_snapshot_id: str
    episode_id: str | None
    ml_snapshot_id: str | None
    paper_trade_id: str | None
    phase3c_outcome_record_id: str | None
    cohort: LearningCohort
    linkage_status: LinkageStatus
    normalized_outcome: NormalizedOutcomeEvidence
    exclusion_reasons: tuple[str, ...]
    primary_supervised_eligible: bool
    measurement_only: bool = True
    affects_live_decisions: bool = False
    trade_authority_changed: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return as dict."""
        row = asdict(self)
        row["cohort"] = self.cohort.value
        row["linkage_status"] = self.linkage_status.value
        row["normalized_outcome"] = self.normalized_outcome.as_dict()
        row["exclusion_reasons"] = list(self.exclusion_reasons)
        return row


def _optional_float(value: Any) -> float | None:
    """Parse one finite numeric evidence value or return None."""
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) else None


def _strict_bool(value: Any) -> bool | None:
    """Accept only actual JSON/Python booleans; never coerce strings or ints."""
    return value if isinstance(value, bool) else None


def _direction(value: Any) -> str | None:
    """Return direction."""
    text = str(value or "").strip().upper()
    return text if text in {"LONG", "SHORT"} else None


def _latest_revision_by_key(
    rows: Iterable[Mapping[str, Any]],
    *,
    key_field: str,
    revision_field: str,
    identity_field: str | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Select one latest immutable revision per explicit key.

    Two distinct rows claiming the same key and same revision are treated as a
    conflict unless their immutable identity is identical. This fails closed
    rather than silently preferring input order.
    """
    latest: dict[str, Mapping[str, Any]] = {}
    revisions: dict[str, int] = {}
    identities: dict[tuple[str, int], str] = {}

    for row in rows:
        key = str(row.get(key_field) or "").strip()
        if not key:
            continue
        try:
            revision = int(row.get(revision_field))
        except (TypeError, ValueError):
            raise ValueError(
                f"{revision_field} is required for immutable revision selection"
            )
        if revision <= 0:
            raise ValueError(
                f"{revision_field} must be positive for immutable revision selection"
            )
        if identity_field is not None:
            identity = str(row.get(identity_field) or "").strip()
            if not identity:
                raise ValueError(
                    f"{identity_field} is required for immutable revision selection"
                )
        else:
            identity = json.dumps(
                dict(row),
                sort_keys=True,
                allow_nan=False,
                default=str,
            )
        duplicate_key = (key, revision)
        prior_identity = identities.get(duplicate_key)
        if prior_identity is not None and prior_identity != identity:
            raise ValueError(
                f"conflicting immutable revisions for {key_field}={key} "
                f"revision={revision}"
            )
        identities[duplicate_key] = identity

        prior_revision = revisions.get(key)
        if prior_revision is None or revision > prior_revision:
            latest[key] = row
            revisions[key] = revision
    return latest


def select_latest_phase3c_outcomes(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Return select latest phase3c outcomes."""
    return _latest_revision_by_key(
        rows,
        key_field="snapshot_id",
        revision_field="outcome_revision",
        identity_field="outcome_record_id",
    )


def select_latest_paper_trades(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Return select latest paper trades."""
    return _latest_revision_by_key(
        rows,
        key_field="paper_trade_id",
        revision_field="revision",
        identity_field=None,
    )


def _index_unique(
    rows: Iterable[Mapping[str, Any]],
    *,
    key_field: str,
    identity_field: str,
) -> dict[str, Mapping[str, Any]]:
    """Return index unique."""
    indexed: dict[str, Mapping[str, Any]] = {}
    identities: dict[str, str] = {}
    for row in rows:
        key = str(row.get(key_field) or "").strip()
        if not key:
            continue
        identity = str(row.get(identity_field) or "").strip()
        if not identity:
            raise ValueError(
                f"{identity_field} is required for exact linkage selection"
            )
        if identity_field == "ml_snapshot_id":
            snapshot = row.get("feature_snapshot")
            if not isinstance(snapshot, Mapping):
                raise ValueError("feature_snapshot is required for exact ML linkage")
            nested_identity = str(snapshot.get("snapshot_id") or "").strip()
            if not nested_identity or nested_identity != identity:
                raise ValueError(
                    "ml_snapshot_id must match feature_snapshot.snapshot_id"
                )
        prior = identities.get(key)
        if prior is not None and prior != identity:
            raise ValueError(
                f"conflicting exact linkage identities for {key_field}={key}"
            )
        indexed.setdefault(key, row)
        identities.setdefault(key, identity)
    return indexed


def index_ml_feature_snapshots(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """Return index ml feature snapshots."""
    return _index_unique(
        rows,
        key_field="canonical_snapshot_id",
        identity_field="ml_snapshot_id",
    )


def normalize_phase3c_outcome(
    row: Mapping[str, Any] | None,
) -> NormalizedOutcomeEvidence:
    """Return normalize phase3c outcome."""
    if row is None:
        return NormalizedOutcomeEvidence(
            source_quality=OutcomeSourceQuality.MISSING,
            source_name="NONE",
            canonical_snapshot_id=None,
            episode_id=None,
            direction=None,
            outcome_record_id=None,
            outcome_revision=None,
            paper_trade_id=None,
            horizon_returns={},
            mfe=None,
            mae=None,
            time_to_mfe_seconds=None,
            time_to_mae_seconds=None,
            censored=False,
            data_gap=False,
            execution_path_ambiguous=False,
            fee_model_version=None,
            slippage_model_version=None,
            terminal_outcome=None,
            net_pnl=None,
            net_pnl_pct=None,
        )

    source = str(row.get("outcome_source") or row.get("source") or "").strip()
    quality = (
        OutcomeSourceQuality.PROVISIONAL_MARKET
        if source == PROVISIONAL_PHASE3C_SOURCE
        else OutcomeSourceQuality.UNUSABLE
    )
    raw_returns = row.get("horizon_returns_pct")
    returns: dict[str, float | None] = {}
    if isinstance(raw_returns, Mapping):
        returns = {
            str(key): _optional_float(value)
            for key, value in raw_returns.items()
        }

    return NormalizedOutcomeEvidence(
        source_quality=quality,
        source_name=source or "UNKNOWN_PHASE3C",
        canonical_snapshot_id=str(row.get("snapshot_id") or "") or None,
        episode_id=str(
            row.get("canonical_episode_id") or row.get("episode_id") or ""
        )
        or None,
        direction=_direction(row.get("direction")),
        outcome_record_id=str(row.get("outcome_record_id") or "") or None,
        outcome_revision=int(row.get("outcome_revision", 0) or 0),
        paper_trade_id=None,
        horizon_returns=returns,
        mfe=_optional_float(row.get("mfe_pct")),
        mae=_optional_float(row.get("mae_pct")),
        time_to_mfe_seconds=_optional_float(row.get("time_to_mfe_seconds")),
        time_to_mae_seconds=_optional_float(row.get("time_to_mae_seconds")),
        censored=bool(row.get("censored", False)),
        data_gap=bool(row.get("data_gap", False)),
        execution_path_ambiguous=bool(
            row.get("execution_path_ambiguous", False)
        ),
        fee_model_version=(
            str(row.get("fee_model_version"))
            if row.get("fee_model_version")
            else None
        ),
        slippage_model_version=(
            str(row.get("slippage_model_version"))
            if row.get("slippage_model_version")
            else None
        ),
        terminal_outcome=str(row.get("outcome") or "") or None,
        net_pnl=None,
        net_pnl_pct=None,
    )


def normalize_paper_outcome(
    row: Mapping[str, Any] | None,
) -> NormalizedOutcomeEvidence:
    """Normalize a paper outcome row.

    Dispatch is by payload form, and canonical form wins:

    * A canonical terminal outcome payload (the authoritative plane) is
      normalized by :func:`normalize_canonical_paper_outcome`.
    * A legacy ``paper_trading/state.json`` lifecycle row keeps the original
      normalization, so existing callers and historical evidence continue to
      work unchanged.

    This keeps the cutover additive: nothing that previously produced a result
    stops producing one, and canonical evidence is preferred wherever present.
    """
    if isinstance(row, Mapping) and (
        row.get("outcome_id") is not None or row.get("terminal_status") is not None
    ):
        return normalize_canonical_paper_outcome(row)

    if row is None:
        return normalize_phase3c_outcome(None)

    status = str(row.get("status") or "").upper()
    paper_only = _strict_bool(row.get("paper_only"))
    authority = _strict_bool(row.get("exchange_write_authority"))
    direction = _direction(row.get("direction"))
    net_pnl = _optional_float(row.get("net_pnl"))
    net_pnl_pct = _optional_float(row.get("net_pnl_pct"))
    final = (
        paper_only is True
        and authority is False
        and status == "CLOSED"
        and direction in {"LONG", "SHORT"}
        and bool(str(row.get("closed_at") or "").strip())
        and _optional_float(row.get("exit_price")) is not None
        and net_pnl is not None
        and net_pnl_pct is not None
        and bool(str(row.get("outcome") or "").strip())
    )
    quality = (
        OutcomeSourceQuality.FINAL_PAPER
        if final
        else OutcomeSourceQuality.UNUSABLE
    )
    returns = (
        {"paper_closed": net_pnl_pct}
        if net_pnl_pct is not None
        else {}
    )

    return NormalizedOutcomeEvidence(
        source_quality=quality,
        source_name="PAPER_TRADE_V1",
        canonical_snapshot_id=None,
        episode_id=str(row.get("episode_id") or "") or None,
        direction=direction,
        outcome_record_id=None,
        outcome_revision=int(row.get("revision", 0) or 0),
        paper_trade_id=str(row.get("paper_trade_id") or "") or None,
        horizon_returns=returns,
        mfe=_optional_float(row.get("mfe_pct")),
        mae=_optional_float(row.get("mae_pct")),
        time_to_mfe_seconds=_optional_float(row.get("time_to_mfe_seconds")),
        time_to_mae_seconds=_optional_float(row.get("time_to_mae_seconds")),
        censored=bool(row.get("censored", False)),
        data_gap=bool(row.get("data_gap", False)),
        execution_path_ambiguous=bool(
            row.get("execution_path_ambiguous", False)
        ),
        fee_model_version=(
            str(row.get("fee_model_version"))
            if row.get("fee_model_version")
            else None
        ),
        slippage_model_version=(
            str(row.get("slippage_model_version"))
            if row.get("slippage_model_version")
            else None
        ),
        terminal_outcome=str(row.get("outcome") or "") or None,
        net_pnl=net_pnl,
        net_pnl_pct=net_pnl_pct,
    )


def normalize_canonical_paper_outcome(
    payload: Mapping[str, Any] | None,
    *,
    evidence_complete: bool = True,
) -> NormalizedOutcomeEvidence:
    """Normalize an authoritative canonical terminal paper outcome.

    This is the learner cutover. The canonical plane is the outcome authority,
    so this reads the committed contract payload rather than the mutable
    ``paper_trading/state.json`` lifecycle row.

    ``evidence_complete`` gates classification. A closed paper lifecycle does
    not imply complete evidence: if an outcome could not be delivered, the
    population is genuinely incomplete and a row must never be presented as
    final supervised truth. Incomplete evidence is returned UNUSABLE, never
    FINAL_PAPER.

    Structural requirements for FINAL_PAPER, all of which must be positively
    present rather than defaulted:

    * ``terminal_status`` is ``CLOSED`` - a cancelled setup realised nothing and
      an unresolved outcome is unknown, so neither is a supervised label.
    * native economics are present and finite.
    * a typed quote currency is present, so P/L is never currency-blind.
    * the execution engine is explicit, so simulator populations are never
      silently combined.
    """
    if payload is None:
        return normalize_phase3c_outcome(None)

    status = str(payload.get("terminal_status") or "").upper()
    direction = _direction(payload.get("direction"))
    net_pnl = _optional_float(payload.get("net_pnl"))
    net_pnl_pct = _optional_float(payload.get("net_pnl_pct"))
    quote_currency = str(payload.get("quote_currency") or "").strip().upper()
    engine = str(payload.get("engine") or "").strip()
    final = (
        status == "CLOSED"
        and direction in {"LONG", "SHORT"}
        and net_pnl is not None
        and net_pnl_pct is not None
        and bool(quote_currency)
        and bool(engine)
        and bool(str(payload.get("exit_timestamp") or "").strip())
        and _optional_float(payload.get("exit_price")) is not None
    )
    if final and not evidence_complete:
        # The outcome is authoritative but the population is not complete, so it
        # is not yet final supervised truth.
        final = False

    quality = (
        OutcomeSourceQuality.FINAL_PAPER
        if final
        else OutcomeSourceQuality.UNUSABLE
    )
    returns = {"paper_closed": net_pnl_pct} if net_pnl_pct is not None else {}

    return NormalizedOutcomeEvidence(
        source_quality=quality,
        source_name="PAPER_OUTCOME_CANONICAL_V1",
        canonical_snapshot_id=str(payload.get("outcome_id") or "") or None,
        episode_id=str(payload.get("episode_id") or "") or None,
        direction=direction,
        outcome_record_id=str(payload.get("outcome_id") or "") or None,
        outcome_revision=int(payload.get("final_revision", 0) or 0),
        paper_trade_id=str(payload.get("paper_trade_id") or "") or None,
        horizon_returns=returns,
        mfe=_optional_float(payload.get("mfe_pct")),
        mae=_optional_float(payload.get("mae_pct")),
        time_to_mfe_seconds=_optional_float(payload.get("time_to_mfe_seconds")),
        time_to_mae_seconds=_optional_float(payload.get("time_to_mae_seconds")),
        censored=status == "UNRESOLVED",
        data_gap=bool(payload.get("lineage_missing")),
        execution_path_ambiguous=False,
        fee_model_version=(
            str(payload.get("economic_model_version"))
            if payload.get("economic_model_version")
            else None
        ),
        slippage_model_version=None,
        terminal_outcome=str(payload.get("exit_reason") or "") or None,
        net_pnl=net_pnl,
        net_pnl_pct=net_pnl_pct,
    )


def classify_learning_cohort(
    canonical_row: Mapping[str, Any],
    *,
    has_exact_feature_snapshot: bool,
) -> LearningCohort:
    """Return classify learning cohort."""
    if not has_exact_feature_snapshot:
        return LearningCohort.INELIGIBLE_UNLINKED

    status = str(
        canonical_row.get("decision_status")
        or canonical_row.get("decision")
        or ""
    ).strip().upper()
    suppressed = bool(canonical_row.get("suppressed", False))
    counterfactual = bool(canonical_row.get("counterfactual_eligible", False))

    if (
        counterfactual
        or suppressed
        or status in {
            "REJECTED",
            "BLOCKED",
            "NOT_QUALIFIED",
            "DISQUALIFIED",
            "SCORED_SUPPRESSED",
        }
    ):
        return LearningCohort.COUNTERFACTUAL_REJECTED
    if status in {"QUALIFIED", "SCORED_ELIGIBLE"}:
        return LearningCohort.QUALIFIED_PAPER
    return LearningCohort.OBSERVATION_ONLY


def _paper_by_episode(
    latest_trades: Mapping[str, Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Return paper by episode."""
    by_episode: dict[str, list[Mapping[str, Any]]] = {}
    for row in latest_trades.values():
        episode_id = str(row.get("episode_id") or "").strip()
        if not episode_id:
            continue
        by_episode.setdefault(episode_id, []).append(row)
    return by_episode


def resolve_effective_outcomes(
    rows: Iterable[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Reduce an append-only outcome set to its currently effective records.

    A governed correction is a *new* identity carrying ``supersedes_id``, so the
    superseded record remains in canonical storage as history. Linkage must
    therefore resolve the effective record rather than treating history as a
    conflict - otherwise every corrected outcome would become unusable.

    A fork (two records superseding the same one) deliberately leaves both in
    place so the caller's ambiguity handling fails closed rather than letting
    ordering pick a winner.
    """
    materialised = list(rows)
    superseded = {
        str(row.get("supersedes_id"))
        for row in materialised
        if str(row.get("supersedes_id") or "").strip()
    }
    return [
        row
        for row in materialised
        if str(row.get("outcome_id") or "").strip() not in superseded
    ]


def canonical_outcomes_by_episode(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Index effective authoritative canonical outcomes by episode.

    A duplicate ``outcome_id`` is impossible under the writer's uniqueness
    constraint, so seeing one means the store was tampered with or hand-edited.
    That raises rather than letting commit order pick a winner.
    """
    by_episode: dict[str, list[Mapping[str, Any]]] = {}
    seen: set[str] = set()
    for row in rows:
        outcome_id = str(row.get("outcome_id") or "").strip()
        if not outcome_id:
            continue
        if outcome_id in seen:
            raise ValueError(f"duplicate canonical outcome identity: {outcome_id}")
        seen.add(outcome_id)
        episode_id = str(row.get("episode_id") or "").strip()
        if not episode_id:
            continue
        by_episode.setdefault(episode_id, []).append(row)
    return by_episode


def select_canonical_paper_outcome(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, str | None]:
    """Choose the single authoritative canonical outcome for one episode.

    Returns ``(row, None)`` for exactly one outcome, or ``(None, reason)`` when
    the evidence cannot decide. No approved engine-selection policy exists, so
    two engines simulating the same opportunity are an ambiguity to surface,
    not a preference to assume.
    """
    if not rows:
        return None, None
    if len(rows) > 1:
        return None, "AMBIGUOUS_CANONICAL_PAPER_OUTCOME"
    return rows[0], None


def _outbox_delivery(row: Mapping[str, Any]) -> str | None:
    outbox = row.get("outcome_outbox")
    if not isinstance(outbox, Mapping):
        return None
    return str(outbox.get("delivery") or "") or None


def paper_outcome_population_incomplete(
    paper_trade_rows: Iterable[Mapping[str, Any]],
) -> tuple[bool, tuple[str, ...]]:
    """Report whether the locally observed canonical outcome population is complete.

    A closed lifecycle does not imply complete evidence. Three states matter:

    * ``COMMITTED`` - acknowledged delivery; the population may be complete.
    * ``PENDING`` - delivery is unresolved, so lifecycle economics cannot be
      promoted to final supervised truth.
    * ``PERMANENT_FAILURE`` - delivery cannot succeed; the population is
      degraded and must never be completed by falling back.

    A terminal lifecycle with **no** outbox at all predates PR-A outcome
    production, so it is legacy evidence rather than an incomplete population.
    That presence check is the discriminator, rather than guessing from
    timestamps.
    """
    reasons: list[str] = []
    for row in paper_trade_rows:
        status = str(row.get("status") or "").upper()
        if status not in {"CLOSED", "CANCELLED", "UNRESOLVED"}:
            continue
        outbox = row.get("outcome_outbox")
        if not isinstance(outbox, Mapping):
            # Legacy terminal row written before PR-A began producing outcomes.
            continue
        delivery = _outbox_delivery(row)
        if delivery == "COMMITTED":
            continue
        if delivery == "PENDING":
            reasons.append("PAPER_OUTCOME_DELIVERY_PENDING")
        elif delivery == "PERMANENT_FAILURE":
            reasons.append("PAPER_OUTCOME_DELIVERY_PERMANENT_FAILURE")
        else:
            # Terminal PR-A lifecycle with a missing or unreadable disposition.
            reasons.append("PAPER_OUTCOME_DELIVERY_UNKNOWN")
    return (bool(reasons), tuple(sorted(set(reasons))))


def _legacy_fallback_allowed(row: Mapping[str, Any]) -> bool:
    """Whether a lifecycle row may supply final paper evidence.

    Only genuinely pre-PR-A rows may. A PR-A-era row carries an outbox, and
    falling back from it would use mutable local state to mask a canonical
    authority-plane defect.
    """
    return not isinstance(row.get("outcome_outbox"), Mapping)


def _ml_direction(row: Mapping[str, Any] | None) -> str | None:
    """Return an explicit LONG/SHORT direction from one ML wrapper."""
    if row is None:
        return None
    snapshot = row.get("feature_snapshot")
    if not isinstance(snapshot, Mapping):
        return None
    return _direction(snapshot.get("direction"))


def build_learning_linkage_records(
    *,
    canonical_rows: Iterable[Mapping[str, Any]],
    ml_snapshot_rows: Iterable[Mapping[str, Any]],
    phase3c_outcome_rows: Iterable[Mapping[str, Any]] = (),
    paper_trade_rows: Iterable[Mapping[str, Any]] = (),
    paper_outcome_rows: Iterable[Mapping[str, Any]] = (),
    paper_outcome_population_incomplete: bool = False,
    paper_outcome_incomplete_reasons: Sequence[str] = (),
) -> tuple[LearningLinkageRecord, ...]:
    """Build one deterministic linkage row per canonical snapshot.

    Authority precedence for terminal paper outcomes:

    1. A valid authoritative canonical outcome wins outright.
    2. Canonical evidence that is present but defective fails closed - it is
       never replaced by the mutable lifecycle row, which would let local state
       mask an authority-plane defect.
    3. Only a genuinely pre-PR-A lifecycle row (one with no outcome delivery
       envelope) may supply legacy final evidence.

    ``canonical_rows`` keeps its existing meaning: canonical snapshot/decision
    evidence used for feature linkage. It is a different population from
    terminal paper outcomes and is deliberately not repurposed.

    Phase 3C event-sampled outcomes remain provisional evidence and can never
    make a row primary-supervised eligible.
    """
    canonical_index: dict[str, Mapping[str, Any]] = {}
    for row in canonical_rows:
        snapshot_id = str(row.get("snapshot_id") or "").strip()
        if not snapshot_id:
            continue
        if snapshot_id in canonical_index:
            raise ValueError(
                f"duplicate canonical snapshot identity: {snapshot_id}"
            )
        canonical_index[snapshot_id] = row

    ml_index = index_ml_feature_snapshots(ml_snapshot_rows)
    phase3c = select_latest_phase3c_outcomes(phase3c_outcome_rows)
    paper_latest = select_latest_paper_trades(paper_trade_rows)
    paper_by_episode = _paper_by_episode(paper_latest)
    canonical_outcomes = canonical_outcomes_by_episode(
        resolve_effective_outcomes(paper_outcome_rows)
    )
    canonical_ids_by_episode: dict[str, list[str]] = {}
    for canonical_snapshot_id, canonical_row in canonical_index.items():
        canonical_episode_id = str(canonical_row.get("episode_id") or "").strip()
        if canonical_episode_id:
            canonical_ids_by_episode.setdefault(canonical_episode_id, []).append(
                canonical_snapshot_id
            )

    records: list[LearningLinkageRecord] = []
    for snapshot_id in sorted(canonical_index):
        canonical = canonical_index[snapshot_id]
        episode_id = str(canonical.get("episode_id") or "").strip() or None
        ml_row = ml_index.get(snapshot_id)
        ml_snapshot_id = (
            str(ml_row.get("ml_snapshot_id") or "").strip()
            if ml_row is not None
            else ""
        ) or None
        cohort = classify_learning_cohort(
            canonical,
            has_exact_feature_snapshot=ml_row is not None,
        )
        reasons: list[str] = []

        if ml_row is None:
            reasons.append("ML_FEATURE_SNAPSHOT_MISSING")
            outcome = normalize_phase3c_outcome(phase3c.get(snapshot_id))
            status = LinkageStatus.MISSING_FEATURE_SNAPSHOT
            primary = False
            paper_trade_id = None
        else:
            matching_paper = (
                paper_by_episode.get(episode_id, [])
                if episode_id is not None
                else []
            )
            episode_snapshot_count = (
                len(canonical_ids_by_episode.get(episode_id, []))
                if episode_id is not None
                else 0
            )
            matching_canonical = (
                canonical_outcomes.get(episode_id, [])
                if episode_id is not None
                else []
            )
            selected_canonical, canonical_ambiguity = select_canonical_paper_outcome(
                matching_canonical
            )
            # Canonical authority. When committed canonical evidence exists for
            # this episode it decides the outcome outright, and a defective
            # canonical set fails closed rather than being replaced by mutable
            # lifecycle state - otherwise local state could mask an
            # authority-plane defect.
            if canonical_ambiguity is not None:
                reasons.append(canonical_ambiguity)

            if selected_canonical is not None or canonical_ambiguity is not None:
                if canonical_ambiguity is not None:
                    outcome = NormalizedOutcomeEvidence(
                        source_quality=OutcomeSourceQuality.UNUSABLE,
                        source_name="PAPER_OUTCOME_CANONICAL_V1",
                        canonical_snapshot_id=None,
                        episode_id=episode_id,
                        direction=None,
                        outcome_record_id=None,
                        outcome_revision=None,
                        paper_trade_id=None,
                        horizon_returns={},
                        mfe=None,
                        mae=None,
                        time_to_mfe_seconds=None,
                        time_to_mae_seconds=None,
                        censored=False,
                        data_gap=True,
                        execution_path_ambiguous=True,
                        fee_model_version=None,
                        slippage_model_version=None,
                        terminal_outcome=None,
                        net_pnl=None,
                        net_pnl_pct=None,
                    )
                    status = LinkageStatus.AMBIGUOUS_PAPER_LINK
                    primary = False
                    paper_trade_id = None
                else:
                    outcome = normalize_canonical_paper_outcome(
                        selected_canonical,
                        evidence_complete=not paper_outcome_population_incomplete,
                    )
                    paper_trade_id = outcome.paper_trade_id
                    if paper_outcome_population_incomplete:
                        reasons.extend(paper_outcome_incomplete_reasons)
                    if outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER:
                        status = LinkageStatus.COMPLETE_FINAL
                    else:
                        status = LinkageStatus.FEATURE_LINKED_NO_OUTCOME
                        reasons.append("CANONICAL_PAPER_OUTCOME_NOT_FINAL_OR_UNUSABLE")
                    ml_direction = _ml_direction(ml_row)
                    if ml_direction is None:
                        reasons.append("ML_DIRECTION_NOT_TRAINABLE")
                    elif (
                        outcome.direction is not None
                        and outcome.direction != ml_direction
                    ):
                        reasons.append("DIRECTION_LINK_MISMATCH")
                    primary = (
                        cohort is LearningCohort.QUALIFIED_PAPER
                        and outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
                        and ml_direction in {"LONG", "SHORT"}
                        and outcome.direction == ml_direction
                        and not outcome.censored
                        and not outcome.data_gap
                        and not outcome.execution_path_ambiguous
                    )
            elif matching_paper and episode_snapshot_count != 1:
                reasons.append("AMBIGUOUS_CANONICAL_SNAPSHOT_FOR_PAPER_EPISODE")
                outcome = NormalizedOutcomeEvidence(
                    source_quality=OutcomeSourceQuality.UNUSABLE,
                    source_name="PAPER_TRADE_V1",
                    canonical_snapshot_id=snapshot_id,
                    episode_id=episode_id,
                    direction=None,
                    outcome_record_id=None,
                    outcome_revision=None,
                    paper_trade_id=None,
                    horizon_returns={},
                    mfe=None,
                    mae=None,
                    time_to_mfe_seconds=None,
                    time_to_mae_seconds=None,
                    censored=False,
                    data_gap=False,
                    execution_path_ambiguous=False,
                    fee_model_version=None,
                    slippage_model_version=None,
                    terminal_outcome=None,
                    net_pnl=None,
                    net_pnl_pct=None,
                )
                status = LinkageStatus.AMBIGUOUS_PAPER_LINK
                primary = False
                paper_trade_id = None
            elif len(matching_paper) > 1:
                reasons.append("AMBIGUOUS_EXACT_PAPER_EPISODE_LINK")
                outcome = NormalizedOutcomeEvidence(
                    source_quality=OutcomeSourceQuality.UNUSABLE,
                    source_name="PAPER_TRADE_V1",
                    canonical_snapshot_id=snapshot_id,
                    episode_id=episode_id,
                    direction=None,
                    outcome_record_id=None,
                    outcome_revision=None,
                    paper_trade_id=None,
                    horizon_returns={},
                    mfe=None,
                    mae=None,
                    time_to_mfe_seconds=None,
                    time_to_mae_seconds=None,
                    censored=False,
                    data_gap=False,
                    execution_path_ambiguous=False,
                    fee_model_version=None,
                    slippage_model_version=None,
                    terminal_outcome=None,
                    net_pnl=None,
                    net_pnl_pct=None,
                )
                status = LinkageStatus.AMBIGUOUS_PAPER_LINK
                primary = False
                paper_trade_id = None
            elif len(matching_paper) == 1:
                paper_row = matching_paper[0]
                if not _legacy_fallback_allowed(paper_row):
                    # PR-A-era lifecycle whose canonical delivery is unresolved
                    # or failed. Its local lifecycle economics must not be
                    # promoted to final truth: falling back would use mutable
                    # state to mask a delivery defect.
                    outcome = NormalizedOutcomeEvidence(
                        source_quality=OutcomeSourceQuality.UNUSABLE,
                        source_name="PAPER_TRADE_V1",
                        canonical_snapshot_id=None,
                        episode_id=episode_id,
                        direction=None,
                        outcome_record_id=None,
                        outcome_revision=None,
                        paper_trade_id=None,
                        horizon_returns={},
                        mfe=None,
                        mae=None,
                        time_to_mfe_seconds=None,
                        time_to_mae_seconds=None,
                        censored=False,
                        data_gap=True,
                        execution_path_ambiguous=False,
                        fee_model_version=None,
                        slippage_model_version=None,
                        terminal_outcome=None,
                        net_pnl=None,
                        net_pnl_pct=None,
                    )
                    status = LinkageStatus.FEATURE_LINKED_NO_OUTCOME
                    delivery = _outbox_delivery(paper_row) or "UNKNOWN"
                    reasons.append(f"PAPER_OUTCOME_DELIVERY_{delivery}")
                    paper_trade_id = None
                    primary = False
                else:
                    outcome = normalize_paper_outcome(paper_row)
                    paper_trade_id = outcome.paper_trade_id
                    if outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER:
                        status = LinkageStatus.COMPLETE_FINAL
                    else:
                        status = LinkageStatus.FEATURE_LINKED_NO_OUTCOME
                        reasons.append("PAPER_OUTCOME_NOT_FINAL_OR_UNUSABLE")
                    ml_direction = _ml_direction(ml_row)
                    if ml_direction is None:
                        reasons.append("ML_DIRECTION_NOT_TRAINABLE")
                    elif (
                        outcome.direction is not None
                        and outcome.direction != ml_direction
                    ):
                        reasons.append("DIRECTION_LINK_MISMATCH")
                    primary = (
                        cohort is LearningCohort.QUALIFIED_PAPER
                        and outcome.source_quality is OutcomeSourceQuality.FINAL_PAPER
                        and ml_direction in {"LONG", "SHORT"}
                        and outcome.direction == ml_direction
                        and not outcome.censored
                        and not outcome.data_gap
                        and not outcome.execution_path_ambiguous
                    )
            else:
                outcome = normalize_phase3c_outcome(phase3c.get(snapshot_id))
                paper_trade_id = None
                if (
                    outcome.source_quality
                    is OutcomeSourceQuality.PROVISIONAL_MARKET
                ):
                    status = LinkageStatus.COMPLETE_PROVISIONAL
                    reasons.append("OUTCOME_PROVISIONAL_NOT_SUPERVISED_TRUTH")
                else:
                    status = LinkageStatus.FEATURE_LINKED_NO_OUTCOME
                    reasons.append("FINAL_OUTCOME_MISSING")
                primary = False

        if cohort is LearningCohort.COUNTERFACTUAL_REJECTED:
            primary = False
            reasons.append("COUNTERFACTUAL_RESEARCH_ONLY")
        elif cohort is LearningCohort.OBSERVATION_ONLY:
            primary = False
            reasons.append("OBSERVATION_ONLY_NOT_PRIMARY_SUPERVISED")
        elif cohort is LearningCohort.INELIGIBLE_UNLINKED:
            primary = False
            if "ML_FEATURE_SNAPSHOT_MISSING" not in reasons:
                reasons.append("EXACT_FEATURE_LINK_REQUIRED")

        records.append(
            LearningLinkageRecord(
                canonical_snapshot_id=snapshot_id,
                episode_id=episode_id,
                ml_snapshot_id=ml_snapshot_id,
                paper_trade_id=paper_trade_id,
                phase3c_outcome_record_id=(
                    str(phase3c.get(snapshot_id, {}).get("outcome_record_id") or "")
                    or None
                ),
                cohort=cohort,
                linkage_status=status,
                normalized_outcome=outcome,
                exclusion_reasons=tuple(sorted(set(reasons))),
                primary_supervised_eligible=primary,
            )
        )

    return tuple(records)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Return read jsonl."""
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError(f"JSONL row is not an object: {path}")
            rows.append(row)
    return rows


def read_ml_snapshot_chunks(snapshot_dir: Path) -> list[dict[str, Any]]:
    """Return read ml snapshot chunks."""
    rows: list[dict[str, Any]] = []
    if not snapshot_dir.exists():
        return rows
    for path in sorted(snapshot_dir.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                row = json.loads(raw)
                if not isinstance(row, dict):
                    raise ValueError(f"ML snapshot row is not an object: {path}")
                rows.append(row)
    return rows
