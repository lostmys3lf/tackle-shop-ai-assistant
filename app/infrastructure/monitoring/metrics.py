"""Prometheus metric definitions and recording helpers for observability."""

from dataclasses import dataclass
from prometheus_client import Counter, Histogram

LLM_CALLS_TOTAL = Counter(
    "llm_calls_total",
    "Total number of LLM calls by operation, model, and status.",
    ["operation", "model", "status"],
)

LLM_LATENCY_SECONDS = Histogram(
    "llm_latency_seconds",
    "Latency of LLM API calls in seconds.",
    ["operation", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0),
)

LLM_TOKENS_TOTAL = Counter(
    "llm_tokens_total",
    "Total LLM tokens consumed by operation, model, and token type.",
    ["operation", "model", "token_type"],
)

LLM_PARSE_FAILURES_TOTAL = Counter(
    "llm_parse_failures_total",
    "Total LLM JSON or schema validation failures.",
    ["operation", "model"],
)

LLM_FALLBACKS_TOTAL = Counter(
    "llm_fallbacks_total",
    "Total fallback activations in LLM client or router.",
    ["component", "from_target", "to_target"],
)

MESSAGES_PROCESSED_TOTAL = Counter(
    "messages_processed_total",
    "Total incoming user messages processed by classified intent.",
    ["intent"],
)

TRANSACTIONS_TOTAL = Counter(
    "transactions_total",
    "Total store transactions processed by type and lifecycle status.",
    ["tx_type", "status"],
)

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests served by the API.",
    ["method", "path", "status_code"],
)

HTTP_REQUEST_LATENCY_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ["method", "path"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 15.0),
)


@dataclass(frozen=True)
class LLMCallMetric:
    """Metric payload for a single LLM call attempt."""

    operation: str
    model: str
    status: str
    latency_seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0


def record_llm_call(metric: LLMCallMetric) -> None:
    """Record count, latency, and token usage for one LLM call."""
    LLM_CALLS_TOTAL.labels(
        operation=metric.operation,
        model=metric.model,
        status=metric.status,
    ).inc()
    LLM_LATENCY_SECONDS.labels(
        operation=metric.operation,
        model=metric.model,
    ).observe(max(0.0, metric.latency_seconds))
    if metric.prompt_tokens > 0:
        LLM_TOKENS_TOTAL.labels(
            operation=metric.operation,
            model=metric.model,
            token_type="prompt",
        ).inc(metric.prompt_tokens)
    if metric.completion_tokens > 0:
        LLM_TOKENS_TOTAL.labels(
            operation=metric.operation,
            model=metric.model,
            token_type="completion",
        ).inc(metric.completion_tokens)


def record_llm_parse_failure(operation: str, model: str) -> None:
    """Increment counter for LLM response parsing or validation failure."""
    LLM_PARSE_FAILURES_TOTAL.labels(operation=operation, model=model).inc()


def record_fallback(component: str, from_target: str, to_target: str) -> None:
    """Increment fallback counter when primary model or router fails."""
    LLM_FALLBACKS_TOTAL.labels(
        component=component,
        from_target=from_target,
        to_target=to_target,
    ).inc()


def record_message_processed(intent: str) -> None:
    """Increment counter for processed incoming messages by intent."""
    MESSAGES_PROCESSED_TOTAL.labels(intent=intent).inc()


def record_transaction_event(tx_type: str, status: str) -> None:
    """Increment counter for transaction creation, confirmation, or rejection."""
    TRANSACTIONS_TOTAL.labels(tx_type=tx_type, status=status).inc()


def record_http_request(
    method: str,
    path: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    """Record count and duration histogram for an HTTP API request."""
    HTTP_REQUESTS_TOTAL.labels(
        method=method,
        path=path,
        status_code=str(status_code),
    ).inc()
    HTTP_REQUEST_LATENCY_SECONDS.labels(
        method=method,
        path=path,
    ).observe(max(0.0, duration_seconds))
