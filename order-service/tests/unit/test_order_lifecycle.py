import unittest
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from itertools import count

from fastapi import HTTPException

from app.application.commands import CreateOrderCommand, PaymentWebhookCommand
from app.application.create_order_use_case import CreateOrderUseCase
from app.application.process_terminalization_task_use_case import (
    ProcessTerminalizationTaskUseCase,
)
from app.application.results import ProcessTerminalizationTasksResult
from app.domain.order import Order, OrderItem
from app.domain.state_machines import transition_order
from app.domain.terminalization_command import (
    TerminalizationCommand,
    new_terminalization_command,
)


class FakeOrderRepository:
    def __init__(self) -> None:
        self._orders: dict[int, Order] = {}
        self._idempotency_keys: dict[str, int] = {}
        self._counter = count(1)

    def create(
        self,
        user_id: int,
        total_amount: float,
        items: list[OrderItem],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
        status: str = "pending",
        payment_status: str = "pending",
    ) -> Order:
        if idempotency_key and idempotency_key in self._idempotency_keys:
            return self._orders[self._idempotency_keys[idempotency_key]]
        order_id = next(self._counter)
        order = Order(
            id=order_id,
            user_id=user_id,
            created_at=datetime.now(timezone.utc),
            total_amount=round(total_amount, 2),
            status=status,
            payment_status=payment_status,
            idempotency_key=idempotency_key,
            items=tuple(items),
            reservation_ids=tuple(reservation_ids),
        )
        self._orders[order_id] = order
        if idempotency_key:
            self._idempotency_keys[idempotency_key] = order_id
        return order

    def update_state(
        self,
        order_id: int,
        status: str,
        payment_status: str | None = None,
    ) -> Order | None:
        order = self._orders.get(order_id)
        if not order:
            return None
        updated = transition_order(order, status, payment_status)
        self._orders[order_id] = updated
        return updated

    def get(self, order_id: int) -> Order | None:
        return self._orders.get(order_id)

    def get_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        order_id = self._idempotency_keys.get(idempotency_key)
        return self._orders.get(order_id) if order_id is not None else None

    def list_all(self) -> list[Order]:
        return list(self._orders.values())

    def list_stale(self, expires_before: datetime) -> list[Order]:
        return [
            order
            for order in self._orders.values()
            if order.status == "pending" and order.created_at <= expires_before
        ]

    def replace_order(self, order: Order) -> None:
        self._orders[order.id] = order

    def override_created_at(self, order_id: int, created_at: datetime) -> None:
        self._orders[order_id] = replace(
            self._orders[order_id],
            created_at=created_at,
        )


class FakeTerminalizationPublisher:
    def __init__(self) -> None:
        self.commands: list[TerminalizationCommand] = []
        self.retries: list[TerminalizationCommand] = []
        self.dead_letters: list[TerminalizationCommand] = []

    def publish(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: str,
    ) -> None:
        for reservation_id in reservation_ids:
            self.commands.append(
                new_terminalization_command(
                    order_id=order_id,
                    reservation_id=reservation_id,
                    action=action,
                )
            )

    def publish_retry(self, command: TerminalizationCommand, error: str) -> None:
        retry = TerminalizationCommand(
            event_id=command.event_id,
            order_id=command.order_id,
            reservation_id=command.reservation_id,
            action=command.action,
            attempt=command.attempt + 1,
            created_at=command.created_at,
            idempotency_key=command.idempotency_key,
        )
        self.retries.append(retry)
        self.commands.append(retry)

    def publish_dead_letter(self, command: TerminalizationCommand, error: str) -> None:
        self.dead_letters.append(command)


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.orders = FakeOrderRepository()

    def init_db(self) -> None:
        return

    def is_healthy(self) -> bool:
        return True

    def reset(self) -> None:
        self.orders = FakeOrderRepository()

    def create_order(
        self,
        user_id: int,
        total_amount: float,
        items: list[OrderItem],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
    ) -> Order:
        if idempotency_key:
            existing = self.orders.get_by_idempotency_key(idempotency_key)
            if existing:
                return existing
        order = self.orders.create(
            user_id=user_id,
            total_amount=total_amount,
            items=items,
            reservation_ids=reservation_ids,
            idempotency_key=idempotency_key,
            status="pending",
            payment_status="pending",
        )
        return order

    def finalize_order(
        self,
        order_id: int,
        status: str,
        payment_status: str,
        action: str,
        reservation_ids: list[int],
    ) -> Order | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        updated = transition_order(order, status, payment_status)
        self.orders.replace_order(updated)
        return updated


class FakeUserDirectoryClient:
    def ensure_user_exists(self, user_id: int) -> None:
        return


