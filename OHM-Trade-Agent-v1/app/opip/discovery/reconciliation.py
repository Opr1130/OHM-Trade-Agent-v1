"""EF-01 evidence-plane reconciliation.

MEASUREMENT ONLY — NO PRODUCTION DECISION AUTHORITY.

Joins canonical Stage-0 screening to V2-01 discovery, Opportunity
Accountability, and Phase 3C. Memory use is bounded: JSONL is streamed
line-by-line into a disk-backed latest-revision SQLite index. This module
does not change ranking, alerts, paper admission, or trading authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from statistics import median
import tempfile
import time
from typing import Any, Iterator, Mapping

from app.opip.decision.store import (
    funnel_events_archive,
    screening_evaluations_archive,
)
from app.opip.discovery.admission import observation_join_id
from app.scanner.candidates import MIN_TECHNICAL_SCORE
from app.services.opportunity_accountability import (
    ACCOUNTABILITY_THRESHOLD_DRIFT,
    ACCOUNTABILITY_WINNER_DEFINITION,
    AccountabilityPolicy,
    _iso,
    _normalize_symbol,
    _observation_id_from_screening,
    _preferred_production_direction,
    _screening_symbol,
    _screening_venue_instrument_id,
    stage0_fields_from_screening,
)


DEFAULT_DATA_ROOT = Path("/app/data")
DISCOVERY_WINNER_DEFINITION = "DISCOVERY_OUTCOME_V1"

# Bounded latest-revision cardinality per evidence plane. Unique keys, not
# physical rows. Exceeding the cap is fail-closed and reported.
MAX_INDEX_ROWS_PER_PLANE = 250_000
MAX_MISMATCH_SAMPLE = 50
SQLITE_CACHE_PAGES = -8000  # 8 MiB page cache
BATCH_COMMIT_ROWS = 500

COMPAT_EXACT_MATCH = "EXACT_MATCH"
COMPAT_SEMANTIC = "SEMANTICALLY_COMPATIBLE"
COMPAT_DIFFERENT_SCOPE = "DIFFERENT_SCOPE_EXPECTED"
COMPAT_MISMATCH = "DATA_MISMATCH"
COMPAT_UNMAPPABLE = "UNMAPPABLE"

STAGE0_ADMISSION_VALUES = (
    "ADMITTED",
    "RANKED_OUTSIDE_BUDGET",
    "BELOW_THRESHOLD",
    "DATA_UNAVAILABLE",
    "EXCLUDED_MARKET",
)


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


def _boolish(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in {1, "1", "true", "True", "TRUE"}:
        return True
    return False


def iter_jsonl_dicts(
    path: Path,
    *,
    stats: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream JSONL without materializing the file as a string or line list.

    A truncated final line (no trailing newline) is skipped fail-closed.
    Malformed complete lines are skipped so one bad row cannot hide the rest.
    """
    if not path.exists() or not path.is_file():
        return
    if path.suffix == ".gz" or path.name.endswith(".jsonl.gz"):
        handle_cm = gzip.open(path, "rt", encoding="utf-8", errors="replace")
    else:
        handle_cm = path.open("r", encoding="utf-8", errors="replace")
    with handle_cm as handle:
        for raw in handle:
            if not raw.endswith("\n"):
                if stats is not None:
                    stats["truncated_tail_skipped"] = (
                        int(stats.get("truncated_tail_skipped") or 0) + 1
                    )
                break
            text = raw.strip()
            if not text:
                continue
            if stats is not None:
                stats["physical_rows"] = int(stats.get("physical_rows") or 0) + 1
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                if stats is not None:
                    stats["malformed_rows"] = int(stats.get("malformed_rows") or 0) + 1
                continue
            if isinstance(value, dict):
                yield value


def _iter_checksummed_archive_jsonl(
    archive_dir: Path,
    *,
    stats: dict[str, int] | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream archive segments one file at a time. Never retain all rows."""
    if not archive_dir.exists():
        return
    seen_checksums: set[str] = set()
    for archive in sorted(archive_dir.rglob("*.jsonl.gz")):
        if not archive.is_file():
            continue
        checksum_path = Path(str(archive) + ".sha256")
        if checksum_path.exists():
            try:
                expected = checksum_path.read_text(encoding="utf-8").split()[0]
            except OSError:
                continue
            digest = hashlib.sha256()
            try:
                with archive.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                continue
            if digest.hexdigest() != expected:
                if stats is not None:
                    stats["archive_checksum_skipped"] = (
                        int(stats.get("archive_checksum_skipped") or 0) + 1
                    )
                continue
            if expected in seen_checksums:
                continue
            seen_checksums.add(expected)
        yield from iter_jsonl_dicts(archive, stats=stats)
    for archive in sorted(archive_dir.rglob("*.jsonl")):
        if archive.is_file() and archive.suffix == ".jsonl":
            yield from iter_jsonl_dicts(archive, stats=stats)


def reconstructed_join_key(
    *,
    scan_id: Any,
    observed_at: Any,
    symbol: Any,
) -> str | None:
    scan = _optional_text(scan_id)
    observed = _iso(observed_at) or _optional_text(observed_at)
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


def join_key_from_screening(row: Mapping[str, Any]) -> str | None:
    return reconstructed_join_key(
        scan_id=row.get("scan_id"),
        observed_at=row.get("observed_at"),
        symbol=_screening_symbol(row) or _screening_venue_instrument_id(row),
    )


def latest_accountability_by_id(
    rows: list[Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    revisions: dict[str, int] = {}
    for row in rows:
        key = str(row.get("accountability_id") or "")
        if not key:
            continue
        try:
            revision = int(row.get("revision") or 0)
        except (TypeError, ValueError):
            revision = 0
        if key not in revisions or revision >= revisions[key]:
            latest[key] = dict(row)
            revisions[key] = revision
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


def _peak_rss_kb() -> int | None:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, AttributeError):
        return None
    # Linux reports KiB; macOS reports bytes.
    if usage > 10_000_000:
        return int(usage / 1024)
    return int(usage)


def _open_index(path: Path | None = None) -> sqlite3.Connection:
    connection = sqlite3.connect(str(path) if path is not None else ":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA cache_size = {SQLITE_CACHE_PAGES}")
    connection.execute("PRAGMA temp_store = FILE")
    connection.execute("PRAGMA journal_mode = OFF")
    connection.execute("PRAGMA synchronous = OFF")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS stage0 (
            observation_id TEXT PRIMARY KEY,
            join_key TEXT,
            scan_id TEXT,
            instrument TEXT,
            production_admission_result TEXT,
            shortlist_selected INTEGER,
            shortlist_admitted INTEGER,
            production_preferred_direction TEXT,
            production_exclusion_reason TEXT,
            threshold_passed INTEGER,
            long_score REAL,
            short_score REAL,
            technical_score REAL,
            screening_outcome TEXT,
            revision INTEGER
        );
        CREATE INDEX IF NOT EXISTS stage0_join ON stage0(join_key);

        CREATE TABLE IF NOT EXISTS discovery (
            observation_id TEXT PRIMARY KEY,
            join_key TEXT,
            scan_id TEXT,
            instrument TEXT,
            observed_at TEXT,
            production_preferred_direction TEXT,
            realized_opportunity_direction TEXT,
            market_opportunity TEXT,
            window_complete INTEGER,
            long_mfe_pct REAL,
            short_mfe_pct REAL,
            long_mae_pct REAL,
            short_mae_pct REAL,
            revision INTEGER
        );
        CREATE INDEX IF NOT EXISTS discovery_join ON discovery(join_key);

        CREATE TABLE IF NOT EXISTS oa (
            accountability_id TEXT PRIMARY KEY,
            observation_id TEXT,
            join_key TEXT,
            scan_id TEXT,
            symbol TEXT,
            direction TEXT,
            classification TEXT,
            market_winner INTEGER,
            outcome_complete INTEGER,
            outcome_available INTEGER,
            funnel_evidence_present INTEGER,
            shortlist_admitted INTEGER,
            production_admission_result TEXT,
            production_preferred_direction TEXT,
            snapshot_id TEXT,
            technical_score REAL,
            revision INTEGER
        );
        CREATE INDEX IF NOT EXISTS oa_obs ON oa(observation_id);
        CREATE INDEX IF NOT EXISTS oa_join ON oa(join_key);

        CREATE TABLE IF NOT EXISTS phase3c (
            snapshot_id TEXT PRIMARY KEY,
            mfe_pct REAL,
            mae_pct REAL,
            window_complete INTEGER,
            revision INTEGER
        );

        CREATE TABLE IF NOT EXISTS funnel (
            scan_id TEXT,
            symbol TEXT,
            direction TEXT,
            PRIMARY KEY (scan_id, symbol, direction)
        );

        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value INTEGER
        );
        """
    )
    return connection


