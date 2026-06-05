from dataclasses import replace

from .order import Order
from .reservation_terminalization_task import ReservationTerminalizationTask
from .statuses import OrderStatus, PaymentStatus, TaskStatus

ALLOWED_ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    "pending": {"confirmed", "failed", "cancelled", "expired"},
    "confirmed": {"confirmed"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
    "expired": {"expired"},
}

ALLOWED_PAYMENT_TRANSITIONS: dict[PaymentStatus, set[PaymentStatus]] = {
    "pending": {"succeeded", "cancelled"},
    "succeeded": {"succeeded"},
    "cancelled": {"cancelled"},
}


def transition_order(
    order: Order,
    status: OrderStatus,
    payment_status: PaymentStatus | None = None,
) -> Order:
    next_status = _transition_status(order.status, status)
    next_payment = order.payment_status
    if payment_status is not None:
        next_payment = _transition_payment(order.payment_status, payment_status)
    return replace(order, status=next_status, payment_status=next_payment)


def claim_task(task: ReservationTerminalizationTask) -> ReservationTerminalizationTask:
    return replace(task, status="processing", attempt_count=task.attempt_count + 1)


def set_task_status(
    task: ReservationTerminalizationTask,
    status: TaskStatus,
    last_error: str | None = None,
) -> ReservationTerminalizationTask:
    return replace(task, status=status, last_error=last_error)


def _transition_status(current: OrderStatus, target: OrderStatus) -> OrderStatus:
    if target == current:
        return current
    if target not in ALLOWED_ORDER_TRANSITIONS[current]:
        raise ValueError(f"invalid order status transition: {current} -> {target}")
    return target


def _transition_payment(
    current: PaymentStatus,
    target: PaymentStatus,
) -> PaymentStatus:
    if target == current:
        return current
    if target not in ALLOWED_PAYMENT_TRANSITIONS[current]:
        raise ValueError(
            f"invalid payment status transition: {current} -> {target}"
        )
    return target
