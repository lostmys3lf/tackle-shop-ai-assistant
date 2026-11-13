"""Tests for Prometheus monitoring metrics, endpoint, and instrumentation."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from prometheus_client import REGISTRY
from pydantic import BaseModel

from app.infrastructure.llm.client import LLMClient, LLMClientConfig, LLMRequest
from app.infrastructure.llm.router import (
    FallbackMessageRouter,
    MessageIntent,
    MessageRoutingResult,
)
from app.infrastructure.monitoring.metrics import (
    record_message_processed,
    record_transaction_event,
)
from app.main import app

client = TestClient(app)


class _DummyStructuredOutput(BaseModel):
    answer: str


def _sample_value(metric_name: str, labels: dict[str, str]) -> float:
    value = REGISTRY.get_sample_value(metric_name, labels)
    return 0.0 if value is None else float(value)


def test_metrics_endpoint_is_public_and_exposes_registered_families() -> None:
    """Verify GET /metrics does not require X-API-Key and exposes Prometheus metrics."""
    # Trigger at least one label combination for families with labels so Prometheus text lists them
    record_message_processed("other")
    record_transaction_event("sale", "pending")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    body = response.text
    for expected_name in (
        "llm_calls_total",
        "llm_latency_seconds",
        "llm_tokens_total",
        "llm_parse_failures_total",
        "llm_fallbacks_total",
        "messages_processed_total",
        "transactions_total",
        "http_requests_total",
    ):
        assert expected_name in body


def test_http_middleware_tracks_api_routes_and_skips_metrics_route() -> None:
    """Verify HTTP middleware records /health requests and skips /metrics self-scrapes."""
    before_health = _sample_value(
        "http_requests_total",
        {"method": "GET", "path": "/health", "status_code": "200"},
    )
    before_metrics = _sample_value(
        "http_requests_total",
        {"method": "GET", "path": "/metrics", "status_code": "200"},
    )

    with patch(
        "app.services.health_service.check_database_connection",
        return_value=True,
    ):
        health_res = client.get("/health")
    metrics_res = client.get("/metrics")

    assert health_res.status_code == 200
    assert metrics_res.status_code == 200

    after_health = _sample_value(
        "http_requests_total",
        {"method": "GET", "path": "/health", "status_code": "200"},
    )
    after_metrics = _sample_value(
        "http_requests_total",
        {"method": "GET", "path": "/metrics", "status_code": "200"},
    )
    assert after_health == before_health + 1.0
    assert after_metrics == before_metrics


def test_llm_client_records_tokens_parse_failures_and_fallback() -> None:
    """Verify LLMClient increments calls, tokens, parse failures, and fallback metrics."""
    config = LLMClientConfig(
        base_url="http://localhost:9999/v1",
        api_key="test-key",
        primary_model="primary-m",
        fallback_model="fallback-m",
        max_rate_limit_retries=0,
        retry_delay_seconds=0.0,
    )
    llm_client = LLMClient(config=config)

    bad_resp_1 = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )
    bad_resp_2 = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"wrong": 1}'))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=6),
    )
    good_fallback_resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"answer": "ok"}'))],
        usage=SimpleNamespace(prompt_tokens=15, completion_tokens=7),
    )

    mock_openai = MagicMock()
    mock_openai.chat.completions.create.side_effect = [
        bad_resp_1,
        bad_resp_2,
        good_fallback_resp,
    ]
    llm_client._client = mock_openai  # pylint: disable=protected-access

    before_primary = _sample_value(
        "llm_calls_total",
        {"operation": "test_op", "model": "primary-m", "status": "success"},
    )
    before_parse_fail = _sample_value(
        "llm_parse_failures_total",
        {"operation": "test_op", "model": "primary-m"},
    )
    before_fallback = _sample_value(
        "llm_fallbacks_total",
        {
            "component": "llm_client",
            "from_target": "primary-m",
            "to_target": "fallback-m",
        },
    )
    before_tokens = _sample_value(
        "llm_tokens_total",
        {"operation": "test_op", "model": "fallback-m", "token_type": "prompt"},
    )

    parsed = llm_client.generate_structured(
        LLMRequest(messages=[{"role": "user", "content": "hi"}], name="test_op"),
        _DummyStructuredOutput,
    )

    assert parsed.answer == "ok"
    assert (
        _sample_value(
            "llm_calls_total",
            {"operation": "test_op", "model": "primary-m", "status": "success"},
        )
        == before_primary + 2.0
    )
    assert (
        _sample_value(
            "llm_parse_failures_total",
            {"operation": "test_op", "model": "primary-m"},
        )
        == before_parse_fail + 2.0
    )
    assert (
        _sample_value(
            "llm_fallbacks_total",
            {
                "component": "llm_client",
                "from_target": "primary-m",
                "to_target": "fallback-m",
            },
        )
        == before_fallback + 1.0
    )
    assert (
        _sample_value(
            "llm_tokens_total",
            {"operation": "test_op", "model": "fallback-m", "token_type": "prompt"},
        )
        == before_tokens + 15.0
    )


def test_fallback_router_records_fallback_metric() -> None:
    """Verify FallbackMessageRouter increments llm_fallbacks_total when primary fails."""
    primary = MagicMock()
    primary.route.side_effect = RuntimeError("primary down")
    fallback = MagicMock()
    fallback.route.return_value = MessageRoutingResult(
        intent=MessageIntent.OTHER,
        confidence=0.9,
    )

    router = FallbackMessageRouter(primary=primary, fallback=fallback)
    labels = {
        "component": "router",
        "from_target": "MagicMock",
        "to_target": "MagicMock",
    }
    before = _sample_value("llm_fallbacks_total", labels)

    res = router.route("halo")

    assert res.intent == MessageIntent.OTHER
    assert _sample_value("llm_fallbacks_total", labels) == before + 1.0
