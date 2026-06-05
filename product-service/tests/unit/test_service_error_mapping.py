import logging
import sys
import types
import unittest

from fastapi import HTTPException

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

import psycopg

from app.models import ReserveRequest
from app.service import ProductService


class PoolTimeout(RuntimeError):
    pass


class FakeLockNotAvailable(RuntimeError):
    pass


class FakeQueryCanceled(RuntimeError):
    pass


class FakeRepository:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def reserve_product(self, product_id: int, quantity: int):
        raise self._exc


class ProductServiceErrorMappingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        psycopg.errors = types.SimpleNamespace(
            LockNotAvailable=FakeLockNotAvailable,
            DeadlockDetected=FakeLockNotAvailable,
            QueryCanceled=FakeQueryCanceled,
        )

    def _build_service(self, exc: Exception) -> ProductService:
        return ProductService(
            repository=FakeRepository(exc),
            logger=logging.getLogger("test-product-service"),
            storage="test",
        )

    def test_pool_timeout_maps_to_503(self) -> None:
        service = self._build_service(PoolTimeout("pool timeout"))

        with self.assertRaises(HTTPException) as exc_info:
            service.reserve_product(1, ReserveRequest(quantity=1))

        self.assertEqual(exc_info.exception.status_code, 503)
        self.assertEqual(
            exc_info.exception.detail, "inventory database pool exhausted"
        )

    def test_lock_contention_maps_to_409(self) -> None:
        service = self._build_service(FakeLockNotAvailable("lock busy"))

        with self.assertRaises(HTTPException) as exc_info:
            service.reserve_product(1, ReserveRequest(quantity=1))

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "inventory is busy, retry later")

    def test_query_timeout_maps_to_504(self) -> None:
        service = self._build_service(FakeQueryCanceled("timeout"))

        with self.assertRaises(HTTPException) as exc_info:
            service.reserve_product(1, ReserveRequest(quantity=1))

        self.assertEqual(exc_info.exception.status_code, 504)
        self.assertEqual(exc_info.exception.detail, "inventory request timed out")


if __name__ == "__main__":
    unittest.main()
