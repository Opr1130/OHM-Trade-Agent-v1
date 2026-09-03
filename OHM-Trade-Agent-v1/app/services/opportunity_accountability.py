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
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from statistics import mean, median
import zlib
from typing import Any, Iterable, Mapping


DEFAULT_SCREENING_FILE = Path("/app/data/opip/qualification/screening_evaluations.jsonl")
DEFAULT_FUNNEL_FILE = Path("/app/data/opip/qualification/funnel_events.jsonl")
DEFAULT_SNAPSHOT_FILE = Path("/app/data/p1_evidence_ledger.jsonl")
DEFAULT_OUTCOME_FILE = Path("/app/data/phase3c_forward_outcomes.jsonl")
DEFAULT_INTELLIGENCE_EVENT_FILE = Path("/app/data/intelligence_learning/events.jsonl")
DEFAULT_LEDGER_FILE = Path("/app/data/opip/opportunity_accountability.jsonl")
DEFAULT_SUMMARY_FILE = Path("/app/data/opip/opportunity_accountability_summary.json")
DEFAULT_SCREENING_ARCHIVE = Path("/app/data/opip/qualification/screening_evaluations_archive")
DEFAULT_FUNNEL_ARCHIVE = Path("/app/data/opip/qualification/funnel_events_archive")


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
        "within_major_move_episode": bool(
            outcome.get("within_major_move_episode", False)
        ),
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
    high = _finite(
        snapshot.get("high_24h") or snapshot.get("recent_24h_high")
    )
    low = _finite(
        snapshot.get("low_24h") or snapshot.get("recent_24h_low")
    )
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
        evidence_snapshot = dict(snapshot or {})
        screening_metadata = screening.get("metadata")
        if isinstance(screening_metadata, Mapping):
            for source_name, target_name in (
                ("reference_price", "reference_price"),
                ("recent_24h_high", "recent_24h_high"),
                ("recent_24h_low", "recent_24h_low"),
            ):
                if evidence_snapshot.get(target_name) is None:
                    evidence_snapshot[target_name] = screening_metadata.get(source_name)
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
            range_consumed = _range_consumed_proxy(evidence_snapshot, direction)

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
            try:
                json.dumps(row, sort_keys=True, allow_nan=False)
            except (TypeError, ValueError):
                continue
            result.append(row)

    return sorted(
        result,
        key=lambda row: (
            str(row.get("observed_at") or ""),
            str(row.get("symbol") or ""),
            str(row.get("direction") or ""),
        ),
    )


def _state_path_for(path: Path) -> Path:
    return path.parent / f".{path.name}.state.sqlite3"


