"""Structured JSON logging, Intent/Outcome auditing, OpenTelemetry, and PII scrubbing.

Implements governance and telemetry patterns aligned with Google Cloud production
agent architectures: zero bare print statements, active PII scrubbing prior to
log/memory persistence, explicit pre-execution intent vs. post-execution outcome
records, and OpenTelemetry distributed span correlation.
"""

from __future__ import annotations

from contextlib import contextmanager
import io
import logging
import re
from typing import Any, Iterator, Optional

from google.cloud import logging as cloud_logging
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

try:
    from pythonjsonlogger.json import JsonFormatter
except ImportError:  # pragma: no cover
    from pythonjsonlogger.jsonlogger import JsonFormatter  # type: ignore[no-redef]

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
_PASSPORT_RE = re.compile(
    r"(?i)\b(?:passport(?:\s*(?:no|number|#|:))?\s*)([A-Z0-9]{6,9})\b"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,3}\)[\s.-]?|\d{2,3}[\s.-])\d{4,5}[\s.-]\d{4}\b"
)

_SPAN_EXPORTER = InMemorySpanExporter()
_TRACER_INITIALIZED = False


def scrub_pii(value: Any) -> Any:
    """Recursively scrubs emails, payment cards, passports, and phone numbers.

    Args:
        value: Arbitrary string, mapping, sequence, or primitive payload.

    Returns:
        Sanitized copy safe for structured logs, traces, and persistent memory.
    """
    if isinstance(value, str):
        text = _EMAIL_RE.sub("[REDACTED_EMAIL]", value)
        text = _PASSPORT_RE.sub("passport [REDACTED_PASSPORT]", text)
        text = _CARD_RE.sub("[REDACTED_CARD]", text)
        text = _PHONE_RE.sub("[REDACTED_PHONE]", text)
        return text
    if isinstance(value, dict):
        return {str(k): scrub_pii(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub_pii(item) for item in value]
    return value


class PiiRedactingFilter(logging.Filter):
    """Logging filter that scrubs PII and injects OpenTelemetry trace context."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_pii(record.msg)
        if record.args:
            record.args = tuple(scrub_pii(arg) for arg in record.args)

        span_ctx = trace.get_current_span().get_span_context()
        if span_ctx and span_ctx.is_valid:
            record.otel_trace_id = f"{span_ctx.trace_id:032x}"
            record.otel_span_id = f"{span_ctx.span_id:016x}"
        else:
            record.otel_trace_id = "0" * 32
            record.otel_span_id = "0" * 16

        for attr in ("payload", "tool_args", "outcome_summary", "user_query"):
            if hasattr(record, attr):
                setattr(record, attr, scrub_pii(getattr(record, attr)))
        return True


def configure_observability(
    service_name: str = "executive-travel-concierge",
    log_level: str = "INFO",
    enable_cloud_logging: bool = False,
    stream: Optional[io.TextIOBase] = None,
) -> logging.Logger:
    """Initializes OpenTelemetry TracerProvider and structured JSON logging."""
    global _TRACER_INITIALIZED
    if not _TRACER_INITIALIZED:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        provider.add_span_processor(SimpleSpanProcessor(_SPAN_EXPORTER))
        trace.set_tracer_provider(provider)
        _TRACER_INITIALIZED = True

    if enable_cloud_logging:
        client = cloud_logging.Client()
        client.setup_logging(log_level=getattr(logging, log_level.upper(), logging.INFO))

    logger = logging.getLogger("concierge_agent")
    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    formatter = JsonFormatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s %(otel_trace_id)s %(otel_span_id)s"
    )
    handler.setFormatter(formatter)
    handler.addFilter(PiiRedactingFilter())
    logger.addHandler(handler)
    return logger


def get_structured_logger(name: str = "concierge_agent") -> logging.Logger:
    """Returns a child logger under the configured structured JSON root logger."""
    root = logging.getLogger("concierge_agent")
    if not root.handlers:
        configure_observability()
    return logging.getLogger(name)


def get_tracer(instrumenting_module: str = "concierge_agent") -> trace.Tracer:
    """Returns an OpenTelemetry tracer, initializing the provider if needed."""
    if not _TRACER_INITIALIZED:
        configure_observability()
    return trace.get_tracer(instrumenting_module)


def get_exported_spans() -> tuple[ReadableSpan, ...]:
    """Returns recorded OpenTelemetry spans for verification and evaluation."""
    return _SPAN_EXPORTER.get_finished_spans()


@contextmanager
def traced_operation(
    span_name: str, attributes: Optional[dict[str, Any]] = None
) -> Iterator[trace.Span]:
    """Context manager wrapping an agent or tool step in an OpenTelemetry span."""
    tracer = get_tracer()
    with tracer.start_as_current_span(span_name) as span:
        if attributes:
            sanitized = scrub_pii(attributes)
            for key, val in sanitized.items():
                span.set_attribute(key, str(val))
        yield span


def record_tool_intent(
    tool_name: str,
    tool_args: dict[str, Any],
    agent_name: str = "coordinator",
    invocation_id: str = "local",
) -> dict[str, Any]:
    """Logs the agent's intended tool invocation BEFORE execution."""
    sanitized_args = scrub_pii(tool_args)
    entry = {
        "event": "tool_intent",
        "phase": "BEFORE_EXECUTION",
        "agent_name": agent_name,
        "tool_name": tool_name,
        "invocation_id": invocation_id,
        "tool_args": sanitized_args,
    }
    get_structured_logger("concierge_agent.audit").info(
        "Tool execution intent captured prior to invocation", extra=entry
    )
    return entry


def record_tool_outcome(
    tool_name: str,
    status: str,
    outcome_summary: Any,
    latency_ms: float = 0.0,
    agent_name: str = "coordinator",
    invocation_id: str = "local",
) -> dict[str, Any]:
    """Logs the actual tool outcome AFTER execution completes or fails."""
    sanitized_outcome = scrub_pii(outcome_summary)
    entry = {
        "event": "tool_outcome",
        "phase": "AFTER_EXECUTION",
        "agent_name": agent_name,
        "tool_name": tool_name,
        "invocation_id": invocation_id,
        "status": status,
        "latency_ms": round(latency_ms, 2),
        "outcome_summary": sanitized_outcome,
    }
    get_structured_logger("concierge_agent.audit").info(
        "Tool execution outcome captured after invocation", extra=entry
    )
    return entry