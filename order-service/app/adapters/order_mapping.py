from datetime import datetime

from app.domain.order import Order, OrderItem
from app.models import OrderItemOut, OrderOut


def to_domain_item(item: OrderItemOut) -> OrderItem:
    return OrderItem(
        product_id=item.product_id,
        quantity=item.quantity,
        unit_price=float(item.unit_price),
    )


def to_api_item(item: OrderItem) -> OrderItemOut:
    return OrderItemOut(
        product_id=item.product_id,
        quantity=item.quantity,
        unit_price=item.unit_price,
        line_total=item.line_total,
    )


def to_api_order(order: Order) -> OrderOut:
    return OrderOut(
        id=order.id,
        user_id=order.user_id,
        created_at=order.created_at.isoformat(),
        total_amount=order.total_amount,
        status=order.status,
        payment_status=order.payment_status,
        idempotency_key=order.idempotency_key,
        items=[to_api_item(item) for item in order.items],
    )


def parse_created_at(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)
