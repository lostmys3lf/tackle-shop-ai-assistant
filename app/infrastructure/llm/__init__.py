"""LLM infrastructure package providing unified client, configuration, routers, and exceptions."""

from app.infrastructure.llm.client import (
    LLMClient,
    LLMClientConfig,
    LLMRequest,
    get_llm_client,
)
from app.infrastructure.llm.exceptions import (
    LLMAllModelsFailedError,
    LLMError,
    LLMRateLimitError,
    LLMValidationError,
)
from app.infrastructure.llm.router import (
    BaseMessageRouter,
    FallbackMessageRouter,
    JevMessageRouter,
    LLMMessageRouter,
    MessageIntent,
    MessageRoutingResult,
    get_message_router,
)

__all__ = [
    "LLMClient",
    "LLMClientConfig",
    "LLMRequest",
    "get_llm_client",
    "LLMError",
    "LLMRateLimitError",
    "LLMValidationError",
    "LLMAllModelsFailedError",
    "BaseMessageRouter",
    "LLMMessageRouter",
    "JevMessageRouter",
    "FallbackMessageRouter",
    "MessageIntent",
    "MessageRoutingResult",
    "get_message_router",
]
