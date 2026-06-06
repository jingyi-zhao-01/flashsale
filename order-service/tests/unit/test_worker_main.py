import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.entrypoints import worker_main


class WorkerMainTest(unittest.TestCase):
    def test_main_builds_runtime_and_starts_worker(self) -> None:
        process = object()
        runtime = SimpleNamespace(process_tasks=SimpleNamespace(process=process))

        with (
            patch.object(
                worker_main,
                "build_http_api",
                return_value=(object(), object(), runtime),
            ) as build_http_api,
            patch.object(worker_main, "TerminalizationWorkerLoop") as worker_loop_cls,
        ):
            worker = worker_loop_cls.return_value
            worker_main.main()

        build_http_api.assert_called_once_with(run_background_worker=False)
        worker_loop_cls.assert_called_once_with(process)
        worker.start.assert_called_once_with()
        worker.wait_forever.assert_called_once_with()
        worker.stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
