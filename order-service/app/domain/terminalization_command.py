from dataclasses import dataclass
from datetime import datetime, timezone
import json
from uuid import uuid4
from typing import Any, cast

from app.domain.statuses import TerminalizationAction


@dataclass(frozen=True)
class TerminalizationCommand:
    event_id: str
    order_id: int
    reservation_id: int
    action: TerminalizationAction
    attempt: int
    created_at: datetime
    idempotency_key: str

    @property
    def message_key(self) -> str:
        return str(self.reservation_id)

    def to_json(self) -> bytes:
        return json.dumps(
            {
                "event_id": self.event_id,
                "order_id": self.order_id,
                "reservation_id": self.reservation_id,
                "action": self.action,
                "attempt": self.attempt,
                "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
                "idempotency_key": self.idempotency_key,
            },
            separators=(",", ":"),
        ).encode("utf-8")

    @classmethod
    def from_json(cls, payload: bytes | str) -> "TerminalizationCommand":
        raw = payload.decode("utf-8") if isinstance(payload, bytes) else payload
        data = json.loads(raw)
        return cls(
            event_id=str(data["event_id"]),
            order_id=int(data["order_id"]),
            reservation_id=int(data["reservation_id"]),
            action=cast(TerminalizationAction, data["action"]),
            attempt=int(data["attempt"]),
            created_at=_datetime_from_json(data["created_at"]),
            idempotency_key=str(data["idempotency_key"]),
        )


def terminalization_idempotency_key(
    reservation_id: int,
    action: TerminalizationAction,
) -> str:
    return f"terminalize:{reservation_id}:{action}"


def new_terminalization_command(
    order_id: int,
    reservation_id: int,
    action: TerminalizationAction,
    attempt: int = 1,
) -> TerminalizationCommand:
    return TerminalizationCommand(
        event_id=str(uuid4()),
        order_id=order_id,
        reservation_id=reservation_id,
        action=action,
        attempt=attempt,
        created_at=datetime.now(timezone.utc),
        idempotency_key=terminalization_idempotency_key(reservation_id, action),
    )


def _datetime_from_json(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed
