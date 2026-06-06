import logging
import time
from abc import ABC, abstractmethod

import psycopg
from psycopg.rows import dict_row

from .config import (
    DB_POOL_MAX_SIZE,
    DB_POOL_MIN_SIZE,
    DB_POOL_TIMEOUT_SECONDS,
    INVENTORY_LOCK_MODE,
    OPTIMISTIC_RETRY_LIMIT,
    RESERVE_SQL_LOG_SLOW_MS,
)
from flashsale_shared.db_pool import DatabasePool
from flashsale_shared.postgres_schema import ensure_schema, set_search_path
from .in_memory_repository import InMemoryProductRepository, RESERVATION_TTL_SECONDS, seed_items
from .locking import InventoryReserveEngine
from .models import ProductCreate, ProductOut, ReservationOut

db_logger = logging.getLogger("product-service.db")


class ProductRepository(ABC):
    @abstractmethod
    def init_db(self) -> None:
        pass

    @abstractmethod
    def is_healthy(self) -> bool:
        pass

    @abstractmethod
    def seed_if_empty(self) -> None:
        pass

    @abstractmethod
    def reset_db(self) -> None:
        pass

    @abstractmethod
    def seed_with_quantity(self, count: int, quantity: int) -> None:
        pass

    @abstractmethod
    def create_product(self, payload: ProductCreate) -> ProductOut:
        pass

    @abstractmethod
    def get_product(self, product_id: int) -> ProductOut | None:
        pass

    @abstractmethod
    def reserve_product(
        self, product_id: int, quantity: int
    ) -> ReservationOut | None:
        pass

    @abstractmethod
    def confirm_reservation(self, reservation_id: int) -> ReservationOut | None:
        pass

    @abstractmethod
    def cancel_reservation(self, reservation_id: int) -> ReservationOut | None:
        pass

    @abstractmethod
    def expire_reservations(self) -> int:
        pass

    @abstractmethod
    def has_product(self, product_id: int) -> bool:
        pass

    @abstractmethod
    def list_products(self) -> list[ProductOut]:
        pass

