from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any, Iterable

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.shipper import DatabaseWriter, iter_lines
from app.opip.data_platform.streams import resolve_streams


@dataclass(frozen=True)
class ReconciliationResult:
    stream_name: str
    source_rows: int
    database_rows: int
    dead_letters: int
    difference: int
    source_sha256: str
    database_sha256: str
    status: str


def _hashes_digest(hashes: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for value in hashes:
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def reconcile_stream(
    connection: Any,
    *,
    stream_name: str,
    path: Path,
    checkpoint_name: str | None = None,
    source_file: Path | None = None,
) -> ReconciliationResult:
    checkpoint_key = checkpoint_name or stream_name
    recorded_source = source_file or path
    checkpoint = DatabaseWriter(connection).checkpoint(checkpoint_key)
    connection.commit()
    source_hashes = [
        line.sha256
        for line in iter_lines(path)
        if line.end_offset <= checkpoint.byte_offset
    ] if path.exists() else []
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source_row_sha256
            FROM raw.ingested_event
            WHERE stream_name = %s AND source_file = %s
              AND source_generation = %s AND source_byte_offset < %s
            ORDER BY source_byte_offset, source_row_sha256
            """,
            (
                stream_name,
                str(recorded_source),
                checkpoint.source_generation,
                checkpoint.byte_offset,
            ),
        )
        database_hashes = [str(row[0]) for row in cursor]
        cursor.execute(
            """
            SELECT count(*) FROM ops.dead_letter dead
            WHERE dead.stream_name = %s AND dead.source_file = %s
              AND dead.source_generation = %s
              AND dead.source_byte_offset < %s AND dead.resolved_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM raw.ingested_event raw
                  WHERE raw.stream_name = dead.stream_name
                    AND raw.source_file = dead.source_file
                    AND raw.source_generation = dead.source_generation
                    AND raw.source_byte_offset = dead.source_byte_offset
                    AND raw.source_row_sha256 = dead.source_row_sha256
              )
            """,
            (
                stream_name,
                str(recorded_source),
                checkpoint.source_generation,
                checkpoint.byte_offset,
            ),
        )
        dead_letters = int(cursor.fetchone()[0])
    source_digest = _hashes_digest(sorted(source_hashes))
    database_digest = _hashes_digest(sorted(database_hashes))
    accounted = len(database_hashes) + dead_letters
    difference = len(source_hashes) - accounted
    status = (
        "CLEAN"
        if difference == 0 and not dead_letters and source_digest == database_digest
        else "MISMATCH"
    )
    result = ReconciliationResult(
        stream_name,
        len(source_hashes),
        len(database_hashes),
        dead_letters,
        difference,
        source_digest,
        database_digest,
        status,
    )
    from psycopg.types.json import Jsonb

    with connection.transaction():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ops.reconciliation_run(
                    stream_name, source_file, source_generation,
                    source_byte_offset, source_rows, database_rows, difference,
                    source_sha256, status, details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    stream_name,
                    str(recorded_source),
                    checkpoint.source_generation,
                    checkpoint.byte_offset,
                    result.source_rows,
                    result.database_rows,
                    result.difference,
                    result.source_sha256,
                    result.status,
                    Jsonb(
                        {
                            "database_sha256": result.database_sha256,
                            "dead_letters": result.dead_letters,
                            "source_generation": checkpoint.source_generation,
                        }
                    ),
                ),
            )
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile O'Pip file WAL and PostgreSQL")
    parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    with connect(
        config.require_shipper_dsn(),
        connect_timeout_seconds=config.connect_timeout_seconds,
        application_name="opip-reconciliation",
    ) as connection:
        results = [
            reconcile_stream(connection, stream_name=spec.name, path=path)
            for spec, path in resolve_streams(config.data_root)
            if path.exists()
        ]
    for result in results:
        print(result)
    return 0 if all(item.status == "CLEAN" for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
