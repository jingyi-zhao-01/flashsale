import unittest

from fastapi import HTTPException

from app.adapters.redis_reserve_admission_gate import RedisReserveAdmissionGate
from app.application.commands import CreateOrderCommand
from app.application.create_order_use_case import CreateOrderUseCase
from app.ports.reserve_admission_gate import NoOpReserveAdmissionGate


# ---------------------------------------------------------------------------
# Fake Redis – provides just enough of the Redis API for the admission gate
# ---------------------------------------------------------------------------

class FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, int] = {}
        self._expirations: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self._store[key] = self._store.get(key, 0) + 1
        return self._store[key]

    def decr(self, key: str) -> int:
        current = self._store.get(key, 0)
        self._store[key] = current - 1
        return self._store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self._expirations[key] = ttl
        return True

    def get(self, key: str) -> int | None:
        return self._store.get(key)

    def ping(self) -> bool:
        return True

    def eval(self, script: str, numkeys: int, *args: object) -> int:
        if numkeys != 1:
            raise AssertionError(f"unexpected numkeys={numkeys}")
        key = str(args[0])

        if "repair_negative_counter_and_incr" in script:
            ttl = int(args[1])
            current = self._store.get(key)
            if current is not None and current < 0:
                self._store.pop(key, None)
            counter = self.incr(key)
            if counter == 1:
                self.expire(key, ttl)
            return counter

        if "safe_release_counter" in script:
            current = self._store.get(key)
            if current is None:
                return 0
            if current <= 1:
                self._store.pop(key, None)
                return 0
            return self.decr(key)

        raise AssertionError(f"unexpected script: {script}")


# ---------------------------------------------------------------------------
# No-Op gate
# ---------------------------------------------------------------------------

class NoOpReserveAdmissionGateTest(unittest.TestCase):
    def test_acquire_returns_all_product_ids(self) -> None:
        gate = NoOpReserveAdmissionGate()
        result = gate.acquire([42, 99])
        self.assertEqual(result, [42, 99])

    def test_release_is_idempotent(self) -> None:
        gate = NoOpReserveAdmissionGate()
        gate.release([42, 99])  # does not raise


# ---------------------------------------------------------------------------
# Redis admission gate – acquire / release
# ---------------------------------------------------------------------------

class RedisReserveAdmissionGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = FakeRedis()
        self.gate = RedisReserveAdmissionGate(self.redis, max_inflight=2)

    def assert_counter_cleared(self, product_id: int) -> None:
        self.assertIn(
            self.redis.get(f"flashsale:reserve:admission:{product_id}"),
            (None, 0),
        )

    def test_acquire_single_product_under_limit(self) -> None:
        acquired = self.gate.acquire([42])
        self.assertEqual(acquired, [42])
        self.assertEqual(self.redis.get("flashsale:reserve:admission:42"), 1)

    def test_acquire_multiple_different_products(self) -> None:
        acquired = self.gate.acquire([42, 99])
        self.assertEqual(acquired, [42, 99])
        self.assertEqual(self.redis.get("flashsale:reserve:admission:42"), 1)
        self.assertEqual(self.redis.get("flashsale:reserve:admission:99"), 1)

    def test_release_decrements_counter(self) -> None:
        self.gate.acquire([42])
        self.gate.release([42])
        self.assert_counter_cleared(42)

    def test_release_empty_list_does_not_raise(self) -> None:
        self.gate.release([])

    def test_reject_when_over_max_inflight(self) -> None:
        self.gate.acquire([42])  # inflight=1
        self.gate.acquire([42])  # inflight=2
        with self.assertRaises(HTTPException) as ctx:
            self.gate.acquire([42])  # inflight=3 > 2
        self.assertEqual(ctx.exception.status_code, 429)
        # Counter should be decremented back to 2 after rejection
        self.assertEqual(self.redis.get("flashsale:reserve:admission:42"), 2)

    def test_partial_failure_releases_acquired_permits(self) -> None:
        self.gate.acquire([42])  # inflight=1 for product 42
        self.gate.acquire([42])  # inflight=2 for product 42 (at limit)
        # Acquire for [99, 42] – 99 succeeds, 42 should fail, 99 must be released
        with self.assertRaises(HTTPException) as ctx:
            self.gate.acquire([99, 42])
        self.assertEqual(ctx.exception.status_code, 429)
        self.assert_counter_cleared(99)
        self.assertEqual(self.redis.get("flashsale:reserve:admission:42"), 2)

    def test_acquire_empty_list_returns_empty(self) -> None:
        self.assertEqual(self.gate.acquire([]), [])

    def test_default_max_inflight_from_config(self) -> None:
        gate = RedisReserveAdmissionGate(self.redis)
        self.assertEqual(gate._max_inflight, 2)  # RESERVE_ADMISSION_MAX_INFLIGHT default

    def test_ttl_set_on_first_incr(self) -> None:
        self.gate.acquire([42])
        self.assertIn("flashsale:reserve:admission:42", self.redis._expirations)

    def test_second_incr_does_not_reset_ttl(self) -> None:
        self.gate.acquire([42])
        first_exp = self.redis._expirations.get("flashsale:reserve:admission:42")
        self.gate.acquire([42])
        second_exp = self.redis._expirations.get("flashsale:reserve:admission:42")
        # TTL is only set on counter==1, so second INCR should not change it
        self.assertEqual(first_exp, second_exp)

    def test_release_missing_key_does_not_create_negative_counter(self) -> None:
        self.gate.release([42])
        self.assert_counter_cleared(42)

    def test_acquire_repairs_negative_counter_before_incrementing(self) -> None:
        self.redis._store["flashsale:reserve:admission:42"] = -3
        acquired = self.gate.acquire([42])
        self.assertEqual(acquired, [42])
        self.assertEqual(self.redis.get("flashsale:reserve:admission:42"), 1)
        self.assertIn("flashsale:reserve:admission:42", self.redis._expirations)


# ---------------------------------------------------------------------------
# CreateOrderUseCase integration with admission gate
# ---------------------------------------------------------------------------

class FakeProductReservationClient:
    def __init__(self, stock: int = 10, price: float = 9.99) -> None:
        self.stock = stock
        self.price = price
        self.reservations: dict[int, str] = {}
        self.confirm_calls = 0
        self.cancel_calls = 0
        self._next_id = 1

    def reserve(self, product_id: int, quantity: int) -> tuple[float, int, int]:
        if self.stock < quantity:
            raise HTTPException(
                status_code=409,
                detail="insufficient stock",
            )
        self.stock -= quantity
        rid = self._next_id
        self._next_id += 1
        self.reservations[rid] = "reserved"
        return self.price, quantity, rid

    def release(self, reservation_ids: list[int]) -> None:
        for rid in reservation_ids:
            if rid in self.reservations:
                self.stock += 1
                del self.reservations[rid]

    def terminalize(self, reservation_id: int, action: str) -> tuple[bool, str | None]:
        if action == "confirm":
            self.confirm_calls += 1
            self.reservations[reservation_id] = "confirmed"
        else:
            self.cancel_calls += 1
            self.reservations[reservation_id] = "cancelled"
        return True, None


