from datetime import datetime
from typing import Protocol

from app.domain.reservation_terminalization_task import (
    ReservationTerminalizationTask,
)
from app.domain.statuses import TerminalizationAction, TerminalizationEventType


class TerminalizationTaskRepository(Protocol):
    def enqueue(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
        now: datetime,
    ) -> None: ...

    def claim_ready(
        self,
        limit: int,
        available_before: datetime,
    ) -> list[ReservationTerminalizationTask]: ...

    def mark_succeeded(self, task_id: int) -> None: ...

    def mark_retrying(
        self,
        task_id: int,
        available_at: datetime,
        last_error: str,
    ) -> None: ...

    def record_event(
        self,
        task_id: int,
        order_id: int,
        reservation_id: int,
        action: TerminalizationAction,
        event_type: TerminalizationEventType,
        attempt_count: int,
        last_error: str | None = None,
    ) -> None: ...
