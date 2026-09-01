from __future__ import annotations

import argparse
import gzip
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Iterable, Iterator

from app.opip.data_platform.config import DataPlatformConfig
from app.opip.data_platform.db import connect
from app.opip.data_platform.migrations import ensure_monthly_partitions
from app.opip.data_platform.reconcile import reconcile_stream
from app.opip.data_platform.shipper import ShipResult, ship_stream
from app.opip.data_platform.streams import StreamSpec, resolve_streams


def archive_paths(data_root: Path, spec: StreamSpec) -> list[Path]:
    hot_path = data_root / spec.relative_path
    stem = spec.relative_path.name.removesuffix(".jsonl")
    candidates = set(hot_path.parent.glob(f"{stem}-*.jsonl.gz"))
    for directory in (
        hot_path.parent / f"{stem}_archive",
        hot_path.parent / "archive",
    ):
        if directory.is_dir():
            candidates.update(directory.rglob(f"{stem}-*.jsonl.gz"))
    return sorted(path for path in candidates if path.is_file())


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_archive_sha256(path: Path) -> str:
    checksum = path.with_suffix(path.suffix + ".sha256")
    if not checksum.is_file():
        raise RuntimeError(f"archive checksum is missing: {path}")
    expected = checksum.read_text(encoding="utf-8").strip().split(maxsplit=1)[0]
    actual = _sha256_file(path)
    if len(expected) != 64 or expected.lower() != actual:
        raise RuntimeError(f"archive checksum mismatch: {path}")
    return actual


def _decompressed(path: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="opip-backfill-") as directory:
        target = Path(directory) / path.name.removesuffix(".gz")
        with gzip.open(path, "rb") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        yield target


def backfill(
    connection,
    config: DataPlatformConfig,
    *,
    include_hot: bool = True,
) -> list[ShipResult]:
    ensure_monthly_partitions(connection, months_before=36, months_after=2)
    results: list[ShipResult] = []
    for spec, hot_path in resolve_streams(config.data_root):
        archives = archive_paths(config.data_root, spec)
        if spec.required and not archives and not hot_path.exists():
            raise RuntimeError(f"required backfill source is missing: {hot_path}")
        for archive in archives:
            digest = _verified_archive_sha256(archive)
            checkpoint_name = f"backfill:{spec.name}:{digest}"
            for extracted in _decompressed(archive):
                result = ship_stream(
                    connection,
                    spec=spec,
                    path=extracted,
                    batch_size=config.batch_size,
                    checkpoint_name=checkpoint_name,
                    source_file=archive,
                )
                results.append(result)
                reconciliation = reconcile_stream(
                    connection,
                    stream_name=spec.name,
                    path=extracted,
                    checkpoint_name=checkpoint_name,
                    source_file=archive,
                )
                if reconciliation.status != "CLEAN":
                    raise RuntimeError(
                        f"archive reconciliation failed: {archive}"
                    )
        if include_hot and hot_path.exists():
            result = ship_stream(
                connection,
                spec=spec,
                path=hot_path,
                batch_size=config.batch_size,
                checkpoint_name=spec.name,
            )
            results.append(result)
            reconciliation = reconcile_stream(
                connection,
                stream_name=spec.name,
                path=hot_path,
            )
            if reconciliation.status != "CLEAN":
                raise RuntimeError(f"hot-file reconciliation failed: {hot_path}")
    return results


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill O'Pip historical evidence")
    parser.add_argument("--archives-only", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    config = DataPlatformConfig.from_env()
    admin_dsn = os.getenv("OPIP_ANALYTICS_ADMIN_DATABASE_URL")
    if not admin_dsn:
        raise RuntimeError("OPIP_ANALYTICS_ADMIN_DATABASE_URL is required")
    with connect(
        admin_dsn,
        connect_timeout_seconds=config.connect_timeout_seconds,
        application_name="opip-backfill",
    ) as connection:
        results = backfill(connection, config, include_hot=not args.archives_only)
    print(
        "O'Pip backfill complete:",
        f"sources={len(results)}",
        f"rows_seen={sum(item.rows_seen for item in results)}",
        f"inserted={sum(item.rows_inserted for item in results)}",
        f"dead_letters={sum(item.dead_letters for item in results)}",
    )
    return 0 if all(item.dead_letters == 0 for item in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
