from datetime import datetime

from fastapi import HTTPException

from app.config import DATABASE_UNAVAILABLE_MESSAGE
from app.domain.order import Order
from app.ports.unit_of_work import UnitOfWork


class OrderQueries:
    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    def get_order(self, order_id: int) -> Order:
        try:
            order = self._uow.orders.get(order_id)
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc
        if not order:
            raise HTTPException(status_code=404, detail="order not found")
        return order

    def list_orders(self) -> list[Order]:
        try:
            return self._uow.orders.list_all()
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail=DATABASE_UNAVAILABLE_MESSAGE,
            ) from exc

    def mark_order_created_at(self, order_id: int, created_at: datetime) -> None:
        override = getattr(self._uow.orders, "override_created_at", None)
        if override is None:
            raise AttributeError("override_created_at is not supported")
        override(order_id, created_at)
