from dataclasses import dataclass
from datetime import datetime

from .statuses import TaskStatus, TerminalizationAction, TerminalizationEventType


@dataclass(frozen=True)
class ReservationTerminalizationTask:
    task_id: int
    order_id: int
    reservation_id: int
    action: TerminalizationAction
    status: TaskStatus
    attempt_count: int
    available_at: datetime
    created_at: datetime
    last_error: str | None


@dataclass(frozen=True)
class ReservationTerminalizationTaskEvent:
    task_id: int
    order_id: int
    reservation_id: int
    action: TerminalizationAction
    event_type: TerminalizationEventType
    attempt_count: int
    occurred_at: datetime
    last_error: str | None = None
