from datetime import datetime
from typing import Any, cast

import psycopg
from psycopg.rows import dict_row

from app.adapters.order_postgres_rows import to_task
from app.config import DB_POOL_MAX_SIZE, DB_POOL_MIN_SIZE, DB_POOL_TIMEOUT_SECONDS
from app.domain.reservation_terminalization_task import ReservationTerminalizationTask
from app.domain.statuses import TerminalizationAction, TerminalizationEventType
from flashsale_shared.db_pool import DatabasePool
from flashsale_shared.observability import start_span

ROW_FACTORY = cast(Any, dict_row)


class TerminalizationTaskPostgresRepository:
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

    def enqueue(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
        now: datetime,
    ) -> None:
        with self._pool.connection(autocommit=True, row_factory=ROW_FACTORY) as conn:
            with conn.cursor() as cur:
                for reservation_id in reservation_ids:
                    cur.execute(
                        """
                        INSERT INTO order_terminalization_tasks (
                            order_id, reservation_id, action, status, attempt_count, available_at, created_at
                        )
                        VALUES (%s, %s, %s, 'queued', 0, %s, %s)
                        RETURNING task_id
                        """,
                        (order_id, reservation_id, action, now, now),
                    )
                    task = cur.fetchone()
                    self._record_event(
                        cur,
                        task_id=int(task["task_id"]),
                        order_id=order_id,
                        reservation_id=reservation_id,
                        action=action,
                        event_type="queued",
                        attempt_count=0,
                    )

    def claim_ready(
        self,
        limit: int,
        available_before: datetime,
    ) -> list[ReservationTerminalizationTask]:
        with start_span(
            "order-service",
            "queue claim ready tasks",
            attributes={"db.system": "postgresql", "flashsale.batch_limit": limit},
        ):
            with self._pool.connection(row_factory=ROW_FACTORY) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        WITH ready AS (
                            SELECT task_id
                            FROM order_terminalization_tasks
                            WHERE status IN ('queued', 'retrying') AND available_at <= %s
                            ORDER BY available_at ASC, task_id ASC
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                        )
                        UPDATE order_terminalization_tasks tasks
                        SET status = 'processing', attempt_count = attempt_count + 1
                        FROM ready
                        WHERE tasks.task_id = ready.task_id
                        RETURNING tasks.task_id, tasks.order_id, tasks.reservation_id,
                                  tasks.action, tasks.status, tasks.attempt_count,
                                  tasks.available_at, tasks.last_error, tasks.created_at
                        """,
                        (available_before, limit),
                    )
                    rows = [to_task(row) for row in cur.fetchall()]
                    conn.commit()
                    for task in rows:
                        self._record_event(
                            cur,
                            task_id=task.task_id,
                            order_id=task.order_id,
                            reservation_id=task.reservation_id,
                            action=task.action,
                            event_type="processing",
                            attempt_count=task.attempt_count,
                            last_error=task.last_error,
                        )
                    return rows

    def mark_succeeded(self, task_id: int) -> None:
        self._mark(task_id, "succeeded", None, None)

    def mark_retrying(
        self,
        task_id: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        self._mark(task_id, "retrying", available_at, last_error)

    def record_event(
        self,
        task_id: int,
        order_id: int,
        reservation_id: int,
        action: TerminalizationAction,
        event_type: TerminalizationEventType,
        attempt_count: int,
        last_error: str | None = None,
    ) -> None:
        with self._pool.connection(autocommit=True) as conn:
            with conn.cursor() as cur:
                self._record_event(
                    cur,
                    task_id=task_id,
                    order_id=order_id,
                    reservation_id=reservation_id,
                    action=action,
                    event_type=event_type,
                    attempt_count=attempt_count,
                    last_error=last_error,
                )

    def _mark(
        self,
        task_id: int,
        status: str,
        available_at: datetime | None,
        last_error: str | None,
    ) -> None:
        with self._pool.connection(autocommit=True, row_factory=ROW_FACTORY) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT task_id, order_id, reservation_id, action, attempt_count
                    FROM order_terminalization_tasks
                    WHERE task_id = %s
                    """,
                    (task_id,),
                )
                task = cur.fetchone()
                if not task:
                    return
                cur.execute(
                    """
                    UPDATE order_terminalization_tasks
                    SET status = %s, available_at = COALESCE(%s, available_at), last_error = %s
                    WHERE task_id = %s
                    """,
                    (status, available_at, last_error, task_id),
                )
                self._record_event(
                    cur,
                    task_id=int(task["task_id"]),
                    order_id=int(task["order_id"]),
                    reservation_id=int(task["reservation_id"]),
                    action=task["action"],
                    event_type=cast(TerminalizationEventType, status),
                    attempt_count=int(task["attempt_count"]),
                    last_error=last_error,
                )

    def _record_event(
        self,
        cur: Any,
        task_id: int,
        order_id: int,
        reservation_id: int,
        action: TerminalizationAction,
        event_type: TerminalizationEventType,
        attempt_count: int,
        last_error: str | None = None,
    ) -> None:
        cur.execute(
            """
            INSERT INTO order_terminalization_task_events (
                task_id, order_id, reservation_id, action, event_type, attempt_count, last_error
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, order_id, reservation_id, action, event_type, attempt_count, last_error),
        )
