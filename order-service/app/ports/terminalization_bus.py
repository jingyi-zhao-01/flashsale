"""Terminalization message-bus abstraction.

Defines a pluggable interface for publishing reservation-terminalization
requests and claiming them for processing.  This is the extension point for
swapping the current PostgreSQL polling queue for a dedicated message broker
(Kafka, Redis Streams, etc.) without touching the use-case layer.

Current implementation
    ``PostgresTerminalizationBus`` (``adapters/``) – polls the
    ``order_terminalization_tasks`` table with ``SELECT … FOR UPDATE
    SKIP LOCKED``.

Kafka migration path (future)
    1. Implement ``KafkaTerminalizationBus`` that satisfies this protocol:
       * ``publish`` → produce to a Kafka topic.
       * ``claim_ready`` → not used; Kafka consumer polls internally.
       * ``acknowledge`` → commit consumer offset.
       * ``retry`` → produce to a retry topic or let Kafka redeliver.
    2. Swap the adapter in ``http_api.build_http_api()`` – no use-case
       changes required.
    3. Remove the ``TerminalizationWorkerLoop`` thread; the Kafka consumer
       runs as its own process / container.
"""

from datetime import datetime
from typing import Protocol

from app.domain.reservation_terminalization_task import (
    ReservationTerminalizationTask,
)
from app.domain.statuses import TerminalizationAction


class TerminalizationBus(Protocol):
    """Async message bus for reservation confirm / cancel operations."""

    def publish(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
        now: datetime,
    ) -> None:
        """Enqueue one terminalization request per *reservation_id*."""
        ...

    def claim_ready(
        self,
        limit: int,
        available_before: datetime,
    ) -> list[ReservationTerminalizationTask]:
        """Claim up to *limit* tasks whose ``available_at <= available_before``.

        Implementations must guarantee at-least-once delivery semantics.
        """
        ...

    def acknowledge(self, task_id: int) -> None:
        """Mark *task_id* as successfully processed (commit offset / mark succeeded)."""
        ...

    def retry(
        self,
        task_id: int,
        available_at: datetime,
        last_error: str,
    ) -> None:
        """Re-queue *task_id* for later processing after a transient failure."""
        ...
