"""SQ-00 / EF-01 evidence-plane reconciliation.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Joins V2-01 discovery outcomes to Phase 3C / opportunity accountability.
Does not change ranking, alerts, paper admission, or trading authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping

from app.opip.discovery.admission import observation_join_id
from app.opip.discovery.maturation import latest_discovery_outcomes_by_observation
from app.scanner.candidates import MIN_TECHNICAL_SCORE
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_THRESHOLD_DRIFT,
    ACCOUNTABILITY_WINNER_DEFINITION,
    AccountabilityPolicy,
    _normalize_symbol,
    _observation_id_from_screening,
    _preferred_production_direction,
    _screening_symbol,
    _screening_venue_instrument_id,
)


DEFAULT_DATA_ROOT = Path("/app/data")
DISCOVERY_WINNER_DEFINITION = "DISCOVERY_OUTCOME_V1"


def _optional_text(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                rows.append(value)
    except OSError:
        return []
    return rows


def reconstructed_join_key(
    *,
    scan_id: Any,
    observed_at: Any,
    symbol: Any,
) -> str | None:
    scan = _optional_text(scan_id)
    observed = _optional_text(observed_at)
    normalized = _normalize_symbol(symbol)
    if not scan or not observed or not normalized:
        return None
    return f"{scan}|{observed}|{normalized}"


def join_key_from_discovery(row: Mapping[str, Any]) -> str | None:
    return reconstructed_join_key(
        scan_id=row.get("scan_id"),
        observed_at=row.get("observed_at"),
        symbol=row.get("venue_instrument_id") or _screening_venue_instrument_id(row),
    )


def join_key_from_accountability(row: Mapping[str, Any]) -> str | None:
    return reconstructed_join_key(
        scan_id=row.get("scan_id"),
        observed_at=row.get("observed_at"),
        symbol=row.get("symbol"),
    )


def latest_accountability_by_id(
    rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = str(row.get("accountability_id") or "")
        if not key:
            continue
        current = latest.get(key)
        try:
            revision = int(row.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if current is None or revision >= int(current.get("revision") or 0):
            latest[key] = dict(row)
    return latest


def latest_phase3c_by_snapshot(
    rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    revisions: dict[str, int] = {}
    for row in rows:
        snapshot_id = str(row.get("snapshot_id") or "")
        if not snapshot_id:
            continue
        try:
            revision = int(row.get("outcome_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if snapshot_id not in revisions or revision >= revisions[snapshot_id]:
            latest[snapshot_id] = dict(row)
            revisions[snapshot_id] = revision
    return latest


def _quantiles(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "max": None}
    ordered = sorted(values)
    p95_index = max(0, int(round(0.95 * (len(ordered) - 1))))
    return {
        "n": len(ordered),
        "p50": round(median(ordered), 6),
        "p95": round(ordered[p95_index], 6),
        "max": round(ordered[-1], 6),
    }


def _discovery_complete(row: Mapping[str, Any]) -> bool:
    if bool(row.get("window_complete")):
        return True
    primary = (row.get("horizons") or {}).get("12h") or {}
    return bool(primary.get("window_complete"))


def _preferred_oa_row(
    oa_rows: list[Mapping[str, Any]],
    preferred: str | None,
) -> Mapping[str, Any] | None:
    if preferred:
        for row in oa_rows:
            if str(row.get("direction") or "").upper() == preferred:
                return row
    return oa_rows[0] if oa_rows else None


def threshold_authority_report() -> dict[str, Any]:
    policy = AccountabilityPolicy.from_env()
    return {
        "MIN_TECHNICAL_SCORE": int(MIN_TECHNICAL_SCORE),
        "AccountabilityPolicy.production_threshold": policy.production_threshold,
        "single_canonical_source": policy.production_threshold
        == float(MIN_TECHNICAL_SCORE),
        "winner_definitions": {
            "v2_01": DISCOVERY_WINNER_DEFINITION,
            "accountability": ACCOUNTABILITY_WINNER_DEFINITION,
            "unifiable": False,
        },
        "drift_token": ACCOUNTABILITY_THRESHOLD_DRIFT,
    }


def reconcile_rows(
    *,
    discovery_rows: list[dict[str, Any]],
    accountability_rows: list[dict[str, Any]],
    phase3c_rows: list[dict[str, Any]] | None = None,
    attribution_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    latest_discovery = latest_discovery_outcomes_by_observation(discovery_rows)
    latest_oa = latest_accountability_by_id(list(accountability_rows))
    latest_phase3c = latest_phase3c_by_snapshot(list(phase3c_rows or []))

    discovery_by_obs: dict[str, dict[str, Any]] = {}
    discovery_by_join: dict[str, dict[str, Any]] = {}
    for row in latest_discovery.values():
        observation_id = str(row.get("observation_id") or "").strip()
        if observation_id:
            discovery_by_obs[observation_id] = row
        join_key = join_key_from_discovery(row)
        if join_key:
            discovery_by_join[join_key] = row

    oa_by_obs: dict[str, list[dict[str, Any]]] = {}
    oa_by_join: dict[str, list[dict[str, Any]]] = {}
    for row in latest_oa.values():
        observation_id = str(row.get("observation_id") or "").strip()
        if observation_id:
            oa_by_obs.setdefault(observation_id, []).append(row)
        join_key = join_key_from_accountability(row)
        if join_key:
            oa_by_join.setdefault(join_key, []).append(row)

    matched_obs = set(discovery_by_obs) & set(oa_by_obs)
    matched_join = set()
    for key in set(discovery_by_join) & set(oa_by_join):
        observation_id = str(discovery_by_join[key].get("observation_id") or "")
        if observation_id and observation_id in matched_obs:
            continue
        matched_join.add(key)

    overlap: list[dict[str, Any]] = []
    mfe_abs: list[float] = []
    mae_abs: list[float] = []
    direction_agree = 0
    direction_compared = 0
    winner_agree = 0
    winner_compared = 0
    complete_agree = 0
    complete_compared = 0
    identity_shared = 0

    def _add_overlap(discovery: Mapping[str, Any], oa_rows: list[Mapping[str, Any]], via: str) -> None:
        nonlocal direction_agree, direction_compared, winner_agree, winner_compared
        nonlocal complete_agree, complete_compared, identity_shared
        preferred = str(
            discovery.get("production_preferred_direction")
            or _preferred_production_direction(discovery)
            or ""
        ).upper()
        preferred_oa = _preferred_oa_row(oa_rows, preferred if preferred in {"LONG", "SHORT"} else None)
        realized = str(discovery.get("realized_opportunity_direction") or "").upper() or None
        if preferred in {"LONG", "SHORT"} and realized in {"LONG", "SHORT"}:
            direction_compared += 1
            if preferred == realized:
                direction_agree += 1
        market = str(discovery.get("market_discovery_opportunity_v1") or "")
        oa_winner = any(bool(row.get("market_winner")) for row in oa_rows)
        if market in {"WINNER", "NON_WINNER"}:
            winner_compared += 1
            if (market == "WINNER") == oa_winner:
                winner_agree += 1
        discovery_complete = _discovery_complete(discovery)
        oa_complete = bool(preferred_oa and preferred_oa.get("outcome_complete"))
        complete_compared += 1
        if discovery_complete == oa_complete:
            complete_agree += 1
        if str(discovery.get("observation_id") or "") and all(
            str(row.get("observation_id") or "") == str(discovery.get("observation_id") or "")
            for row in oa_rows
        ):
            identity_shared += 1
        snapshot_id = str((preferred_oa or {}).get("snapshot_id") or "")
        phase3c = latest_phase3c.get(snapshot_id) if snapshot_id else None
        primary = (discovery.get("horizons") or {}).get("12h") or {}
        disc_mfe = _finite(
            primary.get("long_mfe_pct") if preferred == "LONG" else primary.get("short_mfe_pct")
        )
        p3_mfe = _finite((phase3c or {}).get("mfe_pct"))
        p3_mae = _finite((phase3c or {}).get("mae_pct"))
        if preferred == "SHORT" and p3_mae is not None:
            p3_mfe = abs(p3_mae)
        if disc_mfe is not None and p3_mfe is not None:
            mfe_abs.append(abs(disc_mfe - p3_mfe))
        disc_mae = _finite(
            primary.get("long_mae_pct") if preferred == "LONG" else primary.get("short_mae_pct")
        )
        p3_adverse = _finite((preferred_oa or {}).get("adverse_excursion_pct"))
        if disc_mae is not None and p3_adverse is not None:
            mae_abs.append(abs(abs(disc_mae) - abs(p3_adverse)))
        overlap.append(
            {
                "via": via,
                "observation_id": discovery.get("observation_id"),
                "scan_id": discovery.get("scan_id"),
                "stage0": None,
                "production_preferred_direction": preferred or None,
                "realized_opportunity_direction": realized,
                "discovery_market_opportunity": market or None,
                "oa_any_market_winner": oa_winner,
                "preferred_oa_classification": (
                    preferred_oa.get("opportunity_classification") if preferred_oa else None
                ),
                "discovery_window_complete": discovery_complete,
                "oa_outcome_complete": oa_complete,
                "winner_definitions": {
                    "v2_01": DISCOVERY_WINNER_DEFINITION,
                    "accountability": ACCOUNTABILITY_WINNER_DEFINITION,
                },
            }
        )

    for observation_id in sorted(matched_obs):
        _add_overlap(discovery_by_obs[observation_id], oa_by_obs[observation_id], "observation_id")
    for join_key in sorted(matched_join):
        _add_overlap(discovery_by_join[join_key], oa_by_join[join_key], "reconstructed")

    discovery_complete = sum(1 for row in latest_discovery.values() if _discovery_complete(row))
    oa_complete = sum(1 for row in latest_oa.values() if bool(row.get("outcome_complete")))
    attribution_count = len(attribution_rows or [])
    return {
        "measurement_only": True,
        "trade_authority_changed": False,
        "policy_change_authorized": False,
        "join_keys": {
            "primary": "observation_id",
            "fallback": "scan_id|observed_at|normalized_symbol",
            "accountability_explodes_directions": True,
        },
        "population_counts": {
            "discovery_physical_rows": len(discovery_rows),
            "discovery_logical_latest": len(latest_discovery),
            "discovery_complete": discovery_complete,
            "discovery_incomplete": len(latest_discovery) - discovery_complete,
            "attribution_physical_rows": attribution_count,
            "accountability_physical_rows": len(accountability_rows),
            "accountability_logical_latest": len(latest_oa),
            "accountability_complete": oa_complete,
            "accountability_incomplete": len(latest_oa) - oa_complete,
            "phase3c_physical_rows": len(phase3c_rows or []),
            "phase3c_logical_latest": len(latest_phase3c),
            "matched_observation_id": len(matched_obs),
            "matched_reconstructed_only": len(matched_join),
            "matched": len(overlap),
            "discovery_unmatched": max(0, len(latest_discovery) - len(overlap)),
            "accountability_unmatched": max(0, len(oa_by_join) - len(overlap)),
            "match_rate_vs_discovery": (
                round(len(overlap) / len(latest_discovery), 6)
                if latest_discovery
                else None
            ),
        },
        "outcome_reconciliation": {
            "overlapping_observations": len(overlap),
            "shared_observation_id_count": identity_shared,
            "preferred_vs_realized_direction": {
                "agree": direction_agree,
                "compared": direction_compared,
                "rate": round(direction_agree / direction_compared, 6)
                if direction_compared
                else None,
            },
            "winner_label_agreement": {
                "agree": winner_agree,
                "compared": winner_compared,
                "rate": round(winner_agree / winner_compared, 6)
                if winner_compared
                else None,
                "note": (
                    "Definitions are not unifiable. Disagreement is expected "
                    f"between {DISCOVERY_WINNER_DEFINITION} and "
                    f"{ACCOUNTABILITY_WINNER_DEFINITION}."
                ),
            },
            "mfe_12h_vs_phase3c_24h_abs_pct": _quantiles(mfe_abs),
            "mae_abs_pct": _quantiles(mae_abs),
            "window_complete_agreement": {
                "agree": complete_agree,
                "compared": complete_compared,
                "note": "V2-01 primary completeness is 12h; OA/Phase3C completeness is 24h.",
            },
        },
        "threshold_authorities": threshold_authority_report(),
        "overlap_sample": overlap[:50],
        "labeled_at": datetime.now(timezone.utc).isoformat(),
    }


def inspect_replica(data_root: Path | str) -> dict[str, Any]:
    root = Path(data_root)
    discovery = _load_jsonl(root / "opip/discovery/forward_outcomes.jsonl")
    attributions = _load_jsonl(root / "opip/discovery/attributions.jsonl")
    accountability = _load_jsonl(root / "opip/opportunity_accountability.jsonl")
    phase3c = _load_jsonl(root / "phase3c_forward_outcomes.jsonl")
    report = reconcile_rows(
        discovery_rows=discovery,
        accountability_rows=accountability,
        phase3c_rows=phase3c,
        attribution_rows=attributions,
    )
    report["replica_root"] = str(root)
    report["replica_available"] = bool(discovery or accountability or phase3c)
    return report


def persist_reconciliation_report(report: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


# Keep observation_join_id imported for tests that prove the formula.
__all__ = [
    "DISCOVERY_WINNER_DEFINITION",
    "inspect_replica",
    "join_key_from_accountability",
    "join_key_from_discovery",
    "observation_join_id",
    "persist_reconciliation_report",
    "reconcile_rows",
    "reconstructed_join_key",
    "threshold_authority_report",
    "_observation_id_from_screening",
    "_preferred_production_direction",
    "_screening_symbol",
]
