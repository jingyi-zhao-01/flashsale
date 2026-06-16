from datetime import datetime, timedelta, timezone
import logging
import time

from fastapi import HTTPException

from app.application.commands import CreateOrderCommand, PaymentWebhookCommand
from app.application.results import ExpireOrdersResult
from app.config import DATABASE_UNAVAILABLE_MESSAGE, ORDER_PENDING_TTL_SECONDS
from app.domain.order import Order, OrderItem
from flashsale_shared.observability import start_span
from app.ports.product_reservation_client import ProductReservationClient
from app.ports.reserve_admission_gate import (
    NoOpReserveAdmissionGate,
    ReserveAdmissionGate,
)
from app.ports.terminalization_command_publisher import (
    NoOpTerminalizationCommandPublisher,
    TerminalizationCommandPublisher,
)
from app.ports.unit_of_work import UnitOfWork
from app.ports.user_directory_client import UserDirectoryClient

order_logger = logging.getLogger("order-service.orders")


class CreateOrderUseCase:
    def __init__(
        self,
        uow: UnitOfWork,
        users: UserDirectoryClient,
        products: ProductReservationClient,
        admission: ReserveAdmissionGate | None = None,
        terminalization: TerminalizationCommandPublisher | None = None,
    ) -> None:
        self._uow = uow
        self._users = users
        self._products = products
        self._admission: ReserveAdmissionGate = (
            admission if admission is not None else NoOpReserveAdmissionGate()
        )
        self._terminalization = (
            terminalization
            if terminalization is not None
            else NoOpTerminalizationCommandPublisher()
        )

    def create_order(self, command: CreateOrderCommand) -> Order:
        total_start = time.perf_counter()
        user_validate_ms = 0.0
        reserve_ms = 0.0
        order: Order | None = None
        result = "unknown"
        if not command.items:
            raise HTTPException(status_code=400, detail="order items cannot be empty")
        if command.idempotency_key:
            existing = self._uow.orders.get_by_idempotency_key(command.idempotency_key)
            if existing:
                order_logger.info(
                    "event=order_service_create_order_timing order_id=%s idempotency_hit=true "
                    "user_validate_ms=0.00 reserve_ms=0.00 total_order_ms=0.00 result=idempotency_replay",
                    existing.id,
                )
                return existing

        with start_span(
            "order-service",
            "validate user",
            attributes={"flashsale.user_id": command.user_id},
        ):
            user_validate_start = time.perf_counter()
            self._users.ensure_user_exists(command.user_id)
            user_validate_ms = (time.perf_counter() - user_validate_start) * 1000

        product_ids = [pid for pid, _ in command.items]
        admission_held: list[int] = []
        order_items: list[OrderItem] = []
        reservation_ids: list[int] = []
        total_amount = 0.0

        try:
            with start_span(
                "order-service",
                "reserve admission gate",
                attributes={"flashsale.product_ids": ",".join(str(p) for p in product_ids)},
            ):
                admission_held = self._admission.acquire(product_ids)

            for product_id, quantity in command.items:
                with start_span(
                    "order-service",
                    "reserve inventory",
                    attributes={
                        "flashsale.product_id": product_id,
                        "flashsale.quantity": quantity,
                    },
                ):
                    reserve_start = time.perf_counter()
                    unit_price, reserved_qty, reservation_id = self._products.reserve(
                        product_id=product_id,
                        quantity=quantity,
                    )
                    reserve_ms += (time.perf_counter() - reserve_start) * 1000
                reservation_ids.append(reservation_id)
                item = OrderItem(
                    product_id=product_id,
                    quantity=reserved_qty,
                    unit_price=unit_price,
                )
                total_amount += item.line_total
                order_items.append(item)
            with start_span(
                "order-service",
                "persist order and enqueue terminalization",
                attributes={"flashsale.reservation_count": len(reservation_ids)},
            ):
                order = self._uow.create_order(
                    user_id=command.user_id,
                    total_amount=total_amount,
                    items=order_items,
                    reservation_ids=reservation_ids,
                    idempotency_key=command.idempotency_key,
                )
                self._terminalization.publish(
                    order_id=order.id,
                    reservation_ids=reservation_ids,
                    action="confirm",
                )
            result = "success"
            return order
        except RuntimeError as exc:
            result = "order_persistence_failed"
            self._products.release(reservation_ids)
            raise HTTPException(status_code=503, detail="order persistence failed") from exc
        except HTTPException:
            result = "http_error"
            self._products.release(reservation_ids)
            raise
        except Exception as exc:
            result = "error"
            self._products.release(reservation_ids)
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc
        finally:
            if admission_held:
                self._admission.release(admission_held)
            total_order_ms = (time.perf_counter() - total_start) * 1000
            order_logger.info(
                "event=order_service_create_order_timing order_id=%s idempotency_hit=false "
                "user_validate_ms=%.2f reserve_ms=%.2f total_order_ms=%.2f result=%s",
                order.id if order is not None else None,
                user_validate_ms,
                reserve_ms,
                total_order_ms,
                result,
            )

    def process_payment_webhook(self, command: PaymentWebhookCommand) -> Order:
        try:
            order = self._uow.orders.get(command.order_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc
        if not order:
            raise HTTPException(status_code=404, detail="order not found")
        if order.status == "confirmed" and order.payment_status == "succeeded":
            return order
        if order.status in {"expired", "failed", "cancelled"}:
            return order
        try:
            return self._require_finalized(
                order.id,
                list(order.reservation_ids),
                "succeeded",
            )
        except HTTPException:
            self._enqueue_cancellation(order.id, list(order.reservation_ids), "failed")
            raise
        except Exception as exc:
            self._enqueue_cancellation(order.id, list(order.reservation_ids), "failed")
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc

    def expire_orders(self) -> ExpireOrdersResult:
        expires_before = datetime.now(timezone.utc) - timedelta(
            seconds=ORDER_PENDING_TTL_SECONDS
        )
        stale_orders = self._uow.orders.list_stale(expires_before)
        expired_count = 0
        for order in stale_orders:
            if order.reservation_ids:
                updated = self._uow.finalize_order(
                    order_id=order.id,
                    status="expired",
                    payment_status="cancelled",
                    action="cancel",
                    reservation_ids=list(order.reservation_ids),
                )
                if updated:
                    self._terminalization.publish(
                        order_id=order.id,
                        reservation_ids=list(order.reservation_ids),
                        action="cancel",
                    )
            else:
                updated = self._uow.orders.update_state(
                    order.id,
                    status="expired",
                    payment_status="cancelled",
                )
            if updated:
                expired_count += 1
        return ExpireOrdersResult(expired_count=expired_count)

    def _require_finalized(
        self,
        order_id: int,
        reservation_ids: list[int],
        payment_status: str,
    ) -> Order:
        order = self._uow.finalize_order(
            order_id=order_id,
            status="confirmed",
            payment_status=payment_status,
            action="confirm",
            reservation_ids=reservation_ids,
        )
        if not order:
            raise HTTPException(status_code=404, detail="order not found")
        self._terminalization.publish(
            order_id=order.id,
            reservation_ids=reservation_ids,
            action="confirm",
        )
        return order

    def _enqueue_cancellation(
        self,
        order_id: int,
        reservation_ids: list[int],
        status: str,
    ) -> None:
        if not reservation_ids:
            self._uow.orders.update_state(
                order_id,
                status=status,
                payment_status="cancelled",
            )
            return
        order = self._uow.finalize_order(
            order_id=order_id,
            status=status,
            payment_status="cancelled",
            action="cancel",
            reservation_ids=reservation_ids,
        )
        if order:
            self._terminalization.publish(
                order_id=order_id,
                reservation_ids=reservation_ids,
                action="cancel",
            )
