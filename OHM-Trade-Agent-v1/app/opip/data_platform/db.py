from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


def connect(
    dsn: str,
    *,
    connect_timeout_seconds: int = 5,
    application_name: str = "opip-data-platform",
    autocommit: bool = False,
) -> Any:
    """Create a PostgreSQL connection without importing the driver at startup.

    Lazy import keeps the deterministic production scanner import graph free of
    database work.  Only an explicit analytics/dashboard call imports psycopg.
    """
    import psycopg

    return psycopg.connect(
        dsn,
        connect_timeout=max(1, int(connect_timeout_seconds)),
        application_name=application_name,
        autocommit=autocommit,
    )


@contextmanager
def transaction(connection: Any) -> Iterator[Any]:
    with connection.transaction():
        with connection.cursor() as cursor:
            yield cursor
