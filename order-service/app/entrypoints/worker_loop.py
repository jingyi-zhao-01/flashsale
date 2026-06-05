import logging
from threading import Event, Lock, Thread


logger = logging.getLogger(__name__)


class TerminalizationWorkerLoop:
    def __init__(self, process_tasks) -> None:
        self._process_tasks = process_tasks
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None

    def start(self, poll_interval_seconds: float = 0.5, batch_size: int = 32) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()

            def _run() -> None:
                while not self._stop.is_set():
                    try:
                        self._process_tasks(limit=batch_size)
                    except Exception:
                        logger.exception(
                            "event=order_service_worker_iteration_failed"
                        )
                    self._stop.wait(poll_interval_seconds)

            self._thread = Thread(
                target=_run,
                name="order-terminalization-worker",
                daemon=True,
            )
            self._thread.start()

    def stop(self, join_timeout_seconds: float = 2.0) -> None:
        with self._lock:
            if not self._thread:
                return
            self._stop.set()
            self._thread.join(timeout=join_timeout_seconds)
            self._thread = None

    def wait_forever(self, idle_seconds: float = 3600.0) -> None:
        while not self._stop.wait(idle_seconds):
            pass
