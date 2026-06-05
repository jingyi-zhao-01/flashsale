import unittest
from unittest.mock import patch

from flashsale_shared.db_pool import DatabasePool


class _FakeConnection:
    def __init__(self) -> None:
        self.autocommit = False
        self.row_factory_values: list[object] = []

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    @property
    def row_factory(self) -> object:
        return self.row_factory_values[-1] if self.row_factory_values else "default"

    @row_factory.setter
    def row_factory(self, value: object) -> None:
        self.row_factory_values.append(value)


class _FakeConnectionPool:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        self.connection_obj = _FakeConnection()

    def connection(self) -> _FakeConnection:
        return self.connection_obj


class DatabasePoolTest(unittest.TestCase):
    def test_connection_does_not_override_row_factory_when_not_requested(self) -> None:
        with patch("flashsale_shared.db_pool.ConnectionPool", _FakeConnectionPool):
            pool = DatabasePool(
                database_url="postgresql://example",
                min_size=1,
                max_size=2,
                timeout_seconds=5,
            )

        with pool.connection(autocommit=True) as conn:
            self.assertTrue(conn.autocommit)

        self.assertEqual(conn.row_factory_values, [])

    def test_connection_sets_row_factory_when_requested(self) -> None:
        sentinel = object()

        with patch("flashsale_shared.db_pool.ConnectionPool", _FakeConnectionPool):
            pool = DatabasePool(
                database_url="postgresql://example",
                min_size=1,
                max_size=2,
                timeout_seconds=5,
            )

        with pool.connection(row_factory=sentinel) as conn:
            self.assertIs(conn.row_factory, sentinel)

        self.assertEqual(conn.row_factory_values, [sentinel])


if __name__ == "__main__":
    unittest.main()
