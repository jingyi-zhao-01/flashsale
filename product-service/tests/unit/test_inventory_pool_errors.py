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


class FakePoolTimeout(RuntimeError):
    pass


class _RaisingConnection:
    def __enter__(self) -> "_RaisingConnection":
        raise FakePoolTimeout("pool timeout")

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


class _FakePool:
    def connection(self, **kwargs) -> _RaisingConnection:
        return _RaisingConnection()


class InventoryPoolErrorTest(unittest.TestCase):
    def test_reserve_with_reservation_propagates_pool_timeout(self) -> None:
        engine = InventoryReserveEngine(
            database_url="postgresql://example",
            lock_mode="pessimistic",
            retry_limit=1,
            pool=_FakePool(),
        )

        with patch(
            "app.locking.inventory.POOL_ERRORS",
            (FakePoolTimeout,),
        ):
            with self.assertRaises(FakePoolTimeout):
                engine.reserve_with_reservation(
                    product_id=1,
                    quantity=1,
                    reservation_ttl_seconds=300,
                )


if __name__ == "__main__":
    unittest.main()
