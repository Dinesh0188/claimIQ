"""Request correlation, structured logs, and a Prometheus-compatible metrics endpoint.

No new dependency. `prometheus-client` would be the obvious reach, but the exposition
format is a dozen lines of text and the registry is a dict of counters -- taking a
dependency to avoid writing those would be worse than writing them, and this way the
metric names are defined in one readable place instead of scattered across decorators.

What is deliberately *not* here: tracing spans. The system already has a first-class
per-claim trace (`claimiq/trace.py`) that records node latency and token spend, which
is the thing anyone debugging this actually wants. Adding OpenTelemetry alongside it
would produce two overlapping stories about the same run. The roadmap entry is to make
`trace.py` emit OTLP rather than to bolt on a second mechanism.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import Lock

# Correlation id for the in-flight request. Set by the middleware, read by anything
# that logs, and echoed back on the response so a user reporting "it failed" can hand
# over one string that finds every line of the failure.
request_id: ContextVar[str] = ContextVar("claimiq_request_id", default="")
principal_label: ContextVar[str] = ContextVar("claimiq_principal", default="-")


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# --- metrics --------------------------------------------------------------

# Seconds. Chosen for what this service actually does rather than from a template:
# a deterministic audit lands around 50 ms, an LLM audit in single-digit seconds, and
# OCR on a scanned page takes about 15 -- so the interesting resolution is spread
# across three orders of magnitude and the top bucket has to be generous.
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)


@dataclass
class _Histogram:
    buckets: tuple[float, ...]
    counts: dict[float, int] = field(default_factory=dict)
    total: float = 0.0
    n: int = 0

    def observe(self, value: float) -> None:
        self.n += 1
        self.total += value
        for edge in self.buckets:
            if value <= edge:
                self.counts[edge] = self.counts.get(edge, 0) + 1


class Metrics:
    """Counters, gauges and latency histograms, keyed by label tuples."""

    def __init__(self) -> None:
        self._counters: dict[tuple[str, tuple[tuple[str, str], ...]], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._histograms: dict[tuple[str, tuple[tuple[str, str], ...]], _Histogram] = {}
        self._lock = Lock()

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None):
        return name, tuple(sorted((labels or {}).items()))

    def inc(self, name: str, labels: dict[str, str] | None = None, by: float = 1.0) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += by

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        with self._lock:
            self._gauges[self._key(name, labels)] = value

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            hist = self._histograms.get(key)
            if hist is None:
                hist = self._histograms[key] = _Histogram(LATENCY_BUCKETS)
            hist.observe(value)

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()

    def snapshot(self) -> dict:
        """Everything, as plain JSON. What the UI reads; /metrics reformats it."""
        with self._lock:
            return {
                "counters": {_render(n, lb): v for (n, lb), v in self._counters.items()},
                "gauges": {_render(n, lb): v for (n, lb), v in self._gauges.items()},
                "histograms": {
                    _render(n, lb): {
                        "count": h.n,
                        "sum": round(h.total, 4),
                        "avg": round(h.total / h.n, 4) if h.n else 0.0,
                        "buckets": {str(e): h.counts.get(e, 0) for e in h.buckets},
                    }
                    for (n, lb), h in self._histograms.items()
                },
            }

    def exposition(self) -> str:
        """Prometheus text format v0.0.4."""
        lines: list[str] = []
        with self._lock:
            for (name, labels), value in sorted(self._counters.items()):
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{_render(name, labels)} {value:g}")
            for (name, labels), value in sorted(self._gauges.items()):
                lines.append(f"# TYPE {name} gauge")
                lines.append(f"{_render(name, labels)} {value:g}")
            for (name, labels), hist in sorted(self._histograms.items()):
                lines.append(f"# TYPE {name} histogram")
                cumulative = 0
                for edge in hist.buckets:
                    cumulative = max(cumulative, hist.counts.get(edge, 0))
                    lines.append(f"{_render(name + '_bucket', labels + (('le', str(edge)),))} {cumulative}")
                lines.append(f"{_render(name + '_bucket', labels + (('le', '+Inf'),))} {hist.n}")
                lines.append(f"{_render(name + '_sum', labels)} {hist.total:g}")
                lines.append(f"{_render(name + '_count', labels)} {hist.n}")
        return "\n".join(lines) + "\n"


def _render(name: str, labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return name
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in labels)
    return f"{name}{{{inner}}}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


METRICS = Metrics()


# --- structured logging ---------------------------------------------------


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the correlation id attached automatically.

    Every log aggregator can parse this and none of them can reliably parse uvicorn's
    default line, so the difference is whether a production incident is greppable.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id.get() or None,
            "principal": principal_label.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = value
        return json.dumps({k: v for k, v in payload.items() if v is not None})


def configure_logging(as_json: bool) -> None:
    logger = logging.getLogger("claimiq")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter() if as_json else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False


def log(event: str, **fields) -> None:
    logging.getLogger("claimiq").info(event, extra={"extra_fields": fields})
