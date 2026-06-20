from collections.abc import Callable
import logging
from threading import Event
from typing import Any

from app.config import (
    KAFKA_ACCESS_CERT,
    KAFKA_ACCESS_KEY,
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_PASSWORD,
    KAFKA_SECURITY_PROTOCOL,
    KAFKA_SSL_CA_LOCATION,
    KAFKA_TERMINALIZATION_CONSUMER_GROUP,
    KAFKA_TERMINALIZATION_CONSUMER_POLL_SECONDS,
    KAFKA_TERMINALIZATION_DLQ_TOPIC,
    KAFKA_TERMINALIZATION_RETRY_TOPIC,
    KAFKA_TERMINALIZATION_TOPIC,
    KAFKA_USERNAME,
)
from app.domain.statuses import TerminalizationAction
from app.domain.terminalization_command import (
    TerminalizationCommand,
    new_terminalization_command,
)

logger = logging.getLogger(__name__)


class KafkaUnavailableError(RuntimeError):
    pass


def _looks_like_pem(value: str, label: str) -> bool:
    return value.strip().startswith(f"-----BEGIN {label}-----")


def kafka_connection_config(
    bootstrap_servers: str,
    security_protocol: str = KAFKA_SECURITY_PROTOCOL,
    username: str = KAFKA_USERNAME,
    password: str = KAFKA_PASSWORD,
    access_cert: str = KAFKA_ACCESS_CERT,
    access_key: str = KAFKA_ACCESS_KEY,
    ssl_ca_location: str = KAFKA_SSL_CA_LOCATION,
) -> dict[str, str]:
    if not bootstrap_servers:
        raise KafkaUnavailableError("KAFKA_BOOTSTRAP_SERVERS is required")

    config = {
        "bootstrap.servers": bootstrap_servers,
    }
    if security_protocol:
        config["security.protocol"] = security_protocol
    if username or password:
        config["sasl.mechanism"] = "PLAIN"
        if username:
            config["sasl.username"] = username
        if password:
            config["sasl.password"] = password
    if access_cert and _looks_like_pem(access_cert, "CERTIFICATE"):
        config["ssl.certificate.pem"] = access_cert
    if access_key and _looks_like_pem(access_key, "PRIVATE KEY"):
        config["ssl.key.pem"] = access_key
    if security_protocol.endswith("SSL") and ssl_ca_location:
        config["ssl.ca.location"] = ssl_ca_location
    return config


class KafkaTerminalizationProducer:
    def __init__(
        self,
        bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
        producer: Any | None = None,
    ) -> None:
        if producer is not None:
            self._producer = producer
            return
        try:
            from confluent_kafka import Producer
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise KafkaUnavailableError("confluent-kafka is not installed") from exc
        self._producer = Producer(kafka_connection_config(bootstrap_servers))

    def publish(self, topic: str, command: TerminalizationCommand) -> None:
        errors: list[BaseException] = []

        def on_delivery(error: BaseException | None, message: Any) -> None:
            if error is not None:
                errors.append(error)

        self._producer.produce(
            topic,
            key=command.message_key.encode("utf-8"),
            value=command.to_json(),
            on_delivery=on_delivery,
        )
        self._producer.flush()
        if errors:
            raise KafkaUnavailableError(str(errors[0]))


class KafkaTerminalizationCommandPublisher:
    def __init__(
        self,
        producer: KafkaTerminalizationProducer,
        primary_topic: str = KAFKA_TERMINALIZATION_TOPIC,
        retry_topic: str = KAFKA_TERMINALIZATION_RETRY_TOPIC,
        dlq_topic_name: str = KAFKA_TERMINALIZATION_DLQ_TOPIC,
    ) -> None:
        self._producer = producer
        self._primary_topic = primary_topic
        self._retry_topic = retry_topic
        self._dlq_topic = dlq_topic_name

    def publish(
        self,
        order_id: int,
        reservation_ids: list[int],
        action: TerminalizationAction,
    ) -> None:
        for reservation_id in reservation_ids:
            command = new_terminalization_command(order_id, reservation_id, action)
            self._producer.publish(self._primary_topic, command)
            logger.info(
                "event=kafka_terminalization_published topic=%s order_id=%s reservation_id=%s action=%s attempt=%s",
                self._primary_topic,
                command.order_id,
                command.reservation_id,
                command.action,
                command.attempt,
            )

    def publish_retry(self, command: TerminalizationCommand, error: str) -> None:
        retry_command = TerminalizationCommand(
            event_id=command.event_id,
            order_id=command.order_id,
            reservation_id=command.reservation_id,
            action=command.action,
            attempt=command.attempt + 1,
            created_at=command.created_at,
            idempotency_key=command.idempotency_key,
        )
        self._producer.publish(self._retry_topic, retry_command)
        logger.warning(
            "event=kafka_terminalization_retry_published topic=%s order_id=%s reservation_id=%s action=%s attempt=%s error=%s",
            self._retry_topic,
            retry_command.order_id,
            retry_command.reservation_id,
            retry_command.action,
            retry_command.attempt,
            error,
        )

    def publish_dead_letter(self, command: TerminalizationCommand, error: str) -> None:
        self._producer.publish(self._dlq_topic, command)
        logger.error(
            "event=kafka_terminalization_dlq_published topic=%s order_id=%s reservation_id=%s action=%s attempt=%s error=%s",
            self._dlq_topic,
            command.order_id,
            command.reservation_id,
            command.action,
            command.attempt,
            error,
        )


class KafkaTerminalizationConsumer:
    def __init__(
        self,
        bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
        group_id: str = KAFKA_TERMINALIZATION_CONSUMER_GROUP,
        consumer: Any | None = None,
    ) -> None:
        if consumer is not None:
            self._consumer = consumer
            return
        try:
            from confluent_kafka import Consumer
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise KafkaUnavailableError("confluent-kafka is not installed") from exc
        self._consumer = Consumer({
            **kafka_connection_config(bootstrap_servers),
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        })

    def subscribe(self) -> None:
        self._consumer.subscribe(
            [KAFKA_TERMINALIZATION_TOPIC, KAFKA_TERMINALIZATION_RETRY_TOPIC]
        )

    def poll_command(self, timeout_seconds: float) -> tuple[Any, TerminalizationCommand] | None:
        message = self._consumer.poll(timeout_seconds)
        if message is None:
            return None
        if message.error():
            raise KafkaUnavailableError(str(message.error()))
        return message, TerminalizationCommand.from_json(message.value())

    def commit(self, message: Any) -> None:
        self._consumer.commit(message=message, asynchronous=False)

    def close(self) -> None:
        self._consumer.close()


class KafkaTerminalizationConsumerLoop:
    def __init__(
        self,
        consumer: KafkaTerminalizationConsumer,
        process_command: Callable[[TerminalizationCommand], None],
    ) -> None:
        self._consumer = consumer
        self._process_command = process_command
        self._stop = Event()

    def run_forever(
        self,
        poll_seconds: float = KAFKA_TERMINALIZATION_CONSUMER_POLL_SECONDS,
    ) -> None:
        self._consumer.subscribe()
        try:
            while not self._stop.is_set():
                polled = self._consumer.poll_command(timeout_seconds=poll_seconds)
                if polled is None:
                    continue
                message, command = polled
                self._process_command(command)
                self._consumer.commit(message)
        finally:
            self._consumer.close()

    def stop(self) -> None:
        self._stop.set()


def dlq_topic() -> str:
    return KAFKA_TERMINALIZATION_DLQ_TOPIC
