"""Unit tests for message routing infrastructure."""

from unittest.mock import MagicMock
import httpx
import pytest

from app.config import Settings
from app.infrastructure.llm.exceptions import LLMError
from app.infrastructure.llm.router import (
    BaseMessageRouter,
    FallbackMessageRouter,
    JevMessageRouter,
    LLMMessageRouter,
    MessageIntent,
    MessageRoutingResult,
    get_message_router,
)


def test_llm_message_router_success():
    """Verify LLMMessageRouter calls LLMClient and returns valid MessageRoutingResult."""
    mock_llm_client = MagicMock()
    mock_llm_client.generate_structured.return_value = MessageRoutingResult(
        intent=MessageIntent.SALE,
        confidence=0.96,
    )

    router = LLMMessageRouter(llm_client=mock_llm_client)
    result = router.route("Laku pelet jitu 2 bungkus")

    assert isinstance(result, MessageRoutingResult)
    assert result.intent == MessageIntent.SALE
    assert result.confidence == 0.96
    assert mock_llm_client.generate_structured.call_count == 1


def test_jev_message_router_success():
    """Verify JevMessageRouter parses Cloudflare Workers AI response correctly."""
    mock_http_client = MagicMock()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "result": {
            "intent": {
                "response": "purchase",
                "probability": 0.92,
            }
        },
    }
    mock_http_client.post.return_value = mock_response

    router = JevMessageRouter(
        account_id="cf-acc-123",
        api_token="cf-token-abc",
        http_client=mock_http_client,
    )
    result = router.route("Beli senar 5 gulung dari supplier")

    assert isinstance(result, MessageRoutingResult)
    assert result.intent == MessageIntent.PURCHASE
    assert result.confidence == 0.92
    assert mock_http_client.post.call_count == 1


def test_jev_message_router_missing_credentials_raises_error():
    """Verify Jev router raises error when credentials are not configured."""
    router = JevMessageRouter(account_id="", api_token="")
    with pytest.raises(LLMError) as exc_info:
        router.route("cek stok")

    assert "CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN" in str(exc_info.value)


def test_jev_message_router_http_error_raises_llm_error():
    """Verify Jev router raises LLMError on HTTP failure or non-200 status."""
    mock_http_client = MagicMock()
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_http_client.post.return_value = mock_response

    router = JevMessageRouter(
        account_id="cf-acc-123",
        api_token="cf-token-abc",
        http_client=mock_http_client,
    )

    with pytest.raises(LLMError) as exc_info:
        router.route("cek stok")

    assert "Cloudflare Workers AI returned status 500" in str(exc_info.value)


def test_fallback_message_router_falls_back_when_primary_fails():
    """Verify FallbackMessageRouter seamlessly falls back to secondary router when primary fails."""
    mock_primary = MagicMock(spec=BaseMessageRouter)
    mock_primary.route.side_effect = LLMError("Cloudflare connection timeout")

    mock_fallback = MagicMock(spec=BaseMessageRouter)
    mock_fallback.route.return_value = MessageRoutingResult(
        intent=MessageIntent.QUESTION,
        confidence=0.88,
    )

    router = FallbackMessageRouter(primary=mock_primary, fallback=mock_fallback)
    result = router.route("Sisa stok umpan pak ikin berapa?")

    assert result.intent == MessageIntent.QUESTION
    assert result.confidence == 0.88
    assert mock_primary.route.call_count == 1
    assert mock_fallback.route.call_count == 1


def test_router_factory_configuration():
    """Verify get_message_router factory returns appropriate router instance according to config."""
    cfg_llm = Settings(router_provider="llm")
    router_llm = get_message_router(cfg_llm)
    assert isinstance(router_llm, LLMMessageRouter)

    cfg_jev = Settings(
        router_provider="jev",
        cloudflare_account_id="acc1",
        cloudflare_api_token="tok1",
    )
    router_jev = get_message_router(cfg_jev)
    assert isinstance(router_jev, FallbackMessageRouter)
    assert isinstance(router_jev.primary, JevMessageRouter)
    assert isinstance(router_jev.fallback, LLMMessageRouter)

    cfg_invalid = Settings(router_provider="unsupported_provider")
    with pytest.raises(ValueError) as exc_info:
        get_message_router(cfg_invalid)
    assert "Unsupported ROUTER_PROVIDER" in str(exc_info.value)
