"""Read-only opportunity accountability and missed-winner intelligence.

This module joins evidence that O'Pip already persists. It never scans a market,
changes a score, ranks a live candidate, sends an alert, admits a paper trade, or
holds exchange authority.

The accountability population is deliberately wider than qualified signals:
every directional score observed by Broad Search is retained so later forward
outcomes can answer whether a rejection, shortlist cap, threshold, operational
failure, or late decision hid a profitable market move.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable, Mapping


DEFAULT_SCREENING_FILE = Path("/app/data/opip/qualification/screening_evaluations.jsonl")
DEFAULT_FUNNEL_FILE = Path("/app/data/opip/qualification/funnel_events.jsonl")
DEFAULT_SNAPSHOT_FILE = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_OUTCOME_FILE = Path("/app/data/phase3c_forward_outcomes.jsonl")
DEFAULT_INTELLIGENCE_EVENT_FILE = Path("/app/data/intelligence_learning/events.jsonl")
DEFAULT_LEDGER_FILE = Path("/app/data/opip/opportunity_accountability.jsonl")
DEFAULT_SUMMARY_FILE = Path("/app/data/opip/opportunity_accountability_summary.json")


@dataclass(frozen=True)
class AccountabilityPolicy:
    production_threshold: float = 80.0
    shadow_threshold: float = 70.0
    winner_move_pct: float = 2.0

    @classmethod
    def from_env(cls) -> "AccountabilityPolicy":
        def number(name: str, default: float) -> float:
            try:
                value = float(os.getenv(name, str(default)))
            except (TypeError, ValueError):
                return default
            return value if math.isfinite(value) else default

        production = max(0.0, number("OPIP_PRODUCTION_TECHNICAL_THRESHOLD", 80.0))
        shadow = max(0.0, number("OPIP_ACCOUNTABILITY_SHADOW_THRESHOLD", 70.0))
        winner = max(0.0, number("OPIP_ACCOUNTABILITY_WINNER_MOVE_PCT", 2.0))
        if shadow > production:
            shadow = production
        return cls(production, shadow, winner)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        stamp = value
    elif value:
        try:
            stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    stamp = _parse_utc(value)
    return stamp.isoformat() if stamp is not None else None


def _normalize_symbol(value: Any) -> str:
    text = str(value or "").upper().strip()
    if ":" in text:
        text = text.split(":", 1)[0]
    for token in ("/", "-", "_", " "):
        text = text.replace(token, "")
    return text


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                text = raw.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def _screening_symbol(row: Mapping[str, Any]) -> str:
    identity = row.get("venue_instrument")
    nested = identity if isinstance(identity, Mapping) else {}
    return _normalize_symbol(
        nested.get("venue_instrument_symbol")
        or row.get("venue_instrument_id")
        or nested.get("raw_identifier")
    )


def _snapshot_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        decision = _iso(row.get("decision_at_utc"))
        symbol = _normalize_symbol(row.get("symbol"))
        if decision and symbol:
            indexed[(decision, symbol)] = dict(row)
    return indexed


def _latest_outcome_index(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    revisions: dict[str, int] = {}
    for row in rows:
        snapshot_id = str(row.get("snapshot_id") or "")
        if not snapshot_id:
            continue
        try:
            revision = int(row.get("outcome_revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if snapshot_id not in indexed or revision >= revisions.get(snapshot_id, -1):
            indexed[snapshot_id] = dict(row)
            revisions[snapshot_id] = revision
    return indexed


def _funnel_index(rows: Iterable[Mapping[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        scan_id = str(row.get("scan_id") or "")
        symbol = _normalize_symbol(row.get("pair") or row.get("symbol") or row.get("asset"))
        direction = str(row.get("direction") or "").upper()
        if scan_id and symbol and direction in {"LONG", "SHORT"}:
            indexed[(scan_id, symbol, direction)] = dict(row)
    return indexed


def _paper_indexes(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    admissions: dict[str, dict[str, Any]] = {}
    outcomes: dict[str, dict[str, Any]] = {}
    for row in rows:
        signal_id = str(row.get("signal_id") or "")
        if not signal_id:
            continue
        event_type = str(row.get("event_type") or "").upper()
        if event_type == "PAPER_ADMISSION":
            admissions[signal_id] = dict(row)
        elif event_type == "PAPER_OUTCOME":
            outcomes[signal_id] = dict(row)
    return admissions, outcomes


def _gate_map(funnel: Mapping[str, Any] | None) -> dict[str, Mapping[str, Any]]:
    if not isinstance(funnel, Mapping):
        return {}
    gates = funnel.get("gate_results")
    result: dict[str, Mapping[str, Any]] = {}
    for row in gates if isinstance(gates, list) else []:
        if isinstance(row, Mapping):
            name = str(row.get("gate") or "").upper()
            if name:
                result[name] = row
    return result


def _executability(funnel: Mapping[str, Any] | None, direction: str) -> str:
    if not isinstance(funnel, Mapping):
        return "UNVALIDATED"
    gates = _gate_map(funnel)
    margin = gates.get("MARGIN_ELIGIBILITY")
    execution = gates.get("EXECUTION_VALIDATION")

    if direction == "SHORT":
        if margin is None:
            return "UNVALIDATED"
        if str(margin.get("status") or "").upper() != "PASS":
            return "MARGIN_REJECTED"
    if execution is None:
        return "UNVALIDATED"
    if str(execution.get("status") or "").upper() != "PASS":
        return "EXECUTION_REJECTED"
    return "VALIDATED"


def _directional_outcome(outcome: Mapping[str, Any] | None, direction: str) -> dict[str, Any]:
    if not isinstance(outcome, Mapping):
        return {
            "outcome_available": False,
            "outcome_complete": False,
            "favorable_excursion_pct": None,
            "adverse_excursion_pct": None,
            "directional_horizon_returns_pct": {},
        }

    mfe = _finite(outcome.get("mfe_pct"))
    mae = _finite(outcome.get("mae_pct"))
    if direction == "SHORT":
        favorable = max(0.0, -mae) if mae is not None else None
        adverse = max(0.0, mfe) if mfe is not None else None
    else:
        favorable = max(0.0, mfe) if mfe is not None else None
        adverse = max(0.0, -mae) if mae is not None else None

    raw_horizons = outcome.get("horizon_returns_pct")
    directional_horizons: dict[str, float | None] = {}
    if isinstance(raw_horizons, Mapping):
        for label, value in raw_horizons.items():
            number = _finite(value)
            directional_horizons[str(label)] = (
                (-number if direction == "SHORT" else number)
                if number is not None
                else None
            )

    return {
        "outcome_available": True,
        "outcome_complete": bool(outcome.get("window_complete", False)),
        "outcome_revision": int(outcome.get("outcome_revision") or 0),
        "favorable_excursion_pct": favorable,
        "adverse_excursion_pct": adverse,
        "time_to_favorable_extreme_seconds": (
            outcome.get("time_to_mae_seconds")
            if direction == "SHORT"
            else outcome.get("time_to_mfe_seconds")
        ),
        "directional_horizon_returns_pct": directional_horizons,
    }


def _range_consumed_proxy(snapshot: Mapping[str, Any] | None, direction: str) -> float | None:
    if not isinstance(snapshot, Mapping):
        return None
    price = _finite(snapshot.get("reference_price") or snapshot.get("last_price"))
    high = _finite(snapshot.get("high_24h"))
    low = _finite(snapshot.get("low_24h"))
    if price is None or high is None or low is None or high <= low:
        return None
    if direction == "SHORT":
        fraction = (high - price) / (high - low)
    else:
        fraction = (price - low) / (high - low)
    return round(max(0.0, min(1.0, fraction)) * 100.0, 4)


def _latency_evidence(funnel: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(funnel, Mapping):
        return {
            "decision_latency_ms": None,
            "gate_elapsed_from_decision_ms": {},
            "gate_delta_ms": {},
        }
    started = _parse_utc(funnel.get("decision_at_utc") or funnel.get("decided_at"))
    if started is None:
        return {
            "decision_latency_ms": None,
            "gate_elapsed_from_decision_ms": {},
            "gate_delta_ms": {},
        }

    elapsed: dict[str, float] = {}
    deltas: dict[str, float] = {}
    previous = started
    latest = started
    gates = funnel.get("gate_results")
    for gate in gates if isinstance(gates, list) else []:
        if not isinstance(gate, Mapping):
            continue
        name = str(gate.get("gate") or "")
        at = _parse_utc(gate.get("evaluated_at"))
        if not name or at is None:
            continue
        elapsed[name] = round(max(0.0, (at - started).total_seconds() * 1000.0), 3)
        deltas[name] = round(max(0.0, (at - previous).total_seconds() * 1000.0), 3)
        previous = at
        latest = max(latest, at)

    return {
        "decision_latency_ms": round(max(0.0, (latest - started).total_seconds() * 1000.0), 3),
        "gate_elapsed_from_decision_ms": elapsed,
        "gate_delta_ms": deltas,
    }


def _paper_payload(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(row, Mapping):
        return None
    payload = row.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _classification(
    *,
    score: float | None,
    screening_outcome: str,
    production_direction: bool,
    funnel: Mapping[str, Any] | None,
    outcome: Mapping[str, Any],
    executability: str,
    policy: AccountabilityPolicy,
) -> tuple[str, bool, bool]:
    favorable = _finite(outcome.get("favorable_excursion_pct"))
    winner = favorable is not None and favorable >= policy.winner_move_pct
    production_selected = funnel is not None
    decision = str((funnel or {}).get("decision") or "").upper()
    reason_class = str((funnel or {}).get("terminal_reason_class") or "").upper()

    if not outcome.get("outcome_available"):
        return "PENDING_OUTCOME", False, False
    if decision == "QUALIFIED":
        return ("CAPTURED_WINNER" if winner else "QUALIFIED_NONWINNER"), winner, False
    if not winner:
        return "CORRECT_REJECT_OR_NO_MEANINGFUL_MOVE", False, False

    executable_false_negative = production_selected and executability == "VALIDATED"
    if executable_false_negative:
        if reason_class == "OPERATIONAL":
            return "OPERATIONAL_EXECUTABLE_MISS", True, True
        return "EXECUTABLE_FALSE_NEGATIVE", True, True

    if production_direction and not production_selected:
        return "RANKING_OR_CAP_MISS_CANDIDATE", True, False
    if score is not None and policy.shadow_threshold <= score < policy.production_threshold:
        return "THRESHOLD_70_79_MISS_CANDIDATE", True, False
    if screening_outcome == "BELOW_THRESHOLD":
        return "BELOW_THRESHOLD_MARKET_WINNER", True, False
    return "MARKET_WINNER_UNVERIFIED_EXECUTABILITY", True, False


def build_accountability_rows(
    *,
    screening_rows: Iterable[Mapping[str, Any]],
    funnel_rows: Iterable[Mapping[str, Any]],
    snapshot_rows: Iterable[Mapping[str, Any]],
    outcome_rows: Iterable[Mapping[str, Any]],
    intelligence_events: Iterable[Mapping[str, Any]] = (),
    policy: AccountabilityPolicy | None = None,
) -> list[dict[str, Any]]:
    config = policy or AccountabilityPolicy.from_env()
    snapshots = _snapshot_index(snapshot_rows)
    outcomes = _latest_outcome_index(outcome_rows)
    funnels = _funnel_index(funnel_rows)
    paper_admissions, paper_outcomes = _paper_indexes(intelligence_events)

    result: list[dict[str, Any]] = []
    for screening in screening_rows:
        if str(screening.get("scanner_type") or "").upper() != "BROAD_SEARCH":
            continue
        observed_at = _iso(screening.get("observed_at"))
        scan_id = str(screening.get("scan_id") or "")
        symbol = _screening_symbol(screening)
        if not observed_at or not scan_id or not symbol:
            continue

        snapshot = snapshots.get((observed_at, symbol))
        snapshot_id = str((snapshot or {}).get("snapshot_id") or "")
        outcome_row = outcomes.get(snapshot_id)
        advanced_direction = str(screening.get("advanced_direction") or "").upper()
        screening_outcome = str(screening.get("outcome") or "UNKNOWN").upper()

        for direction, score_field in (("LONG", "long_score"), ("SHORT", "short_score")):
            score = _finite(screening.get(score_field))
            if score is None:
                continue
            funnel = funnels.get((scan_id, symbol, direction))
            direction_outcome = _directional_outcome(outcome_row, direction)
            executability = _executability(funnel, direction)
            classification, market_winner, executable_false_negative = _classification(
                score=score,
                screening_outcome=screening_outcome,
                production_direction=advanced_direction == direction,
                funnel=funnel,
                outcome=direction_outcome,
                executability=executability,
                policy=config,
            )
            signal_id = str((funnel or {}).get("signal_id") or "")
            paper_admission = paper_admissions.get(signal_id)
            paper_outcome = paper_outcomes.get(signal_id)
            favorable = _finite(direction_outcome.get("favorable_excursion_pct"))
            range_consumed = _range_consumed_proxy(snapshot, direction)

            identity_raw = "|".join(
                [scan_id, symbol, direction, observed_at, snapshot_id or "NO_SNAPSHOT"]
            )
            accountability_id = "OA:" + hashlib.sha256(
                identity_raw.encode("utf-8")
            ).hexdigest()[:32]

            row = {
                "record_type": "OPPORTUNITY_ACCOUNTABILITY",
                "schema_version": 1,
                "accountability_id": accountability_id,
                "observed_at": observed_at,
                "scan_id": scan_id,
                "symbol": symbol,
                "direction": direction,
                "screening_outcome": screening_outcome,
                "technical_score": score,
                "production_direction": advanced_direction == direction,
                "production_selected": funnel is not None,
                "production_threshold": config.production_threshold,
                "shadow_threshold": config.shadow_threshold,
                "winner_move_threshold_pct": config.winner_move_pct,
                "snapshot_id": snapshot_id or None,
                "episode_id": (funnel or {}).get("episode_id") or (snapshot or {}).get("episode_id"),
                "candidate_id": (funnel or {}).get("candidate_id"),
                "signal_id": signal_id or None,
                "production_decision": (funnel or {}).get("decision"),
                "terminal_gate": (funnel or {}).get("first_terminal_gate"),
                "terminal_reason_code": (funnel or {}).get("terminal_reason_code"),
                "terminal_reason_class": (funnel or {}).get("terminal_reason_class"),
                "terminal_reason": (funnel or {}).get("terminal_reason"),
                "executability_status": executability,
                **direction_outcome,
                "market_winner": bool(market_winner),
                "executable_false_negative": bool(executable_false_negative),
                "opportunity_classification": classification,
                "estimated_missed_move_pct": (
                    round(favorable, 6)
                    if executable_false_negative and favorable is not None
                    else None
                ),
                "range_consumed_proxy_pct": range_consumed,
                "latency": _latency_evidence(funnel),
                "paper_admission": (
                    {
                        "admitted": paper_admission.get("admitted"),
                        "reason": paper_admission.get("reason"),
                        "observed_at": paper_admission.get("observed_at"),
                        "payload": _paper_payload(paper_admission),
                    }
                    if paper_admission is not None
                    else None
                ),
                "paper_outcome": (
                    {
                        "observed_at": paper_outcome.get("observed_at"),
                        "payload": _paper_payload(paper_outcome),
                    }
                    if paper_outcome is not None
                    else None
                ),
                "counterfactuals": {
                    "threshold_70_79_shadow": bool(
                        config.shadow_threshold <= score < config.production_threshold
                    ),
                    "expanded_cap_shadow": bool(
                        advanced_direction == direction and funnel is None
                    ),
                    "decay_aware_shadow": {
                        "eligible": range_consumed is not None,
                        "range_consumed_proxy_pct": range_consumed,
                        "authoritative_move_completed_fraction": False,
                    },
                },
                "measurement_only": True,
                "affects_ranking": False,
                "affects_alert_authority": False,
                "affects_trade_authority": False,
            }
            json.dumps(row, sort_keys=True, allow_nan=False)
            result.append(row)

    return sorted(
        result,
        key=lambda row: (
            str(row.get("observed_at") or ""),
            str(row.get("symbol") or ""),
            str(row.get("direction") or ""),
        ),
    )


def _latest_ledger_rows(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        identity = str(row.get("accountability_id") or "")
        if not identity:
            continue
        try:
            revision = int(row.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        prior = latest.get(identity)
        try:
            prior_revision = int((prior or {}).get("revision") or 0)
        except (TypeError, ValueError):
            prior_revision = 0
        if prior is None or revision >= prior_revision:
            latest[identity] = dict(row)
    return latest


def _row_fingerprint(row: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in row.items()
        if key not in {"revision", "recorded_at", "accountability_record_id"}
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def append_accountability_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    path: Path = DEFAULT_LEDGER_FILE,
) -> list[dict[str, Any]]:
    pending = [dict(row) for row in rows]
    existing = read_jsonl(path)
    latest = _latest_ledger_rows(existing)
    appended: list[dict[str, Any]] = []
    path.parent.mkdir(parents=True, exist_ok=True)

    for row in pending:
        identity = str(row.get("accountability_id") or "")
        if not identity:
            continue
        prior = latest.get(identity)
        fingerprint = _row_fingerprint(row)
        if prior is not None and str(prior.get("accountability_record_id") or "") == fingerprint:
            continue
        revision = int((prior or {}).get("revision") or 0) + 1
        materialized = {
            **row,
            "revision": revision,
            "accountability_record_id": fingerprint,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "append_only": True,
        }
        appended.append(materialized)
        latest[identity] = materialized

    if appended:
        with path.open("a", encoding="utf-8") as handle:
            for row in appended:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    return list(latest.values())


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 4)


def _paper_net_pnl(row: Mapping[str, Any]) -> float | None:
    paper = row.get("paper_outcome")
    if not isinstance(paper, Mapping):
        return None
    payload = paper.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return _finite(payload.get("net_pnl"))


def build_accountability_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    current = list(rows)
    completed = [row for row in current if bool(row.get("outcome_complete"))]
    winners = [row for row in completed if bool(row.get("market_winner"))]
    captured_winners = [
        row for row in winners if row.get("opportunity_classification") == "CAPTURED_WINNER"
    ]
    executable_misses = [
        row for row in winners if bool(row.get("executable_false_negative"))
    ]
    threshold_misses = [
        row for row in winners
        if row.get("opportunity_classification") == "THRESHOLD_70_79_MISS_CANDIDATE"
    ]
    cap_misses = [
        row for row in winners
        if row.get("opportunity_classification") == "RANKING_OR_CAP_MISS_CANDIDATE"
    ]
    operational_misses = [
        row for row in winners
        if row.get("opportunity_classification") == "OPERATIONAL_EXECUTABLE_MISS"
    ]
    latency_values = [
        _finite((row.get("latency") or {}).get("decision_latency_ms"))
        for row in current
        if isinstance(row.get("latency"), Mapping)
    ]
    latency_values = [value for value in latency_values if value is not None]

    by_terminal_gate: dict[str, int] = {}
    by_classification: dict[str, int] = {}
    for row in current:
        classification = str(row.get("opportunity_classification") or "UNKNOWN")
        by_classification[classification] = by_classification.get(classification, 0) + 1
        if bool(row.get("market_winner")):
            gate = str(row.get("terminal_gate") or "NO_TERMINAL_GATE")
            by_terminal_gate[gate] = by_terminal_gate.get(gate, 0) + 1

    paper_values = [
        value for value in (_paper_net_pnl(row) for row in current) if value is not None
    ]
    paper_wins = sum(value > 0 for value in paper_values)

    validated_opportunities = len(captured_winners) + len(executable_misses)
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "population": {
            "directional_evaluations": len(current),
            "completed_forward_outcomes": len(completed),
            "market_winner_candidates": len(winners),
            "captured_winners": len(captured_winners),
            "executable_false_negatives": len(executable_misses),
            "threshold_70_79_miss_candidates": len(threshold_misses),
            "ranking_or_cap_miss_candidates": len(cap_misses),
            "operational_executable_misses": len(operational_misses),
        },
        "opportunity_capture_rate_pct": _pct(
            len(captured_winners),
            validated_opportunities,
        ),
        "estimated_missed_move_pct_sum": round(
            sum(
                float(row.get("estimated_missed_move_pct") or 0.0)
                for row in executable_misses
            ),
            6,
        ),
        "by_classification": dict(sorted(by_classification.items())),
        "missed_winners_by_terminal_gate": dict(
            sorted(by_terminal_gate.items(), key=lambda item: (-item[1], item[0]))
        ),
        "latency": {
            "samples": len(latency_values),
            "mean_decision_latency_ms": round(mean(latency_values), 3)
            if latency_values
            else None,
            "median_decision_latency_ms": round(median(latency_values), 3)
            if latency_values
            else None,
        },
        "paper": {
            "outcomes": len(paper_values),
            "wins": paper_wins,
            "win_rate_pct": _pct(paper_wins, len(paper_values)),
            "net_pnl": round(sum(paper_values), 8) if paper_values else None,
        },
        "counterfactual_experiments": {
            "threshold_70_79": {
                "population": sum(
                    bool((row.get("counterfactuals") or {}).get("threshold_70_79_shadow"))
                    for row in current
                ),
                "winner_candidates": len(threshold_misses),
                "production_changed": False,
            },
            "expanded_cap": {
                "population": sum(
                    bool((row.get("counterfactuals") or {}).get("expanded_cap_shadow"))
                    for row in current
                ),
                "winner_candidates": len(cap_misses),
                "production_changed": False,
            },
            "decay_aware": {
                "status": "SHADOW_PROXY_ONLY",
                "metric": "24h range consumed proxy",
                "production_changed": False,
            },
        },
        "guardrails": {
            "production_threshold_changed": False,
            "margin_policy_changed": False,
            "ranking_changed": False,
            "alert_authority_changed": False,
            "trade_authority_changed": False,
        },
    }


def write_summary(
    summary: Mapping[str, Any],
    *,
    path: Path = DEFAULT_SUMMARY_FILE,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        try:
            os.fsync(handle.fileno())
        except OSError:
            pass
    os.replace(temp, path)


def build_from_files(
    *,
    screening_path: Path = DEFAULT_SCREENING_FILE,
    funnel_path: Path = DEFAULT_FUNNEL_FILE,
    snapshot_path: Path = DEFAULT_SNAPSHOT_FILE,
    outcome_path: Path = DEFAULT_OUTCOME_FILE,
    intelligence_event_path: Path = DEFAULT_INTELLIGENCE_EVENT_FILE,
    ledger_path: Path = DEFAULT_LEDGER_FILE,
    summary_path: Path = DEFAULT_SUMMARY_FILE,
    policy: AccountabilityPolicy | None = None,
) -> dict[str, Any]:
    rows = build_accountability_rows(
        screening_rows=read_jsonl(screening_path),
        funnel_rows=read_jsonl(funnel_path),
        snapshot_rows=read_jsonl(snapshot_path),
        outcome_rows=read_jsonl(outcome_path),
        intelligence_events=read_jsonl(intelligence_event_path),
        policy=policy,
    )
    current = append_accountability_rows(rows, path=ledger_path)
    summary = build_accountability_summary(current)
    write_summary(summary, path=summary_path)
    return summary
