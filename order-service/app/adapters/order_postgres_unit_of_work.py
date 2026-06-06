import logging
import time
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row

from app.adapters.order_postgres_repository import OrderPostgresRepository
from app.adapters.order_postgres_rows import (
    decimal_amount,
    dump_items,
    dump_reservation_ids,
    to_order,
)
from app.adapters.order_postgres_schema import (
    ensure_order_tables,
    ensure_terminalization_tables,
)
from app.adapters.terminalization_task_postgres_repository import TerminalizationTaskPostgresRepository
from app.config import DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE, DB_POOL_TIMEOUT_SECONDS
from app.domain.order import Order
from app.domain.state_machines import transition_order
from app.domain.statuses import (
    OrderStatus,
    PaymentStatus,
    TerminalizationAction,
)
from flashsale_shared.db_pool import DatabasePool
from flashsale_shared.observability import start_span
from flashsale_shared.postgres_schema import ensure_schema, set_search_path

ROW_FACTORY = cast(Any, dict_row)
db_logger = logging.getLogger("order-service.db")


class OrderPostgresUnitOfWork:
    def __init__(self, database_url: str, schema_name: str = "order_service") -> None:
        self._database_url = database_url
        self._schema_name = schema_name
        self._pool = DatabasePool(
            database_url=database_url,
            min_size=DB_POOL_MIN_SIZE,
            max_size=DB_POOL_MAX_SIZE,
            timeout_seconds=DB_POOL_TIMEOUT_SECONDS,
        )
        self.orders = OrderPostgresRepository(database_url, pool=self._pool)
        self.tasks = TerminalizationTaskPostgresRepository(database_url, pool=self._pool)

    def init_db(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                ensure_schema(cur, self._schema_name)
                set_search_path(cur, self._schema_name)
                ensure_order_tables(cur)
                ensure_terminalization_tables(cur)

    def is_healthy(self) -> bool:
        with psycopg.connect(self._database_url, connect_timeout=2) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True

    def reset(self) -> None:
        with psycopg.connect(self._database_url, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE orders RESTART IDENTITY CASCADE")

    def create_order_and_enqueue_terminalization(
        self,
        user_id: int,
        total_amount: float,
        items: list["OrderItem"],
        reservation_ids: list[int],
        idempotency_key: str | None = None,
        action: TerminalizationAction = "confirm",
    ) -> Order:
        order_db_ms = 0.0
        enqueue_task_ms = 0.0
        total_start = time.perf_counter()
        result = "inserted"
        with start_span(
            "order-service",
            "order db create and enqueue",
            attributes={"db.system": "postgresql"},
        ):
            with self._pool.connection(autocommit=False, row_factory=ROW_FACTORY) as conn:
                with conn.cursor() as cur:
                    create_start = time.perf_counter()
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
                            "pending",
                            "pending",
                            idempotency_key,
                            dump_reservation_ids(reservation_ids),
                            dump_items(items),
                        ),
                    )
                    row = cur.fetchone()
                    if not row and idempotency_key:
                        cur.execute(
                            """
                            SELECT id, user_id, created_at, total_amount, status,
                                   payment_status, idempotency_key, reservation_ids_json, items_json
                            FROM orders
                            WHERE idempotency_key = %s
                            """,
                            (idempotency_key,),
                        )
                        row = cur.fetchone()
                        if row:
                            result = "idempotency_replay"
                    if not row:
                        result = "error"
                        raise RuntimeError("order persistence failed")

                    order = to_order(row)
                    if result == "idempotency_replay":
                        conn.commit()
                        db_logger.info(
                            "event=order_service_order_db order_id=%s operation=create order_db_ms=%.2f result=%s",
                            order.id,
                            (time.perf_counter() - total_start) * 1000,
                            result,
                        )
                        return order
                    order_db_ms = (time.perf_counter() - create_start) * 1000
                    enqueue_start = time.perf_counter()
                    for reservation_id in reservation_ids:
                        cur.execute(
                            """
                            INSERT INTO order_terminalization_tasks (
                                order_id, reservation_id, action, status, attempt_count
                            ) VALUES (%s, %s, %s, 'queued', 0)
                            RETURNING task_id
                            """,
                            (order.id, reservation_id, action),
                        )
                        task = cur.fetchone()
                        cur.execute(
                            """
                            INSERT INTO order_terminalization_task_events (
                                task_id, order_id, reservation_id, action, event_type, attempt_count
                            ) VALUES (%s, %s, %s, %s, 'queued', 0)
                            """,
                            (task["task_id"], order.id, reservation_id, action),
                        )
                    enqueue_task_ms = (time.perf_counter() - enqueue_start) * 1000
                    conn.commit()
                    db_logger.info(
                        "event=order_service_order_db order_id=%s operation=create order_db_ms=%.2f result=%s",
                        order.id,
                        order_db_ms,
                        result,
                    )
                    db_logger.info(
                        "event=order_service_enqueue_task order_id=%s action=%s reservation_count=%s order_db_ms=%.2f enqueue_task_ms=%.2f total_create_ms=%.2f result=success",
                        order.id,
                        action,
                        len(reservation_ids),
                        order_db_ms,
                        enqueue_task_ms,
                        (time.perf_counter() - total_start) * 1000,
                    )
                    return order

    def finalize_order(
        self,
        order_id: int,
        status: OrderStatus,
        payment_status: PaymentStatus,
        action: TerminalizationAction,
        reservation_ids: list[int],
    ) -> Order | None:
        order_update_ms = 0.0
        enqueue_task_ms = 0.0
        total_start = time.perf_counter()
        with start_span(
            "order-service",
            "order db finalize and enqueue",
            attributes={
                "db.system": "postgresql",
                "flashsale.order_id": order_id,
                "flashsale.action": action,
                "flashsale.reservation_count": len(reservation_ids),
            },
        ):
            with self._pool.connection(row_factory=ROW_FACTORY) as conn:
                with conn.cursor() as cur:
                    update_start = time.perf_counter()
                    cur.execute(
                        """
                        SELECT id, user_id, created_at, total_amount, status,
                               payment_status, idempotency_key, reservation_ids_json, items_json
                        FROM orders
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (order_id,),
                    )
                    current = cur.fetchone()
                    if not current:
                        return None
                    updated = transition_order(to_order(current), status, payment_status)
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
                    order_update_ms = (time.perf_counter() - update_start) * 1000
                    enqueue_start = time.perf_counter()
                    for reservation_id in reservation_ids:
                        cur.execute(
                            """
                            INSERT INTO order_terminalization_tasks (
                                order_id, reservation_id, action, status, attempt_count
                            ) VALUES (%s, %s, %s, 'queued', 0)
                            RETURNING task_id
                            """,
                            (order_id, reservation_id, action),
                        )
                        task = cur.fetchone()
                        cur.execute(
                            """
                            INSERT INTO order_terminalization_task_events (
                                task_id, order_id, reservation_id, action, event_type, attempt_count
                            ) VALUES (%s, %s, %s, %s, 'queued', 0)
                            """,
                            (task["task_id"], order_id, reservation_id, action),
                        )
                    enqueue_task_ms = (time.perf_counter() - enqueue_start) * 1000
                    conn.commit()
                    db_logger.info(
                        "event=order_service_enqueue_task order_id=%s action=%s reservation_count=%s "
                        "order_db_ms=%.2f enqueue_task_ms=%.2f total_finalize_ms=%.2f result=success",
                        order_id,
                        action,
                        len(reservation_ids),
                        order_update_ms,
                        enqueue_task_ms,
                        (time.perf_counter() - total_start) * 1000,
                    )
                    return to_order(row)
