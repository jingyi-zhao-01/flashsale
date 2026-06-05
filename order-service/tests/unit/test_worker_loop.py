import time
import unittest
from threading import Event

from app.entrypoints.worker_loop import TerminalizationWorkerLoop


class TerminalizationWorkerLoopTest(unittest.TestCase):
    def test_worker_loop_continues_after_one_iteration_failure(self) -> None:
        second_call = Event()
        calls = 0

        def process_tasks(*, limit: int) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("boom")
            second_call.set()

        loop = TerminalizationWorkerLoop(process_tasks)
        loop.start(poll_interval_seconds=0.01, batch_size=7)
        try:
            self.assertTrue(second_call.wait(timeout=1))
        finally:
            loop.stop()

        self.assertGreaterEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
