from contextlib import contextmanager
from typing import Any, Iterator

import psycopg

try:
    from psycopg_pool import ConnectionPool
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    ConnectionPool = None


class DatabasePool:
    """Thread-safe PostgreSQL connection pool with single-connection fallback.

    Uses psycopg_pool.ConnectionPool when available and a database URL is
    configured.  Falls back to a plain psycopg.connect() per call when the
    pool library is absent or the URL is empty (e.g. in-memory mode).
    """

    def __init__(
        self,
        database_url: str,
        min_size: int,
        max_size: int,
        timeout_seconds: float,
    ) -> None:
        self._database_url = database_url
        self._pool = (
            ConnectionPool(
                conninfo=database_url,
                min_size=min_size,
                max_size=max_size,
                timeout=timeout_seconds,
            )
            if ConnectionPool is not None and database_url
            else None
        )

    @contextmanager
    def connection(
        self,
        *,
        autocommit: bool = False,
        row_factory: Any | None = None,
    ) -> Iterator[Any]:
        if self._pool is None:
            with psycopg.connect(self._database_url) as conn:
                conn.autocommit = autocommit
                if row_factory is not None:
                    conn.row_factory = row_factory
                yield conn
            return

        with self._pool.connection() as conn:
            conn.autocommit = autocommit
            if row_factory is not None:
                conn.row_factory = row_factory
            yield conn
