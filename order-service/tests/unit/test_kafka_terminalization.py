import unittest
from datetime import datetime, timezone

from app.adapters.kafka_terminalization import KafkaTerminalizationCommandPublisher
from app.domain.terminalization_command import TerminalizationCommand


class FakeProducer:
    def __init__(self) -> None:
        self.published: list[tuple[str, TerminalizationCommand]] = []

    def publish(self, topic: str, command: TerminalizationCommand) -> None:
        self.published.append((topic, command))


class KafkaTerminalizationCommandPublisherTest(unittest.TestCase):
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
