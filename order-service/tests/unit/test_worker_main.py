import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.entrypoints import worker_main


class WorkerMainTest(unittest.TestCase):
    def test_main_builds_runtime_and_starts_kafka_consumer(self) -> None:
        process_kafka_command = object()
        process_tasks = SimpleNamespace(
            process_kafka_command=process_kafka_command,
            set_terminalization_publisher=Mock(),
        )
        runtime = SimpleNamespace(process_tasks=process_tasks)

        with (
            patch.object(
                worker_main,
                "build_http_api",
                return_value=(object(), object(), runtime),
            ) as build_http_api,
            patch.object(worker_main, "KafkaTerminalizationProducer") as producer_cls,
            patch.object(
                worker_main,
                "KafkaTerminalizationCommandPublisher",
            ) as publisher_cls,
            patch.object(worker_main, "KafkaTerminalizationConsumer") as consumer_cls,
            patch.object(worker_main, "KafkaTerminalizationConsumerLoop") as consumer_loop_cls,
        ):
            producer = producer_cls.return_value
            publisher = publisher_cls.return_value
            consumer = consumer_cls.return_value
            consumer_loop = consumer_loop_cls.return_value
            worker_main.main()

        build_http_api.assert_called_once_with(run_background_worker=False)
        publisher_cls.assert_called_once_with(producer)
        process_tasks.set_terminalization_publisher.assert_called_once_with(publisher)
        consumer_loop_cls.assert_called_once_with(consumer, process_kafka_command)
        consumer_loop.run_forever.assert_called_once_with()
        consumer_loop.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
