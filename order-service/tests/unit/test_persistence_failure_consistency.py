import unittest

from fastapi import HTTPException

from app.application.commands import CreateOrderCommand
from app.application.create_order_use_case import CreateOrderUseCase


class FakeUserDirectoryClient:
    def ensure_user_exists(self, user_id: int) -> None:
        return


class FakeProductReservationClient:
    def __init__(self, stock: int, price: float) -> None:
        self.initial_stock = stock
        self.stock = stock
        self.price = price
        self.next_reservation_id = 1
        self.reservations: dict[int, str] = {}

    def reserve(self, product_id: int, quantity: int) -> tuple[float, int, int]:
        self.stock -= quantity
        reservation_id = self.next_reservation_id
        self.next_reservation_id += 1
        self.reservations[reservation_id] = "reserved"
        return self.price, quantity, reservation_id

    def release(self, reservation_ids: list[int]) -> None:
        for reservation_id in reservation_ids:
            if self.reservations.get(reservation_id) == "reserved":
                self.reservations[reservation_id] = "cancelled"
                self.stock += 1

    def terminalize(self, reservation_id: int, action: str) -> tuple[bool, str | None]:
        return True, None


class FailingOrdersRepository:
    def create(self, *args, **kwargs):
        raise RuntimeError("simulated persistence failure")

    def get_by_idempotency_key(self, idempotency_key: str):
        return None


class FailingUnitOfWork:
    def __init__(self) -> None:
        self.orders = FailingOrdersRepository()

    def create_order_and_enqueue_terminalization(
        self,
        user_id: int,
        total_amount: float,
        items,
        reservation_ids,
        idempotency_key: str | None = None,
        action: str = "confirm",
    ):
        raise RuntimeError("simulated persistence failure")


class OrderPersistenceFailureConsistencyTest(unittest.TestCase):
    def test_persistence_failure_does_not_consume_inventory(self) -> None:
        products = FakeProductReservationClient(stock=5, price=9.99)
        create_orders = CreateOrderUseCase(
            uow=FailingUnitOfWork(),
            users=FakeUserDirectoryClient(),
            products=products,
        )

        with self.assertRaises(HTTPException) as exc_info:
            create_orders.create_order(
                CreateOrderCommand(user_id=1, items=((42, 1),))
            )

        self.assertEqual(exc_info.exception.status_code, 503)
        self.assertEqual(products.stock, products.initial_stock)


if __name__ == "__main__":
    unittest.main()
