from app.adapters.kafka_terminalization import (
    KafkaTerminalizationConsumer,
    KafkaTerminalizationConsumerLoop,
    KafkaTerminalizationCommandPublisher,
    KafkaTerminalizationProducer,
)
from app.entrypoints.http_api import build_http_api


def main() -> None:
    _, _, runtime = build_http_api(run_background_worker=False)
    producer = KafkaTerminalizationProducer()
    runtime.process_tasks.set_terminalization_publisher(
        KafkaTerminalizationCommandPublisher(producer)
    )
    consumer_loop = KafkaTerminalizationConsumerLoop(
        KafkaTerminalizationConsumer(),
        runtime.process_tasks.process_kafka_command,
    )
    try:
        consumer_loop.run_forever()
    finally:
        consumer_loop.stop()


if __name__ == "__main__":
    main()
