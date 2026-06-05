from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal["pending", "confirmed", "failed", "cancelled", "expired"]
PaymentStatus = Literal["pending", "succeeded", "cancelled"]
PaymentWebhookStatus = Literal["succeeded"]
TerminalizationAction = Literal["confirm", "cancel"]
TerminalizationTaskStatus = Literal["queued", "processing", "succeeded", "retrying"]
TerminalizationEventType = Literal[
    "queued",
    "processing",
    "retrying",
    "succeeded",
    "error",
]


class OrderItemRequest(BaseModel):
    """One product line requested for a new order."""

    product_id: int = Field(description="Identifier of the product to purchase.")
    quantity: int = Field(gt=0, description="Requested quantity for this product.")


class OrderCreateRequest(BaseModel):
    """Payload used to create a new order."""

    user_id: int = Field(description="Identifier of the user placing the order.")
    items: list[OrderItemRequest] = Field(
        description="Products and quantities to reserve and persist in the order."
    )
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional client-supplied key used to deduplicate repeated order submissions.",
    )


class OrderItemOut(BaseModel):
    """One persisted line item in an order response."""

    product_id: int = Field(description="Identifier of the purchased product.")
    quantity: int = Field(description="Quantity confirmed for this line item.")
    unit_price: float = Field(
        description="Unit price captured when the order was created."
    )
    line_total: float = Field(description="Subtotal for this line item.")


class OrderOut(BaseModel):
    """Order resource returned by the API."""

    id: int = Field(description="Identifier of the order.")
    user_id: int = Field(description="Identifier of the user who placed the order.")
    created_at: str = Field(
        description="ISO-8601 timestamp when the order was created."
    )
    total_amount: float = Field(description="Total amount charged for the order.")
    status: OrderStatus = Field(description="Lifecycle state of the order.")
    payment_status: PaymentStatus = Field(
        description="Current payment state recorded for the order."
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Optional idempotency key associated with the order request.",
    )
    items: list[OrderItemOut] = Field(description="Line items captured in the order.")


class ExpireOrdersResult(BaseModel):
    """Summary of the order expiry maintenance task."""

    expired_count: int = Field(
        description="Number of pending orders marked as expired."
    )


class ProcessTerminalizationTasksResult(BaseModel):
    """Summary of one terminalization worker pass."""

    claimed_count: int = Field(description="Number of tasks claimed by the worker.")
    succeeded_count: int = Field(description="Number of tasks finished successfully.")
    retrying_count: int = Field(description="Number of tasks rescheduled for retry.")


class PaymentWebhookRequest(BaseModel):
    """Payload accepted from the payment success webhook."""

    order_id: int = Field(
        description="Identifier of the order referenced by the webhook event."
    )
    event_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional gateway event identifier used for webhook deduplication.",
    )
    status: PaymentWebhookStatus = Field(
        default="succeeded",
        description="Payment status accepted by the webhook endpoint.",
    )


class ErrorResponse(BaseModel):
    """Default error payload returned by FastAPI exception handlers."""

    detail: str = Field(
        description="Human-readable description of the error condition."
    )


class HealthResponse(BaseModel):
    """Health response for service availability checks."""

    status: str = Field(description="Health status for the service.")
    service: str = Field(description="Service name reporting the health response.")
