from dataclasses import dataclass
from datetime import datetime

from .statuses import OrderStatus, PaymentStatus


@dataclass(frozen=True)
class OrderItem:
    product_id: int
    quantity: int
    unit_price: float

    @property
    def line_total(self) -> float:
        return round(self.quantity * self.unit_price, 2)


@dataclass(frozen=True)
class Order:
    id: int
    user_id: int
    created_at: datetime
    total_amount: float
    status: OrderStatus
    payment_status: PaymentStatus
    idempotency_key: str | None
    items: tuple[OrderItem, ...]
    reservation_ids: tuple[int, ...]
