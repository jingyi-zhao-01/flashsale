from dataclasses import dataclass


@dataclass(frozen=True)
class CreateOrderCommand:
    user_id: int
    items: tuple[tuple[int, int], ...]
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PaymentWebhookCommand:
    order_id: int
    event_id: str | None = None