def _meta_inc(connection: sqlite3.Connection, key: str, amount: int = 1) -> None:
    connection.execute(
        """
        INSERT INTO meta(key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = value + excluded.value
        """,
        (key, amount),
    )


def _meta_get(connection: sqlite3.Connection, key: str) -> int:
    row = connection.execute(
        "SELECT value FROM meta WHERE key = ?", (key,)
    ).fetchone()
    return int(row[0]) if row is not None else 0


_INDEX_TABLES = {
    "stage0": "SELECT COUNT(*) FROM stage0",
    "discovery": "SELECT COUNT(*) FROM discovery",
    "oa": "SELECT COUNT(*) FROM oa",
    "phase3c": "SELECT COUNT(*) FROM phase3c",
    "funnel": "SELECT COUNT(*) FROM funnel",
}


def _table_count(connection: sqlite3.Connection, table: str) -> int:
    query = _INDEX_TABLES.get(table)
    if query is None:
        raise ValueError(f"unknown reconciliation index table: {table}")
    return int(connection.execute(query).fetchone()[0])


def _enforce_cap(connection: sqlite3.Connection, table: str, plane: str) -> bool:
    if _table_count(connection, table) >= MAX_INDEX_ROWS_PER_PLANE:
        _meta_inc(connection, f"cap_exceeded_{plane}")
        return False
    return True


