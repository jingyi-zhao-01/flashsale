import logging
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from urllib.parse import parse_qsl
from threading import Lock
from uuid import uuid4

from fastapi import Request, Response
from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

_TRACE_LOCK = Lock()
_TRACE_READY = False


class TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record.trace_id = format(span_context.trace_id, "032x")
            record.span_id = format(span_context.span_id, "016x")
        else:
            record.trace_id = "-"
            record.span_id = "-"
        return True


def configure_service_logger(service_name: str) -> logging.Logger:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format=(
            "%(asctime)s %(levelname)s %(name)s trace_id=%(trace_id)s "
            "span_id=%(span_id)s %(message)s"
        ),
    )
    _install_trace_filter()
    return logging.getLogger(service_name)


def initialize_tracing(service_name: str) -> None:
    global _TRACE_READY
    with _TRACE_LOCK:
        if _TRACE_READY:
            return
        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
        if endpoint:
            headers = dict(
                parse_qsl(
                    os.getenv("OTEL_EXPORTER_OTLP_HEADERS", ""),
                    separator=",",
                    keep_blank_values=True,
                )
            )
            provider.add_span_processor(
                BatchSpanProcessor(
                    OTLPSpanExporter(
                        endpoint=endpoint,
                        headers=headers or None,
                    )
                )
            )
        trace.set_tracer_provider(provider)
        _TRACE_READY = True


def create_request_logging_middleware(
    logger: logging.Logger, service_name: str
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    tracer = trace.get_tracer(service_name)

    async def middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        request_id = request.headers.get("x-request-id", str(uuid4()))
        request_path = request_path_label(request)
        operation = request_path.strip("/").replace("/", "_").replace("-", "_") or "root"
        service_class = "maintenance" if request_path == "/admin" or request_path.startswith("/admin/") else "api"
        span_name = f"{request.method} {request_path}"
        context = propagate.extract(dict(request.headers))
        with tracer.start_as_current_span(
            span_name,
            context=context,
            kind=SpanKind.SERVER,
            attributes={
                "http.request.method": request.method,
                "url.path": request.url.path,
                "flashsale.request_id": request_id,
                "flashsale.service_class": service_class,
                "flashsale.operation": operation,
            },
        ) as span:
            try:
                response = await call_next(request)
            except Exception as exc:
                span.record_exception(exc)
                span.set_attribute("error", True)
                raise
            duration_ms = (time.perf_counter() - start) * 1000
            span.set_attribute("http.response.status_code", response.status_code)
            span.set_attribute("flashsale.duration_ms", duration_ms)
            log_fn = logger.error if response.status_code >= 400 else logger.info
            log_fn(
                "event=request service=%s service_class=%s operation=%s request_id=%s method=%s path=%s status_code=%s duration_ms=%.2f",
                service_name,
                service_class,
                operation,
                request_id,
                request.method,
                request_path,
                response.status_code,
                duration_ms,
            )
            response.headers["x-request-id"] = request_id
            return response

    return middleware


def inject_trace_headers(
    headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    carrier = dict(headers or {})
    propagate.inject(carrier)
    return carrier


def start_span(
    service_name: str,
    name: str,
    *,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: Mapping[str, object] | None = None,
) -> AbstractContextManager[trace.Span]:
    tracer = trace.get_tracer(service_name)
    return tracer.start_as_current_span(name, kind=kind, attributes=attributes)


def request_path_label(request: Request) -> str:
    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    if isinstance(route_path, str) and route_path:
        return route_path
    return request.url.path


def _install_trace_filter() -> None:
    root = logging.getLogger()
    for handler in root.handlers:
        if not any(isinstance(f, TraceContextFilter) for f in handler.filters):
            handler.addFilter(TraceContextFilter())