class FakeProductReservationClient:
    def __init__(self, stock: int, price: float, confirm_status_code: int = 200) -> None:
        self.initial_stock = stock
        self.stock = stock
        self.price = price
        self.confirm_status_code = confirm_status_code
        self.next_reservation_id = 1
        self.reservations: dict[int, str] = {}
        self.confirm_calls = 0
        self.cancel_calls = 0

    def reserve(self, product_id: int, quantity: int) -> tuple[float, int, int]:
        if self.stock < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"insufficient stock for product {product_id}",
            )
        self.stock -= quantity
        reservation_id = self.next_reservation_id
        self.next_reservation_id += 1
        self.reservations[reservation_id] = "reserved"
        return self.price, quantity, reservation_id

    def release(self, reservation_ids: list[int]) -> None:
        for reservation_id in reversed(reservation_ids):
            if self.reservations.get(reservation_id) == "reserved":
                self.reservations[reservation_id] = "cancelled"
                self.stock += 1
                self.cancel_calls += 1

    def terminalize(self, reservation_id: int, action: str) -> tuple[bool, str | None]:
        if action == "confirm":
            if self.reservations.get(reservation_id) == "confirmed":
                return True, None
            self.confirm_calls += 1
            if self.confirm_status_code >= 400:
                return False, f"status_code={self.confirm_status_code}"
            self.reservations[reservation_id] = "confirmed"
            return True, None
        self.cancel_calls += 1
        if self.reservations.get(reservation_id) == "reserved":
            self.reservations[reservation_id] = "cancelled"
            self.stock += 1
        return True, None


class OrderRepositoryStateMachineTest(unittest.TestCase):
    def test_order_status_transitions_pending_to_confirmed(self) -> None:
        uow = FakeUnitOfWork()
        order = uow.orders.create(
            user_id=1,
            total_amount=9.99,
            items=[],
            reservation_ids=[],
            status="pending",
        )

        confirmed = uow.orders.update_state(order.id, "confirmed")

        self.assertIsNotNone(confirmed)
        self.assertEqual(order.status, "pending")
        self.assertEqual(confirmed.status, "confirmed")
        self.assertEqual(confirmed.payment_status, "pending")

    def test_order_status_rejects_invalid_transition(self) -> None:
        uow = FakeUnitOfWork()
        order = uow.orders.create(
            user_id=1,
            total_amount=9.99,
            items=[],
            reservation_ids=[],
            status="pending",
        )
        uow.orders.update_state(order.id, "confirmed", payment_status="succeeded")

        with self.assertRaises(ValueError):
            uow.orders.update_state(order.id, "failed", payment_status="cancelled")


class OrderServiceLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.uow = FakeUnitOfWork()
        self.products = FakeProductReservationClient(stock=5, price=9.99)
        self.terminalization = FakeTerminalizationPublisher()
        self.create_orders = CreateOrderUseCase(
            uow=self.uow,
            users=FakeUserDirectoryClient(),
            products=self.products,
            terminalization=self.terminalization,
        )
        self.process_tasks = ProcessTerminalizationTaskUseCase(
            uow=self.uow,
            products=self.products,
            terminalization=self.terminalization,
        )
        self.command = CreateOrderCommand(
            user_id=1,
            items=((42, 1),),
        )

    def _drain_one_terminalization(self) -> ProcessTerminalizationTasksResult:
        command = self.terminalization.commands.pop(0)
        before_retries = len(self.terminalization.retries)
        self.process_tasks.process_kafka_command(command)
        succeeded = 0 if len(self.terminalization.retries) > before_retries else 1
        retrying = 1 if len(self.terminalization.retries) > before_retries else 0
        return ProcessTerminalizationTasksResult(1, succeeded, retrying)

    def test_successful_order_is_confirmed(self) -> None:
        order = self.create_orders.create_order(self.command)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        worker_result = self._drain_one_terminalization()

        persisted = self.uow.orders.get(order.id)
        self.assertIsNotNone(persisted)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(persisted.status, "confirmed")
        self.assertEqual(persisted.payment_status, "succeeded")
        self.assertEqual(worker_result.succeeded_count, 1)
        self.assertEqual(self.products.stock, 4)
        self.assertEqual(self.products.reservations[1], "confirmed")
        self.assertEqual(self.products.confirm_calls, 1)

    def test_confirm_failure_retries_in_background_without_failing_request(self) -> None:
        self.products.confirm_status_code = 502

        order = self.create_orders.create_order(self.command)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        worker_result = self._drain_one_terminalization()

        self.assertEqual(order.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        self.assertEqual(worker_result.claimed_count, 1)
        self.assertEqual(worker_result.retrying_count, 1)
        self.assertEqual(self.uow.orders.list_all()[0].status, "pending")
        self.assertEqual(self.products.stock, 4)
        self.assertEqual(self.products.reservations[1], "reserved")
        self.assertEqual(self.products.confirm_calls, 1)

    def test_out_of_stock_returns_conflict_and_does_not_persist_order(self) -> None:
        self.products.stock = 0

        with self.assertRaises(HTTPException) as exc_info:
            self.create_orders.create_order(self.command)

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(self.uow.orders.list_all(), [])
        self.assertEqual(self.products.stock, 0)

    def test_idempotency_key_returns_existing_order_without_second_reserve(self) -> None:
        command = CreateOrderCommand(
            user_id=1,
            idempotency_key="flashsale-key-1",
            items=((42, 1),),
        )

        first = self.create_orders.create_order(command)
        second = self.create_orders.create_order(command)
        self._drain_one_terminalization()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "pending")
        self.assertEqual(second.status, "pending")
        self.assertEqual(self.products.stock, 4)
        self.assertEqual(len(self.products.reservations), 1)
        self.assertEqual(self.products.confirm_calls, 1)

    def test_expire_orders_cancels_pending_order_and_releases_inventory(self) -> None:
        self.products.stock = 4
        self.products.reservations[1] = "reserved"
        order = self.uow.orders.create(
            user_id=1,
            total_amount=9.99,
            items=[],
            reservation_ids=[1],
            idempotency_key="stale-key",
            status="pending",
            payment_status="pending",
        )
        self.uow.orders.override_created_at(
            order.id,
            datetime.now(timezone.utc) - timedelta(seconds=301),
        )

        result = self.create_orders.expire_orders()
        worker_result = self._drain_one_terminalization()
        expired_order = self.uow.orders.get(order.id)

        self.assertEqual(result.expired_count, 1)
        self.assertIsNotNone(expired_order)
        self.assertEqual(expired_order.status, "expired")
        self.assertEqual(expired_order.payment_status, "cancelled")
        self.assertEqual(worker_result.succeeded_count, 1)
        self.assertEqual(self.products.stock, 5)
        self.assertEqual(self.products.reservations[1], "cancelled")
        self.assertEqual(self.products.cancel_calls, 1)

    def test_duplicate_payment_webhook_is_idempotent(self) -> None:
        self.products.stock = 4
        self.products.reservations[1] = "reserved"
        order = self.uow.orders.create(
            user_id=1,
            total_amount=9.99,
            items=[],
            reservation_ids=[1],
            idempotency_key="payment-order",
            status="pending",
            payment_status="pending",
        )
        command = PaymentWebhookCommand(order_id=order.id, event_id="evt-1")

        first = self.create_orders.process_payment_webhook(command)
        second = self.create_orders.process_payment_webhook(command)
        self._drain_one_terminalization()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "confirmed")
        self.assertEqual(second.payment_status, "succeeded")
        self.assertEqual(self.products.stock, 4)
        self.assertEqual(self.products.reservations[1], "confirmed")
        self.assertEqual(self.products.confirm_calls, 1)

    def test_kafka_terminalization_command_confirms_order_once(self) -> None:
        order = self.create_orders.create_order(self.command)
        command = self.terminalization.commands[0]

        self.process_tasks.process_kafka_command(command)
        self.process_tasks.process_kafka_command(command)

        persisted = self.uow.orders.get(order.id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.status, "confirmed")
        self.assertEqual(persisted.payment_status, "succeeded")
        self.assertEqual(self.products.confirm_calls, 1)

    def test_payment_success_after_timeout_race_keeps_order_expired(self) -> None:
        self.products.stock = 4
        self.products.reservations[1] = "reserved"
        order = self.uow.orders.create(
            user_id=1,
            total_amount=9.99,
            items=[],
            reservation_ids=[1],
            idempotency_key="timeout-race",
            status="pending",
            payment_status="pending",
        )
        self.uow.orders.override_created_at(
            order.id,
            datetime.now(timezone.utc) - timedelta(seconds=301),
        )

        self.create_orders.expire_orders()
        raced = self.create_orders.process_payment_webhook(
            PaymentWebhookCommand(order_id=order.id, event_id="evt-timeout")
        )
        self._drain_one_terminalization()

        self.assertEqual(raced.status, "expired")
        self.assertEqual(raced.payment_status, "cancelled")
        self.assertEqual(self.products.stock, 5)
        self.assertEqual(self.products.reservations[1], "cancelled")
        self.assertEqual(self.products.cancel_calls, 1)


if __name__ == "__main__":
    unittest.main()
