from typing import Literal

from pydantic import BaseModel, Field


class ProductCreate(BaseModel):
    """Payload used to create a product."""

    name: str = Field(description="Display name of the product.")
    price: float = Field(gt=0, description="Unit price charged for the product.")
    stock: int = Field(ge=0, description="Initial available stock quantity.")


class ProductOut(BaseModel):
    """Product resource returned by product-service."""

    id: int = Field(description="Identifier of the product.")
    name: str = Field(description="Display name of the product.")
    price: float = Field(description="Unit price for the product.")
    stock: int = Field(description="Currently available stock quantity.")


class ReserveRequest(BaseModel):
    """Payload used to reserve stock for a product."""

    quantity: int = Field(
        gt=0, description="Quantity to reserve from the product stock."
    )


ReservationStatus = Literal["reserved", "confirmed", "cancelled", "expired"]


class ReservationOut(BaseModel):
    """Reservation resource returned by reservation lifecycle endpoints."""

    reservation_id: int = Field(description="Identifier of the reservation.")
    product_id: int = Field(description="Identifier of the reserved product.")
    quantity: int = Field(description="Reserved quantity for the product.")
    unit_price: float | None = Field(
        default=None,
        description="Unit price snapshot captured when the reservation was created.",
    )
    status: ReservationStatus = Field(description="Lifecycle state of the reservation.")
    expires_at: str | None = Field(
        default=None,
        description="ISO-8601 expiration timestamp for reserved stock, if applicable.",
    )


class ExpireReservationsResult(BaseModel):
    """Summary of the reservation expiry maintenance task."""

    expired_count: int = Field(
        description="Number of reservations marked expired during the run."
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
