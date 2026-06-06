import logging
import sys
import types
import unittest

from fastapi import HTTPException

# Stub psycopg_pool before app imports, since the real psycopg_pool
# triggers a psycopg version conflict in this test environment.
_psycopg_pool_stub = types.ModuleType("psycopg_pool")
sys.modules["psycopg_pool"] = _psycopg_pool_stub


class FakePoolTimeout(RuntimeError):
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


def _fake_psycopg(**error_classes: type[Exception]) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        errors=types.SimpleNamespace(**error_classes),
        Error=RuntimeError,
    )


class ProductServiceErrorMappingTest(unittest.TestCase):
    def _build_service(self, exc: Exception) -> "ProductService":
        from app.service import ProductService

        return ProductService(
            repository=FakeRepository(exc),
            logger=logging.getLogger("test-product-service"),
            storage="test",
        )

    def test_pool_timeout_maps_to_503(self) -> None:
        import app.service, psycopg_pool

        _orig_psycopg = app.service.psycopg
        try:
            app.service.psycopg = _fake_psycopg()
            psycopg_pool.PoolTimeout = FakePoolTimeout

            from app.models import ReserveRequest

            service = self._build_service(FakePoolTimeout("pool timeout"))

            with self.assertRaises(HTTPException) as exc_info:
                service.reserve_product(1, ReserveRequest(quantity=1))

            self.assertEqual(exc_info.exception.status_code, 503)
            self.assertEqual(exc_info.exception.detail, "inventory database pool exhausted")
        finally:
            app.service.psycopg = _orig_psycopg

    def test_lock_contention_maps_to_409(self) -> None:
        import app.service, psycopg_pool

        _orig_psycopg = app.service.psycopg
        try:
            app.service.psycopg = _fake_psycopg(
                LockNotAvailable=FakeLockNotAvailable,
                DeadlockDetected=FakeLockNotAvailable,
            )
            psycopg_pool.PoolTimeout = FakePoolTimeout

            from app.models import ReserveRequest

            service = self._build_service(FakeLockNotAvailable("lock busy"))

            with self.assertRaises(HTTPException) as exc_info:
                service.reserve_product(1, ReserveRequest(quantity=1))

            self.assertEqual(exc_info.exception.status_code, 409)
            self.assertEqual(exc_info.exception.detail, "inventory is busy, retry later")
        finally:
            app.service.psycopg = _orig_psycopg

    def test_query_timeout_maps_to_504(self) -> None:
        import app.service, psycopg_pool

        _orig_psycopg = app.service.psycopg
        try:
            app.service.psycopg = _fake_psycopg(QueryCanceled=FakeQueryCanceled)
            psycopg_pool.PoolTimeout = FakePoolTimeout

            from app.models import ReserveRequest

            service = self._build_service(FakeQueryCanceled("timeout"))

            with self.assertRaises(HTTPException) as exc_info:
                service.reserve_product(1, ReserveRequest(quantity=1))

            self.assertEqual(exc_info.exception.status_code, 504)
            self.assertEqual(exc_info.exception.detail, "inventory request timed out")
        finally:
            app.service.psycopg = _orig_psycopg


if __name__ == "__main__":
    unittest.main()
