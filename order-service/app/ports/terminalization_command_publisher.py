from typing import Protocol

from app.domain.statuses import TerminalizationAction
from app.domain.terminalization_command import TerminalizationCommand


class TerminalizationCommandPublisher(Protocol):
    def publish(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
    ) -> None: ...

    def publish_retry(self, command: TerminalizationCommand, error: str) -> None: ...

    def publish_dead_letter(self, command: TerminalizationCommand, error: str) -> None: ...


class NoOpTerminalizationCommandPublisher:
    def publish(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
    ) -> None:
        return

    def publish_retry(self, command: TerminalizationCommand, error: str) -> None:
        return

    def publish_dead_letter(self, command: TerminalizationCommand, error: str) -> None:
        return
