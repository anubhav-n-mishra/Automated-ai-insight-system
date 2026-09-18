"""Metrics and tracing.

Metrics are a dependency-free in-process registry rendered in the Prometheus
text exposition format: an operator gets ``/metrics`` out of the box without
pulling a client library into the base install. Tracing is opt-in and degrades
to no-ops when the OpenTelemetry extra is not installed.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

Labels = tuple[tuple[str, str], ...]

# Seconds. Chosen to straddle both a fast CSV run and a slow LLM round trip.
DEFAULT_BUCKETS: tuple[float, ...] = (0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)


def _labels(mapping: Mapping[str, str] | None) -> Labels:
    if not mapping:
        return ()
    return tuple(sorted((str(k), str(v)) for k, v in mapping.items()))


def _render_labels(labels: Labels) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{key}="{_escape(value)}"' for key, value in labels)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: list[int] = field(default_factory=list)
    total: float = 0.0
    count: int = 0

    def __post_init__(self) -> None:
        if not self.counts:
            self.counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self.total += value
        self.count += 1
        for index, bound in enumerate(self.buckets):
            if value <= bound:
                self.counts[index] += 1
                return
        self.counts[-1] += 1


class MetricsRegistry:
    """Thread-safe counters, gauges and histograms."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, Labels], float] = defaultdict(float)
        self._gauges: dict[tuple[str, Labels], float] = {}
        self._histograms: dict[tuple[str, Labels], _Histogram] = {}
        self._help: dict[str, str] = {}

    def counter(
        self,
        name: str,
        value: float = 1.0,
        labels: Mapping[str, str] | None = None,
        *,
        help_text: str = "",
    ) -> None:
        with self._lock:
            self._help.setdefault(name, help_text)
            self._counters[(name, _labels(labels))] += value

    def gauge(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
        *,
        help_text: str = "",
    ) -> None:
        with self._lock:
            self._help.setdefault(name, help_text)
            self._gauges[(name, _labels(labels))] = value

    def observe(
        self,
        name: str,
        value: float,
        labels: Mapping[str, str] | None = None,
        *,
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
        help_text: str = "",
    ) -> None:
        key = (name, _labels(labels))
        with self._lock:
            self._help.setdefault(name, help_text)
            histogram = self._histograms.get(key)
            if histogram is None:
                histogram = _Histogram(buckets=buckets)
                self._histograms[key] = histogram
            histogram.observe(value)

    @contextmanager
    def timer(self, name: str, labels: Mapping[str, str] | None = None) -> Iterator[None]:
        """Observe the duration of a block into a histogram named ``name``."""
        started = time.perf_counter()
        try:
            yield
        finally:
            self.observe(name, time.perf_counter() - started, labels)

    def render(self) -> str:
        """Prometheus text exposition format (version 0.0.4)."""
        lines: list[str] = []
        with self._lock:
            for metric_type, series in (
                ("counter", self._counters),
                ("gauge", self._gauges),
            ):
                for name in sorted({key[0] for key in series}):
                    if self._help.get(name):
                        lines.append(f"# HELP {name} {self._help[name]}")
                    lines.append(f"# TYPE {name} {metric_type}")
                    for (metric_name, labels), value in sorted(series.items()):
                        if metric_name == name:
                            lines.append(f"{name}{_render_labels(labels)} {value}")

            for name in sorted({key[0] for key in self._histograms}):
                if self._help.get(name):
                    lines.append(f"# HELP {name} {self._help[name]}")
                lines.append(f"# TYPE {name} histogram")
                for (metric_name, labels), histogram in sorted(self._histograms.items()):
                    if metric_name != name:
                        continue
                    cumulative = 0
                    for index, bound in enumerate(histogram.buckets):
                        cumulative += histogram.counts[index]
                        bucket_labels = (*labels, ("le", str(bound)))
                        lines.append(f"{name}_bucket{_render_labels(bucket_labels)} {cumulative}")
                    cumulative += histogram.counts[-1]
                    inf_labels = (*labels, ("le", "+Inf"))
                    lines.append(f"{name}_bucket{_render_labels(inf_labels)} {cumulative}")
                    lines.append(f"{name}_sum{_render_labels(labels)} {histogram.total}")
                    lines.append(f"{name}_count{_render_labels(labels)} {histogram.count}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


METRICS = MetricsRegistry()


def setup_tracing(service_name: str, endpoint: str | None, version: str) -> bool:
    """Wire OpenTelemetry if the extra is installed. Returns whether it engaged."""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return False

    resource = Resource.create({"service.name": service_name, "service.version": version})
    provider = TracerProvider(resource=resource)

    if endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            return False
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))

    trace.set_tracer_provider(provider)
    return True


def instrument_app(app: Any) -> None:
    """Attach FastAPI auto-instrumentation when available; otherwise do nothing."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:
        return
    FastAPIInstrumentor.instrument_app(app)
