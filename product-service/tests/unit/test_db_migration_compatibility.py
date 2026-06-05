import sys
import types
import unittest
from unittest.mock import patch

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.repositories import PostgresProductRepository


class _FakeCursor:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, statement: str, params=None) -> None:
        self._statements.append(" ".join(statement.split()))


class _FakeConnection:
    def __init__(self, statements: list[str]) -> None:
        self._statements = statements

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return _FakeCursor(self._statements)


class ProductServiceMigrationCompatibilityTest(unittest.TestCase):
    def test_init_db_keeps_product_and_reservation_tables(self) -> None:
        statements: list[str] = []
        repository = PostgresProductRepository("postgresql://example")

        with patch(
            "app.repositories.psycopg.connect",
            return_value=_FakeConnection(statements),
        ):
            repository.init_db()

        joined = "\n".join(statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS products", joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS reservations", joined)
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS reservations_status_expires_at_idx",
            joined,
        )
        self.assertIn("expires_at TIMESTAMPTZ NULL", joined)


if __name__ == "__main__":
    unittest.main()
