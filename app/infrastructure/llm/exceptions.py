"""Exceptions for the LLM infrastructure module."""


class LLMError(Exception):
    """Base exception for all LLM errors."""


class LLMRateLimitError(LLMError):
    """Raised when LLM provider returns 429 (rate limit) after exhausting retries."""


class LLMValidationError(LLMError):
    """Raised when LLM response cannot be parsed or validated against the target schema."""


class LLMAllModelsFailedError(LLMError):
    """Raised when both the primary and fallback LLM models fail."""
