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
from app.domain.order import Order, OrderItem
from app.domain.reservation_terminalization_task import (
    ReservationTerminalizationTask,
    ReservationTerminalizationTaskEvent,
)
from app.domain.state_machines import claim_task, set_task_status, transition_order


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


class FakeTerminalizationTaskRepository:
    def __init__(self) -> None:
        self._tasks: dict[int, ReservationTerminalizationTask] = {}
        self._events: list[ReservationTerminalizationTaskEvent] = []
        self._counter = count(1)

    def enqueue(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: str,
        now: datetime,
    ) -> None:
        for reservation_id in reservation_ids:
            task_id = next(self._counter)
            self._tasks[task_id] = ReservationTerminalizationTask(
                task_id=task_id,
                order_id=order_id,
                reservation_id=reservation_id,
                action=action,
                status="queued",
                attempt_count=0,
                available_at=now,
                created_at=now,
                last_error=None,
            )

    def claim_ready(
        self,
        limit: int,
        available_before: datetime,
    ) -> list[ReservationTerminalizationTask]:
        claimed: list[ReservationTerminalizationTask] = []
        for task_id in sorted(self._tasks):
            task = self._tasks[task_id]
            if len(claimed) >= limit:
                break
            if task.status not in {"queued", "retrying"}:
                continue
            if task.available_at > available_before:
                continue
            updated = claim_task(task)
            self._tasks[task_id] = updated
            claimed.append(updated)
        return claimed

    def mark_succeeded(self, task_id: int) -> None:
        self._tasks[task_id] = set_task_status(self._tasks[task_id], "succeeded", None)

    def mark_retrying(
        self,
        task_id: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        task = set_task_status(self._tasks[task_id], "retrying", last_error)
        self._tasks[task_id] = replace(task, available_at=available_at)

    def record_event(
        self,
        task_id: int,
        order_id: int,
        reservation_id: int,
        action: str,
        event_type: str,
        attempt_count: int,
        last_error: str | None = None,
    ) -> None:
        self._events.append(
            ReservationTerminalizationTaskEvent(
                task_id=task_id,
                order_id=order_id,
                reservation_id=reservation_id,
                action=action,
                event_type=event_type,
                attempt_count=attempt_count,
                occurred_at=datetime.now(timezone.utc),
                last_error=last_error,
            )
        )


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.orders = FakeOrderRepository()
        self.tasks = FakeTerminalizationTaskRepository()

    def init_db(self) -> None:
        return

    def is_healthy(self) -> bool:
        return True

    def reset(self) -> None:
        self.orders = FakeOrderRepository()
        self.tasks = FakeTerminalizationTaskRepository()

    def create_order_and_enqueue_terminalization(
        self,
        user_id: int,
        total_amount: float,
        items: list[OrderItem],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
        action: str = "confirm",
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
        self.tasks.enqueue(
            order_id=order.id,
            reservation_ids=reservation_ids,
            action=action,
            now=datetime.now(timezone.utc),
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
        self.tasks.enqueue(
            order_id=order_id,
            reservation_ids=reservation_ids,
            action=action,
            now=datetime.now(timezone.utc),
        )
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
        self.create_orders = CreateOrderUseCase(
            uow=self.uow,
            users=FakeUserDirectoryClient(),
            products=self.products,
        )
        self.process_tasks = ProcessTerminalizationTaskUseCase(
            uow=self.uow,
            products=self.products,
        )
        self.command = CreateOrderCommand(
            user_id=1,
            items=((42, 1),),
        )

    def test_successful_order_is_confirmed(self) -> None:
        order = self.create_orders.create_order(self.command)
        self.assertEqual(order.status, "pending")
        self.assertEqual(order.payment_status, "pending")
        worker_result = self.process_tasks.process()

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
        worker_result = self.process_tasks.process()

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
        self.process_tasks.process()

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
        worker_result = self.process_tasks.process()
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
        self.process_tasks.process()

        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "confirmed")
        self.assertEqual(second.payment_status, "succeeded")
        self.assertEqual(self.products.stock, 4)
        self.assertEqual(self.products.reservations[1], "confirmed")
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
        self.process_tasks.process()

        self.assertEqual(raced.status, "expired")
        self.assertEqual(raced.payment_status, "cancelled")
        self.assertEqual(self.products.stock, 5)
        self.assertEqual(self.products.reservations[1], "cancelled")
        self.assertEqual(self.products.cancel_calls, 1)


if __name__ == "__main__":
    unittest.main()
