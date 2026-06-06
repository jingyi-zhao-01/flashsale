from app.entrypoints.http_api import build_http_api
from app.entrypoints.worker_loop import TerminalizationWorkerLoop


def main() -> None:
    _, _, runtime = build_http_api(run_background_worker=False)
    worker = TerminalizationWorkerLoop(runtime.process_tasks.process)
    worker.start()
    try:
        worker.wait_forever()
    finally:
        worker.stop()


if __name__ == "__main__":
    main()
