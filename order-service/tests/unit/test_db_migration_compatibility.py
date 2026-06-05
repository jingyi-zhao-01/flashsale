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

from app.adapters.order_postgres_unit_of_work import OrderPostgresUnitOfWork


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


class OrderServiceMigrationCompatibilityTest(unittest.TestCase):
    def test_init_db_keeps_backward_compatible_order_columns(self) -> None:
        statements: list[str] = []
        repository = OrderPostgresUnitOfWork("postgresql://example")

        with patch(
            "app.adapters.order_postgres_unit_of_work.psycopg.connect",
            return_value=_FakeConnection(statements),
        ):
            repository.init_db()

        joined = "\n".join(statements)
        self.assertIn("CREATE TABLE IF NOT EXISTS orders", joined)
        self.assertIn(
            "ADD COLUMN IF NOT EXISTS payment_status TEXT NOT NULL DEFAULT 'pending'",
            joined,
        )
        self.assertIn("ADD COLUMN IF NOT EXISTS idempotency_key TEXT NULL", joined)
        self.assertIn("ADD COLUMN IF NOT EXISTS reservation_ids_json JSONB", joined)
        self.assertIn("CREATE UNIQUE INDEX IF NOT EXISTS orders_idempotency_key_idx", joined)
        self.assertIn("CREATE INDEX IF NOT EXISTS orders_pending_created_at_idx", joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS order_terminalization_tasks", joined)
        self.assertIn("CREATE INDEX IF NOT EXISTS order_terminalization_tasks_ready_idx", joined)
        self.assertIn("CREATE TABLE IF NOT EXISTS order_terminalization_task_events", joined)
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS order_terminalization_task_events_lookup_idx",
            joined,
        )


if __name__ == "__main__":
    unittest.main()
