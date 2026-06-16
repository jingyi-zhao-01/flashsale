from typing import Protocol

from app.domain.order import Order
from app.domain.statuses import OrderStatus, PaymentStatus, TerminalizationAction
from app.ports.order_repository import OrderRepository


class UnitOfWork(Protocol):
    orders: OrderRepository
    def init_db(self) -> None: ...

    def is_healthy(self) -> bool: ...

    def reset(self) -> None: ...

    def create_order(
        self,
        user_id: int,
        total_amount: float,
        items: list["OrderItem"],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
    ) -> Order: ...

    def finalize_order(
        self,
        order_id: int,
        status: OrderStatus,
        payment_status: PaymentStatus,
        action: TerminalizationAction,
        reservation_ids: list[int],
    ) -> Order | None: ...
