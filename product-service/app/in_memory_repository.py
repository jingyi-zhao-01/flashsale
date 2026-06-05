from datetime import datetime, timedelta, timezone
from itertools import count
from threading import Lock

from .config import DEFAULT_SEED_PRODUCT_COUNT
from .models import ProductCreate, ProductOut, ReservationOut

RESERVATION_TTL_SECONDS = 300


def seed_items(
    count_value: int = DEFAULT_SEED_PRODUCT_COUNT,
) -> list[tuple[str, float, int]]:
    return [
        (
            f"Seed Item {idx}",
            round(9.9 + idx * 0.5, 2),
            10 + (idx % 15),
        )
        for idx in range(1, count_value + 1)
    ]


class InMemoryProductRepository:
    def __init__(self) -> None:
        self._products: dict[int, ProductOut] = {}
        self._reservations: dict[int, ReservationOut] = {}
        self._counter = count(1)
        self._reservation_counter = count(1)
        self._lock = Lock()

    @staticmethod
    def _reservation_expiry() -> str:
        return (
            datetime.now(timezone.utc) + timedelta(seconds=RESERVATION_TTL_SECONDS)
        ).isoformat()

    def init_db(self) -> None:
        return

    def is_healthy(self) -> bool:
        return True

    def seed_if_empty(self) -> None:
        with self._lock:
            if self._products:
                return
            for idx, (name, price, stock) in enumerate(seed_items(), start=1):
                self._products[idx] = ProductOut(
                    id=idx, name=name, price=price, stock=stock
                )
            self._counter = count(len(self._products) + 1)

    def reset_db(self) -> None:
        with self._lock:
            self._products.clear()
            self._reservations.clear()
            self._counter = count(1)
            self._reservation_counter = count(1)

    def seed_with_quantity(self, count_value: int, quantity: int) -> None:
        with self._lock:
            self._products.clear()
            self._reservations.clear()
            for idx in range(1, count_value + 1):
                self._products[idx] = ProductOut(
                    id=idx,
                    name=f"Seed Item {idx}",
                    price=round(9.9 + idx * 0.5, 2),
                    stock=quantity,
                )
            self._counter = count(count_value + 1)
            self._reservation_counter = count(1)

    def create_product(self, payload: ProductCreate) -> ProductOut:
        with self._lock:
            product_id = next(self._counter)
            product = ProductOut(
                id=product_id,
                name=payload.name,
                price=payload.price,
                stock=payload.stock,
            )
            self._products[product_id] = product
            return product

    def get_product(self, product_id: int) -> ProductOut | None:
        return self._products.get(product_id)

    def reserve_product(
        self, product_id: int, quantity: int
    ) -> ReservationOut | None:
        with self._lock:
            product = self._products.get(product_id)
            if not product:
                return None
            if product.stock < quantity:
                raise ValueError("insufficient stock")
            updated = ProductOut(
                id=product.id,
                name=product.name,
                price=product.price,
                stock=product.stock - quantity,
            )
            self._products[product_id] = updated
            reservation_id = next(self._reservation_counter)
            reservation = ReservationOut(
                reservation_id=reservation_id,
                product_id=product_id,
                quantity=quantity,
                unit_price=product.price,
                status="reserved",
                expires_at=self._reservation_expiry(),
            )
            self._reservations[reservation_id] = reservation
            return reservation

    def confirm_reservation(self, reservation_id: int) -> ReservationOut | None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if not reservation:
                return None
            if reservation.status != "reserved":
                return reservation
            updated = ReservationOut(
                reservation_id=reservation.reservation_id,
                product_id=reservation.product_id,
                quantity=reservation.quantity,
                unit_price=reservation.unit_price,
                status="confirmed",
                expires_at=reservation.expires_at,
            )
            self._reservations[reservation_id] = updated
            return updated

    def cancel_reservation(self, reservation_id: int) -> ReservationOut | None:
        with self._lock:
            reservation = self._reservations.get(reservation_id)
            if not reservation:
                return None
            if reservation.status != "reserved":
                return reservation
            product = self._products.get(reservation.product_id)
            if not product:
                return None
            self._products[reservation.product_id] = ProductOut(
                id=product.id,
                name=product.name,
                price=product.price,
                stock=product.stock + reservation.quantity,
            )
            updated = ReservationOut(
                reservation_id=reservation.reservation_id,
                product_id=reservation.product_id,
                quantity=reservation.quantity,
                unit_price=reservation.unit_price,
                status="cancelled",
                expires_at=reservation.expires_at,
            )
            self._reservations[reservation_id] = updated
            return updated

    def expire_reservations(self) -> int:
        now = datetime.now(timezone.utc)
        expired_count = 0
        with self._lock:
            for reservation_id, reservation in list(self._reservations.items()):
                if reservation.status != "reserved" or not reservation.expires_at:
                    continue
                if datetime.fromisoformat(reservation.expires_at) > now:
                    continue
                product = self._products.get(reservation.product_id)
                if not product:
                    continue
                self._products[reservation.product_id] = ProductOut(
                    id=product.id,
                    name=product.name,
                    price=product.price,
                    stock=product.stock + reservation.quantity,
                )
                self._reservations[reservation_id] = ReservationOut(
                    reservation_id=reservation.reservation_id,
                    product_id=reservation.product_id,
                    quantity=reservation.quantity,
                    unit_price=reservation.unit_price,
                    status="expired",
                    expires_at=reservation.expires_at,
                )
                expired_count += 1
        return expired_count

    def has_product(self, product_id: int) -> bool:
        return product_id in self._products

    def list_products(self) -> list[ProductOut]:
        return list(self._products.values())