class FakeOrderRepository:
    def __init__(self) -> None:
        self._orders: dict[int, object] = {}
        self._counter = 1

    def create(self, **kw: object) -> object:
        from datetime import datetime, timezone

        from app.domain.order import Order

        order_id = self._counter
        self._counter += 1
        order = Order(
            id=order_id,
            user_id=int(kw.get("user_id", 0)),
            created_at=datetime.now(timezone.utc),
            total_amount=float(kw.get("total_amount", 0)),
            status=str(kw.get("status", "pending")),
            payment_status=str(kw.get("payment_status", "pending")),
            items=tuple(kw.get("items", [])),
            reservation_ids=tuple(kw.get("reservation_ids", [])),
            idempotency_key=kw.get("idempotency_key"),  # type: ignore[arg-type]
        )
        self._orders[order_id] = order
        return order

    def get_by_idempotency_key(self, key: str | None) -> object | None:
        if key is None:
            return None
        for order in self._orders.values():
            if getattr(order, "idempotency_key", None) == key:
                return order
        return None

    def get(self, order_id: int) -> object | None:
        return self._orders.get(order_id)

    def list_all(self) -> list[object]:
        return list(self._orders.values())

    def list_stale(self, _before: object) -> list[object]:
        return []

    def update_state(self, *args: object, **kw: object) -> None:
        pass


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.orders = FakeOrderRepository()

    def create_order(self, **kw: object) -> object:
        order = self.orders.create(**kw)
        return order


class FakeUserDirectoryClient:
    def ensure_user_exists(self, user_id: int) -> None:
        pass


class AdmissionGateOrderUseCaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.products = FakeProductReservationClient(stock=5)
        self.uow = FakeUnitOfWork()
        self.users = FakeUserDirectoryClient()

    def _make_sut(self, admission: object | None = None) -> CreateOrderUseCase:
        return CreateOrderUseCase(
            uow=self.uow,
            users=self.users,
            products=self.products,
            admission=admission,  # type: ignore[arg-type]
        )

    def test_noop_gate_allows_order(self) -> None:
        sut = self._make_sut(admission=NoOpReserveAdmissionGate())
        command = CreateOrderCommand(user_id=1, items=((42, 1),))
        order = sut.create_order(command)
        self.assertEqual(order.status, "pending")
        self.assertEqual(self.products.stock, 4)

    def test_none_admission_defaults_to_noop(self) -> None:
        sut = CreateOrderUseCase(
            uow=self.uow,
            users=self.users,
            products=self.products,
        )
        command = CreateOrderCommand(user_id=1, items=((42, 1),))
        order = sut.create_order(command)
        self.assertEqual(order.status, "pending")

    def test_admission_rejection_propagates_429(self) -> None:
        redis = FakeRedis()
        gate = RedisReserveAdmissionGate(redis, max_inflight=0)
        sut = self._make_sut(admission=gate)
        command = CreateOrderCommand(user_id=1, items=((42, 1),))
        with self.assertRaises(HTTPException) as ctx:
            sut.create_order(command)
        self.assertEqual(ctx.exception.status_code, 429)

    def test_admission_permit_released_after_order_success(self) -> None:
        redis = FakeRedis()
        gate = RedisReserveAdmissionGate(redis, max_inflight=2)
        sut = self._make_sut(admission=gate)
        command = CreateOrderCommand(user_id=1, items=((42, 1),))
        sut.create_order(command)
        self.assertIn(redis.get("flashsale:reserve:admission:42"), (None, 0))

    def test_admission_permit_released_after_reserve_failure(self) -> None:
        redis = FakeRedis()
        gate = RedisReserveAdmissionGate(redis, max_inflight=2)
        sut = self._make_sut(admission=gate)
        self.products.stock = 0  # will cause 409
        command = CreateOrderCommand(user_id=1, items=((42, 1),))
        with self.assertRaises(HTTPException):
            sut.create_order(command)
        self.assertIn(redis.get("flashsale:reserve:admission:42"), (None, 0))

    def test_idempotency_replay_does_not_acquire_admission(self) -> None:
        redis = FakeRedis()
        gate = RedisReserveAdmissionGate(redis, max_inflight=2)
        sut = self._make_sut(admission=gate)
        command = CreateOrderCommand(user_id=1, items=((42, 1),), idempotency_key="key-1")

        first = sut.create_order(command)
        second = sut.create_order(command)
        self.assertEqual(first.id, second.id)
        # The idempotency path returns before acquire, so counter should be at 0
        # (the first call acquires then releases; the second never acquires)
        self.assertIn(redis.get("flashsale:reserve:admission:42"), (None, 0))


if __name__ == "__main__":
    unittest.main()
