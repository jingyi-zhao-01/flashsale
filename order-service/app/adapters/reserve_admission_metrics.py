import logging
import time
from collections.abc import Mapping

from flashsale_shared.observability import start_span
from opentelemetry.trace import SpanKind

gate_logger = logging.getLogger("order-service.reserve-admission")


class ReserveAdmissionMetrics:
    """Lightweight metrics collector for the reserve admission gate.

    Emits OpenTelemetry spans and structured logs.  Prometheus metrics
    are registered via prometheus_client when available.
    """

    def __init__(self) -> None:
        self._allowed: dict[int, int] = {}
        self._rejected: dict[int, int] = {}
        self._errors: dict[str, int] = {}
        self._prometheus_available = False
        self._allowed_counter: object = None
        self._rejected_counter: object = None
        self._inflight_gauge: object = None
        self._wait_histogram: object = None
        self._error_counter: object = None
        self._setup_prometheus()

    # -- prometheus_client integration (best-effort) --------------------

    def _setup_prometheus(self) -> None:
        try:
            from prometheus_client import Counter, Gauge, Histogram  # noqa: F401
        except ImportError:
            return

        try:
            self._allowed_counter = Counter(
                "flashsale_reserve_admission_allowed_total",
                "Reserve admission permits allowed",
                ["product_id"],
            )
            self._rejected_counter = Counter(
                "flashsale_reserve_admission_rejected_total",
                "Reserve admission permits rejected",
                ["product_id"],
            )
            self._inflight_gauge = Gauge(
                "flashsale_reserve_admission_inflight",
                "Current reserve admission permits in flight",
                ["product_id"],
            )
            self._wait_histogram = Histogram(
                "flashsale_reserve_admission_wait_seconds",
                "Time spent waiting for reserve admission",
                ["product_id"],
            )
            self._error_counter = Counter(
                "flashsale_reserve_admission_errors_total",
                "Reserve admission gate errors",
                ["product_id", "error_type"],
            )
            self._prometheus_available = True
        except Exception:
            self._prometheus_available = False

    # -- public API ----------------------------------------------------

    def record_allowed(
        self,
        product_id: int,
        inflight: int,
        max_inflight: int,
        wait_ms: float,
    ) -> None:
        self._allowed[product_id] = self._allowed.get(product_id, 0) + 1
        gate_logger.info(
            "event=reserve_admission_gate product_id=%s decision=allowed "
            "inflight=%s max_inflight=%s wait_ms=%.2f",
            product_id,
            inflight,
            max_inflight,
            wait_ms,
        )
        if self._prometheus_available:
            labels = {"product_id": str(product_id)}
            self._allowed_counter.labels(**labels).inc()  # type: ignore[union-attr]
            self._inflight_gauge.labels(**labels).set(inflight)  # type: ignore[union-attr]
            self._wait_histogram.labels(**labels).observe(wait_ms / 1000.0)  # type: ignore[union-attr]

    def record_rejected(
        self,
        product_id: int,
        inflight: int,
        max_inflight: int,
    ) -> None:
        self._rejected[product_id] = self._rejected.get(product_id, 0) + 1
        gate_logger.warning(
            "event=reserve_admission_gate product_id=%s decision=rejected "
            "inflight=%s max_inflight=%s",
            product_id,
            inflight,
            max_inflight,
        )
        if self._prometheus_available:
            labels = {"product_id": str(product_id)}
            self._rejected_counter.labels(**labels).inc()  # type: ignore[union-attr]
            self._inflight_gauge.labels(**labels).set(inflight)  # type: ignore[union-attr]

    def record_inflight(self, product_id: int, inflight: int) -> None:
        if self._prometheus_available:
            self._inflight_gauge.labels(product_id=str(product_id)).set(inflight)  # type: ignore[union-attr]

    def record_error(self, product_id: int, error_type: str) -> None:
        key = f"{product_id}:{error_type}"
        self._errors[key] = self._errors.get(key, 0) + 1
        gate_logger.error(
            "event=reserve_admission_gate product_id=%s decision=error "
            "error_type=%s",
            product_id,
            error_type,
        )
        if self._prometheus_available:
            self._error_counter.labels(  # type: ignore[union-attr]
                product_id=str(product_id), error_type=error_type
            ).inc()

    # -- tracing -------------------------------------------------------

    @staticmethod
    def trace_gate(
        product_id: int,
        decision: str,
        inflight: int,
        max_inflight: int,
        wait_ms: float | None = None,
    ) -> None:
        attrs: dict[str, object] = {
            "flashsale.product_id": product_id,
            "flashsale.admission.decision": decision,
            "flashsale.admission.inflight": inflight,
            "flashsale.admission.max_inflight": max_inflight,
        }
        if wait_ms is not None:
            attrs["flashsale.admission.wait_ms"] = wait_ms
        span = start_span(
            "order-service",
            "reserve admission gate",
            kind=SpanKind.INTERNAL,
            attributes=attrs,
        )
        span.__enter__()
        span.__exit__(None, None, None)

    # -- snapshot ------------------------------------------------------

    def snapshot(self) -> Mapping[str, object]:
        return {
            "allowed": dict(self._allowed),
            "rejected": dict(self._rejected),
            "errors": dict(self._errors),
        }
