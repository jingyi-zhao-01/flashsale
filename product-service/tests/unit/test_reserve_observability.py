import sys
import types
import unittest
from unittest.mock import patch

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.Error = RuntimeError
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.locking.inventory import InventoryReserveEngine


class _FakeCursor:
    def __init__(self) -> None:
        self._fetches = [
            {"price": 9.99},
            {
                "reservation_id": 101,
                "product_id": 7,
                "quantity": 1,
                "unit_price": 9.99,
                "status": "reserved",
                "expires_at": None,
            },
        ]

    def __enter__(self) -> "_FakeCursor":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute(self, query: str, params: object | None = None) -> None:
        return None

    def fetchone(self) -> object:
        return self._fetches.pop(0)


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_obj = _FakeCursor()

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def cursor(self) -> _FakeCursor:
        return self.cursor_obj

    def commit(self) -> None:
        return None


class _FakePool:
    def connection(self, **kwargs) -> _FakeConnection:
        return _FakeConnection()


class ProductReserveObservabilityTest(unittest.TestCase):
    def test_optimistic_reserve_logs_stage_and_attempt_timings(self) -> None:
        engine = InventoryReserveEngine(
            database_url="postgresql://example",
            lock_mode="optimistic",
            retry_limit=1,
            slow_ms_threshold=200,
            pool=_FakePool(),
        )

        with (
            patch("app.locking.inventory.lock_logger.info") as info_log,
            patch("app.locking.inventory.lock_logger.warning") as warning_log,
            patch(
                "app.locking.inventory.time.perf_counter",
                side_effect=[
                    0.00,
                    0.00,
                    0.00,
                    0.01,
                    0.01,
                    0.26,
                    0.26,
                    0.27,
                    0.27,
                    0.28,
                    0.28,
                    0.28,
                    0.28,
                ],
            ),
        ):
            reservation = engine.reserve_with_reservation(
                product_id=7,
                quantity=1,
                reservation_ttl_seconds=300,
            )

        self.assertEqual(reservation["reservation_id"], 101)
        info_messages = [call.args[0] for call in info_log.call_args_list]
        warning_messages = [call.args[0] for call in warning_log.call_args_list]

        self.assertTrue(
            any("event=product_service_reserve_stage" in message for message in info_messages)
        )
        self.assertTrue(
            any("event=product_service_reserve_attempt" in message for message in info_messages)
        )
        self.assertTrue(
            any(
                "event=product_service_reserve_stage_slow" in message
                for message in warning_messages
            )
        )
        self.assertTrue(
            any(
                "event=product_service_reserve_attempt_slow" in message
                for message in warning_messages
            )
        )


if __name__ == "__main__":
    unittest.main()
