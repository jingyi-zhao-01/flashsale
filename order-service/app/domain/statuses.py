from typing import Literal

OrderStatus = Literal["pending", "confirmed", "failed", "cancelled", "expired"]
PaymentStatus = Literal["pending", "succeeded", "cancelled"]
TerminalizationAction = Literal["confirm", "cancel"]
TaskStatus = Literal["queued", "processing", "succeeded", "retrying"]
TerminalizationEventType = Literal[
    "queued",
    "processing",
    "retrying",
    "succeeded",
    "error",
]
