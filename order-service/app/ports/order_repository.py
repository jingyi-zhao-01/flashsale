from datetime import datetime
from typing import Protocol

from app.domain.order import Order, OrderItem
from app.domain.statuses import OrderStatus, PaymentStatus


class OrderRepository(Protocol):
    def create(
        self,
        user_id: int,
        total_amount: float,
        items: list[OrderItem],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
        status: OrderStatus = "pending",
        payment_status: PaymentStatus = "pending",
    ) -> Order: ...

    def update_state(
        self,
        order_id: int,
        status: OrderStatus,
        payment_status: PaymentStatus | None = None,
    ) -> Order | None: ...

    def get(self, order_id: int) -> Order | None: ...

    def get_by_idempotency_key(self, idempotency_key: str) -> Order | None: ...

    def list_all(self) -> list[Order]: ...

    def list_stale(self, expires_before: datetime) -> list[Order]: ...
