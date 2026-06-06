from __future__ import annotations

from collections.abc import Callable
from threading import Condition, Thread


class ResetController:
    def __init__(self) -> None:
        self._condition = Condition()
        self._running = False
        self._last_error: Exception | None = None

    def run(self, reset_fn: Callable[[], None]) -> None:
        while True:
            with self._condition:
                while self._running:
                    self._condition.wait()
                self._running = True
                self._last_error = None
            try:
                reset_fn()
            except Exception as exc:
                with self._condition:
                    self._running = False
                    self._last_error = exc
                    self._condition.notify_all()
                raise
            with self._condition:
                self._running = False
                self._last_error = None
                self._condition.notify_all()
            return

    def trigger(self, reset_fn: Callable[[], None]) -> bool:
        with self._condition:
            if self._running:
                return False
            self._running = True
            self._last_error = None

        thread = Thread(target=self._run_background, args=(reset_fn,), daemon=True)
        thread.start()
        return True

    def _run_background(self, reset_fn: Callable[[], None]) -> None:
        error: Exception | None = None
        try:
            reset_fn()
        except Exception as exc:
            error = exc
        finally:
            with self._condition:
                self._running = False
                self._last_error = error
                self._condition.notify_all()

    @property
    def running(self) -> bool:
        with self._condition:
            return self._running
