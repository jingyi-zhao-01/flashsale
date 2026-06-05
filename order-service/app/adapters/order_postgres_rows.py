import json
from datetime import datetime
from decimal import Decimal
from typing import Any, cast

from app.domain.order import Order, OrderItem
from app.domain.reservation_terminalization_task import ReservationTerminalizationTask
from app.domain.statuses import (
    OrderStatus,
    PaymentStatus,
    TaskStatus,
    TerminalizationAction,
)


def to_order(row: Any) -> Order:
    return Order(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        created_at=_created_at(row["created_at"]),
        total_amount=float(row["total_amount"]),
        status=cast(OrderStatus, row["status"]),
        payment_status=cast(PaymentStatus, row["payment_status"]),
        idempotency_key=cast(str | None, row["idempotency_key"]),
        items=tuple(
            OrderItem(
                product_id=int(item["product_id"]),
                quantity=int(item["quantity"]),
                unit_price=float(item["unit_price"]),
            )
            for item in row["items_json"]
        ),
        reservation_ids=tuple(int(value) for value in row["reservation_ids_json"]),
    )


def to_task(row: Any) -> ReservationTerminalizationTask:
    return ReservationTerminalizationTask(
        task_id=int(row["task_id"]),
        order_id=int(row["order_id"]),
        reservation_id=int(row["reservation_id"]),
        action=cast(TerminalizationAction, row["action"]),
        status=cast(TaskStatus, row["status"]),
        attempt_count=int(row["attempt_count"]),
        available_at=_created_at(row["available_at"]),
        created_at=_created_at(row["created_at"]),
        last_error=cast(str | None, row["last_error"]),
    )


def dump_items(items: list[OrderItem]) -> str:
    return json.dumps(
        [
            {
                "product_id": item.product_id,
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
            }
            for item in items
        ]
    )


def dump_reservation_ids(reservation_ids: list[int]) -> str:
    return json.dumps(reservation_ids)


def decimal_amount(amount: float) -> Decimal:
    return Decimal(str(round(amount, 2)))


def _created_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
