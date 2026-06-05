"""Cache abstraction for flashsale services.

Defines a pluggable cache interface.  The default implementation is a no-op
that satisfies the protocol without caching anything.  When Redis is
introduced, a ``RedisCache`` adapter can be dropped in without changing
call-sites.

Usage::

    from flashsale_shared.cache import Cache, NoOpCache

    class UserService:
        def __init__(self, repository, cache: Cache | None = None) -> None:
            self._cache = cache or NoOpCache()

        def get_user(self, user_id: int):
            cached = self._cache.get(f"user:{user_id}")
            if cached is not None:
                return cached
            user = self._repository.get_user(user_id)
            if user:
                self._cache.set(f"user:{user_id}", user, ttl=30)
            return user
"""

from typing import Any, Protocol


class Cache(Protocol):
    """Pluggable cache for look-aside caching patterns.

    Implementations:
        * ``NoOpCache`` – default, no caching.
        * ``RedisCache`` – future, backed by Redis.
    """

    def get(self, key: str) -> Any | None:
        """Return the cached value or ``None`` on miss."""
        ...

    def set(self, key: str, value: Any, ttl: int = 60) -> None:
        """Store *value* under *key* with the given TTL in seconds."""
        ...

    def delete(self, key: str) -> None:
        """Invalidate *key*."""
        ...


class NoOpCache:
    """Cache implementation that never stores anything."""

    def get(self, key: str) -> None:  # type: ignore[override]
        return None

    def set(self, key: str, value: Any, ttl: int = 60) -> None:
        pass

    def delete(self, key: str) -> None:
        pass