def _upsert_stage0(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    scanner = str(row.get("scanner_type") or "").upper()
    if scanner and scanner != "BROAD_SEARCH":
        return
    observation_id = _observation_id_from_screening(row) or ""
    join_key = join_key_from_screening(row)
    if not observation_id:
        if not join_key:
            return
        observation_id = f"RECON:{join_key}"
    stage0 = stage0_fields_from_screening(row)
    try:
        revision = int(row.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    current = connection.execute(
        "SELECT revision FROM stage0 WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if current is not None and int(current[0] or 0) > revision:
        return
    if current is None and not _enforce_cap(connection, "stage0", "stage0"):
        return
    connection.execute(
        """
        INSERT INTO stage0(
            observation_id, join_key, scan_id, instrument,
            production_admission_result, shortlist_selected, shortlist_admitted,
            production_preferred_direction, production_exclusion_reason,
            threshold_passed, long_score, short_score, technical_score,
            screening_outcome, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO UPDATE SET
            join_key = excluded.join_key,
            scan_id = excluded.scan_id,
            instrument = excluded.instrument,
            production_admission_result = excluded.production_admission_result,
            shortlist_selected = excluded.shortlist_selected,
            shortlist_admitted = excluded.shortlist_admitted,
            production_preferred_direction = excluded.production_preferred_direction,
            production_exclusion_reason = excluded.production_exclusion_reason,
            threshold_passed = excluded.threshold_passed,
            long_score = excluded.long_score,
            short_score = excluded.short_score,
            technical_score = excluded.technical_score,
            screening_outcome = excluded.screening_outcome,
            revision = excluded.revision
        """,
        (
            observation_id,
            join_key,
            _optional_text(row.get("scan_id")) or None,
            _screening_symbol(row) or _screening_venue_instrument_id(row) or None,
            stage0["production_admission_result"],
            int(bool(stage0["shortlist_selected"])),
            int(bool(stage0["shortlist_admitted"])),
            stage0["production_preferred_direction"],
            stage0["production_exclusion_reason"],
            int(bool(stage0["threshold_passed"])),
            stage0["long_score"],
            stage0["short_score"],
            stage0["technical_score"],
            stage0["screening_outcome"],
            revision,
        ),
    )


def _upsert_discovery(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    observation_id = _optional_text(row.get("observation_id"))
    join_key = join_key_from_discovery(row)
    if not observation_id:
        if not join_key:
            return
        observation_id = f"RECON:{join_key}"
    try:
        revision = int(row.get("outcome_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    current = connection.execute(
        "SELECT revision FROM discovery WHERE observation_id = ?",
        (observation_id,),
    ).fetchone()
    if current is not None and int(current[0] or 0) > revision:
        return
    if current is None and not _enforce_cap(connection, "discovery", "discovery"):
        return
    primary = (row.get("horizons") or {}).get("12h") or {}
    if not isinstance(primary, Mapping):
        primary = {}
    preferred = _optional_text(
        row.get("production_preferred_direction")
        or _preferred_production_direction(row)
    ).upper() or None
    connection.execute(
        """
        INSERT INTO discovery(
            observation_id, join_key, scan_id, instrument, observed_at,
            production_preferred_direction, realized_opportunity_direction,
            market_opportunity, window_complete, long_mfe_pct, short_mfe_pct,
            long_mae_pct, short_mae_pct, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(observation_id) DO UPDATE SET
            join_key = excluded.join_key,
            scan_id = excluded.scan_id,
            instrument = excluded.instrument,
            observed_at = excluded.observed_at,
            production_preferred_direction = excluded.production_preferred_direction,
            realized_opportunity_direction = excluded.realized_opportunity_direction,
            market_opportunity = excluded.market_opportunity,
            window_complete = excluded.window_complete,
            long_mfe_pct = excluded.long_mfe_pct,
            short_mfe_pct = excluded.short_mfe_pct,
            long_mae_pct = excluded.long_mae_pct,
            short_mae_pct = excluded.short_mae_pct,
            revision = excluded.revision
        """,
        (
            observation_id,
            join_key,
            _optional_text(row.get("scan_id")) or None,
            _optional_text(
                row.get("venue_instrument_id") or _screening_venue_instrument_id(row)
            )
            or None,
            _optional_text(row.get("observed_at")) or None,
            preferred,
            _optional_text(row.get("realized_opportunity_direction")).upper() or None,
            _optional_text(row.get("market_discovery_opportunity_v1")) or None,
            int(_discovery_complete(row)),
            _finite(primary.get("long_mfe_pct")),
            _finite(primary.get("short_mfe_pct")),
            _finite(primary.get("long_mae_pct")),
            _finite(primary.get("short_mae_pct")),
            revision,
        ),
    )


def _upsert_oa(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    identity = _optional_text(row.get("accountability_id"))
    if not identity:
        return
    try:
        revision = int(row.get("revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    current = connection.execute(
        "SELECT revision FROM oa WHERE accountability_id = ?",
        (identity,),
    ).fetchone()
    if current is not None and int(current[0] or 0) > revision:
        return
    if current is None and not _enforce_cap(connection, "oa", "oa"):
        return
    funnel_present = row.get("funnel_evidence_present")
    if funnel_present is None:
        funnel_present = row.get("production_selected")
    connection.execute(
        """
        INSERT INTO oa(
            accountability_id, observation_id, join_key, scan_id, symbol,
            direction, classification, market_winner, outcome_complete,
            outcome_available, funnel_evidence_present, shortlist_admitted,
            production_admission_result, production_preferred_direction,
            snapshot_id, technical_score, revision
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(accountability_id) DO UPDATE SET
            observation_id = excluded.observation_id,
            join_key = excluded.join_key,
            scan_id = excluded.scan_id,
            symbol = excluded.symbol,
            direction = excluded.direction,
            classification = excluded.classification,
            market_winner = excluded.market_winner,
            outcome_complete = excluded.outcome_complete,
            outcome_available = excluded.outcome_available,
            funnel_evidence_present = excluded.funnel_evidence_present,
            shortlist_admitted = excluded.shortlist_admitted,
            production_admission_result = excluded.production_admission_result,
            production_preferred_direction = excluded.production_preferred_direction,
            snapshot_id = excluded.snapshot_id,
            technical_score = excluded.technical_score,
            revision = excluded.revision
        """,
        (
            identity,
            _optional_text(row.get("observation_id")) or None,
            join_key_from_accountability(row),
            _optional_text(row.get("scan_id")) or None,
            _normalize_symbol(row.get("symbol")) or None,
            _optional_text(row.get("direction")).upper() or None,
            _optional_text(row.get("opportunity_classification")) or None,
            int(_boolish(row.get("market_winner"))),
            int(_boolish(row.get("outcome_complete"))),
            int(
                True
                if row.get("outcome_available") is None
                else _boolish(row.get("outcome_available"))
            ),
            int(_boolish(funnel_present)),
            int(_boolish(row.get("shortlist_admitted"))),
            _optional_text(row.get("production_admission_result")) or None,
            _optional_text(row.get("production_preferred_direction")).upper() or None,
            _optional_text(row.get("snapshot_id")) or None,
            _finite(row.get("technical_score")),
            revision,
        ),
    )


def _upsert_phase3c(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    snapshot_id = _optional_text(row.get("snapshot_id"))
    if not snapshot_id:
        return
    try:
        revision = int(row.get("outcome_revision") or 0)
    except (TypeError, ValueError):
        revision = 0
    current = connection.execute(
        "SELECT revision FROM phase3c WHERE snapshot_id = ?",
        (snapshot_id,),
    ).fetchone()
    if current is not None and int(current[0] or 0) > revision:
        return
    if current is None and not _enforce_cap(connection, "phase3c", "phase3c"):
        return
    connection.execute(
        """
        INSERT INTO phase3c(snapshot_id, mfe_pct, mae_pct, window_complete, revision)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            mfe_pct = excluded.mfe_pct,
            mae_pct = excluded.mae_pct,
            window_complete = excluded.window_complete,
            revision = excluded.revision
        """,
        (
            snapshot_id,
            _finite(row.get("mfe_pct")),
            _finite(row.get("mae_pct")),
            int(_boolish(row.get("window_complete"))),
            revision,
        ),
    )


def _upsert_funnel(connection: sqlite3.Connection, row: Mapping[str, Any]) -> None:
    scan_id = _optional_text(row.get("scan_id"))
    symbol = _normalize_symbol(row.get("pair") or row.get("symbol") or row.get("asset"))
    direction = _optional_text(row.get("direction")).upper()
    if not scan_id or not symbol or direction not in {"LONG", "SHORT"}:
        return
    if not _enforce_cap(connection, "funnel", "funnel"):
        existing = connection.execute(
            "SELECT 1 FROM funnel WHERE scan_id = ? AND symbol = ? AND direction = ?",
            (scan_id, symbol, direction),
        ).fetchone()
        if existing is None:
            return
    connection.execute(
        """
        INSERT INTO funnel(scan_id, symbol, direction) VALUES (?, ?, ?)
        ON CONFLICT(scan_id, symbol, direction) DO NOTHING
        """,
        (scan_id, symbol, direction),
    )


def _ingest_stream(
    connection: sqlite3.Connection,
    rows: Iterator[Mapping[str, Any]],
    upsert,
    *,
    plane: str,
) -> int:
    ingested = 0
    batch = 0
    for row in rows:
        if _meta_get(connection, f"cap_exceeded_{plane}"):
            break
        upsert(connection, row)
        ingested += 1
        batch += 1
        if batch >= BATCH_COMMIT_ROWS:
            connection.commit()
            _meta_inc(connection, "batches_processed")
            batch = 0
    if batch:
        connection.commit()
        _meta_inc(connection, "batches_processed")
    _meta_inc(connection, f"ingested_{plane}", ingested)
    return ingested


def _file_bytes(path: Path) -> int:
    try:
        return int(path.stat().st_size) if path.exists() else 0
    except OSError:
        return 0


def _compat_verdict(
    *,
    via: str | None,
    stage0_present: bool,
    winner_compared: bool,
    winner_agree: bool,
    complete_agree: bool,
    classification_mismatch: bool,
) -> str:
    if not via:
        return COMPAT_UNMAPPABLE
    if classification_mismatch:
        return COMPAT_MISMATCH
    if winner_compared and not winner_agree:
        return COMPAT_DIFFERENT_SCOPE
    if not complete_agree:
        return COMPAT_DIFFERENT_SCOPE
    if via == "observation_id" and stage0_present and winner_agree:
        return COMPAT_EXACT_MATCH
    if via in {"observation_id", "reconstructed"}:
        return COMPAT_SEMANTIC
    return COMPAT_UNMAPPABLE


def _expected_oa_classification(
    *,
    admission: str,
    preferred: str | None,
    oa_direction: str | None,
    funnel_present: bool,
    market_winner: bool,
    outcome_complete: bool,
    outcome_available: bool,
    technical_score: float | None,
    screening_outcome: str,
) -> str | None:
    if not outcome_available or not outcome_complete:
        return "PENDING_OUTCOME"
    if not market_winner:
        return None
    if (
        admission == "RANKED_OUTSIDE_BUDGET"
        and preferred
        and oa_direction == preferred
        and not funnel_present
    ):
        return "RANKING_OR_CAP_MISS_CANDIDATE"
    if admission == "ADMITTED" and not funnel_present:
        return "MARKET_WINNER_UNVERIFIED_EXECUTABILITY"
    if (
        technical_score is not None
        and 70.0 <= technical_score < float(MIN_TECHNICAL_SCORE)
    ):
        return "THRESHOLD_70_79_MISS_CANDIDATE"
    if admission == "BELOW_THRESHOLD" or screening_outcome == "BELOW_THRESHOLD":
        return "BELOW_THRESHOLD_MARKET_WINNER"
    return None


def _assemble_report(
    connection: sqlite3.Connection,
    *,
    physical: Mapping[str, int],
    ingest_stats: Mapping[str, Mapping[str, int]],
    input_file_bytes: Mapping[str, int],
    elapsed_seconds: float,
) -> dict[str, Any]:
    stage0_counts = {
        value: int(
            connection.execute(
                "SELECT COUNT(*) FROM stage0 WHERE production_admission_result = ?",
                (value,),
            ).fetchone()[0]
        )
        for value in STAGE0_ADMISSION_VALUES
    }
    stage0_other = int(
        connection.execute(
            """
            SELECT COUNT(*) FROM stage0
            WHERE production_admission_result NOT IN (?, ?, ?, ?, ?)
            """,
            STAGE0_ADMISSION_VALUES,
        ).fetchone()[0]
    )

    mapping = {
        "ranked_out_preferred_winner_mapped_to_rank_cap_miss": 0,
        "ranked_out_preferred_winner_mapped_elsewhere": 0,
        "admitted_missing_funnel": 0,
        "admitted_incorrectly_mapped_to_rank_cap_miss": 0,
        "threshold_70_79_winner_mappings": 0,
        "below_70_market_winner_mappings": 0,
        "unmatched_discovery": 0,
        "unmatched_oa": 0,
        "unmatched_stage0": 0,
        "shared_observation_id_joins": 0,
        "fallback_reconstructed_joins": 0,
        "classification_mismatches": 0,
    }
    verdict_counts = {
        COMPAT_EXACT_MATCH: 0,
        COMPAT_SEMANTIC: 0,
        COMPAT_DIFFERENT_SCOPE: 0,
        COMPAT_MISMATCH: 0,
        COMPAT_UNMAPPABLE: 0,
    }
    mismatches: list[dict[str, Any]] = []
    overlap_sample: list[dict[str, Any]] = []
    mfe_abs: list[float] = []
    mae_abs: list[float] = []
    direction_agree = 0
    direction_compared = 0
    winner_agree = 0
    winner_compared = 0
    complete_agree = 0
    complete_compared = 0
    identity_shared = 0
    matched_obs = 0
    matched_recon = 0
    matched_total = 0

    def _note_mismatch(payload: dict[str, Any]) -> None:
        mapping["classification_mismatches"] += 1
        if len(mismatches) < MAX_MISMATCH_SAMPLE:
            mismatches.append(payload)

    seen_discovery: set[str] = set()
    seen_oa: set[str] = set()

    for stage0 in connection.execute("SELECT * FROM stage0"):
        observation_id = str(stage0["observation_id"] or "")
        join_key = str(stage0["join_key"] or "")
        discovery = connection.execute(
            "SELECT * FROM discovery WHERE observation_id = ?",
            (observation_id,),
        ).fetchone()
        discovery_via_obs = discovery is not None
        if discovery is None and join_key:
            discovery = connection.execute(
                "SELECT * FROM discovery WHERE join_key = ?",
                (join_key,),
            ).fetchone()
        oa_rows = connection.execute(
            "SELECT * FROM oa WHERE observation_id = ?",
            (observation_id,),
        ).fetchall()
        oa_via_obs = bool(oa_rows)
        if not oa_rows and join_key:
            oa_rows = connection.execute(
                "SELECT * FROM oa WHERE join_key = ?",
                (join_key,),
            ).fetchall()
        used_fallback = (discovery is not None and not discovery_via_obs) or (
            bool(oa_rows) and not oa_via_obs
        )
        if discovery is None and not oa_rows:
            via = None
        elif used_fallback:
            via = "reconstructed"
        elif discovery_via_obs or oa_via_obs:
            via = "observation_id"
        else:
            via = "reconstructed"

        if discovery is not None:
            seen_discovery.add(str(discovery["observation_id"]))
        for oa in oa_rows:
            seen_oa.add(str(oa["accountability_id"]))

        preferred = str(
            stage0["production_preferred_direction"]
            or ((discovery["production_preferred_direction"] if discovery else "") or "")
        ).upper() or None
        preferred_oa = _preferred_oa_row([dict(row) for row in oa_rows], preferred)
        admission = str(stage0["production_admission_result"] or "")
        funnel_present = False
        if preferred_oa is not None:
            funnel_present = bool(preferred_oa.get("funnel_evidence_present"))
        if not funnel_present and stage0["scan_id"] and stage0["instrument"] and preferred:
            funnel_row = connection.execute(
                "SELECT 1 FROM funnel WHERE scan_id = ? AND symbol = ? AND direction = ?",
                (
                    stage0["scan_id"],
                    _normalize_symbol(stage0["instrument"]),
                    preferred,
                ),
            ).fetchone()
            funnel_present = funnel_row is not None

        oa_winner = any(bool(row["market_winner"]) for row in oa_rows)
        preferred_winner = bool(preferred_oa and preferred_oa.get("market_winner"))
        classification = (
            str(preferred_oa.get("classification") or "") if preferred_oa else ""
        )
        technical = _finite(stage0["technical_score"])
        if preferred_oa and preferred_oa.get("technical_score") is not None:
            technical = _finite(preferred_oa.get("technical_score"))

        if admission == "ADMITTED" and not funnel_present:
            mapping["admitted_missing_funnel"] += 1
            if classification == "RANKING_OR_CAP_MISS_CANDIDATE":
                mapping["admitted_incorrectly_mapped_to_rank_cap_miss"] += 1
                _note_mismatch(
                    {
                        "reason_code": "ADMITTED_MAPPED_TO_RANK_CAP_MISS",
                        "observation_id": observation_id,
                        "scan_id": stage0["scan_id"],
                        "instrument": stage0["instrument"],
                        "accountability_id": (
                            preferred_oa.get("accountability_id") if preferred_oa else None
                        ),
                        "production_admission_result": admission,
                        "observed_classification": classification,
                        "expected_classification": (
                            "MARKET_WINNER_UNVERIFIED_EXECUTABILITY"
                        ),
                    }
                )

        if (
            admission == "RANKED_OUTSIDE_BUDGET"
            and preferred_winner
            and preferred
            and preferred_oa
            and str(preferred_oa.get("direction") or "") == preferred
            and not funnel_present
        ):
            if classification == "RANKING_OR_CAP_MISS_CANDIDATE":
                mapping["ranked_out_preferred_winner_mapped_to_rank_cap_miss"] += 1
            else:
                mapping["ranked_out_preferred_winner_mapped_elsewhere"] += 1
                _note_mismatch(
                    {
                        "reason_code": "RANKED_OUT_WINNER_NOT_RANK_CAP_MISS",
                        "observation_id": observation_id,
                        "scan_id": stage0["scan_id"],
                        "instrument": stage0["instrument"],
                        "accountability_id": preferred_oa.get("accountability_id"),
                        "production_admission_result": admission,
                        "observed_classification": classification,
                        "expected_classification": "RANKING_OR_CAP_MISS_CANDIDATE",
                    }
                )

        if preferred_winner and technical is not None:
            if 70.0 <= technical < float(MIN_TECHNICAL_SCORE):
                mapping["threshold_70_79_winner_mappings"] += 1
                if classification and classification != "THRESHOLD_70_79_MISS_CANDIDATE":
                    if admission != "RANKED_OUTSIDE_BUDGET":
                        _note_mismatch(
                            {
                                "reason_code": "THRESHOLD_70_79_MAPPING",
                                "observation_id": observation_id,
                                "scan_id": stage0["scan_id"],
                                "instrument": stage0["instrument"],
                                "accountability_id": (
                                    preferred_oa.get("accountability_id")
                                    if preferred_oa
                                    else None
                                ),
                                "technical_score": technical,
                                "observed_classification": classification,
                                "expected_classification": (
                                    "THRESHOLD_70_79_MISS_CANDIDATE"
                                ),
                            }
                        )
            elif technical < 70.0 and admission == "BELOW_THRESHOLD":
                mapping["below_70_market_winner_mappings"] += 1

        expected = _expected_oa_classification(
            admission=admission,
            preferred=preferred,
            oa_direction=(
                str(preferred_oa.get("direction") or "") if preferred_oa else None
            ),
            funnel_present=funnel_present,
            market_winner=preferred_winner,
            outcome_complete=bool(preferred_oa and preferred_oa.get("outcome_complete")),
            outcome_available=bool(
                preferred_oa and preferred_oa.get("outcome_available")
            ),
            technical_score=technical,
            screening_outcome=str(stage0["screening_outcome"] or ""),
        )
        classification_mismatch = bool(
            expected
            and classification
            and expected != classification
            and expected != "PENDING_OUTCOME"
        )
        if (
            expected == "PENDING_OUTCOME"
            and classification
            and classification != "PENDING_OUTCOME"
            and preferred_oa
            and not preferred_oa.get("outcome_complete")
        ):
            classification_mismatch = True
            _note_mismatch(
                {
                    "reason_code": "INCOMPLETE_OUTCOME_NOT_PENDING",
                    "observation_id": observation_id,
                    "scan_id": stage0["scan_id"],
                    "instrument": stage0["instrument"],
                    "accountability_id": preferred_oa.get("accountability_id"),
                    "observed_classification": classification,
                    "expected_classification": "PENDING_OUTCOME",
                }
            )
        elif classification_mismatch and expected:
            _note_mismatch(
                {
                    "reason_code": "CLASSIFICATION_MISMATCH",
                    "observation_id": observation_id,
                    "scan_id": stage0["scan_id"],
                    "instrument": stage0["instrument"],
                    "accountability_id": (
                        preferred_oa.get("accountability_id") if preferred_oa else None
                    ),
                    "observed_classification": classification,
                    "expected_classification": expected,
                }
            )

        if via is None and discovery is None and not oa_rows:
            mapping["unmatched_stage0"] += 1
            verdict_counts[COMPAT_UNMAPPABLE] += 1
            continue

        if via == "observation_id":
            mapping["shared_observation_id_joins"] += 1
            matched_obs += 1
            identity_shared += 1
        elif via == "reconstructed":
            mapping["fallback_reconstructed_joins"] += 1
            matched_recon += 1
        matched_total += 1

        realized = (
            str(discovery["realized_opportunity_direction"] or "").upper() or None
            if discovery is not None
            else None
        )
        if preferred in {"LONG", "SHORT"} and realized in {"LONG", "SHORT"}:
            direction_compared += 1
            if preferred == realized:
                direction_agree += 1
        market = str(discovery["market_opportunity"] or "") if discovery is not None else ""
        if market in {"WINNER", "NON_WINNER"}:
            winner_compared += 1
            if (market == "WINNER") == oa_winner:
                winner_agree += 1
        discovery_complete = bool(discovery["window_complete"]) if discovery is not None else False
        oa_complete = bool(preferred_oa and preferred_oa.get("outcome_complete"))
        complete_compared += 1
        if discovery is None:
            complete_agree += 0 if oa_complete else 1
            complete_local_agree = discovery is None
        else:
            complete_local_agree = discovery_complete == oa_complete
            if complete_local_agree:
                complete_agree += 1

        snapshot_id = str((preferred_oa or {}).get("snapshot_id") or "")
        phase3c = (
            connection.execute(
                "SELECT * FROM phase3c WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if snapshot_id
            else None
        )
        if discovery is not None and preferred == "LONG":
            disc_mfe = _finite(discovery["long_mfe_pct"])
            disc_mae = _finite(discovery["long_mae_pct"])
        elif discovery is not None and preferred == "SHORT":
            disc_mfe = _finite(discovery["short_mfe_pct"])
            disc_mae = _finite(discovery["short_mae_pct"])
        else:
            disc_mfe = None
            disc_mae = None
        p3_mfe = _finite(phase3c["mfe_pct"]) if phase3c is not None else None
        p3_mae = _finite(phase3c["mae_pct"]) if phase3c is not None else None
        if preferred == "SHORT" and p3_mae is not None:
            p3_mfe = abs(p3_mae)
        if disc_mfe is not None and p3_mfe is not None:
            mfe_abs.append(abs(disc_mfe - p3_mfe))
        if disc_mae is not None and p3_mae is not None:
            mae_abs.append(abs(abs(disc_mae) - abs(p3_mae)))

        winner_local_agree = True
        if market in {"WINNER", "NON_WINNER"}:
            winner_local_agree = (market == "WINNER") == oa_winner
        verdict = _compat_verdict(
            via=via,
            stage0_present=True,
            winner_compared=market in {"WINNER", "NON_WINNER"},
            winner_agree=winner_local_agree,
            complete_agree=complete_local_agree if discovery is not None else True,
            classification_mismatch=classification_mismatch,
        )
        verdict_counts[verdict] += 1

        item = {
            "via": via,
            "compatibility": verdict,
            "observation_id": observation_id,
            "scan_id": stage0["scan_id"],
            "instrument": stage0["instrument"],
            "stage0": {
                "observation_id": observation_id,
                "scan_id": stage0["scan_id"],
                "normalized_instrument": stage0["instrument"],
                "production_admission_result": admission,
                "shortlist_selected": bool(stage0["shortlist_selected"]),
                "production_preferred_direction": preferred,
                "production_exclusion_reason": stage0["production_exclusion_reason"],
                "threshold_passed": bool(stage0["threshold_passed"]),
                "technical_score": stage0["technical_score"],
                "long_score": stage0["long_score"],
                "short_score": stage0["short_score"],
            },
            "production_preferred_direction": preferred,
            "realized_opportunity_direction": realized,
            "discovery_market_opportunity": market or None,
            "oa_any_market_winner": oa_winner,
            "oa_directional_classification": classification or None,
            "funnel_evidence_present": funnel_present,
            "outcome_maturity": {
                "discovery_window_complete_12h": discovery_complete,
                "oa_outcome_complete_24h": oa_complete,
                "phase3c_window_complete_24h": (
                    bool(phase3c["window_complete"]) if phase3c is not None else None
                ),
            },
            "winner_definitions": {
                "v2_01": DISCOVERY_WINNER_DEFINITION,
                "accountability": ACCOUNTABILITY_WINNER_DEFINITION,
            },
            "preferred_oa_classification": classification or None,
            "discovery_window_complete": discovery_complete,
            "oa_outcome_complete": oa_complete,
        }
        if len(overlap_sample) < MAX_MISMATCH_SAMPLE:
            overlap_sample.append(item)

    for discovery in connection.execute("SELECT * FROM discovery"):
        observation_id = str(discovery["observation_id"] or "")
        if observation_id in seen_discovery:
            continue
        join_key = str(discovery["join_key"] or "")
        oa_rows = connection.execute(
            "SELECT * FROM oa WHERE observation_id = ?",
            (observation_id,),
        ).fetchall()
        via = "observation_id" if oa_rows else None
        if not oa_rows and join_key:
            oa_rows = connection.execute(
                "SELECT * FROM oa WHERE join_key = ?",
                (join_key,),
            ).fetchall()
            if oa_rows:
                via = "reconstructed"
        if not oa_rows:
            continue
        seen_discovery.add(observation_id)
        for oa in oa_rows:
            seen_oa.add(str(oa["accountability_id"]))
        preferred = str(discovery["production_preferred_direction"] or "").upper() or None
        preferred_oa = _preferred_oa_row([dict(row) for row in oa_rows], preferred)
        oa_winner = any(bool(row["market_winner"]) for row in oa_rows)
        classification = (
            str(preferred_oa.get("classification") or "") if preferred_oa else ""
        )
        realized = str(discovery["realized_opportunity_direction"] or "").upper() or None
        if preferred in {"LONG", "SHORT"} and realized in {"LONG", "SHORT"}:
            direction_compared += 1
            if preferred == realized:
                direction_agree += 1
        market = str(discovery["market_opportunity"] or "")
        winner_local_agree = True
        if market in {"WINNER", "NON_WINNER"}:
            winner_compared += 1
            winner_local_agree = (market == "WINNER") == oa_winner
            if winner_local_agree:
                winner_agree += 1
        discovery_complete = bool(discovery["window_complete"])
        oa_complete = bool(preferred_oa and preferred_oa.get("outcome_complete"))
        complete_compared += 1
        complete_local_agree = discovery_complete == oa_complete
        if complete_local_agree:
            complete_agree += 1
        snapshot_id = str((preferred_oa or {}).get("snapshot_id") or "")
        phase3c = (
            connection.execute(
                "SELECT * FROM phase3c WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()
            if snapshot_id
            else None
        )
        if preferred == "LONG":
            disc_mfe = _finite(discovery["long_mfe_pct"])
            disc_mae = _finite(discovery["long_mae_pct"])
        elif preferred == "SHORT":
            disc_mfe = _finite(discovery["short_mfe_pct"])
            disc_mae = _finite(discovery["short_mae_pct"])
        else:
            disc_mfe = None
            disc_mae = None
        p3_mfe = _finite(phase3c["mfe_pct"]) if phase3c is not None else None
        p3_mae = _finite(phase3c["mae_pct"]) if phase3c is not None else None
        if preferred == "SHORT" and p3_mae is not None:
            p3_mfe = abs(p3_mae)
        if disc_mfe is not None and p3_mfe is not None:
            mfe_abs.append(abs(disc_mfe - p3_mfe))
        if disc_mae is not None and p3_mae is not None:
            mae_abs.append(abs(abs(disc_mae) - abs(p3_mae)))
        if via == "observation_id":
            mapping["shared_observation_id_joins"] += 1
            matched_obs += 1
            identity_shared += 1
        elif via == "reconstructed":
            mapping["fallback_reconstructed_joins"] += 1
            matched_recon += 1
        matched_total += 1
        verdict = _compat_verdict(
            via=via,
            stage0_present=False,
            winner_compared=market in {"WINNER", "NON_WINNER"},
            winner_agree=winner_local_agree,
            complete_agree=complete_local_agree,
            classification_mismatch=False,
        )
        if verdict == COMPAT_EXACT_MATCH:
            verdict = COMPAT_SEMANTIC
        verdict_counts[verdict] += 1
        if len(overlap_sample) < MAX_MISMATCH_SAMPLE:
            overlap_sample.append(
                {
                    "via": via,
                    "compatibility": verdict,
                    "observation_id": observation_id,
                    "scan_id": discovery["scan_id"],
                    "instrument": discovery["instrument"],
                    "stage0": None,
                    "production_preferred_direction": preferred,
                    "realized_opportunity_direction": realized,
                    "discovery_market_opportunity": market or None,
                    "oa_any_market_winner": oa_winner,
                    "oa_directional_classification": classification or None,
                    "funnel_evidence_present": bool(
                        preferred_oa and preferred_oa.get("funnel_evidence_present")
                    ),
                    "outcome_maturity": {
                        "discovery_window_complete_12h": discovery_complete,
                        "oa_outcome_complete_24h": oa_complete,
                        "phase3c_window_complete_24h": (
                            bool(phase3c["window_complete"])
                            if phase3c is not None
                            else None
                        ),
                    },
                    "winner_definitions": {
                        "v2_01": DISCOVERY_WINNER_DEFINITION,
                        "accountability": ACCOUNTABILITY_WINNER_DEFINITION,
                    },
                    "preferred_oa_classification": classification or None,
                    "discovery_window_complete": discovery_complete,
                    "oa_outcome_complete": oa_complete,
                }
            )

    discovery_total = _table_count(connection, "discovery")
    oa_logical = _table_count(connection, "oa")
    mapping["unmatched_discovery"] = max(0, discovery_total - len(seen_discovery))
    mapping["unmatched_oa"] = max(0, oa_logical - len(seen_oa))

    discovery_complete_n = int(
        connection.execute(
            "SELECT COUNT(*) FROM discovery WHERE window_complete = 1"
        ).fetchone()[0]
    )
    oa_complete_n = int(
        connection.execute(
            "SELECT COUNT(*) FROM oa WHERE outcome_complete = 1"
        ).fetchone()[0]
    )
    stage0_n = _table_count(connection, "stage0")
    sqlite_pages = int(connection.execute("PRAGMA page_count").fetchone()[0] or 0)
    sqlite_page_size = int(connection.execute("PRAGMA page_size").fetchone()[0] or 0)

    identity_rate = (
        round(mapping["shared_observation_id_joins"] / stage0_n, 6) if stage0_n else None
    )
    return {
        "measurement_only": True,
        "trade_authority_changed": False,
        "policy_change_authorized": False,
        "join_keys": {
            "primary": "observation_id",
            "fallback": "scan_id|observed_at|normalized_symbol",
            "accountability_explodes_directions": True,
            "stage0_authority": "opip/qualification/screening_evaluations.jsonl",
        },
        "population_counts": {
            "discovery_physical_rows": int(physical.get("discovery") or 0),
            "discovery_logical_latest": discovery_total,
            "discovery_complete": discovery_complete_n,
            "discovery_incomplete": discovery_total - discovery_complete_n,
            "attribution_physical_rows": int(physical.get("attribution") or 0),
            "accountability_physical_rows": int(physical.get("oa") or 0),
            "accountability_logical_latest": oa_logical,
            "accountability_complete": oa_complete_n,
            "accountability_incomplete": oa_logical - oa_complete_n,
            "phase3c_physical_rows": int(physical.get("phase3c") or 0),
            "phase3c_logical_latest": _table_count(connection, "phase3c"),
            "stage0_physical_rows": int(physical.get("stage0") or 0),
            "stage0_logical_latest": stage0_n,
            "funnel_logical_latest": _table_count(connection, "funnel"),
            "matched_observation_id": matched_obs,
            "matched_reconstructed_only": matched_recon,
            "matched": matched_total,
            "discovery_unmatched": mapping["unmatched_discovery"],
            "accountability_unmatched": mapping["unmatched_oa"],
            "match_rate_vs_discovery": (
                round(len(seen_discovery) / discovery_total, 6)
                if discovery_total
                else None
            ),
        },
        "stage0_reconciliation": {
            "authority": "screening_evaluations.jsonl",
            "reconstructed_from_oa": False,
            "population": {
                **stage0_counts,
                "OTHER": stage0_other,
                "total": stage0_n,
            },
            "unmatched_stage0": mapping["unmatched_stage0"],
        },
        "admission_mapping": {
            "ranked_out_preferred_winner_correct_rank_cap_miss": mapping[
                "ranked_out_preferred_winner_mapped_to_rank_cap_miss"
            ],
            "ranked_out_preferred_winner_mapped_elsewhere": mapping[
                "ranked_out_preferred_winner_mapped_elsewhere"
            ],
            "admitted_missing_funnel": mapping["admitted_missing_funnel"],
            "admitted_incorrectly_mapped_to_rank_cap_miss": mapping[
                "admitted_incorrectly_mapped_to_rank_cap_miss"
            ],
            "admitted_incorrectly_mapped_to_rank_cap_miss_expected": 0,
            "threshold_70_79_winner_mappings": mapping["threshold_70_79_winner_mappings"],
            "below_70_market_winner_mappings": mapping["below_70_market_winner_mappings"],
        },
        "classification_mismatches": {
            "count": mapping["classification_mismatches"],
            "sample_limit": MAX_MISMATCH_SAMPLE,
            "sample": mismatches,
        },
        "identity_reconciliation": {
            "shared_observation_id_joins": mapping["shared_observation_id_joins"],
            "fallback_reconstructed_joins": mapping["fallback_reconstructed_joins"],
            "unmatched_discovery": mapping["unmatched_discovery"],
            "unmatched_oa": mapping["unmatched_oa"],
            "unmatched_stage0": mapping["unmatched_stage0"],
            "identity_match_rate_vs_stage0": identity_rate,
            "compatibility_counts": verdict_counts,
        },
        "outcome_reconciliation": {
            "overlapping_observations": matched_total,
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
                "compatibility": COMPAT_DIFFERENT_SCOPE,
                "note": (
                    "Definitions are not unifiable. Disagreement is expected "
                    f"between {DISCOVERY_WINNER_DEFINITION} and "
                    f"{ACCOUNTABILITY_WINNER_DEFINITION}."
                ),
            },
            "mfe_12h_vs_phase3c_24h_abs_pct": {
                **_quantiles(mfe_abs),
                "compatibility": COMPAT_DIFFERENT_SCOPE,
                "note": "V2-01 primary MFE is 12h; Phase 3C MFE is 24h. Not exact equivalents.",
            },
            "mae_abs_pct": {
                **_quantiles(mae_abs),
                "compatibility": COMPAT_DIFFERENT_SCOPE,
            },
            "window_complete_agreement": {
                "agree": complete_agree,
                "compared": complete_compared,
                "compatibility": COMPAT_DIFFERENT_SCOPE,
                "note": "V2-01 primary completeness is 12h; OA/Phase3C completeness is 24h.",
            },
        },
        "maturity_reconciliation": {
            "v2_01_horizon": "12h",
            "phase3c_horizon": "24h",
            "oa_horizon": "24h",
            "equivalence": COMPAT_DIFFERENT_SCOPE,
            "discovery_complete": discovery_complete_n,
            "accountability_complete": oa_complete_n,
            "complete_agree": complete_agree,
            "complete_compared": complete_compared,
        },
        "threshold_authorities": threshold_authority_report(),
        "winner_definitions": {
            "v2_01": DISCOVERY_WINNER_DEFINITION,
            "accountability": ACCOUNTABILITY_WINNER_DEFINITION,
            "unifiable": False,
            "compatibility": COMPAT_DIFFERENT_SCOPE,
        },
        "resource_usage": {
            "elapsed_seconds": round(elapsed_seconds, 3),
            "processed_physical_rows": dict(physical),
            "ingest_stats": {key: dict(value) for key, value in ingest_stats.items()},
            "batches_processed": _meta_get(connection, "batches_processed"),
            "index_cardinality_cap": MAX_INDEX_ROWS_PER_PLANE,
            "index_rows": {
                "stage0": stage0_n,
                "discovery": discovery_total,
                "oa": oa_logical,
                "phase3c": _table_count(connection, "phase3c"),
                "funnel": _table_count(connection, "funnel"),
            },
            "index_cap_exceeded": {
                plane: bool(_meta_get(connection, f"cap_exceeded_{plane}"))
                for plane in ("stage0", "discovery", "oa", "phase3c", "funnel")
            },
            "sqlite_working_set_bytes": sqlite_pages * sqlite_page_size,
            "peak_rss_kb": _peak_rss_kb(),
            "input_file_bytes": dict(input_file_bytes),
            "jsonl_ingestion": "streaming_line_iterator",
            "used_path_read_text_for_jsonl": False,
        },
        "overlap_sample": overlap_sample,
        "labeled_at": datetime.now(timezone.utc).isoformat(),
    }


def _ingest_iterables(
    connection: sqlite3.Connection,
    *,
    screening_rows: Iterator[Mapping[str, Any]] | None,
    discovery_rows: Iterator[Mapping[str, Any]],
    accountability_rows: Iterator[Mapping[str, Any]],
    phase3c_rows: Iterator[Mapping[str, Any]],
    funnel_rows: Iterator[Mapping[str, Any]] | None,
    attribution_rows: Iterator[Mapping[str, Any]] | None,
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    physical = {
        "stage0": 0,
        "discovery": 0,
        "oa": 0,
        "phase3c": 0,
        "funnel": 0,
        "attribution": 0,
    }
    ingest_stats: dict[str, dict[str, int]] = {}

    def _count_and_ingest(name: str, rows: Iterator[Mapping[str, Any]] | None, upsert) -> None:
        if rows is None:
            return
        stats = {"physical_rows": 0, "malformed_rows": 0, "truncated_tail_skipped": 0}
        # Iterables from lists have no stats hook; wrap to count.
        def _wrapped() -> Iterator[Mapping[str, Any]]:
            for row in rows:
                stats["physical_rows"] += 1
                yield row

        _ingest_stream(connection, _wrapped(), upsert, plane=name)
        physical[name if name != "stage0" else "stage0"] = stats["physical_rows"]
        if name == "stage0":
            physical["stage0"] = stats["physical_rows"]
        elif name == "discovery":
            physical["discovery"] = stats["physical_rows"]
        elif name == "oa":
            physical["oa"] = stats["physical_rows"]
        elif name == "phase3c":
            physical["phase3c"] = stats["physical_rows"]
        elif name == "funnel":
            physical["funnel"] = stats["physical_rows"]
        ingest_stats[name] = stats

    _count_and_ingest("stage0", screening_rows, _upsert_stage0)
    _count_and_ingest("discovery", discovery_rows, _upsert_discovery)
    _count_and_ingest("oa", accountability_rows, _upsert_oa)
    _count_and_ingest("phase3c", phase3c_rows, _upsert_phase3c)
    _count_and_ingest("funnel", funnel_rows, _upsert_funnel)
    if attribution_rows is not None:
        count = 0
        for _row in attribution_rows:
            count += 1
        physical["attribution"] = count
    return physical, ingest_stats


def reconcile_rows(
    *,
    discovery_rows: list[dict[str, Any]],
    accountability_rows: list[dict[str, Any]],
    phase3c_rows: list[dict[str, Any]] | None = None,
    attribution_rows: list[dict[str, Any]] | None = None,
    screening_rows: list[dict[str, Any]] | None = None,
    funnel_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    connection = _open_index()
    try:
        physical, ingest_stats = _ingest_iterables(
            connection,
            screening_rows=iter(screening_rows or []),
            discovery_rows=iter(discovery_rows),
            accountability_rows=iter(accountability_rows),
            phase3c_rows=iter(phase3c_rows or []),
            funnel_rows=iter(funnel_rows or []),
            attribution_rows=iter(attribution_rows or []),
        )
        report = _assemble_report(
            connection,
            physical=physical,
            ingest_stats=ingest_stats,
            input_file_bytes={},
            elapsed_seconds=time.perf_counter() - started,
        )
        return report
    finally:
        connection.close()


def _stream_plane_files(
    connection: sqlite3.Connection,
    *,
    paths: list[Path],
    archive_dir: Path | None,
    upsert,
    plane: str,
) -> tuple[int, dict[str, int], dict[str, int]]:
    stats = {
        "physical_rows": 0,
        "malformed_rows": 0,
        "truncated_tail_skipped": 0,
        "archive_checksum_skipped": 0,
    }
    file_bytes: dict[str, int] = {}
    for path in paths:
        file_bytes[str(path)] = _file_bytes(path)
        _ingest_stream(connection, iter_jsonl_dicts(path, stats=stats), upsert, plane=plane)
    if archive_dir is not None and archive_dir.exists():
        file_bytes[str(archive_dir)] = sum(
            _file_bytes(item)
            for item in archive_dir.rglob("*")
            if item.is_file() and item.suffix in {".gz", ".jsonl"}
        )
        _ingest_stream(
            connection,
            _iter_checksummed_archive_jsonl(archive_dir, stats=stats),
            upsert,
            plane=plane,
        )
    return int(stats["physical_rows"]), stats, file_bytes


def inspect_replica(data_root: Path | str) -> dict[str, Any]:
    root = Path(data_root)
    started = time.perf_counter()
    screening_path = root / "opip/qualification/screening_evaluations.jsonl"
    funnel_path = root / "opip/qualification/funnel_events.jsonl"
    discovery_path = root / "opip/discovery/forward_outcomes.jsonl"
    attribution_path = root / "opip/discovery/attributions.jsonl"
    oa_path = root / "opip/opportunity_accountability.jsonl"
    phase3c_path = root / "phase3c_forward_outcomes.jsonl"

    # Use the canonical BoundedJsonl archive contract for paths only.
    # Do not call iter_hot_rows(): that helper materializes the HOT file.
    screening_archive_dir = screening_evaluations_archive(screening_path).archive_dir
    funnel_archive_dir = funnel_events_archive(funnel_path).archive_dir

    index_dir = root / "opip/discovery"
    index_dir.mkdir(parents=True, exist_ok=True)
    handle, index_name = tempfile.mkstemp(
        prefix=".ef01_reconcile_",
        suffix=".sqlite3",
        dir=str(index_dir),
    )
    os.close(handle)
    index_file = Path(index_name)
    connection = _open_index(index_file)
    physical: dict[str, int] = {}
    ingest_stats: dict[str, dict[str, int]] = {}
    input_file_bytes: dict[str, int] = {}
    try:
        count, stats, sizes = _stream_plane_files(
            connection,
            paths=[screening_path],
            archive_dir=screening_archive_dir if screening_archive_dir.exists() else None,
            upsert=_upsert_stage0,
            plane="stage0",
        )
        physical["stage0"] = count
        ingest_stats["stage0"] = stats
        input_file_bytes.update(sizes)

        count, stats, sizes = _stream_plane_files(
            connection,
            paths=[funnel_path],
            archive_dir=funnel_archive_dir if funnel_archive_dir.exists() else None,
            upsert=_upsert_funnel,
            plane="funnel",
        )
        physical["funnel"] = count
        ingest_stats["funnel"] = stats
        input_file_bytes.update(sizes)

        count, stats, sizes = _stream_plane_files(
            connection,
            paths=[discovery_path],
            archive_dir=None,
            upsert=_upsert_discovery,
            plane="discovery",
        )
        physical["discovery"] = count
        ingest_stats["discovery"] = stats
        input_file_bytes.update(sizes)

        attr_stats = {
            "physical_rows": 0,
            "malformed_rows": 0,
            "truncated_tail_skipped": 0,
        }
        attr_count = 0
        for _row in iter_jsonl_dicts(attribution_path, stats=attr_stats):
            attr_count += 1
        physical["attribution"] = attr_count
        ingest_stats["attribution"] = attr_stats
        input_file_bytes[str(attribution_path)] = _file_bytes(attribution_path)

        count, stats, sizes = _stream_plane_files(
            connection,
            paths=[oa_path],
            archive_dir=None,
            upsert=_upsert_oa,
            plane="oa",
        )
        physical["oa"] = count
        ingest_stats["oa"] = stats
        input_file_bytes.update(sizes)

        count, stats, sizes = _stream_plane_files(
            connection,
            paths=[phase3c_path],
            archive_dir=None,
            upsert=_upsert_phase3c,
            plane="phase3c",
        )
        physical["phase3c"] = count
        ingest_stats["phase3c"] = stats
        input_file_bytes.update(sizes)

        report = _assemble_report(
            connection,
            physical=physical,
            ingest_stats=ingest_stats,
            input_file_bytes=input_file_bytes,
            elapsed_seconds=time.perf_counter() - started,
        )
    finally:
        connection.close()
        try:
            index_file.unlink()
        except OSError:
            pass
        for leftover in index_file.parent.glob(index_file.name + "*"):
            try:
                leftover.unlink()
            except OSError:
                pass

    report["replica_root"] = str(root)
    report["replica_available"] = bool(
        physical.get("stage0")
        or (physical.get("discovery") and physical.get("oa"))
    )
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
    "MAX_INDEX_ROWS_PER_PLANE",
    "inspect_replica",
    "iter_jsonl_dicts",
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