def _open_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_index (
            signal_id TEXT PRIMARY KEY,
            accountability_id TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS paper_event_pending (
            event_key TEXT PRIMARY KEY,
            signal_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            observed_at TEXT,
            row_json TEXT NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS latest (
            accountability_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            revision INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            classification TEXT NOT NULL,
            terminal_gate TEXT,
            outcome_complete INTEGER NOT NULL,
            market_winner INTEGER NOT NULL,
            captured_winner INTEGER NOT NULL,
            executable_false_negative INTEGER NOT NULL,
            threshold_miss INTEGER NOT NULL,
            cap_miss INTEGER NOT NULL,
            operational_miss INTEGER NOT NULL,
            threshold_shadow INTEGER NOT NULL,
            expanded_cap_shadow INTEGER NOT NULL,
            estimated_missed_move REAL,
            decision_latency_ms REAL,
            paper_net_pnl REAL,
            range_consumed_proxy REAL,
            row_json TEXT NOT NULL
        )
        """
    )
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(latest)").fetchall()
    }
    if "range_consumed_proxy" not in columns:
        connection.execute(
            "ALTER TABLE latest ADD COLUMN range_consumed_proxy REAL"
        )
    connection.commit()
    return connection


def _state_offset(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = 'ledger_offset'"
    ).fetchone()
    try:
        return int(row[0]) if row is not None else 0
    except (TypeError, ValueError):
        return 0


def _set_state_offset(connection: sqlite3.Connection, value: int) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES ('ledger_offset', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(max(0, int(value))),),
    )


def _paper_net_pnl(row: Mapping[str, Any]) -> float | None:
    paper = row.get("paper_outcome")
    if not isinstance(paper, Mapping):
        return None
    payload = paper.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return _finite(payload.get("net_pnl"))


def _upsert_state_row(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    identity = str(row.get("accountability_id") or "")
    fingerprint = str(row.get("accountability_record_id") or "")
    if not identity or not fingerprint:
        return
    classification = str(row.get("opportunity_classification") or "UNKNOWN")
    counterfactuals = row.get("counterfactuals")
    counter = counterfactuals if isinstance(counterfactuals, Mapping) else {}
    latency = row.get("latency")
    latency_map = latency if isinstance(latency, Mapping) else {}
    connection.execute(
        """
        INSERT INTO latest(
            accountability_id, fingerprint, revision, observed_at,
            classification, terminal_gate, outcome_complete, market_winner,
            captured_winner, executable_false_negative, threshold_miss,
            cap_miss, operational_miss, threshold_shadow,
            expanded_cap_shadow, estimated_missed_move,
            decision_latency_ms, paper_net_pnl, range_consumed_proxy, row_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(accountability_id) DO UPDATE SET
            fingerprint = excluded.fingerprint,
            revision = excluded.revision,
            observed_at = excluded.observed_at,
            classification = excluded.classification,
            terminal_gate = excluded.terminal_gate,
            outcome_complete = excluded.outcome_complete,
            market_winner = excluded.market_winner,
            captured_winner = excluded.captured_winner,
            executable_false_negative = excluded.executable_false_negative,
            threshold_miss = excluded.threshold_miss,
            cap_miss = excluded.cap_miss,
            operational_miss = excluded.operational_miss,
            threshold_shadow = excluded.threshold_shadow,
            expanded_cap_shadow = excluded.expanded_cap_shadow,
            estimated_missed_move = excluded.estimated_missed_move,
            decision_latency_ms = excluded.decision_latency_ms,
            paper_net_pnl = excluded.paper_net_pnl,
            range_consumed_proxy = excluded.range_consumed_proxy,
            row_json = excluded.row_json
        WHERE excluded.revision >= latest.revision
        """,
        (
            identity,
            fingerprint,
            int(row.get("revision") or 0),
            str(row.get("observed_at") or ""),
            classification,
            row.get("terminal_gate"),
            1 if bool(row.get("outcome_complete")) else 0,
            1 if bool(row.get("market_winner")) else 0,
            1 if classification == "CAPTURED_WINNER" else 0,
            1 if bool(row.get("executable_false_negative")) else 0,
            1 if classification == "THRESHOLD_70_79_MISS_CANDIDATE" else 0,
            1 if classification == "RANKING_OR_CAP_MISS_CANDIDATE" else 0,
            1 if classification == "OPERATIONAL_EXECUTABLE_MISS" else 0,
            1 if bool(counter.get("threshold_70_79_shadow")) else 0,
            1 if bool(counter.get("expanded_cap_shadow")) else 0,
            _finite(row.get("estimated_missed_move_pct")),
            _finite(latency_map.get("decision_latency_ms")),
            _paper_net_pnl(row),
            _finite(row.get("range_consumed_proxy_pct")),
            json.dumps(dict(row), sort_keys=True, allow_nan=False),
        ),
    )
    signal_id = str(row.get("signal_id") or "")
    if signal_id:
        connection.execute(
            "DELETE FROM signal_index "
            "WHERE accountability_id = ? AND signal_id <> ?",
            (identity, signal_id),
        )
        connection.execute(
            """
            INSERT INTO signal_index(signal_id, accountability_id)
            VALUES (?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                accountability_id = excluded.accountability_id
            """,
            (signal_id, identity),
        )


def _reconcile_state(connection: sqlite3.Connection, path: Path) -> None:
    if not path.exists():
        if _state_offset(connection):
            connection.execute("DELETE FROM latest")
            connection.execute("DELETE FROM signal_index")
            _set_state_offset(connection, 0)
            connection.commit()
        return
    size = path.stat().st_size
    offset = _state_offset(connection)
    if offset > size:
        connection.execute("DELETE FROM latest")
        connection.execute("DELETE FROM signal_index")
        offset = 0
        _set_state_offset(connection, 0)
    with path.open("rb") as handle:
        handle.seek(offset)
        last_complete = offset
        while True:
            raw = handle.readline()
            if not raw:
                break
            end = handle.tell()
            if not raw.endswith(b"\n"):
                break
            last_complete = end
            try:
                row = json.loads(raw.decode("utf-8", errors="replace"))
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                _upsert_state_row(connection, row)
        _set_state_offset(connection, last_complete)
    connection.commit()


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
    state_path: Path | None = None,
) -> list[dict[str, Any]]:
    pending = [dict(row) for row in rows]
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _open_state(state_path or _state_path_for(path))
    appended: list[dict[str, Any]] = []
    try:
        _reconcile_state(connection, path)
        for row in pending:
            identity = str(row.get("accountability_id") or "")
            if not identity:
                continue
            fingerprint = _row_fingerprint(row)
            prior = connection.execute(
                "SELECT fingerprint, revision FROM latest WHERE accountability_id = ?",
                (identity,),
            ).fetchone()
            if prior is not None and str(prior[0]) == fingerprint:
                continue
            revision = (int(prior[1]) if prior is not None else 0) + 1
            materialized = {
                **row,
                "revision": revision,
                "accountability_record_id": fingerprint,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
                "append_only": True,
            }
            appended.append(materialized)

        if appended:
            with path.open("ab") as handle:
                for row in appended:
                    handle.write(
                        (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode(
                            "utf-8"
                        )
                    )
                handle.flush()
                os.fsync(handle.fileno())
                end_offset = handle.tell()
            for row in appended:
                _upsert_state_row(connection, row)
            _set_state_offset(connection, end_offset)
            connection.commit()
        return appended
    finally:
        connection.close()


def _metadata_int(
    connection: sqlite3.Connection,
    key: str,
    default: int = 0,
) -> int:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return default


def _metadata_text(
    connection: sqlite3.Connection,
    key: str,
) -> str | None:
    row = connection.execute(
        "SELECT value FROM metadata WHERE key = ?",
        (key,),
    ).fetchone()
    return str(row[0]) if row is not None else None


def _set_metadata(
    connection: sqlite3.Connection,
    key: str,
    value: Any,
) -> None:
    connection.execute(
        """
        INSERT INTO metadata(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(key), str(value)),
    )


def _paper_event_anchor(path: Path, offset: int) -> str:
    if offset <= 0:
        return ""
    size = min(4096, offset)
    with path.open("rb") as handle:
        handle.seek(offset - size)
        payload = handle.read(size)
    return hashlib.sha256(payload).hexdigest()


def _paper_event_key(row: Mapping[str, Any]) -> str | None:
    try:
        encoded = json.dumps(
            dict(row),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return "PAPER:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _apply_paper_event(
    row: dict[str, Any],
    event: Mapping[str, Any],
) -> None:
    event_type = str(event.get("event_type") or "").upper()
    if event_type == "PAPER_ADMISSION":
        row["paper_admission"] = {
            "admitted": event.get("admitted"),
            "reason": event.get("reason"),
            "observed_at": event.get("observed_at"),
            "payload": _paper_payload(event),
        }
    elif event_type == "PAPER_OUTCOME":
        row["paper_outcome"] = {
            "observed_at": event.get("observed_at"),
            "payload": _paper_payload(event),
        }


def reconcile_paper_events(
    *,
    intelligence_event_path: Path = DEFAULT_INTELLIGENCE_EVENT_FILE,
    ledger_path: Path = DEFAULT_LEDGER_FILE,
    state_path: Path | None = None,
) -> int:
    """Incrementally apply paper admission/outcome events to existing rows.

    Relevant events are first staged durably. This lets a paper event arrive
    before its accountability row without being lost; once the signal mapping
    exists, the event revises the append-only accountability row.
    """
    state_db = state_path or _state_path_for(ledger_path)
    connection = _open_state(state_db)
    processed_keys: list[str] = []
    pending_updates: dict[str, dict[str, Any]] = {}
    try:
        _reconcile_state(connection, ledger_path)
        if intelligence_event_path.exists():
            size = intelligence_event_path.stat().st_size
            offset = _metadata_int(connection, "paper_event_offset", 0)
            expected_anchor = _metadata_text(
                connection,
                "paper_event_anchor_sha256",
            ) or ""
            if (
                offset > size
                or (
                    offset > 0
                    and _paper_event_anchor(intelligence_event_path, offset)
                    != expected_anchor
                )
            ):
                offset = 0

            last_complete = offset
            with intelligence_event_path.open("rb") as handle:
                handle.seek(offset)
                while True:
                    raw = handle.readline()
                    if not raw:
                        break
                    end = handle.tell()
                    if not raw.endswith(b"\n"):
                        break
                    last_complete = end
                    try:
                        event = json.loads(
                            raw.decode("utf-8", errors="replace")
                        )
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    event_type = str(
                        event.get("event_type") or ""
                    ).upper()
                    signal_id = str(event.get("signal_id") or "")
                    if (
                        event_type not in {
                            "PAPER_ADMISSION",
                            "PAPER_OUTCOME",
                        }
                        or not signal_id
                    ):
                        continue
                    event_key = _paper_event_key(event)
                    if event_key is None:
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO paper_event_pending(
                            event_key, signal_id, event_type,
                            observed_at, row_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            event_key,
                            signal_id,
                            event_type,
                            str(event.get("observed_at") or ""),
                            json.dumps(
                                event,
                                sort_keys=True,
                                allow_nan=False,
                            ),
                        ),
                    )

            _set_metadata(
                connection,
                "paper_event_offset",
                last_complete,
            )
            _set_metadata(
                connection,
                "paper_event_anchor_sha256",
                _paper_event_anchor(
                    intelligence_event_path,
                    last_complete,
                ),
            )

        joined = connection.execute(
            """
            SELECT p.event_key, p.row_json, s.accountability_id,
                   l.row_json
            FROM paper_event_pending p
            JOIN signal_index s ON s.signal_id = p.signal_id
            JOIN latest l ON l.accountability_id = s.accountability_id
            ORDER BY coalesce(p.observed_at, ''), p.event_key
            """
        ).fetchall()
        for event_key, event_raw, accountability_id, latest_raw in joined:
            base = pending_updates.get(str(accountability_id))
            if base is None:
                try:
                    parsed = json.loads(latest_raw)
                except (TypeError, json.JSONDecodeError):
                    continue
                if not isinstance(parsed, dict):
                    continue
                base = parsed
                pending_updates[str(accountability_id)] = base
            try:
                event = json.loads(event_raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            _apply_paper_event(base, event)
            processed_keys.append(str(event_key))
        connection.commit()
    finally:
        connection.close()

    if pending_updates:
        append_accountability_rows(
            pending_updates.values(),
            path=ledger_path,
            state_path=state_db,
        )

    if processed_keys:
        connection = _open_state(state_db)
        try:
            _reconcile_state(connection, ledger_path)
            connection.executemany(
                "DELETE FROM paper_event_pending WHERE event_key = ?",
                [(key,) for key in processed_keys],
            )
            connection.commit()
        finally:
            connection.close()
    return len(processed_keys)


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 4)


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
    validated = len(captured_winners) + len(executable_misses)
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
        "opportunity_capture_rate_pct": _pct(len(captured_winners), validated),
        "estimated_missed_move_pct_sum": round(
            sum(float(row.get("estimated_missed_move_pct") or 0.0) for row in executable_misses),
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
        "guardrails": {
            "production_threshold_changed": False,
            "margin_policy_changed": False,
            "ranking_changed": False,
            "alert_authority_changed": False,
            "trade_authority_changed": False,
        },
    }


def build_accountability_summary_from_state(
    *,
    ledger_path: Path = DEFAULT_LEDGER_FILE,
    state_path: Path | None = None,
) -> dict[str, Any]:
    connection = _open_state(state_path or _state_path_for(ledger_path))
    try:
        _reconcile_state(connection, ledger_path)
        aggregate = connection.execute(
            """
            SELECT count(*), coalesce(sum(outcome_complete), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1 AND market_winner = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1 AND captured_winner = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1
                             AND executable_false_negative = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1 AND threshold_miss = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1 AND cap_miss = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1 AND operational_miss = 1
                            THEN 1 ELSE 0 END
                   ), 0),
                   coalesce(sum(
                       CASE WHEN outcome_complete = 1
                             AND executable_false_negative = 1
                            THEN estimated_missed_move ELSE 0.0 END
                   ), 0.0),
                   avg(decision_latency_ms), count(decision_latency_ms),
                   coalesce(sum(threshold_shadow), 0),
                   coalesce(sum(expanded_cap_shadow), 0),
                   count(paper_net_pnl),
                   coalesce(sum(CASE WHEN paper_net_pnl > 0 THEN 1 ELSE 0 END), 0),
                   sum(paper_net_pnl),
                   count(range_consumed_proxy),
                   coalesce(sum(
                       CASE
                           WHEN outcome_complete = 1
                            AND market_winner = 1
                            AND range_consumed_proxy < 50.0 THEN 1
                           ELSE 0
                       END
                   ), 0),
                   coalesce(sum(
                       CASE
                           WHEN outcome_complete = 1
                            AND market_winner = 1
                            AND range_consumed_proxy >= 50.0 THEN 1
                           ELSE 0
                       END
                   ), 0)
            FROM latest
            """
        ).fetchone()
        by_classification = {
            str(name): int(count)
            for name, count in connection.execute(
                "SELECT classification, count(*) FROM latest GROUP BY classification"
            ).fetchall()
        }
        by_gate = {
            str(name or "NO_TERMINAL_GATE"): int(count)
            for name, count in connection.execute(
                """
                SELECT terminal_gate, count(*) FROM latest
                WHERE outcome_complete = 1 AND market_winner = 1
                GROUP BY terminal_gate
                """
            ).fetchall()
        }
    finally:
        connection.close()

    directional = int(aggregate[0])
    completed = int(aggregate[1])
    winners = int(aggregate[2])
    captured = int(aggregate[3])
    misses = int(aggregate[4])
    threshold_misses = int(aggregate[5])
    cap_misses = int(aggregate[6])
    operational = int(aggregate[7])
    paper_count = int(aggregate[13])
    paper_wins = int(aggregate[14])
    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "population": {
            "directional_evaluations": directional,
            "completed_forward_outcomes": completed,
            "market_winner_candidates": winners,
            "captured_winners": captured,
            "executable_false_negatives": misses,
            "threshold_70_79_miss_candidates": threshold_misses,
            "ranking_or_cap_miss_candidates": cap_misses,
            "operational_executable_misses": operational,
        },
        "opportunity_capture_rate_pct": _pct(captured, captured + misses),
        "estimated_missed_move_pct_sum": round(float(aggregate[8] or 0.0), 6),
        "by_classification": dict(sorted(by_classification.items())),
        "missed_winners_by_terminal_gate": dict(
            sorted(by_gate.items(), key=lambda item: (-item[1], item[0]))
        ),
        "latency": {
            "samples": int(aggregate[10]),
            "mean_decision_latency_ms": (
                round(float(aggregate[9]), 3) if aggregate[9] is not None else None
            ),
        },
        "paper": {
            "outcomes": paper_count,
            "wins": paper_wins,
            "win_rate_pct": _pct(paper_wins, paper_count),
            "net_pnl": round(float(aggregate[15]), 8)
            if aggregate[15] is not None
            else None,
        },
        "counterfactual_experiments": {
            "threshold_70_79": {
                "population": int(aggregate[11]),
                "winner_candidates": threshold_misses,
                "production_changed": False,
            },
            "expanded_cap": {
                "population": int(aggregate[12]),
                "winner_candidates": cap_misses,
                "production_changed": False,
            },
            "decay_aware": {
                "status": (
                    "SHADOW_MEASURED"
                    if int(aggregate[16]) > 0
                    else "AWAITING_POINT_IN_TIME_RANGE_EVIDENCE"
                ),
                "metric": "24h range consumed proxy at screening time",
                "samples": int(aggregate[16]),
                "winner_candidates_before_50pct_range_consumed": int(
                    aggregate[17]
                ),
                "winner_candidates_at_or_after_50pct_range_consumed": int(
                    aggregate[18]
                ),
                "authoritative_move_completed_fraction": False,
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


def _iter_jsonl_sources(
    path: Path,
    *,
    archive_dir: Path | None = None,
) -> Iterable[dict[str, Any]]:
    sources: list[Path] = []
    if archive_dir is not None and archive_dir.exists():
        sources.extend(
            item
            for pattern in ("*.jsonl", "*.jsonl.gz")
            for item in sorted(archive_dir.rglob(pattern))
            if item.is_file()
        )
    sources.append(path)
    for source in sources:
        if not source.exists():
            continue
        try:
            if source.suffix == ".gz":
                handle_context = gzip.open(
                    source,
                    "rt",
                    encoding="utf-8",
                    errors="replace",
                )
            else:
                handle_context = source.open(
                    "r",
                    encoding="utf-8",
                    errors="replace",
                )
            with handle_context as handle:
                for raw in handle:
                    text = raw.strip()
                    if not text:
                        continue
                    try:
                        row = json.loads(text)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        yield row
        except (OSError, EOFError, zlib.error):
            continue


def build_incremental_from_outcomes(
    outcome_rows: Iterable[Mapping[str, Any]],
    *,
    screening_path: Path = DEFAULT_SCREENING_FILE,
    screening_archive: Path = DEFAULT_SCREENING_ARCHIVE,
    funnel_path: Path = DEFAULT_FUNNEL_FILE,
    funnel_archive: Path = DEFAULT_FUNNEL_ARCHIVE,
    intelligence_event_path: Path = DEFAULT_INTELLIGENCE_EVENT_FILE,
    ledger_path: Path = DEFAULT_LEDGER_FILE,
    summary_path: Path = DEFAULT_SUMMARY_FILE,
    state_path: Path | None = None,
    policy: AccountabilityPolicy | None = None,
) -> dict[str, Any]:
    """Join only the outcome rows matured by the current bounded learning cycle."""
    outcomes = [dict(row) for row in outcome_rows if isinstance(row, Mapping)]
    target_keys = {
        (_iso(row.get("reference_at")), _normalize_symbol(row.get("symbol")))
        for row in outcomes
        if _iso(row.get("reference_at")) and _normalize_symbol(row.get("symbol"))
    }
    if not target_keys:
        reconcile_paper_events(
            intelligence_event_path=intelligence_event_path,
            ledger_path=ledger_path,
            state_path=state_path,
        )
        summary = build_accountability_summary_from_state(
            ledger_path=ledger_path,
            state_path=state_path,
        )
        write_summary(summary, path=summary_path)
        return summary

    screening: list[dict[str, Any]] = []
    for row in _iter_jsonl_sources(
        screening_path,
        archive_dir=screening_archive,
    ):
        if str(row.get("scanner_type") or "").upper() != "BROAD_SEARCH":
            continue
        key = (_iso(row.get("observed_at")), _screening_symbol(row))
        if key in target_keys:
            screening.append(row)

    selected_scan_symbols = {
        (str(row.get("scan_id") or ""), _screening_symbol(row))
        for row in screening
        if str(row.get("scan_id") or "")
    }
    funnel: list[dict[str, Any]] = []
    for row in _iter_jsonl_sources(
        funnel_path,
        archive_dir=funnel_archive,
    ):
        key = (
            str(row.get("scan_id") or ""),
            _normalize_symbol(row.get("pair") or row.get("symbol") or row.get("asset")),
        )
        if key in selected_scan_symbols:
            funnel.append(row)

    signal_ids = {
        str(row.get("signal_id") or "")
        for row in funnel
        if str(row.get("signal_id") or "")
    }
    intelligence: list[dict[str, Any]] = []
    if signal_ids and intelligence_event_path.exists():
        for row in _iter_jsonl_sources(intelligence_event_path):
            if (
                str(row.get("signal_id") or "") in signal_ids
                and str(row.get("event_type") or "").upper()
                in {"PAPER_ADMISSION", "PAPER_OUTCOME"}
            ):
                intelligence.append(row)

    synthetic_snapshots = [
        {
            "decision_at_utc": row.get("reference_at"),
            "symbol": row.get("symbol"),
            "snapshot_id": row.get("snapshot_id"),
            "episode_id": row.get("canonical_episode_id") or row.get("episode_id"),
            "reference_price": row.get("reference_price"),
        }
        for row in outcomes
    ]
    accountability_rows = build_accountability_rows(
        screening_rows=screening,
        funnel_rows=funnel,
        snapshot_rows=synthetic_snapshots,
        outcome_rows=outcomes,
        intelligence_events=intelligence,
        policy=policy,
    )
    append_accountability_rows(
        accountability_rows,
        path=ledger_path,
        state_path=state_path,
    )
    reconcile_paper_events(
        intelligence_event_path=intelligence_event_path,
        ledger_path=ledger_path,
        state_path=state_path,
    )
    summary = build_accountability_summary_from_state(
        ledger_path=ledger_path,
        state_path=state_path,
    )
    write_summary(summary, path=summary_path)
    return summary


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
    append_accountability_rows(rows, path=ledger_path)
    summary = build_accountability_summary_from_state(ledger_path=ledger_path)
    write_summary(summary, path=summary_path)
    return summary
