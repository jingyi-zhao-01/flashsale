from typing import Protocol


class ReserveAdmissionGate(Protocol):
    def acquire(self, product_ids: list[int]) -> list[int]:
        """Acquire admission permits for the given product_ids.

        Returns the list of product_ids for which permits were successfully
        acquired.  Callers must release every acquired permit in a finally
        block.
        """

    def release(self, product_ids: list[int]) -> None:
        """Release admission permits previously acquired for product_ids."""


class NoOpReserveAdmissionGate:
    def acquire(self, product_ids: list[int]) -> list[int]:
        return list(product_ids)

    def release(self, product_ids: list[int]) -> None:
        pass
