from typing import Literal

OrderStatus = Literal["pending", "confirmed", "failed", "cancelled", "expired"]
PaymentStatus = Literal["pending", "succeeded", "cancelled"]
TerminalizationAction = Literal["confirm", "cancel"]
