from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any, Iterable, Iterator, Mapping

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.streams import StreamSpec, resolve_streams


logger = logging.getLogger(__name__)
SHIPPER_LOCK_ID = 6_270_051_702


@dataclass(frozen=True)
class SourceLine:
    start_offset: int
    end_offset: int
    raw: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw.rstrip(b"\r\n")).hexdigest()


@dataclass(frozen=True)
class Checkpoint:
    byte_offset: int = 0
    last_row_sha256: str = ""
    rows_ingested: int = 0
    source_size: int = 0
    source_generation: int = 1


@dataclass(frozen=True)
class ShipResult:
    stream_name: str
    rows_seen: int
    rows_inserted: int
    dead_letters: int
    byte_offset: int
    source_size: int
    reset_to_start: bool = False


def _canonical_json(row: Mapping[str, Any]) -> bytes:
    return json.dumps(
        row,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def source_event_id(stream_name: str, row: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(stream_name.encode("utf-8") + b"\0" + _canonical_json(row))
    return digest.hexdigest()


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        return None
    return stamp.astimezone(timezone.utc)


def observed_at(row: Mapping[str, Any]) -> datetime:
    for key in (
        "observed_at",
        "observed_at_utc",
        "recorded_at",
        "updated_at",
        "occurred_at",
        "attempted_at",
        "generated_at",
        "captured_at",
        "scan_at",
        "decision_at",
        "decision_at_utc",
        "decided_at",
        "event_time",
        "timestamp",
        "created_at",
    ):
        stamp = _parse_time(row.get(key))
        if stamp is not None:
            return stamp
    raise ValueError("row has no timezone-aware observation timestamp")


def iter_lines(path: Path, *, start_offset: int = 0) -> Iterator[SourceLine]:
    with path.open("rb") as handle:
        handle.seek(start_offset)
        while True:
            start = handle.tell()
            raw = handle.readline()
            if not raw:
                break
            end = handle.tell()
            # An interrupted producer tail is not a committed WAL row.  Leave
            # the checkpoint before it so the next invocation retries it.
            if not raw.endswith(b"\n"):
                break
            if raw.strip():
                yield SourceLine(start, end, raw)


def _last_complete_row_hash(path: Path, end_offset: int) -> str | None:
    if end_offset <= 0:
        return ""
    with path.open("rb") as handle:
        window = min(end_offset, 256 * 1024)
        handle.seek(end_offset - window)
        raw = handle.read(window)
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return None
    return hashlib.sha256(lines[-1]).hexdigest()


def checkpoint_is_continuous(path: Path, checkpoint: Checkpoint) -> bool:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return checkpoint.byte_offset == 0
    if checkpoint.byte_offset == 0:
        return True
    if checkpoint.byte_offset > size or not checkpoint.last_row_sha256:
        return False
    return _last_complete_row_hash(path, checkpoint.byte_offset) == checkpoint.last_row_sha256


def _asset_from_symbol(symbol: str) -> tuple[str, str | None]:
    value = str(symbol or "").upper().replace("/", "").replace("-", "")
    for quote in ("USDT", "USDC", "USD", "EUR", "BTC", "ETH"):
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote
    return value or "UNKNOWN", None


class DatabaseWriter:
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def checkpoint(self, stream_name: str) -> Checkpoint:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT byte_offset, last_row_sha256, rows_ingested, source_size,
                       source_generation
                FROM ops.ingest_checkpoint WHERE stream_name = %s
                """,
                (stream_name,),
            )
            row = cursor.fetchone()
        return Checkpoint(*row) if row else Checkpoint()

    def _instrument(self, cursor: Any, row: Mapping[str, Any], stamp: datetime) -> int:
        identity = row.get("venue_instrument")
        nested = identity if isinstance(identity, Mapping) else {}
        raw_symbol = str(
            nested.get("raw_identifier")
            or row.get("venue_instrument_id")
            or row.get("symbol")
            or row.get("pair")
            or row.get("asset")
            or row.get("base_asset")
            or "UNKNOWN"
        ).upper()
        inferred_asset, quote = _asset_from_symbol(raw_symbol)
        resolution = str(nested.get("resolution_status") or "EXACT").upper()
        resolved = resolution in {"EXACT", "REFERENCE_ALIAS", "UNIQUE"}
        canonical = str(
            nested.get("canonical_asset_id")
            or row.get("base_asset")
            or row.get("asset")
            or inferred_asset
        ).upper()
        if not resolved or not canonical or canonical == "UNKNOWN":
            canonical = f"UNRESOLVED:{hashlib.sha256(raw_symbol.encode()).hexdigest()[:16]}"
        mapping = "UNIQUE" if resolved else "UNRESOLVED"
        venue = str(nested.get("venue") or row.get("venue") or "KRAKEN").upper()
        cursor.execute(
            """
            INSERT INTO market.instrument(canonical_asset, display_name, first_seen_at)
            VALUES (%s, %s, %s)
            ON CONFLICT (canonical_asset) DO UPDATE SET
                display_name = coalesce(market.instrument.display_name, EXCLUDED.display_name)
            RETURNING instrument_id
            """,
            (canonical, nested.get("display_name") or row.get("asset_display_name"), stamp),
        )
        instrument_id = int(cursor.fetchone()[0])
        cursor.execute(
            "SELECT instrument_id FROM market.instrument_alias WHERE venue = %s AND venue_symbol = %s",
            (venue, raw_symbol),
        )
        existing = cursor.fetchone()
        if existing is not None and int(existing[0]) != instrument_id:
            raise ValueError(f"ambiguous instrument alias {venue}:{raw_symbol}")
        cursor.execute(
            """
            INSERT INTO market.instrument_alias(
                instrument_id, venue, venue_symbol, quote_currency,
                mapping_status, resolved_at
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (venue, venue_symbol) DO NOTHING
            """,
            (instrument_id, venue, raw_symbol, quote, mapping, stamp),
        )
        return instrument_id

    def insert_raw(
        self,
        cursor: Any,
        *,
        spec: StreamSpec,
        path: Path,
        line: SourceLine,
        row: Mapping[str, Any],
        event_id: str,
        stamp: datetime,
        source_generation: int,
    ) -> bool:
        from psycopg.types.json import Jsonb

        cursor.execute(
            """
            INSERT INTO raw.ingested_event(
                stream_name, source_event_id, source_file, source_byte_offset,
                source_row_sha256, observed_at, payload, source_generation
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            """,
            (
                spec.name,
                event_id,
                str(path),
                line.start_offset,
                line.sha256,
                stamp,
                Jsonb(dict(row)),
                source_generation,
            ),
        )
        return cursor.rowcount == 1

    def insert_typed(
        self,
        cursor: Any,
        *,
        spec: StreamSpec,
        row: Mapping[str, Any],
        event_id: str,
        stamp: datetime,
    ) -> None:
        from psycopg.types.json import Jsonb

        if spec.kind == "screening":
            instrument_id = self._instrument(cursor, row, stamp)
            outcome = str(row.get("outcome") or "DATA_UNAVAILABLE").upper()
            reason = str(row.get("reason") or row.get("reason_code") or outcome)
            metadata = row.get("metadata")
            metadata_dict = dict(metadata) if isinstance(metadata, Mapping) else {}
            cursor.execute(
                """
                INSERT INTO market.screening(
                    scan_id, scanner_type, instrument_id, observed_at, last_price,
                    technical_score, long_score, short_score, outcome, reason_code,
                    strategy_version, metadata
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    str(row.get("scan_id") or "UNKNOWN"),
                    str(row.get("scanner_type") or "UNKNOWN"),
                    instrument_id,
                    stamp,
                    row.get("last_price"),
                    row.get("technical_score"),
                    row.get("long_score"),
                    row.get("short_score"),
                    outcome,
                    reason,
                    str(row.get("strategy_version") or metadata_dict.get("strategy_version") or "UNKNOWN"),
                    Jsonb(metadata_dict),
                ),
            )
        elif spec.kind == "market_observation":
            instrument_id = self._instrument(cursor, row, stamp)
            cursor.execute(
                """
                INSERT INTO market.observation(
                    observation_key, instrument_id, observed_at, capture_reason,
                    last_price, volume_24h, notional_24h_usd, high_24h, low_24h, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    event_id,
                    instrument_id,
                    stamp,
                    row.get("capture_reason"),
                    row.get("last_price"),
                    row.get("volume_24h"),
                    row.get("notional_24h_usd_approx"),
                    row.get("high_24h"),
                    row.get("low_24h"),
                    Jsonb(dict(row)),
                ),
            )
        elif spec.kind == "intelligence":
            instrument_id = self._instrument(cursor, row, stamp)
            payload = row.get("payload")
            cursor.execute(
                """
                INSERT INTO signal.intelligence_event(
                    event_key, observed_at, event_type, journey_id, signal_id,
                    instrument_id, delivered, admitted, reason_code,
                    strategy_version, config_version, model_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    event_id,
                    stamp,
                    str(row.get("event_type") or "UNKNOWN"),
                    row.get("journey_id"),
                    row.get("signal_id"),
                    instrument_id,
                    row.get("delivered"),
                    row.get("admitted"),
                    row.get("reason"),
                    row.get("strategy_version"),
                    row.get("config_version") or row.get("gate_policy_version"),
                    row.get("model_version") or row.get("intelligence_version"),
                    Jsonb(dict(payload) if isinstance(payload, Mapping) else {}),
                ),
            )
        elif spec.kind == "funnel":
            self._insert_funnel(cursor, row=row, event_id=event_id, stamp=stamp)
        elif spec.kind == "paper_event":
            instrument_id = self._instrument(cursor, row, stamp)
            paper_trade_id = str(row.get("paper_trade_id") or "")
            if not paper_trade_id:
                raise ValueError("paper event has no paper_trade_id")
            state = str(row.get("status") or "UNKNOWN").upper()
            cursor.execute(
                """
                INSERT INTO paper.trade(
                    paper_trade_id, signal_id, instrument_id, state, opened_at,
                    closed_at, realised_pnl, reconciliation_status,
                    strategy_version, config_version, model_version, payload
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'UNVERIFIED', %s, %s, %s, %s)
                ON CONFLICT (paper_trade_id) DO UPDATE SET
                    state = EXCLUDED.state,
                    closed_at = coalesce(EXCLUDED.closed_at, paper.trade.closed_at),
                    realised_pnl = coalesce(EXCLUDED.realised_pnl, paper.trade.realised_pnl),
                    payload = EXCLUDED.payload
                """,
                (
                    paper_trade_id,
                    row.get("signal_id"),
                    instrument_id,
                    state,
                    _parse_time(row.get("opened_at")),
                    _parse_time(row.get("closed_at")) if row.get("closed_at") else (
                        stamp if state in {"CLOSED", "REJECTED", "EXPIRED", "CANCELLED"} else None
                    ),
                    row.get("net_pnl"),
                    row.get("strategy_version"),
                    row.get("config_version") or row.get("gate_policy_version"),
                    row.get("model_version") or row.get("intelligence_version"),
                    Jsonb(dict(row)),
                ),
            )
            cursor.execute(
                """
                INSERT INTO paper.trade_event(
                    event_id, paper_trade_id, revision, event_type, occurred_at, payload
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    str(row.get("event_id") or event_id),
                    paper_trade_id,
                    int(row.get("revision") or 1),
                    str(row.get("event_type") or "UNKNOWN"),
                    stamp,
                    Jsonb(dict(row)),
                ),
            )

    def _insert_funnel(
        self,
        cursor: Any,
        *,
        row: Mapping[str, Any],
        event_id: str,
        stamp: datetime,
    ) -> None:
        from psycopg.types.json import Jsonb

        instrument_id = self._instrument(cursor, row, stamp)
        episode_id = str(row.get("episode_id") or f"EPISODE:{event_id[:24]}")
        candidate_id = str(row.get("candidate_id") or f"CANDIDATE:{event_id[:24]}")
        decision = str(row.get("decision") or "INCOMPLETE").upper()
        terminal_code = str(
            row.get("terminal_reason_code")
            or ("QUALIFIED" if decision in {"QUALIFIED", "ADVANCED"} else "UNATTRIBUTED")
        )
        strategy_version = str(row.get("strategy_version") or "UNKNOWN")
        config_version = str(row.get("gate_policy_version") or "UNKNOWN")
        model_version = row.get("intelligence_version")
        cursor.execute(
            """
            INSERT INTO lifecycle.episode(
                episode_id, cohort_id, instrument_id, decision_at, current_stage,
                terminal_reason, strategy_version, config_version, model_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (episode_id) DO UPDATE SET
                current_stage = EXCLUDED.current_stage,
                terminal_reason = EXCLUDED.terminal_reason
            """,
            (
                episode_id,
                row.get("cohort_id"),
                instrument_id,
                stamp,
                decision,
                row.get("terminal_reason"),
                strategy_version,
                config_version,
                model_version,
            ),
        )
        cursor.execute(
            """
            INSERT INTO lifecycle.candidate(
                candidate_id, episode_id, scan_id, instrument_id, direction,
                decision_at, decision, terminal_reason_code, terminal_reason,
                strategy_version, config_version, model_version, evidence
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (candidate_id) DO UPDATE SET
                decision = EXCLUDED.decision,
                terminal_reason_code = EXCLUDED.terminal_reason_code,
                terminal_reason = EXCLUDED.terminal_reason,
                evidence = EXCLUDED.evidence
            """,
            (
                candidate_id,
                episode_id,
                str(row.get("scan_id") or "UNKNOWN"),
                instrument_id,
                str(row.get("direction") or "LONG").upper(),
                stamp,
                decision,
                terminal_code,
                row.get("terminal_reason"),
                strategy_version,
                config_version,
                model_version,
                Jsonb(dict(row)),
            ),
        )
        gates = row.get("gate_results")
        for index, gate in enumerate(gates if isinstance(gates, list) else []):
            if not isinstance(gate, Mapping):
                continue
            gate_stamp = _parse_time(gate.get("evaluated_at")) or stamp
            gate_name = str(gate.get("gate") or f"GATE_{index}")
            status = str(gate.get("status") or "ERROR").upper()
            reason_code = str(gate.get("reason_code") or status)
            transition_key = hashlib.sha256(
                f"{candidate_id}|{gate_name}|{gate_stamp.isoformat()}".encode()
            ).hexdigest()
            cursor.execute(
                """
                INSERT INTO lifecycle.stage_transition(
                    transition_key, episode_id, candidate_id, occurred_at,
                    from_stage, to_stage, outcome, reason_code, gate_name,
                    measured_value, threshold_value, evidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    transition_key,
                    episode_id,
                    candidate_id,
                    gate_stamp,
                    None,
                    gate_name,
                    status,
                    reason_code,
                    gate_name,
                    gate.get("measured_value"),
                    gate.get("threshold"),
                    Jsonb(dict(gate.get("metadata") or {})),
                ),
            )

    def dead_letter(
        self,
        cursor: Any,
        *,
        spec: StreamSpec,
        path: Path,
        line: SourceLine,
        error: Exception,
        source_generation: int,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO ops.dead_letter(
                stream_name, source_file, source_byte_offset, source_row_sha256,
                raw_text, error_type, error_message, source_generation
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (
                stream_name, source_generation, source_row_sha256, source_byte_offset
            ) DO NOTHING
            """,
            (
                spec.name,
                str(path),
                line.start_offset,
                line.sha256,
                line.raw.decode("utf-8", errors="replace")[:1_000_000],
                type(error).__name__,
                str(error)[:4000],
                source_generation,
            ),
        )

    def advance_checkpoint(
        self,
        cursor: Any,
        *,
        spec: StreamSpec,
        path: Path,
        line: SourceLine | None,
        prior: Checkpoint,
        source_size: int,
        rows_seen: int,
        checkpoint_name: str | None = None,
    ) -> None:
        end_offset = line.end_offset if line else prior.byte_offset
        last_hash = line.sha256 if line else prior.last_row_sha256
        cursor.execute(
            """
            INSERT INTO ops.ingest_checkpoint(
                stream_name, source_file, byte_offset, last_row_sha256,
                rows_ingested, source_size, source_generation, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, now())
            ON CONFLICT (stream_name) DO UPDATE SET
                source_file = EXCLUDED.source_file,
                byte_offset = EXCLUDED.byte_offset,
                last_row_sha256 = EXCLUDED.last_row_sha256,
                rows_ingested = EXCLUDED.rows_ingested,
                source_size = EXCLUDED.source_size,
                source_generation = EXCLUDED.source_generation,
                updated_at = now()
            """,
            (
                checkpoint_name or spec.name,
                str(path),
                end_offset,
                last_hash,
                prior.rows_ingested + rows_seen,
                source_size,
                prior.source_generation,
            ),
        )


def ship_stream(
    connection: Any,
    *,
    spec: StreamSpec,
    path: Path,
    batch_size: int = 1000,
    checkpoint_name: str | None = None,
    source_file: Path | None = None,
) -> ShipResult:
    writer = DatabaseWriter(connection)
    checkpoint_key = checkpoint_name or spec.name
    recorded_source = source_file or path
    prior = writer.checkpoint(checkpoint_key)
    connection.commit()
    if not path.exists():
        if spec.required:
            logger.warning("required O'Pip stream is absent: %s", path)
        return ShipResult(spec.name, 0, 0, 0, prior.byte_offset, 0)

    source_size = path.stat().st_size
    continuous = checkpoint_is_continuous(path, prior)
    if not continuous:
        prior = Checkpoint(
            rows_ingested=prior.rows_ingested,
            source_generation=prior.source_generation + 1,
        )
    seen = inserted = dead = 0
    last_line: SourceLine | None = None
    batch: list[SourceLine] = []

    def flush(lines: list[SourceLine]) -> None:
        nonlocal seen, inserted, dead, last_line, prior
        if not lines:
            return
        with connection.transaction():
            with connection.cursor() as cursor:
                for line in lines:
                    seen += 1
                    last_line = line
                    try:
                        decoded = line.raw.decode("utf-8")
                        row = json.loads(decoded, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(f"non-finite JSON value {token}")))
                        if not isinstance(row, dict):
                            raise ValueError("JSONL row must be an object")
                        stamp = observed_at(row)
                        event_id = source_event_id(spec.name, row)
                        was_inserted = writer.insert_raw(
                            cursor,
                            spec=spec,
                            path=recorded_source,
                            line=line,
                            row=row,
                            event_id=event_id,
                            stamp=stamp,
                            source_generation=prior.source_generation,
                        )
                        # Typed projection is independently recoverable from raw.
                        # A schema/constraint failure is dead-lettered without
                        # discarding the canonical raw record.
                        try:
                            with connection.transaction():
                                with connection.cursor() as typed_cursor:
                                    writer.insert_typed(
                                        typed_cursor,
                                        spec=spec,
                                        row=row,
                                        event_id=event_id,
                                        stamp=stamp,
                                    )
                        except Exception as typed_error:
                            writer.dead_letter(
                                cursor,
                                spec=spec,
                                path=recorded_source,
                                line=line,
                                error=typed_error,
                                source_generation=prior.source_generation,
                            )
                            dead += 1
                        inserted += int(was_inserted)
                    except Exception as error:
                        writer.dead_letter(
                            cursor,
                            spec=spec,
                            path=recorded_source,
                            line=line,
                            error=error,
                            source_generation=prior.source_generation,
                        )
                        dead += 1
                writer.advance_checkpoint(
                    cursor,
                    spec=spec,
                    path=recorded_source,
                    line=last_line,
                    prior=prior,
                    source_size=source_size,
                    rows_seen=seen,
                    checkpoint_name=checkpoint_key,
                )

    for line in iter_lines(path, start_offset=prior.byte_offset):
        batch.append(line)
        if len(batch) >= max(1, int(batch_size)):
            flush(batch)
            batch.clear()
    flush(batch)
    if seen == 0:
        # A checkpoint heartbeat distinguishes a healthy quiet stream from a
        # stalled shipper without moving past an incomplete producer tail.
        with connection.transaction():
            with connection.cursor() as cursor:
                writer.advance_checkpoint(
                    cursor,
                    spec=spec,
                    path=recorded_source,
                    line=None,
                    prior=prior,
                    source_size=source_size,
                    rows_seen=0,
                    checkpoint_name=checkpoint_key,
                )
    checkpoint = writer.checkpoint(checkpoint_key)
    connection.commit()
    return ShipResult(
        spec.name,
        seen,
        inserted,
        dead,
        checkpoint.byte_offset,
        source_size,
        reset_to_start=not continuous,
    )


def run_once(connection: Any, config: DataPlatformConfig) -> list[ShipResult]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_try_advisory_lock(%s)", (SHIPPER_LOCK_ID,))
        locked = bool(cursor.fetchone()[0])
    connection.commit()
    if not locked:
        raise RuntimeError("another O'Pip shipper instance holds the advisory lock")
    try:
        return [
            ship_stream(
                connection,
                spec=spec,
                path=path,
                batch_size=config.batch_size,
            )
            for spec, path in resolve_streams(config.data_root)
        ]
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", (SHIPPER_LOCK_ID,))
        connection.commit()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ship O'Pip file WAL into PostgreSQL")
    parser.add_argument("--watch", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    dsn = config.require_shipper_dsn()

    while True:
        try:
            with connect(
                dsn,
                connect_timeout_seconds=config.connect_timeout_seconds,
                application_name="opip-shipper",
            ) as connection:
                results = run_once(connection, config)
                for result in results:
                    logger.info("O'Pip shipper result: %s", result)
        except Exception:
            logger.exception("O'Pip shipper cycle failed; source files remain authoritative")
            if not args.watch:
                raise
        if not args.watch:
            return 0
        time.sleep(config.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
