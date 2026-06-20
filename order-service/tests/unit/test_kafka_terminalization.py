import unittest
from datetime import datetime, timezone

from app.adapters.kafka_terminalization import (
    KafkaTerminalizationCommandPublisher,
    kafka_connection_config,
)
from app.domain.terminalization_command import TerminalizationCommand


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[str, TerminalizationCommand]] = []

    def publish(self, topic: str, command: TerminalizationCommand) -> None:
        self.published.append((topic, command))


class KafkaTerminalizationCommandPublisherTest(unittest.TestCase):
    def test_connection_config_keeps_plaintext_local_defaults_minimal(self) -> None:
        config = kafka_connection_config("flashsale-kafka:9092")

        self.assertEqual(config, {"bootstrap.servers": "flashsale-kafka:9092"})

    def test_connection_config_includes_sasl_ca_pem_and_client_cert_fields(self) -> None:
        config = kafka_connection_config(
            "example.aivencloud.com:19154",
            security_protocol="SASL_SSL",
            username="avnadmin",
            password="secret",
            ca_cert="-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----",
            access_cert="-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----",
            access_key="-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----",
        )

        self.assertEqual(config["bootstrap.servers"], "example.aivencloud.com:19154")
        self.assertEqual(config["security.protocol"], "SASL_SSL")
        self.assertEqual(config["sasl.mechanism"], "PLAIN")
        self.assertEqual(config["sasl.username"], "avnadmin")
        self.assertEqual(config["sasl.password"], "secret")
        self.assertEqual(
            config["ssl.ca.pem"],
            "-----BEGIN CERTIFICATE-----\nca\n-----END CERTIFICATE-----",
        )
        self.assertNotIn("ssl.ca.location", config)
        self.assertEqual(
            config["ssl.certificate.pem"],
            "-----BEGIN CERTIFICATE-----\ncert\n-----END CERTIFICATE-----",
        )
        self.assertEqual(
            config["ssl.key.pem"],
            "-----BEGIN PRIVATE KEY-----\nkey\n-----END PRIVATE KEY-----",
        )

    def test_connection_config_ignores_redacted_client_cert_fields(self) -> None:
        config = kafka_connection_config(
            "example.aivencloud.com:19154",
            security_protocol="SASL_SSL",
            username="avnadmin",
            password="secret",
            ca_cert="<redacted>",
            access_cert="<redacted>",
            access_key="<redacted>",
            ssl_ca_location="/custom/ca.pem",
        )

        self.assertEqual(config["security.protocol"], "SASL_SSL")
        self.assertEqual(config["sasl.username"], "avnadmin")
        self.assertEqual(config["sasl.password"], "secret")
        self.assertNotIn("ssl.ca.pem", config)
        self.assertEqual(config["ssl.ca.location"], "/custom/ca.pem")
        self.assertNotIn("ssl.certificate.pem", config)
        self.assertNotIn("ssl.key.pem", config)

    def test_publish_writes_primary_commands_keyed_by_reservation(self) -> None:
        producer = FakeProducer()
        publisher = KafkaTerminalizationCommandPublisher(
            producer=producer,
            primary_topic="primary",
            retry_topic="retry",
            dlq_topic_name="dlq",
        )

        publisher.publish(order_id=7, reservation_ids=[42], action="confirm")

        self.assertEqual(len(producer.published), 1)
        topic, command = producer.published[0]
        self.assertEqual(topic, "primary")
        self.assertEqual(command.order_id, 7)
        self.assertEqual(command.reservation_id, 42)
        self.assertEqual(command.message_key, "42")
        self.assertEqual(command.attempt, 1)

    def test_retry_increments_attempt_and_uses_retry_topic(self) -> None:
        producer = FakeProducer()
        publisher = KafkaTerminalizationCommandPublisher(
            producer=producer,
            primary_topic="primary",
            retry_topic="retry",
            dlq_topic_name="dlq",
        )
        command = TerminalizationCommand(
            event_id="evt-2",
            order_id=8,
            reservation_id=43,
            action="cancel",
            attempt=2,
            created_at=datetime.now(timezone.utc),
            idempotency_key="terminalize:43:cancel",
        )

        publisher.publish_retry(command, "timeout")

        topic, retry = producer.published[0]
        self.assertEqual(topic, "retry")
        self.assertEqual(retry.attempt, 3)
        self.assertEqual(retry.event_id, command.event_id)

    def test_dead_letter_uses_dlq_topic_without_changing_payload(self) -> None:
        producer = FakeProducer()
        publisher = KafkaTerminalizationCommandPublisher(
            producer=producer,
            primary_topic="primary",
            retry_topic="retry",
            dlq_topic_name="dlq",
        )
        command = TerminalizationCommand(
            event_id="evt-3",
            order_id=9,
            reservation_id=44,
            action="confirm",
            attempt=5,
            created_at=datetime.now(timezone.utc),
            idempotency_key="terminalize:44:confirm",
        )

        publisher.publish_dead_letter(command, "poison")

        self.assertEqual(producer.published, [("dlq", command)])


if __name__ == "__main__":
    unittest.main()
