"""Unified LLM client module for OpenAI-compatible providers.

Supports fallback, backoff retry on HTTP 429, structured validation, and Langfuse tracing.
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

import openai
from openai import OpenAI as StandardOpenAI
from langfuse.openai import OpenAI as LangfuseOpenAI
from app.config import Settings, settings as default_settings
from app.infrastructure.llm.exceptions import (
    LLMAllModelsFailedError,
    LLMError,
    LLMRateLimitError,
    LLMValidationError,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)
U = TypeVar("U")


@dataclass
class LLMToolCall:
    """Single tool/function call emitted by the LLM."""

    id: str
    name: str
    arguments: str


@dataclass
class LLMToolResponse:
    """Completion output that may include assistant text and tool calls."""

    content: str
    tool_calls: list[LLMToolCall] = field(default_factory=list)


@dataclass
class LLMRequest:  # pylint: disable=too-many-instance-attributes
    """Request payload parameters for LLM text, structured, and tool generation."""

    messages: list[dict[str, Any]]
    prompt_version: str | None = None
    max_tokens: int = 1000
    temperature: float = 0.0
    name: str = "llm_completion"
    metadata: dict[str, Any] = field(default_factory=dict)
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None


@dataclass
class LLMClientConfig:  # pylint: disable=too-many-instance-attributes
    """Configuration options for LLMClient."""

    base_url: str
    api_key: str
    primary_model: str
    fallback_model: str
    timeout: float = 30.0
    max_rate_limit_retries: int = 3
    retry_delay_seconds: float = 1.0
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = ""

    @classmethod
    def from_settings(cls, cfg: Settings) -> "LLMClientConfig":
        """Build LLMClientConfig from application Settings."""
        return cls(
            base_url=cfg.llm_base_url,
            api_key=cfg.llm_api_key,
            primary_model=cfg.llm_model,
            fallback_model=cfg.llm_fallback_model,
            langfuse_public_key=cfg.langfuse_public_key,
            langfuse_secret_key=cfg.langfuse_secret_key,
            langfuse_base_url=cfg.langfuse_base_url,
        )


class LLMClient:
    """Unified client for OpenAI-compatible endpoints with retry, fallback, and tracing."""

    def __init__(
        self,
        config: LLMClientConfig | None = None,
        raw_client: Any | None = None,
    ) -> None:
        """Initialize the LLM client with configuration and underlying OpenAI client."""
        if config is None:
            config = LLMClientConfig.from_settings(default_settings)
        self.config = config
        self.primary_model = config.primary_model
        self.fallback_model = config.fallback_model
        self.is_langfuse_enabled = bool(
            config.langfuse_public_key and config.langfuse_secret_key
        )

        if raw_client is not None:
            self._client = raw_client
        else:
            self._client = self._create_openai_client()

    def _create_openai_client(self) -> Any:
        """Create standard OpenAI client or Langfuse-instrumented OpenAI client."""
        # Gagal sejak awal kalau API key kosong, supaya salah konfigurasi langsung ketahuan
        if not self.config.api_key:
            raise LLMError("LLM_API_KEY is empty; set it before calling the LLM.")
        api_key = self.config.api_key

        # Sinkronkan environment variable Langfuse jika diisi
        if self.is_langfuse_enabled:
            os.environ["LANGFUSE_PUBLIC_KEY"] = self.config.langfuse_public_key
            os.environ["LANGFUSE_SECRET_KEY"] = self.config.langfuse_secret_key
            if self.config.langfuse_base_url:
                os.environ["LANGFUSE_BASE_URL"] = self.config.langfuse_base_url

            try:
                return LangfuseOpenAI(
                    base_url=self.config.base_url,
                    api_key=api_key,
                    timeout=self.config.timeout,
                )
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.warning(
                    "Gagal inisialisasi Langfuse OpenAI client, fallback: %s", exc
                )

        return StandardOpenAI(
            base_url=self.config.base_url,
            api_key=api_key,
            timeout=self.config.timeout,
        )

    def _call_completion_raw(
        self,
        model: str,
        request: LLMRequest,
        response_format: dict[str, str] | None = None,
    ) -> Any:
        """Execute chat completion for a model with retry on HTTP 429 and return message."""
        retries = 0
        current_delay = self.config.retry_delay_seconds

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": request.messages,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }

        if response_format is not None:
            kwargs["response_format"] = response_format

        if request.tools:
            kwargs["tools"] = request.tools
            if request.tool_choice is not None:
                kwargs["tool_choice"] = request.tool_choice

        # Tambahkan metadata Langfuse jika tracing aktif
        if self.is_langfuse_enabled:
            kwargs["name"] = request.name
            trace_metadata = dict(request.metadata)
            if request.prompt_version:
                trace_metadata["prompt_version"] = request.prompt_version
            kwargs["metadata"] = trace_metadata

        while True:
            try:
                response = self._client.chat.completions.create(**kwargs)
                return response.choices[0].message
            except openai.RateLimitError as exc:
                retries += 1
                if retries > self.config.max_rate_limit_retries:
                    logger.error(
                        "Rate limit 429 terlampaui setelah %d retry: %s",
                        retries - 1,
                        model,
                    )
                    raise LLMRateLimitError(
                        f"Rate limit 429 exceeded after {retries - 1} retries: {exc}"
                    ) from exc

                logger.warning(
                    "Kena rate limit 429 pada model %s. Jeda %.2fs (retry %d/%d)...",
                    model,
                    current_delay,
                    retries,
                    self.config.max_rate_limit_retries,
                )
                time.sleep(current_delay)
                current_delay *= 2.0
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error("Panggilan LLM gagal untuk model %s: %s", model, exc)
                raise LLMError(f"LLM call failed for model '{model}': {exc}") from exc

    def _call_model_with_retry(
        self,
        model: str,
        request: LLMRequest,
        response_format: dict[str, str] | None = None,
    ) -> str:
        """Call LLM provider for a specific model and return text content."""
        message = self._call_completion_raw(
            model, request, response_format=response_format
        )
        return message.content or ""

    def _run_with_fallback(self, runner: Callable[[str], U]) -> U:
        """Execute model call on primary model with automatic fallback to secondary model."""
        primary_error: Exception | None = None
        try:
            return runner(self.primary_model)
        except (LLMRateLimitError, LLMError) as exc:
            primary_error = exc
            logger.warning(
                "Model utama '%s' gagal (%s). Berpindah ke fallback '%s'...",
                self.primary_model,
                exc,
                self.fallback_model,
            )

        try:
            return runner(self.fallback_model)
        except (LLMRateLimitError, LLMError) as fallback_exc:
            logger.error(
                "Model fallback '%s' juga gagal: %s",
                self.fallback_model,
                fallback_exc,
            )
            raise LLMAllModelsFailedError(
                f"Semua model LLM gagal. Model utama '{self.primary_model}': {primary_error}. "
                f"Model fallback '{self.fallback_model}': {fallback_exc}."
            ) from fallback_exc

    def generate_text(self, request: LLMRequest) -> str:
        """Generate text completion with automatic fallback to secondary model."""
        return self._run_with_fallback(
            lambda model: self._call_model_with_retry(model, request)
        )

    def _parse_tool_message(self, model: str, message: Any) -> LLMToolResponse:
        """Extract text content and normalized tool calls from completion message."""
        content = getattr(message, "content", None) or ""
        raw_calls = getattr(message, "tool_calls", None)
        tool_calls: list[LLMToolCall] = []

        if isinstance(raw_calls, list):
            try:
                for call in raw_calls:
                    tool_calls.append(
                        LLMToolCall(
                            id=str(call.id),
                            name=str(call.function.name),
                            arguments=str(call.function.arguments or "{}"),
                        )
                    )
            except AttributeError as exc:
                raise LLMError(
                    f"Malformed tool call structure from model '{model}': {exc}"
                ) from exc

        return LLMToolResponse(content=content, tool_calls=tool_calls)

    def generate_with_tools(self, request: LLMRequest) -> LLMToolResponse:
        """Generate completion supporting OpenAI tool calls with fallback and tracing."""
        return self._run_with_fallback(
            lambda model: self._parse_tool_message(
                model, self._call_completion_raw(model, request)
            )
        )

    def _execute_and_validate(
        self,
        model: str,
        request: LLMRequest,
        response_model: type[T],
    ) -> T:
        """Call model expecting JSON and validate into target Pydantic schema."""
        raw_text = self._call_model_with_retry(
            model=model,
            request=request,
            response_format={"type": "json_object"},
        )
        try:
            return response_model.model_validate_json(raw_text)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise LLMValidationError(
                f"Validasi output Pydantic gagal untuk model '{model}': {exc}."
            ) from exc

    def generate_structured(
        self,
        request: LLMRequest,
        response_model: type[T],
    ) -> T:
        """Generate structured JSON output validated against Pydantic schema."""
        # 1. Coba panggil model utama
        try:
            return self._execute_and_validate(
                self.primary_model, request, response_model
            )
        except LLMValidationError as val_exc:
            # Sesuai aturan AGENTS.md: jika gagal validasi, retry sekali pada model utama
            logger.warning(
                "Model utama '%s' gagal validasi (%s). Retry sekali...",
                self.primary_model,
                val_exc,
            )
            try:
                return self._execute_and_validate(
                    self.primary_model, request, response_model
                )
            except (LLMValidationError, LLMError) as retry_exc:
                logger.warning(
                    "Retry validasi model utama '%s' gagal (%s). Pindah ke '%s'...",
                    self.primary_model,
                    retry_exc,
                    self.fallback_model,
                )
        except (LLMRateLimitError, LLMError) as call_exc:
            logger.warning(
                "Model utama '%s' gagal dipanggil (%s). Pindah ke '%s'...",
                self.primary_model,
                call_exc,
                self.fallback_model,
            )

        # 2. Coba model fallback jika model utama gagal panggil atau gagal validasi
        try:
            return self._execute_and_validate(
                self.fallback_model, request, response_model
            )
        except (LLMValidationError, LLMRateLimitError, LLMError) as fallback_exc:
            logger.error(
                "Model fallback '%s' juga gagal: %s",
                self.fallback_model,
                fallback_exc,
            )
            raise LLMAllModelsFailedError(
                f"Semua model LLM gagal. Model fallback '{self.fallback_model}': {fallback_exc}"
            ) from fallback_exc


_DEFAULT_CLIENT: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Get or create singleton LLMClient configured from application settings."""
    global _DEFAULT_CLIENT  # pylint: disable=global-statement
    if _DEFAULT_CLIENT is None:
        _DEFAULT_CLIENT = LLMClient()
    return _DEFAULT_CLIENT
