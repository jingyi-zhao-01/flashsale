from typing import Protocol


class UserDirectoryClient(Protocol):
    def ensure_user_exists(self, user_id: int) -> None: ...