class PostgresProductRepository(ProductRepository):
    def __init__(self, database_url: str, schema_name: str = "product_service") -> None:
        self._database_url = database_url
        self._schema_name = schema_name
        self._pool = DatabasePool(
            database_url=database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout_seconds=DB_POOL_TIMEOUT_SECONDS,
        )
        self._inventory_lock_mode = INVENTORY_LOCK_MODE
        self._reserve_engine = InventoryReserveEngine(
            database_url=database_url,
            lock_mode=INVENTORY_LOCK_MODE,
            retry_limit=OPTIMISTIC_RETRY_LIMIT,
            slow_ms_threshold=RESERVE_SQL_LOG_SLOW_MS,
            pool=self._pool,
        )

    @staticmethod
    def _to_product(row: dict[str, object]) -> ProductOut:
        return ProductOut(
            id=int(row["id"]),
            name=str(row["name"]),
            price=float(row["price"]),
            stock=int(row["stock"]),
        )

    def init_db(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                ensure_schema(cur, self._schema_name)
                set_search_path(cur, self._schema_name)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS products (
                        id BIGSERIAL PRIMARY KEY,
                        name TEXT NOT NULL,
                        price NUMERIC(12, 2) NOT NULL,
                        stock INTEGER NOT NULL CHECK (stock >= 0)
                    )
                    """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS reservations (
                        reservation_id BIGSERIAL PRIMARY KEY,
                        product_id BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
                        quantity INTEGER NOT NULL CHECK (quantity > 0),
                        unit_price NUMERIC(12, 2) NULL,
                        status TEXT NOT NULL,
                        expires_at TIMESTAMPTZ NULL,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """)
                cur.execute("""
                    CREATE INDEX IF NOT EXISTS reservations_status_expires_at_idx
                    ON reservations (status, expires_at, reservation_id)
                    """)
                cur.execute("""
                    ALTER TABLE reservations
                    ADD COLUMN IF NOT EXISTS unit_price NUMERIC(12, 2) NULL
                    """)

    def is_healthy(self) -> bool:
        with psycopg.connect(self._database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True

    def seed_if_empty(self) -> None:
        with psycopg.connect(
            self._database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS count FROM products")
                row = cur.fetchone()
                if row and int(row["count"]) > 0:
                    return
                cur.executemany(
                    """
                    INSERT INTO products (name, price, stock)
                    VALUES (%s, %s, %s)
                    """,
                    seed_items(),
                )

    def reset_db(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE reservations RESTART IDENTITY CASCADE")
                cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE")

    def seed_with_quantity(self, count_value: int, quantity: int) -> None:
        with psycopg.connect(
            self._database_url, autocommit=True, row_factory=dict_row
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE reservations RESTART IDENTITY CASCADE")
                cur.execute("TRUNCATE TABLE products RESTART IDENTITY CASCADE")
                items_to_seed = [
                    (f"Seed Item {idx}", round(9.9 + idx * 0.5, 2), quantity)
                    for idx in range(1, count_value + 1)
                ]
                cur.executemany(
                    """
                    INSERT INTO products (name, price, stock)
                    VALUES (%s, %s, %s)
                    """,
                    items_to_seed,
                )

    def create_product(self, payload: ProductCreate) -> ProductOut:
        with self._pool.connection(autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO products (name, price, stock)
                    VALUES (%s, %s, %s)
                    RETURNING id, name, price, stock
                    """,
                    (payload.name, payload.price, payload.stock),
                )
                row = cur.fetchone()
                if not row:
                    raise RuntimeError("product persistence failed")
                return ProductOut(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    price=float(row["price"]),
                    stock=int(row["stock"]),
                )

    def get_product(self, product_id: int) -> ProductOut | None:
        with self._pool.connection(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, name, price, stock
                    FROM products
                    WHERE id = %s
                    """,
                    (product_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ProductOut(
                    id=int(row["id"]),
                    name=str(row["name"]),
                    price=float(row["price"]),
                    stock=int(row["stock"]),
                )

    def reserve_product(
        self, product_id: int, quantity: int
    ) -> ReservationOut | None:
        start = time.perf_counter()
        result = "updated"
        try:
            reservation_row = self._reserve_engine.reserve_with_reservation(
                product_id,
                quantity,
                RESERVATION_TTL_SECONDS,
            )
            if not reservation_row:
                result = "missing"
                return None
            return ReservationOut(
                reservation_id=int(reservation_row["reservation_id"]),
                product_id=int(reservation_row["product_id"]),
                quantity=int(reservation_row["quantity"]),
                unit_price=float(reservation_row["unit_price"]),
                status=str(reservation_row["status"]),
                expires_at=reservation_row["expires_at"].isoformat()
                if reservation_row["expires_at"]
                else None,
            )
        except ValueError:
            result = "insufficient_stock"
            raise
        except RuntimeError:
            result = "retry_exhausted"
            raise
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            if elapsed_ms >= RESERVE_SQL_LOG_SLOW_MS:
                db_logger.warning(
                    "event=reserve_sql_slow product_id=%s quantity=%s elapsed_ms=%.2f result=%s lock_mode=%s",
                    product_id,
                    quantity,
                    elapsed_ms,
                    result,
                    self._inventory_lock_mode,
                )

    def confirm_reservation(self, reservation_id: int) -> ReservationOut | None:
        with self._pool.connection(autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE reservations
                    SET status = CASE WHEN status = 'reserved' THEN 'confirmed' ELSE status END
                    WHERE reservation_id = %s
                    RETURNING reservation_id, product_id, quantity, unit_price, status, expires_at
                    """,
                    (reservation_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return ReservationOut(
                    reservation_id=int(row["reservation_id"]),
                    product_id=int(row["product_id"]),
                    quantity=int(row["quantity"]),
                    unit_price=float(row["unit_price"]) if row["unit_price"] is not None else None,
                    status=str(row["status"]),
                    expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
                )

    def cancel_reservation(self, reservation_id: int) -> ReservationOut | None:
        with self._pool.connection(autocommit=False, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT reservation_id, product_id, quantity, unit_price, status, expires_at
                    FROM reservations
                    WHERE reservation_id = %s
                    FOR UPDATE
                    """,
                    (reservation_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                if str(row["status"]) == "reserved":
                    cur.execute(
                        "UPDATE products SET stock = stock + %s WHERE id = %s",
                        (int(row["quantity"]), int(row["product_id"])),
                    )
                    cur.execute(
                        """
                        UPDATE reservations
                        SET status = 'cancelled'
                        WHERE reservation_id = %s
                        RETURNING reservation_id, product_id, quantity, unit_price, status, expires_at
                        """,
                        (reservation_id,),
                    )
                    row = cur.fetchone()
                conn.commit()
                return ReservationOut(
                    reservation_id=int(row["reservation_id"]),
                    product_id=int(row["product_id"]),
                    quantity=int(row["quantity"]),
                    unit_price=float(row["unit_price"]) if row["unit_price"] is not None else None,
                    status=str(row["status"]),
                    expires_at=row["expires_at"].isoformat() if row["expires_at"] else None,
                )

    def expire_reservations(self) -> int:
        expired_count = 0
        with self._pool.connection(autocommit=False, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT reservation_id, product_id, quantity
                    FROM reservations
                    WHERE status = 'reserved' AND expires_at <= NOW()
                    ORDER BY expires_at ASC, reservation_id ASC
                    FOR UPDATE
                    """
                )
                rows = cur.fetchall()
                for row in rows:
                    cur.execute(
                        "UPDATE products SET stock = stock + %s WHERE id = %s",
                        (int(row["quantity"]), int(row["product_id"])),
                    )
                    cur.execute(
                        "UPDATE reservations SET status = 'expired' WHERE reservation_id = %s",
                        (int(row["reservation_id"]),),
                    )
                    expired_count += 1
                conn.commit()
        return expired_count

    def has_product(self, product_id: int) -> bool:
        with self._pool.connection(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM products WHERE id = %s", (product_id,))
                return cur.fetchone() is not None

    def release_product(self, product_id: int, quantity: int) -> ProductOut | None:
        with self._pool.connection(autocommit=True, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE products
                    SET stock = stock + %s
                    WHERE id = %s
                    RETURNING id, name, price, stock
                    """,
                    (quantity, product_id),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return self._to_product(row)

    def list_products(self) -> list[ProductOut]:
        with self._pool.connection(row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, name, price, stock
                    FROM products
                    ORDER BY id DESC
                    """)
                rows = cur.fetchall()
                return [
                    ProductOut(
                        id=int(row["id"]),
                        name=str(row["name"]),
                        price=float(row["price"]),
                        stock=int(row["stock"]),
                    )
                    for row in rows
                ]
