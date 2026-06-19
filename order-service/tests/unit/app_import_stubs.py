import os
import sys
import types

os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("ORDER_SERVICE_RUN_BACKGROUND_WORKER", "false")

psycopg_stub = types.ModuleType("psycopg")
psycopg_rows_stub = types.ModuleType("psycopg.rows")
psycopg_stub.connect = None
psycopg_rows_stub.dict_row = object()
sys.modules.setdefault("psycopg", psycopg_stub)
sys.modules.setdefault("psycopg.rows", psycopg_rows_stub)

confluent_kafka_stub = types.ModuleType("confluent_kafka")


class _StubProducer:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def produce(self, *_args, **kwargs) -> None:
        on_delivery = kwargs.get("on_delivery")
        if on_delivery is not None:
            on_delivery(None, object())

    def flush(self) -> None:
        return


class _StubConsumer:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def subscribe(self, *_args, **_kwargs) -> None:
        return

    def poll(self, *_args, **_kwargs):
        return None

    def commit(self, *_args, **_kwargs) -> None:
        return

    def close(self) -> None:
        return


confluent_kafka_stub.Producer = _StubProducer
confluent_kafka_stub.Consumer = _StubConsumer
sys.modules.setdefault("confluent_kafka", confluent_kafka_stub)

redis_stub = types.ModuleType("redis")
redis_exceptions_stub = types.ModuleType("redis.exceptions")


class _StubRedisError(Exception):
    pass


class _StubRedis:
    @classmethod
    def from_url(cls, *_args, **_kwargs):
        return cls()

    def ping(self) -> None:
        return


redis_stub.Redis = _StubRedis
redis_exceptions_stub.RedisError = _StubRedisError
redis_stub.exceptions = redis_exceptions_stub
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_exceptions_stub)
