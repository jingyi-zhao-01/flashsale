import os

from app.entrypoints.http_api import SERVICE_NAME, build_http_api

run_background_worker = os.getenv("ORDER_SERVICE_RUN_BACKGROUND_WORKER", "true").lower() == "true"
app, repository, runtime = build_http_api(run_background_worker=run_background_worker)

__all__ = [
    "SERVICE_NAME",
    "app",
    "repository",
    "runtime",
]
