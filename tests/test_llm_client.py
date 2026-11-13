"""Unit tests for the LLMClient with mock implementations."""

from unittest.mock import MagicMock
import pytest
from pydantic import BaseModel

import openai
from app.infrastructure.llm.client import (
    LLMClient,
    LLMClientConfig,
    LLMRequest,
)
from app.infrastructure.llm.exceptions import (
    LLMAllModelsFailedError,
    LLMError,
)


class SampleOutput(BaseModel):
    """Pydantic model for testing structured output validation."""

    answer: str
    confidence: float


def create_mock_completion(content: str) -> MagicMock:
    """Helper to create a mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    completion = MagicMock()
    completion.choices = [choice]
    return completion


@pytest.fixture
def mock_config() -> LLMClientConfig:
    """Fixture providing a test configuration with zero delays for fast tests."""
    return LLMClientConfig(
        base_url="https://mock.llm.local/v1",
        api_key="mock-key",
        primary_model="mock/primary-model",
        fallback_model="mock/fallback-model",
        timeout=5.0,
        max_rate_limit_retries=2,
        retry_delay_seconds=0.001,  # Jeda minimal untuk kecepatan unit test
        langfuse_public_key="",
        langfuse_secret_key="",
    )


def test_generate_text_primary_success(mock_config):
    """Test text generation succeeds on primary model without fallback."""
    mock_raw_client = MagicMock()
    mock_raw_client.chat.completions.create.return_value = create_mock_completion(
        "Halo, ada yang bisa dibantu?"
    )

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "Halo"}])

    result = client.generate_text(req)

    assert result == "Halo, ada yang bisa dibantu?"
    assert mock_raw_client.chat.completions.create.call_count == 1
    call_kwargs = mock_raw_client.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "mock/primary-model"


def test_retry_on_rate_limit_429(mock_config):
    """Test that HTTP 429 rate limit triggers retries with backoff and succeeds."""
    mock_raw_client = MagicMock()
    rate_err = openai.RateLimitError(
        message="Rate limit hit",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )

    # Gagal 429 sekali, lalu panggilan kedua berhasil
    mock_raw_client.chat.completions.create.side_effect = [
        rate_err,
        create_mock_completion("Sukses setelah retry"),
    ]

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "Test rate limit"}])

    result = client.generate_text(req)

    assert result == "Sukses setelah retry"
    assert mock_raw_client.chat.completions.create.call_count == 2


def test_fallback_when_primary_model_fails(mock_config):
    """Test that client falls back to secondary model when primary model fails."""
    mock_raw_client = MagicMock()
    api_err = openai.APIConnectionError(request=MagicMock())

    def side_effect(**kwargs):
        if kwargs.get("model") == "mock/primary-model":
            raise api_err
        return create_mock_completion("Jawaban dari model fallback")

    mock_raw_client.chat.completions.create.side_effect = side_effect

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "Test fallback"}])

    result = client.generate_text(req)

    assert result == "Jawaban dari model fallback"
    assert mock_raw_client.chat.completions.create.call_count >= 2


def test_all_models_fail_raises_clear_error(mock_config):
    """Test that LLMAllModelsFailedError is raised when both primary and fallback fail."""
    mock_raw_client = MagicMock()
    mock_raw_client.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "Test fail all"}])

    with pytest.raises(LLMAllModelsFailedError) as exc_info:
        client.generate_text(req)

    assert "Semua model LLM gagal" in str(exc_info.value)
    assert "mock/primary-model" in str(exc_info.value)
    assert "mock/fallback-model" in str(exc_info.value)


def test_structured_output_validation_success(mock_config):
    """Test structured output generates and validates valid Pydantic response."""
    mock_raw_client = MagicMock()
    valid_json = '{"answer": "stok tersedia", "confidence": 0.95}'
    mock_raw_client.chat.completions.create.return_value = create_mock_completion(
        valid_json
    )

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "cek stok"}])

    parsed = client.generate_structured(req, SampleOutput)

    assert isinstance(parsed, SampleOutput)
    assert parsed.answer == "stok tersedia"
    assert parsed.confidence == 0.95


def test_structured_output_retry_and_fallback(mock_config):
    """Test structured output retries primary on invalid JSON, then falls back and succeeds."""
    mock_raw_client = MagicMock()
    invalid_json = "bukan json valid"
    valid_json = '{"answer": "stok dari fallback", "confidence": 0.88}'

    def side_effect(**kwargs):
        if kwargs.get("model") == "mock/primary-model":
            return create_mock_completion(invalid_json)
        return create_mock_completion(valid_json)

    mock_raw_client.chat.completions.create.side_effect = side_effect

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(messages=[{"role": "user", "content": "cek stok"}])

    parsed = client.generate_structured(req, SampleOutput)

    assert isinstance(parsed, SampleOutput)
    assert parsed.answer == "stok dari fallback"
    assert parsed.confidence == 0.88


def test_langfuse_tracing_metadata_included():
    """Test that Langfuse-enabled client passes prompt_version and metadata in kwargs."""
    config_with_langfuse = LLMClientConfig(
        base_url="https://mock.llm.local/v1",
        api_key="mock-key",
        primary_model="mock/primary-model",
        fallback_model="mock/fallback-model",
        langfuse_public_key="pk-test",
        langfuse_secret_key="sk-test",
    )
    mock_raw_client = MagicMock()
    mock_raw_client.chat.completions.create.return_value = create_mock_completion("ok")

    client = LLMClient(config=config_with_langfuse, raw_client=mock_raw_client)
    req = LLMRequest(
        messages=[{"role": "user", "content": "ping"}],
        prompt_version="v1.2",
        name="test_prompt",
        metadata={"user_type": "owner"},
    )

    client.generate_text(req)

    call_kwargs = mock_raw_client.chat.completions.create.call_args[1]
    assert call_kwargs["name"] == "test_prompt"
    assert call_kwargs["metadata"]["prompt_version"] == "v1.2"
    assert call_kwargs["metadata"]["user_type"] == "owner"


def test_generate_with_tools_parses_tool_calls_and_falls_back(mock_config):
    """Test generate_with_tools extracts tool calls and falls back on primary error."""
    mock_raw_client = MagicMock()
    tool_call = MagicMock()
    tool_call.id = "call_123"
    tool_call.function.name = "get_stock"
    tool_call.function.arguments = '{"product_query": "pelet"}'

    choice = MagicMock()
    choice.message.content = ""
    choice.message.tool_calls = [tool_call]
    completion = MagicMock()
    completion.choices = [choice]

    api_err = openai.APIConnectionError(request=MagicMock())

    def side_effect(**kwargs):
        if kwargs.get("model") == "mock/primary-model":
            raise api_err
        return completion

    mock_raw_client.chat.completions.create.side_effect = side_effect

    client = LLMClient(config=mock_config, raw_client=mock_raw_client)
    req = LLMRequest(
        messages=[{"role": "user", "content": "cek stok pelet"}],
        tools=[{"type": "function", "function": {"name": "get_stock"}}],
        tool_choice="auto",
    )

    result = client.generate_with_tools(req)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "call_123"
    assert result.tool_calls[0].name == "get_stock"
    assert result.tool_calls[0].arguments == '{"product_query": "pelet"}'
