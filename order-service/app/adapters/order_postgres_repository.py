from datetime import datetime
import logging
import time
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row

from app.adapters.order_postgres_rows import (
    decimal_amount,
    dump_items,
    dump_reservation_ids,
    to_order,
)
from app.config import DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE, DB_POOL_TIMEOUT_SECONDS
from app.domain.order import Order, OrderItem
from app.domain.state_machines import transition_order
from app.domain.statuses import OrderStatus, PaymentStatus
from flashsale_shared.db_pool import DatabasePool
from flashsale_shared.observability import start_span

ROW_FACTORY = cast(Any, dict_row)
db_logger = logging.getLogger("order-service.db")


class OrderPostgresRepository:
    def __init__(
        self,
        database_url: str,
        pool: DatabasePool | None = None,
    ) -> None:
        self._database_url = database_url
        self._pool = pool or DatabasePool(
            database_url=database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout_seconds=DB_POOL_TIMEOUT_SECONDS,
        )

    def create(
        self,
        user_id: int,
        total_amount: float,
        items: list[OrderItem],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
        status: OrderStatus = "pending",
        payment_status: PaymentStatus = "pending",
    ) -> Order:
        start = time.perf_counter()
        result = "inserted"
        with start_span(
            "order-service",
            "order db create",
            attributes={"db.system": "postgresql"},
        ):
            with self._pool.connection(autocommit=True, row_factory=ROW_FACTORY) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO orders (
                            user_id, total_amount, status, payment_status, idempotency_key,
                            reservation_ids_json, items_json
                        )
                        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
                        RETURNING id, user_id, created_at, total_amount, status,
                                  payment_status, idempotency_key, reservation_ids_json, items_json
                        """,
                        (
                            user_id,
                            decimal_amount(total_amount),
                            status,
                            payment_status,
                            idempotency_key,
                            dump_reservation_ids(reservation_ids),
                            dump_items(items),
                        ),
                    )
                    row = cur.fetchone()
                    if row:
                        order = to_order(row)
                        db_logger.info(
                            "event=order_service_order_db order_id=%s operation=create order_db_ms=%.2f result=%s",
                            order.id,
                            (time.perf_counter() - start) * 1000,
                            result,
                        )
                        return order
                    if idempotency_key:
                        cur.execute(
                            """
                            SELECT id, user_id, created_at, total_amount, status,
                                   payment_status, idempotency_key, reservation_ids_json, items_json
                            FROM orders
                            WHERE idempotency_key = %s
                            """,
                            (idempotency_key,),
                        )
                        existing_row = cur.fetchone()
                        existing = to_order(existing_row) if existing_row else None
                        if existing:
                            result = "idempotency_replay"
                            db_logger.info(
                                "event=order_service_order_db order_id=%s operation=create order_db_ms=%.2f result=%s",
                                existing.id,
                                (time.perf_counter() - start) * 1000,
                                result,
                            )
                            return existing
                    result = "error"
                    raise RuntimeError("order persistence failed")

    def update_state(
        self,
        order_id: int,
        status: OrderStatus,
        payment_status: PaymentStatus | None = None,
    ) -> Order | None:
        current = self.get(order_id)
        if not current:
            return None
        updated = transition_order(current, status, payment_status)
        with self._pool.connection(autocommit=True, row_factory=ROW_FACTORY) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE orders
                    SET status = %s, payment_status = %s
                    WHERE id = %s
                    RETURNING id, user_id, created_at, total_amount, status,
                              payment_status, idempotency_key, reservation_ids_json, items_json
                    """,
                    (updated.status, updated.payment_status, order_id),
                )
                row = cur.fetchone()
                return to_order(row) if row else None

    def get(self, order_id: int) -> Order | None:
        return self._fetch_one("WHERE id = %s", (order_id,))

    def get_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        return self._fetch_one("WHERE idempotency_key = %s", (idempotency_key,))

    def list_all(self) -> list[Order]:
        return self._fetch_many("ORDER BY id DESC", ())

    def list_stale(self, expires_before: datetime) -> list[Order]:
        return self._fetch_many(
            "WHERE status = 'pending' AND created_at <= %s ORDER BY created_at ASC, id ASC",
            (expires_before,),
        )

    def _fetch_one(self, where_clause: str, params: tuple[object, ...]) -> Order | None:
        rows = self._fetch_many(where_clause, params)
        return rows[0] if rows else None

    def _fetch_many(self, clause: str, params: tuple[object, ...]) -> list[Order]:
        with self._pool.connection(row_factory=ROW_FACTORY) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, user_id, created_at, total_amount, status,
                           payment_status, idempotency_key, reservation_ids_json, items_json
                    FROM orders
                    {clause}
                    """,
                    params,
                )
                return [to_order(row) for row in cur.fetchall()]
