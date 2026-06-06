import unittest

from flashsale_shared.cache import NoOpCache


class CacheProtocolTest(unittest.TestCase):
    def test_noop_get_always_returns_none(self) -> None:
        cache = NoOpCache()
        self.assertIsNone(cache.get("any-key"))
        self.assertIsNone(cache.get("user:42"))

    def test_noop_set_does_not_raise(self) -> None:
        cache = NoOpCache()
        cache.set("key", "value")
        cache.set("key", {"nested": True}, ttl=300)

    def test_noop_delete_does_not_raise(self) -> None:
        cache = NoOpCache()
        cache.delete("key")
        cache.delete("missing-key")

    def test_noop_set_then_get_returns_none(self) -> None:
        cache = NoOpCache()
        cache.set("key", "stored-value")
        self.assertIsNone(cache.get("key"))


if __name__ == "__main__":
    unittest.main()
