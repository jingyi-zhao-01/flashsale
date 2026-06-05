from typing import Protocol

from app.domain.statuses import TerminalizationAction


class ProductReservationClient(Protocol):
    def reserve(self, product_id: int, quantity: int) -> tuple[float, int, int]: ...

    def release(self, reservation_ids: list[int]) -> None: ...

    def terminalize(
        self,
        reservation_id: int,
        action: TerminalizationAction,
    ) -> tuple[bool, str | None]: ...
