import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

from app.models import ProductCreate, ReserveRequest
from app.repositories import InMemoryProductRepository
from app.service import ProductService


class ProductReservationLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryProductRepository()
        self.service = ProductService(
            repository=self.repository,
            logger=__import__("logging").getLogger("test-product-service"),
            storage="test",
        )
        self.product = self.service.create_product(
            ProductCreate(name="flashsale item", price=9.99, stock=5)
        )

    def test_reserve_creates_pending_reservation_and_reduces_available_stock(self) -> None:
        reservation = self.service.reserve_product(self.product.id, ReserveRequest(quantity=2))

        self.assertEqual(reservation.product_id, self.product.id)
        self.assertEqual(reservation.quantity, 2)
        self.assertEqual(reservation.status, "reserved")
        self.assertIsNotNone(reservation.expires_at)
        self.assertEqual(self.service.get_product(self.product.id).stock, 3)

    def test_confirm_is_idempotent_and_keeps_stock_consumed(self) -> None:
        reservation = self.service.reserve_product(self.product.id, ReserveRequest(quantity=2))

        confirmed_once = self.service.confirm_reservation(reservation.reservation_id)
        confirmed_twice = self.service.confirm_reservation(reservation.reservation_id)

        self.assertEqual(confirmed_once.status, "confirmed")
        self.assertEqual(confirmed_twice.status, "confirmed")
        self.assertEqual(self.service.get_product(self.product.id).stock, 3)

    def test_reserve_out_of_stock_returns_conflict(self) -> None:
        with self.assertRaises(HTTPException) as exc_info:
            self.service.reserve_product(self.product.id, ReserveRequest(quantity=6))

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(self.service.get_product(self.product.id).stock, 5)

    def test_cancel_restores_stock_and_is_idempotent(self) -> None:
        reservation = self.service.reserve_product(self.product.id, ReserveRequest(quantity=2))

        cancelled_once = self.service.cancel_reservation(reservation.reservation_id)
        cancelled_twice = self.service.cancel_reservation(reservation.reservation_id)

        self.assertEqual(cancelled_once.status, "cancelled")
        self.assertEqual(cancelled_twice.status, "cancelled")
        self.assertEqual(self.service.get_product(self.product.id).stock, 5)

    def test_expire_releases_only_elapsed_reservations(self) -> None:
        reservation = self.service.reserve_product(self.product.id, ReserveRequest(quantity=2))
        self.repository._reservations[reservation.reservation_id] = reservation.model_copy(
            update={
                "expires_at": (
                    datetime.now(timezone.utc) - timedelta(seconds=1)
                ).isoformat()
            }
        )

        result = self.service.expire_reservations()

        self.assertEqual(result.expired_count, 1)
        self.assertEqual(self.service.get_product(self.product.id).stock, 5)
        self.assertEqual(
            self.repository._reservations[reservation.reservation_id].status, "expired"
        )


if __name__ == "__main__":
    unittest.main()
