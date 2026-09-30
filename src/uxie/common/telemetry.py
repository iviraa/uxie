"""OpenTelemetry traces + metrics. No-op unless an OTLP endpoint is configured.

The endpoint and auth headers come from config.otel_settings() (a Secrets Manager secret),
so Grafana Cloud or any other OTLP backend works.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import context as otel_context
from opentelemetry import metrics, trace
from opentelemetry.propagate import extract, inject
from opentelemetry.trace import Span, Status, StatusCode

_providers: list[Any] = []

tracer = trace.get_tracer("uxie")
meter = metrics.get_meter("uxie")

reviews = meter.create_counter("uxie.reviews", description="PR reviews by outcome")
findings = meter.create_counter("uxie.findings", description="posted findings by severity")
verdicts = meter.create_counter("uxie.verdicts", description="sandbox verdicts")
tokens = meter.create_counter("uxie.llm.tokens", unit="{token}")
cost = meter.create_counter("uxie.llm.cost", unit="USD")
review_latency = meter.create_histogram("uxie.review.duration", unit="s")
proof_latency = meter.create_histogram("uxie.proof.duration", unit="s")


def setup(service: str, endpoint: str | None, headers: dict[str, str] | None = None) -> bool:
    """Install exporting providers once per process. Returns whether telemetry is on."""
    if _providers or not endpoint:
        return bool(_providers)
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service, "service.namespace": "uxie"})
    tp = TracerProvider(resource=resource)
    tp.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces", headers=headers))
    )
    mp = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics", headers=headers), 30_000
            )
        ],
    )
    trace.set_tracer_provider(tp)
    metrics.set_meter_provider(mp)
    _providers.extend([tp, mp])
    return True


def flush() -> None:
    """Lambda freezes between invocations; push buffered spans/metrics out first."""
    for p in _providers:
        p.force_flush()


def carrier() -> dict[str, str]:
    """The current trace context as W3C headers (traceparent), to ride along in a message."""
    out: dict[str, str] = {}
    inject(out)
    return out


@contextmanager
def continued(headers: dict[str, str] | None) -> Iterator[None]:
    """Make spans in this block children of the trace that sent the message."""
    token = otel_context.attach(extract(headers or {}))
    try:
        yield
    finally:
        otel_context.detach(token)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Span]:
    with tracer.start_as_current_span(name) as s:
        for k, v in attrs.items():
            if v is not None:
                s.set_attribute(k, v)
        try:
            yield s
        except Exception as e:
            s.record_exception(e)
            s.set_status(Status(StatusCode.ERROR, str(e)[:200]))
            raise
