import sys
import types
import unittest
from unittest.mock import patch

psycopg_stub = types.ModuleType("psycopg")
psycopg_errors_stub = types.ModuleType("psycopg.errors")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
psycopg_stub.errors = psycopg_errors_stub
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.errors", psycopg_errors_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.repositories import PostgresUserRepository


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


class UserServiceMigrationCompatibilityTest(unittest.TestCase):
    def test_init_db_creates_schema_and_users_table(self) -> None:
        statements: list[str] = []
        repository = PostgresUserRepository("postgresql://example")

        with patch(
            "app.repositories.psycopg.connect",
            return_value=_FakeConnection(statements),
        ):
            repository.init_db()

        joined = "\n".join(statements)
        self.assertIn('CREATE SCHEMA IF NOT EXISTS "user_service"', joined)
        self.assertIn('SET search_path TO "user_service", public', joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS users", joined)
        self.assertIn("email TEXT NOT NULL UNIQUE", joined)


if __name__ == "__main__":
    unittest.main()
